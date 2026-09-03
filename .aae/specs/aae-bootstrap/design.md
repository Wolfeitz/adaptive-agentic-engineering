# AAE Bootstrap Design

The reference implementation is a dependency-free Python package with an `aae` CLI. Templates are packaged as resources and installed conservatively. The deterministic compiler stage produces hashes, overlay relationships, and a semantic compiler request. Active AI runtimes use thin repository adapters to interpret the packet. This preserves portability while leaving direct provider integration open for later phases.

Testing behavior is expressed as open-world intent rather than hard-coded CLI policy. A seeded `testing.md` enables automated test creation, regression testing, coverage reporting, and risk-triggered resource-lifecycle verification while defining no arbitrary coverage threshold. A matching ignored local overlay may specialize or disable these preferences. Completion contracts continue to require verification evidence regardless of the selected testing policy.

The capability and skill fabric uses dependency-free JSON advertisements and separate Markdown procedures. A native project source is implicit; configured `aae-json`, `skill-md`, and `registry-json` adapters normalize enterprise, project, and local sources. A v1 advertisement requires only name, description, and procedure; `when_to_use`, capabilities, required tools, destructive behavior, and independence are optional.

Compilation writes a registry containing portable skill/registry identities and separate local runtime provenance, never procedure content. Invocation searches advertisements or accepts an explicit skill, checks required tools, destructive approval, and fresh-context requirements, then digest-checks and loads the procedure. Invocation and outcome records provide durable evidence without becoming a policy language or self-authorizing learning system.

Four versioned JSON Schemas document the public v1 contracts. Runtime validation remains dependency-free. Skill telemetry uses atomic per-event files for concurrent writers. Semantic retrieval, graph expansion, lifecycle governance, and automatic skill suggestions are deferred until actual use demonstrates a need.

The deterministic hook layer implements `X happens -> do Y`. Each rule names an event, optional file globs, and exactly one skill request or direct deterministic check. Checks are argv lists executed without a shell. Payloads are persisted only as digests; idempotency and small internal bounds prevent duplicate or recursive fan-out. Destructive checks require explicit approval.
