from __future__ import annotations

from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Iterable
import uuid

from .control import invoke_skill
from .skills import build_skill_registry


HOOKS_PATH = Path(".aae/hooks.json")
HOOK_RECORD_DIRECTORY = Path(".aae/runtime/hook-events")
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
MAX_ACTIONS_PER_EVENT = 16
MAX_CHAIN_DEPTH = 4


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_hook_config(root: Path) -> tuple[dict[str, Any], list[str]]:
    path = root / HOOKS_PATH
    if not path.exists():
        return {"schema_version": 1, "rules": []}, []
    errors: list[str] = []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {"schema_version": 1, "rules": []}, [f"Cannot read hook configuration: {error}"]
    if not isinstance(value, dict):
        return {"schema_version": 1, "rules": []}, ["Hook configuration must be an object"]
    if set(value) - {"schema_version", "rules"}:
        errors.append("Hook configuration fields are schema_version and rules")
    if value.get("schema_version") != 1 or not isinstance(value.get("rules"), list):
        errors.append("Hook configuration requires schema_version 1 and a rules list")
        return value, errors
    seen: set[str] = set()
    for index, rule in enumerate(value["rules"]):
        location = f"rules[{index}]"
        if not isinstance(rule, dict):
            errors.append(f"{location} must be an object")
            continue
        allowed = {
            "id", "enabled", "on", "paths", "request_skill", "run_check",
            "task", "destructive", "timeout_seconds",
        }
        unknown = sorted(set(rule) - allowed)
        if unknown:
            errors.append(f"{location} has unknown fields: {unknown}")
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not NAME_PATTERN.fullmatch(rule_id):
            errors.append(f"{location}.id must be lowercase kebab-case")
        elif rule_id in seen:
            errors.append(f"Duplicate hook rule id: {rule_id}")
        else:
            seen.add(rule_id)
        if not isinstance(rule.get("enabled", True), bool):
            errors.append(f"{location}.enabled must be true or false")
        if not isinstance(rule.get("on"), str) or not NAME_PATTERN.fullmatch(str(rule.get("on", ""))):
            errors.append(f"{location}.on must be a lowercase kebab-case event")
        paths = rule.get("paths", [])
        if not isinstance(paths, list) or any(not isinstance(item, str) or not item for item in paths):
            errors.append(f"{location}.paths must be a list of glob strings")
        has_skill = isinstance(rule.get("request_skill"), str) and bool(rule["request_skill"].strip())
        check = rule.get("run_check")
        has_check = isinstance(check, list) and bool(check) and all(isinstance(item, str) and item for item in check)
        if has_skill == has_check:
            errors.append(f"{location} must define exactly one of request_skill or run_check")
        if "run_check" in rule and not has_check:
            errors.append(f"{location}.run_check must be a non-empty argv list")
        if "task" in rule and (not isinstance(rule["task"], str) or not rule["task"].strip()):
            errors.append(f"{location}.task must be non-empty text")
        if not isinstance(rule.get("destructive", False), bool):
            errors.append(f"{location}.destructive must be true or false")
        timeout = rule.get("timeout_seconds", 300)
        if not isinstance(timeout, int) or timeout < 1 or timeout > 3600:
            errors.append(f"{location}.timeout_seconds must be between 1 and 3600")
    return value, errors


def _paths_match(patterns: list[str], payload: dict[str, Any]) -> bool:
    if not patterns:
        return True
    paths = payload.get("paths", [])
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, list):
        return False
    return any(
        fnmatch.fnmatch(str(path).replace("\\", "/"), pattern)
        for path in paths
        for pattern in patterns
    )


def process_event(
    root: Path,
    *,
    event: str,
    payload: dict[str, Any],
    runtime_profile: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    parent_event_id: str | None = None,
    chain_depth: int = 0,
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """Apply simple `X happens -> request a skill or run a check` rules."""
    profile = runtime_profile or {}
    config, errors = load_hook_config(root)
    input_sha256 = _digest({"event": event, "payload": payload})
    record_key = _digest({"event": event, "idempotency_key": idempotency_key or str(uuid.uuid4())})
    record_path = root / HOOK_RECORD_DIRECTORY / f"{record_key}.json"
    if idempotency_key and record_path.exists():
        try:
            existing = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            return {"status": "record-invalid"}, {}, [f"Cannot read prior hook record: {error}"]
        if existing.get("input_sha256") != input_sha256:
            existing["idempotency_conflict"] = True
            return existing, {}, ["Idempotency key was reused for a different event payload"]
        existing["duplicate_delivery"] = True
        return existing, {}, []

    record: dict[str, Any] = {
        "schema_version": 1,
        "event_id": str(uuid.uuid4()),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "input_sha256": input_sha256,
        "idempotency_key": idempotency_key,
        "parent_event_id": parent_event_id,
        "chain_depth": chain_depth,
        "duplicate_delivery": False,
        "idempotency_conflict": False,
        "matched_rules": [],
        "actions": [],
        "status": "configuration-invalid" if errors else "no-match",
    }
    procedures: dict[str, str] = {}
    if chain_depth > MAX_CHAIN_DEPTH:
        errors.append(f"Hook chain depth {chain_depth} exceeds {MAX_CHAIN_DEPTH}")
        record["status"] = "chain-depth-denied"
    elif not errors:
        rules = [
            rule for rule in config.get("rules", [])
            if rule.get("enabled", True)
            and rule.get("on") == event
            and _paths_match(rule.get("paths", []), payload)
        ]
        if len(rules) > MAX_ACTIONS_PER_EVENT:
            errors.append(f"Hook event matched more than {MAX_ACTIONS_PER_EVENT} rules")
            record["status"] = "action-budget-denied"
        else:
            registry: dict[str, Any] | None = None
            for rule in rules:
                record["matched_rules"].append(rule["id"])
                if "request_skill" in rule:
                    if registry is None:
                        registry, registry_errors, _ = build_skill_registry(root)
                        errors.extend(registry_errors)
                    if errors:
                        record["status"] = "configuration-invalid"
                        break
                    invocation, procedure, invocation_errors = invoke_skill(
                        root,
                        registry,
                        task=rule.get("task") or f"Handle {event} event",
                        explicit_skill=rule["request_skill"],
                        runtime_profile=profile,
                        trigger_provenance={
                            "event_id": record["event_id"],
                            "event": event,
                            "rule_id": rule["id"],
                            "input_sha256": input_sha256,
                        },
                    )
                    errors.extend(invocation_errors)
                    record["actions"].append({
                        "rule_id": rule["id"],
                        "action": "request-skill",
                        "skill": rule["request_skill"],
                        "invocation_id": invocation["invocation_id"],
                        "status": invocation["status"],
                    })
                    if procedure is not None:
                        procedures[invocation["invocation_id"]] = procedure
                else:
                    destructive = bool(rule.get("destructive", False))
                    if destructive and "destructive" not in set(profile.get("approvals", [])):
                        record["actions"].append({
                            "rule_id": rule["id"], "action": "run-check",
                            "status": "denied", "reason": "destructive-approval-required",
                        })
                        continue
                    try:
                        result = subprocess.run(
                            rule["run_check"], cwd=root, capture_output=True,
                            text=True, timeout=rule.get("timeout_seconds", 300), check=False,
                        )
                        record["actions"].append({
                            "rule_id": rule["id"], "action": "run-check",
                            "argv": rule["run_check"], "exit_code": result.returncode,
                            "status": "passed" if result.returncode == 0 else "failed",
                            "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
                            "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
                        })
                    except (OSError, subprocess.TimeoutExpired) as error:
                        errors.append(f"{rule['id']}: check failed to run: {error}")
                        record["actions"].append({
                            "rule_id": rule["id"], "action": "run-check", "status": "error"
                        })
            if record["actions"]:
                statuses = {action["status"] for action in record["actions"]}
                record["status"] = (
                    "failed" if statuses & {"failed", "error", "load-failed"}
                    else "denied" if statuses == {"denied"}
                    else "skill-requested" if procedures
                    else "completed"
                )
    record["hook_record_sha256"] = _digest(
        {key: value for key, value in record.items() if key not in {"recorded_at", "hook_record_sha256"}}
    )
    _write_json(record_path, record)
    return record, procedures, errors


def parse_payload_values(values: Iterable[str]) -> tuple[dict[str, Any], list[str]]:
    payload: dict[str, Any] = {}
    errors: list[str] = []
    for value in values:
        if "=" not in value:
            errors.append(f"Event data must be key=value: {value}")
            continue
        key, raw = value.split("=", 1)
        if not key or key in payload:
            errors.append(f"Event data key is empty or repeated: {key}")
            continue
        try:
            payload[key] = json.loads(raw)
        except json.JSONDecodeError:
            payload[key] = raw
    return payload, errors
