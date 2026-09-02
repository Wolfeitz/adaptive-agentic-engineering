from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterable
import uuid

from .skills import (
    DATA_CLASSIFICATIONS,
    ROUTABLE_LIFECYCLES,
    SIDE_EFFECTS,
    TRUST_LEVELS,
    discover_skills,
    load_skill_instructions,
)


POLICY_PATH = Path(".aae/skill-policy.json")
INVOCATION_DIRECTORY = Path(".aae/runtime/invocations")
TRUST_RANK = {"untrusted": 0, "declared": 1, "governed": 2}

DEFAULT_POLICY: dict[str, Any] = {
    "schema_version": 1,
    "minimum_trust": "declared",
    "required_source_approval": "approved",
    "allow_advisory_contracts": False,
    "allowed_side_effects": ["read-only", "workspace-write"],
    "approval_required_for": ["external-write", "destructive"],
    "allowed_tools": [],
    "network_allowed": False,
    "allowed_data_classifications": ["public", "internal"],
    "model_authorizations": [],
    "require_verified_signature": False,
}

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

CAPABILITY_RULES: tuple[tuple[set[str], tuple[str, ...]], ...] = (
    (
        {"concurrent", "concurrency", "async", "asyncio", "worker", "workers", "pool"},
        (
            "concurrency-analysis",
            "resource-lifecycle-analysis",
            "cancellation-safety",
            "shutdown-verification",
        ),
    ),
    (
        {"implement", "implementation", "add", "change", "modify", "build"},
        ("implementation-readiness", "change-impact-analysis"),
    ),
    (
        {"test", "tests", "verify", "verification", "regression", "acceptance"},
        ("acceptance-verification", "regression-verification"),
    ),
    (
        {"runtime", "deploy", "deployment", "configuration", "config", "unhealthy"},
        ("runtime-provenance", "configuration-resolution"),
    ),
    (
        {"review", "independent", "security", "architecture"},
        ("independent-review",),
    ),
    (
        {"repository", "repo", "unfamiliar", "ownership", "authority"},
        ("repository-reconnaissance", "authority-discovery"),
    ),
)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def invocation_record_digest(record: dict[str, Any]) -> str:
    return _digest(
        {
            key: value
            for key, value in record.items()
            if key not in {"recorded_at", "runtime_instance", "invocation_record_sha256"}
        }
    )


def invocation_record_digest_is_valid(record: dict[str, Any]) -> bool:
    return record.get("invocation_record_sha256") == invocation_record_digest(record)


def _tokens(value: str) -> set[str]:
    return set(TOKEN_PATTERN.findall(value.lower()))


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


def load_invocation_policy(root: Path) -> tuple[dict[str, Any], list[str]]:
    path = root / POLICY_PATH
    if not path.exists():
        return dict(DEFAULT_POLICY), [
            "No .aae/skill-policy.json exists; using restrictive built-in policy"
        ]
    errors: list[str] = []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return dict(DEFAULT_POLICY), [f"Cannot read invocation policy: {error}"]
    if not isinstance(value, dict):
        return dict(DEFAULT_POLICY), ["Invocation policy must be a JSON object"]
    unknown_fields = sorted(set(value) - set(DEFAULT_POLICY))
    if unknown_fields:
        errors.append(f"Unknown invocation policy fields: {unknown_fields}")
    if value.get("schema_version") != 1:
        errors.append("Invocation policy schema_version must be 1")
    policy = {**DEFAULT_POLICY, **value}
    if policy.get("minimum_trust") not in TRUST_LEVELS:
        errors.append(f"minimum_trust must be one of {sorted(TRUST_LEVELS)}")
    if policy.get("required_source_approval") not in {"approved", "pending", "rejected"}:
        errors.append("required_source_approval must be approved, pending, or rejected")
    if not isinstance(policy.get("allow_advisory_contracts"), bool):
        errors.append("allow_advisory_contracts must be true or false")
    if not isinstance(policy.get("network_allowed"), bool):
        errors.append("network_allowed must be true or false")
    if not isinstance(policy.get("require_verified_signature"), bool):
        errors.append("require_verified_signature must be true or false")
    for field in ("allowed_side_effects", "approval_required_for"):
        values = policy.get(field)
        if not isinstance(values, list) or any(value not in SIDE_EFFECTS for value in values):
            errors.append(f"{field} must contain only {sorted(SIDE_EFFECTS)}")
    allowed_tools = policy.get("allowed_tools")
    if not isinstance(allowed_tools, list) or any(
        not isinstance(value, str) or not value.strip() for value in allowed_tools
    ):
        errors.append("allowed_tools must be a list of non-empty strings")
    classifications = policy.get("allowed_data_classifications")
    if not isinstance(classifications, list) or any(
        value not in DATA_CLASSIFICATIONS for value in classifications
    ):
        errors.append(
            "allowed_data_classifications must contain recognized classification names"
        )
    authorizations = policy.get("model_authorizations")
    if not isinstance(authorizations, list):
        errors.append("model_authorizations must be a list")
    else:
        for index, authorization in enumerate(authorizations):
            location = f"model_authorizations[{index}]"
            if not isinstance(authorization, dict):
                errors.append(f"{location} must be an object")
                continue
            if set(authorization) - {
                "provider",
                "models",
                "capabilities",
                "data_classifications",
            }:
                errors.append(f"{location} has unknown fields")
            if not isinstance(authorization.get("provider"), str):
                errors.append(f"{location}.provider must be text")
            for field in ("models", "capabilities", "data_classifications"):
                values = authorization.get(field)
                if not isinstance(values, list) or any(
                    not isinstance(item, str) or not item.strip() for item in values
                ):
                    errors.append(f"{location}.{field} must be a list of non-empty strings")
            model_classifications = authorization.get("data_classifications", [])
            if isinstance(model_classifications, list) and any(
                value not in DATA_CLASSIFICATIONS for value in model_classifications
            ):
                errors.append(
                    f"{location}.data_classifications contains an unknown classification"
                )
    policy["policy_content_sha256"] = _digest(
        {key: value for key, value in policy.items() if key != "policy_content_sha256"}
    )
    return policy, errors


def build_capability_demand(
    *,
    task: str,
    explicit_capabilities: Iterable[str] = (),
    architecture: Iterable[str] = (),
    environment: Iterable[str] = (),
    risks: Iterable[str] = (),
    evidence_gaps: Iterable[str] = (),
    task_id: str | None = None,
    spec_id: str | None = None,
) -> dict[str, Any]:
    architecture_values = [value.strip() for value in architecture if value.strip()]
    environment_values = [value.strip() for value in environment if value.strip()]
    risk_values = [value.strip() for value in risks if value.strip()]
    gap_values = [value.strip() for value in evidence_gaps if value.strip()]
    capabilities: dict[str, list[dict[str, Any]]] = {}

    for capability in explicit_capabilities:
        if capability.strip():
            capabilities.setdefault(capability.strip(), []).append(
                {"kind": "explicit", "derived_from": "request", "evidence": capability.strip()}
            )

    evidence_fields = {
        "task_intent": [task],
        "architecture": architecture_values,
        "environment": environment_values,
        "risk": risk_values,
        "evidence_gap": gap_values,
    }
    for field, values in evidence_fields.items():
        for value in values:
            value_tokens = _tokens(value)
            for keywords, derived_capabilities in CAPABILITY_RULES:
                matched = sorted(value_tokens & keywords)
                if not matched:
                    continue
                for capability in derived_capabilities:
                    capabilities.setdefault(capability, []).append(
                        {
                            "kind": "deterministic-rule",
                            "derived_from": field,
                            "matched_terms": matched,
                            "evidence": value,
                        }
                    )

    portable = {
        "schema_version": 1,
        "task": task,
        "task_id": task_id,
        "spec_id": spec_id,
        "required_capabilities": sorted(capabilities),
        "architecture_surfaces": architecture_values,
        "environment_constraints": environment_values,
        "risks": risk_values,
        "evidence_gaps": gap_values,
        "provenance": {key: capabilities[key] for key in sorted(capabilities)},
    }
    portable["capability_demand_sha256"] = _digest(portable)
    return portable


def build_candidate_set(
    registry: dict[str, Any],
    demand: dict[str, Any],
    *,
    candidate_limit: int = 18,
    shortlist_limit: int = 4,
) -> dict[str, Any]:
    discovery = discover_skills(
        registry,
        task=str(demand["task"]),
        capabilities=demand["required_capabilities"],
        architecture=demand["architecture_surfaces"],
        environment=demand["environment_constraints"],
        risks=demand["risks"],
        evidence_gaps=demand["evidence_gaps"],
        candidate_limit=candidate_limit,
        limit=shortlist_limit,
    )
    candidates = discovery["shortlist"]
    portable = {
        "schema_version": 1,
        "capability_demand_sha256": demand["capability_demand_sha256"],
        "registry_content_sha256": registry["registry_content_sha256"],
        "candidate_limit": candidate_limit,
        "shortlist_limit": shortlist_limit,
        "registry_skill_count": discovery["registry_skill_count"],
        "eligible_skill_count": discovery["eligible_skill_count"],
        "metadata_candidate_count": discovery["metadata_candidate_count"],
        "candidates": candidates,
    }
    portable["candidate_set_sha256"] = _digest(portable)
    return portable


def select_candidate(
    candidate_set: dict[str, Any], explicit_skill: str | None = None
) -> dict[str, Any]:
    candidates = candidate_set["candidates"]
    selected: dict[str, Any] | None = None
    selection_reason: str | None = None
    if explicit_skill:
        matches = [
            candidate
            for candidate in candidates
            if candidate["registry_id"] == explicit_skill or candidate["name"] == explicit_skill
        ]
        if len(matches) == 1:
            selected = matches[0]
            selection_reason = "explicit-selection-from-eligible-candidate-set"
        elif len(matches) > 1:
            selection_reason = "explicit-skill-name-is-ambiguous"
        else:
            selection_reason = "explicit-skill-is-not-an-eligible-candidate"
    elif candidates:
        selected = candidates[0]
        selection_reason = "highest-deterministic-relevance-score"
    else:
        selection_reason = "no-eligible-candidates"

    considered = []
    for candidate in candidates:
        is_selected = selected is not None and candidate["registry_id"] == selected["registry_id"]
        considered.append(
            {
                "registry_id": candidate["registry_id"],
                "skill_content_sha256": candidate["skill_content_sha256"],
                "score": candidate["score"],
                "selected": is_selected,
                "reason": selection_reason if is_selected else "not-selected-after-ranking",
            }
        )
    portable = {
        "schema_version": 1,
        "candidate_set_sha256": candidate_set["candidate_set_sha256"],
        "selected_registry_id": selected["registry_id"] if selected else None,
        "selected_skill_content_sha256": selected["skill_content_sha256"] if selected else None,
        "reason": selection_reason,
        "candidates_considered": considered,
    }
    portable["selection_decision_sha256"] = _digest(portable)
    return portable


def _policy_check(name: str, passed: bool, observed: object, required: object) -> dict[str, Any]:
    return {"name": name, "passed": passed, "observed": observed, "required": required}


def build_invocation_plan(
    registry: dict[str, Any],
    demand: dict[str, Any],
    candidate_set: dict[str, Any],
    selection: dict[str, Any],
    policy: dict[str, Any],
    runtime_profile: dict[str, Any],
) -> dict[str, Any]:
    selected_id = selection["selected_registry_id"]
    selected = next(
        (skill for skill in registry["skills"] if skill["registry_id"] == selected_id),
        None,
    )
    checks: list[dict[str, Any]] = []
    if selected is None:
        checks.append(_policy_check("selection", False, selected_id, "one eligible skill"))
        plan = {
            "schema_version": 1,
            "registry_content_sha256": registry["registry_content_sha256"],
            "capability_demand_sha256": demand["capability_demand_sha256"],
            "candidate_set_sha256": candidate_set["candidate_set_sha256"],
            "selection_decision_sha256": selection["selection_decision_sha256"],
            "skill": None,
            "binding": None,
            "policy": {
                "policy_content_sha256": policy["policy_content_sha256"],
                "decision": "denied",
                "checks": checks,
                "rejection_reasons": ["No eligible skill was selected"],
            },
        }
        plan["invocation_plan_sha256"] = _digest(plan)
        return plan

    source = selected["source"]
    requirements = selected["requirements"]
    available_tools = set(runtime_profile.get("available_tools", []))
    policy_tools = set(policy.get("allowed_tools", []))
    model_capabilities = set(runtime_profile.get("model_capabilities", []))
    provider = runtime_profile.get("provider")
    model = runtime_profile.get("model")
    approvals = set(runtime_profile.get("approvals", []))
    side_effects = selected["execution"]["side_effects"]
    data_classification = runtime_profile.get("data_classification", "internal")
    model_data = set(runtime_profile.get("model_data_classifications", []))
    platform = runtime_profile.get("platform", sys.platform)
    required_platforms = set(requirements["platforms"])
    contract_states = set(selected["contract_status"].values())
    minimum_trust = policy["minimum_trust"]
    capability_allowlist = set(source.get("capability_allowlist", []))
    model_authorization = next(
        (
            authorization
            for authorization in policy.get("model_authorizations", [])
            if isinstance(authorization, dict)
            and authorization.get("provider") == provider
            and (
                "*" in authorization.get("models", [])
                or model in authorization.get("models", [])
            )
        ),
        None,
    )
    authorized_model_capabilities = set(
        model_authorization.get("capabilities", [])
        if isinstance(model_authorization, dict)
        else []
    )
    authorized_model_data = set(
        model_authorization.get("data_classifications", [])
        if isinstance(model_authorization, dict)
        else []
    )

    checks.extend(
        [
            _policy_check(
                "lifecycle",
                selected["lifecycle"] in ROUTABLE_LIFECYCLES,
                selected["lifecycle"],
                sorted(ROUTABLE_LIFECYCLES),
            ),
            _policy_check(
                "source-trust",
                TRUST_RANK[source["trust"]] >= TRUST_RANK[minimum_trust],
                source["trust"],
                f">={minimum_trust}",
            ),
            _policy_check(
                "source-approval",
                source["approval"]["status"] == policy["required_source_approval"],
                source["approval"]["status"],
                policy["required_source_approval"],
            ),
            _policy_check(
                "source-capabilities",
                "*" in capability_allowlist
                or set(selected["capabilities"]).issubset(capability_allowlist),
                sorted(capability_allowlist),
                selected["capabilities"],
            ),
            _policy_check(
                "source-integrity",
                source["integrity"]["content_status"] != "mismatch",
                source["integrity"]["content_status"],
                "verified or not-pinned",
            ),
            _policy_check(
                "signature",
                not policy["require_verified_signature"]
                or source["integrity"]["signature_status"] == "verified",
                source["integrity"]["signature_status"],
                "verified" if policy["require_verified_signature"] else "not required",
            ),
            _policy_check(
                "contract-enforcement",
                policy["allow_advisory_contracts"] or "advisory" not in contract_states,
                selected["contract_status"],
                "all enforced" if not policy["allow_advisory_contracts"] else "advisory allowed",
            ),
            _policy_check(
                "independence",
                not selected["independence_required"]
                or bool(runtime_profile.get("fresh_context")),
                bool(runtime_profile.get("fresh_context")),
                "fresh context" if selected["independence_required"] else "current context allowed",
            ),
            _policy_check(
                "side-effects",
                side_effects in policy["allowed_side_effects"],
                side_effects,
                policy["allowed_side_effects"],
            ),
            _policy_check(
                "side-effect-approval",
                side_effects not in policy["approval_required_for"]
                or side_effects in approvals
                or f"side-effect:{side_effects}" in approvals,
                sorted(approvals),
                side_effects if side_effects in policy["approval_required_for"] else "not required",
            ),
            _policy_check(
                "tools",
                set(requirements["tools"]).issubset(available_tools),
                sorted(available_tools),
                requirements["tools"],
            ),
            _policy_check(
                "policy-tools",
                set(requirements["tools"]).issubset(policy_tools),
                sorted(policy_tools),
                requirements["tools"],
            ),
            _policy_check(
                "model-authorization",
                model_authorization is not None,
                {"provider": provider, "model": model},
                "an authorized provider/model binding",
            ),
            _policy_check(
                "model-capabilities",
                set(requirements["model_capabilities"]).issubset(model_capabilities)
                and set(requirements["model_capabilities"]).issubset(
                    authorized_model_capabilities
                ),
                {
                    "runtime": sorted(model_capabilities),
                    "authorized": sorted(authorized_model_capabilities),
                },
                requirements["model_capabilities"],
            ),
            _policy_check(
                "platform",
                "any" in required_platforms or platform in required_platforms,
                platform,
                requirements["platforms"],
            ),
            _policy_check(
                "network",
                requirements["network"] != "required"
                or (
                    policy["network_allowed"]
                    and bool(runtime_profile.get("network_available"))
                ),
                {
                    "policy_allows": policy["network_allowed"],
                    "runtime_available": bool(runtime_profile.get("network_available")),
                },
                requirements["network"],
            ),
            _policy_check(
                "policy-data-classification",
                data_classification in policy["allowed_data_classifications"],
                data_classification,
                policy["allowed_data_classifications"],
            ),
            _policy_check(
                "skill-data-classification",
                data_classification in requirements["data_classifications"],
                data_classification,
                requirements["data_classifications"],
            ),
            _policy_check(
                "model-data-classification",
                data_classification in model_data
                and data_classification in authorized_model_data,
                data_classification,
                {
                    "runtime": sorted(model_data),
                    "authorized": sorted(authorized_model_data),
                },
            ),
        ]
    )
    rejection_reasons = [check["name"] for check in checks if not check["passed"]]
    decision = "allowed" if not rejection_reasons else "denied"
    plan = {
        "schema_version": 1,
        "registry_content_sha256": registry["registry_content_sha256"],
        "capability_demand_sha256": demand["capability_demand_sha256"],
        "candidate_set_sha256": candidate_set["candidate_set_sha256"],
        "selection_decision_sha256": selection["selection_decision_sha256"],
        "skill": {
            "registry_id": selected["registry_id"],
            "name": selected["name"],
            "version": selected["version"],
            "skill_content_sha256": selected["skill_content_sha256"],
            "procedure_sha256": selected["procedure_sha256"],
        },
        "binding": {
            "role": "independent-reviewer"
            if selected["independence_required"]
            else "current-agent",
            "context_policy": {
                "fresh_context": "required"
                if selected["independence_required"]
                else "current-allowed"
            },
            "model": model,
            "provider": provider,
            "model_capabilities": sorted(model_capabilities),
            "tools": sorted(available_tools),
            "network_available": bool(runtime_profile.get("network_available")),
            "data_classification": data_classification,
            "side_effects": side_effects,
            "approvals": sorted(approvals),
            "platform": platform,
        },
        "policy": {
            "policy_content_sha256": policy["policy_content_sha256"],
            "decision": decision,
            "checks": checks,
            "rejection_reasons": rejection_reasons,
        },
    }
    plan["invocation_plan_sha256"] = _digest(plan)
    return plan


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
) -> tuple[dict[str, Any], str | None, list[str]]:
    profile = runtime_profile or {}
    policy, policy_errors = load_invocation_policy(root)
    demand = build_capability_demand(
        task=task,
        explicit_capabilities=explicit_capabilities,
        architecture=architecture,
        environment=environment,
        risks=risks,
        evidence_gaps=evidence_gaps,
        task_id=task_id,
        spec_id=spec_id,
    )
    candidate_set = build_candidate_set(
        registry,
        demand,
        candidate_limit=candidate_limit,
        shortlist_limit=shortlist_limit,
    )
    selection = select_candidate(candidate_set, explicit_skill)
    plan = build_invocation_plan(
        registry,
        demand,
        candidate_set,
        selection,
        policy,
        profile,
    )
    if policy_errors:
        plan["policy"]["decision"] = "denied"
        plan["policy"]["rejection_reasons"].extend(
            f"policy-invalid:{error}" for error in policy_errors
        )
        plan["invocation_plan_sha256"] = _digest(
            {
                key: value
                for key, value in plan.items()
                if key != "invocation_plan_sha256"
            }
        )

    invocation_id = str(uuid.uuid4())
    record: dict[str, Any] = {
        "schema_version": 1,
        "invocation_id": invocation_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "task": {"identity": task_id, "spec_identity": spec_id, "intent": task},
        "capability_demand": demand,
        "registry_content_sha256": registry["registry_content_sha256"],
        "candidate_set": candidate_set,
        "selection_decision": selection,
        "invocation_plan": plan,
        "runtime_instance": registry["runtime_instance"],
        "context_evidence_sha256": context_evidence_sha256,
        "status": "denied" if plan["policy"]["decision"] != "allowed" else "planned",
        "procedure_loaded": False,
        "execution": None,
        "outcome": None,
    }
    procedure: str | None = None
    if plan["policy"]["decision"] == "allowed":
        selected_id = selection["selected_registry_id"]
        skill, procedure, load_error = load_skill_instructions(
            registry,
            selected_id,
            authorization=plan,
        )
        if load_error:
            record["status"] = "load-failed"
            record["invocation_plan"]["policy"]["decision"] = "denied"
            record["invocation_plan"]["policy"]["rejection_reasons"].append(
                f"procedure-load:{load_error}"
            )
            record["invocation_plan"]["invocation_plan_sha256"] = _digest(
                {
                    key: value
                    for key, value in record["invocation_plan"].items()
                    if key != "invocation_plan_sha256"
                }
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

    record["invocation_record_sha256"] = invocation_record_digest(record)
    _write_json(root / INVOCATION_DIRECTORY / f"{invocation_id}.json", record)
    return record, procedure, policy_errors


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
    try:
        parsed_id = uuid.UUID(invocation_id)
    except ValueError:
        return "Invocation id must be a canonical UUID"
    if str(parsed_id) != invocation_id:
        return "Invocation id must be a canonical UUID"
    path = root / INVOCATION_DIRECTORY / f"{invocation_id}.json"
    if not path.is_file():
        return f"Invocation record not found: {invocation_id}"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return f"Cannot read invocation record: {error}"
    if not isinstance(record, dict) or not invocation_record_digest_is_valid(record):
        return f"Invocation {invocation_id} has an invalid record digest"
    if record.get("status") != "procedure-loaded":
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
    record["invocation_record_sha256"] = invocation_record_digest(record)
    _write_json(path, record)
    return None
