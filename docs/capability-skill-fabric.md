# Capability and Skill Fabric

AAE workflows request engineering capabilities. Durable skills advertise versioned procedures that can provide those capabilities. An agent or ephemeral specialist is one possible executor of a skill, not the skill itself.

## Concepts

- **Capability:** an outcome needed by a task, including expected inputs, outputs, and evidence.
- **Skill:** a reusable, versioned procedure that advertises one or more capabilities.
- **Role:** a temporary reasoning responsibility selected for the current invocation.
- **Agent:** the active runtime instance holding a role.
- **Tool:** a mechanism used by a skill.
- **Model:** reasoning capacity bound to a skill invocation.
- **Workflow:** control flow that requests capabilities at relevant stages.
- **Enforcement:** deterministic controls that do not depend on agent cooperation.

Roles are normally ephemeral. Skills are normally durable. Skill invocation does not imply agent creation: the current agent, an ephemeral specialist, a deterministic executor, an independent reviewer, or a human may execute a selected skill.

The trustworthy invocation path is:

```text
Task / Spec / Project Truth
          |
          v
  CapabilityDemand
          |
          v
     Skill Registry
          |
          v
      CandidateSet
          |
          v
   SelectionDecision
          |
          v
    InvocationPlan
          |
       POLICY GATE
          |
          v
      Procedure Load
          |
          v
       Execution
          |
          v
   InvocationRecord
```

No procedure may be loaded until an `InvocationPlan` proves that its exact content identity is eligible, trusted, satisfiable, and appropriately isolated. Discovery does not imply authority. Selection does not imply permission. Description does not imply enforcement. Execution does not imply verification.

## Skill advertisement

Native AAE skills live below `.aae/skills/` and contain a small `skill.json` advertisement plus a procedure file. Deterministic indexing may hash the procedure for provenance, but procedure content is not placed in the registry or reasoning context.

A complete advertisement looks like:

```json
{
  "schema_version": 1,
  "name": "api-contract-impact-analysis",
  "version": "1.0.0",
  "description": "Identify consumers and compatibility risks before changing an API contract.",
  "capabilities": ["api-analysis", "dependency-analysis", "compatibility-analysis"],
  "triggers": ["api contract modification", "public interface change", "schema change"],
  "applicable_when": ["existing-api", "shared-contract"],
  "inputs": ["proposed-change", "repository-evidence"],
  "produces": ["affected-consumers", "compatibility-risks", "recommended-tests"],
  "requires": ["repository-search"],
  "may_recommend": ["independent-review"],
  "cost": {"context": "medium", "reasoning": "medium"},
  "independence_required": false,
  "lifecycle": "experimental",
  "execution": {"mode": "hybrid", "side_effects": "read-only"},
  "requirements": {
    "tools": ["filesystem-search"],
    "model_capabilities": ["reasoning"],
    "platforms": ["any"],
    "network": "none",
    "data_classifications": ["public", "internal"]
  },
  "procedure": "SKILL.md"
}
```

Names and capabilities use lowercase kebab-case. Versions use semantic version text. Procedures are safe relative paths beneath their manifest directory. The registry records manifest and procedure digests but does not place full instructions into discovery results.

## Sources and adapters

Every project implicitly indexes `.aae/skills/` as its project source. Additional sources are declared in `.aae/skill-sources.json` or the ignored `.aae/skill-sources.local.json`:

```json
{
  "schema_version": 1,
  "sources": [
    {
      "id": "organization",
      "scope": "enterprise",
      "adapter": "aae-json",
      "path": "/approved/aae-skills",
      "owner": "platform-engineering",
      "provenance": "governed-repository",
      "trust": "governed",
      "capability_allowlist": ["api-analysis", "dependency-analysis", "compatibility-analysis"],
      "approval": {"status": "approved", "approved_by": "engineering-policy", "policy_version": "1"},
      "integrity": {}
    }
  ]
}
```

The `aae-json` adapter reads native `skill.json` advertisements. The `skill-md` adapter normalizes the `name`, `description`, and optional capability metadata from `SKILL.md` frontmatter. The `registry-json` adapter consumes an index of file-backed advertisements with procedures relative to that index, allowing runtime or MCP adapters to publish metadata without making AAE dependent on one vendor. Live MCP enumeration and invocation remain integration-adapter responsibilities. AAE does not scan personal or enterprise locations unless configured.

Source scope is recorded rather than silently establishing authority. Scope says where content came from; owner, provenance, trust, approval, integrity, and `capability_allowlist` say whether it can be used. Filesystem or configuration precedence never grants trust or replacement authority. In v1, replacement is never implicit: duplicate source identifiers and duplicate registry identifiers are invalid. Identically named skills from different sources remain distinct and must be selected by registry ID when ambiguous.

## Portable identity

AAE keeps three identities separate:

- `skill_content_sha256` is portable and changes with the normalized advertisement or procedure bytes.
- `registry_content_sha256` is portable and changes with the effective portable source/skill set.
- `runtime_instance_id` is local provenance over host, project path, process, and interpreter.

Absolute filesystem paths never participate in the first two identities. Relative procedure paths normalize `/` and `\`, so identical content initialized under Linux, Windows-style, CI, or developer paths produces identical portable identities.

## Bounded discovery

Discovery deliberately separates advertisements from procedures:

1. Task classification supplies requested capabilities and known architecture, environment, risk, and evidence-gap facts.
2. Deterministic metadata filtering removes non-routable lifecycle states and irrelevant capabilities, then ranks triggers and applicability metadata.
3. A bounded lexical relevance stage ranks the remaining descriptions, inputs, outputs, requirements, and relationships.
4. The active agent receives only the small shortlist advertisements.
5. A deterministic selection decision names one candidate, but grants no permission.
6. An `InvocationPlan` binds the selected content digests to role/context isolation, model, tools, network, data classification, side effects, approvals, platform, and policy checks.
7. Full procedure content is loaded only when every enforced check passes and the plan digest still matches.

Run:

```bash
aae registry
aae discover "add an asyncio background worker pool" --architecture event-driven-service --environment python-asyncio --risk concurrency --evidence-gap shutdown-semantics-unknown --limit 4
aae skill project:resource-lifecycle-check --metadata-only
aae invoke "review worker lifecycle" --skill project:resource-lifecycle-check --capability resource-lifecycle-analysis --tool filesystem-read --model-capability reasoning --provider local --model MODEL_ID --model-data-classification internal
aae outcome project:resource-lifecycle-check succeeded --invocation-id INVOCATION_ID --verification passed --context-tokens 2400 --evidence .aae/specs/change/tasks.md
aae skill-stats
```

`MODEL_ID` must be replaced with a model authorized by `.aae/skill-policy.json`; `INVOCATION_ID` must be replaced with the identifier printed by `aae invoke`. `aae skill` is metadata-only in governed operation; asking it to load instructions fails closed. Native manifest contracts are enforced. Adapted manifests that cannot advertise enforceable requirements are marked advisory and denied unless policy explicitly permits advisory contracts. Policy also allowlists tools and provider/model bindings; a runtime capability claim cannot grant either authority by itself.

The baseline relevance stage is deterministic and dependency-free. A pluggable semantic reranker may reorder only the bounded candidate set and cannot introduce an out-of-candidate skill. Historical-use and architecture graphs may augment later decisions while preserving the registry, candidate, and policy contracts.

`may_recommend` relationships provide bounded neighboring suggestions. They never trigger recursive invocation. The workflow router retains the relevance, consequence, independence, and budget decision.

## Lifecycle and governance

Supported lifecycle states are:

```text
candidate -> experimental -> validated -> project or enterprise -> deprecated -> retired
```

Candidate, deprecated, and retired skills are not automatically routable. A repeated procedure may produce a candidate proposal, but AAE must not silently promote it into authoritative project or enterprise guidance. Promotion requires review under the owning project's governance.

A procedure should be promoted when it has a named outcome, explicit inputs and outputs, an independently evaluable completion contract, enough stability to version, and demonstrated reuse, consequence reduction, expertise value, or composition value. One-off instructions, personas, unverifiable prompts, trivial tool wrappers, and deterministic policy controls are not skills.

`CapabilityDemand` records every derived requirement with the task, architecture, environment, risk, or evidence-gap clue that caused it. Each invocation persists the demand, registry identity, bounded candidate set, selected and rejected candidates, policy checks, plan, context/evidence digest, runtime/model/tool binding, loaded skill identity, outcome, verification, and cost telemetry below `.aae/runtime/invocations/`. Skill events use atomic per-event publication so concurrent writers do not corrupt telemetry. `aae skill-stats` summarizes those events and derives selection and failure rates.

The nine v1 contracts have versioned JSON Schemas under `.aae/schemas/`. Unknown schema versions fail closed; a future version must ship an explicit migration before becoming current. Expected source-content digests are enforced now. Detached signature metadata is retained but no verifier is bundled; setting `require_verified_signature` therefore denies invocation until an integration supplies verified status. This evidence informs review and quality learning; it does not self-authorize promotion. Cross-runtime telemetry transport, live MCP discovery/invocation, and automatic repeated-procedure detection remain later integration work.
