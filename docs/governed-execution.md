# Governed execution

AAE 0.3 contains one deliberately narrow semantic execution path. It proves
that the deterministic control plane can own a real task lifecycle without
becoming a provider framework.

```text
TaskRequest
    -> CapabilityDemand
    -> bounded candidate discovery and deterministic selection
    -> policy-checked InvocationPlan
    -> digest-bound bounded evidence packet
    -> ephemeral executor process for semantic criteria only
    -> deterministic-control criterion evaluation
    -> deterministic combined pre-review outcome
    -> optional separate-process independent review
    -> canonical governed-run accounting
```

The executor cannot load a procedure until policy allows its exact content
identity. It cannot launch from a denied record, replace the selected skill,
authorize its own side effects, or write AAE outcome state. The parent control
plane records the outcome after validating the subprocess result.

## Codex CLI adapter

The only implemented adapter is `codex-cli`. It invokes `codex exec` with:

- an ephemeral session;
- ignored user configuration and project rules, while retaining Codex
  authentication;
- an empty isolated working directory and a digest-bound bounded prompt;
- an explicit model and read-only or workspace-write sandbox matching the
  authorized skill side effect;
- a strict structured-output schema; and
- JSONL lifecycle events used to capture a fresh thread identity and token
  usage.

The adapter does not enumerate providers, invent credentials, choose policy,
or infer a model. `.aae/execution.json` must name the portable adapter contract;
machine-specific command or model overrides may be supplied only in ignored
`.aae/execution.local.json`. If the effective command or model is absent, the
run fails closed.

The effective local binding must also pin the installed `codex` executable by
SHA-256. AAE resolves and hashes it before planning, binds that identity into
the InvocationPlan, and rechecks it immediately before launch. The provider
name remains a project-policy binding for the Codex CLI adapter; it is not
misrepresented as a provider identifier emitted by the CLI process.

For independent review, AAE creates another invocation plan and launches a
second ephemeral process. The reviewer receives a neutral packet containing
the original task, semantic acceptance criteria, listed source evidence, the
criterion-level semantic result, and any canonical deterministic runtime proof.
It does not receive the executor's chain of rationale or conversation history.
Distinct invocation, execution, and Codex thread identities are recorded.

## Criterion authority and deterministic outcome contract

Every governed criterion has a content-derived identity, an authority, and an
evaluator. `semantic-executor` criteria are the only criteria placed in the
executor packet. `deterministic-control` criteria are withheld from the model
and evaluated from the post-execution runtime proof by AAE. The InvocationPlan
binds the complete assignment, while the reviewer plan binds only the semantic
projection. Governed-run schema version 2 records this split; existing schema
version 1 records remain valid historical accounting and are not rewritten.
Configuring the enforced filesystem boundary also installs its matching
deterministic-control criterion automatically, so omission of the CLI flag
cannot disable a configured security boundary.

The executor reports findings and one status for every assigned semantic
criterion, exactly once and in packet order. AAE records each criterion result
with its evaluator, supporting evidence digest, and responsible invocation or
control identity. It then derives the combined pre-review outcome across all
semantic and deterministic results:

- any `failed` criterion produces `failed`;
- otherwise, any `blocked` criterion produces `blocked`;
- otherwise, all criteria are `passed` and the outcome is `succeeded`.

The structured-output `outcome` field remains for compatibility, but it is
informational about the semantic result only. AAE requires it to equal the
semantic result derived from the executor's assigned criteria and rejects a
contradiction as `invalid-output`. A missing deterministic proof blocks the
combined outcome; a failed or inconsistent proof fails it. Independent review
can launch only after the complete pre-review outcome succeeds. Criterion
validation and evidence-reference validation remain fail-closed.

## Context bounds

Every governed run defines positive limits for:

- packet items;
- files;
- UTF-8 bytes; and
- a conservative byte-based token estimate.

The token value is an explicit UTF-8-byte upper bound rather than a tokenizer
claim. Before launch, AAE also measures the complete prompt, including the
authorized procedure and serialization framing, against both byte and token
bounds. File-size checks reject oversized evidence before reading file bodies.

Evidence paths must be unique regular UTF-8 files under the project root. AAE
enforces configured project-relative allow and deny prefixes, then hashes and
embeds permitted files before planning. Exceeding any bound fails before the
semantic executor starts. Result evidence references must resolve to a path in
the packet.

## Durable accounting

Raw packets, subprocess artifacts, and invocation records live below ignored
`.aae/runtime/`. The project-selected `accounting_directory` receives an
exclusive, canonical, digest-bearing governed-run record suitable for durable
version control. `aae accounting` and `aae accounting --json` reconcile these
records and fail on digest drift.

Every launched semantic attempt publishes an execution artifact before a
validation rejection is surfaced. Invalid output is not retained verbatim, but
the artifact retains its raw and parsed digests, invocation/thread identity,
role, executor/model and command identity, packet and plan bindings, duration,
usage when emitted, validation failure, and final AAE disposition. This keeps
diagnostic provenance without duplicating the bounded prompt or rejected model
content.

This feature does not implement hooks, arbitrary command execution, background
agents, a provider catalog, credentials, retrievers, or distributed
orchestration.
