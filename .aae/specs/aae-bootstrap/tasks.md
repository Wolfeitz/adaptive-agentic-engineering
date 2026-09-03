# AAE Bootstrap Tasks

## T-001 — Package repository templates

- **Status:** completed ✅
- **Requirements:** REQ-001, REQ-005
- **Evidence:** Template package and conservative `aae init` behavior.

## T-002 — Implement open-world discovery and overlays

- **Status:** completed ✅
- **Requirements:** REQ-002, REQ-003
- **Evidence:** CLI implementation and overlay-order test.

## T-003 — Prepare semantic compiler packet

- **Status:** completed ✅
- **Requirements:** REQ-004
- **Evidence:** Manifest/request generation and test.

## T-004 — Validate bootstrap

- **Status:** verified
- **Requirements:** REQ-006
- **Completion contract:** Unit tests pass, example validates, package installs, and a generated bootstrap compiles successfully.
- **Evidence:** Standard-library unit suite passes; repository and example validation pass; built-wheel installation, initialization, compilation, and validation smoke test pass.

## T-005 — Add configurable testing intent

- **Status:** verified
- **Requirements:** REQ-007
- **Completion contract:** New projects receive testing defaults and a local override example; adapters honor the effective policy; leak-prone behavior triggers resource-lifecycle consideration; no numeric coverage threshold is invented; bootstrap tests and validation pass.
- **Evidence:** Testing intent and local example are packaged; task and Codex adapters reference the policy; standard-library unit suite and repository validation pass.

## T-006 — Add capability and skill fabric

- **Status:** verified
- **Requirements:** REQ-008, REQ-009, REQ-010
- **Completion contract:** Native and adapted skills compile into a normalized digest-bearing registry; invalid advertisements fail validation; discovery produces a bounded metadata-only shortlist; basic safety checks run before procedure loading; initialized projects contain working starter skills and source configuration; documentation distinguishes skills from ephemeral executors.
- **Verification:** Run the standard-library unit suite, repository validation and compilation, registry and discovery smoke tests, built-wheel initialization, and initialized-project validation/compilation/discovery.
- **Evidence:** Superseded by T-007 verification for the governed invocation path.

## T-007 — Implement minimal skill invocation end to end

- **Status:** verified
- **Requirements:** REQ-011, REQ-012, REQ-013, REQ-014, REQ-015
- **Completion contract:** Identical content under arbitrary locations has identical portable identities; direct loading fails closed; one real invocation crosses advertisement discovery, basic safety checks, digest-checked loading, recording, and outcome; negative tests prove denial for missing tools, destructive approval, independence, and stale identity; four schemas ship in initialized projects; concurrent telemetry remains valid; type checks, tests, repository validation, wheel build/install, and initialized-project invocation smoke pass.
- **Verification:** Run mypy and unit tests, validate/compile the repository, parse packaged schemas, build and install the wheel in an isolated environment, initialize a fresh project, verify denied and allowed invocations, then record a verified outcome.
- **Evidence:** `python -m mypy src tests` and all 40 standard-library tests pass; repository validation and compilation pass with 8 skills and 35 capability labels; clean wheel `adaptive_agentic_engineering-0.3.0-py3-none-any.whl` has SHA-256 `f6ffb0682241968332ad8c22d8073c1c04acb64370f063086a48172d5cb11fe0`; isolated installation initializes 49 files, validates, compiles, discovers `repo-recon`, loads it when both required tools are present, and denies `acceptance-verify` without exposing its procedure when `test-execution` is absent.

## T-008 — Add simple hooks and event rules

- **Status:** verified
- **Requirements:** REQ-016
- **Completion contract:** Initialized projects contain disabled-by-default valid examples; an event plus optional path globs requests one skill or runs one argv-based check; raw payload is not persisted; duplicate delivery does not repeat the action; destructive checks require approval; hook and invocation records retain trigger provenance; CLI, validation, documentation, tests, and installed-package smoke pass.
- **Verification:** Run mypy and the complete unit suite, validate the repository, exercise both hook action types, and confirm idempotency and destructive denial in an installed project.
- **Evidence:** Hook tests cover disabled defaults, skill requests, path-based direct checks, destructive approval, invalid configuration, payload parsing, trigger provenance, and idempotent redelivery; the installed CLI accepts an arbitrary event name and a disabled seeded `files-changed` event completes as `no-match` without side effects.
