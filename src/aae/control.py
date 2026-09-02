from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable
import uuid

from .skills import discover_skills, load_skill_instructions, resolve_skill_metadata


INVOCATION_DIRECTORY = Path(".aae/runtime/invocations")


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


def invoke_skill(
    root: Path,
    registry: dict[str, Any],
    *,
    task: str,
    explicit_skill: str | None = None,
    explicit_capabilities: Iterable[str] = (),
    architecture: Iterable[str] = (),
    environment: Iterable[str] = (),
    risks: Iterable[str] = (),
    evidence_gaps: Iterable[str] = (),
    task_id: str | None = None,
    spec_id: str | None = None,
    context_evidence_sha256: str | None = None,
    runtime_profile: dict[str, Any] | None = None,
    candidate_limit: int = 18,
    shortlist_limit: int = 4,
    trigger_provenance: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None, list[str]]:
    """Discover one skill, enforce v1 safety checks, then load its procedure."""
    profile = runtime_profile or {}
    errors: list[str] = []
    capabilities = [value.strip() for value in explicit_capabilities if value.strip()]
    discovery = discover_skills(
        registry,
        task=task,
        capabilities=capabilities,
        architecture=architecture,
        environment=environment,
        risks=risks,
        evidence_gaps=evidence_gaps,
        candidate_limit=candidate_limit,
        limit=shortlist_limit,
    )

    selected: dict[str, Any] | None = None
    reason = "no matching skill"
    if explicit_skill:
        selected, error = resolve_skill_metadata(registry, explicit_skill)
        if error:
            errors.append(error)
        else:
            reason = "explicitly requested"
    elif discovery["shortlist"]:
        selected, error = resolve_skill_metadata(
            registry, discovery["shortlist"][0]["registry_id"]
        )
        if error:
            errors.append(error)
        else:
            reason = "best advertisement match"

    checks: list[dict[str, Any]] = []
    rejections: list[str] = []
    if selected is None:
        rejections.append("no-skill-selected")
    else:
        available_tools = set(profile.get("available_tools", []))
        missing_tools = sorted(set(selected.get("requires_tools", [])) - available_tools)
        checks.append(
            {"check": "required-tools", "passed": not missing_tools, "missing": missing_tools}
        )
        if missing_tools:
            rejections.append("missing-tools:" + ",".join(missing_tools))

        destructive_allowed = (
            not selected.get("destructive", False)
            or "destructive" in set(profile.get("approvals", []))
        )
        checks.append({"check": "destructive-approval", "passed": destructive_allowed})
        if not destructive_allowed:
            rejections.append("destructive-approval-required")

        independence_allowed = (
            not selected.get("independence_required", False)
            or bool(profile.get("fresh_context", False))
        )
        checks.append({"check": "fresh-context", "passed": independence_allowed})
        if not independence_allowed:
            rejections.append("fresh-context-required")

    decision = "allowed" if selected is not None and not rejections and not errors else "denied"
    invocation_id = str(uuid.uuid4())
    record: dict[str, Any] = {
        "schema_version": 1,
        "invocation_id": invocation_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "task": {"identity": task_id, "spec_identity": spec_id, "intent": task},
        "requested_capabilities": capabilities,
        "registry_content_sha256": registry["registry_content_sha256"],
        "candidates": discovery["shortlist"],
        "selected_skill": None
        if selected is None
        else {
            "registry_id": selected["registry_id"],
            "name": selected["name"],
            "version": selected["version"],
            "skill_content_sha256": selected["skill_content_sha256"],
        },
        "selection_reason": reason,
        "safety": {
            "decision": decision,
            "checks": checks,
            "rejection_reasons": rejections,
        },
        "context_evidence_sha256": context_evidence_sha256,
        "trigger_provenance": trigger_provenance,
        "status": "planned" if decision == "allowed" else "denied",
        "procedure_loaded": False,
        "execution": None,
        "outcome": None,
    }

    procedure: str | None = None
    if selected is not None and decision == "allowed":
        authorization = {
            "decision": "allowed",
            "registry_content_sha256": registry["registry_content_sha256"],
            "skill_content_sha256": selected["skill_content_sha256"],
        }
        skill, procedure, load_error = load_skill_instructions(
            registry, selected["registry_id"], authorization=authorization
        )
        if load_error:
            errors.append(load_error)
            record["status"] = "load-failed"
            record["safety"]["decision"] = "denied"
            record["safety"]["rejection_reasons"].append(
                f"procedure-load:{load_error}"
            )
            procedure = None
        else:
            assert skill is not None
            record["status"] = "procedure-loaded"
            record["procedure_loaded"] = True
            record["execution"] = {
                "skill_registry_id": skill["registry_id"],
                "skill_version": skill["version"],
                "skill_content_sha256": skill["skill_content_sha256"],
                "procedure_sha256": skill["procedure_sha256"],
                "loaded_at": datetime.now(timezone.utc).isoformat(),
            }

    record["invocation_record_sha256"] = _digest(
        {
            key: value
            for key, value in record.items()
            if key not in {"recorded_at", "invocation_record_sha256"}
        }
    )
    _write_json(root / INVOCATION_DIRECTORY / f"{invocation_id}.json", record)
    return record, procedure, errors


def record_invocation_outcome(
    root: Path,
    invocation_id: str,
    *,
    outcome: str,
    verification: str | None,
    evidence: str | None,
    context_tokens: int | None,
    execution_cost: float | None,
) -> str | None:
    if outcome not in {"succeeded", "failed", "superseded"}:
        return "Invocation outcome must be succeeded, failed, or superseded"
    if context_tokens is not None and context_tokens < 0:
        return "Context tokens must be non-negative"
    if execution_cost is not None and execution_cost < 0:
        return "Execution cost must be non-negative"
    path = root / INVOCATION_DIRECTORY / f"{invocation_id}.json"
    if not path.is_file():
        return f"Invocation record not found: {invocation_id}"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return f"Cannot read invocation record: {error}"
    if record.get("status") not in {"procedure-loaded", "completed", "failed"}:
        return f"Invocation {invocation_id} was not eligible for execution"
    record["outcome"] = {
        "result": outcome,
        "verification": verification,
        "evidence": evidence,
        "context_tokens": context_tokens,
        "execution_cost": execution_cost,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    record["status"] = (
        "completed"
        if outcome == "succeeded"
        else "superseded"
        if outcome == "superseded"
        else "failed"
    )
    record["invocation_record_sha256"] = _digest(
        {
            key: value
            for key, value in record.items()
            if key not in {"recorded_at", "invocation_record_sha256"}
        }
    )
    _write_json(path, record)
    return None
