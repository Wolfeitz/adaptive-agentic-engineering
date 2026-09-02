# Architecture

<!-- Describe current system boundaries, components, dependencies, architectural principles, authoritative ADRs, established patterns, known exceptions, and representative implementations. Existing architecture constrains proposed design. -->

The architecture has not yet been documented here.

AAE workflows request capabilities from the normalized skill registry. Durable skills advertise reusable procedures; roles and agents remain ephemeral runtime bindings. Add project skills under `.aae/skills/` and configure approved enterprise, runtime, or local sources explicitly. Discovery must remain metadata-first and bounded. Selection is not permission: load full procedures only through a policy-allowed `InvocationPlan` bound to portable skill and registry identities.
