# Adaptive Agentic Engineering Entry Instructions

This repository uses Adaptive Agentic Engineering (AAE).

Before consequential work:

1. Run `aae compile` if `.aae/runtime/compiler-request.md` is missing or stale.
2. Read the compiler request and only the intent sources relevant to the next decision.
3. Treat `.aae/intent/` as open-ended; do not ignore a source because its filename is unfamiliar.
4. Apply matching `.local.md` overlays after shared sources for local execution, but do not treat Markdown as an enforcement boundary.
5. Distinguish confirmed facts, repository-backed inferences, proposals, and unknowns.
6. Ask focused questions whenever the answer materially affects correctness, safety, scope, architecture, or downstream work.
7. For meaningful features, use requirements → design → executable tasks. Update task state only when its completion contract is satisfied.
8. Use the smallest workflow that owns the next action. Research only demonstrated evidence gaps.
9. Give independent challengers/reviewers fresh bounded evidence rather than the author's full conversational framing.
10. Persist durable facts, decisions, constraints, evidence, and lessons into authoritative artifacts, then compact or discard stale working context.
11. For bounded long-running work, keep substantive progress updates compact: report phase, completed/total comparable units when meaningful, current activity, remaining work, next milestone, and blockers. Do not invent percentages from elapsed time or uneven checklist items.

The active tool may generate additional runtime instructions, but generated artifacts must preserve provenance to human-readable intent.
