# Architecture

The human-readable intent plane is authoritative project input. Deterministic code discovers, hashes, orders, validates, and watches sources. Semantic interpretation is model-agnostic and performed through portable compiler contracts. Tool-specific adapters remain thin.

The control plane includes a capability router backed by a normalized skill registry. Workflows request capabilities; durable skills advertise versioned procedures; roles, agents, models, and tools are bound only for the current invocation. The trustworthy path is CapabilityDemand -> CandidateSet -> SelectionDecision -> InvocationPlan -> policy gate -> procedure load -> InvocationRecord. Selection grants no authority. Portable skill and registry content identities exclude local runtime paths; source scope remains separate from trust, approval, and integrity. Candidate generation may learn from repetition, but project or enterprise promotion requires governed approval.
