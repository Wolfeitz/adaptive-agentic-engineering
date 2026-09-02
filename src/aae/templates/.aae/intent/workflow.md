# Engineering Workflow

<!-- Describe planning depth, requirements/design/task expectations, human approval boundaries, review independence, release practices, and exceptions. AAE uses the smallest workflow appropriate to risk and complexity. -->

Use evidence-driven, context-aware, plan-before-execution practices appropriate to the risk and complexity of the work.

For each bounded task, identify needed capabilities and use registry discovery rather than relying on prior knowledge of installed skills. Select the smallest relevant set, load only selected procedures, and create an ephemeral specialist only when expertise or independence warrants it. Candidate generation may propose reusable procedures, but skill promotion requires project governance.

## Progress telemetry

**Progress telemetry:** on

When enabled for bounded long-running work, keep substantive progress updates compact and expose real plan or runtime state: current phase, completed/total comparable units when meaningful, current activity, remaining work, next milestone, and blockers. Prefer measurable counts over estimated percentages. Do not infer completion from elapsed time or checklist counts when units vary materially in cost.

A matching `workflow.local.md` may turn this off or specialize its presentation for the current developer or execution environment.
