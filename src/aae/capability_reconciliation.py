"""Deterministic capability coverage accounting for AAE project bootstrap.

Inputs are recorded assessments, not trusted claims of actual IDE invocation.
HVE and managed assets are protected from implicit replacement.
"""
from collections import defaultdict

CATEGORIES = ("agents", "skills", "hooks", "mcp", "instructions", "ci")
DECISIONS = {"reuse", "configure", "supplement", "extend", "merge", "replace",
             "create", "retain_separately", "not_applicable"}


def reconcile(requirements, installed, decisions):
    """Return explicit gaps and conflicts without changing installed components.

    Each requirement has id and category, optionally an existing provider_id.
    Each installed item has id, category, owner, and scope.
    Each decision has requirement_id, action, target_id, reason and optional approval.
    """
    errors, gaps, actions = [], [], []
    by_id = {}
    for item in installed:
        if item.get("id") in by_id:
            errors.append("Duplicate installed capability: " + str(item.get("id")))
        by_id[item.get("id")] = item
    indexed = defaultdict(list)
    for decision in decisions:
        indexed[decision.get("requirement_id")].append(decision)
    for req in requirements:
        rid = req.get("id")
        category = req.get("category")
        if not rid or category not in CATEGORIES:
            errors.append("Invalid capability requirement")
            continue
        matches = indexed[rid]
        if len(matches) != 1:
            gaps.append(rid)
            errors.append(f"{rid}: requires one explicit disposition")
            continue
        decision = matches[0]
        action = decision.get("action")
        if action not in DECISIONS or not decision.get("reason"):
            errors.append(f"{rid}: invalid or unexplained decision")
            gaps.append(rid)
            continue
        target = by_id.get(decision.get("target_id"))
        if action in ("reuse", "configure", "supplement", "extend", "merge", "replace", "retain_separately"):
            if target is None or target.get("category") != category:
                gaps.append(rid)
                errors.append(f"{rid}: existing target missing or wrong category")
                continue
        if action in ("replace", "merge", "extend", "configure"):
            if target.get("owner") in ("hve", "organization", "team", "managed") and not decision.get("approval"):
                gaps.append(rid)
                errors.append(f"{rid}: managed capability modification requires authorization")
                continue
        if action in ("create", "supplement", "extend", "merge", "replace", "configure") and not decision.get("implementation_evidence"):
            gaps.append(rid)
        if action == "reuse" and not decision.get("discovery_evidence"):
            gaps.append(rid)
        if action == "not_applicable" and not decision.get("reason"):
            gaps.append(rid)
        actions.append({"requirement_id": rid, "action": action, "target_id": decision.get("target_id")})
    return {"state": "COVERED" if not gaps and not errors else "PARTIAL",
            "errors": errors, "gaps": sorted(set(gaps)), "actions": actions}
