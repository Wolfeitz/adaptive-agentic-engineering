# AAE 0.3 Architecture Audit

This audit is bound to `codex/aae-0.3-baseline`, based on
`a5a17d04500df2ef4d4db78f0fef65caab9ded5d`. It distinguishes executable
behavior from contracts, adapters, and future integrations.

Status meanings:

- **IMPLEMENTED**: executable behavior exists and has repository tests.
- **IMPLEMENTED_BUT_UNINTEGRATED**: working code exists, but the normal runtime path does not invoke it.
- **PARTIAL**: a meaningful part works, but the complete goal is not satisfied.
- **NOT_IMPLEMENTED**: no working implementation exists.
- **DEFERRED_BY_DESIGN**: explicitly outside the 0.3 boundary.

## Architecture findings

### Deterministic control plane

Identity and digest derivation, source normalization, candidate ranking, policy
checks, semantic publication, rollback, and durable records are deterministic
code paths. Skills do not select or promote themselves. Semantic releases are
verified against the active pointer, manifest, complete file inventory, file
digests, semantic identity, and task-packet digests before authoritative reads
or transitions.

This is an application control boundary, not an operating-system sandbox. A
process with direct filesystem or Python access can bypass the CLI. Runtime
approval and fresh-context values are assertions from the invoking authority;
AAE does not cryptographically attest the human approver, executor, or
conversation boundary. **Status: IMPLEMENTED as the reference control path;
PARTIAL as a security boundary.**

Invocation outcomes are one-shot, require canonical UUIDs, verify the prior
record digest, and are written atomically. Accounting rejects records whose
content does not match their digest.

### Agents and independence

AAE creates no permanent personality or role agents. `current-agent` and
`independent-reviewer` are ephemeral role bindings. The latter requires a
caller-supplied fresh-context assertion before instructions load. No executor
plug-in proves a separate process/session or author-context exclusion.
**Status: PARTIAL.**

### Skills

The registry records lifecycle, version, source, portable content identity,
procedure digest, execution mode, side effects, tools, model capabilities,
platform, network, data classifications, and outcome evidence. Discovery is
metadata-only and deterministic; a digest-bound allowed `InvocationPlan` is
required before procedure loading. Procedural skills work without inventing an
agent. **Status: IMPLEMENTED.**

Source and runtime policy files are repository/runtime authority inputs. Their
integrity is checked by the application, but repository permissions and review
rules must prevent an agent from rewriting those inputs.

### Context hygiene

Semantic task/review packets and skill shortlists carry provenance and avoid
requiring accumulated conversation history. Shortlist counts are bounded.
Task/review packets do not enforce byte, token, or source-item budgets, and
fresh reviewer context is not runtime-attested. **Status: PARTIAL.**

### Writes and integrations

Workspace and skill side effects are represented in policy. Live tracker calls
require HTTPS except on localhost, a non-empty token, and explicit
external-write confirmation; tokens are excluded from persisted artifacts.
Those confirmations remain caller assertions. GitHub Issues, Jira, and Azure
work-item paths have injected-transport tests; GitHub Projects is absent and no
real credentialed SaaS write was performed. **Status: PARTIAL for the original
integration goal.**

### Model routing and extension points

Model profiles are explicit, capability/data/network/location filtered, and
deterministically ordered with fallbacks. No profile is fabricated when none is
configured. Semantic-provider and skill-retriever entry-point contracts are
narrow; retrievers cannot introduce out-of-candidate skills. Provider execution
and semantic reranking are not wired into normal invocation.
**Status: IMPLEMENTED_BUT_UNINTEGRATED.**

### Evidence and observability

Invocation records preserve demand, candidates, selection, policy checks,
runtime-binding assertions, procedure identity, evidence digest, outcome,
verification, token estimate, and cost. Trace export is deterministic,
OpenTelemetry-compatible JSON and omits prompt, procedure, token, and secret
payloads. It does not automatically observe an external executor, so tool calls,
changed files, model version, latency, and provider-reported usage depend on an
integrating runtime. **Status: PARTIAL end to end.**

### Extensibility

HVE, RPI, Spec-Kit, security, architecture, ticket, and review workflows can be
represented through skills, policy, semantic packets, or adapters without
changing core data types. Distributed orchestration, automatic agent creation,
and autonomous promotion are **DEFERRED_BY_DESIGN**.

## Original roadmap accounting

| Original goal at `a5a17d0` | Status | Evidence or limitation |
| --- | --- | --- |
| Open-world Markdown intent plane | IMPLEMENTED | Existing compiler discovery and tests |
| Local overlay convention | IMPLEMENTED | Shared/local precedence and ignore tests |
| Deterministic source manifest | IMPLEMENTED | Canonical source hashes and ordering |
| Semantic compiler request | IMPLEMENTED | Compiler request artifact generation |
| Codex and Copilot entry adapters | IMPLEMENTED | Packaged templates |
| HVE Core interoperability guidance | IMPLEMENTED | Guidance, as originally scoped |
| Spec templates and task completion contract | IMPLEMENTED | Packaged requirements/design/tasks |
| CI validation and tests | IMPLEMENTED | Python 3.10/3.14, Mypy, Ruff, tests, validation, wheel |
| Provider-neutral semantic intermediate representation | IMPLEMENTED | Schema-v1 validation and tests |
| Pluggable model-provider interface | IMPLEMENTED_BUT_UNINTEGRATED | Protocol/entry point exists; CLI execution does not |
| Incremental impact graph | IMPLEMENTED | Graph and previous/current delta |
| Generated artifact provenance | IMPLEMENTED | Source, registry, provider, and file digests |
| Conflict and clarification workflow | IMPLEMENTED | Material conflicts/questions block publication |
| Atomic publication and rollback | IMPLEMENTED | Atomic writes and authenticated reads |
| Risk and evidence classifier | IMPLEMENTED | Deterministic classifier |
| Bounded task-packet compiler | PARTIAL | Structured packet exists; no byte/token/item budget |
| Ephemeral specialist routing | PARTIAL | Role binding exists; executor isolation is external |
| Independent challenge and review packets | PARTIAL | Packet exists; independent execution is not attested |
| Model capability registry and fallback policy | IMPLEMENTED | Local profiles and deterministic fallback routing |
| Azure DevOps tracking adapter | IMPLEMENTED | Offline and confirmation-gated injectable live path |
| GitHub Issues/Projects adapter | PARTIAL | Issues exists; Projects is absent |
| Jira adapter | IMPLEMENTED | Offline and confirmation-gated injectable live path |
| CI policy-generation adapters | IMPLEMENTED | Deterministic GitHub/Azure/GitLab artifacts |
| OpenTelemetry-compatible GenAI traces | IMPLEMENTED | Redacted deterministic span export |

## Additional 0.3 capability accounting

| Added goal | Status | Evidence or limitation |
| --- | --- | --- |
| Multi-source skill registry and governed invocation | IMPLEMENTED | Eight starter skills, 35 capabilities, policy-negative tests |
| Provider/retriever discovery contracts | IMPLEMENTED_BUT_UNINTEGRATED | They enumerate honestly as empty by default |
| Skill lifecycle promotion | PARTIAL | Proposal-only transition exists; no durable corpus |
| Historical-use graph | IMPLEMENTED_BUT_UNINTEGRATED | Export exists; routing does not consume it |
| Live tracker submission | PARTIAL | Injected transport tested; no real provider exercise |
| Permanent agent cast | DEFERRED_BY_DESIGN | Zero named persistent agents is intentional |
| Distributed orchestration | DEFERRED_BY_DESIGN | Explicitly outside 0.3 |

## Concise future work

1. Define an externally attestable approval and fresh-review execution contract.
2. Enforce byte/token/item budgets for task and neutral review packets.
3. Wire one provider and one bounded retriever through the normal control path.
4. Capture executor-observed tool/change/model-version/latency evidence.
5. Add GitHub Projects only when an active integration requires it.
