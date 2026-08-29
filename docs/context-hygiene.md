# Context Hygiene

Context is a scarce runtime resource, not durable project memory.

AAE deliberately acquires only the evidence needed for the next action, uses it for the current decision, persists durable facts and lessons into authoritative artifacts, and then compacts or discards stale working context. Subsequent work reconstructs fresh bounded context from sources of truth.

Operationally:

- more context is not inherently better context;
- reduce token ingress as aggressively as token accumulation;
- prefer targeted reads, bounded evidence packets, concise tool output, and fresh independent-review contexts;
- persist, then compact;
- treat compaction as an epistemic and repeatability control, not merely a cost optimization.
