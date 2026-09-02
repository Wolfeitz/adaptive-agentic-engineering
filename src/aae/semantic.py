from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping, Protocol, Sequence, cast

from . import __version__
from .skills import build_skill_registry, discover_skills, resolve_skill_metadata


SEMANTIC_SCHEMA_VERSION = 1
STATEMENT_KINDS = {
    "fact",
    "constraint",
    "decision",
    "preference",
    "proposal",
    "unknown",
}
LEVELS = {"low": 0, "medium": 1, "high": 2, "critical": 3}
TRACKER_PROVIDERS = {"azure", "github", "jira"}
ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_.")


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def canonical_digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _valid_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value[0].isalnum()
        and all(character in ID_CHARS for character in value)
    )


def _safe_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def _objects(
    document: Mapping[str, Any], field: str, errors: list[str]
) -> list[dict[str, Any]]:
    value = document.get(field, [])
    if not isinstance(value, list):
        errors.append(f"{field} must be a list")
        return []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"{field}[{index}] must be an object")
        else:
            result.append(cast(dict[str, Any], item))
    return result


def _string_list(value: object, location: str, errors: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        errors.append(f"{location} must be a list of non-empty strings")
        return []
    return cast(list[str], value)


def _index_by_id(
    items: Sequence[dict[str, Any]], field: str, errors: list[str]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        identifier = item.get("id")
        if not _valid_id(identifier):
            errors.append(f"{field}[{index}].id must be a portable identifier")
            continue
        key = cast(str, identifier)
        if key in result:
            errors.append(f"duplicate {field} id: {key}")
            continue
        result[key] = item
    return result


def validate_semantic_document(
    value: object,
    registry: Mapping[str, Any] | None = None,
    *,
    root: Path | None = None,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(value, dict):
        return ["semantic document must be an object"], warnings
    document = cast(dict[str, Any], value)
    if document.get("schema_version") != SEMANTIC_SCHEMA_VERSION:
        errors.append(f"schema_version must be {SEMANTIC_SCHEMA_VERSION}")

    project = document.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("name"), str):
        errors.append("project.name must be non-empty text")
    elif not project["name"].strip():
        errors.append("project.name must be non-empty text")

    statements = _objects(document, "statements", errors)
    capabilities = _objects(document, "capabilities", errors)
    tasks = _objects(document, "tasks", errors)
    conflicts = _objects(document, "conflicts", errors)
    questions = _objects(document, "questions", errors)
    artifacts = _objects(document, "artifacts", errors)
    statement_index = _index_by_id(statements, "statements", errors)
    capability_index = _index_by_id(capabilities, "capabilities", errors)
    task_index = _index_by_id(tasks, "tasks", errors)
    _index_by_id(conflicts, "conflicts", errors)
    _index_by_id(questions, "questions", errors)
    _index_by_id(artifacts, "artifacts", errors)

    for index, statement in enumerate(statements):
        if statement.get("kind") not in STATEMENT_KINDS:
            errors.append(
                f"statements[{index}].kind must be one of {sorted(STATEMENT_KINDS)}"
            )
        if not isinstance(statement.get("text"), str) or not statement["text"].strip():
            errors.append(f"statements[{index}].text must be non-empty text")
        sources = statement.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"statements[{index}].sources must not be empty")
            continue
        for source_index, source in enumerate(sources):
            location = f"statements[{index}].sources[{source_index}]"
            if not isinstance(source, dict):
                errors.append(f"{location} must be an object")
                continue
            if not _safe_relative_path(source.get("path")):
                errors.append(f"{location}.path must be a safe relative path")
            digest = source.get("sha256")
            if not isinstance(digest, str) or len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                errors.append(f"{location}.sha256 must be a lowercase SHA-256")
                continue
            if root is not None and _safe_relative_path(source.get("path")):
                project_root = root.resolve()
                source_path = (project_root / cast(str, source["path"])).resolve()
                if not source_path.is_relative_to(project_root):
                    errors.append(f"{location}.path resolves outside the project root")
                elif not source_path.is_file():
                    errors.append(f"{location}.path does not exist: {source['path']}")
                else:
                    observed = hashlib.sha256(source_path.read_bytes()).hexdigest()
                    if observed != digest:
                        errors.append(
                            f"{location}.sha256 does not match {source['path']}: "
                            f"expected {digest}, observed {observed}"
                        )

    for index, capability in enumerate(capabilities):
        if not isinstance(capability.get("description"), str) or not capability[
            "description"
        ].strip():
            errors.append(f"capabilities[{index}].description must be non-empty text")
        _string_list(capability.get("inputs"), f"capabilities[{index}].inputs", errors)
        _string_list(capability.get("outputs"), f"capabilities[{index}].outputs", errors)
        evidence = _string_list(
            capability.get("evidence"), f"capabilities[{index}].evidence", errors
        )
        if not evidence:
            warnings.append(f"capabilities[{index}] has no declared evidence")
        for statement_id in _string_list(
            capability.get("statement_ids"),
            f"capabilities[{index}].statement_ids",
            errors,
        ):
            if statement_id not in statement_index:
                errors.append(
                    f"capabilities[{index}] references unknown statement: {statement_id}"
                )

    known_registry_ids = {
        str(skill.get("registry_id"))
        for skill in (registry or {}).get("skills", [])
        if isinstance(skill, dict)
    }
    for index, task in enumerate(tasks):
        if not isinstance(task.get("title"), str) or not task["title"].strip():
            errors.append(f"tasks[{index}].title must be non-empty text")
        for field in ("consequence", "evidence_gap"):
            if task.get(field, "low") not in LEVELS:
                errors.append(f"tasks[{index}].{field} must be one of {sorted(LEVELS)}")
        task_capabilities = _string_list(
            task.get("capabilities"), f"tasks[{index}].capabilities", errors
        )
        if not task_capabilities:
            errors.append(f"tasks[{index}].capabilities must not be empty")
        for capability_id in task_capabilities:
            if capability_id not in capability_index:
                errors.append(
                    f"tasks[{index}] references unknown capability: {capability_id}"
                )
        for dependency in _string_list(
            task.get("depends_on"), f"tasks[{index}].depends_on", errors
        ):
            if dependency not in task_index:
                errors.append(f"tasks[{index}] references unknown task: {dependency}")
            if dependency == task.get("id"):
                errors.append(f"tasks[{index}] cannot depend on itself")
        acceptance = _string_list(
            task.get("acceptance"), f"tasks[{index}].acceptance", errors
        )
        if not acceptance:
            errors.append(f"tasks[{index}].acceptance must not be empty")
        for skill_id in _string_list(
            task.get("selected_skills"), f"tasks[{index}].selected_skills", errors
        ):
            if registry is not None and skill_id not in known_registry_ids:
                errors.append(f"tasks[{index}] selects unknown skill: {skill_id}")

    for index, conflict in enumerate(conflicts):
        statement_ids = _string_list(
            conflict.get("statement_ids"), f"conflicts[{index}].statement_ids", errors
        )
        if len(statement_ids) < 2:
            errors.append(f"conflicts[{index}].statement_ids must contain at least two ids")
        for statement_id in statement_ids:
            if statement_id not in statement_index:
                errors.append(f"conflicts[{index}] references unknown statement: {statement_id}")
        if not isinstance(conflict.get("material", False), bool):
            errors.append(f"conflicts[{index}].material must be true or false")
        if conflict.get("material") and not conflict.get("resolution"):
            warnings.append(f"material conflict remains unresolved: {conflict.get('id')}")

    for index, question in enumerate(questions):
        if not isinstance(question.get("question"), str) or not question[
            "question"
        ].strip():
            errors.append(f"questions[{index}].question must be non-empty text")
        if not isinstance(question.get("material", False), bool):
            errors.append(f"questions[{index}].material must be true or false")
        if question.get("material") and not question.get("answer"):
            warnings.append(f"material question remains unanswered: {question.get('id')}")

    for index, artifact in enumerate(artifacts):
        if not _safe_relative_path(artifact.get("path")):
            errors.append(f"artifacts[{index}].path must be a safe relative path")
        for task_id in _string_list(
            artifact.get("task_ids"), f"artifacts[{index}].task_ids", errors
        ):
            if task_id not in task_index:
                errors.append(f"artifacts[{index}] references unknown task: {task_id}")
    return errors, warnings


def unresolved_material_items(document: Mapping[str, Any]) -> list[str]:
    unresolved: list[str] = []
    for conflict in document.get("conflicts", []):
        if isinstance(conflict, dict) and conflict.get("material") and not conflict.get(
            "resolution"
        ):
            unresolved.append(f"conflict:{conflict.get('id')}")
    for question in document.get("questions", []):
        if isinstance(question, dict) and question.get("material") and not question.get(
            "answer"
        ):
            unresolved.append(f"question:{question.get('id')}")
    return sorted(unresolved)


def build_impact_graph(document: Mapping[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, str]] = []
    edges: set[tuple[str, str, str]] = set()
    for field, kind in (
        ("statements", "statement"),
        ("capabilities", "capability"),
        ("tasks", "task"),
        ("artifacts", "artifact"),
    ):
        for item in document.get(field, []):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                nodes.append({"id": f"{kind}:{item['id']}", "kind": kind})
    for capability in document.get("capabilities", []):
        if not isinstance(capability, dict):
            continue
        for statement_id in capability.get("statement_ids", []):
            edges.add(
                (
                    f"statement:{statement_id}",
                    f"capability:{capability['id']}",
                    "motivates",
                )
            )
    for task in document.get("tasks", []):
        if not isinstance(task, dict):
            continue
        for capability_id in task.get("capabilities", []):
            edges.add(
                (f"capability:{capability_id}", f"task:{task['id']}", "required-by")
            )
        for dependency in task.get("depends_on", []):
            edges.add((f"task:{dependency}", f"task:{task['id']}", "precedes"))
        for skill_id in task.get("selected_skills", []):
            nodes.append({"id": f"skill:{skill_id}", "kind": "skill"})
            edges.add((f"skill:{skill_id}", f"task:{task['id']}", "selected-for"))
    for artifact in document.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        for task_id in artifact.get("task_ids", []):
            edges.add((f"task:{task_id}", f"artifact:{artifact['id']}", "produces"))
    unique_nodes = {(node["id"], node["kind"]) for node in nodes}
    graph: dict[str, Any] = {
        "schema_version": 1,
        "nodes": [
            {"id": identifier, "kind": kind}
            for identifier, kind in sorted(unique_nodes)
        ],
        "edges": [
            {"source": source, "target": target, "relationship": relationship}
            for source, target, relationship in sorted(edges)
        ],
    }
    graph["graph_sha256"] = canonical_digest(graph)
    return graph


def build_impact_delta(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    def indexed(document: Mapping[str, Any] | None) -> dict[str, str]:
        if document is None:
            return {}
        result: dict[str, str] = {}
        for field, kind in (
            ("statements", "statement"),
            ("capabilities", "capability"),
            ("tasks", "task"),
            ("artifacts", "artifact"),
        ):
            for item in document.get(field, []):
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    result[f"{kind}:{item['id']}"] = canonical_digest(item)
        return result

    before = indexed(previous)
    after = indexed(current)
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(
        identifier
        for identifier in set(before) & set(after)
        if before[identifier] != after[identifier]
    )
    seeds = set(added) | set(removed) | set(changed)
    graphs = [build_impact_graph(current)]
    if previous is not None:
        graphs.append(build_impact_graph(previous))
    adjacency: dict[str, set[str]] = {}
    for graph in graphs:
        for edge in graph["edges"]:
            adjacency.setdefault(edge["source"], set()).add(edge["target"])
    impacted = set(seeds)
    frontier = list(sorted(seeds))
    while frontier:
        source = frontier.pop(0)
        for target in sorted(adjacency.get(source, set())):
            if target not in impacted:
                impacted.add(target)
                frontier.append(target)
    delta: dict[str, Any] = {
        "schema_version": 1,
        "previous_semantic_document_sha256": (
            canonical_digest(previous) if previous is not None else None
        ),
        "current_semantic_document_sha256": canonical_digest(current),
        "added": added,
        "removed": removed,
        "changed": changed,
        "impacted": sorted(impacted),
    }
    delta["delta_sha256"] = canonical_digest(delta)
    return delta


def classify_execution(task: Mapping[str, Any]) -> dict[str, Any]:
    consequence = str(task.get("consequence", "low"))
    evidence_gap = str(task.get("evidence_gap", "low"))
    highest = max(LEVELS.get(consequence, 0), LEVELS.get(evidence_gap, 0))
    if highest >= LEVELS["high"]:
        mode = "challenged"
        review = True
    elif highest == LEVELS["medium"]:
        mode = "planned"
        review = False
    else:
        mode = "direct"
        review = False
    return {
        "schema_version": 1,
        "classification_basis": "explicit-consequence-and-evidence-gap-v1",
        "consequence": consequence,
        "evidence_gap": evidence_gap,
        "execution_mode": mode,
        "independent_review_required": review,
    }


def build_task_packets(
    document: Mapping[str, Any], registry: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    capabilities = {
        str(item["id"]): item
        for item in document.get("capabilities", [])
        if isinstance(item, dict) and "id" in item
    }
    packets: list[dict[str, Any]] = []
    errors: list[str] = []
    for task in document.get("tasks", []):
        if not isinstance(task, dict):
            continue
        task_id = str(task["id"])
        capability_ids = cast(list[str], task.get("capabilities", []))
        requested_skill_capabilities = sorted(
            {
                value
                for capability_id in capability_ids
                for value in capabilities.get(capability_id, {}).get(
                    "skill_capabilities", []
                )
                if isinstance(value, str)
            }
        )
        discovery = discover_skills(
            cast(dict[str, Any], registry),
            task=f"{task.get('title', '')} {task.get('description', '')}",
            capabilities=requested_skill_capabilities,
            risks=cast(list[str], task.get("risks", [])),
            evidence_gaps=[str(task.get("evidence_gap", "low"))],
        )
        selected: list[dict[str, Any]] = []
        for skill_id in task.get("selected_skills", []):
            skill, resolve_error = resolve_skill_metadata(
                cast(dict[str, Any], registry), str(skill_id)
            )
            if resolve_error or skill is None:
                errors.append(f"task {task_id}: {resolve_error}")
                continue
            selected.append(
                {
                    "registry_id": skill["registry_id"],
                    "version": skill["version"],
                    "manifest_sha256": skill["manifest_sha256"],
                    "procedure_sha256": skill["procedure_sha256"],
                    "selection_source": "semantic-document",
                }
            )
        packet: dict[str, Any] = {
            "schema_version": 1,
            "task_id": task_id,
            "title": task["title"],
            "description": task.get("description", ""),
            "depends_on": sorted(task.get("depends_on", [])),
            "capability_requirements": [capabilities[item] for item in capability_ids],
            "risks": sorted(task.get("risks", [])),
            "acceptance": task.get("acceptance", []),
            "execution_classification": classify_execution(task),
            "skill_discovery": {
                "registry_sha256": registry.get("registry_sha256"),
                "requested_capabilities": requested_skill_capabilities,
                "shortlist": discovery["shortlist"],
            },
            "selected_skill_invocations": selected,
        }
        packet["packet_sha256"] = canonical_digest(packet)
        packets.append(packet)
    return sorted(packets, key=lambda packet: str(packet["task_id"])), errors


class SemanticProvider(Protocol):
    name: str

    def compile(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


def provider_entry_points() -> dict[str, importlib.metadata.EntryPoint]:
    points = importlib.metadata.entry_points()
    selected = points.select(group="aae.semantic_providers")
    return {point.name: point for point in selected}


def compile_with_provider(
    provider: SemanticProvider, request: Mapping[str, Any]
) -> dict[str, Any]:
    result = dict(provider.compile(request))
    errors, _ = validate_semantic_document(result)
    if errors:
        raise ValueError("provider returned invalid semantic document: " + "; ".join(errors))
    return result


@dataclass(frozen=True)
class PublicationResult:
    release_id: str
    release_path: Path
    manifest_sha256: str
    created: bool


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return cast(dict[str, Any], value)


def verify_semantic_release(
    root: Path, release_id: str | None = None
) -> tuple[dict[str, Any] | None, dict[str, Any], Path]:
    """Verify a published release before any authoritative read or transition."""
    generated = root / ".aae/generated"
    active: dict[str, Any] | None = None
    if release_id is None:
        active_path = generated / "active-release.json"
        if not active_path.is_file():
            raise ValueError("no active semantic release exists")
        active = _read_json(active_path)
        if active.get("schema_version") != 1:
            raise ValueError("active semantic release schema_version must be 1")
        release_id = active.get("release_id")
    if not _valid_id(release_id):
        raise ValueError("semantic release id must be a portable identifier")

    release_path = generated / "releases" / cast(str, release_id)
    manifest_path = release_path / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"semantic release does not exist: {release_id}")
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != 1:
        raise ValueError(f"semantic release manifest schema_version is invalid: {release_id}")
    expected_manifest_digest = manifest.get("manifest_sha256")
    observed_manifest_digest = canonical_digest(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    if expected_manifest_digest != observed_manifest_digest:
        raise ValueError(f"semantic release manifest digest mismatch: {release_id}")
    if manifest.get("release_id") != release_id:
        raise ValueError(f"semantic release manifest identity mismatch: {release_id}")
    if active is not None and active.get("manifest_sha256") != observed_manifest_digest:
        raise ValueError(f"active semantic release manifest binding mismatch: {release_id}")

    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise ValueError(f"semantic release manifest files must be a list: {release_id}")
    declared: dict[str, str] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"semantic release manifest files[{index}] is invalid")
        relative = entry.get("path")
        digest = entry.get("sha256")
        if not _safe_relative_path(relative) or not isinstance(digest, str):
            raise ValueError(f"semantic release manifest files[{index}] is invalid")
        relative_text = cast(str, relative)
        if relative_text in declared:
            raise ValueError(f"semantic release manifest has duplicate file: {relative_text}")
        declared[relative_text] = digest

    actual = {
        path.relative_to(release_path).as_posix()
        for path in release_path.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if actual != set(declared):
        raise ValueError(f"semantic release file inventory mismatch: {release_id}")
    for relative, expected_digest in declared.items():
        path = release_path / relative
        observed_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if observed_digest != expected_digest:
            raise ValueError(f"semantic release file digest mismatch: {relative}")

    semantic_path = release_path / "semantic-model.json"
    semantic_document = _read_json(semantic_path)
    semantic_digest = canonical_digest(semantic_document)
    if manifest.get("semantic_document_sha256") != semantic_digest:
        raise ValueError(f"semantic release document digest mismatch: {release_id}")
    if release_id != f"semantic-v1-{semantic_digest[:16]}":
        raise ValueError(f"semantic release id does not match its document: {release_id}")

    for relative in sorted(declared):
        if not relative.startswith("task-packets/"):
            continue
        packet = _read_json(release_path / relative)
        packet_digest = packet.get("packet_sha256")
        observed_packet_digest = canonical_digest(
            {key: value for key, value in packet.items() if key != "packet_sha256"}
        )
        if packet_digest != observed_packet_digest:
            raise ValueError(f"semantic task packet digest mismatch: {relative}")
    return active, manifest, release_path


def publish_semantic_document(root: Path, document: Mapping[str, Any]) -> PublicationResult:
    registry, registry_errors, _ = build_skill_registry(root)
    errors, _ = validate_semantic_document(document, registry, root=root)
    errors.extend(registry_errors)
    unresolved = unresolved_material_items(document)
    if unresolved:
        errors.append("unresolved material items: " + ", ".join(unresolved))
    if errors:
        raise ValueError("semantic publication blocked: " + "; ".join(errors))

    document_digest = canonical_digest(document)
    release_id = f"semantic-v1-{document_digest[:16]}"
    generated = root / ".aae/generated"
    releases = generated / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    release_path = releases / release_id
    graph = build_impact_graph(document)
    active_path = generated / "active-release.json"
    previous_document: dict[str, Any] | None = None
    if active_path.exists():
        previous_active, _, _ = verify_semantic_release(root)
        assert previous_active is not None
        previous_id = previous_active.get("release_id")
        if isinstance(previous_id, str) and previous_id != release_id:
            previous_model = releases / previous_id / "semantic-model.json"
            if previous_model.is_file():
                previous_document = _read_json(previous_model)
    impact_delta = build_impact_delta(previous_document, document)
    packets, packet_errors = build_task_packets(document, registry)
    if packet_errors:
        raise ValueError("task packet generation failed: " + "; ".join(packet_errors))

    files: dict[str, object] = {
        "semantic-model.json": document,
        "impact-graph.json": graph,
        "impact-delta.json": impact_delta,
        "skill-registry-reference.json": {
            "schema_version": 1,
            "registry_sha256": registry["registry_sha256"],
            "skill_count": registry["skill_count"],
        },
    }
    for packet in packets:
        files[f"task-packets/{packet['task_id']}.json"] = packet
        classification = packet["execution_classification"]
        if classification["independent_review_required"]:
            files[f"review-packets/{packet['task_id']}.json"] = {
                "schema_version": 1,
                "task_id": packet["task_id"],
                "task_packet_sha256": packet["packet_sha256"],
                "review_question": "Does the bounded implementation satisfy authority, acceptance, safety, and evidence requirements?",
                "independence_required": True,
                "evidence_required": packet["acceptance"],
            }
    file_digests = {
        relative: hashlib.sha256(canonical_json_bytes(content)).hexdigest()
        for relative, content in sorted(files.items())
    }
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "aae_version": __version__,
        "semantic_schema_version": SEMANTIC_SCHEMA_VERSION,
        "semantic_document_sha256": document_digest,
        "registry_sha256": registry["registry_sha256"],
        "source_digests": sorted(
            {
                (source["path"], source["sha256"])
                for statement in document.get("statements", [])
                if isinstance(statement, dict)
                for source in statement.get("sources", [])
                if isinstance(source, dict)
            }
        ),
        "provider": document.get("compiler", {"provider": "external-unspecified"}),
    }
    files["provenance.json"] = provenance
    file_digests["provenance.json"] = hashlib.sha256(
        canonical_json_bytes(provenance)
    ).hexdigest()
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "release_id": release_id,
        "semantic_document_sha256": document_digest,
        "files": [
            {"path": relative, "sha256": digest}
            for relative, digest in sorted(file_digests.items())
        ],
    }
    manifest_digest = canonical_digest(manifest)
    manifest["manifest_sha256"] = manifest_digest
    files["manifest.json"] = manifest

    created = False
    if release_path.exists():
        verify_semantic_release(root, release_id)
        existing = _read_json(release_path / "manifest.json")
        if existing != manifest:
            raise ValueError(f"existing release {release_id} does not match expected manifest")
    else:
        staging = Path(tempfile.mkdtemp(dir=releases, prefix=f".{release_id}."))
        try:
            for relative, content in files.items():
                _atomic_json(staging / relative, content)
            os.replace(staging, release_path)
            created = True
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    previous = None
    if active_path.exists():
        previous = _read_json(active_path).get("release_id")
    _atomic_json(
        active_path,
        {
            "schema_version": 1,
            "release_id": release_id,
            "manifest_sha256": manifest_digest,
            "previous_release_id": previous if previous != release_id else None,
        },
    )
    return PublicationResult(release_id, release_path, manifest_digest, created)


def rollback_release(root: Path, target_release_id: str | None = None) -> str:
    generated = root / ".aae/generated"
    active_path = generated / "active-release.json"
    if not active_path.exists():
        raise ValueError("no active semantic release exists")
    active, _, _ = verify_semantic_release(root)
    assert active is not None
    target = target_release_id or active.get("previous_release_id")
    if not isinstance(target, str) or not target:
        raise ValueError("no previous semantic release is available")
    _, manifest, _ = verify_semantic_release(root, target)
    observed = cast(str, manifest["manifest_sha256"])
    _atomic_json(
        active_path,
        {
            "schema_version": 1,
            "release_id": target,
            "manifest_sha256": observed,
            "previous_release_id": active.get("release_id"),
        },
    )
    return target


def load_active_task_packet(root: Path, task_id: str) -> dict[str, Any]:
    if not _valid_id(task_id):
        raise ValueError("task id must be a portable identifier")
    active, _, release_path = verify_semantic_release(root)
    assert active is not None
    release_id = active.get("release_id")
    if not isinstance(release_id, str):
        raise ValueError("active release has no release id")
    return _read_json(
        release_path / "task-packets" / f"{task_id}.json"
    )


def export_tracker_items(root: Path, provider: str) -> list[dict[str, Any]]:
    if provider not in TRACKER_PROVIDERS:
        raise ValueError(f"tracker provider must be one of {sorted(TRACKER_PROVIDERS)}")
    active, _, release_path = verify_semantic_release(root)
    assert active is not None
    release_id = str(active["release_id"])
    packet_root = release_path / "task-packets"
    result: list[dict[str, Any]] = []
    for path in sorted(packet_root.glob("*.json")):
        packet = _read_json(path)
        body = "\n".join(
            [
                packet.get("description", ""),
                "",
                "Acceptance:",
                *[f"- {item}" for item in packet.get("acceptance", [])],
                "",
                f"AAE task packet: {packet['packet_sha256']}",
            ]
        ).strip()
        labels = sorted({"aae", *packet.get("risks", [])})
        if provider == "github":
            item = {"title": packet["title"], "body": body, "labels": labels}
        elif provider == "jira":
            item = {
                "fields": {
                    "summary": packet["title"],
                    "description": body,
                    "labels": labels,
                }
            }
        else:
            item = {
                "fields": {
                    "System.Title": packet["title"],
                    "System.Description": body,
                    "System.Tags": "; ".join(labels),
                }
            }
        result.append(
            {
                "schema_version": 1,
                "provider": provider,
                "release_id": release_id,
                "task_id": packet["task_id"],
                "task_packet_sha256": packet["packet_sha256"],
                "payload": item,
            }
        )
    return result
