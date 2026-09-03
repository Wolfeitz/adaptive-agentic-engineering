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

`aae invoke` matches task evidence against skill advertisements, records the bounded shortlist and selection, and applies the v1 safety checks before loading any procedure.

The safety gate checks:

- required tools;
- destructive behavior and explicit approval;
- independence and fresh-context requirements;
- content identity between registry construction and procedure loading.

The resulting invocation record contains the task, candidates, selection, safety checks, procedure digest, trigger provenance, and outcome. A denial is durable evidence and never silently falls back to another skill.

The current agent runs an allowed procedure by default. An independence-required procedure needs fresh context and may be assigned to a temporary reviewer. Those are runtime roles, not permanently running agents.

## Boundaries

Implemented provider interfaces do not imply a configured model. Credentialed tracker
submission exists behind an HTTPS, environment-credential, explicit
external-write confirmation boundary; it is never invoked implicitly.
Distributed orchestration remains outside AAE.
