# Semantic and Invocation Control Plane

AAE now has two deliberately separate control surfaces.

## Semantic compilation

`aae compile` remains deterministic: it hashes ordered Markdown intent and writes a bounded compiler request. Semantic interpretation is supplied by an external runtime implementing the `aae.semantic_providers` entry-point protocol or by another process that emits the same JSON contract.

The semantic document records:

- project identity;
- provenance-bearing facts, constraints, decisions, preferences, proposals, and unknowns;
- capabilities with inputs, outputs, evidence, and source-statement links;
- executable tasks with dependencies, consequence, evidence gaps, acceptance criteria, risks, and selected skills;
- material conflicts and clarification questions;
- expected artifacts.

`aae semantic validate FILE` checks identity, references, provenance, paths, skill selections, and explicit consequence/evidence states. Material unresolved conflicts or questions may be inspected but block publication.

`aae semantic publish FILE` writes an immutable content-addressed release under `.aae/generated/releases/`. It includes the semantic model, dependency graph, incremental impact delta, bounded task packets, required independent-review packets, skill-registry reference, and provenance. Publication stages the complete release before one atomic rename and then atomically updates `active-release.json`. Re-publishing identical input is idempotent. `aae semantic rollback` changes only the active release pointer after validating the target manifest.

Offline tracker exporters translate active task packets into Azure DevOps, GitHub, or Jira request payloads. They do not authenticate, call a network, or mutate a tracker.

## Invocation control

`aae invoke` turns task evidence into a capability demand, produces the bounded registry shortlist, records the deterministic selection, and evaluates `.aae/skill-policy.json` before loading any procedure.

The policy gate checks:

- skill lifecycle and source trust/approval/integrity;
- whether adapted advisory contracts are allowed;
- independence and fresh-context requirements;
- side effects and explicit approvals;
- required tools and model capabilities;
- platform and network availability;
- project, skill, and model data-classification boundaries.

The resulting invocation record contains capability, candidate, selection, plan, runtime binding, procedure digest, and outcome provenance. A denial is durable evidence and never silently falls back to an ineligible skill.

The control plane binds an allowed procedure to the current agent by default. An independence-required procedure binds to a fresh independent-reviewer role. Those are runtime roles, not permanently running agents.

## Boundaries

Implemented provider and retriever interfaces do not imply a configured model.
Model profiles remain explicit local configuration. Credentialed tracker
submission exists behind an HTTPS, environment-credential, explicit
external-write confirmation boundary; it is never invoked implicitly.
Distributed orchestration remains outside AAE.
