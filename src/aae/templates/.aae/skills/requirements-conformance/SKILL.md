---
name: requirements-conformance
description: Reconcile the original request and applicable authoritative requirements against planning, implementation, or PR-review evidence, including requirements omitted from generated todos.
---
# Requirements Conformance

## Outcome
Prove the **coverage of the original request**, not merely that all generated tasks are marked done. Complements architecture-review and acceptance-verification skills and HVE's existing reviewer; it does not replace them.

## Procedure
1. Preserve the original authorized request, work-item acceptance, and relevant repository/organization guidance independently of generated tasks. For PR reviews also record repository, PR, base/head revisions.
2. Identify authoritative architecture, coding, security, testing and business sources relevant to the **actual change surface**. Discover detailed child pages/diagrams when applicable. Do not infer that reading an overview satisfies review of relevant details.
3. List applicable requirements with unique identifiers and source/revision. Distinguish confirmed standards, inference needing approval, historical debt and exceptions. Ask about consequential uncertainty.
4. Map each requirement to changed paths, plan steps or review obligations and evidence that demonstrates actual conformance or violation. A completed document-retrieval task is not conformance evidence.
5. Record for each requirement: `verified`, `violated`, `not_applicable`, `deferred` with actual authorization, `blocked`, or `unassessed`; include rationale and provenance. Never invent approval.
6. Reconcile the source-derived requirement list against **original scope**, independently of task/todo statuses, immediately before declaring work or review coverage complete. If a PR head changes, revalidate impacted evidence.
7. Run `python -m aae.requirements_coverage RECORD.json` when available for structural checks. The validator cannot judge whether the reviewer identified *all* applicable sources or understood them correctly. Use HVE's native planning/review phase to challenge the semantic coverage where supported.
8. Report two independent outcomes: **review coverage** (complete/partial) and **engineering recommendation** (approve/request changes/accept approved risk/not evaluated). Complete review coverage can properly conclude that the PR violates standards.

## Integration
Use through the native skill registry or HVE's existing research/planning/review workflow. Do not create competing permanent reviewer agents solely to invoke this skill. Prove actual native discovery/invocation separately; installing a file alone is not sufficient.

## Completion contract
All applicable source-derived requirements have a supported disposition; unresolved evidence is explicit; no silent promotion of generated todos to source authority; PR revision evidence is fresh. A partial handoff is allowed and must never be labeled complete.
