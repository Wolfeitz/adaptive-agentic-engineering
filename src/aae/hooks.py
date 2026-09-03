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
from .criteria import hook_control_criterion
from .skills import build_skill_registry


HOOKS_PATH = Path(".aae/hooks.json")
HOOK_RECORD_DIRECTORY = Path(".aae/runtime/hook-events")
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
MAX_ACTIONS_PER_EVENT = 16
MAX_CHAIN_DEPTH = 4
MAX_NATIVE_CONTEXT_CHARS = 8_000
NATIVE_PROVIDERS = {"codex", "copilot"}
NATIVE_EVENT_MAP = {
    "PreToolUse": "tool-requested",
    "PostToolUse": "tool-completed",
    "PermissionRequest": "permission-requested",
    "UserPromptSubmit": "user-prompt-submitted",
    "SubagentStart": "subagent-started",
    "SubagentStop": "subagent-stopped",
    "Stop": "agent-stopped",
    "SessionStart": "session-started",
    "SessionEnd": "session-ended",
    "PreCompact": "context-compaction-requested",
    "PostCompact": "context-compacted",
    "Interrupt": "execution-interrupted",
}
EDIT_TOOL_NAMES = {
    "Edit", "Write", "apply_patch", "edit", "write", "edit_file", "write_file",
}
PATCH_PATH_PATTERN = re.compile(
    r"^\*\*\* (?:Add|Update|Delete) File: (?P<path>.+)$", re.MULTILINE
)


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
            "task", "criterion", "destructive", "timeout_seconds",
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
        if "criterion" in rule and (
            not isinstance(rule["criterion"], str) or not rule["criterion"].strip()
        ):
            errors.append(f"{location}.criterion must be non-empty text")
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
    for_invocation_id: str | None = None,
    record_no_match: bool = True,
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """Apply simple `X happens -> request a skill or run a check` rules."""
    profile = runtime_profile or {}
    config, errors = load_hook_config(root)
    input_sha256 = _digest(
        {"event": event, "payload": payload, "for_invocation_id": for_invocation_id}
    )
    record_key = _digest(
        {
            "event": event,
            "for_invocation_id": for_invocation_id,
            "idempotency_key": idempotency_key or str(uuid.uuid4()),
        }
    )
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
        "for_invocation_id": for_invocation_id,
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
    if payload.get("provider") in NATIVE_PROVIDERS:
        record["native_provenance"] = {
            key: payload[key]
            for key in (
                "provider", "native_event", "native_payload_sha256", "session_id",
                "turn_id", "tool_use_id", "tool_name", "paths",
            )
            if key in payload
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
                    criterion = hook_control_criterion(rule)
                    destructive = bool(rule.get("destructive", False))
                    if destructive and "destructive" not in set(profile.get("approvals", [])):
                        record["actions"].append({
                            "rule_id": rule["id"], "action": "run-check",
                            "status": "denied", "reason": "destructive-approval-required",
                            "criterion": criterion,
                            "criterion_result": {
                                **criterion,
                                "result": "blocked",
                                "supporting_evidence_sha256": None,
                                "responsible_identity": {
                                    "kind": "deterministic-hook",
                                    "event_id": record["event_id"],
                                    "rule_id": rule["id"],
                                    "invocation_id": for_invocation_id,
                                },
                            },
                        })
                        continue
                    try:
                        result = subprocess.run(
                            rule["run_check"], cwd=root, capture_output=True,
                            text=True, timeout=rule.get("timeout_seconds", 300), check=False,
                        )
                        action = {
                            "rule_id": rule["id"], "action": "run-check",
                            "argv": rule["run_check"], "exit_code": result.returncode,
                            "status": "passed" if result.returncode == 0 else "failed",
                            "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
                            "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
                            "criterion": criterion,
                        }
                        evidence_sha256 = _digest(action)
                        action["criterion_result"] = {
                            **criterion,
                            "result": "passed" if result.returncode == 0 else "failed",
                            "supporting_evidence_sha256": evidence_sha256,
                            "responsible_identity": {
                                "kind": "deterministic-hook",
                                "event_id": record["event_id"],
                                "rule_id": rule["id"],
                                "invocation_id": for_invocation_id,
                            },
                        }
                        record["actions"].append(action)
                    except (OSError, subprocess.TimeoutExpired) as error:
                        errors.append(f"{rule['id']}: check failed to run: {error}")
                        record["actions"].append({
                            "rule_id": rule["id"], "action": "run-check", "status": "error",
                            "criterion": criterion,
                            "criterion_result": {
                                **criterion,
                                "result": "blocked",
                                "supporting_evidence_sha256": None,
                                "responsible_identity": {
                                    "kind": "deterministic-hook",
                                    "event_id": record["event_id"],
                                    "rule_id": rule["id"],
                                    "invocation_id": for_invocation_id,
                                },
                            },
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
    if record_no_match or record["status"] != "no-match":
        _write_json(record_path, record)
    return record, procedures, errors


def find_aae_root(start: Path) -> Path | None:
    """Find the nearest repository with AAE event rules."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / HOOKS_PATH).is_file():
            return candidate
    return None


def _native_event_name(payload: dict[str, Any]) -> str | None:
    value = payload.get("hook_event_name", payload.get("hookEventName"))
    return value if isinstance(value, str) and value else None


def _portable_native_path(root: Path, value: str) -> str | None:
    if (
        not value
        or value == "/dev/null"
        or len(value) > 4096
        or "\n" in value
        or "\r" in value
        or "\x00" in value
    ):
        return None
    candidate = Path(value.replace("\\", "/"))
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(root.resolve())
        except ValueError:
            return None
    portable = candidate.as_posix()
    if portable.startswith("./"):
        portable = portable[2:]
    parts = Path(portable).parts
    if not portable or ".." in parts:
        return None
    return portable


def _native_paths(root: Path, tool_input: object) -> list[str]:
    found: list[str] = []

    def add(value: object) -> None:
        if isinstance(value, str):
            normalized = _portable_native_path(root, value)
            if normalized is not None:
                found.append(normalized)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for key in ("file_path", "filePath", "path"):
                        if key in item:
                            add(item[key])
                else:
                    add(item)

    if not isinstance(tool_input, dict):
        return []
    for key in ("file_path", "filePath", "path", "paths", "files"):
        if key in tool_input:
            add(tool_input[key])
    patch = tool_input.get("patch", tool_input.get("command"))
    if isinstance(patch, str):
        for match in PATCH_PATH_PATTERN.finditer(patch):
            add(match.group("path").strip())
    return sorted(set(found))


def normalize_native_hook(
    root: Path,
    provider: str,
    native_payload: dict[str, Any],
) -> tuple[str | None, dict[str, Any], str | None, list[str]]:
    """Reduce a native hook payload to portable, non-sensitive event facts."""
    if provider not in NATIVE_PROVIDERS:
        return None, {}, None, [f"Unsupported native hook provider: {provider}"]
    native_event = _native_event_name(native_payload)
    if native_event is None:
        return None, {}, None, ["Native hook payload has no hook event name"]
    tool_name_value = native_payload.get("tool_name", native_payload.get("toolName"))
    tool_name = tool_name_value if isinstance(tool_name_value, str) else None
    tool_input = native_payload.get("tool_input", native_payload.get("toolInput", {}))
    paths = _native_paths(root, tool_input)
    event = (
        "files-changed"
        if native_event == "PostToolUse" and tool_name in EDIT_TOOL_NAMES and paths
        else NATIVE_EVENT_MAP.get(native_event)
    )
    if event is None:
        return None, {}, None, [f"Unsupported native hook event: {native_event}"]

    identifiers: dict[str, str] = {}
    for portable_name, keys in {
        "session_id": ("session_id", "sessionId"),
        "turn_id": ("turn_id", "turnId"),
        "tool_use_id": ("tool_use_id", "toolUseId"),
    }.items():
        for key in keys:
            value = native_payload.get(key)
            if isinstance(value, str) and value:
                identifiers[portable_name] = value
                break
    normalized = {
        "provider": provider,
        "native_event": native_event,
        "native_payload_sha256": _digest(native_payload),
        "tool_name": tool_name,
        "paths": paths,
        **identifiers,
    }
    idempotency_key = "native:" + _digest(
        {
            "provider": provider,
            "native_event": native_event,
            "identifiers": identifiers,
            "tool_name": tool_name,
            "paths": paths,
            "native_payload_sha256": normalized["native_payload_sha256"],
        }
    )
    return event, normalized, idempotency_key, []


def native_hook_output(
    provider: str,
    native_event: str,
    record: dict[str, Any],
    procedures: dict[str, str],
    errors: list[str],
) -> dict[str, Any] | None:
    """Format bounded feedback using the native host's hook response contract."""
    messages: list[str] = []
    if procedures:
        messages.append(
            "AAE requested the following registered procedure(s):\n\n"
            + "\n\n".join(procedures.values())
        )
    if errors:
        messages.append("AAE hook errors: " + "; ".join(errors))
    failed = record.get("status") in {
        "failed", "denied", "configuration-invalid", "chain-depth-denied",
        "action-budget-denied",
    }
    if failed and not errors:
        messages.append(f"AAE event handling ended with status {record.get('status')}.")
    if not messages:
        return None
    context = "\n\n".join(messages)
    if len(context) > MAX_NATIVE_CONTEXT_CHARS:
        context = context[:MAX_NATIVE_CONTEXT_CHARS] + "\n\n[AAE output truncated]"
    if provider == "codex":
        output: dict[str, Any] = {
            "hookSpecificOutput": {
                "hookEventName": native_event,
                "additionalContext": context,
            }
        }
        if failed and native_event == "PostToolUse":
            output.update({"decision": "block", "reason": context})
        return output
    return {"additionalContext": context}


def process_native_hook(
    start: Path,
    provider: str,
    native_payload: dict[str, Any],
    native_event_override: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    """Adapt one platform-native hook delivery into AAE's event/action rules."""
    root = find_aae_root(start)
    if root is None:
        return None, None, []
    if native_event_override and _native_event_name(native_payload) is None:
        native_payload = {**native_payload, "hook_event_name": native_event_override}
    event, payload, idempotency_key, normalization_errors = normalize_native_hook(
        root, provider, native_payload
    )
    native_event = _native_event_name(native_payload) or "PostToolUse"
    if event is None:
        empty = {"status": "normalization-failed", "actions": []}
        return empty, native_hook_output(
            provider, native_event, empty, {}, normalization_errors
        ), normalization_errors
    record, procedures, errors = process_event(
        root,
        event=event,
        payload=payload,
        idempotency_key=idempotency_key,
        record_no_match=False,
    )
    return record, native_hook_output(
        provider, native_event, record, procedures, errors
    ), errors


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
