# AAE Roadmap

Checkboxes denote working repository integrations. Items marked partial or
unintegrated remain open even when an isolated function or contract exists. See
[`docs/architecture-audit-0.3.md`](docs/architecture-audit-0.3.md) for strict
accounting.

## Phase 0 — Reference bootstrap

- [x] Open-world Markdown intent plane
- [x] Local overlay convention
- [x] Deterministic source manifest
- [x] Semantic compiler request
- [x] Codex and Copilot entry adapters
- [x] HVE Core interoperability guidance
- [x] Spec templates and task completion contract
- [x] CI validation and tests
- [x] Native skill advertisements and multi-source registry adapters
- [x] Deterministic metadata-first skill discovery
- [x] Portable skill and registry content identity separated from runtime identity
- [x] Source trust, approval, provenance, and integrity separated from source scope
- [x] Provenance-bearing CapabilityDemand, CandidateSet, and SelectionDecision
- [x] Policy-gated, digest-bound InvocationPlan with deferred procedure loading
- [x] Durable InvocationRecord and verified outcome join
- [x] Policy-negative tests and concurrent-safe telemetry publication
- [x] Versioned v1 JSON Schemas and two-version CI matrix

## Phase 1 — Semantic compiler

- [x] Provider-neutral semantic intermediate representation
- [ ] Pluggable semantic-provider entry-point interface (IMPLEMENTED_BUT_UNINTEGRATED)
- [x] Incremental impact graph and downstream impact delta
- [x] Generated artifact provenance
- [x] Conflict and clarification workflow
- [x] Atomic publication and rollback
- [x] Provider-neutral capability requirement representation
- [x] Skill invocation provenance in generated task packets

## Phase 2 — Adaptive execution

- [x] Deterministic explicit-risk and evidence-gap classifier
- [ ] Bounded task-packet compiler (PARTIAL: no byte/token/item budget)
- [x] Capability router integration with bounded task packets
- [ ] Ephemeral specialist routing as an optional skill execution binding (PARTIAL)
- [ ] Independent challenge and review packets (PARTIAL: packet exists; execution independence is not attested)
- [x] Model profile registry and fallback policy
- [ ] Pluggable semantic skill retrieval bounded to the deterministic candidate set (IMPLEMENTED_BUT_UNINTEGRATED)
- [x] Versioned skill invocation and outcome telemetry
- [ ] Evaluation corpus and governed lifecycle promotion workflow (PARTIAL: proposal-only promotion exists; no durable corpus)
- [x] Architecture dependency graph augmentation
- [ ] Historical-use graph augmentation for skill retrieval (IMPLEMENTED_BUT_UNINTEGRATED)

## Phase 3 — Integrations

- [x] Azure DevOps offline work-item payload adapter
- [ ] GitHub Issues/Projects adapter (PARTIAL: Issues exists; Projects does not)
- [x] Jira offline issue payload adapter
- [x] Credentialed live tracker submission adapters
- [x] CI policy-generation adapters
- [x] OpenTelemetry-compatible GenAI trace export

## Private experiment

The presentation narrator is intentionally not part of this repository or roadmap. It can privately consume ordinary public lifecycle events without becoming an AAE product capability.
