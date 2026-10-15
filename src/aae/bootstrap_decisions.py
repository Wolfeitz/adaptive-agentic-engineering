"""Lightweight bootstrap decision ledger validation.

Assessment is mandatory; creating any particular capability is not.
No network access, IDE enforcement, or project mutation occurs here.
"""
from .bootstrap_acceptance import REQUIRED

DECISIONS = frozenset({
    "reuse", "configure", "supplement", "extend", "merge", "replace",
    "create", "retain", "defer", "not_applicable", "blocked",
})
READY = frozenset({"reuse", "configure", "supplement", "extend", "merge", "replace", "create", "retain"})
CAPABILITIES = frozenset({
    "agent_assessment", "skill_assessment", "hook_assessment", "mcp_assessment",
    "existing_capability_inventory", "capability_reconciliation",
})


def assess_decisions(record):
    """Report assessment gaps separately from chosen actions and pending verification."""
    if record.get("requested_operation") != "project-bootstrap":
        return {"state": "NOT_FULL_BOOTSTRAP", "errors": [], "pending": []}
    outcomes = record.get("outcomes") if isinstance(record.get("outcomes"), dict) else {}
    errors, pending = [], []
    if not record.get("original_request"):
        errors.append("Original requested outcome missing")
    for key in REQUIRED:
        item = outcomes.get(key)
        if not isinstance(item, dict):
            errors.append(f"{key}: not assessed")
            continue
        decision = item.get("decision")
        if decision not in DECISIONS:
            errors.append(f"{key}: no valid decision")
            continue
        if not item.get("reason"):
            errors.append(f"{key}: decision lacks rationale")
        if not item.get("evidence"):
            errors.append(f"{key}: no assessment evidence")
        if decision in ("blocked", "defer"):
            if not item.get("next_action"):
                errors.append(f"{key}: unresolved item lacks next action")
            pending.append(key)
        elif decision in READY and item.get("verification") != "verified":
            pending.append(key)
        # A justified not-applicable decision is complete without installation.
    return {"state": "PARTIAL" if errors or pending else "COMPLETE",
            "errors": errors, "pending": pending}
