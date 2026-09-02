# Semantic Control Plane Tasks

## SCP-T-001 — Semantic schema and provider protocol

- **Status:** verified
- **Requirements:** SCP-REQ-001, SCP-REQ-002
- **Evidence:** `aae.semantic` validation and provider-contract tests.

## SCP-T-002 — Impact, task, and review packets

- **Status:** verified
- **Requirements:** SCP-REQ-003
- **Evidence:** exact graph, incremental delta, classification, skill-provenance, and review-packet tests.

## SCP-T-003 — Atomic release and rollback

- **Status:** verified
- **Requirements:** SCP-REQ-004
- **Evidence:** idempotent publish, manifest verification, second release, and rollback integration test.

## SCP-T-004 — Policy-checked invocation records

- **Status:** verified
- **Requirements:** SCP-REQ-005, SCP-REQ-006
- **Evidence:** deterministic selection, denied-load, allowed-load, persisted outcome, and invalid-policy tests.

## SCP-T-005 — Integrations and accounting

- **Status:** verified
- **Requirements:** SCP-REQ-007, SCP-REQ-008
- **Evidence:** three offline tracker shapes, confirmation-gated injected live transport, token-redaction tests, and seeded-project accounting.
