# HVE Core Interoperability

HVE Core is an optional AAE execution adapter for VS Code and Copilot CLI.

## Principles

- Repository-owned AAE intent, specifications, decisions, and evidence remain authoritative.
- Use the smallest HVE/RPI entry surface that owns the next action.
- Reuse adequate existing evidence; research only demonstrated gaps.
- Pin or selectively adopt HVE components when reproducibility matters.
- Do not copy the entire HVE catalog into every project or context.
- Map HVE research, planning, implementation, and review outputs into the applicable AAE specification and task ledger.
- A task is complete only when its AAE completion contract is verified.

## Suggested experiment

Run the same bounded feature in three modes:

1. Codex with `aae-codex`
2. VS Code/Copilot with `aae-github-copilot`
3. VS Code/Copilot with `aae-github-copilot` plus pinned HVE Core/RPI

Compare decisions, requirement coverage, task completion, review findings, token/context usage, and durable artifacts rather than identical wording.
