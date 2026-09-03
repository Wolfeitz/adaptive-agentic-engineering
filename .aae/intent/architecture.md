# Architecture

The human-readable intent plane is authoritative project input. Deterministic code discovers, hashes, orders, validates, and watches sources. Semantic interpretation is model-agnostic and performed through portable compiler contracts. Tool-specific adapters remain thin.

The control plane includes a small skill registry. Durable skills advertise a name, description, `when_to_use` clues, and a separate procedure; roles and agents remain ephemeral runtime bindings. AAE searches advertisements, returns a bounded shortlist, checks required tools plus destructive approval or fresh-context requirements, then loads the selected procedure. Portable content identities exclude local runtime paths. AAE does not automatically create or promote skills.

A deterministic hook layer maps an event and optional path globs to exactly one requested skill or direct check. Event and invocation records retain trigger provenance without persisting raw payload. Webhook authentication and transport remain adapter responsibilities.
