# Native Runtime Adapters

AAE core owns portable project intent, event/action rules, skill advertisements,
criterion authority, and evidence records. It does not imitate an agent
runtime's native capabilities.

Runtime integration is maintained in three independently versioned adapters:

- [`aae-codex`](https://github.com/Wolfeitz/aae-codex): Codex CLI, app, and IDE extension;
- [`aae-claude-code`](https://github.com/Wolfeitz/aae-claude-code): Claude Code;
- [`aae-github-copilot`](https://github.com/Wolfeitz/aae-github-copilot): GitHub Copilot in VS Code. Other GitHub-hosted
  runtimes are added only after their native surfaces are separately verified.

The adapter boundary follows one rule:

> Use the strongest relevant native capability verified for the detected
> runtime and version. Use portable AAE behavior only for a demonstrated gap.

This does not mean enabling every feature. An adapter uses a native surface only
when it contributes to the task and respects the runtime's own trust,
permission, and approval controls.

## Adapter contract

Every adapter repository must provide:

1. `capabilities.json`, recording the runtime surfaces, current support status,
   authoritative documentation, verification date, and tested versions;
2. an `init` command that installs only provider-native project files and never
   overwrites existing configuration;
3. a `sync-skills` command that projects AAE's canonical skills into a native
   skill directory without making the projection authoritative;
4. a `doctor` command that detects the installed runtime/version and reports
   whether it is inside the adapter's verified range;
5. native hook handling that passes only bounded, non-sensitive facts into
   `.aae/hooks.json` event/action rules;
6. an optional, read-only independent-review role implemented with the
   runtime's native agent mechanism;
7. offline unit tests and a scheduled upstream verification workflow.

Capability states are intentionally small:

- `native`: the runtime owns the behavior and the adapter uses it;
- `available`: the native surface exists but AAE has no justified use for it;
- `unavailable`: the runtime or a particular surface does not provide it;
- `not-applicable`: it does not belong to this adapter.

Adapters must not silently claim support after an upstream change. Scheduled
checks may detect drift and open or fail a maintenance item, but changes to
native configuration require review and a new adapter release.

## Core integration

Adapters call the public `aae.hooks.process_event` function or the equivalent
`aae event` CLI. They may attach a sanitized `delivery_provenance` object and
may suppress durable records for clean no-match deliveries. Core never needs
the raw native prompt, tool response, transcript, or file contents.

The canonical AAE skill remains under `.aae/skills`. Native copies under
`.agents/skills`, `.claude/skills`, or `.github/skills` are generated
projections. An adapter must preserve existing native skills and identify the
source digest of every projection it creates.

## Verification cadence

Each adapter tests on two cadences:

- pull requests: offline contract, template, normalization, privacy, and
  non-overwrite tests;
- scheduled: current stable runtime installation, version/feature probes, and
  authoritative documentation reachability.

`doctor --strict` fails when a required native surface disappears or the
installed version falls outside the verified range. It warns, rather than
pretending compatibility, when a newer unverified runtime is detected.
