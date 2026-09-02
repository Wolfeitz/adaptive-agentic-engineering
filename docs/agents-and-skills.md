# Agents and Skills Accounting

AAE includes eight starter skills and zero permanent named agents.

That is intentional. A skill is durable, versioned procedure metadata plus digest-bound instructions. An agent is a runtime executor. Selecting a skill does not automatically create an agent.

## Runtime roles

| Role | Kind | When used |
| --- | --- | --- |
| current-agent | ephemeral agent role | Default executor for a selected, policy-allowed skill |
| independent-reviewer | fresh ephemeral agent role | Required by `independent-review` or a task packet whose explicit consequence/evidence state requires independence |
| deterministic-control-plane | code, not an agent | Owns registry normalization, identity/digest calculation, policy checks, canonical publication, and durable evidence |

## Starter skills

| Skill | Primary purpose | Mode | Independence |
| --- | --- | --- | --- |
| `acceptance-verify` | Criterion-by-criterion completion evidence | hybrid | no |
| `bounded-context-builder` | Minimal provenance-aware context packet | procedural | no |
| `implementation-preflight` | Authority, impact, precedent, and verification readiness | hybrid | no |
| `independent-review` | Fresh-context challenge of consequential work | agentic | yes |
| `repo-recon` | Bounded repository authority and architecture discovery | hybrid | no |
| `resource-lifecycle-check` | Concurrency, cancellation, shutdown, and leak analysis | hybrid | no |
| `review-lesson-extractor` | Evidence-backed guidance/skill/control proposals | hybrid | no |
| `runtime-diagnosis` | Live provenance and discriminating diagnosis | hybrid | no |

All starter skills are experimental. None can promote itself. Source policy, runtime capability checks, and human/project governance determine whether a procedure may execute or advance lifecycle.

Run `aae accounting` for a concise live inventory or `aae accounting --json` for exact capabilities, requirements, digests, lifecycle counts, side-effect counts, and authority policy.

The machine-readable accounting also reports observed runtime invocation counts
by status and ephemeral role, configured model-profile count, installed semantic
provider/retriever entry points, and a component ledger. This prevents provider
adapters, deterministic routers, registries, and publishers from being mislabeled
as autonomous agents.

## What is not present

- no permanent planner/reviewer/tester cast;
- no hidden autonomous agent daemon;
- no automatic subagent spawning;
- no autonomous skill promotion;
- no distributed scheduler;
- no live model or tracker credentials.

## Implemented non-agent controls

- deterministic intent compilation and semantic validation/publication;
- content-addressed releases, impact graphs, task packets, and rollback;
- source-trust and capability-aware skill invocation policy;
- deterministic model eligibility and fallback ordering;
- bounded semantic-retriever plug-in contract;
- advisory skill evaluation and promotion proposals that never self-apply;
- historical-use, CI-policy, tracker-payload, and redacted trace artifacts.

Configured provider or retriever plug-ins may internally use models or agents,
but AAE records them as adapters until a runtime actually binds an executor.

An active runtime may create a specialist when fresh context, model diversity, or expertise is justified. The invocation record must preserve why that binding occurred.
