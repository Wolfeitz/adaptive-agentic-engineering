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

CONFIG_FIELDS = {
    "schema_version",
    "context_limits",
    "evidence_paths",
    "primary_executor",
    "review",
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


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


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
        local_unknown = sorted(set(local) - {"schema_version", "primary_executor", "review"})
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
) -> None:
    expected = list(packet["acceptance_criteria"])
    observed = [entry["criterion"] for entry in result["verification"]]
    if observed != expected:
        raise ValueError(
            "Codex executor must verify every acceptance criterion exactly once "
            "in packet order"
        )
    all_passed = all(
        entry["status"] == "passed" for entry in result["verification"]
    )
    if (result["outcome"] == "succeeded") != all_passed:
        raise ValueError(
            "Codex executor outcome must be succeeded exactly when every "
            "acceptance criterion passed"
        )
    if result["outcome"] == "succeeded" and any(
        finding["severity"] == "error" for finding in result["findings"]
    ):
        raise ValueError("a succeeded Codex result cannot contain an error finding")


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
        "packet paths. Return only the required structured result."
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
        schema_path.write_bytes(_canonical_bytes(CODEX_RESULT_SCHEMA) + b"\n")
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
            prompt,
        ]
        completed = subprocess.run(
            argv,
            input="",
            capture_output=True,
            text=True,
            timeout=executor["timeout_seconds"],
            check=False,
            cwd=workspace,
            env=_executor_environment(),
        )
        duration_ns = time.monotonic_ns() - started_ns
        if completed.returncode != 0 or not result_path.is_file():
            raise RuntimeError(
                "Codex CLI execution failed: "
                f"exit={completed.returncode}, stderr_sha256="
                f"{hashlib.sha256(completed.stderr.encode()).hexdigest()}"
            )
        result = _validate_codex_result(
            json.loads(result_path.read_text(encoding="utf-8")), role
        )
        _validate_result_against_packet(result, packet)
        allowed_paths = {str(item["path"]) for item in packet["items"]}
        for collection in ("findings", "verification"):
            for entry in result[collection]:
                for reference in entry["evidence_refs"]:
                    if not any(
                        reference == path or reference.startswith(f"{path}:")
                        for path in allowed_paths
                    ):
                        raise ValueError(
                            f"Codex executor cited evidence outside the bounded packet: {reference}"
                        )
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
        if len(thread_ids) != 1:
            raise RuntimeError("Codex CLI did not report exactly one fresh thread identity")

    artifact: dict[str, Any] = {
        "schema_version": 1,
        "execution_id": execution_id,
        "invocation_id": invocation_record["invocation_id"],
        "role": role,
        "adapter": "codex-cli",
        "provider": executor["provider"],
        "model": executor["model"],
        "command": Path(binary).name,
        "command_sha256": executor_identity["command_sha256"],
        "command_version": executor_identity["command_version"],
        "sandbox": executor["sandbox"],
        "workspace_scope": "isolated-cwd-with-bounded-prompt",
        "separate_process": True,
        "thread_id": thread_ids[0],
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
        "changed_project_paths": [],
        "result": result,
    }
    artifact["execution_sha256"] = _digest(artifact)
    _write_canonical_exclusive(
        root / EXECUTION_DIRECTORY / f"{execution_id}.json", artifact
    )
    return artifact


def _runtime_profile(
    executor: dict[str, Any],
    *,
    executor_identity: dict[str, Any],
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
        "result": primary_execution.get("result") if primary_execution is not None else None,
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
        "changed_project_paths": (
            review_execution.get("changed_project_paths", []) if review_execution else []
        ),
        "result": review_execution.get("result") if review_execution else None,
    }
    record: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "task_request": {
            "task_id": task_id,
            "task": task,
            "requested_capabilities": sorted(set(capabilities)),
            "acceptance_criteria": [
                value.strip() for value in acceptance_criteria if value.strip()
            ],
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
    evidence_paths: Iterable[Path],
    approvals: Iterable[str] = (),
) -> dict[str, Any]:
    capability_values = tuple(capabilities)
    acceptance_values = tuple(acceptance_criteria)
    evidence_values = tuple(evidence_paths)
    approval_values = tuple(approvals)
    configuration = load_execution_configuration(root)
    registry, registry_errors, registry_warnings = build_skill_registry(root)
    if registry_errors:
        raise ValueError("skill registry is invalid: " + "; ".join(registry_errors))
    limits = configuration["context_limits"]
    primary_packet = build_context_packet(
        root,
        task_id=task_id,
        task=task,
        acceptance_criteria=acceptance_values,
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
            role="executor",
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
            approvals=approval_values,
            phase="primary-execution",
            error=execution_error,
            primary_packet=primary_packet,
            primary_record=primary_record,
        )
    primary_result = primary_execution["result"]
    primary_outcome = (
        "succeeded" if primary_result["outcome"] == "succeeded" else "failed"
    )
    primary_evidence = (
        f"{EXECUTION_DIRECTORY.as_posix()}/{primary_execution['execution_id']}.json"
    )
    error = record_invocation_outcome(
        root,
        primary_record["invocation_id"],
        outcome=primary_outcome,
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
            approvals=approval_values,
            phase="primary-outcome-recording",
            error=RuntimeError(error),
            primary_packet=primary_packet,
            primary_record=primary_record,
            primary_execution=primary_execution,
        )

    review_record: dict[str, Any] | None = None
    review_execution: dict[str, Any] | None = None
    if configuration["review"]["required"]:
        original_criteria = "\n".join(
            f"- {criterion}" for criterion in primary_packet["acceptance_criteria"]
        )
        review_task = "\n".join(
            [
                "Independently perform the original assessment from the task, "
                "acceptance criteria, and bounded source evidence. The executor's "
                "result is intentionally withheld so you cannot inherit its framing.",
                "",
                "ORIGINAL TASK",
                task,
                "",
                "ORIGINAL ACCEPTANCE CRITERIA",
                original_criteria,
            ]
        )
        review_packet = build_context_packet(
            root,
            task_id=f"{task_id}:independent-review",
            task=review_task,
            acceptance_criteria=primary_packet["acceptance_criteria"],
            evidence_paths=evidence_values,
            limits=limits,
            evidence_policy=configuration["evidence_paths"],
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
                role="reviewer",
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
            review_result["outcome"] == "succeeded"
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

    succeeded = primary_outcome == "succeeded" and review_succeeded
    run: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "task_request": {
            "task_id": task_id,
            "task": task,
            "requested_capabilities": sorted(set(capability_values)),
            "acceptance_criteria": [
                value.strip() for value in acceptance_values if value.strip()
            ],
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
            "duration_ns": primary_execution["duration_ns"],
            "usage": primary_execution["usage"],
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
                "changed_project_paths": review_execution["changed_project_paths"],
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
    approvals: Iterable[str] = (),
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    capability_values = tuple(capabilities)
    acceptance_values = tuple(acceptance_criteria)
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
            "schema_version": 1,
            "run_id": run_id,
            "task_request": {
                "task_id": task_id,
                "task": task,
                "requested_capabilities": sorted(set(capability_values)),
                "acceptance_criteria": [
                    value.strip() for value in acceptance_values if value.strip()
                ],
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
