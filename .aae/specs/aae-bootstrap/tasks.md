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
