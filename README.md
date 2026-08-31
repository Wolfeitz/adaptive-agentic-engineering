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
- model-routing, context-hygiene, and observability contracts;
- a CI workflow and standard-library test suite;
- a bootstrap command for greenfield or existing repositories.

Semantic compilation is intentionally model-agnostic. `aae compile` produces a bounded compiler request and manifest. The active agent runtime interprets that packet according to the AAE compiler contract. Later releases can add direct model-provider adapters without changing the intent plane.

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
aae watch [PATH]      Recompile when intent Markdown changes
aae doctor [PATH]     Show environment and repository diagnostics
```

## HVE Core

AAE can use HVE Core as a VS Code execution adapter. It does not make HVE Core the source of project truth. See [docs/integrations/hve-core.md](docs/integrations/hve-core.md).

## Project status

This is an initial reference bootstrap intended for experiments across Codex, VS Code/Copilot, and HVE Core. See [ROADMAP.md](ROADMAP.md) and the repository's own `.aae/specs/aae-bootstrap/` specification.

## License

No license has been selected yet. Choose one before publishing the repository for general reuse.
