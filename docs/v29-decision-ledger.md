# v29 decision ledger: assess everything applicable, mandate no artifact

A full AAE bootstrap has an explicit **assessment obligation**, not an artifact quota.

- Every applicable engineering area gets evidence, a decision and a rationale. Inspect sources first; ask the owner when consequential ambiguity remains.
- Decisions: `reuse`, `configure`, `supplement`, `extend`, `merge`, `replace`, `create`, `retain`, `defer`, `not_applicable`, `blocked`.
- A decision to create an agent, skill, hook, MCP integration, or any other mechanism is **conditional on fit, value, trust, authority and project risk**. Having zero new agents can be correct when supported by an actual assessment. Silently skipping that assessment is not.
- Existing HVE and team capabilities are inventoried before duplication. AAE does not override managed assets without authorization.
- Discovery is proportional: a small personal project needs less depth than an enterprise migration. No arbitrary ticket, branch naming, repository structure, or technology mandate.
- Do not confuse an artifact's presence with runtime invocation. For adopted mechanisms, track verification; unverified integration remains pending.
- One block does not stop unrelated safe work. `blocked` and `defer` must specify a next action.
- The original authorized full-bootstrap outcome must survive planning. A smaller pilot or developer setup is not an equivalent completion.
- Research current external agents, skills, hooks, MCP servers and relevant stack practices when meaningful and permitted; absence of an installation does not mean absence of research.

The executable `aae.bootstrap_decisions.assess_decisions(record)` validates an explicit ledger record, reporting missing decisions and outstanding verification. It is a **reporting validator**, not an automatic skill router, native IDE hook, or authority to mutate project files.

This is deliberately one small module and tests, not another orchestration service.

## Completion and scoped exclusions

Every required outcome must be assessed; implementation of an individual checklist
item remains conditional on that reasoned assessment. `not_applicable` can close a
scoped exclusion, but cannot exempt `original_scope_reconciliation`: the full
request and all exclusions must actually be reconciled before claiming completion.
In the acceptance record, reconciliation needs `status: verified` and evidence.
In the decision ledger, it needs a non-exempt decision (for example `retain`), a
rationale, assessment evidence, and `verification: verified`. A blocked, deferred,
or unverified reconciliation keeps the result partial.

For example, an assessed project may need no new MCP integration: record the
reasoned exclusion, then verify that this and other decisions still satisfy the
original full-bootstrap request. Zero new artifacts can be valid; skipping the
scope review or silently substituting a limited pilot cannot. These validators
check recorded dispositions and evidence presence, not the truth of supplied prose.
