from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from .skills import build_skill_registry


def build_agent_skill_accounting(
    root: Path,
) -> tuple[dict[str, Any], list[str], list[str]]:
    registry, errors, warnings = build_skill_registry(root)
    skills = registry.get("skills", [])
    invocation_counts: Counter[str] = Counter()
    invocation_directory = root / ".aae/runtime/invocations"
    for path in sorted(invocation_directory.glob("*.json")) if invocation_directory.exists() else []:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            warnings.append(f"Cannot read invocation accounting record {path}: {error}")
            continue
        invocation_counts[str(record.get("status", "unknown"))] += 1
    capabilities = sorted(
        {capability for skill in skills for capability in skill.get("capabilities", [])}
    )
    return {
        "schema_version": 1,
        "agent_model": {
            "persistent_named_agents": 0,
            "runtime_agents_are_ephemeral": True,
            "roles": [
                {
                    "role": "current-agent",
                    "purpose": "Run a selected skill in the current context.",
                    "persistent": False,
                },
                {
                    "role": "independent-reviewer",
                    "purpose": "Run an independence-required skill from fresh context.",
                    "persistent": False,
                },
                {
                    "role": "deterministic-control-plane",
                    "purpose": "Index advertisements, match tasks, enforce basic safety, and record outcomes.",
                    "persistent": True,
                    "is_agent": False,
                },
            ],
        },
        "skill_fabric": {
            "registry_sha256": registry.get("registry_sha256"),
            "source_count": len(registry.get("sources", [])),
            "skill_count": len(skills),
            "capability_count": len(capabilities),
            "skills": [
                {
                    "registry_id": skill["registry_id"],
                    "version": skill["version"],
                    "when_to_use": skill.get("when_to_use", []),
                    "capabilities": skill.get("capabilities", []),
                    "requires_tools": skill.get("requires_tools", []),
                    "destructive": skill.get("destructive", False),
                    "independence_required": skill.get("independence_required", False),
                }
                for skill in skills
            ],
        },
        "safety": {
            "local_skills_cannot_silently_replace_other_sources": True,
            "destructive_skills_require_approval": True,
            "required_tools_must_be_available": True,
            "automatic_skill_creation": False,
            "automatic_agent_creation": False,
        },
        "runtime_evidence": {
            "invocation_count": sum(invocation_counts.values()),
            "invocation_status_counts": dict(sorted(invocation_counts.items())),
        },
        "extension_points": {"configured_model_profiles": 0},
        "component_accounting": [
            {"component": "skill-registry", "kind": "deterministic-index", "is_agent": False},
            {"component": "skill-matcher", "kind": "deterministic-router", "is_agent": False},
            {"component": "hook-runner", "kind": "deterministic-event-handler", "is_agent": False},
        ],
    }, errors, warnings
