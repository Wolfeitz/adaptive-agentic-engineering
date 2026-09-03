# Hooks and Events

Hooks use one rule:

```text
X happens -> do Y
```

A hook decides when work is useful. A skill defines how an agent performs reusable reasoning. A direct check handles deterministic work without involving an agent.

## Configuration

Rules live in `.aae/hooks.json`. Seeded examples are disabled so initialization never starts surprise automation.

Request a skill:

```json
{
  "id": "check-migration-safety",
  "on": "files-changed",
  "paths": ["migrations/**"],
  "request_skill": "project:migration-safety-check",
  "task": "Review the changed migrations for deployment and rollback risk"
}
```

Run a deterministic check directly:

```json
{
  "id": "test-python-changes",
  "on": "files-changed",
  "paths": ["src/**/*.py", "tests/**/*.py"],
  "criterion": "The Python test suite passes.",
  "run_check": ["python", "-m", "unittest", "discover", "-s", "tests"]
}
```

Each rule defines exactly one of `request_skill` or `run_check`. `run_check` is an argument list executed directly without a shell. Because this is executable project configuration, it should be reviewed like CI configuration. Set `destructive: true` when a check can make destructive changes; the event then requires `--approval destructive`.

Every direct check emits a `deterministic-control` criterion result. The stable
evaluator is selected by AAE from the rule type, never from criterion prose.
The result binds the command identity, exit code, output digests, event ID, and
rule ID. An invocation can require that proof with `--control-check RULE_ID` and
join the resulting event with `aae outcome --control-event EVENT_ID`.

## Emitting events

```bash
aae event files-changed \
  --data 'paths=["migrations/0004_add_index.sql"]' \
  --idempotency-key CI_RUN_ID:files-changed \
  --tool filesystem-read
```

Replace `CI_RUN_ID` with the actual provider delivery or run identifier. A webhook adapter remains responsible for authenticating the sender and normalizing its payload before calling AAE; AAE does not bundle an unauthenticated HTTP listener.

After enabling the configured `verify-python-changes` rule, a
criterion-governed invocation uses the actual invocation and event IDs returned
by the preceding commands:

```bash
aae invoke "implement the change" \
  --skill project:implementation-preflight \
  --tool filesystem-search --tool version-control-read \
  --acceptance "The implementation matches the task." \
  --control-check verify-python-changes

aae event files-changed \
  --data 'paths=["src/example.py"]' \
  --for-invocation INVOCATION_ID

aae outcome project:implementation-preflight succeeded \
  --invocation-id INVOCATION_ID \
  --criterion-result 'passed:The implementation matches the task.' \
  --control-event EVENT_ID \
  --evidence verification.json
```

Replace `INVOCATION_ID` and `EVENT_ID` with the values printed above.
`verification.json` must be a real repository file; alternatively pass a
precomputed evidence reference as `sha256:<64-hex-digest>`.

Control proof is bound to the invocation ID, so an old passing event cannot be
replayed as evidence for a later invocation.

## Records

AAE stores a payload digest rather than the raw event payload. Reusing an idempotency key for the same event returns the prior record and does not invoke the skill or check again. A skill invocation links back to the hook rule through trigger provenance. Small internal depth and fan-out limits prevent accidental loops; they are implementation safeguards, not a user-facing rule language.
