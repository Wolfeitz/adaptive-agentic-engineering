from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


SEMANTIC_EXECUTOR = "semantic-executor"
DETERMINISTIC_CONTROL = "deterministic-control"
SEMANTIC_EVALUATOR = "agent-report-v1"
HOOK_CHECK_EVALUATOR = "hook-command-exit-zero-v1"
RESULTS = {"passed", "failed", "blocked"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _criterion(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "criterion_id": digest(body)}


def semantic_criteria(statements: Iterable[str]) -> list[dict[str, Any]]:
    criteria: list[dict[str, Any]] = []
    for value in statements:
        statement = value.strip()
        if not statement:
            continue
        criteria.append(
            _criterion(
                {
                    "statement": statement,
                    "authority": SEMANTIC_EXECUTOR,
                    "evaluator": SEMANTIC_EVALUATOR,
                }
            )
        )
    _require_unique_statements(criteria)
    return criteria


def hook_control_criterion(rule: Mapping[str, Any]) -> dict[str, Any]:
    rule_id = str(rule["id"])
    argv = rule.get("run_check")
    if not isinstance(argv, list) or not argv or any(
        not isinstance(item, str) or not item for item in argv
    ):
        raise ValueError(f"hook rule {rule_id} is not a deterministic run_check")
    statement = str(
        rule.get("criterion") or f"Configured check '{rule_id}' exits successfully."
    ).strip()
    if not statement:
        raise ValueError(f"hook rule {rule_id} has an empty criterion statement")
    return _criterion(
        {
            "statement": statement,
            "authority": DETERMINISTIC_CONTROL,
            "evaluator": HOOK_CHECK_EVALUATOR,
            "evaluator_config": {
                "rule_id": rule_id,
                "argv_sha256": digest(argv),
            },
        }
    )


def combine_criteria(
    semantic: Iterable[str], control_rules: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    criteria = [
        *semantic_criteria(semantic),
        *(hook_control_criterion(rule) for rule in control_rules),
    ]
    validate_criteria(criteria)
    return criteria


def validate_criteria(criteria: Iterable[Mapping[str, Any]]) -> None:
    values = list(criteria)
    _require_unique_statements(values)
    for criterion in values:
        authority = criterion.get("authority")
        evaluator = criterion.get("evaluator")
        if authority == SEMANTIC_EXECUTOR:
            if evaluator != SEMANTIC_EVALUATOR or "evaluator_config" in criterion:
                raise ValueError("semantic criteria require the agent-report-v1 evaluator")
        elif authority == DETERMINISTIC_CONTROL:
            if evaluator != HOOK_CHECK_EVALUATOR:
                raise ValueError(f"unsupported deterministic evaluator: {evaluator}")
            config = criterion.get("evaluator_config")
            if not isinstance(config, dict) or set(config) != {"rule_id", "argv_sha256"}:
                raise ValueError("hook control criterion has an invalid evaluator config")
        else:
            raise ValueError(f"unsupported criterion authority: {authority}")
        body = {
            key: value for key, value in criterion.items() if key != "criterion_id"
        }
        if criterion.get("criterion_id") != digest(body):
            raise ValueError("criterion identity does not match its portable content")


def executor_criteria(criteria: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return the only criterion projection that may enter agent execution context."""
    return [
        {
            "criterion_id": criterion["criterion_id"],
            "statement": criterion["statement"],
            "authority": criterion["authority"],
            "evaluator": criterion["evaluator"],
        }
        for criterion in criteria
        if criterion.get("authority") == SEMANTIC_EXECUTOR
    ]


def outcome_for(results: Iterable[Mapping[str, Any]]) -> str:
    values = [str(result.get("result")) for result in results]
    if any(value == "failed" for value in values):
        return "failed"
    if any(value == "blocked" for value in values):
        return "blocked"
    return "succeeded"


def semantic_evidence_sha256(root: Path, evidence: str | None) -> str | None:
    if evidence is None:
        return None
    if evidence.startswith("sha256:") and SHA256_PATTERN.fullmatch(evidence[7:].lower()):
        return evidence[7:].lower()
    candidate = (root / evidence).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    sha256 = hashlib.sha256()
    with candidate.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def evaluate_criteria(
    root: Path,
    criteria: list[dict[str, Any]],
    *,
    invocation_id: str,
    semantic_results: Mapping[str, str],
    evidence: str | None,
    control_event_ids: Iterable[str],
) -> tuple[list[dict[str, Any]], str]:
    validate_criteria(criteria)
    semantic_specs = [
        criterion
        for criterion in criteria
        if criterion.get("authority") == SEMANTIC_EXECUTOR
    ]
    expected = {str(criterion["statement"]) for criterion in semantic_specs}
    supplied = set(semantic_results)
    if supplied != expected:
        missing = sorted(expected - supplied)
        unexpected = sorted(supplied - expected)
        details = []
        if missing:
            details.append("missing=" + repr(missing))
        if unexpected:
            details.append("unexpected=" + repr(unexpected))
        raise ValueError("semantic criterion results must match exactly: " + ", ".join(details))
    if any(value not in RESULTS for value in semantic_results.values()):
        raise ValueError("semantic criterion result must be passed, failed, or blocked")
    evidence_sha256 = semantic_evidence_sha256(root, evidence)
    if semantic_specs and evidence_sha256 is None:
        raise ValueError(
            "semantic criterion results require a repository evidence file or sha256:<digest>"
        )

    results: list[dict[str, Any]] = []
    for criterion in semantic_specs:
        results.append(
            {
                **criterion,
                "result": semantic_results[str(criterion["statement"])],
                "supporting_evidence_sha256": evidence_sha256,
                "responsible_identity": {
                    "kind": "semantic-invocation",
                    "invocation_id": invocation_id,
                },
            }
        )

    events = _load_control_events(root, control_event_ids)
    for criterion in criteria:
        if criterion.get("authority") != DETERMINISTIC_CONTROL:
            continue
        results.append(_evaluate_hook_control(criterion, events, invocation_id))
    return results, outcome_for(results)


def _load_control_events(root: Path, event_ids: Iterable[str]) -> list[dict[str, Any]]:
    requested = list(event_ids)
    if len(requested) != len(set(requested)):
        raise ValueError("control event identities must be unique")
    if not requested:
        return []
    found: dict[str, dict[str, Any]] = {}
    directory = root / ".aae/runtime/hook-events"
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("event_id") in requested:
            found[str(value["event_id"])] = value
    return [found[event_id] for event_id in requested if event_id in found]


def _evaluate_hook_control(
    criterion: dict[str, Any], events: list[dict[str, Any]], invocation_id: str
) -> dict[str, Any]:
    config = criterion.get("evaluator_config", {})
    rule_id = config.get("rule_id")
    matches: list[tuple[dict[str, Any], dict[str, Any], bool]] = []
    for event in events:
        if event.get("for_invocation_id") != invocation_id:
            continue
        recorded_digest = event.get("hook_record_sha256")
        actual_digest = digest(
            {
                key: value
                for key, value in event.items()
                if key not in {"recorded_at", "hook_record_sha256"}
            }
        )
        valid_record = recorded_digest == actual_digest
        for action in event.get("actions", []):
            if (
                isinstance(action, dict)
                and action.get("action") == "run-check"
                and action.get("rule_id") == rule_id
            ):
                matches.append((event, action, valid_record))

    if not matches:
        return _control_result(criterion, "blocked", None, None)
    if len(matches) != 1:
        return _control_result(criterion, "failed", None, None)
    event, action, valid_record = matches[0]
    evidence_sha256 = digest(
        {key: value for key, value in action.items() if key != "criterion_result"}
    )
    identity = {
        "kind": "deterministic-hook",
        "event_id": event.get("event_id"),
        "rule_id": rule_id,
        "invocation_id": invocation_id,
    }
    if not valid_record:
        return _control_result(criterion, "failed", evidence_sha256, identity)
    if action.get("criterion") != criterion:
        return _control_result(criterion, "failed", evidence_sha256, identity)
    status = action.get("status")
    if status in {"denied", "error"}:
        expected = _control_result(criterion, "blocked", None, identity)
        return (
            expected
            if action.get("criterion_result") == expected
            else _control_result(criterion, "failed", evidence_sha256, identity)
        )
    argv = action.get("argv")
    if not isinstance(argv, list) or digest(argv) != config.get("argv_sha256"):
        return _control_result(criterion, "failed", evidence_sha256, identity)
    exit_code = action.get("exit_code")
    if status == "passed" and exit_code == 0:
        result = "passed"
    elif status == "failed" and isinstance(exit_code, int) and exit_code != 0:
        result = "failed"
    else:
        result = "failed"
    expected = _control_result(criterion, result, evidence_sha256, identity)
    return (
        expected
        if action.get("criterion_result") == expected
        else _control_result(criterion, "failed", evidence_sha256, identity)
    )


def _control_result(
    criterion: dict[str, Any],
    result: str,
    evidence_sha256: str | None,
    identity: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        **criterion,
        "result": result,
        "supporting_evidence_sha256": evidence_sha256,
        "responsible_identity": identity
        or {"kind": "deterministic-control", "evaluator": criterion["evaluator"]},
    }


def _require_unique_statements(criteria: Iterable[Mapping[str, Any]]) -> None:
    statements = [str(criterion["statement"]) for criterion in criteria]
    if len(statements) != len(set(statements)):
        raise ValueError("acceptance criterion statements must be unique")
