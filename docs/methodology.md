# Adaptive Agentic Engineering Methodology

AAE is intent-grounded, evidence-driven, spec-decomposed, verification-backed, and tool-independent.

## Core lifecycle

1. Acquire only evidence needed for the next decision.
2. Research only demonstrated evidence gaps.
3. Capture requirements and challenge material ambiguity.
4. Design within existing architecture, environment, standards, and constraints.
5. Decompose meaningful work into traceable executable tasks.
6. Execute one bounded task packet at a time.
7. Verify completion contracts with deterministic evidence and independent review proportional to risk.
8. Persist durable facts, decisions, constraints, lessons, and evidence.
9. Compact or clear stale working context and reconstruct future context from authoritative sources.

## Adaptive topology

AAE asks what evidence, independence, expertise, model capability, human oversight, and verification the next decision requires. It then activates the minimum useful topology. Specialists are ephemeral cognitive roles, not a permanent theatrical cast.

## Capability and skill fabric

Workflows request capabilities rather than permanent named agents. Durable, versioned skills advertise reusable procedures that provide those capabilities. The capability router indexes machine-readable advertisements, filters them using task intent and project evidence, and exposes only a bounded candidate set. Selection grants no authority; full procedures load only after a digest-bound InvocationPlan passes policy.

Invoking a skill does not require spawning an agent. The current agent, an ephemeral specialist, a deterministic executor, an independent reviewer, or a human may execute it according to consequence and independence needs. See [Capability and Skill Fabric](capability-skill-fabric.md).

AAE promotes no target number of skills. A repeated procedure becomes a candidate only when it has a named outcome, explicit inputs and outputs, an independently evaluable completion contract, and sufficient stability to version. Promotion into project or enterprise scope requires explicit governance; learning may propose but cannot self-authorize it.

## Open-world intent

Any Markdown placed in `.aae/intent/` is potentially meaningful. Seed documents are starting prompts, not a closed schema. Novel intent should be realized through existing, composed, or proposed capabilities. Unresolved gaps remain explicit.

## Guidance and enforcement

AAE Markdown guides cooperative humans and agents. It is not access control. CI/CD, repository governance, model gateways, IAM, network policy, and platform administration provide enforcement.

## Progress telemetry

Progress telemetry is a workflow preference, enabled in the starter by default and overridable through `workflow.local.md`. When enabled, long-running bounded work should expose concise, measurable state in substantive user updates: phase, completed/total comparable units where meaningful, current activity, remaining work, next milestone, and blockers. Counts are preferable to percentages; neither elapsed time nor uneven checklist items justify a completion estimate.

## Testing and coverage

Testing is a configurable project preference rather than a universal AAE mandate. The starter enables automated test creation, defect regression tests, and coverage reporting when supported; shared or local intent may disable or specialize each behavior. No numeric coverage threshold is invented by default. Task completion always requires verification evidence, even when automated testing is disabled or impractical.

Testing should follow risk rather than line count. In addition to acceptance criteria and material failure paths, repeated, concurrent, batched, long-running, and resource-owning behavior should trigger consideration of bounded repeated-operation or soak verification. Memory, handles, subscriptions, timers, workers, sockets, caches, buffers, GPU resources, temporary files, cancellation, and cleanup paths are relevant. Coverage remains diagnostic and cannot substitute for stability or lifecycle evidence.
