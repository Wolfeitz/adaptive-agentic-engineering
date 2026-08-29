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

## Telemetry

Record model/provider/version, execution location, task/spec/phase/role, token counts, cache usage, latency, retries, fallback reason, cost or local compute, and outcome evidence. Attribute input tokens to context categories so excess history, logs, duplicate evidence, or irrelevant retrieval can be found.

Record whether usage is provider-reported, directly measured, tokenizer-estimated, or unavailable. Do not capture prompt, code, tool-result, secret, or sensitive payload content by default. Prefer OpenTelemetry-compatible GenAI traces.
