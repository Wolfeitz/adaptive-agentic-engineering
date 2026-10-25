"""Structural requirements coverage check; semantic applicability remains reviewer-owned."""
from __future__ import annotations
import json
from pathlib import Path

CLOSED = {"verified", "violated", "not_applicable", "deferred"}
ALL = CLOSED | {"blocked", "unassessed"}

def check(record: dict) -> dict:
    """Validate requirements independently of generated task completion."""
    errors = []
    if not isinstance(record, dict):
        return {"coverage": "partial", "errors": ["record must be an object"]}
    request = record.get("original_request")
    if not isinstance(request, str) or not request.strip():
        errors.append("missing original_request")
    required = record.get("applicable_requirements")
    results = record.get("results")
    if not isinstance(required, list) or not required or not all(isinstance(x, str) and x.strip() for x in required):
        errors.append("applicable_requirements must contain source-derived identifiers")
        required = []
    if len(required) != len(set(required)):
        errors.append("duplicate requirement identifiers")
    if not isinstance(results, dict):
        results = {}
    for req in required:
        row = results.get(req)
        if not isinstance(row, dict):
            errors.append(f"{req}: missing disposition")
            continue
        state = row.get("status")
        if state not in ALL:
            errors.append(f"{req}: invalid disposition")
            continue
        if state in {"blocked", "unassessed"}:
            errors.append(f"{req}: not fully assessed")
            continue
        if not row.get("source"):
            errors.append(f"{req}: source evidence missing")
        if not row.get("reason"):
            errors.append(f"{req}: rationale missing")
        if state in {"verified", "violated"}:
            if not row.get("change_evidence") or not row.get("assessment_evidence"):
                errors.append(f"{req}: implementation assessment evidence missing")
        if state == "deferred" and not row.get("approval"):
            errors.append(f"{req}: explicit approved deferral missing")
    if record.get("kind") == "pr-review":
        if not record.get("repository") or not record.get("pr"):
            errors.append("PR review identity missing")
        if not record.get("head_sha"):
            errors.append("PR head SHA missing")
        elif record.get("current_head_sha") != record.get("head_sha"):
            errors.append("stale or unverified PR head")
    return {"coverage": "complete" if not errors else "partial", "errors": errors}

def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("record")
    args = parser.parse_args(argv)
    try:
        data = json.loads(Path(args.record).read_text(encoding="utf-8"))
        result = check(data)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        result = {"coverage": "partial", "errors": [str(exc)]}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["coverage"] == "complete" else 1

if __name__ == "__main__":
    raise SystemExit(main())
