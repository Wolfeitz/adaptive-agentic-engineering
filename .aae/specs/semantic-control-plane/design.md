# Semantic Control Plane Design

The dependency-free `aae.semantic` module defines and validates schema v1, builds dependency/impact graphs and task packets, stages immutable releases, and atomically advances a small active-release pointer. Provider implementations are discovered only through the `aae.semantic_providers` entry-point group; no provider is bundled or contacted implicitly.

The `aae.control` module converts task clues into a provenance-bearing capability demand, delegates bounded ranking to the skill registry, applies project invocation policy, loads an explicitly selected procedure only after approval, and persists one versioned invocation record. Runtime roles are bindings inside that record, not durable agent identities.

Tracker adapters produce deterministic offline request bodies. Live submission
is a separate explicit command that reads a named credential environment
variable, requires external-write confirmation, validates HTTPS, and keeps
authorization outside canonical request/result artifacts. Model routing,
bounded semantic retrieval, advisory lifecycle evaluation, historical-use
graphs, CI policies, and trace export remain deterministic controls.
Distributed execution remains outside this slice.
