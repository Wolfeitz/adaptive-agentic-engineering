# AAE Project Sources

Files in `intent/` are human-readable project sources. The seeded filenames are suggestions, not limits. Add any Markdown that matters to the way this project should be engineered.

For a private local specialization, copy a tracked `*.local.example.md` file to the matching `*.local.md` name—for example, copy `environment.local.example.md` to `environment.local.md`. The example remains in Git so the capability is discoverable. The copied local file is ignored by Git and processed after its shared counterpart.

You may create a matching local overlay for any shared intent source; an example is not required for the mechanism to work. Do not put credentials or secrets in local files.

Run `aae compile` after changes or use `aae watch`. Compiler-owned local state appears in `runtime/` and must not be committed.
