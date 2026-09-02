from __future__ import annotations

import argparse
import hashlib
import importlib.resources
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, cast

from . import __version__
from .accounting import build_agent_skill_accounting
from .adaptive import (
    build_ci_policy,
    build_historical_use_graph,
    build_otel_genai_trace_export,
    build_promotion_proposal,
    evaluate_skill_lifecycle,
    route_model,
    skill_retriever_entry_points,
)
from .control import (
    POLICY_PATH,
    invoke_skill,
    load_invocation_policy,
    record_invocation_outcome,
)
from .execution import (
    EXECUTION_CONFIG,
    execute_governed_task,
    load_execution_configuration,
)
from .integrations import submit_tracker_items
from .skills import (
    LOCAL_SKILL_SOURCES,
    SKILL_DIRECTORY,
    SKILL_SOURCES,
    build_skill_registry,
    discover_skills,
    record_skill_event,
    resolve_skill_metadata,
    summarize_skill_events,
    watched_skill_paths,
)
from .semantic import (
    build_impact_graph,
    canonical_digest,
    export_tracker_items,
    load_active_task_packet,
    provider_entry_points,
    publish_semantic_document,
    rollback_release,
    validate_semantic_document,
)


INTENT_DIRECTORY = Path(".aae/intent")
RUNTIME_DIRECTORY = Path(".aae/runtime")
STATE_DIRECTORY = Path(".aae/state")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_local(path: Path) -> bool:
    return path.name.endswith(".local.md")


def base_name_for(path: Path) -> str:
    if is_local(path):
        return path.name.removesuffix(".local.md") + ".md"
    return path.name


def discover_intent(root: Path) -> list[dict[str, Any]]:
    intent_root = root / INTENT_DIRECTORY
    if not intent_root.exists():
        return []

    paths = sorted(
        path
        for path in intent_root.rglob("*.md")
        if path.is_file() and not path.name.endswith(".example.md")
    )
    sources: list[dict[str, Any]] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        sources.append(
            {
                "path": relative,
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
                "local": is_local(path),
                "overlay_of": (
                    (path.parent / base_name_for(path)).relative_to(root).as_posix()
                    if is_local(path)
                    else None
                ),
            }
        )
    sources.sort(key=lambda item: (str(item["overlay_of"] or item["path"]), bool(item["local"])))
    return sources


def write_json(path: Path, value: object) -> None:
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


def compiler_request(
    root: Path,
    sources: list[dict[str, Any]],
    registry: dict[str, Any],
) -> str:
    lines = [
        "# AAE Semantic Compiler Request",
        "",
        "Interpret the listed Markdown sources in the spirit of Adaptive Agentic Engineering.",
        "",
        "## Required behavior",
        "",
        "1. Treat the intent plane as open-ended; filenames and seed documents are hints, not a whitelist.",
        "2. Read shared sources before their matching `.local.md` overlays.",
        "3. Use local overlays to specialize the effective local configuration.",
        "4. Preserve provenance for every extracted fact, preference, constraint, decision, and unresolved question.",
        "5. Distinguish explicit statements, repository-backed inferences, proposals, and unknowns.",
        "6. Ask focused questions when answers materially affect correctness, safety, scope, or architecture.",
        "7. Acquire only evidence needed for the next action; persist durable results and discard stale working context.",
        "8. Do not treat Markdown as enforcement. Identify CI, administrative, policy, or platform controls needed for enforcement.",
        "9. Generate or update only artifacts affected by semantic changes; explain removals and conflicts.",
        "10. Never copy secrets or sensitive payload content into generated telemetry or context packets.",
        "11. Workflows request capabilities. Discover registered skills with bounded metadata before loading any full skill procedure.",
        "12. Treat skill invocation and specialist creation as separate decisions; use an ephemeral role only when specialization or independence warrants it.",
        "13. Record selected skill registry IDs and versions in execution evidence. Do not route candidate, deprecated, or retired skills automatically.",
        "",
        "## Sources in effective order",
        "",
    ]
    for source in sources:
        kind = "local overlay" if source["local"] else "shared"
        lines.append(f"- `{source['path']}` ({kind}, sha256 `{str(source['sha256'])[:12]}`)")
    lines.extend(
        [
            "",
            "## Capability and skill registry",
            "",
            f"- Registered skills: {registry['skill_count']}",
            f"- Advertised capabilities: {len(registry['capabilities'])}",
            f"- Portable registry identity: `{str(registry['registry_content_sha256'])[:12]}`",
            "- Registry path: `.aae/runtime/skill-registry.json`",
            "- Use `aae discover` to obtain a bounded shortlist. Load full instructions with `aae skill` only after selection.",
        ]
    )
    lines.extend(
        [
            "",
            "## Expected result",
            "",
            "Produce a concise semantic change plan, diagnostics, impacted capabilities, and the bounded runtime artifacts required by the active tool. Do not preload all sources into unrelated future tasks.",
            "",
        ]
    )
    return "\n".join(lines)


def init_repository(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    template_root = cast(Any, importlib.resources.files("aae.templates"))
    installed: list[str] = []
    skipped: list[str] = []
    for resource in template_root.rglob("*"):
        if (
            not resource.is_file()
            or resource.name == "__init__.py"
            or resource.suffix == ".pyc"
            or "__pycache__" in resource.parts
        ):
            continue
        relative = Path(*resource.relative_to(template_root).parts)
        destination = root / relative
        if destination.exists():
            skipped.append(relative.as_posix())
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with importlib.resources.as_file(resource) as source:
            shutil.copyfile(source, destination)
        installed.append(relative.as_posix())

    print(f"AAE initialized at {root.resolve()}")
    print(f"Installed {len(installed)} file(s); preserved {len(skipped)} existing file(s).")
    if skipped:
        print("Preserved: " + ", ".join(skipped))
    return 0


def compile_repository(root: Path, quiet: bool = False) -> int:
    sources = discover_intent(root)
    if not sources:
        print(f"No intent sources found under {root / INTENT_DIRECTORY}", file=sys.stderr)
        return 1

    registry, registry_errors, registry_warnings = build_skill_registry(root)
    for warning in registry_warnings:
        if not quiet:
            print(f"WARNING: {warning}")
    if registry_errors:
        for error in registry_errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print("Skill registry compilation failed.", file=sys.stderr)
        return 1

    manifest = {
        "schema_version": 2,
        "project_root": str(root.resolve()),
        "source_count": len(sources),
        "sources": sources,
        "skill_registry": {
            "skill_count": registry["skill_count"],
            "capability_count": len(registry["capabilities"]),
            "registry_sha256": registry["registry_sha256"],
            "registry_content_sha256": registry["registry_content_sha256"],
        },
    }
    write_json(root / RUNTIME_DIRECTORY / "effective-sources.json", manifest)
    write_json(root / RUNTIME_DIRECTORY / "skill-registry.json", registry)
    request_path = root / RUNTIME_DIRECTORY / "compiler-request.md"
    request_path.write_text(compiler_request(root, sources, registry), encoding="utf-8")
    if not quiet:
        shared = sum(not bool(source["local"]) for source in sources)
        local = len(sources) - shared
        print(f"Prepared {len(sources)} intent source(s): {shared} shared, {local} local.")
        print(
            f"Indexed {registry['skill_count']} skill(s) advertising "
            f"{len(registry['capabilities'])} capability name(s)."
        )
        print(f"Compiler request: {request_path}")
    return 0


def git_tracked_local_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "*.local.md",
                ".aae/**/*.local.md",
                "*.local.json",
                ".aae/**/*.local.json",
            ],
            cwd=root,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []
    return [line for line in result.stdout.splitlines() if line]


def validate_repository(root: Path) -> int:
    errors: list[str] = []
    warnings: list[str] = []
    sources = discover_intent(root)
    if not sources:
        errors.append("No Markdown sources exist under .aae/intent.")

    for source in sources:
        if source["local"] and not (root / str(source["overlay_of"])).exists():
            warnings.append(f"Local overlay has no shared counterpart: {source['path']}")
        if int(source["bytes"]) == 0:
            warnings.append(f"Empty intent source: {source['path']}")

    for path in git_tracked_local_files(root):
        errors.append(f"Local overlay is tracked by Git: {path}")

    registry, registry_errors, registry_warnings = build_skill_registry(root)
    errors.extend(registry_errors)
    warnings.extend(registry_warnings)
    if int(registry["skill_count"]) == 0:
        warnings.append("No skills are registered; capability discovery will return no candidates.")

    _, policy_errors = load_invocation_policy(root)
    if (root / POLICY_PATH).exists():
        errors.extend(policy_errors)
    else:
        warnings.extend(policy_errors)

    if (root / EXECUTION_CONFIG).exists():
        try:
            load_execution_configuration(root, require_effective_executor=False)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            errors.append(f"Governed execution configuration is invalid: {error}")

    required_gitignore = ["*.local.md", "*.local.json", ".aae/runtime/"]
    gitignore = root / ".gitignore"
    content = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    for entry in required_gitignore:
        if entry not in content:
            warnings.append(f"Recommended .gitignore entry is missing: {entry}")

    status = "PASS" if not errors else "FAIL"
    print(f"AAE validation: {status}")
    for message in warnings:
        print(f"WARNING: {message}")
    for message in errors:
        print(f"ERROR: {message}")
    return 0 if not errors else 1


def watched_state(root: Path) -> tuple[tuple[str, str], ...]:
    paths: set[Path] = set()
    intent_root = root / INTENT_DIRECTORY
    skill_root = root / SKILL_DIRECTORY
    if intent_root.exists():
        paths.update(path for path in intent_root.rglob("*.md") if path.is_file())
    if skill_root.exists():
        paths.update(path for path in skill_root.rglob("*") if path.is_file())
    paths.update(watched_skill_paths(root))
    for config in (root / SKILL_SOURCES, root / LOCAL_SKILL_SOURCES):
        if config.is_file():
            paths.add(config)
    return tuple(
        (
            (
                path.relative_to(root).as_posix()
                if path.is_relative_to(root)
                else str(path.resolve())
            ),
            sha256(path),
        )
        for path in sorted(paths)
    )


def watch_repository(root: Path, interval: float) -> int:
    previous: tuple[tuple[str, str], ...] | None = None
    print(f"Watching AAE intent and configured skill sources under {root}; press Ctrl+C to stop.")
    try:
        while True:
            current = watched_state(root)
            if current != previous:
                compile_repository(root, quiet=previous is not None)
                if previous is not None:
                    print("Intent or skill change detected; compiler request refreshed.")
                previous = current
            time.sleep(interval)
    except KeyboardInterrupt:
        print("Stopped.")
        return 0


def doctor(root: Path) -> int:
    print(f"AAE version: {__version__}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Project root: {root.resolve()}")
    print(f"Intent directory: {'present' if (root / INTENT_DIRECTORY).exists() else 'missing'}")
    print(f"Git repository: {'yes' if (root / '.git').exists() else 'no'}")
    print(f"Intent sources: {len(discover_intent(root))}")
    registry, _, _ = build_skill_registry(root)
    print(f"Registered skills: {registry['skill_count']}")
    print(f"Advertised capabilities: {len(registry['capabilities'])}")
    print(f"Portable registry identity: {registry['registry_content_sha256']}")
    return validate_repository(root)


def registry_repository(root: Path, as_json: bool = False) -> int:
    registry, errors, warnings = build_skill_registry(root)
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    write_json(root / RUNTIME_DIRECTORY / "skill-registry.json", registry)
    if as_json:
        print(json.dumps(registry, indent=2))
        return 0
    print(
        f"AAE skill registry: {registry['skill_count']} skill(s), "
        f"{len(registry['capabilities'])} capability name(s), "
        f"{registry['source_count']} source(s)."
    )
    print(f"Portable registry identity: {registry['registry_content_sha256']}")
    for skill in registry["skills"]:
        capabilities = ", ".join(skill["capabilities"])
        print(
            f"- {skill['registry_id']}@{skill['version']} "
            f"[{skill['lifecycle']}; {skill['source']['scope']}; "
            f"trust={skill['source']['trust']}; "
            f"approval={skill['source']['approval']['status']}] -> {capabilities}"
        )
    return 0


def discover_repository(
    root: Path,
    task: str,
    capabilities: Iterable[str],
    facts: Iterable[str],
    architecture: Iterable[str],
    environment: Iterable[str],
    risks: Iterable[str],
    evidence_gaps: Iterable[str],
    candidate_limit: int,
    limit: int,
    as_json: bool,
) -> int:
    registry, errors, warnings = build_skill_registry(root)
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    write_json(root / RUNTIME_DIRECTORY / "skill-registry.json", registry)
    result = discover_skills(
        registry,
        task=task,
        capabilities=capabilities,
        facts=facts,
        architecture=architecture,
        environment=environment,
        risks=risks,
        evidence_gaps=evidence_gaps,
        candidate_limit=candidate_limit,
        limit=limit,
    )
    for skill in result["shortlist"]:
        matched = skill["matched"]
        reason = "; ".join(
            f"{field}={','.join(values)}"
            for field, values in matched.items()
            if values
        )
        record_skill_event(
            root,
            registry,
            str(skill["registry_id"]),
            "considered",
            reason=reason or "bounded-relevance-shortlist",
        )
    if as_json:
        print(json.dumps(result, indent=2))
        return 0
    print(
        f"AAE skill discovery: {result['registry_skill_count']} registered -> "
        f"{result['metadata_candidate_count']} metadata candidate(s) -> "
        f"{len(result['shortlist'])} shortlisted."
    )
    for skill in result["shortlist"]:
        capabilities_text = ", ".join(skill["capabilities"])
        print(
            f"- {skill['registry_id']}@{skill['version']} "
            f"(score {skill['score']}; {capabilities_text}): {skill['description']}"
        )
    return 0


def show_skill(
    root: Path,
    identifier: str,
    metadata_only: bool = False,
    reason: str | None = None,
) -> int:
    registry, errors, warnings = build_skill_registry(root)
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if not metadata_only:
        print(
            "ERROR: Direct procedure loading is disabled; use 'aae invoke' so an "
            "InvocationPlan can authorize the load",
            file=sys.stderr,
        )
        return 1
    skill, load_error = resolve_skill_metadata(registry, identifier)
    if load_error:
        print(f"ERROR: {load_error}", file=sys.stderr)
        return 1
    assert skill is not None
    public = {
        key: value
        for key, value in skill.items()
        if key not in {"procedure_path"}
    }
    print(json.dumps(public, indent=2))
    return 0


def invoke_repository(
    root: Path,
    task: str,
    explicit_skill: str | None,
    capabilities: Iterable[str],
    architecture: Iterable[str],
    environment: Iterable[str],
    risks: Iterable[str],
    evidence_gaps: Iterable[str],
    task_id: str | None,
    spec_id: str | None,
    context_digest: str | None,
    fresh_context: bool,
    tools: Iterable[str],
    model_capabilities: Iterable[str],
    model: str | None,
    provider: str | None,
    network_available: bool,
    data_classification: str,
    model_data_classifications: Iterable[str],
    approvals: Iterable[str],
    platform: str,
    candidate_limit: int,
    limit: int,
    as_json: bool,
) -> int:
    registry, errors, warnings = build_skill_registry(root)
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    write_json(root / RUNTIME_DIRECTORY / "skill-registry.json", registry)
    runtime_profile = {
        "fresh_context": fresh_context,
        "available_tools": list(tools),
        "model_capabilities": list(model_capabilities),
        "model": model,
        "provider": provider,
        "network_available": network_available,
        "data_classification": data_classification,
        "model_data_classifications": list(model_data_classifications),
        "approvals": list(approvals),
        "platform": platform,
    }
    record, procedure, policy_errors = invoke_skill(
        root,
        registry,
        task=task,
        explicit_skill=explicit_skill,
        explicit_capabilities=capabilities,
        architecture=architecture,
        environment=environment,
        risks=risks,
        evidence_gaps=evidence_gaps,
        task_id=task_id,
        spec_id=spec_id,
        context_evidence_sha256=context_digest,
        runtime_profile=runtime_profile,
        candidate_limit=candidate_limit,
        shortlist_limit=limit,
    )
    for candidate in record["candidate_set"]["candidates"]:
        record_skill_event(
            root,
            registry,
            candidate["registry_id"],
            "considered",
            reason="invocation-candidate-set",
        )
    selected_id = record["selection_decision"]["selected_registry_id"]
    if selected_id:
        record_skill_event(
            root,
            registry,
            selected_id,
            "selected",
            reason=record["selection_decision"]["reason"],
        )
    output = {
        "invocation_record": record,
        "procedure": procedure if as_json else None,
    }
    if as_json:
        print(json.dumps(output, indent=2))
    else:
        summary = {
            "invocation_id": record["invocation_id"],
            "status": record["status"],
            "selected_registry_id": selected_id,
            "policy_decision": record["invocation_plan"]["policy"]["decision"],
            "rejection_reasons": record["invocation_plan"]["policy"][
                "rejection_reasons"
            ],
            "capability_demand_sha256": record["capability_demand"][
                "capability_demand_sha256"
            ],
            "candidate_set_sha256": record["candidate_set"][
                "candidate_set_sha256"
            ],
            "invocation_plan_sha256": record["invocation_plan"][
                "invocation_plan_sha256"
            ],
            "invocation_record_sha256": record["invocation_record_sha256"],
            "record_path": str(
                root
                / ".aae/runtime/invocations"
                / f"{record['invocation_id']}.json"
            ),
        }
        print(json.dumps(summary, indent=2))
        if procedure is not None:
            print("\n--- selected procedure ---\n")
            print(procedure, end="" if procedure.endswith("\n") else "\n")
    for error in policy_errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return 0 if record["status"] == "procedure-loaded" else 1


def record_outcome(
    root: Path,
    identifier: str,
    outcome: str,
    context_tokens: int | None,
    execution_cost: float | None,
    evidence: str | None,
    invocation_id: str | None = None,
    verification: str | None = None,
) -> int:
    registry, errors, warnings = build_skill_registry(root)
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    event_error = record_skill_event(
        root,
        registry,
        identifier,
        outcome,
        context_tokens=context_tokens,
        execution_cost=execution_cost,
        evidence=evidence,
    )
    if event_error:
        print(f"ERROR: {event_error}", file=sys.stderr)
        return 1
    if invocation_id:
        invocation_error = record_invocation_outcome(
            root,
            invocation_id,
            outcome=outcome,
            verification=verification,
            evidence=evidence,
            context_tokens=context_tokens,
            execution_cost=execution_cost,
        )
        if invocation_error:
            print(f"ERROR: {invocation_error}", file=sys.stderr)
            return 1
    print(f"Recorded {outcome} outcome for {identifier}.")
    return 0


def skill_stats(root: Path, as_json: bool = False) -> int:
    registry, errors, registry_warnings = build_skill_registry(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    summaries, event_warnings = summarize_skill_events(root, registry)
    for warning in [*registry_warnings, *event_warnings]:
        print(f"WARNING: {warning}", file=sys.stderr)
    if as_json:
        print(json.dumps(summaries, indent=2))
        return 0
    print("AAE skill telemetry:")
    for versioned_id, summary in summaries.items():
        print(
            f"- {versioned_id}: considered={summary['considered']} "
            f"selected={summary['selected']} succeeded={summary['succeeded']} "
            f"failed={summary['failed']} superseded={summary['superseded']} "
            f"selection_rate={summary['selection_rate']} failure_rate={summary['failure_rate']} "
            f"context_tokens={summary['context_tokens']} execution_cost={summary['execution_cost']}"
        )
    return 0


def _load_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return None, f"Cannot read JSON from {path}: {error}"
    if not isinstance(value, dict):
        return None, f"{path} must contain a JSON object"
    return cast(dict[str, Any], value), None


def semantic_validate(root: Path, input_path: Path, as_json: bool) -> int:
    document, read_error = _load_json_object(input_path)
    if read_error:
        print(f"ERROR: {read_error}", file=sys.stderr)
        return 1
    assert document is not None
    registry, registry_errors, registry_warnings = build_skill_registry(root)
    errors, warnings = validate_semantic_document(document, registry, root=root)
    errors.extend(registry_errors)
    warnings.extend(registry_warnings)
    result = {
        "status": "PASS" if not errors else "FAIL",
        "document_sha256": canonical_digest(document),
        "errors": errors,
        "warnings": warnings,
    }
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"AAE semantic validation: {result['status']}")
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR: {error}")
    return 0 if not errors else 1


def semantic_impact(input_path: Path) -> int:
    document, read_error = _load_json_object(input_path)
    if read_error:
        print(f"ERROR: {read_error}", file=sys.stderr)
        return 1
    assert document is not None
    errors, warnings = validate_semantic_document(document)
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(build_impact_graph(document), indent=2))
    return 0


def semantic_publish(root: Path, input_path: Path) -> int:
    document, read_error = _load_json_object(input_path)
    if read_error:
        print(f"ERROR: {read_error}", file=sys.stderr)
        return 1
    assert document is not None
    try:
        result = publish_semantic_document(root, document)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "release_id": result.release_id,
                "release_path": str(result.release_path),
                "manifest_sha256": result.manifest_sha256,
                "created": result.created,
            },
            indent=2,
        )
    )
    return 0


def semantic_rollback(root: Path, release_id: str | None) -> int:
    try:
        selected = rollback_release(root, release_id)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Active semantic release: {selected}")
    return 0


def show_task_packet(root: Path, task_id: str) -> int:
    try:
        packet = load_active_task_packet(root, task_id)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(packet, indent=2))
    return 0


def tracker_export(root: Path, provider: str, output: Path | None) -> int:
    try:
        items = export_tracker_items(root, provider)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    envelope = {"schema_version": 1, "provider": provider, "items": items}
    if output is not None:
        write_json(output, envelope)
        print(f"Exported {len(items)} item(s) to {output}.")
    else:
        print(json.dumps(envelope, indent=2))
    return 0


def tracker_submit(
    root: Path,
    provider: str,
    endpoint: str,
    token_environment: str,
    confirm_external_write: bool,
    defaults_path: Path | None,
) -> int:
    token = os.environ.get(token_environment)
    if not token:
        print(
            f"ERROR: credential environment variable is unset: {token_environment}",
            file=sys.stderr,
        )
        return 1
    try:
        defaults: dict[str, Any] | None = None
        if defaults_path is not None:
            defaults, read_error = _load_json_object(defaults_path)
            if read_error:
                raise ValueError(read_error)
        result = submit_tracker_items(
            root,
            provider,
            endpoint,
            token,
            confirm_external_write=confirm_external_write,
            payload_defaults=defaults,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def accounting_repository(root: Path, as_json: bool) -> int:
    accounting, errors, warnings = build_agent_skill_accounting(root)
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(accounting, indent=2))
        return 0
    agent_model = accounting["agent_model"]
    fabric = accounting["skill_fabric"]
    print("AAE agents and skills accounting")
    print(f"Persistent named agents: {agent_model['persistent_named_agents']}")
    print(
        f"Registered skills: {fabric['skill_count']} across "
        f"{fabric['capability_count']} capability names"
    )
    print("Runtime roles:")
    for role in agent_model["roles"]:
        kind = "deterministic code" if not role.get("is_agent", True) else "ephemeral role"
        print(f"- {role['role']} ({kind}): {role['purpose']}")
    print("Skills:")
    for skill in fabric["skills"]:
        capabilities = ", ".join(skill["capabilities"])
        print(
            f"- {skill['registry_id']}@{skill['version']} "
            f"[{skill['lifecycle']}; {skill['execution']['mode']}]: {capabilities}"
        )
    governed_runs = accounting["runtime_evidence"].get("governed_runs", [])
    print(f"Governed runs: {len(governed_runs)}")
    for run in governed_runs:
        print(
            f"- {run['run_id']} [{run['status']}]: "
            f"{run['selected_skill']['registry_id']} via "
            f"{run['executor']['provider']}/{run['executor']['model']}"
        )
    return 0


def governed_run_repository(
    root: Path,
    task: str,
    task_id: str,
    skill: str | None,
    capabilities: Iterable[str],
    acceptance_criteria: Iterable[str],
    evidence_paths: Iterable[Path],
    approvals: Iterable[str],
    as_json: bool,
) -> int:
    try:
        result = execute_governed_task(
            root,
            task_id=task_id,
            task=task,
            explicit_skill=skill,
            capabilities=capabilities,
            acceptance_criteria=acceptance_criteria,
            evidence_paths=evidence_paths,
            approvals=approvals,
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        PermissionError,
        RuntimeError,
        ValueError,
        subprocess.SubprocessError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(
            json.dumps(
                {
                    "run_id": result["run_id"],
                    "status": result["status"],
                    "task_id": result["task_request"]["task_id"],
                    "skill": result["primary"]["skill"]["registry_id"],
                    "executor": result["primary"]["tool"],
                    "model": result["primary"]["model"],
                    "review_invocation_id": result["review"].get("invocation_id"),
                    "accounting_path": result["accounting_path"],
                    "run_sha256": result["run_sha256"],
                },
                indent=2,
            )
        )
    return 0 if result["status"] == "succeeded" else 1


def model_route_repository(
    root: Path,
    capabilities: list[str],
    data_classification: str,
    network_available: bool,
    locations: list[str],
) -> int:
    try:
        result = route_model(
            root,
            capabilities=capabilities,
            data_classification=data_classification,
            network_available=network_available,
            allowed_locations=locations or ["local", "on-premises", "cloud"],
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["decision"] == "selected" else 1


def lifecycle_repository(
    root: Path, registry_id: str, target: str | None
) -> int:
    try:
        result = (
            build_promotion_proposal(root, registry_id, target)
            if target
            else evaluate_skill_lifecycle(root, registry_id)
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def json_artifact_repository(root: Path, kind: str, provider: str | None) -> int:
    try:
        if kind == "skill-graph":
            result = build_historical_use_graph(root)
        elif kind == "ci-policy":
            assert provider is not None
            result = build_ci_policy(provider)
        else:
            result = build_otel_genai_trace_export(root)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="aae", description="Adaptive Agentic Engineering bootstrap")
    commands = root.add_subparsers(dest="command", required=True)

    for name in ("init", "compile", "validate", "doctor"):
        command = commands.add_parser(name)
        command.add_argument("path", nargs="?", default=".")

    watch = commands.add_parser("watch")
    watch.add_argument("path", nargs="?", default=".")
    watch.add_argument("--interval", type=float, default=1.0)

    registry = commands.add_parser("registry")
    registry.add_argument("path", nargs="?", default=".")
    registry.add_argument("--json", action="store_true")

    discover = commands.add_parser("discover")
    discover.add_argument("task")
    discover.add_argument("path", nargs="?", default=".")
    discover.add_argument("--capability", action="append", default=[])
    discover.add_argument("--fact", action="append", default=[])
    discover.add_argument("--architecture", action="append", default=[])
    discover.add_argument("--environment", action="append", default=[])
    discover.add_argument("--risk", action="append", default=[])
    discover.add_argument("--evidence-gap", action="append", default=[])
    discover.add_argument("--candidate-limit", type=_positive_int, default=18)
    discover.add_argument("--limit", type=_positive_int, default=4)
    discover.add_argument("--json", action="store_true")

    skill = commands.add_parser("skill")
    skill.add_argument("identifier")
    skill.add_argument("path", nargs="?", default=".")
    skill.add_argument("--metadata-only", action="store_true")
    skill.add_argument("--reason")

    invoke = commands.add_parser("invoke")
    invoke.add_argument("task")
    invoke.add_argument("path", nargs="?", default=".")
    invoke.add_argument("--skill")
    invoke.add_argument("--capability", action="append", default=[])
    invoke.add_argument("--architecture", action="append", default=[])
    invoke.add_argument("--environment", action="append", default=[])
    invoke.add_argument("--risk", action="append", default=[])
    invoke.add_argument("--evidence-gap", action="append", default=[])
    invoke.add_argument("--task-id")
    invoke.add_argument("--spec-id")
    invoke.add_argument("--context-digest")
    invoke.add_argument("--fresh-context", action="store_true")
    invoke.add_argument("--tool", action="append", default=[])
    invoke.add_argument("--model-capability", action="append", default=[])
    invoke.add_argument("--model")
    invoke.add_argument("--provider")
    invoke.add_argument("--network-available", action="store_true")
    invoke.add_argument(
        "--data-classification",
        choices=("public", "internal", "controlled", "restricted"),
        default="internal",
    )
    invoke.add_argument("--model-data-classification", action="append", default=[])
    invoke.add_argument("--approval", action="append", default=[])
    invoke.add_argument("--platform", default=sys.platform)
    invoke.add_argument("--candidate-limit", type=_positive_int, default=18)
    invoke.add_argument("--limit", type=_positive_int, default=4)
    invoke.add_argument("--json", action="store_true")

    outcome = commands.add_parser("outcome")
    outcome.add_argument("identifier")
    outcome.add_argument("outcome", choices=("succeeded", "failed", "superseded"))
    outcome.add_argument("path", nargs="?", default=".")
    outcome.add_argument("--context-tokens", type=int)
    outcome.add_argument("--execution-cost", type=float)
    outcome.add_argument("--evidence")
    outcome.add_argument("--invocation-id")
    outcome.add_argument("--verification", choices=("passed", "failed", "blocked"))

    stats = commands.add_parser("skill-stats")
    stats.add_argument("path", nargs="?", default=".")
    stats.add_argument("--json", action="store_true")

    semantic = commands.add_parser("semantic")
    semantic_commands = semantic.add_subparsers(dest="semantic_command", required=True)
    semantic_validate_parser = semantic_commands.add_parser("validate")
    semantic_validate_parser.add_argument("input")
    semantic_validate_parser.add_argument("path", nargs="?", default=".")
    semantic_validate_parser.add_argument("--json", action="store_true")
    semantic_impact_parser = semantic_commands.add_parser("impact")
    semantic_impact_parser.add_argument("input")
    semantic_impact_parser.add_argument("path", nargs="?", default=".")
    semantic_publish_parser = semantic_commands.add_parser("publish")
    semantic_publish_parser.add_argument("input")
    semantic_publish_parser.add_argument("path", nargs="?", default=".")
    semantic_rollback_parser = semantic_commands.add_parser("rollback")
    semantic_rollback_parser.add_argument("release_id", nargs="?")
    semantic_rollback_parser.add_argument("--path", default=".")

    packet = commands.add_parser("task-packet")
    packet.add_argument("task_id")
    packet.add_argument("path", nargs="?", default=".")

    tracker = commands.add_parser("tracker-export")
    tracker.add_argument("provider", choices=("azure", "github", "jira"))
    tracker.add_argument("path", nargs="?", default=".")
    tracker.add_argument("--output", type=Path)

    tracker_submit_parser = commands.add_parser("tracker-submit")
    tracker_submit_parser.add_argument("provider", choices=("azure", "github", "jira"))
    tracker_submit_parser.add_argument("--endpoint", required=True)
    tracker_submit_parser.add_argument("--token-env", required=True)
    tracker_submit_parser.add_argument(
        "--defaults",
        type=Path,
        help="JSON object merged into each provider payload (required fields for Jira)",
    )
    tracker_submit_parser.add_argument(
        "--confirm-external-write", action="store_true"
    )
    tracker_submit_parser.add_argument("path", nargs="?", default=".")

    providers = commands.add_parser("providers")
    providers.add_argument("path", nargs="?", default=".")

    accounting = commands.add_parser("accounting")
    accounting.add_argument("path", nargs="?", default=".")
    accounting.add_argument("--json", action="store_true")

    governed = commands.add_parser("governed-run")
    governed.add_argument("task")
    governed.add_argument("path", nargs="?", default=".")
    governed.add_argument("--task-id", required=True)
    governed.add_argument("--skill")
    governed.add_argument("--capability", action="append", default=[])
    governed.add_argument("--acceptance", action="append", required=True)
    governed.add_argument("--evidence", action="append", type=Path, required=True)
    governed.add_argument("--approval", action="append", default=[])
    governed.add_argument("--json", action="store_true")

    model_route = commands.add_parser("model-route")
    model_route.add_argument("path", nargs="?", default=".")
    model_route.add_argument("--capability", action="append", default=[])
    model_route.add_argument(
        "--data-classification",
        choices=("public", "internal", "controlled", "restricted"),
        default="internal",
    )
    model_route.add_argument("--network-available", action="store_true")
    model_route.add_argument(
        "--location",
        action="append",
        choices=("local", "on-premises", "cloud"),
        default=[],
    )

    retrievers = commands.add_parser("retrievers")
    retrievers.add_argument("path", nargs="?", default=".")

    lifecycle = commands.add_parser("skill-evaluate")
    lifecycle.add_argument("registry_id")
    lifecycle.add_argument("path", nargs="?", default=".")
    lifecycle.add_argument("--propose")

    skill_graph = commands.add_parser("skill-graph")
    skill_graph.add_argument("path", nargs="?", default=".")

    ci_policy = commands.add_parser("ci-policy")
    ci_policy.add_argument("provider", choices=("github", "azure", "gitlab"))
    ci_policy.add_argument("path", nargs="?", default=".")

    trace_export = commands.add_parser("trace-export")
    trace_export.add_argument("path", nargs="?", default=".")

    return root


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def main(argv: Iterable[str] | None = None) -> int:
    arguments = parser().parse_args(list(argv) if argv is not None else None)
    root = Path(arguments.path).resolve()
    if arguments.command == "init":
        return init_repository(root)
    if arguments.command == "compile":
        return compile_repository(root)
    if arguments.command == "validate":
        return validate_repository(root)
    if arguments.command == "doctor":
        return doctor(root)
    if arguments.command == "watch":
        return watch_repository(root, arguments.interval)
    if arguments.command == "registry":
        return registry_repository(root, arguments.json)
    if arguments.command == "discover":
        return discover_repository(
            root,
            arguments.task,
            arguments.capability,
            arguments.fact,
            arguments.architecture,
            arguments.environment,
            arguments.risk,
            arguments.evidence_gap,
            arguments.candidate_limit,
            arguments.limit,
            arguments.json,
        )
    if arguments.command == "skill":
        return show_skill(root, arguments.identifier, arguments.metadata_only, arguments.reason)
    if arguments.command == "invoke":
        return invoke_repository(
            root,
            arguments.task,
            arguments.skill,
            arguments.capability,
            arguments.architecture,
            arguments.environment,
            arguments.risk,
            arguments.evidence_gap,
            arguments.task_id,
            arguments.spec_id,
            arguments.context_digest,
            arguments.fresh_context,
            arguments.tool,
            arguments.model_capability,
            arguments.model,
            arguments.provider,
            arguments.network_available,
            arguments.data_classification,
            arguments.model_data_classification,
            arguments.approval,
            arguments.platform,
            arguments.candidate_limit,
            arguments.limit,
            arguments.json,
        )
    if arguments.command == "outcome":
        return record_outcome(
            root,
            arguments.identifier,
            arguments.outcome,
            arguments.context_tokens,
            arguments.execution_cost,
            arguments.evidence,
            arguments.invocation_id,
            arguments.verification,
        )
    if arguments.command == "skill-stats":
        return skill_stats(root, arguments.json)
    if arguments.command == "semantic":
        if arguments.semantic_command == "validate":
            return semantic_validate(root, Path(arguments.input).resolve(), arguments.json)
        if arguments.semantic_command == "impact":
            return semantic_impact(Path(arguments.input).resolve())
        if arguments.semantic_command == "publish":
            return semantic_publish(root, Path(arguments.input).resolve())
        if arguments.semantic_command == "rollback":
            return semantic_rollback(root, arguments.release_id)
    if arguments.command == "task-packet":
        return show_task_packet(root, arguments.task_id)
    if arguments.command == "tracker-export":
        output = arguments.output.resolve() if arguments.output else None
        return tracker_export(root, arguments.provider, output)
    if arguments.command == "tracker-submit":
        return tracker_submit(
            root,
            arguments.provider,
            arguments.endpoint,
            arguments.token_env,
            arguments.confirm_external_write,
            arguments.defaults.resolve() if arguments.defaults else None,
        )
    if arguments.command == "providers":
        print(json.dumps({"entry_point_group": "aae.semantic_providers", "providers": sorted(provider_entry_points())}, indent=2))
        return 0
    if arguments.command == "accounting":
        return accounting_repository(root, arguments.json)
    if arguments.command == "governed-run":
        return governed_run_repository(
            root,
            arguments.task,
            arguments.task_id,
            arguments.skill,
            arguments.capability,
            arguments.acceptance,
            arguments.evidence,
            arguments.approval,
            arguments.json,
        )
    if arguments.command == "model-route":
        return model_route_repository(
            root,
            arguments.capability,
            arguments.data_classification,
            arguments.network_available,
            arguments.location,
        )
    if arguments.command == "retrievers":
        print(
            json.dumps(
                {
                    "entry_point_group": "aae.skill_retrievers",
                    "retrievers": sorted(skill_retriever_entry_points()),
                },
                indent=2,
            )
        )
        return 0
    if arguments.command == "skill-evaluate":
        return lifecycle_repository(root, arguments.registry_id, arguments.propose)
    if arguments.command == "skill-graph":
        return json_artifact_repository(root, "skill-graph", None)
    if arguments.command == "ci-policy":
        return json_artifact_repository(root, "ci-policy", arguments.provider)
    if arguments.command == "trace-export":
        return json_artifact_repository(root, "trace-export", None)
    return 2
