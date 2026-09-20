# Current reproduction after default Outcome compatibility

The active fixed-Plan reproduction coordinate is `v0.1-repro-5`. This supersedes only
the current-coordinate selection in `PLAN-REPRODUCTION-RAW-ADMISSION.md`; historical
documents and reproduction directories remain unchanged.

Restoring default Outcome provenance changed `evolution.py` and `sealed.py`. Both are
inside the existing complete Python source-material closure. The original repro-4
source ledger cannot represent these new bytes, even though the change is scoped to
default Outcome certificates. A new reproduction therefore retains the existing
current-source exact replay contract, rather than substituting historical replay or
the separate 39-case Plan parity check.

The writer anchors repro-4's original detached and raw manifest digests and recursively
checks all ancestor artifacts before and after generation. It refuses an existing
output directory. The three incidents, deletion operators, reason policy, both real
adapters and final whole-tree byte comparison are unchanged. No runtime module, new
business experiment or alternative evidence protocol is introduced.

`--check` now compares the complete frozen source-material paths and bytes with the
current tree before building either adapter or generating observations. Missing,
additional and changed source materials immediately fail. This prevents thousands of
unnecessary calls when the final exact comparison is already known to be impossible.
A successful source preflight is not a successful replay: the independent complete
generation, runtime/build checks and final artifact comparison are still required.

Finalize the bound script before generating a new coordinate. Then run one generation
and one independent replay with the same environment:

```sh
.venv/bin/python scripts/minimize_plan_verification_disagreements.py
.venv/bin/python scripts/minimize_plan_verification_disagreements.py --check
```

Record their actual exit codes, elapsed times, source bindings and observations outside
the immutable coordinate. A generation failure publishes no partial lineage; a replay
failure is retained rather than repaired by overwriting its evidence. The new coordinate
is expected to add approximately 91 files and 10 MB of versioned reproduction evidence;
the final inventory is measured after generation. It is not an increase in production
services or an enterprise effectiveness claim.
