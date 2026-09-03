# Architecture

<!-- Describe current system boundaries, components, dependencies, architectural principles, authoritative ADRs, established patterns, known exceptions, and representative implementations. Existing architecture constrains proposed design. -->

The architecture has not yet been documented here.

AAE workflows discover reusable procedures from a normalized skill registry; roles and agents remain ephemeral runtime bindings. Add project skills under `.aae/skills/` and configure enterprise, project, or local sources explicitly. Discovery remains advertisement-first and bounded. Use `aae invoke` so required-tool, destructive-approval, and fresh-context checks run before a full procedure loads.

Use `.aae/hooks.json` for deterministic `on` plus optional `paths` routing. Each rule requests one skill or runs one direct check. Keep rules idempotent, payload-redacted, and disabled until the project intentionally enables them.
