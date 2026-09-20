# Current reproduction after raw admission closure

The active fixed-Plan reproduction coordinate is `v0.1-repro-4`. This follow-up
supersedes only the current-coordinate selection in `PLAN-REPRODUCTION-SUCCESSOR.md`;
the historical document and reproduction directories remain unchanged.

The CLI, decoder and evidence provenance corrections changed source bytes bound
by the previous coordinate. The existing writer therefore publishes one successor
after those changes stabilize. It retains the exact `v0.1-repro-3` manifest and
anchors its raw and detached digests; the existing recursive history checker
verifies every older artifact. No additional evidence resolution protocol exists.

Both real adapters still execute every deletion candidate. `--check` independently
repeats generation and compares every artifact path and byte, including current
source, dependencies, runtime and rebuilt Go binary. It does not substitute a
historical integrity check or a weaker parity claim for current exact replay.

The predecessor has 91 files totaling 9,987,291 bytes; 8,631,652 bytes are trial
traces. A similarly sized new coordinate is the explicit storage cost of retaining
the existing source-bound reproducibility contract. It adds no production module,
service or execution path. The original three incidents, reason policy, deletion
operators and controlled-local claim limit remain unchanged.
