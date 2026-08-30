# Testing and Verification

These are project preferences for cooperative agents, not enforcement boundaries. Change them here for the repository or in a matching `testing.local.md` for one developer or execution environment.

**Automated test creation:** on

**Regression tests for defects:** on

**Coverage reporting:** on when supported by the existing toolchain

**Numeric coverage threshold:** none by default; projects and organizations may define one

**Resource-lifecycle verification:** risk-triggered

When automated test creation is enabled, new or changed executable behavior should receive automated verification at the cheapest stable level that demonstrates its acceptance criteria and material failure paths. Existing relevant tests should continue to pass. Defect fixes should include a regression test that fails without the fix when practical.

When code creates, retains, pools, subscribes to, or disposes resources—or runs repeatedly, concurrently, in batches, or for long periods—evaluate whether bounded repeated-operation or soak verification is needed. Relevant resources include memory, handles, sockets, streams, subscriptions, timers, workers, processes, caches, buffers, GPU resources, and temporary files. Exercise successful, failed, cancelled, and cleanup paths as applicable. Look for sustained unexplained growth after warm-up, failure to return to an expected steady state, or resources that remain reachable or open. Use profiling or instrumentation evidence when a deterministic automated assertion is impractical.

Coverage is diagnostic evidence, not proof of correctness. Report line and branch coverage when practical, investigate material regressions, and prioritize acceptance-criteria, boundary, failure-path, concurrency, and resource-lifecycle coverage. Do not invent or impose a numeric threshold unless effective project intent defines one.

When automated tests are disabled or impractical, task completion still requires explicit alternative verification evidence.
