# OAC development TCK

`manifest.json` is a deterministic, repository-local development kit. It validates strict resource
parsing and detached digests, accepts two unchanged SC-001 v0.1 plan topologies, exercises contextual
subject/scope and root-Unknown plans, and rejects targeted structural and applicability-ledger
mutations. SC-009 derived uncertainty is `PROVISIONAL`; SC-010 root uncertainty is `UNKNOWN`.

Run it with:

```bash
uv run python tck/build_fixtures.py
uv run oac tck
```

`build_fixtures.py` is the single writer for contextual Snapshot successors, strict cases, positive
contextual plans, and plan mutations. The two v0.1 plural witnesses are immutable inputs: the current
verifier must accept their original bytes, and the generator never rewrites them. The generator makes
no network calls. A passing run proves only the mechanics enumerated in the manifest; it is not a
formal OAC conformance mark and says nothing about human annotation quality or enterprise outcomes.
