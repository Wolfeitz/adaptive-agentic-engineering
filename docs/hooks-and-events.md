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
  "run_check": ["python", "-m", "unittest", "discover", "-s", "tests"]
}
```

Each rule defines exactly one of `request_skill` or `run_check`. `run_check` is an argument list executed directly without a shell. Because this is executable project configuration, it should be reviewed like CI configuration. Set `destructive: true` when a check can make destructive changes; the event then requires `--approval destructive`.

## Emitting events

```bash
aae event files-changed \
  --data 'paths=["migrations/0004_add_index.sql"]' \
  --idempotency-key CI_RUN_ID:files-changed \
  --tool filesystem-read
```

Replace `CI_RUN_ID` with the actual provider delivery or run identifier. A webhook adapter remains responsible for authenticating the sender and normalizing its payload before calling AAE; AAE does not bundle an unauthenticated HTTP listener.

## Records

AAE stores a payload digest rather than the raw event payload. Reusing an idempotency key for the same event returns the prior record and does not invoke the skill or check again. A skill invocation links back to the hook rule through trigger provenance. Small internal depth and fan-out limits prevent accidental loops; they are implementation safeguards, not a user-facing rule language.
