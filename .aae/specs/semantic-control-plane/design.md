# Semantic Control Plane Design

The dependency-free `aae.semantic` module defines and validates schema v1, builds dependency/impact graphs and task packets, stages immutable releases, and atomically advances a small active-release pointer. Provider implementations are discovered only through the `aae.semantic_providers` entry-point group; no provider is bundled or contacted implicitly.

The `aae.control` module matches task clues against bounded skill advertisements, applies basic enforceable safety checks, digest-checks the selected procedure, and persists one versioned invocation record. Runtime roles are ephemeral bindings, not durable agent identities.

Tracker adapters produce deterministic offline request bodies. Live submission
is a separate explicit command that reads a named credential environment
variable, requires external-write confirmation, validates HTTPS, and keeps
authorization outside canonical request/result artifacts. Semantic skill retrieval,
lifecycle evaluation, historical-use graphs, and model policy are deliberately
outside this v1 slice. Distributed execution remains outside this slice.
