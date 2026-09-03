# Capability and Skill Fabric

AAE treats skills as reusable procedures and agents as temporary executors.

```text
Task
  -> search skill advertisements
  -> shortlist the smallest useful set
  -> apply basic safety checks
  -> load selected procedure
  -> execute and verify
```

The current agent normally runs the selected skill. A fresh reviewer or other specialist is created only when the task actually needs a separate role or context.

## Skill advertisement

A native skill is a directory containing `skill.json` and a procedure such as `SKILL.md`:

```json
{
  "schema_version": 1,
  "name": "migration-safety-check",
  "description": "Check database migrations for deployment and rollback risks.",
  "when_to_use": ["database schema changes", "migration files changed"],
  "capabilities": ["migration-safety"],
  "requires_tools": ["filesystem-read"],
  "procedure": "SKILL.md"
}
```

Only `schema_version`, `name`, `description`, and `procedure` are required. `version` defaults to `0.1.0`; `capabilities` defaults to the skill name. `when_to_use` improves matching. Three optional fields have direct v1 safety behavior:

- `requires_tools` prevents loading when the runtime lacks a required tool;
- `destructive: true` requires the explicit `destructive` approval;
- `independence_required: true` requires fresh context.

AAE does not ask authors to declare cost models, lifecycle states, model/provider policy, input/output ontologies, recommendation graphs, or trust graphs in v1.

## Registry and discovery

`.aae/skills/` is the implicit project source. `.aae/skill-sources.json` and the ignored `.aae/skill-sources.local.json` may add enterprise, project, or local sources:

```json
{
  "schema_version": 1,
  "sources": [
    {
      "id": "organization",
      "scope": "enterprise",
      "adapter": "skill-md",
      "path": "/approved/skills"
    }
  ]
}
```

Supported adapters are native `aae-json`, frontmatter-based `skill-md`, and file-backed `registry-json`. AAE does not scan arbitrary personal or enterprise locations unless configured.

Discovery uses only advertisements. It scores requested capability matches and words shared by the task, description, and `when_to_use`, then returns a bounded shortlist. Full procedure text is loaded only for the selected skill after v1 safety checks pass.

```bash
aae registry
aae discover "add an asyncio worker pool" --risk concurrency --limit 4
aae invoke "review worker lifecycle" \
  --skill project:resource-lifecycle-check \
  --tool filesystem-read
```

## Safety and evidence

V1 intentionally has a small safety boundary:

- governed/project configuration is version controlled;
- a duplicate registry identity is rejected, so a local skill cannot silently replace another provider;
- required tools must be present;
- destructive skills require explicit approval;
- independence-required skills require fresh context;
- the selected procedure is digest-checked between indexing and loading.

Invocation records persist the task, advertisement shortlist, selected skill, safety decision, content digests, trigger provenance, and eventual outcome under `.aae/runtime/invocations/`. Those records support debugging and later improvement; they are not a policy language.

## Criterion authority

An invocation may bind two deliberately small kinds of acceptance criterion:

- `semantic-executor`: a statement assessed by the active agent from bounded evidence;
- `deterministic-control`: an enabled hook `run_check` whose recorded exit status is evaluated by AAE.

Each criterion has a content-derived ID and named evaluator. Each result records
its authority, evidence digest, and responsible invocation or hook identity.
AAE exposes only the semantic projection as `executor_criteria`; control
criteria never ask a model to grade its own enforcement.

The combined result is deterministic: any failure means `failed`; otherwise
missing or unavailable proof means `blocked`; otherwise the result is
`succeeded`. A reported outcome that contradicts this result is rejected.

Independent-review skills additionally require `--review-of INVOCATION_ID`.
The target must have succeeded, but its verdict is not copied into reviewer
context. This is gating, not a new policy system or an automatic agent launcher.

AAE does not automatically create skills or agents from observed behavior. When people notice a repeated procedure, they can turn it into a skill. Automatic suggestions, semantic reranking, graphs, and lifecycle governance remain future enhancements that must be justified by real use.
