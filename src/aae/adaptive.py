from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, cast

from .skills import build_skill_registry, summarize_skill_events


MODEL_PROFILES = Path(".aae/model-profiles.json")
SKILL_EVALUATION = Path(".aae/skill-evaluation.json")
INVOCATION_DIRECTORY = Path(".aae/runtime/invocations")
MODEL_LOCATIONS = {"local", "on-premises", "cloud"}
NETWORK_MODES = {"none", "optional", "required"}
LIFECYCLE_ORDER = (
    "candidate",
    "experimental",
    "validated",
    "project",
    "enterprise",
    "deprecated",
    "retired",
)
PROMOTION_TRANSITIONS = {
    "candidate": "experimental",
    "experimental": "validated",
    "validated": "project",
    "project": "enterprise",
}


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def canonical_digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return cast(dict[str, Any], value)


def load_model_profiles(root: Path) -> tuple[dict[str, Any], list[str]]:
    path = root / MODEL_PROFILES
    if not path.is_file():
        return {"schema_version": 1, "profiles": []}, [
            f"No {MODEL_PROFILES} exists; no model can be routed"
        ]
    try:
        config = _read_object(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        return {"schema_version": 1, "profiles": []}, [str(error)]
    errors: list[str] = []
    if config.get("schema_version") != 1:
        errors.append("model profile schema_version must be 1")
    profiles = config.get("profiles")
    if not isinstance(profiles, list):
        return config, [*errors, "model profiles must be a list"]
    seen: set[str] = set()
    for index, profile in enumerate(profiles):
        location = f"profiles[{index}]"
        if not isinstance(profile, dict):
            errors.append(f"{location} must be an object")
            continue
        identifier = profile.get("id")
        if not isinstance(identifier, str) or not identifier:
            errors.append(f"{location}.id must be non-empty text")
        elif identifier in seen:
            errors.append(f"duplicate model profile id: {identifier}")
        else:
            seen.add(identifier)
        for field in ("provider", "model"):
            if not isinstance(profile.get(field), str) or not profile[field]:
                errors.append(f"{location}.{field} must be non-empty text")
        if profile.get("location") not in MODEL_LOCATIONS:
            errors.append(f"{location}.location must be one of {sorted(MODEL_LOCATIONS)}")
        if profile.get("network", "none") not in NETWORK_MODES:
            errors.append(f"{location}.network must be one of {sorted(NETWORK_MODES)}")
        for field in ("capabilities", "data_classifications", "fallback_to"):
            values = profile.get(field, [])
            if not isinstance(values, list) or any(
                not isinstance(item, str) or not item for item in values
            ):
                errors.append(f"{location}.{field} must be a list of non-empty strings")
        for field in ("preference", "cost_rank"):
            value = profile.get(field, 100)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                errors.append(f"{location}.{field} must be a non-negative integer")
        if not isinstance(profile.get("available", True), bool):
            errors.append(f"{location}.available must be true or false")
    known = {
        profile.get("id")
        for profile in profiles
        if isinstance(profile, dict) and isinstance(profile.get("id"), str)
    }
    for index, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            continue
        for fallback in profile.get("fallback_to", []):
            if fallback not in known:
                errors.append(f"profiles[{index}] references unknown fallback: {fallback}")
    return config, errors


def route_model(
    root: Path,
    *,
    capabilities: Sequence[str],
    data_classification: str,
    network_available: bool,
    allowed_locations: Sequence[str] = ("local", "on-premises", "cloud"),
) -> dict[str, Any]:
    config, errors = load_model_profiles(root)
    if errors:
        raise ValueError("; ".join(errors))
    required = set(capabilities)
    allowed = set(allowed_locations)
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for raw in config.get("profiles", []):
        profile = cast(dict[str, Any], raw)
        reasons: list[str] = []
        if not profile.get("available", True):
            reasons.append("unavailable")
        if profile.get("location") not in allowed:
            reasons.append("location")
        if not required.issubset(set(profile.get("capabilities", []))):
            reasons.append("capabilities")
        if data_classification not in profile.get("data_classifications", []):
            reasons.append("data-classification")
        if profile.get("network", "none") == "required" and not network_available:
            reasons.append("network")
        public = {
            key: value
            for key, value in profile.items()
            if key not in {"credentials", "token", "secret"}
        }
        if reasons:
            rejected.append({"profile": public, "reasons": reasons})
        else:
            candidates.append(public)
    candidates.sort(
        key=lambda item: (
            int(item.get("preference", 100)),
            int(item.get("cost_rank", 100)),
            str(item["id"]),
        )
    )
    selected = candidates[0] if candidates else None
    fallback_order: list[str] = []
    if selected:
        eligible_ids = {str(item["id"]) for item in candidates}
        fallback_order = [
            identifier
            for identifier in selected.get("fallback_to", [])
            if identifier in eligible_ids
        ]
        fallback_order.extend(
            str(item["id"])
            for item in candidates[1:]
            if str(item["id"]) not in fallback_order
        )
    route: dict[str, Any] = {
        "schema_version": 1,
        "request": {
            "capabilities": sorted(required),
            "data_classification": data_classification,
            "network_available": network_available,
            "allowed_locations": sorted(allowed),
        },
        "selected": selected,
        "fallback_order": fallback_order,
        "eligible": candidates,
        "rejected": rejected,
        "decision": "selected" if selected else "no-eligible-model",
    }
    route["route_sha256"] = canonical_digest(route)
    return route


class SemanticSkillRetriever(Protocol):
    def rank(
        self, request: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]
    ) -> Sequence[Mapping[str, Any]]: ...


def skill_retriever_entry_points() -> dict[str, importlib.metadata.EntryPoint]:
    points = importlib.metadata.entry_points()
    return {
        point.name: point for point in points.select(group="aae.skill_retrievers")
    }


def rerank_with_retriever(
    retriever: SemanticSkillRetriever,
    request: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *,
    limit: int,
) -> dict[str, Any]:
    allowed = {str(candidate["registry_id"]): candidate for candidate in candidates}
    raw = retriever.rank(request, candidates)
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"retriever result {index} must be an object")
        identifier = item.get("registry_id")
        score = item.get("score")
        if identifier not in allowed:
            raise ValueError(f"retriever returned an out-of-candidate skill: {identifier}")
        if identifier in seen:
            raise ValueError(f"retriever returned a duplicate skill: {identifier}")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError(f"retriever score for {identifier} must be numeric")
        key = cast(str, identifier)
        seen.add(key)
        ranked.append(
            {
                **dict(allowed[key]),
                "retrieval": {
                    "semantic_score": float(score),
                    "reason": item.get("reason"),
                },
            }
        )
    ranked.sort(
        key=lambda item: (
            -float(item["retrieval"]["semantic_score"]),
            str(item["registry_id"]),
        )
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "candidate_registry_ids": sorted(allowed),
        "shortlist": ranked[: max(limit, 0)],
    }
    result["retrieval_sha256"] = canonical_digest(result)
    return result


def build_historical_use_graph(root: Path) -> dict[str, Any]:
    registry, errors, _ = build_skill_registry(root)
    if errors:
        raise ValueError("; ".join(errors))
    summaries, warnings = summarize_skill_events(root, registry)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for versioned_id, summary in sorted(summaries.items()):
        nodes.append(
            {
                "id": f"skill:{versioned_id}",
                "kind": "skill-version",
                "summary": summary,
            }
        )
    invocation_dir = root / INVOCATION_DIRECTORY
    paths = sorted(invocation_dir.glob("*.json")) if invocation_dir.exists() else []
    for path in paths:
        try:
            record = _read_object(path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            warnings.append(f"{path}: {error}")
            continue
        task = record.get("task", {})
        selected = record.get("selection_decision", {}).get("selected_registry_id")
        if not isinstance(selected, str):
            continue
        task_id = task.get("identity") or f"invocation:{record.get('invocation_id')}"
        version = record.get("invocation_plan", {}).get("skill", {}).get("version")
        nodes.append({"id": f"task:{task_id}", "kind": "task"})
        edges.append(
            {
                "from": f"task:{task_id}",
                "to": f"skill:{selected}@{version}",
                "kind": "selected",
                "outcome": (record.get("outcome") or {}).get("result"),
            }
        )
    graph: dict[str, Any] = {
        "schema_version": 1,
        "nodes": sorted(nodes, key=lambda item: str(item["id"])),
        "edges": sorted(edges, key=lambda item: (str(item["from"]), str(item["to"]))),
        "warnings": warnings,
    }
    graph["graph_sha256"] = canonical_digest(graph)
    return graph


def _evidence_reference_count(root: Path, registry_id: str) -> int:
    directory = root / ".aae/runtime/skill-events"
    count = 0
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            event = _read_object(path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continue
        if event.get("registry_id") == registry_id and event.get("evidence"):
            count += 1
    return count


def evaluate_skill_lifecycle(root: Path, registry_id: str) -> dict[str, Any]:
    config_path = root / SKILL_EVALUATION
    if not config_path.is_file():
        raise ValueError(f"{SKILL_EVALUATION} does not exist")
    config = _read_object(config_path)
    if config.get("schema_version") != 1:
        raise ValueError("skill evaluation schema_version must be 1")
    thresholds = config.get("promotion_thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("promotion_thresholds must be an object")
    registry, errors, _ = build_skill_registry(root)
    if errors:
        raise ValueError("; ".join(errors))
    summaries, warnings = summarize_skill_events(root, registry)
    matches = [value for value in summaries.values() if value["registry_id"] == registry_id]
    if not matches:
        raise ValueError(f"skill is not in the registry: {registry_id}")
    summary = matches[0]
    checks = {
        "minimum_selected": int(summary["selected"])
        >= int(thresholds.get("minimum_selected", 1)),
        "minimum_succeeded": int(summary["succeeded"])
        >= int(thresholds.get("minimum_succeeded", 1)),
        "maximum_failure_rate": summary["failure_rate"] is not None
        and float(summary["failure_rate"])
        <= float(thresholds.get("maximum_failure_rate", 0.1)),
        "minimum_evidence_references": _evidence_reference_count(root, registry_id)
        >= int(thresholds.get("minimum_evidence_references", 1)),
    }
    evaluation: dict[str, Any] = {
        "schema_version": 1,
        "registry_id": registry_id,
        "summary": summary,
        "thresholds": thresholds,
        "checks": checks,
        "eligible_for_proposal": all(checks.values()),
        "warnings": warnings,
        "authority": "advisory-only-human-or-project-approval-required",
    }
    evaluation["evaluation_sha256"] = canonical_digest(evaluation)
    return evaluation


def build_promotion_proposal(
    root: Path, registry_id: str, target_lifecycle: str
) -> dict[str, Any]:
    if target_lifecycle not in LIFECYCLE_ORDER:
        raise ValueError(f"unknown lifecycle: {target_lifecycle}")
    evaluation = evaluate_skill_lifecycle(root, registry_id)
    registry, _, _ = build_skill_registry(root)
    skill = next(
        item for item in registry["skills"] if item["registry_id"] == registry_id
    )
    current = str(skill["lifecycle"])
    if PROMOTION_TRANSITIONS.get(current) != target_lifecycle:
        raise ValueError("promotion proposals may advance exactly one lifecycle state")
    proposal: dict[str, Any] = {
        "schema_version": 1,
        "registry_id": registry_id,
        "skill_content_sha256": skill["skill_content_sha256"],
        "from_lifecycle": current,
        "to_lifecycle": target_lifecycle,
        "evaluation_sha256": evaluation["evaluation_sha256"],
        "eligible": evaluation["eligible_for_proposal"],
        "decision": "proposal-only-not-applied",
        "required_approval": "owning-project-governance",
    }
    proposal["proposal_sha256"] = canonical_digest(proposal)
    return proposal


def build_ci_policy(provider: str) -> dict[str, Any]:
    commands = [
        "python -m unittest discover -s tests -v",
        "mypy src/aae tests",
        "aae validate .",
    ]
    if provider == "github":
        payload: dict[str, Any] = {
            "name": "AAE validation",
            "on": {"push": {}, "pull_request": {}},
            "jobs": {
                "validate": {
                    "runs-on": "ubuntu-latest",
                    "steps": [
                        {"uses": "actions/checkout@v4"},
                        {
                            "uses": "actions/setup-python@v5",
                            "with": {"python-version": "3.12"},
                        },
                        {"run": "python -m pip install -e . mypy"},
                        *[{"run": command} for command in commands],
                    ],
                }
            },
        }
    elif provider == "azure":
        payload = {
            "trigger": ["main"],
            "pool": {"vmImage": "ubuntu-latest"},
            "steps": [
                {"task": "UsePythonVersion@0", "inputs": {"versionSpec": "3.12"}},
                {"script": "python -m pip install -e . mypy"},
                *[{"script": command} for command in commands],
            ],
        }
    elif provider == "gitlab":
        payload = {
            "image": "python:3.12",
            "stages": ["validate"],
            "aae-validate": {
                "stage": "validate",
                "script": ["python -m pip install -e . mypy", *commands],
            },
        }
    else:
        raise ValueError("CI provider must be github, azure, or gitlab")
    return {
        "schema_version": 1,
        "provider": provider,
        "format": "provider-neutral-json-representation",
        "payload": payload,
        "payload_sha256": canonical_digest(payload),
    }


def build_otel_genai_trace_export(root: Path) -> dict[str, Any]:
    spans: list[dict[str, Any]] = []
    directory = root / INVOCATION_DIRECTORY
    paths = sorted(directory.glob("*.json")) if directory.exists() else []
    for path in paths:
        record = _read_object(path)
        plan = record.get("invocation_plan", {})
        binding = plan.get("binding", {})
        outcome = record.get("outcome") or {}
        attributes = [
            {
                "key": "gen_ai.operation.name",
                "value": {"stringValue": "execute_skill"},
            },
            {
                "key": "gen_ai.provider.name",
                "value": {"stringValue": str(binding.get("provider") or "unavailable")},
            },
            {
                "key": "gen_ai.request.model",
                "value": {"stringValue": str(binding.get("model") or "unavailable")},
            },
            {
                "key": "aae.invocation.id",
                "value": {"stringValue": str(record.get("invocation_id"))},
            },
            {
                "key": "aae.skill.id",
                "value": {
                    "stringValue": str(plan.get("skill", {}).get("registry_id"))
                },
            },
            {
                "key": "aae.outcome",
                "value": {
                    "stringValue": str(outcome.get("result") or record.get("status"))
                },
            },
        ]
        spans.append(
            {
                "traceId": hashlib.sha256(
                    str(record.get("invocation_id")).encode()
                ).hexdigest()[:32],
                "spanId": hashlib.sha256(path.name.encode()).hexdigest()[:16],
                "name": "aae.skill.invoke",
                "kind": 1,
                "attributes": attributes,
                "status": {"code": 1 if outcome.get("result") != "failed" else 2},
            }
        )
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "aae"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "adaptive-agentic-engineering"},
                        "spans": spans,
                    }
                ],
            }
        ]
    }
