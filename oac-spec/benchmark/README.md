# OrgChangeBench exploratory seed

This directory contains **mechanics-only exploratory evaluation, not performance or business
results**.

- `matched-inputs.json` freezes the same ten `OrgChangeCase` resources for every system.
- `baselines/` defines initiator-only, fixed-team, and graph-only mechanics policies.
- `runs/` contains deterministic matched-input manifests for the reference compiler and all three
  transparent policies.
- `data-readiness.json` reports both passing mechanics and blocking validity gates.

Every run publishes two auditable byte closures. `inputClosure` enumerates the matched-input
declaration plus every case, annotation packet, snapshot, and change resource actually read by the
scorer. `implementationClosure` enumerates the scorer/runner, local implementation modules,
dependency lock, and (for a baseline) its machine-policy definition. Their RFC 8785 closure digests
change if any listed byte changes; `fixtureSetDigest` binds the same input closure without the
matched-input declaration for backward-compatible fixture auditing.

All four systems are run over the exact same input closure. Scores measure agreement with
project-authored exploratory candidate role sets only. They are useful for exposing over-selection,
omission, and current Profile gaps; they do not support a model leaderboard, business outcome,
enterprise ROI, human Gold quality, or superiority claim. Negative and null findings are preserved.
