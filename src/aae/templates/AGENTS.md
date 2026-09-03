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
11. Honor the effective shared-plus-local workflow setting for progress telemetry. Unless disabled or specialized there, bounded long-running work should report compact phase, completed/total comparable units when meaningful, current activity, remaining work, next milestone, and blockers—without inventing percentages from elapsed time or uneven checklist items.
12. Honor the effective testing settings. When enabled, create tests with changed behavior and regression tests for defects when practical. For resource-owning, repeated, concurrent, batched, or long-running code, evaluate repeated-operation or soak verification for leaks and incomplete cleanup. Treat coverage as diagnostic unless project intent defines a threshold. Record alternative evidence when tests are disabled or impractical.
13. Use `aae discover` to search installed skill advertisements before relevant work. Do not assume prior knowledge of every skill or preload every procedure.
14. Inspect advertisements with `aae skill --metadata-only`. Use `aae invoke` to check required tools, destructive approval, and fresh-context requirements before loading a procedure.
15. Treat agents as ephemeral executors and skills as durable procedures. Selecting a skill does not automatically create a specialist.
16. Do not automatically create or promote skills from observed behavior. Package a repeated procedure only through an explicit project decision.
17. Honor enabled `.aae/hooks.json` rules. A hook requests one skill or runs one configured check when its event and optional path globs match. Preserve idempotency and trigger provenance.
18. Keep criterion authority explicit. Agents assess only semantic criteria; configured hook checks provide deterministic-control results. Do not invent or self-report a control result, and do not start an independent review unless its target invocation succeeded.

The active tool may generate additional runtime instructions, but generated artifacts must preserve provenance to human-readable intent.
