"""Evidence-based full-bootstrap completion assessment.

This module reports completeness; it is not a security or IDE enforcement gate.
"""
REQUIRED = (
    "project_discovery", "environment_discovery", "architecture_assessment",
    "requirements_readiness", "existing_capability_inventory",
    "external_capability_research", "capability_reconciliation",
    "agent_assessment", "skill_assessment", "hook_assessment", "mcp_assessment",
    "workflow_integration", "native_invocation_verification",
    "original_scope_reconciliation", "installation_manifest",
)
VALID = {"verified", "not_applicable", "blocked", "awaiting_decision", "incomplete"}


def assess(record):
    """Return truthful status; only evidenced verification can close an outcome."""
    if record.get("requested_operation") != "project-bootstrap":
        return {"state": "NOT_FULL_BOOTSTRAP", "errors": [], "outstanding": []}
    outcomes = record.get("outcomes")
    outcomes = outcomes if isinstance(outcomes, dict) else {}
    errors, outstanding = [], []
    if not record.get("original_request"):
        errors.append("Original requested outcome missing")
    for name in REQUIRED:
        item = outcomes.get(name, {})
        status = item.get("status") if isinstance(item, dict) else None
        if status not in VALID:
            errors.append(f"{name}: missing or invalid disposition")
        elif status == "verified" and not item.get("evidence"):
            errors.append(f"{name}: verification lacks evidence")
        elif status == "not_applicable" and not item.get("reason"):
            errors.append(f"{name}: not-applicable lacks a reason")
        elif status not in ("verified", "not_applicable"):
            outstanding.append(name)
    return {
        "state": "COMPLETE" if not errors and not outstanding else "PARTIAL",
        "errors": errors, "outstanding": outstanding,
    }
