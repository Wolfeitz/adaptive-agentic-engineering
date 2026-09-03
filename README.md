# Adaptive Agentic Engineering

Adaptive Agentic Engineering (AAE) is a portable engineering method and control-plane bootstrap for AI-assisted software work.

Teams describe a project in readable Markdown. AAE discovers that intent, combines shared sources with private local overlays, and prepares the minimum evidence and workflow needed by Codex, VS Code, HVE Core, CI, or another agent runtime.

> AAE dynamically assembles the minimum evidence, context, workflow, specialization, human oversight, and verification appropriate to each engineering decision.

## What this repository is

This repository is a runnable reference bootstrap, not a claim that every part of the future control plane is already automated. It currently provides:

- an open-ended Markdown intent plane;
- ignored `.local.md` overlays;
- deterministic source discovery, hashing, overlay ordering, validation, and watch mode;
- requirements → design → task specification templates;
- Codex and GitHub Copilot adapters;
- an HVE Core interoperability guide;
- context-hygiene and observability guidance;
- a multi-source skill registry with small advertisements, bounded matching, basic enforceable safety checks, and durable invocation records;
- thin Codex and GitHub Copilot native-hook adapters backed by portable `on` / `paths` rules that request one skill or run one deterministic check;
- explicit semantic-versus-deterministic criterion authority with evidence-bound outcomes;
- a provider-neutral semantic intermediate representation with conflict and clarification gates;
- atomic semantic releases, rollback, incremental impact graphs, and provenance-bearing task/review packets;
- offline Azure DevOps, GitHub, and Jira payload exporters that never submit without a separate integration;
- deterministic agents-and-skills accounting;
- a CI workflow and standard-library test suite;
- a bootstrap command for greenfield or existing repositories.

Semantic compilation is intentionally model-agnostic. `aae compile` produces a bounded compiler request and manifest. A provider may implement the `aae.semantic_providers` entry-point contract and return the versioned semantic document; `aae semantic validate` verifies that document before `aae semantic publish` atomically creates a provenance-bound release. AAE does not ship credentials or silently call a model provider.

## Quick start

```bash
python -m pip install -e .
aae init ../my-project
cd ../my-project
aae compile
aae validate
```

Then open the project in Codex or VS Code and ask:

> Read the AAE entry instructions, inspect the compiler request, ask any material questions, and bootstrap this project in the spirit of Adaptive Agentic Engineering.

That remains a single-prompt entry experience. The durable behavior lives in the repository rather than inside the prompt.

## Repository layout

```text
.aae/
├── skills/        Versioned project skill advertisements and procedures
├── hooks.json     Deterministic event/action rules (seed examples disabled)
├── intent/        Human/AI-authored Markdown sources
├── specs/         Requirements, design, and executable task ledgers
├── generated/     Shared compiler-owned artifacts
├── runtime/       Ignored effective/local compiler state
└── state/         Shared manifests and diagnostics
```

The seeded files are starting points, not a whitelist. Any Markdown added under `.aae/intent/` is treated as potentially meaningful.

## Shared and local intent

`environment.md` is committed. A tracked `environment.local.example.md` demonstrates the override mechanism; copy it to the ignored `environment.local.md`, which is interpreted after its shared counterpart. The bootstrap also includes tracked local examples for models and compute, workflow, and testing. Local overlays specialize shared defaults for a workstation or execution environment. Markdown is guidance, not a security boundary; enforcement belongs to CI/CD, repository governance, model gateways, IAM, and platform administrators.

Never put credentials or secrets in a `.local.md` file. Reference a credential-store entry or environment-variable name instead. Any shared intent source may have a matching local overlay even when no dedicated example is provided.

## Commands

```text
aae init [PATH]       Install AAE into a repository without overwriting files
aae compile [PATH]    Discover sources and prepare the semantic compiler packet
aae validate [PATH]   Check repository and overlay hygiene
aae watch [PATH]      Recompile when intent or configured skill sources change
aae doctor [PATH]     Show environment and repository diagnostics
aae registry [PATH]   Build and inspect the normalized skill registry
aae discover TASK     Shortlist skills from capabilities, architecture, environment, risk, and evidence gaps
aae skill ID [PATH]   Inspect skill metadata (`--metadata-only` is required)
aae invoke TASK       Match a skill, bind acceptance/control checks, and load it safely
aae event EVENT       Apply simple event-to-skill or event-to-check hooks
aae native-hook HOST  Adapt one Codex/Copilot native hook payload from stdin
aae outcome ID RESULT Record evidence and derive any criterion-governed invocation result
aae skill-stats       Summarize consideration, selection, outcome, and cost telemetry
aae semantic ...      Validate, inspect, publish, or roll back a semantic release
aae task-packet ID    Read one bounded packet from the active semantic release
aae tracker-export    Create offline Azure DevOps, GitHub, or Jira payloads
aae tracker-submit    Submit active task packets after explicit external-write confirmation
aae providers         List installed semantic-provider entry points
aae accounting        Inventory skills, ephemeral roles, and deterministic authority
```

## Capability and skill fabric

Skills advertise what they are good for. AAE searches those advertisements, exposes the smallest useful shortlist, checks required tools plus destructive approval or fresh-context requirements, and only then loads the selected procedure. Roles remain ephemeral; reusable skills remain durable. See [docs/capability-skill-fabric.md](docs/capability-skill-fabric.md).

Hooks provide literal “X happens, do Y” routing: Codex or Copilot supplies the native lifecycle event, and AAE can request one skill or run one configured deterministic check. `aae event` remains available for CI, webhooks, and runtimes without a native lifecycle. See [docs/hooks-and-events.md](docs/hooks-and-events.md).

Acceptance statements supplied with `aae invoke --acceptance` are evaluated by
the semantic executor. Checks named with `--control-check` must be enabled
`run_check` hooks; their actual hook records are the deterministic evidence.
AAE combines those results with failure taking precedence over blocked, and
blocked taking precedence over success. It does not accept arbitrary
model-authored “control” verdicts.

AAE intentionally seeds skills but no permanent theatrical agent cast. Run `aae accounting --json` or read [docs/agents-and-skills.md](docs/agents-and-skills.md) for the exact distinction.

`aae tracker-submit` reads credentials only from the named environment
variable, requires `--confirm-external-write`, and never places authorization
material in request-plan or result artifacts. Jira submissions additionally
require a `--defaults` JSON object containing project and issue-type fields.

## HVE Core

AAE can use HVE Core as a VS Code execution adapter. It does not make HVE Core the source of project truth. See [docs/integrations/hve-core.md](docs/integrations/hve-core.md).

## Project status

Version 0.3 is a runnable reference control plane intended for experiments across Codex, VS Code/Copilot, and HVE Core. See [ROADMAP.md](ROADMAP.md) and the repository's own `.aae/specs/` contracts.

## License

No license has been selected yet. Choose one before publishing the repository for general reuse.
