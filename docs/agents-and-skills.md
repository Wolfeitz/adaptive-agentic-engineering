# Agents and Skills

AAE includes eight starter skills and zero permanent named agents.

A skill is a durable procedure. An agent is a runtime executor. Selecting a skill normally gives its procedure to the current agent; it does not create a new specialist.

AAE recognizes only three useful runtime distinctions:

| Role | Kind | Use |
| --- | --- | --- |
| current-agent | ephemeral role | Normal skill executor |
| independent-reviewer | fresh ephemeral role | Used when a skill explicitly requires independence |
| deterministic-control-plane | code, not an agent | Indexes, matches, evaluates hook controls, and records |

The starter library covers repository reconnaissance, bounded context, implementation preflight, resource lifecycle, runtime diagnosis, acceptance verification, independent review, and lesson extraction. These are reusable procedures, not departments in an AI org chart.

`aae accounting` reports the live registry, basic safety properties, and observed invocation counts. It deliberately does not report invented lifecycle rankings, autonomous promotion, semantic skill graphs, or a permanent planner/reviewer/tester cast.

An independent reviewer is gated on a succeeded target invocation and fresh
context. AAE records the target identity but does not inject its semantic
verdict into reviewer context.

AAE does not automatically create agents or skills. A runtime may use a fresh specialist when independence or expertise genuinely warrants it. A repeated procedure becomes a new skill only when a person or an explicitly governed future workflow chooses to package it.
