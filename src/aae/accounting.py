from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from .adaptive import load_model_profiles, skill_retriever_entry_points
from .control import invocation_record_digest_is_valid, load_invocation_policy
from .execution import (
    EXECUTION_CONFIG,
    context_packet_digest_is_valid,
    execution_artifact_digest_is_valid,
    filesystem_boundary_proof_digest_is_valid,
    governed_run_digest_is_valid,
    load_execution_configuration,
)
from .semantic import provider_entry_points
from .skills import build_skill_registry


def build_agent_skill_accounting(
    root: Path,
) -> tuple[dict[str, Any], list[str], list[str]]:
    registry, errors, warnings = build_skill_registry(root)
    policy, policy_errors = load_invocation_policy(root)
    errors.extend(policy_errors)
    skills = registry.get("skills", [])
    lifecycle_counts = Counter(str(skill["lifecycle"]) for skill in skills)
    mode_counts = Counter(str(skill["execution"]["mode"]) for skill in skills)
    side_effect_counts = Counter(
        str(skill["execution"]["side_effects"]) for skill in skills
    )
    independent = [
        str(skill["registry_id"])
        for skill in skills
        if skill.get("independence_required")
    ]
    invocation_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    invocation_directory = root / ".aae/runtime/invocations"
    invocation_paths = (
        sorted(invocation_directory.glob("*.json"))
        if invocation_directory.exists()
        else []
    )
    for path in invocation_paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            warnings.append(f"Cannot read invocation accounting record {path}: {error}")
            continue
        if not isinstance(record, dict):
            warnings.append(f"Invocation accounting record is not an object: {path}")
            continue
        if not invocation_record_digest_is_valid(record):
            errors.append(f"Invocation accounting record digest is invalid: {path}")
            continue
        invocation_counts[str(record.get("status", "unknown"))] += 1
        plan = record.get("invocation_plan")
        if isinstance(plan, dict):
            binding = plan.get("binding")
            if isinstance(binding, dict):
                role_counts[str(binding.get("role", "unknown"))] += 1
    governed_runs: list[dict[str, Any]] = []
    governed_directory = root / ".aae/state/governed-runs"
    if (root / EXECUTION_CONFIG).exists():
        try:
            execution_configuration = load_execution_configuration(
                root, require_effective_executor=False
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            errors.append(f"Governed execution configuration is invalid: {error}")
        else:
            governed_directory = root / execution_configuration["accounting_directory"]
    governed_paths = (
        sorted(governed_directory.glob("*.json"))
        if governed_directory.exists()
        else []
    )
    for path in governed_paths:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            warnings.append(f"Cannot read governed-run accounting record {path}: {error}")
            continue
        if not isinstance(run, dict) or not governed_run_digest_is_valid(run):
            errors.append(f"Governed-run accounting record digest is invalid: {path}")
            continue
        primary = run.get("primary")
        review = run.get("review")
        task = run.get("task_request")
        if not isinstance(primary, dict) or not isinstance(review, dict) or not isinstance(task, dict):
            errors.append(f"Governed-run accounting record structure is invalid: {path}")
            continue
        for role, value in (("primary", primary), ("review", review)):
            execution_id = value.get("execution_id")
            expected_execution_sha256 = value.get("execution_sha256")
            if isinstance(execution_id, str):
                execution_path = (
                    root / ".aae/runtime/executions" / f"{execution_id}.json"
                )
                if not execution_path.is_file():
                    warnings.append(
                        f"Governed run {path.name} {role} runtime execution "
                        "artifact is not retained"
                    )
                else:
                    try:
                        execution = json.loads(
                            execution_path.read_text(encoding="utf-8")
                        )
                    except (OSError, UnicodeError, json.JSONDecodeError) as error:
                        errors.append(
                            f"Cannot read governed run {path.name} {role} "
                            f"execution artifact: {error}"
                        )
                    else:
                        if (
                            not isinstance(execution, dict)
                            or not execution_artifact_digest_is_valid(execution)
                            or execution.get("execution_sha256")
                            != expected_execution_sha256
                            or execution.get("execution_id") != execution_id
                            or execution.get("invocation_id")
                            != value.get("invocation_id")
                            or execution.get("role")
                            != ("executor" if role == "primary" else "reviewer")
                            or execution.get("context_packet_sha256")
                            != value.get("context_packet_sha256")
                            or execution.get("provider") != value.get("provider")
                            or execution.get("model") != value.get("model")
                            or execution.get("thread_id") != value.get("thread_id")
                            or execution.get("duration_ns") != value.get("duration_ns")
                            or execution.get("usage") != value.get("usage")
                            or execution.get("disposition")
                            != value.get("execution_disposition")
                            or execution.get("authoritative_outcome")
                            != value.get("authoritative_outcome")
                            or execution.get("raw_output_sha256")
                            != value.get("raw_output_sha256")
                            or execution.get("parsed_output_sha256")
                            != value.get("parsed_output_sha256")
                            or execution.get("validation_failure")
                            != value.get("validation_failure")
                            or execution.get("result") != value.get("result")
                            or execution.get("changed_project_paths")
                            != value.get("changed_project_paths")
                            or execution.get("filesystem_boundary")
                            != value.get("filesystem_boundary")
                        ):
                            errors.append(
                                f"Governed run {path.name} {role} execution "
                                "artifact does not reconcile"
                            )
                        boundary = execution.get("filesystem_boundary")
                        if isinstance(boundary, dict):
                            boundary_path = (
                                root
                                / ".aae/runtime/filesystem-boundaries"
                                / f"{execution_id}.json"
                            )
                            try:
                                stored_boundary = json.loads(
                                    boundary_path.read_text(encoding="utf-8")
                                )
                            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                                errors.append(
                                    f"Cannot read governed run {path.name} {role} "
                                    f"filesystem boundary proof: {error}"
                                )
                            else:
                                if (
                                    not isinstance(stored_boundary, dict)
                                    or not filesystem_boundary_proof_digest_is_valid(
                                        stored_boundary
                                    )
                                    or stored_boundary != boundary
                                    or stored_boundary.get("execution_id") != execution_id
                                    or stored_boundary.get("context_packet_sha256")
                                    != value.get("context_packet_sha256")
                                    or stored_boundary.get("invocation_plan_sha256")
                                    != value.get("invocation_plan_sha256")
                                ):
                                    errors.append(
                                        f"Governed run {path.name} {role} filesystem "
                                        "boundary proof does not reconcile"
                                    )
            invocation_id = value.get("invocation_id")
            if isinstance(invocation_id, str):
                invocation_path = (
                    root / ".aae/runtime/invocations" / f"{invocation_id}.json"
                )
                if invocation_path.is_file():
                    try:
                        invocation = json.loads(
                            invocation_path.read_text(encoding="utf-8")
                        )
                    except (OSError, UnicodeError, json.JSONDecodeError) as error:
                        errors.append(
                            f"Cannot read governed run {path.name} {role} "
                            f"invocation record: {error}"
                        )
                    else:
                        if (
                            not isinstance(invocation, dict)
                            or not invocation_record_digest_is_valid(invocation)
                            or invocation.get("invocation_id") != invocation_id
                            or invocation.get("context_evidence_sha256")
                            != value.get("context_packet_sha256")
                            or invocation.get("invocation_plan", {}).get(
                                "invocation_plan_sha256"
                            )
                            != value.get("invocation_plan_sha256")
                            or (
                                value.get("invocation_status") is not None
                                and invocation.get("status")
                                != value.get("invocation_status")
                            )
                        ):
                            errors.append(
                                f"Governed run {path.name} {role} invocation "
                                "record does not reconcile"
                            )
                else:
                    warnings.append(
                        f"Governed run {path.name} {role} runtime invocation "
                        "record is not retained"
                    )
        for role, value in (("primary", primary), ("review", review)):
            packet_sha256 = value.get("context_packet_sha256")
            if isinstance(packet_sha256, str):
                packet_path = (
                    root / ".aae/runtime/context-packets" / f"{packet_sha256}.json"
                )
                if packet_path.is_file():
                    try:
                        packet = json.loads(packet_path.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError) as error:
                        errors.append(
                            f"Cannot read governed run {path.name} {role} "
                            f"context packet: {error}"
                        )
                    else:
                        if (
                            not isinstance(packet, dict)
                            or not context_packet_digest_is_valid(packet)
                            or packet.get("packet_sha256") != packet_sha256
                        ):
                            errors.append(
                                f"Governed run {path.name} {role} context "
                                "packet does not reconcile"
                            )
                        if role == "review" and isinstance(
                            primary.get("filesystem_boundary"), dict
                        ):
                            proof_items = [
                                item
                                for item in packet.get("items", [])
                                if isinstance(item, dict)
                                and item.get("path") == "AAE_RUNTIME_BOUNDARY.json"
                            ]
                            expected_content = json.dumps(
                                primary["filesystem_boundary"],
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                            if (
                                len(proof_items) != 1
                                or proof_items[0].get("content") != expected_content
                            ):
                                errors.append(
                                    f"Governed run {path.name} reviewer packet does "
                                    "not bind primary filesystem boundary proof"
                                )
                else:
                    warnings.append(
                        f"Governed run {path.name} {role} runtime context "
                        "packet is not retained"
                    )
        governed_runs.append(
            {
                "run_id": run.get("run_id"),
                "run_sha256": run.get("run_sha256"),
                "status": run.get("status"),
                "task": task,
                "selected_skill": primary.get("skill"),
                "selection_reason": primary.get("selection_reason"),
                "capability_demand": primary.get("capability_demand"),
                "policy": primary.get("policy"),
                "invocation_id": primary.get("invocation_id"),
                "invocation_plan_sha256": primary.get("invocation_plan_sha256"),
                "context_packet_sha256": primary.get("context_packet_sha256"),
                "context_packet": primary.get("context_packet"),
                "executor": {
                    "provider": primary.get("provider"),
                    "model": primary.get("model"),
                    "tool": primary.get("tool"),
                },
                "authorized_side_effects": primary.get("authorized_side_effects"),
                "changed_project_paths": primary.get("changed_project_paths"),
                "usage": primary.get("usage"),
                "review": review,
                "duration_ns": run.get("duration_ns"),
                "fallbacks": run.get("fallbacks"),
                "retries": run.get("retries"),
            }
        )
    model_profiles, model_profile_errors = load_model_profiles(root)
    configured_profiles = model_profiles.get("profiles", [])
    if model_profile_errors and (root / ".aae/model-profiles.json").exists():
        errors.extend(model_profile_errors)
    accounting: dict[str, Any] = {
        "schema_version": 1,
        "agent_model": {
            "persistent_named_agents": 0,
            "runtime_agents_are_ephemeral": True,
            "selection_rule": "A selected skill is normally executed by the current agent; a fresh independent reviewer is required only when the skill or consequence policy requires independence.",
            "roles": [
                {
                    "role": "current-agent",
                    "purpose": "Execute an allowed selected skill in the current bounded context.",
                    "persistent": False,
                },
                {
                    "role": "independent-reviewer",
                    "purpose": "Execute independence-required review from fresh bounded evidence.",
                    "persistent": False,
                    "triggered_by": independent,
                },
                {
                    "role": "deterministic-control-plane",
                    "purpose": "Derive and bind identities, validate policy, rank candidates, persist evidence, and authorize state transitions in code rather than agent judgment.",
                    "persistent": True,
                    "is_agent": False,
                },
            ],
        },
        "skill_fabric": {
            "registry_sha256": registry.get("registry_sha256"),
            "source_count": registry.get("source_count", 0),
            "skill_count": registry.get("skill_count", 0),
            "capability_count": len(registry.get("capabilities", [])),
            "lifecycle_counts": dict(sorted(lifecycle_counts.items())),
            "execution_mode_counts": dict(sorted(mode_counts.items())),
            "side_effect_counts": dict(sorted(side_effect_counts.items())),
            "skills": [
                {
                    "registry_id": skill["registry_id"],
                    "version": skill["version"],
                    "capabilities": skill["capabilities"],
                    "lifecycle": skill["lifecycle"],
                    "execution": skill["execution"],
                    "requirements": skill.get("requirements", {}),
                    "independence_required": skill["independence_required"],
                    "source_id": skill["source"]["id"],
                    "skill_content_sha256": skill.get("skill_content_sha256"),
                }
                for skill in skills
            ],
        },
        "authority": {
            "invocation_policy_sha256": policy.get("policy_content_sha256"),
            "minimum_trust": policy.get("minimum_trust"),
            "required_source_approval": policy.get("required_source_approval"),
            "allowed_side_effects": policy.get("allowed_side_effects"),
            "approval_required_for": policy.get("approval_required_for"),
            "automatic_skill_promotion": False,
            "automatic_agent_creation": False,
        },
        "runtime_evidence": {
            "invocation_count": sum(invocation_counts.values()),
            "invocation_status_counts": dict(sorted(invocation_counts.items())),
            "runtime_role_counts": dict(sorted(role_counts.items())),
            "governed_run_count": len(governed_runs),
            "governed_runs": governed_runs,
        },
        "extension_points": {
            "semantic_provider_entry_points": sorted(provider_entry_points()),
            "skill_retriever_entry_points": sorted(skill_retriever_entry_points()),
            "configured_model_profiles": (
                len(configured_profiles) if isinstance(configured_profiles, list) else 0
            ),
        },
        "component_accounting": [
            {
                "component": "intent-and-semantic-compiler",
                "kind": "deterministic-control-plane",
                "is_agent": False,
            },
            {
                "component": "skill-registry-and-invocation-policy",
                "kind": "deterministic-control-plane",
                "is_agent": False,
            },
            {
                "component": "model-router",
                "kind": "deterministic-control-plane",
                "is_agent": False,
            },
            {
                "component": "semantic-provider",
                "kind": "optional-runtime-adapter",
                "is_agent": False,
            },
            {
                "component": "semantic-skill-retriever",
                "kind": "optional-runtime-adapter",
                "is_agent": False,
            },
        ],
        "implementation_boundaries": {
            "implemented": [
                "skill advertisement normalization and provenance",
                "bounded deterministic discovery",
                "policy-checked invocation planning",
                "explicit procedure loading",
                "ephemeral role binding",
                "versioned invocation and outcome evidence",
                "provider-neutral semantic task and review packets",
                "deterministic model routing and fallback ordering",
                "bounded semantic skill-retriever contract",
                "advisory lifecycle evaluation and promotion proposals",
                "historical-use graph",
                "CI policy and OpenTelemetry-compatible trace exports",
                "bounded governed execution through an explicit Codex CLI adapter",
                "separate-process independent review with neutral evidence packets",
            ],
            "not_implied": [
                "a permanent multi-agent cast",
                "autonomous authority promotion",
                "configured credentials or implicit SaaS submission",
                "distributed orchestration",
            ],
        },
    }
    return accounting, errors, warnings
