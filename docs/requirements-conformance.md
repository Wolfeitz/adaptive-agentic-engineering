# Requirements conformance capability

The `requirements-conformance` skill complements `acceptance-verify` and `independent-review`. Use it in the selected runtime/HVE workflow where applicable.

The structural validator `python -m aae.requirements_coverage RECORD.json` checks source identifiers, evidence dispositions, authorized deferrals, and current PR head binding. **It does not discover applicable wiki pages or prove semantic correctness.** A reviewer must do that work, including asking for missing decisions. Full coverage does not mean approval.

Regression: Mediation PR #42 had completed overview-reading tasks but had not checked applicable detailed architecture/coding requirements. Coverage must remain partial even when all generated todos are done.

Do not globally require this procedure for minor changes with no applicable architecture sources. Use proportional, source-driven applicability. No native IDE hook interception is claimed.
