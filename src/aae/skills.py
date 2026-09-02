from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
from typing import Any, Iterable
import uuid


SKILL_DIRECTORY = Path(".aae/skills")
SKILL_SOURCES = Path(".aae/skill-sources.json")
LOCAL_SKILL_SOURCES = Path(".aae/skill-sources.local.json")
SKILL_EVENTS = Path(".aae/runtime/skill-events.jsonl")
SKILL_EVENT_DIRECTORY = Path(".aae/runtime/skill-events")

ADAPTERS = {"aae-json", "skill-md", "registry-json"}
SCOPES = {"enterprise", "project", "runtime", "local"}
LIFECYCLES = {
    "candidate",
    "experimental",
    "validated",
    "project",
    "enterprise",
    "deprecated",
    "retired",
}
ROUTABLE_LIFECYCLES = {"experimental", "validated", "project", "enterprise"}
EXECUTION_MODES = {"deterministic", "procedural", "agentic", "hybrid"}
SIDE_EFFECTS = {"read-only", "workspace-write", "external-write", "destructive"}
COST_LEVELS = {"low", "medium", "high"}
EVENT_TYPES = {"considered", "selected", "succeeded", "failed", "superseded"}
TRUST_LEVELS = {"untrusted", "declared", "governed"}
APPROVAL_STATUSES = {"pending", "approved", "rejected"}
NETWORK_REQUIREMENTS = {"none", "optional", "required"}
DATA_CLASSIFICATIONS = {"public", "internal", "controlled", "restricted"}
MANIFEST_FIELDS = {
    "schema_version",
    "name",
    "version",
    "description",
    "capabilities",
    "triggers",
    "applicable_when",
    "inputs",
    "produces",
    "requires",
    "may_recommend",
    "cost",
    "independence_required",
    "lifecycle",
    "execution",
    "requirements",
    "procedure",
}
REQUIREMENT_FIELDS = {
    "tools",
    "model_capabilities",
    "platforms",
    "network",
    "data_classifications",
}
SOURCE_FIELDS = {
    "id",
    "scope",
    "adapter",
    "path",
    "owner",
    "provenance",
    "trust",
    "capability_allowlist",
    "approval",
    "integrity",
}

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
VERSION_PATTERN = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def portable_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    return "/".join(parts)


def _tokens(value: str) -> set[str]:
    return set(TOKEN_PATTERN.findall(value.lower())) - STOP_WORDS


def _string_list(
    value: object,
    field: str,
    location: str,
    errors: list[str],
    *,
    required: bool = False,
) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{location}: {field} must be a list of non-empty strings")
        return []
    if required and not value:
        errors.append(f"{location}: {field} must not be empty")
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _read_json(path: Path, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        errors.append(f"{path}: cannot read JSON: {error}")
        return None


def _safe_relative_path(value: str) -> bool:
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
        return False
    candidate = portable_path(value)
    parts = candidate.split("/")
    return bool(candidate) and ".." not in parts


def _normalize_manifest(
    raw: object,
    *,
    manifest_path: Path,
    source: dict[str, Any],
    root: Path,
    errors: list[str],
    adapted: bool = False,
) -> dict[str, Any] | None:
    location = manifest_path.as_posix()
    if not isinstance(raw, dict):
        errors.append(f"{location}: skill manifest must be a JSON object")
        return None

    if not adapted:
        unknown_fields = sorted(set(raw) - MANIFEST_FIELDS)
        if unknown_fields:
            errors.append(f"{location}: unknown manifest fields: {unknown_fields}")

    if not adapted and raw.get("schema_version") != 1:
        errors.append(f"{location}: schema_version must be 1")

    name = raw.get("name")
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        errors.append(f"{location}: name must be lowercase kebab-case")
        return None

    version = raw.get("version", "0.0.0+adapted" if adapted else None)
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        errors.append(f"{location}: version must be semantic version text")

    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append(f"{location}: description must be non-empty text")
        description = ""

    capabilities = _string_list(
        raw.get("capabilities", [name] if adapted else None),
        "capabilities",
        location,
        errors,
        required=True,
    )
    for capability in capabilities:
        if not NAME_PATTERN.fullmatch(capability):
            errors.append(f"{location}: capability {capability!r} must be lowercase kebab-case")

    lifecycle = raw.get("lifecycle", "experimental" if adapted else None)
    if lifecycle not in LIFECYCLES:
        errors.append(f"{location}: lifecycle must be one of {sorted(LIFECYCLES)}")

    cost = raw.get("cost", {"context": "medium", "reasoning": "medium"} if adapted else None)
    if not isinstance(cost, dict):
        errors.append(f"{location}: cost must be an object")
        cost = {}
    context_cost = cost.get("context")
    reasoning_cost = cost.get("reasoning")
    if context_cost not in COST_LEVELS:
        errors.append(f"{location}: cost.context must be one of {sorted(COST_LEVELS)}")
    if reasoning_cost not in COST_LEVELS:
        errors.append(f"{location}: cost.reasoning must be one of {sorted(COST_LEVELS)}")

    execution = raw.get(
        "execution",
        {"mode": "agentic", "side_effects": "read-only"} if adapted else None,
    )
    if not isinstance(execution, dict):
        errors.append(f"{location}: execution must be an object")
        execution = {}
    mode = execution.get("mode")
    side_effects = execution.get("side_effects")
    if mode not in EXECUTION_MODES:
        errors.append(f"{location}: execution.mode must be one of {sorted(EXECUTION_MODES)}")
    if side_effects not in SIDE_EFFECTS:
        errors.append(f"{location}: execution.side_effects must be one of {sorted(SIDE_EFFECTS)}")

    independence = raw.get("independence_required", False if adapted else None)
    if not isinstance(independence, bool):
        errors.append(f"{location}: independence_required must be true or false")

    triggers = _string_list(raw.get("triggers"), "triggers", location, errors)
    applicable_when = _string_list(
        raw.get("applicable_when"), "applicable_when", location, errors
    )
    inputs = _string_list(raw.get("inputs"), "inputs", location, errors)
    produces = _string_list(raw.get("produces"), "produces", location, errors)
    requires = _string_list(raw.get("requires"), "requires", location, errors)
    may_recommend = _string_list(
        raw.get("may_recommend"), "may_recommend", location, errors
    )

    raw_requirements = raw.get("requirements")
    requirements_explicit = isinstance(raw_requirements, dict)
    if raw_requirements is None:
        raw_requirements = {}
    elif not isinstance(raw_requirements, dict):
        errors.append(f"{location}: requirements must be an object")
        raw_requirements = {}
    unknown_requirement_fields = sorted(set(raw_requirements) - REQUIREMENT_FIELDS)
    if unknown_requirement_fields:
        errors.append(
            f"{location}: unknown requirements fields: {unknown_requirement_fields}"
        )
    required_tools = _string_list(
        raw_requirements.get("tools"), "requirements.tools", location, errors
    )
    model_capabilities = _string_list(
        raw_requirements.get("model_capabilities"),
        "requirements.model_capabilities",
        location,
        errors,
    )
    platforms = _string_list(
        raw_requirements.get("platforms", ["any"]),
        "requirements.platforms",
        location,
        errors,
        required=True,
    )
    network = raw_requirements.get("network", "none")
    if network not in NETWORK_REQUIREMENTS:
        errors.append(
            f"{location}: requirements.network must be one of {sorted(NETWORK_REQUIREMENTS)}"
        )
    data_classifications = _string_list(
        raw_requirements.get("data_classifications", ["public", "internal"]),
        "requirements.data_classifications",
        location,
        errors,
        required=True,
    )
    if any(value not in DATA_CLASSIFICATIONS for value in data_classifications):
        errors.append(
            f"{location}: requirements.data_classifications must contain only "
            f"{sorted(DATA_CLASSIFICATIONS)}"
        )

    procedure = raw.get("procedure", "SKILL.md" if adapted else None)
    procedure_path: Path | None = None
    if not isinstance(procedure, str) or not procedure or not _safe_relative_path(procedure):
        errors.append(f"{location}: procedure must be a safe relative path")
    else:
        procedure_path = (manifest_path.parent / portable_path(procedure)).resolve()
        if not procedure_path.is_relative_to(manifest_path.parent.resolve()):
            errors.append(f"{location}: procedure must remain within the skill directory")
        if not procedure_path.is_file():
            errors.append(f"{location}: procedure does not exist: {procedure}")

    if errors and any(message.startswith(f"{location}:") for message in errors):
        return None

    try:
        manifest_relative = manifest_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        manifest_relative = str(manifest_path.resolve())
    assert procedure_path is not None
    try:
        procedure_relative = procedure_path.relative_to(root.resolve()).as_posix()
    except ValueError:
        procedure_relative = str(procedure_path)

    requirements = {
        "tools": required_tools,
        "model_capabilities": model_capabilities,
        "platforms": platforms,
        "network": network,
        "data_classifications": data_classifications,
    }
    contract_status = {
        "independence": "advisory" if adapted else "enforced",
        "side_effects": "advisory" if adapted else "enforced",
        "requirements": "enforced" if requirements_explicit else "advisory",
    }
    advertisement = {
        "schema_version": 1,
        "name": name,
        "version": version,
        "description": description.strip(),
        "capabilities": capabilities,
        "triggers": triggers,
        "applicable_when": applicable_when,
        "inputs": inputs,
        "produces": produces,
        "requires": requires,
        "may_recommend": may_recommend,
        "cost": {"context": context_cost, "reasoning": reasoning_cost},
        "independence_required": independence,
        "lifecycle": lifecycle,
        "execution": {"mode": mode, "side_effects": side_effects},
        "requirements": requirements,
        "contract_status": contract_status,
        "procedure": portable_path(str(procedure)),
        "procedure_sha256": _sha256(procedure_path),
    }
    skill_content_sha256 = _canonical_digest(advertisement)

    return {
        "registry_id": f"{source['id']}:{name}",
        **{key: value for key, value in advertisement.items() if key != "procedure"},
        "skill_content_sha256": skill_content_sha256,
        "source": source,
        "manifest_path": manifest_relative,
        "manifest_sha256": _sha256(manifest_path),
        "procedure_path": procedure_relative,
        "adapted": adapted,
    }


def _parse_frontmatter(path: Path, errors: list[str]) -> dict[str, object] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        errors.append(f"{path}: cannot read skill instructions: {error}")
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        errors.append(f"{path}: adapted SKILL.md requires YAML frontmatter")
        return None
    try:
        closing = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        errors.append(f"{path}: adapted SKILL.md has unterminated YAML frontmatter")
        return None

    result: dict[str, object] = {}
    active_list: str | None = None
    for line in lines[1:closing]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-") and active_list:
            value = stripped[1:].strip().strip("\"'")
            cast = result.setdefault(active_list, [])
            if isinstance(cast, list) and value:
                cast.append(value)
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        active_list = None
        if not value:
            result[key] = []
            active_list = key
        elif value.startswith("[") and value.endswith("]"):
            result[key] = [part.strip().strip("\"'") for part in value[1:-1].split(",") if part.strip()]
        else:
            result[key] = value.strip("\"'")
    result["procedure"] = path.name
    return result


def _load_sources(root: Path, errors: list[str], warnings: list[str]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = [
        {
            "id": "project",
            "scope": "project",
            "adapter": "aae-json",
            "path": str((root / SKILL_DIRECTORY).resolve()),
            "owner": "project",
            "provenance": "repository",
            "trust": "declared",
            "capability_allowlist": ["*"],
            "approval": {
                "status": "approved",
                "approved_by": "implicit-project-policy",
                "policy_version": "1",
            },
            "integrity": {
                "expected_content_sha256": None,
                "signature": None,
                "signature_status": "not-present",
            },
        }
    ]
    for config_path in (root / SKILL_SOURCES, root / LOCAL_SKILL_SOURCES):
        if not config_path.exists():
            continue
        value = _read_json(config_path, errors)
        if not isinstance(value, dict) or not isinstance(value.get("sources"), list):
            errors.append(f"{config_path}: sources must be a list")
            continue
        if value.get("schema_version") != 1:
            errors.append(f"{config_path}: schema_version must be 1")
        for index, candidate in enumerate(value["sources"]):
            location = f"{config_path}: sources[{index}]"
            if not isinstance(candidate, dict):
                errors.append(f"{location} must be an object")
                continue
            unknown_source_fields = sorted(set(candidate) - SOURCE_FIELDS)
            if unknown_source_fields:
                errors.append(f"{location} has unknown fields: {unknown_source_fields}")
                continue
            source_id = candidate.get("id")
            scope = candidate.get("scope")
            adapter = candidate.get("adapter")
            path_value = candidate.get("path")
            owner = candidate.get("owner", source_id)
            provenance = candidate.get("provenance", "configured-filesystem")
            trust = candidate.get("trust", "untrusted")
            approval = candidate.get("approval", {"status": "pending"})
            integrity = candidate.get("integrity", {})
            capability_allowlist = candidate.get("capability_allowlist", [])
            if not isinstance(source_id, str) or not NAME_PATTERN.fullmatch(source_id):
                errors.append(f"{location}.id must be lowercase kebab-case")
                continue
            if scope not in SCOPES:
                errors.append(f"{location}.scope must be one of {sorted(SCOPES)}")
                continue
            if adapter not in ADAPTERS:
                errors.append(f"{location}.adapter must be one of {sorted(ADAPTERS)}")
                continue
            if not isinstance(path_value, str) or not path_value:
                errors.append(f"{location}.path must be non-empty text")
                continue
            if not isinstance(owner, str) or not owner.strip():
                errors.append(f"{location}.owner must be non-empty text")
                continue
            if not isinstance(provenance, str) or not provenance.strip():
                errors.append(f"{location}.provenance must be non-empty text")
                continue
            if trust not in TRUST_LEVELS:
                errors.append(f"{location}.trust must be one of {sorted(TRUST_LEVELS)}")
                continue
            if not isinstance(approval, dict) or approval.get("status") not in APPROVAL_STATUSES:
                errors.append(
                    f"{location}.approval.status must be one of {sorted(APPROVAL_STATUSES)}"
                )
                continue
            if not isinstance(integrity, dict):
                errors.append(f"{location}.integrity must be an object")
                continue
            if not isinstance(capability_allowlist, list) or any(
                not isinstance(item, str) or not item.strip()
                for item in capability_allowlist
            ):
                errors.append(
                    f"{location}.capability_allowlist must be a list of non-empty strings"
                )
                continue
            if any(
                item != "*" and not NAME_PATTERN.fullmatch(item)
                for item in capability_allowlist
            ):
                errors.append(
                    f"{location}.capability_allowlist entries must be '*' or lowercase kebab-case"
                )
                continue
            expected_digest = integrity.get("expected_content_sha256")
            if expected_digest is not None and (
                not isinstance(expected_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)
            ):
                errors.append(
                    f"{location}.integrity.expected_content_sha256 must be a lowercase SHA-256 digest"
                )
                continue
            signature = integrity.get("signature")
            if signature is not None and not isinstance(signature, str):
                errors.append(f"{location}.integrity.signature must be text")
                continue
            source_path = Path(path_value).expanduser()
            if not source_path.is_absolute():
                source_path = root / source_path
            sources.append(
                {
                    "id": source_id,
                    "scope": str(scope),
                    "adapter": str(adapter),
                    "path": str(source_path.resolve()),
                    "owner": owner.strip(),
                    "provenance": provenance.strip(),
                    "trust": str(trust),
                    "capability_allowlist": sorted(
                        {str(item).strip() for item in capability_allowlist}
                    ),
                    "approval": {
                        "status": approval["status"],
                        "approved_by": approval.get("approved_by"),
                        "policy_version": approval.get("policy_version"),
                    },
                    "integrity": {
                        "expected_content_sha256": expected_digest,
                        "signature": signature,
                        "signature_status": "unsupported" if signature else "not-present",
                    },
                }
            )

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for source in sources:
        if source["id"] in seen:
            errors.append(f"Duplicate skill source id: {source['id']}")
            continue
        seen.add(source["id"])
        if not Path(source["path"]).exists():
            if source["id"] != "project":
                warnings.append(f"Configured skill source is unavailable: {source['id']}")
            continue
        unique.append(source)
    return unique


def _manifest_paths(source: dict[str, Any]) -> list[Path]:
    path = Path(source["path"])
    adapter = source["adapter"]
    if adapter == "aae-json":
        return sorted(path.rglob("skill.json")) if path.is_dir() else [path]
    if adapter == "skill-md":
        return sorted(path.rglob("SKILL.md")) if path.is_dir() else [path]
    if path.is_dir():
        return sorted(path.rglob("*.json"))
    return [path]


def build_skill_registry(root: Path) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    sources = _load_sources(root, errors, warnings)
    skills: list[dict[str, Any]] = []

    for source in sources:
        for path in _manifest_paths(source):
            if source["adapter"] == "skill-md":
                frontmatter = _parse_frontmatter(path, errors)
                if frontmatter is None:
                    continue
                skill = _normalize_manifest(
                    frontmatter,
                    manifest_path=path,
                    source=source,
                    root=root,
                    errors=errors,
                    adapted=True,
                )
                if skill:
                    skills.append(skill)
                continue

            value = _read_json(path, errors)
            candidates: Iterable[object]
            if (
                source["adapter"] == "registry-json"
                and isinstance(value, dict)
                and isinstance(value.get("skills"), list)
            ):
                if value.get("schema_version") != 1:
                    errors.append(f"{path}: registry schema_version must be 1")
                candidates = value["skills"]
            else:
                candidates = [value]
            for candidate in candidates:
                skill = _normalize_manifest(
                    candidate,
                    manifest_path=path,
                    source=source,
                    root=root,
                    errors=errors,
                )
                if skill:
                    skills.append(skill)

    skills.sort(key=lambda item: (item["registry_id"], item["version"]))
    seen_registry_ids: set[str] = set()
    for skill in skills:
        registry_id = skill["registry_id"]
        if registry_id in seen_registry_ids:
            errors.append(f"Duplicate skill registry id: {registry_id}")
        seen_registry_ids.add(registry_id)

    available_names = {skill["name"] for skill in skills if skill["lifecycle"] != "retired"}
    for skill in skills:
        for related in skill["may_recommend"]:
            if related not in available_names:
                warnings.append(
                    f"{skill['registry_id']} may recommend unavailable skill: {related}"
                )

    for source in sources:
        source_skills = [
            {
                "name": skill["name"],
                "version": skill["version"],
                "skill_content_sha256": skill["skill_content_sha256"],
            }
            for skill in skills
            if skill["source"]["id"] == source["id"]
        ]
        source_content_sha256 = _canonical_digest(sorted(source_skills, key=lambda item: (item["name"], item["version"])))
        source["source_content_sha256"] = source_content_sha256
        expected = source["integrity"].get("expected_content_sha256")
        source["integrity"]["content_status"] = (
            "verified" if expected == source_content_sha256 else "not-pinned"
        )
        if expected is not None and expected != source_content_sha256:
            source["integrity"]["content_status"] = "mismatch"
            errors.append(
                f"Skill source content digest mismatch for {source['id']}: "
                f"expected {expected}, observed {source_content_sha256}"
            )
        portable_source = {
            "id": source["id"],
            "scope": source["scope"],
            "adapter": source["adapter"],
            "owner": source["owner"],
            "provenance": source["provenance"],
            "trust": source["trust"],
            "capability_allowlist": source["capability_allowlist"],
            "approval": source["approval"],
            "integrity": {
                "expected_content_sha256": expected,
                "signature": source["integrity"].get("signature"),
                "signature_status": source["integrity"]["signature_status"],
                "content_status": source["integrity"]["content_status"],
            },
            "source_content_sha256": source_content_sha256,
        }
        source["source_identity_sha256"] = _canonical_digest(portable_source)

    portable_registry = {
        "schema_version": 1,
        "sources": sorted(
            [
                {
                    "id": source["id"],
                    "source_identity_sha256": source["source_identity_sha256"],
                }
                for source in sources
            ],
            key=lambda item: item["id"],
        ),
        "skills": sorted(
            [
                {
                    "registry_id": skill["registry_id"],
                    "skill_content_sha256": skill["skill_content_sha256"],
                    "source_identity_sha256": skill["source"]["source_identity_sha256"],
                }
                for skill in skills
            ],
            key=lambda item: item["registry_id"],
        ),
    }
    registry_content_sha256 = _canonical_digest(portable_registry)
    runtime_provenance = {
        "host": socket.gethostname(),
        "project_root": str(root.resolve()),
        "process_id": os.getpid(),
        "executable": sys.executable,
    }
    runtime_provenance["runtime_instance_id"] = _canonical_digest(runtime_provenance)

    registry: dict[str, Any] = {
        "schema_version": 1,
        "project_root": str(root.resolve()),
        "runtime_instance": runtime_provenance,
        "source_count": len(sources),
        "skill_count": len(skills),
        "capabilities": sorted(
            {capability for skill in skills for capability in skill["capabilities"]}
        ),
        "sources": sources,
        "skills": skills,
        "registry_content_sha256": registry_content_sha256,
    }
    registry["registry_sha256"] = registry_content_sha256
    return registry, errors, warnings


def watched_skill_paths(root: Path) -> set[Path]:
    errors: list[str] = []
    warnings: list[str] = []
    paths: set[Path] = set()
    for source in _load_sources(root, errors, warnings):
        paths.update(_manifest_paths(source))
    registry, _, _ = build_skill_registry(root)
    raw_skills = registry.get("skills", [])
    skills = raw_skills if isinstance(raw_skills, list) else []
    project_root = Path(str(registry["project_root"]))
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        procedure = Path(str(skill["procedure_path"]))
        paths.add(procedure if procedure.is_absolute() else project_root / procedure)
    return {path for path in paths if path.is_file()}


def _field_tokens(skill: dict[str, Any], fields: Iterable[str]) -> set[str]:
    values: list[str] = []
    for field in fields:
        value = skill.get(field)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
    return _tokens(" ".join(values))


def discover_skills(
    registry: dict[str, Any],
    *,
    task: str,
    capabilities: Iterable[str] = (),
    facts: Iterable[str] = (),
    architecture: Iterable[str] = (),
    environment: Iterable[str] = (),
    risks: Iterable[str] = (),
    evidence_gaps: Iterable[str] = (),
    candidate_limit: int = 18,
    limit: int = 4,
) -> dict[str, Any]:
    requested = {value.strip() for value in capabilities if value.strip()}
    fact_values = [value.strip() for value in facts if value.strip()]
    architecture_values = [value.strip() for value in architecture if value.strip()]
    environment_values = [value.strip() for value in environment if value.strip()]
    risk_values = [value.strip() for value in risks if value.strip()]
    gap_values = [value.strip() for value in evidence_gaps if value.strip()]
    query_tokens = _tokens(
        " ".join(
            [
                task,
                *requested,
                *fact_values,
                *architecture_values,
                *environment_values,
                *risk_values,
                *gap_values,
            ]
        )
    )
    raw_skills = registry.get("skills", [])
    skills = raw_skills if isinstance(raw_skills, list) else []
    eligible = [
        skill
        for skill in skills
        if isinstance(skill, dict) and skill.get("lifecycle") in ROUTABLE_LIFECYCLES
    ]

    metadata_ranked: list[tuple[int, dict[str, Any], dict[str, list[str]]]] = []
    for skill in eligible:
        provided = set(skill.get("capabilities", []))
        matched_capabilities = sorted(requested & provided)
        if requested and not matched_capabilities:
            continue
        trigger_tokens = _field_tokens(skill, ("triggers",))
        applicable_tokens = _field_tokens(skill, ("applicable_when",))
        matched_triggers = sorted(query_tokens & trigger_tokens)
        matched_facts = sorted(query_tokens & applicable_tokens)
        metadata_tokens = _field_tokens(
            skill,
            ("name", "capabilities", "triggers", "applicable_when"),
        )
        metadata_terms = sorted(query_tokens & metadata_tokens)
        score = (
            30 * len(matched_capabilities)
            + 5 * len(matched_triggers)
            + 4 * len(matched_facts)
            + len(metadata_terms)
        )
        if not requested and query_tokens and score == 0:
            continue
        matches = {
            "capabilities": matched_capabilities,
            "triggers": matched_triggers,
            "applicable_when": matched_facts,
            "metadata_terms": metadata_terms,
        }
        metadata_ranked.append((score, skill, matches))

    metadata_ranked.sort(key=lambda item: (-item[0], item[1]["registry_id"]))
    candidates = metadata_ranked[: max(candidate_limit, 0)]

    shortlisted: list[dict[str, Any]] = []
    for metadata_score, skill, matches in candidates:
        semantic_tokens = _field_tokens(
            skill,
            ("description", "inputs", "produces", "requires", "may_recommend"),
        )
        semantic_terms = sorted(query_tokens & semantic_tokens)
        total_score = metadata_score + 2 * len(semantic_terms)
        shortlisted.append(
            {
                "registry_id": skill["registry_id"],
                "name": skill["name"],
                "version": skill["version"],
                "description": skill["description"],
                "capabilities": skill["capabilities"],
                "produces": skill["produces"],
                "cost": skill["cost"],
                "independence_required": skill["independence_required"],
                "lifecycle": skill["lifecycle"],
                "execution": skill["execution"],
                "requirements": skill.get(
                    "requirements",
                    {
                        "tools": [],
                        "model_capabilities": [],
                        "platforms": ["any"],
                        "network": "none",
                        "data_classifications": ["public", "internal"],
                    },
                ),
                "contract_status": skill.get(
                    "contract_status",
                    {
                        "independence": "advisory",
                        "side_effects": "advisory",
                        "requirements": "advisory",
                    },
                ),
                "skill_content_sha256": skill.get("skill_content_sha256"),
                "source": {
                    key: value
                    for key, value in skill["source"].items()
                    if key != "path"
                },
                "score": total_score,
                "matched": {**matches, "semantic_terms": semantic_terms},
            }
        )
    shortlisted.sort(key=lambda item: (-int(item["score"]), str(item["registry_id"])))

    return {
        "schema_version": 1,
        "task": task,
        "requested_capabilities": sorted(requested),
        "facts": fact_values,
        "clues": {
            "architecture": architecture_values,
            "environment": environment_values,
            "risks": risk_values,
            "evidence_gaps": gap_values,
        },
        "registry_skill_count": len(skills),
        "eligible_skill_count": len(eligible),
        "metadata_candidate_count": len(metadata_ranked),
        "candidate_limit": candidate_limit,
        "shortlist_limit": limit,
        "shortlist": shortlisted[: max(limit, 0)],
    }


def load_skill_instructions(
    registry: dict[str, Any],
    identifier: str,
    *,
    authorization: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    skill, resolve_error = resolve_skill_metadata(registry, identifier)
    if resolve_error:
        return None, None, resolve_error
    assert skill is not None
    if authorization is None:
        return (
            skill,
            None,
            "Procedure loading requires an allowed InvocationPlan; use the governed invocation path",
        )
    supplied_plan_digest = authorization.get("invocation_plan_sha256")
    observed_plan_digest = _canonical_digest(
        {
            key: value
            for key, value in authorization.items()
            if key != "invocation_plan_sha256"
        }
    )
    if supplied_plan_digest != observed_plan_digest:
        return skill, None, "InvocationPlan content digest is invalid"
    if authorization.get("registry_content_sha256") != registry.get(
        "registry_content_sha256"
    ):
        return skill, None, "InvocationPlan registry identity does not match the registry"
    policy = authorization.get("policy")
    authorized_skill = authorization.get("skill")
    if not isinstance(policy, dict) or policy.get("decision") != "allowed":
        return skill, None, "InvocationPlan does not authorize procedure loading"
    if not isinstance(authorized_skill, dict):
        return skill, None, "InvocationPlan has no bound skill identity"
    bound_fields = (
        "registry_id",
        "skill_content_sha256",
        "procedure_sha256",
    )
    if any(authorized_skill.get(field) != skill.get(field) for field in bound_fields):
        return skill, None, "InvocationPlan skill identity does not match the registry"
    path = Path(str(skill["procedure_path"]))
    if not path.is_absolute():
        project_root = registry.get("project_root")
        if not isinstance(project_root, str):
            return skill, None, "Registry has no project root for relative procedure path"
        path = Path(project_root) / path
    try:
        instructions = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return skill, None, f"Cannot load skill procedure {path}: {error}"
    if _sha256(path) != skill["procedure_sha256"]:
        return skill, None, "Skill procedure changed after the registry was built; rebuild the registry"
    return skill, instructions, None


def resolve_skill_metadata(
    registry: dict[str, Any], identifier: str
) -> tuple[dict[str, Any] | None, str | None]:
    raw_skills = registry.get("skills", [])
    skills = raw_skills if isinstance(raw_skills, list) else []
    exact = [
        skill
        for skill in skills
        if isinstance(skill, dict) and skill.get("registry_id") == identifier
    ]
    matches = exact or [
        skill for skill in skills if isinstance(skill, dict) and skill.get("name") == identifier
    ]
    if not matches:
        return None, f"Skill not found: {identifier}"
    if len(matches) > 1:
        choices = ", ".join(str(skill["registry_id"]) for skill in matches)
        return None, f"Skill name is ambiguous; use a registry id: {choices}"
    return matches[0], None


def record_skill_event(
    root: Path,
    registry: dict[str, Any],
    identifier: str,
    event: str,
    *,
    context_tokens: int | None = None,
    execution_cost: float | None = None,
    evidence: str | None = None,
    reason: str | None = None,
) -> str | None:
    if event not in EVENT_TYPES:
        return f"Skill event must be one of {sorted(EVENT_TYPES)}"
    if context_tokens is not None and context_tokens < 0:
        return "Context tokens must be non-negative"
    if execution_cost is not None and execution_cost < 0:
        return "Execution cost must be non-negative"
    skill, error = resolve_skill_metadata(registry, identifier)
    if error:
        return error
    assert skill is not None
    record = {
        "schema_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "registry_id": skill["registry_id"],
        "skill_version": skill["version"],
        "event": event,
        "context_tokens": context_tokens,
        "execution_cost": execution_cost,
        "evidence": evidence,
        "reason": reason,
    }
    directory = root / SKILL_EVENT_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    event_id = uuid.uuid4().hex
    path = directory / f"{event_id}.json"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=directory, prefix=f".{event_id}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except OSError as error:
        return f"Cannot record skill telemetry: {error}"
    finally:
        temporary.unlink(missing_ok=True)
    return None


def summarize_skill_events(
    root: Path, registry: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    summaries: dict[str, dict[str, Any]] = {}
    raw_skills = registry.get("skills", [])
    skills = raw_skills if isinstance(raw_skills, list) else []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        key = f"{skill['registry_id']}@{skill['version']}"
        summaries[key] = {
            "registry_id": skill["registry_id"],
            "version": skill["version"],
            "current": True,
            "considered": 0,
            "selected": 0,
            "succeeded": 0,
            "failed": 0,
            "superseded": 0,
            "context_tokens": 0,
            "execution_cost": 0.0,
            "selection_rate": None,
            "failure_rate": None,
        }

    warnings: list[str] = []
    serialized_events: list[tuple[str, str]] = []
    legacy_path = root / SKILL_EVENTS
    if legacy_path.exists():
        try:
            serialized_events.extend(
                (f"{SKILL_EVENTS}:{line_number}", line)
                for line_number, line in enumerate(
                    legacy_path.read_text(encoding="utf-8").splitlines(), 1
                )
            )
        except (OSError, UnicodeError) as error:
            warnings.append(f"Cannot read legacy skill telemetry: {error}")
    directory = root / SKILL_EVENT_DIRECTORY
    if directory.exists():
        for event_path in sorted(directory.glob("*.json")):
            try:
                serialized_events.append(
                    (event_path.relative_to(root).as_posix(), event_path.read_text(encoding="utf-8"))
                )
            except (OSError, UnicodeError) as error:
                warnings.append(f"Cannot read skill telemetry {event_path}: {error}")
    for location, line in serialized_events:
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            warnings.append(f"{location}: invalid JSON: {error}")
            continue
        if not isinstance(event, dict):
            warnings.append(f"{location}: event must be an object")
            continue
        registry_id = event.get("registry_id")
        skill_version = event.get("skill_version")
        event_name = event.get("event")
        known_registry_ids = {
            str(skill.get("registry_id")) for skill in skills if isinstance(skill, dict)
        }
        if registry_id not in known_registry_ids:
            warnings.append(f"{location}: unknown registry id {registry_id}")
            continue
        if not isinstance(skill_version, str) or not VERSION_PATTERN.fullmatch(skill_version):
            warnings.append(
                f"{location}: invalid skill version {skill_version!r}"
            )
            continue
        if event_name not in EVENT_TYPES:
            warnings.append(f"{location}: unknown event {event_name}")
            continue
        key = f"{registry_id}@{skill_version}"
        if key not in summaries:
            summaries[key] = {
                "registry_id": registry_id,
                "version": skill_version,
                "current": False,
                "considered": 0,
                "selected": 0,
                "succeeded": 0,
                "failed": 0,
                "superseded": 0,
                "context_tokens": 0,
                "execution_cost": 0.0,
                "selection_rate": None,
                "failure_rate": None,
            }
        summary = summaries[key]
        summary[str(event_name)] = int(summary[str(event_name)]) + 1
        context_tokens = event.get("context_tokens")
        if isinstance(context_tokens, int) and not isinstance(context_tokens, bool) and context_tokens >= 0:
            summary["context_tokens"] = int(summary["context_tokens"]) + context_tokens
        execution_cost = event.get("execution_cost")
        if (
            isinstance(execution_cost, (int, float))
            and not isinstance(execution_cost, bool)
            and execution_cost >= 0
        ):
            summary["execution_cost"] = float(summary["execution_cost"]) + float(execution_cost)
    for summary in summaries.values():
        considered = int(summary["considered"])
        completed = int(summary["succeeded"]) + int(summary["failed"])
        summary["selection_rate"] = (
            int(summary["selected"]) / considered if considered else None
        )
        summary["failure_rate"] = (
            int(summary["failed"]) / completed if completed else None
        )
    return summaries, warnings
