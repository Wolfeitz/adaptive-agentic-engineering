# Runtime Diagnosis

## Outcome

Identify the demonstrated cause of an observed runtime problem, or narrow it to explicit unresolved hypotheses, without implementing an unauthorized fix.

## Procedure

1. Define the observed symptom, expected behavior, time window, and affected boundary.
2. Establish checkout, build, process, container, listener, network, configuration-precedence, dependency, and data provenance relevant to that boundary.
3. Collect bounded logs, health, and persisted-state evidence without exposing secrets.
4. Build competing hypotheses and identify the cheapest safe check that distinguishes each one.
5. Run read-only discriminating checks first. Obtain authority before state-changing diagnostics.
6. Update hypothesis confidence from evidence and separate cause, contributing conditions, and unrelated defects.

## Completion contract

The result cites current runtime evidence, explains configuration and ownership provenance, distinguishes observation from hypothesis, and identifies either a demonstrated cause or the exact missing evidence needed next.
