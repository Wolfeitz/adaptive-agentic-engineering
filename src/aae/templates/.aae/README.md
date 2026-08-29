# AAE Project Sources

Files in `intent/` are human-readable project sources. The seeded filenames are suggestions, not limits. Add any Markdown that matters to the way this project should be engineered.

For a private local specialization, create a matching file such as `environment.local.md`. Local files are ignored by Git and processed after their shared counterpart. Do not put credentials in them.

Run `aae compile` after changes or use `aae watch`. Compiler-owned local state appears in `runtime/` and must not be committed.
