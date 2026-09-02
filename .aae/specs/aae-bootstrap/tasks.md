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
- **Completion contract:** Native and adapted skills compile into a normalized digest-bearing registry; invalid advertisements fail validation; discovery produces a bounded metadata-only shortlist; full procedures load only through an allowed InvocationPlan; initialized projects contain working starter skills and source configuration; documentation distinguishes capabilities, skills, roles, tools, models, workflows, and enforcement.
- **Verification:** Run the standard-library unit suite, repository validation and compilation, registry and discovery smoke tests, built-wheel initialization, and initialized-project validation/compilation/discovery.
- **Evidence:** Superseded by T-007 verification for the governed invocation path.

## T-007 — Govern skill invocation end to end

- **Status:** verified
- **Requirements:** REQ-011, REQ-012, REQ-013, REQ-014, REQ-015
- **Completion contract:** Identical content under arbitrary locations has identical portable identities; demand and selection retain provenance; direct loading fails closed; one real invocation crosses demand, registry, candidates, selection, plan, policy, load, record, and outcome; negative tests prove denial for missing capabilities, isolation, approval, data authorization, invalid policy, and plan tampering; schemas ship in initialized projects; concurrent telemetry remains valid; type checks, unit tests, repository validation, two-version CI configuration, wheel build/install, and initialized-project invocation smoke pass.
- **Verification:** Run mypy and unit tests, validate/compile the repository, parse packaged schemas, build and install the wheel in an isolated environment, initialize a fresh project, verify a denied invocation cannot expose instructions, verify an allowed invocation loads the selected procedure and writes an InvocationRecord, then record a verified outcome.
- **Evidence:** `python -m mypy src tests` passes; the 38-test standard-library suite passes, including portable identity, Windows-style normalization, policy-negative, tamper, concurrency, semantic, model-routing, lifecycle, history, and telemetry cases; repository validation and compilation pass with 8 skills and 35 capability names; the Python 3.10/current-Python CI matrix is configured; wheel `adaptive_agentic_engineering-0.3.0-py3-none-any.whl` (SHA-256 `4440d4859678ec1bae6772678be5b0c09bfa55565d44128fbbaa6440a48d5e54`) builds and installs without network dependencies; the installed CLI initializes 56 files, validates and compiles a fresh project, permits a locally authorized invocation, records its verified outcome, and denies an unauthorized provider without loading the procedure.
