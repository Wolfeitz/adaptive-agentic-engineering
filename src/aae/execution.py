from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Iterable
import uuid

from . import __version__
from .control import (
    invocation_record_digest_is_valid,
    invoke_skill,
    record_invocation_outcome,
)
from .skills import build_skill_registry


EXECUTION_CONFIG = Path(".aae/execution.json")
LOCAL_EXECUTION_CONFIG = Path(".aae/execution.local.json")
CONTEXT_PACKET_DIRECTORY = Path(".aae/runtime/context-packets")
EXECUTION_DIRECTORY = Path(".aae/runtime/executions")
FILESYSTEM_BOUNDARY_DIRECTORY = Path(".aae/runtime/filesystem-boundaries")
BOUNDARY_VERSION = "aae_bwrap_project_root_read_isolated_v1"

CONFIG_FIELDS = {
    "schema_version",
    "context_limits",
    "evidence_paths",
    "primary_executor",
    "review",
    "filesystem_boundary",
    "accounting_directory",
}
EXECUTOR_FIELDS = {
    "adapter",
    "command",
    "command_sha256",
    "model",
    "provider",
    "sandbox",
    "timeout_seconds",
    "available_tools",
    "model_capabilities",
    "data_classifications",
}
REVIEW_FIELDS = {"required", "skill", "executor"}
LIMIT_FIELDS = {"max_items", "max_files", "max_bytes", "max_estimated_tokens"}
EVIDENCE_PATH_FIELDS = {"allowed_prefixes", "denied_prefixes"}
BOUNDARY_FIELDS = {"version", "mode", "launcher", "launcher_sha256"}
SEMANTIC_EXECUTOR = "semantic-executor"
DETERMINISTIC_CONTROL = "deterministic-control"
FILESYSTEM_BOUNDARY_CRITERION = (
    "No protected scientific evidence is accessed or changed."
)

CODEX_RESULT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "role",
        "outcome",
        "review_verdict",
        "summary",
        "findings",
        "verification",
    ],
    "properties": {
        "role": {"type": "string", "enum": ["executor", "reviewer"]},
        "outcome": {
            "type": "string",
            "enum": ["succeeded", "failed", "blocked"],
        },
        "review_verdict": {
            "type": "string",
            "enum": ["not-applicable", "approved", "changes-required", "blocked"],
        },
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "statement", "evidence_refs"],
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["info", "warning", "error"],
                    },
                    "statement": {"type": "string"},
                    "evidence_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
        "verification": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["criterion", "status", "evidence_refs"],
                "properties": {
                    "criterion": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["passed", "failed", "blocked"],
                    },
                    "evidence_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
}


def _codex_result_schema(role: str) -> dict[str, Any]:
    if role not in {"executor", "reviewer"}:
        raise ValueError(f"unsupported Codex execution role: {role}")
    schema = json.loads(json.dumps(CODEX_RESULT_SCHEMA))
    schema["properties"]["role"]["enum"] = [role]
    schema["properties"]["review_verdict"]["enum"] = (
        ["not-applicable"]
        if role == "executor"
        else ["approved", "changes-required", "blocked"]
    )
    return schema


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _build_criterion_specs(
    semantic: Iterable[str], deterministic: Iterable[str]
) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    for authority, evaluator, values in (
        (SEMANTIC_EXECUTOR, "codex-cli-executor", semantic),
        (DETERMINISTIC_CONTROL, BOUNDARY_VERSION, deterministic),
    ):
        for value in values:
            statement = value.strip()
            if not statement:
                continue
            if authority == DETERMINISTIC_CONTROL and statement != (
                FILESYSTEM_BOUNDARY_CRITERION
            ):
                raise ValueError(
                    "unsupported deterministic-control criterion; "
                    "AAE has no evaluator for that statement"
                )
            body = {"statement": statement, "authority": authority, "evaluator": evaluator}
            specs.append({**body, "criterion_id": _digest(body)})
    statements = [spec["statement"] for spec in specs]
    if len(statements) != len(set(statements)):
        raise ValueError("governed acceptance criterion statements must be unique")
    if not any(spec["authority"] == SEMANTIC_EXECUTOR for spec in specs):
        raise ValueError("governed execution requires at least one semantic criterion")
    return specs


def _criterion_outcome(results: Iterable[dict[str, Any]]) -> str:
    statuses = [str(result["result"]) for result in results]
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "blocked" for status in statuses):
        return "blocked"
    return "succeeded"


def _evaluate_pre_review_criteria(
    specs: list[dict[str, str]],
    *,
    semantic_result: dict[str, Any],
    invocation_id: str,
    execution_id: str,
    execution_sha256: str,
    boundary_proof: object,
    invocation_plan_sha256: str,
    context_packet_sha256: str,
    project_root: Path,
    boundary_identity: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], str]:
    semantic_entries = {
        str(entry["criterion"]): entry for entry in semantic_result["verification"]
    }
    results: list[dict[str, Any]] = []
    for spec in specs:
        authority = spec["authority"]
        identity: dict[str, Any]
        if authority == SEMANTIC_EXECUTOR:
            entry = semantic_entries.get(spec["statement"])
            if entry is None:
                raise ValueError("semantic executor did not evaluate its assigned criterion")
            results.append(
                {
                    **spec,
                    "result": entry["status"],
                    "supporting_evidence_sha256": execution_sha256,
                    "responsible_identity": {
                        "kind": "semantic-invocation",
                        "invocation_id": invocation_id,
                        "execution_id": execution_id,
                    },
                }
            )
            continue
        if authority != DETERMINISTIC_CONTROL:
            raise ValueError(f"unsupported criterion authority: {authority}")
        if not isinstance(boundary_proof, dict):
            status = "blocked"
            evidence_sha256 = None
            identity = {"kind": "deterministic-control", "control": BOUNDARY_VERSION}
        else:
            root = project_root.resolve()
            attestation = boundary_proof.get("attestation")
            checks = (
                attestation.get("checks", {}) if isinstance(attestation, dict) else {}
            )
            proof_valid = (
                filesystem_boundary_proof_digest_is_valid(boundary_proof)
                and boundary_identity is not None
                and boundary_proof.get("invocation_plan_sha256")
                == invocation_plan_sha256
                and boundary_proof.get("context_packet_sha256")
                == context_packet_sha256
                and boundary_proof.get("execution_id") == execution_id
                and boundary_proof.get("launcher") == boundary_identity
                and boundary_proof.get("project_root") == str(root)
                and boundary_proof.get("protected_paths")
                == [str(root), str(root / ".armiosto")]
                and isinstance(checks, dict)
                and checks
                and all(value is True for value in checks.values())
            )
            status = (
                "passed"
                if proof_valid and boundary_proof.get("status") == "passed"
                else "failed"
            )
            evidence_sha256 = boundary_proof.get("proof_sha256")
            identity = {
                "kind": "deterministic-control",
                "control": BOUNDARY_VERSION,
                "execution_id": boundary_proof.get("execution_id"),
            }
        results.append(
            {
                **spec,
                "result": status,
                "supporting_evidence_sha256": evidence_sha256,
                "responsible_identity": identity,
            }
        )
    if len(results) != len(specs):
        raise ValueError("criterion evaluators did not produce a complete result set")
    return results, _criterion_outcome(results)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def controller_identity() -> dict[str, Any]:
    package_root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    files: list[dict[str, Any]] = []
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root).as_posix()
        content_digest = _file_sha256(path)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(content_digest))
        files.append({"path": relative, "sha256": content_digest})
    return {
        "name": "adaptive-agentic-engineering",
        "version": __version__,
        "package_root": str(package_root),
        "source_file_count": len(files),
        "source_content_sha256": digest.hexdigest(),
    }


def governed_run_digest(record: dict[str, Any]) -> str:
    return _digest({key: value for key, value in record.items() if key != "run_sha256"})


def governed_run_digest_is_valid(record: dict[str, Any]) -> bool:
    return record.get("run_sha256") == governed_run_digest(record)


def execution_artifact_digest_is_valid(record: dict[str, Any]) -> bool:
    return record.get("execution_sha256") == _digest(
        {key: value for key, value in record.items() if key != "execution_sha256"}
    )


def filesystem_boundary_proof_digest_is_valid(record: dict[str, Any]) -> bool:
    return record.get("proof_sha256") == _digest(
        {key: value for key, value in record.items() if key != "proof_sha256"}
    )


class CodexExecutionRejected(ValueError):
    """A launched Codex process whose output failed deterministic AAE validation."""

    def __init__(self, message: str, artifact: dict[str, Any]) -> None:
        super().__init__(message)
        self.artifact = artifact


def context_packet_digest_is_valid(record: dict[str, Any]) -> bool:
    return record.get("packet_sha256") == _digest(
        {key: value for key, value in record.items() if key != "packet_sha256"}
    )


def _write_canonical_exclusive(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _merge_executor(
    portable: dict[str, Any], local: object, location: str
) -> dict[str, Any]:
    if local is None:
        return dict(portable)
    if not isinstance(local, dict):
        raise ValueError(f"{location} must be an object")
    unknown = sorted(set(local) - {"command", "command_sha256", "model"})
    if unknown:
        raise ValueError(f"{location} has unsupported local fields: {unknown}")
    return {**portable, **local}


def _merge_boundary(portable: object, local: object) -> dict[str, Any] | None:
    if portable is None:
        if local is not None:
            raise ValueError("local filesystem_boundary requires a portable policy")
        return None
    if not isinstance(portable, dict) or set(portable) != BOUNDARY_FIELDS:
        raise ValueError(
            f"filesystem_boundary must contain exactly {sorted(BOUNDARY_FIELDS)}"
        )
    if local is None:
        return dict(portable)
    if not isinstance(local, dict):
        raise ValueError("local filesystem_boundary must be an object")
    unknown = sorted(set(local) - {"launcher", "launcher_sha256"})
    if unknown:
        raise ValueError(
            f"local filesystem_boundary has unsupported fields: {unknown}"
        )
    return {**portable, **local}


def load_execution_configuration(
    root: Path, *, require_effective_executor: bool = True
) -> dict[str, Any]:
    path = root / EXECUTION_CONFIG
    if not path.is_file():
        raise ValueError(f"governed execution configuration is missing: {EXECUTION_CONFIG}")
    portable = _read_json_object(path)
    unknown = sorted(set(portable) - CONFIG_FIELDS)
    if unknown:
        raise ValueError(f"execution configuration has unknown fields: {unknown}")
    if portable.get("schema_version") != 1:
        raise ValueError("execution configuration schema_version must be 1")

    local: dict[str, Any] = {}
    local_path = root / LOCAL_EXECUTION_CONFIG
    if local_path.exists():
        local = _read_json_object(local_path)
        local_unknown = sorted(
            set(local)
            - {"schema_version", "primary_executor", "review", "filesystem_boundary"}
        )
        if local_unknown:
            raise ValueError(f"local execution configuration has unknown fields: {local_unknown}")
        if local.get("schema_version") != 1:
            raise ValueError("local execution configuration schema_version must be 1")

    limits = portable.get("context_limits")
    if not isinstance(limits, dict) or set(limits) != LIMIT_FIELDS:
        raise ValueError(f"context_limits must contain exactly {sorted(LIMIT_FIELDS)}")
    for field in LIMIT_FIELDS:
        value = limits.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"context_limits.{field} must be a positive integer")
    evidence_policy = portable.get("evidence_paths")
    if not isinstance(evidence_policy, dict) or set(evidence_policy) != EVIDENCE_PATH_FIELDS:
        raise ValueError(
            f"evidence_paths must contain exactly {sorted(EVIDENCE_PATH_FIELDS)}"
        )
    for field in EVIDENCE_PATH_FIELDS:
        values = evidence_policy[field]
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise ValueError(f"evidence_paths.{field} must contain non-empty paths")
        for value in values:
            candidate = Path(value)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError(
                    f"evidence_paths.{field} entries must be project-relative"
                )

    primary = portable.get("primary_executor")
    review = portable.get("review")
    if not isinstance(primary, dict):
        raise ValueError("primary_executor must be an object")
    if not isinstance(review, dict):
        raise ValueError("review must be an object")
    if set(primary) - EXECUTOR_FIELDS:
        raise ValueError("primary_executor has unknown fields")
    if set(review) - REVIEW_FIELDS:
        raise ValueError("review has unknown fields")
    review_executor = review.get("executor")
    if not isinstance(review_executor, dict) or set(review_executor) - EXECUTOR_FIELDS:
        raise ValueError("review.executor must be a valid executor object")

    local_review = local.get("review", {})
    if not isinstance(local_review, dict) or set(local_review) - {"executor"}:
        raise ValueError("local review configuration may contain only executor")
    effective_primary = _merge_executor(
        primary, local.get("primary_executor"), "local primary_executor"
    )
    effective_review = _merge_executor(
        review_executor, local_review.get("executor"), "local review.executor"
    )
    boundary = _merge_boundary(
        portable.get("filesystem_boundary"), local.get("filesystem_boundary")
    )
    if boundary is not None:
        if boundary["version"] != BOUNDARY_VERSION:
            raise ValueError(f"filesystem_boundary.version must be {BOUNDARY_VERSION}")
        if boundary["mode"] != "project-root-read-isolated":
            raise ValueError(
                "filesystem_boundary.mode must be project-root-read-isolated"
            )
        launcher = boundary["launcher"]
        if not isinstance(launcher, str) or not launcher.strip():
            raise ValueError("filesystem_boundary.launcher must be non-empty text")
        launcher_sha256 = boundary["launcher_sha256"]
        if require_effective_executor:
            if not isinstance(launcher_sha256, str) or not re.fullmatch(
                r"[0-9a-f]{64}", launcher_sha256
            ):
                raise ValueError(
                    "filesystem_boundary.launcher_sha256 must bind the launcher"
                )
        elif launcher_sha256 is not None and (
            not isinstance(launcher_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", launcher_sha256)
        ):
            raise ValueError(
                "filesystem_boundary.launcher_sha256 must be null or a SHA-256 digest"
            )
    for location, executor in (
        ("primary_executor", effective_primary),
        ("review.executor", effective_review),
    ):
        missing = sorted(EXECUTOR_FIELDS - set(executor))
        if missing:
            raise ValueError(f"{location} is missing fields: {missing}")
        if executor["adapter"] != "codex-cli":
            raise ValueError(f"{location}.adapter must be codex-cli")
        for field in ("command", "provider"):
            if not isinstance(executor[field], str) or not executor[field].strip():
                raise ValueError(f"{location}.{field} must be explicit non-empty text")
        model = executor["model"]
        if require_effective_executor:
            if not isinstance(model, str) or not model.strip():
                raise ValueError(f"{location}.model must be explicit non-empty text")
        elif model is not None and (
            not isinstance(model, str) or not model.strip()
        ):
            raise ValueError(f"{location}.model must be null or non-empty text")
        command_sha256 = executor["command_sha256"]
        if require_effective_executor:
            if not isinstance(command_sha256, str) or not re.fullmatch(
                r"[0-9a-f]{64}", command_sha256
            ):
                raise ValueError(
                    f"{location}.command_sha256 must bind the installed executable"
                )
        elif command_sha256 is not None and (
            not isinstance(command_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", command_sha256)
        ):
            raise ValueError(
                f"{location}.command_sha256 must be null or a SHA-256 digest"
            )
        if executor["sandbox"] not in {"read-only", "workspace-write"}:
            raise ValueError(f"{location}.sandbox must be read-only or workspace-write")
        timeout = executor["timeout_seconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise ValueError(f"{location}.timeout_seconds must be a positive integer")
        for field in ("available_tools", "model_capabilities", "data_classifications"):
            values = executor[field]
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise ValueError(f"{location}.{field} must be a list of non-empty strings")
    if not isinstance(review.get("required"), bool):
        raise ValueError("review.required must be true or false")
    if not isinstance(review.get("skill"), str) or not review["skill"].strip():
        raise ValueError("review.skill must be non-empty text")

    accounting = portable.get("accounting_directory")
    if not isinstance(accounting, str) or not accounting.strip():
        raise ValueError("accounting_directory must be non-empty text")
    accounting_path = (root / accounting).resolve()
    if not accounting_path.is_relative_to(root.resolve()):
        raise ValueError("accounting_directory must remain under the project root")

    effective = {
        **portable,
        "primary_executor": effective_primary,
        "review": {**review, "executor": effective_review},
        "filesystem_boundary": boundary,
        "portable_config_sha256": _digest(portable),
    }
    effective["effective_config_sha256"] = _digest(effective)
    return effective


def build_context_packet(
    root: Path,
    *,
    task_id: str,
    task: str,
    acceptance_criteria: Iterable[str],
    evidence_paths: Iterable[Path],
    limits: dict[str, int],
    evidence_policy: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    if not task_id.strip() or not task.strip():
        raise ValueError("context packet task_id and task must be non-empty text")
    items: list[dict[str, Any]] = []
    raw_bytes = 0
    for raw_path in evidence_paths:
        candidate = raw_path if raw_path.is_absolute() else root / raw_path
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"evidence path escapes the project root: {raw_path}")
        relative = resolved.relative_to(root).as_posix()
        if evidence_policy is not None:
            allowed = evidence_policy["allowed_prefixes"]
            denied = evidence_policy["denied_prefixes"]
            if any(
                relative == prefix.rstrip("/")
                or relative.startswith(prefix.rstrip("/") + "/")
                for prefix in denied
            ):
                raise PermissionError(f"evidence path is denied by policy: {relative}")
            if allowed and not any(
                relative == prefix.rstrip("/")
                or relative.startswith(prefix.rstrip("/") + "/")
                for prefix in allowed
            ):
                raise PermissionError(f"evidence path is outside allowed policy: {relative}")
        if not resolved.is_file():
            raise ValueError(f"evidence path is not a file: {raw_path}")
        raw_bytes += resolved.stat().st_size
        if raw_bytes > limits["max_bytes"]:
            raise ValueError(
                "context packet exceeds policy before file materialization: "
                f"evidence_bytes={raw_bytes}>max_bytes={limits['max_bytes']}"
            )
        content_bytes = resolved.read_bytes()
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"evidence file is not UTF-8 text: {raw_path}") from error
        items.append(
            {
                "path": relative,
                "bytes": len(content_bytes),
                "sha256": hashlib.sha256(content_bytes).hexdigest(),
                "content": content,
            }
        )
    return _finalize_context_packet(
        task_id=task_id,
        task=task,
        acceptance_criteria=acceptance_criteria,
        items=items,
        limits=limits,
    )


def _finalize_context_packet(
    *,
    task_id: str,
    task: str,
    acceptance_criteria: Iterable[str],
    items: list[dict[str, Any]],
    limits: dict[str, int],
) -> dict[str, Any]:
    paths = [item["path"] for item in items]
    if len(paths) != len(set(paths)):
        raise ValueError("context packet evidence paths must be unique")
    criteria = [value.strip() for value in acceptance_criteria if value.strip()]
    if not criteria:
        raise ValueError("context packet requires at least one acceptance criterion")
    if not items:
        raise ValueError("context packet requires at least one evidence file")
    packet_body: dict[str, Any] = {
        "schema_version": 1,
        "task_id": task_id,
        "task": task,
        "acceptance_criteria": criteria,
        "items": items,
        "limits": dict(sorted(limits.items())),
    }
    measured_bytes = len(_canonical_bytes(packet_body))
    measured = {
        "items": 1 + len(criteria) + len(items),
        "files": len(items),
        "bytes": measured_bytes,
        "estimated_tokens_upper_bound": measured_bytes,
    }
    comparisons = {
        "items": "max_items",
        "files": "max_files",
        "bytes": "max_bytes",
        "estimated_tokens_upper_bound": "max_estimated_tokens",
    }
    exceeded = [
        f"{name}={measured[name]}>{limit_name}={limits[limit_name]}"
        for name, limit_name in comparisons.items()
        if measured[name] > limits[limit_name]
    ]
    if exceeded:
        raise ValueError("context packet exceeds policy: " + ", ".join(exceeded))
    packet: dict[str, Any] = {**packet_body, "measured": measured}
    packet["packet_sha256"] = _digest(packet)
    return packet


def _validate_codex_result(value: object, expected_role: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Codex executor output must be a JSON object")
    required = set(CODEX_RESULT_SCHEMA["required"])
    if set(value) != required:
        raise ValueError(f"Codex executor output fields must be exactly {sorted(required)}")
    if value.get("role") != expected_role:
        raise ValueError(f"Codex executor output role must be {expected_role}")
    if value.get("outcome") not in {"succeeded", "failed", "blocked"}:
        raise ValueError("Codex executor output outcome is invalid")
    if value.get("review_verdict") not in {
        "not-applicable",
        "approved",
        "changes-required",
        "blocked",
    }:
        raise ValueError("Codex executor review_verdict is invalid")
    if expected_role == "executor" and value["review_verdict"] != "not-applicable":
        raise ValueError("executor output must use review_verdict=not-applicable")
    if expected_role == "reviewer" and value["review_verdict"] == "not-applicable":
        raise ValueError("reviewer output must provide a review verdict")
    if not isinstance(value.get("summary"), str):
        raise ValueError("Codex executor summary must be text")
    for collection, required_fields in (
        ("findings", {"severity", "statement", "evidence_refs"}),
        ("verification", {"criterion", "status", "evidence_refs"}),
    ):
        entries = value.get(collection)
        if not isinstance(entries, list):
            raise ValueError(f"Codex executor {collection} must be a list")
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or set(entry) != required_fields:
                raise ValueError(f"Codex executor {collection}[{index}] is invalid")
            refs = entry.get("evidence_refs")
            if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
                raise ValueError(
                    f"Codex executor {collection}[{index}].evidence_refs is invalid"
                )
            if not refs:
                raise ValueError(
                    f"Codex executor {collection}[{index}] must cite packet evidence"
                )
            if collection == "findings":
                if entry.get("severity") not in {"info", "warning", "error"}:
                    raise ValueError(
                        f"Codex executor findings[{index}].severity is invalid"
                    )
                if not isinstance(entry.get("statement"), str):
                    raise ValueError(
                        f"Codex executor findings[{index}].statement is invalid"
                    )
            else:
                if entry.get("status") not in {"passed", "failed", "blocked"}:
                    raise ValueError(
                        f"Codex executor verification[{index}].status is invalid"
                    )
                if not isinstance(entry.get("criterion"), str):
                    raise ValueError(
                        f"Codex executor verification[{index}].criterion is invalid"
                    )
    return value


def _validate_result_against_packet(
    result: dict[str, Any], packet: dict[str, Any]
) -> str:
    expected = list(packet["acceptance_criteria"])
    observed = [entry["criterion"] for entry in result["verification"]]
    if observed != expected:
        raise ValueError(
            "Codex executor must verify every acceptance criterion exactly once "
            "in packet order"
        )
    statuses = [entry["status"] for entry in result["verification"]]
    if any(status == "failed" for status in statuses):
        derived_outcome = "failed"
    elif any(status == "blocked" for status in statuses):
        derived_outcome = "blocked"
    else:
        derived_outcome = "succeeded"
    if result["outcome"] != derived_outcome:
        raise ValueError(
            "Codex executor informational outcome disagrees with the "
            f"deterministic AAE outcome: reported={result['outcome']}, "
            f"derived={derived_outcome}"
        )
    if derived_outcome == "succeeded" and any(
        finding["severity"] == "error" for finding in result["findings"]
    ):
        raise ValueError("a succeeded Codex result cannot contain an error finding")
    return derived_outcome


def _prompt(
    *,
    role: str,
    task: str,
    procedure: str,
    packet: dict[str, Any],
) -> str:
    constraints = (
        "You are an ephemeral AAE executor. The deterministic AAE control plane, not "
        "you, owns authorization and durable state. Perform only the task below using "
        "only the provided procedure and bounded evidence packet. Do not inspect the "
        "host, repository, environment, conversation history, or any unlisted file. "
        "Do not authorize a write or state transition. Evidence refs must name provided "
        "packet paths. Report every acceptance criterion exactly once and in packet "
        "order. The outcome field is informational: report failed if any criterion "
        "failed, otherwise blocked if any criterion is blocked, otherwise succeeded. "
        "AAE derives the authoritative outcome independently. Return only the required "
        "structured result."
    )
    return "\n\n".join(
        [
            constraints,
            f"ROLE\n{role}",
            f"TASK\n{task}",
            f"AUTHORIZED PROCEDURE\n{procedure}",
            "BOUNDED EVIDENCE PACKET\n" + _canonical_bytes(packet).decode("utf-8"),
        ]
    )


def resolve_executor_identity(executor: dict[str, Any]) -> dict[str, Any]:
    if executor.get("adapter") != "codex-cli":
        raise ValueError("the governed execution adapter must be codex-cli")
    binary_value = shutil.which(str(executor["command"]))
    if binary_value is None:
        raise ValueError(f"Codex CLI executable is unavailable: {executor['command']}")
    binary = Path(binary_value).resolve()
    if binary.name != "codex":
        raise ValueError("codex-cli adapter command must resolve to an executable named codex")
    observed_sha256 = _file_sha256(binary)
    if observed_sha256 != executor["command_sha256"]:
        raise ValueError("Codex CLI executable does not match the configured digest")
    completed = subprocess.run(
        [str(binary), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env=_executor_environment(),
    )
    return {
        "adapter": "codex-cli",
        "command": "codex",
        "resolved_path": str(binary),
        "command_sha256": observed_sha256,
        "command_version": completed.stdout.strip(),
    }


def resolve_filesystem_boundary(boundary: dict[str, Any] | None) -> dict[str, Any] | None:
    if boundary is None:
        return None
    launcher_value = shutil.which(str(boundary["launcher"]))
    if launcher_value is None:
        raise ValueError(
            f"filesystem boundary launcher is unavailable: {boundary['launcher']}"
        )
    launcher = Path(launcher_value).resolve()
    observed_sha256 = _file_sha256(launcher)
    if observed_sha256 != boundary["launcher_sha256"]:
        raise ValueError("filesystem boundary launcher does not match configured digest")
    completed = subprocess.run(
        [str(launcher), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env=_executor_environment(),
    )
    return {
        "version": boundary["version"],
        "mode": boundary["mode"],
        "launcher": launcher.name,
        "resolved_path": str(launcher),
        "launcher_sha256": observed_sha256,
        "launcher_version": completed.stdout.strip(),
    }


def _boundary_helper_source() -> str:
    return """from __future__ import annotations
import errno
import json
import os
from pathlib import Path
import sys

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
project = Path(config["project_root"])
workspace = Path(config["workspace"])
checks = {
    "project_root_empty": project.is_dir() and list(project.iterdir()) == [],
    "project_canary_hidden": not (project / config["project_canary_relative"]).exists(),
    "host_tmp_canary_hidden": not Path(config["host_tmp_canary"]).exists(),
    "protected_store_hidden": not (project / ".armiosto").exists(),
}
codex_state = Path(config["codex_state"])
state_probe = codex_state / "aae-state-write-probe"
state_probe.write_text("allowed", encoding="utf-8")
checks["codex_state_writable"] = state_probe.read_text(encoding="utf-8") == "allowed"
state_probe.unlink()
checks["codex_auth_readable"] = (
    not config["codex_auth_required"] or Path(config["codex_auth"]).is_file()
)
probe = project / ".aae-boundary-write-probe"
write_errno = None
try:
    probe.write_text("forbidden", encoding="utf-8")
except OSError as error:
    write_errno = error.errno
checks["project_root_write_denied"] = write_errno in {errno.EROFS, errno.EACCES, errno.EPERM}
workspace_probe = workspace / "workspace-write-probe"
workspace_probe.write_text("allowed", encoding="utf-8")
checks["executor_workspace_writable"] = workspace_probe.read_text(encoding="utf-8") == "allowed"
workspace_probe.unlink()
attestation = {
    "schema_version": 1,
    "boundary_version": config["boundary_version"],
    "nonce": config["nonce"],
    "checks": checks,
}
print("AAE_RUNTIME_BOUNDARY " + json.dumps(attestation, sort_keys=True, separators=(",", ":")), flush=True)
if not all(checks.values()):
    raise SystemExit(125)
os.execv(config["command"], [config["command"], *sys.argv[2:]])
"""


def _run_with_filesystem_boundary(
    *,
    root: Path,
    workspace: Path,
    argv: list[str],
    prompt: str,
    timeout: int,
    environment: dict[str, str],
    execution_id: str,
    plan_sha256: str,
    packet_sha256: str,
    boundary_identity: dict[str, Any],
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    root = root.resolve()
    workspace = workspace.resolve()
    nonce = uuid.uuid4().hex
    project_canary_relative = (
        FILESYSTEM_BOUNDARY_DIRECTORY / "canaries" / f"{execution_id}.json"
    )
    project_canary = root / project_canary_relative
    project_canary_value = {
        "schema_version": 1,
        "execution_id": execution_id,
        "nonce": nonce,
    }
    _write_canonical_exclusive(project_canary, project_canary_value)
    project_canary_sha256 = _file_sha256(project_canary)
    descriptor, host_tmp_name = tempfile.mkstemp(prefix="aae-boundary-host-canary-")
    host_tmp_canary = Path(host_tmp_name)
    os.write(descriptor, nonce.encode("ascii"))
    os.fsync(descriptor)
    os.close(descriptor)
    host_tmp_sha256 = _file_sha256(host_tmp_canary)
    helper_path = workspace / "boundary-helper.py"
    helper_path.write_text(_boundary_helper_source(), encoding="utf-8")
    helper_sha256 = _file_sha256(helper_path)
    namespace_workspace = "/tmp/aae-workspace"
    namespace_command = "/tmp/aae-codex-command"
    namespace_config = f"{namespace_workspace}/boundary-config.json"
    config: dict[str, Any] = {
        "boundary_version": BOUNDARY_VERSION,
        "nonce": nonce,
        "project_root": str(root),
        "workspace": namespace_workspace,
        "project_canary_relative": project_canary_relative.as_posix(),
        "host_tmp_canary": str(host_tmp_canary),
        "command": namespace_command,
    }
    codex_state = Path(
        environment.get("CODEX_HOME")
        or (Path(environment["HOME"]) / ".codex")
    ).resolve()
    codex_auth = codex_state / "auth.json"
    config.update(
        {
            "codex_state": str(codex_state),
            "codex_auth": str(codex_auth),
            "codex_auth_required": codex_auth.is_file(),
        }
    )
    (workspace / "boundary-config.json").write_bytes(_canonical_bytes(config) + b"\n")
    translated_argv = [
        namespace_command if value == argv[0] else
        value.replace(str(workspace), namespace_workspace)
        for value in argv
    ]
    bwrap_argv = [
        boundary_identity["resolved_path"],
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--share-net",
        "--ro-bind", "/", "/",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--tmpfs", "/run",
        "--tmpfs", str(codex_state),
        *(
            ["--ro-bind", str(codex_auth), str(codex_auth)]
            if codex_auth.is_file()
            else []
        ),
        "--bind", str(workspace), namespace_workspace,
        "--ro-bind", argv[0], namespace_command,
        "--tmpfs", str(root),
        "--remount-ro", str(root),
        "--chdir", namespace_workspace,
        "/usr/bin/python3",
        f"{namespace_workspace}/boundary-helper.py",
        namespace_config,
        *translated_argv[1:],
    ]
    started = time.monotonic_ns()
    timed_out = False
    try:
        try:
            completed = subprocess.run(
                bwrap_argv,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                cwd=workspace,
                env=environment,
            )
        except subprocess.TimeoutExpired as error:
            timed_out = True
            stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else error.stdout
            stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else error.stderr
            completed = subprocess.CompletedProcess(
                bwrap_argv,
                124,
                stdout or "",
                stderr or "filesystem boundary execution timed out",
            )
    finally:
        duration_ns = time.monotonic_ns() - started
    prefix = "AAE_RUNTIME_BOUNDARY "
    attestation_lines = [
        line[len(prefix):]
        for line in completed.stdout.splitlines()
        if line.startswith(prefix)
    ]
    attestation: object | None = None
    attestation_error: str | None = None
    if len(attestation_lines) == 1:
        try:
            attestation = json.loads(attestation_lines[0])
        except json.JSONDecodeError as error:
            attestation_error = f"invalid-json:{error.msg}"
    else:
        attestation_error = f"attestation-count:{len(attestation_lines)}"
    expected_checks = {
        "project_root_empty": True,
        "project_canary_hidden": True,
        "host_tmp_canary_hidden": True,
        "protected_store_hidden": True,
        "codex_state_writable": True,
        "codex_auth_readable": True,
        "project_root_write_denied": True,
        "executor_workspace_writable": True,
    }
    attestation_valid = (
        isinstance(attestation, dict)
        and attestation.get("boundary_version") == BOUNDARY_VERSION
        and attestation.get("nonce") == nonce
        and attestation.get("checks") == expected_checks
    )
    project_post_sha256 = _file_sha256(project_canary) if project_canary.is_file() else None
    host_tmp_post_sha256 = (
        _file_sha256(host_tmp_canary) if host_tmp_canary.is_file() else None
    )
    canaries_valid = (
        project_post_sha256 == project_canary_sha256
        and host_tmp_post_sha256 == host_tmp_sha256
    )
    host_tmp_canary.unlink(missing_ok=True)
    passed = attestation_valid and canaries_valid and not timed_out
    proof: dict[str, Any] = {
        "schema_version": 1,
        "boundary_version": BOUNDARY_VERSION,
        "mode": "project-root-read-isolated",
        "execution_id": execution_id,
        "invocation_plan_sha256": plan_sha256,
        "context_packet_sha256": packet_sha256,
        "launcher": boundary_identity,
        "helper_sha256": helper_sha256,
        "project_root": str(root),
        "protected_paths": [str(root), str(root / ".armiosto")],
        "namespace_policy": {
            "host_root": "read-only",
            "project_root": "empty-read-only",
            "tmp": "isolated",
            "run": "isolated",
            "executor_workspace": "read-write",
            "codex_state": "isolated-write-with-read-only-auth",
        },
        "status": "passed" if passed else "failed",
        "attestation": attestation,
        "attestation_error": attestation_error,
        "canaries": {
            "project_before_sha256": project_canary_sha256,
            "project_after_sha256": project_post_sha256,
            "host_tmp_before_sha256": host_tmp_sha256,
            "host_tmp_after_sha256": host_tmp_post_sha256,
        },
        "duration_ns": duration_ns,
    }
    proof["proof_sha256"] = _digest(proof)
    return completed, proof


def _executor_environment() -> dict[str, str]:
    allowed = (
        "HOME",
        "CODEX_HOME",
        "PATH",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    )
    return {name: os.environ[name] for name in allowed if name in os.environ}


def run_codex_cli(
    root: Path,
    *,
    invocation_record: dict[str, Any],
    procedure: str,
    packet: dict[str, Any],
    executor: dict[str, Any],
    executor_identity: dict[str, Any],
    filesystem_boundary: dict[str, Any] | None,
    role: str,
) -> dict[str, Any]:
    if invocation_record.get("status") != "procedure-loaded":
        raise ValueError("executor cannot run an invocation that policy did not authorize")
    plan = invocation_record.get("invocation_plan")
    if not isinstance(plan, dict) or plan.get("policy", {}).get("decision") != "allowed":
        raise ValueError("executor cannot bypass a denied InvocationPlan")
    if not invocation_record_digest_is_valid(invocation_record):
        raise ValueError("executor invocation record digest is invalid")
    binding = plan.get("binding")
    if not isinstance(binding, dict):
        raise ValueError("authorized invocation has no runtime binding")
    if binding.get("provider") != executor["provider"] or binding.get("model") != executor["model"]:
        raise ValueError("executor provider/model does not match the authorized binding")
    if binding.get("executor_identity") != executor_identity:
        raise ValueError("executor identity does not match the authorized binding")
    if binding.get("filesystem_boundary") != filesystem_boundary:
        raise ValueError("filesystem boundary does not match the authorized binding")
    bound_criteria = binding.get("criteria")
    if not isinstance(bound_criteria, list):
        raise ValueError("authorized invocation has no criterion authority binding")
    semantic_projection = [
        criterion.get("statement")
        for criterion in bound_criteria
        if isinstance(criterion, dict)
        and criterion.get("authority") == SEMANTIC_EXECUTOR
    ]
    if packet.get("acceptance_criteria") != semantic_projection:
        raise ValueError("executor packet does not match its semantic criterion authority")
    if invocation_record.get("context_evidence_sha256") != packet.get("packet_sha256"):
        raise ValueError("executor context packet does not match the authorized digest")
    if packet.get("packet_sha256") != _digest(
        {key: value for key, value in packet.items() if key != "packet_sha256"}
    ):
        raise ValueError("executor context packet digest is invalid")
    if hashlib.sha256(procedure.encode("utf-8")).hexdigest() != plan.get("skill", {}).get(
        "procedure_sha256"
    ):
        raise ValueError("executor procedure does not match the authorized digest")
    side_effects = binding.get("side_effects")
    required_sandbox = "read-only" if side_effects == "read-only" else "workspace-write"
    if side_effects not in {"read-only", "workspace-write"}:
        raise ValueError("Codex CLI adapter does not support external or destructive effects")
    if executor["sandbox"] != required_sandbox:
        raise ValueError("executor sandbox does not match authorized side effects")

    observed_identity = resolve_executor_identity(executor)
    if observed_identity != executor_identity:
        raise ValueError("Codex CLI executable identity drifted after authorization")
    binary = executor_identity["resolved_path"]
    execution_id = str(uuid.uuid4())
    started_ns = time.monotonic_ns()
    with tempfile.TemporaryDirectory(prefix="aae-codex-exec-") as temporary:
        workspace = Path(temporary)
        schema_path = workspace / "result-schema.json"
        result_path = workspace / "result.json"
        schema_path.write_bytes(_canonical_bytes(_codex_result_schema(role)) + b"\n")
        prompt = _prompt(role=role, task=str(packet["task"]), procedure=procedure, packet=packet)
        prompt_bytes = len(prompt.encode("utf-8"))
        limits = packet["limits"]
        if prompt_bytes > limits["max_bytes"]:
            raise ValueError(
                "executor prompt exceeds context byte policy: "
                f"bytes={prompt_bytes}>max_bytes={limits['max_bytes']}"
            )
        if prompt_bytes > limits["max_estimated_tokens"]:
            raise ValueError(
                "executor prompt exceeds conservative token policy: "
                f"upper_bound={prompt_bytes}>max_estimated_tokens="
                f"{limits['max_estimated_tokens']}"
            )
        argv = [
            binary,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            executor["sandbox"],
            "--cd",
            str(workspace),
            "--model",
            executor["model"],
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(result_path),
            "--json",
            "-",
        ]
        boundary_proof: dict[str, Any] | None = None
        if filesystem_boundary is None:
            completed = subprocess.run(
                argv,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=executor["timeout_seconds"],
                check=False,
                cwd=workspace,
                env=_executor_environment(),
            )
        else:
            observed_boundary = resolve_filesystem_boundary(
                {
                    "version": filesystem_boundary["version"],
                    "mode": filesystem_boundary["mode"],
                    "launcher": filesystem_boundary["resolved_path"],
                    "launcher_sha256": filesystem_boundary["launcher_sha256"],
                }
            )
            if observed_boundary != filesystem_boundary:
                raise ValueError("filesystem boundary identity drifted after authorization")
            completed, boundary_proof = _run_with_filesystem_boundary(
                root=root,
                workspace=workspace,
                argv=argv,
                prompt=prompt,
                timeout=executor["timeout_seconds"],
                environment=_executor_environment(),
                execution_id=execution_id,
                plan_sha256=plan["invocation_plan_sha256"],
                packet_sha256=packet["packet_sha256"],
                boundary_identity=filesystem_boundary,
            )
        duration_ns = time.monotonic_ns() - started_ns
        thread_ids: list[str] = []
        usage: dict[str, Any] | None = None
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
                thread_ids.append(event["thread_id"])
            if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
        raw_output = result_path.read_bytes() if result_path.is_file() else None
        raw_output_sha256 = (
            hashlib.sha256(raw_output).hexdigest() if raw_output is not None else None
        )
        parsed_output: object | None = None
        parsed_output_sha256: str | None = None
        result: dict[str, Any] | None = None
        authoritative_outcome: str | None = None
        validation_error: BaseException | None = None
        try:
            boundary_failed_after_execution = (
                boundary_proof is not None
                and boundary_proof.get("status") != "passed"
                and raw_output is not None
            )
            if (
                raw_output is None
                or completed.returncode != 0
                and not boundary_failed_after_execution
            ):
                raise RuntimeError(
                    "Codex CLI execution failed: "
                    f"exit={completed.returncode}, stderr_sha256="
                    f"{hashlib.sha256(completed.stderr.encode()).hexdigest()}"
                )
            parsed_output = json.loads(raw_output.decode("utf-8"))
            parsed_output_sha256 = _digest(parsed_output)
            result = _validate_codex_result(parsed_output, role)
            authoritative_outcome = _validate_result_against_packet(result, packet)
            allowed_paths = {str(item["path"]) for item in packet["items"]}
            for collection in ("findings", "verification"):
                for entry in result[collection]:
                    for reference in entry["evidence_refs"]:
                        if not any(
                            reference == path or reference.startswith(f"{path}:")
                            for path in allowed_paths
                        ):
                            raise ValueError(
                                "Codex executor cited evidence outside the bounded "
                                f"packet: {reference}"
                            )
            if len(thread_ids) != 1:
                raise RuntimeError(
                    "Codex CLI did not report exactly one fresh thread identity"
                )
        except (UnicodeError, json.JSONDecodeError, ValueError, RuntimeError) as error:
            validation_error = error

    validation_failure = (
        {
            "type": type(validation_error).__name__,
            "message": str(validation_error),
            "message_sha256": hashlib.sha256(
                str(validation_error).encode("utf-8")
            ).hexdigest(),
        }
        if validation_error is not None
        else None
    )
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "execution_id": execution_id,
        "invocation_id": invocation_record["invocation_id"],
        "invocation_plan_sha256": plan["invocation_plan_sha256"],
        "role": role,
        "adapter": "codex-cli",
        "provider": executor["provider"],
        "model": executor["model"],
        "command": Path(binary).name,
        "command_sha256": executor_identity["command_sha256"],
        "command_version": executor_identity["command_version"],
        "sandbox": executor["sandbox"],
        "workspace_scope": "isolated-cwd-with-bounded-prompt",
        "filesystem_boundary": boundary_proof,
        "separate_process": True,
        "thread_id": thread_ids[0] if len(thread_ids) == 1 else None,
        "reported_thread_ids": thread_ids,
        "context_packet_sha256": packet["packet_sha256"],
        "prompt_measured": {
            "bytes": prompt_bytes,
            "estimated_tokens_upper_bound": prompt_bytes,
        },
        "duration_ns": duration_ns,
        "exit_code": completed.returncode,
        "usage": usage,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        "raw_output_bytes": len(raw_output) if raw_output is not None else None,
        "raw_output_sha256": raw_output_sha256,
        "parsed_output_sha256": parsed_output_sha256,
        "reported_outcome": (
            parsed_output.get("outcome")
            if isinstance(parsed_output, dict)
            and isinstance(parsed_output.get("outcome"), str)
            else None
        ),
        "authoritative_outcome": authoritative_outcome,
        "disposition": (
            "accepted"
            if validation_error is None
            else "execution-failed"
            if completed.returncode != 0
            or raw_output is None
            or (
                boundary_proof is not None
                and boundary_proof.get("status") != "passed"
            )
            else "invalid-output"
        ),
        "validation_failure": validation_failure,
        "changed_project_paths": [],
        "result": result if validation_error is None else None,
    }
    artifact["execution_sha256"] = _digest(artifact)
    _write_canonical_exclusive(
        root / EXECUTION_DIRECTORY / f"{execution_id}.json", artifact
    )
    if boundary_proof is not None:
        _write_canonical_exclusive(
            root / FILESYSTEM_BOUNDARY_DIRECTORY / f"{execution_id}.json",
            boundary_proof,
        )
    if validation_error is not None:
        raise CodexExecutionRejected(str(validation_error), artifact)
    return artifact


def _runtime_profile(
    executor: dict[str, Any],
    *,
    executor_identity: dict[str, Any],
    filesystem_boundary: dict[str, Any] | None,
    criteria: list[dict[str, str]],
    fresh_context: bool,
    packet: dict[str, Any],
    approvals: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "fresh_context": fresh_context,
        "available_tools": executor["available_tools"],
        "model_capabilities": executor["model_capabilities"],
        "model": executor["model"],
        "provider": executor["provider"],
        "network_available": False,
        "data_classification": "internal",
        "model_data_classifications": executor["data_classifications"],
        "approvals": list(approvals),
        "platform": "linux",
        "context_packet": {
            "packet_sha256": packet["packet_sha256"],
            "measured": packet["measured"],
            "limits": packet["limits"],
        },
        "executor_identity": executor_identity,
        "filesystem_boundary": filesystem_boundary,
        "criteria": criteria,
    }


def _failed_run_record(
    root: Path,
    *,
    run_id: str,
    configuration: dict[str, Any],
    registry_warnings: list[str],
    task_id: str,
    task: str,
    capabilities: tuple[str, ...],
    acceptance_criteria: tuple[str, ...],
    criterion_specs: list[dict[str, str]] | None = None,
    criterion_results: list[dict[str, Any]] | None = None,
    approvals: tuple[str, ...],
    phase: str,
    error: BaseException,
    primary_packet: dict[str, Any],
    primary_record: dict[str, Any],
    primary_execution: dict[str, Any] | None = None,
    review_record: dict[str, Any] | None = None,
    review_execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    def invocation_status(record: dict[str, Any] | None) -> str | None:
        invocation_id = record.get("invocation_id") if record else None
        if not isinstance(invocation_id, str):
            return None
        path = root / ".aae/runtime/invocations" / f"{invocation_id}.json"
        if not path.is_file():
            return None
        value = _read_json_object(path)
        return str(value.get("status")) if value.get("status") is not None else None

    plan = primary_record.get("invocation_plan", {})
    primary: dict[str, Any] = {
        "invocation_id": primary_record.get("invocation_id"),
        "invocation_status": invocation_status(primary_record),
        "invocation_plan_sha256": plan.get("invocation_plan_sha256"),
        "skill": plan.get("skill"),
        "selection_reason": primary_record.get("selection_decision", {}).get("reason"),
        "capability_demand": primary_record.get("capability_demand"),
        "policy": plan.get("policy"),
        "context_packet_sha256": primary_packet["packet_sha256"],
        "context_packet": {
            "measured": primary_packet["measured"],
            "limits": primary_packet["limits"],
            "evidence": [
                {
                    "path": item["path"],
                    "bytes": item["bytes"],
                    "sha256": item["sha256"],
                }
                for item in primary_packet["items"]
            ],
        },
        "authorized_side_effects": plan.get("binding", {}).get("side_effects"),
        "changed_project_paths": (
            primary_execution.get("changed_project_paths", [])
            if primary_execution is not None
            else []
        ),
        "filesystem_boundary": (
            primary_execution.get("filesystem_boundary")
            if primary_execution is not None
            else None
        ),
        "execution_id": (
            primary_execution.get("execution_id")
            if primary_execution is not None
            else None
        ),
        "execution_sha256": (
            primary_execution.get("execution_sha256")
            if primary_execution is not None
            else None
        ),
        "thread_id": (
            primary_execution.get("thread_id")
            if primary_execution is not None
            else None
        ),
        "provider": plan.get("binding", {}).get("provider"),
        "model": plan.get("binding", {}).get("model"),
        "tool": plan.get("binding", {}).get("executor_identity", {}).get("adapter"),
        "duration_ns": (
            primary_execution.get("duration_ns", 0)
            if primary_execution is not None
            else 0
        ),
        "usage": primary_execution.get("usage") if primary_execution is not None else None,
        "execution_disposition": (
            primary_execution.get("disposition")
            if primary_execution is not None
            else None
        ),
        "authoritative_outcome": (
            primary_execution.get("authoritative_outcome")
            if primary_execution is not None
            else None
        ),
        "raw_output_sha256": (
            primary_execution.get("raw_output_sha256")
            if primary_execution is not None
            else None
        ),
        "parsed_output_sha256": (
            primary_execution.get("parsed_output_sha256")
            if primary_execution is not None
            else None
        ),
        "validation_failure": (
            primary_execution.get("validation_failure")
            if primary_execution is not None
            else None
        ),
        "result": primary_execution.get("result") if primary_execution is not None else None,
        "criterion_results": criterion_results or [],
        "pre_review_outcome": (
            _criterion_outcome(criterion_results) if criterion_results else None
        ),
    }
    review: dict[str, Any] = {
        "required": bool(configuration["review"]["required"]),
        "invocation_id": review_record.get("invocation_id") if review_record else None,
        "invocation_status": invocation_status(review_record),
        "invocation_plan_sha256": (
            review_record.get("invocation_plan", {}).get("invocation_plan_sha256")
            if review_record
            else None
        ),
        "execution_id": review_execution.get("execution_id") if review_execution else None,
        "execution_sha256": (
            review_execution.get("execution_sha256") if review_execution else None
        ),
        "thread_distinct": (
            review_execution.get("thread_id") != primary_execution.get("thread_id")
            if review_execution is not None and primary_execution is not None
            else None
        ),
        "thread_id": review_execution.get("thread_id") if review_execution else None,
        "context_packet_sha256": (
            review_execution.get("context_packet_sha256")
            if review_execution
            else review_record.get("context_evidence_sha256")
            if review_record
            else None
        ),
        "provider": (
            review_execution.get("provider")
            if review_execution
            else review_record.get("invocation_plan", {}).get("binding", {}).get(
                "provider"
            )
            if review_record
            else None
        ),
        "model": (
            review_execution.get("model")
            if review_execution
            else review_record.get("invocation_plan", {}).get("binding", {}).get(
                "model"
            )
            if review_record
            else None
        ),
        "duration_ns": review_execution.get("duration_ns", 0) if review_execution else 0,
        "usage": review_execution.get("usage") if review_execution else None,
        "execution_disposition": (
            review_execution.get("disposition") if review_execution else None
        ),
        "authoritative_outcome": (
            review_execution.get("authoritative_outcome")
            if review_execution
            else None
        ),
        "raw_output_sha256": (
            review_execution.get("raw_output_sha256") if review_execution else None
        ),
        "parsed_output_sha256": (
            review_execution.get("parsed_output_sha256")
            if review_execution
            else None
        ),
        "validation_failure": (
            review_execution.get("validation_failure") if review_execution else None
        ),
        "changed_project_paths": (
            review_execution.get("changed_project_paths", []) if review_execution else []
        ),
        "filesystem_boundary": (
            review_execution.get("filesystem_boundary") if review_execution else None
        ),
        "result": review_execution.get("result") if review_execution else None,
    }
    record: dict[str, Any] = {
        "schema_version": 2,
        "run_id": run_id,
        "task_request": {
            "task_id": task_id,
            "task": task,
            "requested_capabilities": sorted(set(capabilities)),
            "acceptance_criteria": [
                value.strip() for value in acceptance_criteria if value.strip()
            ],
            "criteria": criterion_specs or [],
            "approvals": sorted(set(approvals)),
        },
        "configuration": {
            "portable_config_sha256": configuration["portable_config_sha256"],
            "effective_config_sha256": configuration["effective_config_sha256"],
            "context_limits": configuration["context_limits"],
            "controller": controller_identity(),
        },
        "registry_warnings": registry_warnings,
        "primary": primary,
        "review": review,
        "status": "failed",
        "failure": {
            "phase": phase,
            "type": type(error).__name__,
            "message": str(error),
            "message_sha256": hashlib.sha256(str(error).encode("utf-8")).hexdigest(),
        },
        "fallbacks": [],
        "retries": [],
        "duration_ns": int(primary.get("duration_ns") or 0)
        + int(review_execution.get("duration_ns", 0) if review_execution else 0),
    }
    record["run_sha256"] = governed_run_digest(record)
    accounting_path = (
        root / configuration["accounting_directory"] / f"{run_id}.json"
    )
    _write_canonical_exclusive(accounting_path, record)
    return {
        **record,
        "accounting_path": accounting_path.relative_to(root).as_posix(),
    }


def _execute_governed_task(
    root: Path,
    *,
    run_id: str,
    task_id: str,
    task: str,
    explicit_skill: str | None,
    capabilities: Iterable[str],
    acceptance_criteria: Iterable[str],
    deterministic_acceptance_criteria: Iterable[str],
    evidence_paths: Iterable[Path],
    approvals: Iterable[str] = (),
) -> dict[str, Any]:
    capability_values = tuple(capabilities)
    acceptance_values = tuple(acceptance_criteria)
    deterministic_values = tuple(deterministic_acceptance_criteria)
    configuration = load_execution_configuration(root)
    if (
        configuration["filesystem_boundary"] is not None
        and FILESYSTEM_BOUNDARY_CRITERION not in deterministic_values
    ):
        deterministic_values = (*deterministic_values, FILESYSTEM_BOUNDARY_CRITERION)
    criterion_specs = _build_criterion_specs(acceptance_values, deterministic_values)
    all_acceptance_values = tuple(spec["statement"] for spec in criterion_specs)
    semantic_values = tuple(
        spec["statement"]
        for spec in criterion_specs
        if spec["authority"] == SEMANTIC_EXECUTOR
    )
    acceptance_values = all_acceptance_values
    evidence_values = tuple(evidence_paths)
    approval_values = tuple(approvals)
    registry, registry_errors, registry_warnings = build_skill_registry(root)
    if registry_errors:
        raise ValueError("skill registry is invalid: " + "; ".join(registry_errors))
    limits = configuration["context_limits"]
    primary_packet = build_context_packet(
        root,
        task_id=task_id,
        task=task,
        acceptance_criteria=semantic_values,
        evidence_paths=evidence_values,
        limits=limits,
        evidence_policy=configuration["evidence_paths"],
    )
    _write_canonical_exclusive(
        root / CONTEXT_PACKET_DIRECTORY / f"{primary_packet['packet_sha256']}.json",
        primary_packet,
    )
    primary_executor = configuration["primary_executor"]
    primary_executor_identity = resolve_executor_identity(primary_executor)
    filesystem_boundary_identity = resolve_filesystem_boundary(
        configuration["filesystem_boundary"]
    )
    primary_record, primary_procedure, policy_errors = invoke_skill(
        root,
        registry,
        task=task,
        explicit_skill=explicit_skill,
        explicit_capabilities=capability_values,
        architecture=("deterministic-control-plane", "bounded-context"),
        environment=("engineering-only", "ephemeral-codex-cli"),
        risks=("authority-bypass", "unbounded-context"),
        evidence_gaps=("independent-verification",),
        task_id=task_id,
        context_evidence_sha256=primary_packet["packet_sha256"],
        runtime_profile=_runtime_profile(
            primary_executor,
            executor_identity=primary_executor_identity,
            filesystem_boundary=filesystem_boundary_identity,
            criteria=criterion_specs,
            fresh_context=False,
            packet=primary_packet,
            approvals=approval_values,
        ),
    )
    if policy_errors or primary_record["status"] != "procedure-loaded" or primary_procedure is None:
        denial = PermissionError(
            "AAE policy denied primary execution: "
            + ", ".join(primary_record["invocation_plan"]["policy"]["rejection_reasons"])
        )
        return _failed_run_record(
            root,
            run_id=run_id,
            configuration=configuration,
            registry_warnings=registry_warnings,
            task_id=task_id,
            task=task,
            capabilities=capability_values,
            acceptance_criteria=acceptance_values,
            criterion_specs=criterion_specs,
            approvals=approval_values,
            phase="primary-policy",
            error=denial,
            primary_packet=primary_packet,
            primary_record=primary_record,
        )
    try:
        primary_execution = run_codex_cli(
            root,
            invocation_record=primary_record,
            procedure=primary_procedure,
            packet=primary_packet,
            executor=primary_executor,
            executor_identity=primary_executor_identity,
            filesystem_boundary=filesystem_boundary_identity,
            role="executor",
        )
    except CodexExecutionRejected as execution_error:
        primary_execution = execution_error.artifact
        primary_evidence = (
            f"{EXECUTION_DIRECTORY.as_posix()}/"
            f"{primary_execution['execution_id']}.json"
        )
        record_error = record_invocation_outcome(
            root,
            primary_record["invocation_id"],
            outcome="failed",
            verification="failed",
            evidence=primary_evidence,
            context_tokens=(primary_execution.get("usage") or {}).get("input_tokens"),
            execution_cost=None,
        )
        if record_error:
            raise RuntimeError(record_error) from execution_error
        return _failed_run_record(
            root,
            run_id=run_id,
            configuration=configuration,
            registry_warnings=registry_warnings,
            task_id=task_id,
            task=task,
            capabilities=capability_values,
            acceptance_criteria=acceptance_values,
            criterion_specs=criterion_specs,
            approvals=approval_values,
            phase=(
                "primary-invalid-output"
                if primary_execution["disposition"] == "invalid-output"
                else "primary-execution"
            ),
            error=execution_error,
            primary_packet=primary_packet,
            primary_record=primary_record,
            primary_execution=primary_execution,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, subprocess.SubprocessError, ValueError, RuntimeError) as execution_error:
        failure_path = (
            Path(configuration["accounting_directory"]) / f"{run_id}.json"
        ).as_posix()
        record_error = record_invocation_outcome(
            root,
            primary_record["invocation_id"],
            outcome="failed",
            verification="failed",
            evidence=failure_path,
            context_tokens=None,
            execution_cost=None,
        )
        if record_error:
            raise RuntimeError(record_error) from execution_error
        return _failed_run_record(
            root,
            run_id=run_id,
            configuration=configuration,
            registry_warnings=registry_warnings,
            task_id=task_id,
            task=task,
            capabilities=capability_values,
            acceptance_criteria=acceptance_values,
            criterion_specs=criterion_specs,
            approvals=approval_values,
            phase="primary-execution",
            error=execution_error,
            primary_packet=primary_packet,
            primary_record=primary_record,
        )
    primary_result = primary_execution["result"]
    primary_outcome = primary_execution["authoritative_outcome"]
    primary_evidence = (
        f"{EXECUTION_DIRECTORY.as_posix()}/{primary_execution['execution_id']}.json"
    )
    error = record_invocation_outcome(
        root,
        primary_record["invocation_id"],
        outcome="succeeded" if primary_outcome == "succeeded" else "failed",
        verification=("passed" if primary_outcome == "succeeded" else "failed"),
        evidence=primary_evidence,
        context_tokens=(primary_execution.get("usage") or {}).get("input_tokens"),
        execution_cost=None,
    )
    if error:
        return _failed_run_record(
            root,
            run_id=run_id,
            configuration=configuration,
            registry_warnings=registry_warnings,
            task_id=task_id,
            task=task,
            capabilities=capability_values,
            acceptance_criteria=acceptance_values,
            criterion_specs=criterion_specs,
            approvals=approval_values,
            phase="primary-outcome-recording",
            error=RuntimeError(error),
            primary_packet=primary_packet,
            primary_record=primary_record,
            primary_execution=primary_execution,
        )
    criterion_results, pre_review_outcome = _evaluate_pre_review_criteria(
        criterion_specs,
        semantic_result=primary_result,
        invocation_id=primary_record["invocation_id"],
        execution_id=primary_execution["execution_id"],
        execution_sha256=primary_execution["execution_sha256"],
        boundary_proof=primary_execution.get("filesystem_boundary"),
        invocation_plan_sha256=primary_record["invocation_plan"][
            "invocation_plan_sha256"
        ],
        context_packet_sha256=primary_packet["packet_sha256"],
        project_root=root,
        boundary_identity=filesystem_boundary_identity,
    )
    if pre_review_outcome != "succeeded":
        return _failed_run_record(
            root,
            run_id=run_id,
            configuration=configuration,
            registry_warnings=registry_warnings,
            task_id=task_id,
            task=task,
            capabilities=capability_values,
            acceptance_criteria=acceptance_values,
            criterion_specs=criterion_specs,
            criterion_results=criterion_results,
            approvals=approval_values,
            phase="primary-governed-outcome",
            error=RuntimeError(
                "combined deterministic pre-review outcome is "
                f"{pre_review_outcome}; review not launched"
            ),
            primary_packet=primary_packet,
            primary_record=primary_record,
            primary_execution=primary_execution,
        )

    review_record: dict[str, Any] | None = None
    review_execution: dict[str, Any] | None = None
    if configuration["review"]["required"]:
        original_criteria = "\n".join(
            f"- {criterion}" for criterion in semantic_values
        )
        review_task = "\n".join(
            [
                "Independently assess the semantic criteria from the bounded source "
                "evidence. A neutral criterion-level semantic result and the "
                "deterministic runtime-boundary proof are supplied for reconciliation; "
                "do not treat either as a substitute for source review.",
                "",
                "ORIGINAL TASK",
                task,
                "",
                "ORIGINAL ACCEPTANCE CRITERIA",
                original_criteria,
            ]
        )
        primary_boundary = primary_execution.get("filesystem_boundary")
        if filesystem_boundary_identity is not None and (
            not isinstance(primary_boundary, dict)
            or not filesystem_boundary_proof_digest_is_valid(primary_boundary)
            or primary_boundary.get("status") != "passed"
        ):
            return _failed_run_record(
                root,
                run_id=run_id,
                configuration=configuration,
                registry_warnings=registry_warnings,
                task_id=task_id,
                task=task,
                capabilities=capability_values,
                acceptance_criteria=acceptance_values,
                criterion_specs=criterion_specs,
                criterion_results=criterion_results,
                approvals=approval_values,
                phase="primary-runtime-boundary",
                error=RuntimeError(
                    "independent review requires deterministic filesystem-boundary proof"
                ),
                primary_packet=primary_packet,
                primary_record=primary_record,
                primary_execution=primary_execution,
            )
        review_items = [dict(item) for item in primary_packet["items"]]
        semantic_result_record = {
            "schema_version": 1,
            "execution_id": primary_execution["execution_id"],
            "execution_sha256": primary_execution["execution_sha256"],
            "authoritative_outcome": primary_outcome,
            "criterion_results": [
                result
                for result in criterion_results
                if result["authority"] == SEMANTIC_EXECUTOR
            ],
        }
        semantic_result_bytes = _canonical_bytes(semantic_result_record)
        review_items.append(
            {
                "path": "AAE_SEMANTIC_RESULT.json",
                "bytes": len(semantic_result_bytes),
                "sha256": hashlib.sha256(semantic_result_bytes).hexdigest(),
                "content": semantic_result_bytes.decode("utf-8"),
            }
        )
        if isinstance(primary_boundary, dict):
            boundary_bytes = _canonical_bytes(primary_boundary)
            review_items.append(
                {
                    "path": "AAE_RUNTIME_BOUNDARY.json",
                    "bytes": len(boundary_bytes),
                    "sha256": hashlib.sha256(boundary_bytes).hexdigest(),
                    "content": boundary_bytes.decode("utf-8"),
                }
            )
        review_packet = _finalize_context_packet(
            task_id=f"{task_id}:independent-review",
            task=review_task,
            acceptance_criteria=semantic_values,
            items=review_items,
            limits=limits,
        )
        _write_canonical_exclusive(
            root / CONTEXT_PACKET_DIRECTORY / f"{review_packet['packet_sha256']}.json",
            review_packet,
        )
        reviewer = configuration["review"]["executor"]
        reviewer_identity = resolve_executor_identity(reviewer)
        review_record, review_procedure, review_policy_errors = invoke_skill(
            root,
            registry,
            task=review_task,
            explicit_skill=configuration["review"]["skill"],
            explicit_capabilities=("independent-review",),
            architecture=("deterministic-control-plane", "fresh-context-process"),
            environment=("engineering-only", "ephemeral-codex-cli"),
            risks=("reviewer-framing-contamination",),
            evidence_gaps=("independent-verification",),
            task_id=f"{task_id}:independent-review",
            context_evidence_sha256=review_packet["packet_sha256"],
            runtime_profile=_runtime_profile(
                reviewer,
                executor_identity=reviewer_identity,
                filesystem_boundary=filesystem_boundary_identity,
                criteria=[
                    spec
                    for spec in criterion_specs
                    if spec["authority"] == SEMANTIC_EXECUTOR
                ],
                fresh_context=True,
                packet=review_packet,
            ),
        )
        if (
            review_policy_errors
            or review_record["status"] != "procedure-loaded"
            or review_procedure is None
        ):
            denial = PermissionError(
                "AAE policy denied independent review: "
                + ", ".join(review_record["invocation_plan"]["policy"]["rejection_reasons"])
            )
            return _failed_run_record(
                root,
                run_id=run_id,
                configuration=configuration,
                registry_warnings=registry_warnings,
                task_id=task_id,
                task=task,
                capabilities=capability_values,
                acceptance_criteria=acceptance_values,
                criterion_specs=criterion_specs,
                criterion_results=criterion_results,
                approvals=approval_values,
                phase="review-policy",
                error=denial,
                primary_packet=primary_packet,
                primary_record=primary_record,
                primary_execution=primary_execution,
                review_record=review_record,
            )
        try:
            review_execution = run_codex_cli(
                root,
                invocation_record=review_record,
                procedure=review_procedure,
                packet=review_packet,
                executor=reviewer,
                executor_identity=reviewer_identity,
                filesystem_boundary=filesystem_boundary_identity,
                role="reviewer",
            )
        except CodexExecutionRejected as review_error:
            review_execution = review_error.artifact
            review_evidence = (
                f"{EXECUTION_DIRECTORY.as_posix()}/"
                f"{review_execution['execution_id']}.json"
            )
            record_error = record_invocation_outcome(
                root,
                review_record["invocation_id"],
                outcome="failed",
                verification="failed",
                evidence=review_evidence,
                context_tokens=(review_execution.get("usage") or {}).get(
                    "input_tokens"
                ),
                execution_cost=None,
            )
            if record_error:
                raise RuntimeError(record_error) from review_error
            return _failed_run_record(
                root,
                run_id=run_id,
                configuration=configuration,
                registry_warnings=registry_warnings,
                task_id=task_id,
                task=task,
                capabilities=capability_values,
                acceptance_criteria=acceptance_values,
                criterion_specs=criterion_specs,
                criterion_results=criterion_results,
                approvals=approval_values,
                phase=(
                    "review-invalid-output"
                    if review_execution["disposition"] == "invalid-output"
                    else "review-execution"
                ),
                error=review_error,
                primary_packet=primary_packet,
                primary_record=primary_record,
                primary_execution=primary_execution,
                review_record=review_record,
                review_execution=review_execution,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, subprocess.SubprocessError, ValueError, RuntimeError) as review_error:
            failure_path = (
                Path(configuration["accounting_directory"]) / f"{run_id}.json"
            ).as_posix()
            record_error = record_invocation_outcome(
                root,
                review_record["invocation_id"],
                outcome="failed",
                verification="failed",
                evidence=failure_path,
                context_tokens=None,
                execution_cost=None,
            )
            if record_error:
                raise RuntimeError(record_error) from review_error
            return _failed_run_record(
                root,
                run_id=run_id,
                configuration=configuration,
                registry_warnings=registry_warnings,
                task_id=task_id,
                task=task,
                capabilities=capability_values,
                acceptance_criteria=acceptance_values,
                criterion_specs=criterion_specs,
                criterion_results=criterion_results,
                approvals=approval_values,
                phase="review-execution",
                error=review_error,
                primary_packet=primary_packet,
                primary_record=primary_record,
                primary_execution=primary_execution,
                review_record=review_record,
            )
        if review_execution["thread_id"] == primary_execution["thread_id"]:
            thread_failure = RuntimeError(
                "independent reviewer reused the executor thread"
            )
            record_error = record_invocation_outcome(
                root,
                review_record["invocation_id"],
                outcome="failed",
                verification="failed",
                evidence=f"{EXECUTION_DIRECTORY.as_posix()}/"
                f"{review_execution['execution_id']}.json",
                context_tokens=(review_execution.get("usage") or {}).get(
                    "input_tokens"
                ),
                execution_cost=None,
            )
            if record_error:
                thread_failure = RuntimeError(
                    f"review thread independence and outcome recording failed: "
                    f"{record_error}"
                )
            return _failed_run_record(
                root,
                run_id=run_id,
                configuration=configuration,
                registry_warnings=registry_warnings,
                task_id=task_id,
                task=task,
                capabilities=capability_values,
                acceptance_criteria=acceptance_values,
                criterion_specs=criterion_specs,
                criterion_results=criterion_results,
                approvals=approval_values,
                phase="review-thread-independence",
                error=thread_failure,
                primary_packet=primary_packet,
                primary_record=primary_record,
                primary_execution=primary_execution,
                review_record=review_record,
                review_execution=review_execution,
            )
        review_result = review_execution["result"]
        review_agreement = [
            (entry["criterion"], entry["status"])
            for entry in review_result["verification"]
        ] == [
            (entry["criterion"], entry["status"])
            for entry in primary_result["verification"]
        ]
        review_succeeded = (
            review_execution["authoritative_outcome"] == "succeeded"
            and review_result["review_verdict"] == "approved"
            and review_agreement
        )
        review_evidence = (
            f"{EXECUTION_DIRECTORY.as_posix()}/{review_execution['execution_id']}.json"
        )
        error = record_invocation_outcome(
            root,
            review_record["invocation_id"],
            outcome="succeeded" if review_succeeded else "failed",
            verification="passed" if review_succeeded else "failed",
            evidence=review_evidence,
            context_tokens=(review_execution.get("usage") or {}).get("input_tokens"),
            execution_cost=None,
        )
        if error:
            return _failed_run_record(
                root,
                run_id=run_id,
                configuration=configuration,
                registry_warnings=registry_warnings,
                task_id=task_id,
                task=task,
                capabilities=capability_values,
                acceptance_criteria=acceptance_values,
                criterion_specs=criterion_specs,
                criterion_results=criterion_results,
                approvals=approval_values,
                phase="review-outcome-recording",
                error=RuntimeError(error),
                primary_packet=primary_packet,
                primary_record=primary_record,
                primary_execution=primary_execution,
                review_record=review_record,
                review_execution=review_execution,
            )
    else:
        review_succeeded = True
        review_agreement = True

    succeeded = pre_review_outcome == "succeeded" and review_succeeded
    run: dict[str, Any] = {
        "schema_version": 2,
        "run_id": run_id,
        "task_request": {
            "task_id": task_id,
            "task": task,
            "requested_capabilities": sorted(set(capability_values)),
            "acceptance_criteria": [
                value.strip() for value in acceptance_values if value.strip()
            ],
            "criteria": criterion_specs,
            "approvals": sorted(set(approval_values)),
        },
        "configuration": {
            "portable_config_sha256": configuration["portable_config_sha256"],
            "effective_config_sha256": configuration["effective_config_sha256"],
            "context_limits": limits,
            "controller": controller_identity(),
        },
        "registry_warnings": registry_warnings,
        "primary": {
            "invocation_id": primary_record["invocation_id"],
            "invocation_status": "completed" if primary_outcome == "succeeded" else "failed",
            "invocation_plan_sha256": primary_record["invocation_plan"][
                "invocation_plan_sha256"
            ],
            "skill": primary_record["invocation_plan"]["skill"],
            "selection_reason": primary_record["selection_decision"]["reason"],
            "capability_demand": primary_record["capability_demand"],
            "policy": primary_record["invocation_plan"]["policy"],
            "context_packet_sha256": primary_packet["packet_sha256"],
            "context_packet": {
                "measured": primary_packet["measured"],
                "limits": primary_packet["limits"],
                "evidence": [
                    {
                        "path": item["path"],
                        "bytes": item["bytes"],
                        "sha256": item["sha256"],
                    }
                    for item in primary_packet["items"]
                ],
            },
            "execution_id": primary_execution["execution_id"],
            "execution_sha256": primary_execution["execution_sha256"],
            "thread_id": primary_execution["thread_id"],
            "provider": primary_execution["provider"],
            "model": primary_execution["model"],
            "tool": primary_execution["adapter"],
            "authorized_side_effects": primary_record["invocation_plan"]["binding"][
                "side_effects"
            ],
            "changed_project_paths": primary_execution["changed_project_paths"],
            "filesystem_boundary": primary_execution["filesystem_boundary"],
            "duration_ns": primary_execution["duration_ns"],
            "usage": primary_execution["usage"],
            "execution_disposition": primary_execution["disposition"],
            "authoritative_outcome": primary_execution["authoritative_outcome"],
            "criterion_results": criterion_results,
            "pre_review_outcome": pre_review_outcome,
            "raw_output_sha256": primary_execution["raw_output_sha256"],
            "parsed_output_sha256": primary_execution["parsed_output_sha256"],
            "validation_failure": primary_execution["validation_failure"],
            "result": primary_result,
        },
        "review": (
            {
                "required": True,
                "invocation_id": review_record["invocation_id"],
                "invocation_status": "completed" if review_succeeded else "failed",
                "invocation_plan_sha256": review_record["invocation_plan"][
                    "invocation_plan_sha256"
                ],
                "execution_id": review_execution["execution_id"],
                "execution_sha256": review_execution["execution_sha256"],
                "thread_distinct": True,
                "thread_id": review_execution["thread_id"],
                "acceptance_reconciliation": review_agreement,
                "context_packet_sha256": review_execution[
                    "context_packet_sha256"
                ],
                "provider": review_execution["provider"],
                "model": review_execution["model"],
                "duration_ns": review_execution["duration_ns"],
                "usage": review_execution["usage"],
                "execution_disposition": review_execution["disposition"],
                "authoritative_outcome": review_execution[
                    "authoritative_outcome"
                ],
                "raw_output_sha256": review_execution["raw_output_sha256"],
                "parsed_output_sha256": review_execution[
                    "parsed_output_sha256"
                ],
                "validation_failure": review_execution["validation_failure"],
                "changed_project_paths": review_execution["changed_project_paths"],
                "filesystem_boundary": review_execution["filesystem_boundary"],
                "result": review_execution["result"],
            }
            if review_record is not None and review_execution is not None
            else {"required": False}
        ),
        "status": "succeeded" if succeeded else "failed",
        "failure": (
            None
            if succeeded
            else {
                "phase": "governed-outcome",
                "type": "AcceptanceFailure",
                "message": "primary execution or independent verification failed",
            }
        ),
        "fallbacks": [],
        "retries": [],
        "duration_ns": primary_execution["duration_ns"]
        + (review_execution["duration_ns"] if review_execution else 0),
    }
    run["run_sha256"] = governed_run_digest(run)
    accounting_path = root / configuration["accounting_directory"] / f"{run_id}.json"
    _write_canonical_exclusive(accounting_path, run)
    return {**run, "accounting_path": accounting_path.relative_to(root).as_posix()}


def execute_governed_task(
    root: Path,
    *,
    task_id: str,
    task: str,
    explicit_skill: str | None,
    capabilities: Iterable[str],
    acceptance_criteria: Iterable[str],
    evidence_paths: Iterable[Path],
    deterministic_acceptance_criteria: Iterable[str] = (),
    approvals: Iterable[str] = (),
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    capability_values = tuple(capabilities)
    acceptance_values = tuple(acceptance_criteria)
    deterministic_values = tuple(deterministic_acceptance_criteria)
    evidence_values = tuple(evidence_paths)
    approval_values = tuple(approvals)
    try:
        return _execute_governed_task(
            root,
            run_id=run_id,
            task_id=task_id,
            task=task,
            explicit_skill=explicit_skill,
            capabilities=capability_values,
            acceptance_criteria=acceptance_values,
            deterministic_acceptance_criteria=deterministic_values,
            evidence_paths=evidence_values,
            approvals=approval_values,
        )
    except Exception as error:
        try:
            configuration = load_execution_configuration(
                root, require_effective_executor=False
            )
            accounting_directory = configuration["accounting_directory"]
            configuration_summary: dict[str, Any] = {
                "portable_config_sha256": configuration["portable_config_sha256"],
                "effective_config_sha256": configuration["effective_config_sha256"],
                "context_limits": configuration["context_limits"],
                "controller": controller_identity(),
            }
        except Exception as configuration_error:
            accounting_directory = ".aae/state/governed-runs"
            configuration_summary = {
                "controller": controller_identity(),
                "configuration_error": {
                    "type": type(configuration_error).__name__,
                    "message_sha256": hashlib.sha256(
                        str(configuration_error).encode("utf-8")
                    ).hexdigest(),
                },
            }
        accounting_path = root / accounting_directory / f"{run_id}.json"
        if accounting_path.is_file():
            existing = _read_json_object(accounting_path)
            return {
                **existing,
                "accounting_path": accounting_path.relative_to(root).as_posix(),
            }
        failure: dict[str, Any] = {
            "schema_version": 2,
            "run_id": run_id,
            "task_request": {
                "task_id": task_id,
                "task": task,
                "requested_capabilities": sorted(set(capability_values)),
                "acceptance_criteria": [
                    value.strip()
                    for value in (*acceptance_values, *deterministic_values)
                    if value.strip()
                ],
                "criteria": [],
                "approvals": sorted(set(approval_values)),
                "evidence_paths": [str(path) for path in evidence_values],
            },
            "configuration": configuration_summary,
            "registry_warnings": [],
            "primary": {
                "invocation_id": None,
                "skill": None,
                "selection_reason": None,
                "capability_demand": None,
                "policy": None,
                "context_packet": None,
                "provider": None,
                "model": None,
                "tool": None,
                "authorized_side_effects": None,
                "changed_project_paths": [],
                "usage": None,
            },
            "review": {"required": None},
            "status": "failed",
            "failure": {
                "phase": "control-plane-prepublication",
                "type": type(error).__name__,
                "message": str(error),
                "message_sha256": hashlib.sha256(
                    str(error).encode("utf-8")
                ).hexdigest(),
            },
            "fallbacks": [],
            "retries": [],
            "duration_ns": 0,
        }
        failure["run_sha256"] = governed_run_digest(failure)
        _write_canonical_exclusive(accounting_path, failure)
        return {
            **failure,
            "accounting_path": accounting_path.relative_to(root).as_posix(),
        }
