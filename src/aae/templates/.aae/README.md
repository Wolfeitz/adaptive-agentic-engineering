# AAE Project Sources

Files in `intent/` are human-readable project sources. The seeded filenames are suggestions, not limits. Add any Markdown that matters to the way this project should be engineered.

For a private local specialization, copy a tracked `*.local.example.md` file to the matching `*.local.md` name—for example, copy `environment.local.example.md` to `environment.local.md`. The example remains in Git so the capability is discoverable. The copied local file is ignored by Git and processed after its shared counterpart.

You may create a matching local overlay for any shared intent source; an example is not required for the mechanism to work. Do not put credentials or secrets in local files.

Reusable procedures live under `skills/` as a small `skill.json` advertisement plus separate instructions. Versioned JSON Schemas live under `schemas/`. Additional enterprise, runtime, or local skill sources can be normalized through `skill-sources.json` and the ignored `skill-sources.local.json`; scope records origin, while trust, approval, owner, provenance, and integrity independently govern authority. Discovery reads advertisements first. A full procedure can load only through `aae invoke` after an integrity-bound `InvocationPlan` satisfies policy.

Run `aae compile` after intent or skill changes or use `aae watch`. Compiler-owned local state, including the normalized skill registry, appears in `runtime/` and must not be committed.

When a semantic provider produces a schema-v1 JSON document, validate it with `aae semantic validate` and publish it with `aae semantic publish`. Published content-addressed releases, task packets, review packets, impact deltas, and provenance appear under `generated/releases/`. Use `aae invoke` for policy-checked skill selection and `aae accounting` to inspect the resulting agent/skill model.
