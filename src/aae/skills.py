from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any, Iterable
import uuid


SKILL_DIRECTORY = Path(".aae/skills")
SKILL_SOURCES = Path(".aae/skill-sources.json")
LOCAL_SKILL_SOURCES = Path(".aae/skill-sources.local.json")
SKILL_EVENT_DIRECTORY = Path(".aae/runtime/skill-events")

ADAPTERS = {"aae-json", "skill-md", "registry-json"}
SCOPES = {"enterprise", "project", "local"}
EVENT_TYPES = {"considered", "selected", "succeeded", "failed", "blocked", "superseded"}
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "before", "by", "for",
    "from", "in", "into", "is", "it", "of", "on", "or", "the", "to",
    "when", "with",
}


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def portable_path(value: str) -> str:
    return str(PurePosixPath(value.replace("\\", "/")))


def _tokens(value: str) -> set[str]:
    return {token for token in TOKEN_PATTERN.findall(value.lower()) if token not in STOP_WORDS}


def _string_list(value: object, field: str, location: str, errors: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{location}: {field} must be a list of non-empty strings")
        return []
    return [item.strip() for item in value]


def _read_json(path: Path, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        errors.append(f"{path}: cannot read JSON: {error}")
        return None


def _safe_relative_path(value: str) -> bool:
    path = PurePosixPath(portable_path(value))
    return not path.is_absolute() and ".." not in path.parts


def _normalize_manifest(
    raw: object,
    *,
    location: str,
    source: dict[str, Any],
    procedure_path: Path,
    adapted: bool,
    errors: list[str],
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        errors.append(f"{location}: skill manifest must be a JSON object")
        return None
    allowed = {
        "schema_version", "name", "version", "description", "when_to_use",
        "capabilities", "procedure", "requires_tools", "destructive",
        "independence_required",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown and not adapted:
        errors.append(f"{location}: unknown fields: {unknown}")
    name = raw.get("name")
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        errors.append(f"{location}: name must be lowercase kebab-case")
        return None
    version = raw.get("version", "0.1.0")
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        errors.append(f"{location}: version must be semantic version text")
    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append(f"{location}: description must be non-empty text")
        description = ""
    when_to_use = _string_list(raw.get("when_to_use"), "when_to_use", location, errors)
    capabilities = _string_list(raw.get("capabilities"), "capabilities", location, errors) or [name]
    requires_tools = _string_list(raw.get("requires_tools"), "requires_tools", location, errors)
    procedure = raw.get("procedure", procedure_path.name)
    if not isinstance(procedure, str) or not procedure.strip() or not _safe_relative_path(procedure):
        errors.append(f"{location}: procedure must be a safe relative path")
        procedure = procedure_path.name
    if not isinstance(raw.get("destructive", False), bool):
        errors.append(f"{location}: destructive must be true or false")
    if not isinstance(raw.get("independence_required", False), bool):
        errors.append(f"{location}: independence_required must be true or false")
    if any(error.startswith(f"{location}:") for error in errors):
        return None

    resolved_procedure = procedure_path.parent / portable_path(procedure)
    if not resolved_procedure.is_file():
        errors.append(f"{location}: procedure file does not exist: {portable_path(procedure)}")
        return None
    try:
        relative_procedure = resolved_procedure.relative_to(source["root_path"])
    except ValueError:
        errors.append(f"{location}: procedure escapes the configured source")
        return None
    advertisement = {
        "schema_version": 1,
        "name": name,
        "version": version,
        "description": description.strip(),
        "when_to_use": when_to_use,
        "capabilities": capabilities,
        "requires_tools": requires_tools,
        "destructive": bool(raw.get("destructive", False)),
        "independence_required": bool(raw.get("independence_required", False)),
        "procedure": portable_path(procedure),
    }
    procedure_sha256 = _sha256(resolved_procedure)
    manifest_sha256 = _canonical_digest(advertisement)
    portable_identity = {
        "source": {"id": source["id"], "scope": source["scope"], "adapter": source["adapter"]},
        "advertisement": advertisement,
        "procedure_sha256": procedure_sha256,
    }
    return {
        **advertisement,
        "registry_id": f"{source['id']}:{name}",
        "source": {
            "id": source["id"], "scope": source["scope"],
            "adapter": source["adapter"], "path": source["portable_path"],
        },
        "adapted": adapted,
        "manifest_path": location,
        "procedure_path": str(resolved_procedure),
        "procedure_portable_path": portable_path(str(relative_procedure)),
        "procedure_sha256": procedure_sha256,
        "manifest_sha256": manifest_sha256,
        "skill_content_sha256": _canonical_digest(portable_identity),
    }


def _parse_frontmatter(path: Path, errors: list[str]) -> dict[str, object] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        errors.append(f"{path}: cannot read skill instructions: {error}")
        return None
    if not lines or lines[0].strip() != "---":
        return {"name": path.parent.name, "description": f"Instructions from {path.parent.name}", "when_to_use": []}
    metadata: dict[str, object] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            errors.append(f"{path}: unsupported frontmatter line: {line}")
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            metadata[key.strip()] = [item.strip() for item in value[1:-1].split(",") if item.strip()]
        else:
            metadata[key.strip()] = value.strip("\"'")
    return metadata


def _load_sources(root: Path, errors: list[str], warnings: list[str]) -> list[dict[str, Any]]:
    configured: list[dict[str, Any]] = []
    for config_path, local_only in ((root / SKILL_SOURCES, False), (root / LOCAL_SKILL_SOURCES, True)):
        if not config_path.exists():
            continue
        raw = _read_json(config_path, errors)
        if not isinstance(raw, dict):
            continue
        unknown = sorted(set(raw) - {"schema_version", "sources"})
        if unknown:
            errors.append(f"{config_path}: unknown fields: {unknown}")
        if raw.get("schema_version") != 1 or not isinstance(raw.get("sources"), list):
            errors.append(f"{config_path}: schema_version must be 1 and sources must be a list")
            continue
        for index, source in enumerate(raw["sources"]):
            location = f"{config_path}: sources[{index}]"
            if not isinstance(source, dict):
                errors.append(f"{location} must be an object")
                continue
            unknown_source = sorted(set(source) - {"id", "scope", "adapter", "path"})
            if unknown_source:
                errors.append(f"{location}: unknown fields: {unknown_source}")
            source_id, scope, adapter, path_value = (
                source.get("id"), source.get("scope"), source.get("adapter"), source.get("path")
            )
            if not isinstance(source_id, str) or not NAME_PATTERN.fullmatch(source_id):
                errors.append(f"{location}: id must be lowercase kebab-case")
                continue
            if scope not in SCOPES or (local_only and scope != "local"):
                errors.append(f"{location}: scope must be enterprise, project, or local")
                continue
            if adapter not in ADAPTERS:
                errors.append(f"{location}: adapter must be one of {sorted(ADAPTERS)}")
                continue
            if not isinstance(path_value, str) or not path_value.strip():
                errors.append(f"{location}: path must be non-empty text")
                continue
            source_path = Path(path_value)
            if not source_path.is_absolute():
                source_path = root / source_path
            if not source_path.exists():
                warnings.append(f"{location}: source path does not exist: {path_value}")
                continue
            configured.append({
                "id": source_id, "scope": scope, "adapter": adapter,
                "root_path": (
                    source_path.resolve().parent
                    if adapter == "registry-json" and source_path.is_file()
                    else source_path.resolve()
                ),
                "manifest_path": source_path.resolve(),
                "portable_path": portable_path(path_value),
            })
    implicit = root / SKILL_DIRECTORY
    if implicit.exists() and not any(source["root_path"] == implicit.resolve() for source in configured):
        configured.insert(0, {
            "id": "project", "scope": "project", "adapter": "aae-json",
            "root_path": implicit.resolve(), "manifest_path": implicit.resolve(),
            "portable_path": str(SKILL_DIRECTORY),
        })
    return configured


def _manifest_paths(source: dict[str, Any]) -> list[Path]:
    root_path: Path = source["manifest_path"]
    if source["adapter"] == "aae-json":
        return sorted(root_path.rglob("skill.json"))
    if source["adapter"] == "skill-md":
        return sorted(root_path.rglob("SKILL.md"))
    return [root_path] if root_path.is_file() else sorted(root_path.rglob("*.json"))


def build_skill_registry(root: Path) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    sources = _load_sources(root, errors, warnings)
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        for path in _manifest_paths(source):
            if source["adapter"] == "registry-json":
                raw_registry = _read_json(path, errors)
                raw_skills = raw_registry.get("skills", []) if isinstance(raw_registry, dict) else []
                if not isinstance(raw_skills, list):
                    errors.append(f"{path}: skills must be a list")
                    continue
                entries = [
                    (item, path, path.parent / "SKILL.md")
                    for item in raw_skills
                    if isinstance(item, dict)
                ]
            elif source["adapter"] == "skill-md":
                metadata = _parse_frontmatter(path, errors)
                if metadata is None:
                    continue
                metadata.setdefault("procedure", path.name)
                entries = [(metadata, path, path)]
            else:
                entries = [(_read_json(path, errors), path, path.parent / "SKILL.md")]
            for raw, manifest_path, procedure_path in entries:
                before = len(errors)
                skill = _normalize_manifest(
                    raw, location=str(manifest_path), source=source,
                    procedure_path=procedure_path, adapted=source["adapter"] != "aae-json",
                    errors=errors,
                )
                if skill is None or len(errors) > before:
                    continue
                if skill["registry_id"] in seen:
                    errors.append(
                        f"Duplicate skill identity {skill['registry_id']}; local skills cannot silently replace project or enterprise skills"
                    )
                    continue
                seen.add(skill["registry_id"])
                skills.append(skill)
    skills.sort(key=lambda item: item["registry_id"])
    portable_skills = [
        {key: skill[key] for key in (
            "registry_id", "name", "version", "description", "when_to_use",
            "capabilities", "requires_tools", "destructive", "independence_required",
            "procedure_portable_path", "procedure_sha256", "skill_content_sha256",
            "source", "adapted",
        )}
        for skill in skills
    ]
    portable_registry = {"schema_version": 1, "skills": portable_skills}
    registry_digest = _canonical_digest(portable_registry)
    capability_names = sorted(
        {capability for skill in skills for capability in skill.get("capabilities", [])}
    )
    return {
        **portable_registry,
        "skill_count": len(skills),
        "source_count": len(sources),
        "capabilities": capability_names,
        "skills": skills,
        "registry_content_sha256": registry_digest,
        "registry_sha256": registry_digest,
        "runtime_instance": {
            "runtime_instance_id": str(uuid.uuid5(uuid.NAMESPACE_URL, str(root.resolve()))),
            "project_root": str(root.resolve()),
        },
        "sources": [
            {
                key: value
                for key, value in source.items()
                if key not in {"root_path", "manifest_path"}
            }
            for source in sources
        ],
    }, errors, warnings


def watched_skill_paths(root: Path) -> set[Path]:
    paths = {
        path for path in (root / SKILL_SOURCES, root / LOCAL_SKILL_SOURCES)
        if path.exists()
    }
    errors: list[str] = []
    warnings: list[str] = []
    for source in _load_sources(root, errors, warnings):
        paths.update(_manifest_paths(source))
        if source["root_path"].is_dir():
            paths.update(source["root_path"].rglob("SKILL.md"))
    return paths


def discover_skills(
    registry: dict[str, Any], *, task: str, capabilities: Iterable[str] = (),
    facts: Iterable[str] = (), architecture: Iterable[str] = (), environment: Iterable[str] = (),
    risks: Iterable[str] = (), evidence_gaps: Iterable[str] = (), candidate_limit: int = 18,
    limit: int = 4,
) -> dict[str, Any]:
    requested = {value.strip() for value in capabilities if value.strip()}
    clue_values = {
        "facts": [value.strip() for value in facts if value.strip()],
        "architecture": [value.strip() for value in architecture if value.strip()],
        "environment": [value.strip() for value in environment if value.strip()],
        "risks": [value.strip() for value in risks if value.strip()],
        "evidence_gaps": [value.strip() for value in evidence_gaps if value.strip()],
    }
    query_tokens = _tokens(" ".join([task, *[item for values in clue_values.values() for item in values]]))
    ranked: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for skill in registry.get("skills", []):
        capability_matches = sorted(requested & set(skill.get("capabilities", [])))
        advertisement_tokens = _tokens(" ".join([
            skill.get("name", ""), skill.get("description", ""),
            *skill.get("when_to_use", []), *skill.get("capabilities", []),
        ]))
        term_matches = sorted(query_tokens & advertisement_tokens)
        if requested and not capability_matches and not term_matches:
            continue
        score = len(capability_matches) * 30 + len(term_matches) * 5
        if score == 0:
            continue
        ranked.append((score, skill, {
            "registry_id": skill["registry_id"], "name": skill["name"],
            "version": skill["version"], "description": skill["description"],
            "when_to_use": skill.get("when_to_use", []),
            "capabilities": skill.get("capabilities", []),
            "requires_tools": skill.get("requires_tools", []),
            "destructive": skill.get("destructive", False),
            "independence_required": skill.get("independence_required", False),
            "skill_content_sha256": skill["skill_content_sha256"],
            "score": score, "matched_capabilities": capability_matches,
            "matched_terms": term_matches,
        }))
    ranked.sort(key=lambda item: (-item[0], item[1]["registry_id"]))
    bounded = ranked[: max(candidate_limit, 0)]
    return {
        "task": task, "requested_capabilities": sorted(requested), "clues": clue_values,
        "registry_skill_count": len(registry.get("skills", [])),
        "eligible_skill_count": len(ranked), "metadata_candidate_count": len(ranked),
        "shortlist": [item[2] for item in bounded[: max(limit, 0)]],
    }


def resolve_skill_metadata(registry: dict[str, Any], selector: str) -> tuple[dict[str, Any] | None, str | None]:
    matches = [skill for skill in registry.get("skills", []) if skill.get("registry_id") == selector or skill.get("name") == selector]
    if not matches:
        return None, f"Skill not found: {selector}"
    if len(matches) > 1:
        return None, f"Skill name is ambiguous; use a registry id: {selector}"
    return matches[0], None


def load_skill_instructions(
    registry: dict[str, Any], selector: str, authorization: dict[str, Any] | None = None
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    skill, error = resolve_skill_metadata(registry, selector)
    if error or skill is None:
        return skill, None, error
    if authorization is None or authorization.get("decision") != "allowed":
        return skill, None, "Procedure loading requires an allowed invocation decision"
    if authorization.get("registry_content_sha256") != registry.get("registry_content_sha256"):
        return skill, None, "Invocation decision registry digest does not match"
    if authorization.get("skill_content_sha256") != skill.get("skill_content_sha256"):
        return skill, None, "Invocation decision skill digest does not match"
    path = Path(skill["procedure_path"])
    if not path.is_file() or _sha256(path) != skill["procedure_sha256"]:
        return skill, None, "Skill procedure changed after discovery; rebuild the registry"
    try:
        return skill, path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeError) as read_error:
        return skill, None, f"Cannot read skill procedure: {read_error}"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def record_skill_event(
    root: Path, registry: dict[str, Any], selector: str, event: str, *,
    context_tokens: int | None = None, execution_cost: float | None = None,
    evidence: str | None = None, reason: str | None = None,
) -> str | None:
    if event not in EVENT_TYPES:
        return f"Event must be one of {sorted(EVENT_TYPES)}"
    if context_tokens is not None and context_tokens < 0:
        return "Context tokens must be non-negative"
    if execution_cost is not None and execution_cost < 0:
        return "Execution cost must be non-negative"
    skill, error = resolve_skill_metadata(registry, selector)
    if error or skill is None:
        return error
    event_id = uuid.uuid4().hex
    _write_json(root / SKILL_EVENT_DIRECTORY / f"{event_id}.json", {
        "schema_version": 1, "event_id": event_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "registry_id": skill["registry_id"], "version": skill["version"],
        "skill_content_sha256": skill["skill_content_sha256"], "event": event,
        "context_tokens": context_tokens, "execution_cost": execution_cost,
        "evidence": evidence, "reason": reason,
    })
    return None


def summarize_skill_events(root: Path, registry: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    del registry
    warnings: list[str] = []
    summaries: dict[str, Any] = {}
    directory = root / SKILL_EVENT_DIRECTORY
    if not directory.exists():
        return summaries, warnings
    for path in sorted(directory.glob("*.json")):
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            warnings.append(f"{path}: cannot read skill event: {error}")
            continue
        key = f"{event.get('registry_id')}@{event.get('version')}"
        summary = summaries.setdefault(key, {
            "registry_id": event.get("registry_id"), "version": event.get("version"),
            "considered": 0, "selected": 0, "succeeded": 0, "failed": 0, "blocked": 0,
            "superseded": 0, "context_tokens": 0, "execution_cost": 0.0,
        })
        if event.get("event") in EVENT_TYPES:
            summary[event["event"]] += 1
        summary["context_tokens"] += event.get("context_tokens") or 0
        summary["execution_cost"] += event.get("execution_cost") or 0.0
    for summary in summaries.values():
        summary["selection_rate"] = (
            summary["selected"] / summary["considered"] if summary["considered"] else 0.0
        )
        completed = summary["succeeded"] + summary["failed"]
        summary["failure_rate"] = summary["failed"] / completed if completed else 0.0
    return summaries, warnings
