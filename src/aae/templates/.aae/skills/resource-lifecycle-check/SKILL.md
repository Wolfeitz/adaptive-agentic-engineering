# Resource Lifecycle Check

## Outcome

Demonstrate that concurrent, repeated, batched, long-running, or resource-owning behavior has explicit ownership and safe startup, cancellation, shutdown, and cleanup semantics with verification capable of exposing leaks.

## Procedure

1. Enumerate created resources: workers, tasks, processes, threads, handles, subscriptions, timers, sockets, connections, caches, buffers, temporary files, and accelerator allocations.
2. Map who creates, owns, shares, cancels, drains, closes, retries, and observes each resource across success, error, timeout, and shutdown paths.
3. Identify unbounded growth, lost tasks, race windows, partial startup, duplicate registration, cancellation swallowing, and cleanup ordering risks.
4. Trace platform and framework shutdown semantics from authoritative documentation or exercised repository precedent.
5. Design bounded repeated-operation or soak checks with measurable baselines and post-run resource counts.
6. Include forced failure and cancellation at material lifecycle boundaries and require deterministic cleanup evidence.

## Completion contract

Every material resource has an owner and terminal path, shutdown and cancellation behavior is explicit, verification covers repetition and failure, and evidence can distinguish stable reuse from leaked or orphaned resources.
