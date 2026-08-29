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
import time
from typing import Iterable


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


def discover_intent(root: Path) -> list[dict[str, object]]:
    intent_root = root / INTENT_DIRECTORY
    if not intent_root.exists():
        return []

    paths = sorted(
        path
        for path in intent_root.rglob("*.md")
        if path.is_file() and not path.name.endswith(".example.md")
    )
    sources: list[dict[str, object]] = []
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
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def compiler_request(root: Path, sources: list[dict[str, object]]) -> str:
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
            "## Expected result",
            "",
            "Produce a concise semantic change plan, diagnostics, impacted capabilities, and the bounded runtime artifacts required by the active tool. Do not preload all sources into unrelated future tasks.",
            "",
        ]
    )
    return "\n".join(lines)


def init_repository(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    template_root = importlib.resources.files("aae.templates")
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

    manifest = {
        "schema_version": 1,
        "project_root": str(root.resolve()),
        "source_count": len(sources),
        "sources": sources,
    }
    write_json(root / RUNTIME_DIRECTORY / "effective-sources.json", manifest)
    request_path = root / RUNTIME_DIRECTORY / "compiler-request.md"
    request_path.write_text(compiler_request(root, sources), encoding="utf-8")
    if not quiet:
        shared = sum(not bool(source["local"]) for source in sources)
        local = len(sources) - shared
        print(f"Prepared {len(sources)} intent source(s): {shared} shared, {local} local.")
        print(f"Compiler request: {request_path}")
    return 0


def git_tracked_local_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "*.local.md", ".aae/**/*.local.md"],
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

    required_gitignore = ["*.local.md", ".aae/runtime/"]
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


def watched_state(root: Path) -> tuple[tuple[str, int, int], ...]:
    intent_root = root / INTENT_DIRECTORY
    if not intent_root.exists():
        return ()
    return tuple(
        (path.relative_to(root).as_posix(), path.stat().st_mtime_ns, path.stat().st_size)
        for path in sorted(intent_root.rglob("*.md"))
        if path.is_file()
    )


def watch_repository(root: Path, interval: float) -> int:
    previous: tuple[tuple[str, int, int], ...] | None = None
    print(f"Watching {root / INTENT_DIRECTORY}; press Ctrl+C to stop.")
    try:
        while True:
            current = watched_state(root)
            if current != previous:
                compile_repository(root, quiet=previous is not None)
                if previous is not None:
                    print("Intent change detected; compiler request refreshed.")
                previous = current
            time.sleep(interval)
    except KeyboardInterrupt:
        print("Stopped.")
        return 0


def doctor(root: Path) -> int:
    print(f"AAE version: 0.1.0")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Project root: {root.resolve()}")
    print(f"Intent directory: {'present' if (root / INTENT_DIRECTORY).exists() else 'missing'}")
    print(f"Git repository: {'yes' if (root / '.git').exists() else 'no'}")
    print(f"Intent sources: {len(discover_intent(root))}")
    return validate_repository(root)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="aae", description="Adaptive Agentic Engineering bootstrap")
    commands = root.add_subparsers(dest="command", required=True)

    for name in ("init", "compile", "validate", "doctor"):
        command = commands.add_parser(name)
        command.add_argument("path", nargs="?", default=".")

    watch = commands.add_parser("watch")
    watch.add_argument("path", nargs="?", default=".")
    watch.add_argument("--interval", type=float, default=1.0)
    return root


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
    return 2
