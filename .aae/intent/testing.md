# Testing and Verification

These are project preferences for cooperative agents, not enforcement boundaries. A matching `testing.local.md` may override them locally.

**Automated test creation:** on

**Regression tests for defects:** on

**Coverage reporting:** on when supported by the existing toolchain

**Numeric coverage threshold:** none by default

**Resource-lifecycle verification:** risk-triggered

Create automated tests for new or changed executable behavior at the cheapest stable level that verifies acceptance criteria and material failure paths. Add a regression test for a corrected defect when practical. Existing relevant tests must continue to pass.

For repeated, concurrent, batched, or long-running behavior—and code that creates, retains, pools, subscribes to, or disposes resources—evaluate bounded repeated-operation or soak tests. Check memory and other applicable resources after warm-up for sustained unexplained growth, failure to reach a steady state, and incomplete cleanup across success, failure, cancellation, and disposal paths. Use profiling or instrumentation evidence when a deterministic test is impractical.

Coverage is diagnostic evidence rather than proof of correctness. Report it where practical, but do not invent a numeric threshold. If testing is disabled or impractical, record alternative verification evidence.
