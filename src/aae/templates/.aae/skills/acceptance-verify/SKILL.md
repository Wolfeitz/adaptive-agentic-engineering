# Acceptance Verification

## Outcome

Issue a criterion-by-criterion completion verdict supported by evidence that exercises the changed behavior at the boundary users or dependent systems rely on.

## Procedure

1. Restate each acceptance criterion and map it to a verification method before judging completion.
2. Prefer deterministic checks, then integration or rendered/runtime evidence, and use independent review where consequence warrants it.
3. Exercise material failure, cancellation, repetition, concurrency, cleanup, migration, and rollback paths when applicable.
4. Distinguish code presence, test simulation, build success, deployed state, and observed user-visible behavior.
5. Grade evidence for relevance, freshness, provenance, independence, and completeness.
6. Report exclusions and pre-existing failures without hiding them or assigning them to the change without evidence.

## Completion contract

Every criterion has a pass, fail, or explicitly blocked verdict with cited evidence; required runtime and lifecycle behavior is exercised; exclusions and uncertainty are visible; status is not promoted beyond the evidence.
