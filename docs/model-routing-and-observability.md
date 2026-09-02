# Model Routing and Observability

## Routing order

1. Data-handling, regulatory, network, and approval constraints
2. Required modality, tool use, context, structured output, and reasoning capability
3. Consequence and uncertainty of the decision
4. Independence or model diversity needs
5. Availability, latency, capacity, and reliability
6. Cost and resource efficiency
7. Configured preference and fallback

Local CPU/GPU/NPU models, shared on-premises inference, and approved cloud models are peers in a hybrid model estate. Zero API price is not zero resource cost.

AAE implements this ordering through `.aae/model-profiles.json` and
`aae model-route`. A profile is eligible only when availability, location,
capabilities, network state, and data classification all permit it. Ranking and
fallback order are deterministic. The seeded local example is ignored until an
operator copies and binds it to a real runtime.

## Telemetry

Record model/provider/version, execution location, task/spec/phase/role, requested capabilities, selected skill registry ID and version, selection reason, token counts, cache usage, latency, retries, fallback reason, cost or local compute, and outcome evidence. Attribute input tokens to context categories so excess history, skill advertisements, full procedures, logs, duplicate evidence, or irrelevant retrieval can be found.

Track registry-level consideration, selection, success and failure, context cost, execution cost, overlap, and deterministic supersession. Lifecycle evidence can justify a promotion or deprecation proposal, but telemetry never self-authorizes a skill or changes project authority.

Record whether usage is provider-reported, directly measured, tokenizer-estimated, or unavailable. Do not capture prompt, code, tool-result, secret, or sensitive payload content by default. Prefer OpenTelemetry-compatible GenAI traces.

`aae trace-export` emits redacted OpenTelemetry-compatible JSON spans from
durable invocation records. It exports identities and operational metadata, not
prompt, procedure, source-code, tool-result, or secret content.
