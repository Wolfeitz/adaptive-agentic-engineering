# AAE Bootstrap Requirements

- **REQ-001:** Install a portable AAE starter into greenfield or existing repositories without overwriting existing files.
- **REQ-002:** Discover any Markdown under the intent plane rather than relying on a filename whitelist.
- **REQ-003:** Process matching `.local.md` sources after shared sources and keep local state out of Git.
- **REQ-004:** Produce a deterministic manifest and bounded semantic compiler request without binding to one model provider.
- **REQ-005:** Supply Codex, Copilot, HVE Core, specification, context-hygiene, routing, and observability guidance.
- **REQ-006:** Validate core repository hygiene in local and CI execution.
- **REQ-007:** Seed configurable testing preferences that default automated and regression testing on, avoid invented coverage thresholds, and trigger resource-lifecycle verification when risk warrants it.
- **REQ-008:** Index native, enterprise, project, and local skill advertisements into a normalized registry without loading full procedures for discovery.
- **REQ-009:** Produce a bounded skill shortlist by matching task clues to names, descriptions, `when_to_use`, and optional capability labels.
- **REQ-010:** Validate the minimal skill advertisement and procedure reference while keeping role allocation separate from skill selection.
- **REQ-011:** Produce portable skill and registry content identities that exclude machine paths while retaining a separate local runtime-instance identity.
- **REQ-012:** Keep discovery and invocation inspectable without requiring a formal capability ontology, semantic reranker, or policy language.
- **REQ-013:** Deny procedure loading when required tools are absent, destructive approval is missing, fresh context is required but absent, or content changed after discovery.
- **REQ-014:** Persist a durable invocation record joining task/spec identity, advertisement shortlist, selection, basic safety checks, evidence, outcome, verification, and cost telemetry.
- **REQ-015:** Publish four small versioned schemas, support concurrent telemetry writers, and validate Python 3.10 plus the current supported Python in CI.
- **REQ-016:** Evaluate simple event rules containing `on`, optional `paths`, and exactly one `request_skill` or `run_check`, with idempotency, payload redaction, trigger provenance, and destructive-check approval.
