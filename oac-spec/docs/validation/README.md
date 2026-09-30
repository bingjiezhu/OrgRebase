# Validation and reproducibility

OAC validation is scoped to an exact Profile, resource version, implementation and input set. A passing schema check, accepted Plan, completed execution and accepted Outcome are different results. None grants runtime permission or enterprise source authority.

## Public source checks

The public workspace contains the product and OAC as adjacent projects. Use CPython 3.12.13, Go 1.22 and the locked development environment:

```sh
cd oac-spec
uv sync --locked --all-extras
cd ..
python3 -B orgrebase/scripts/build_source_snapshot.py public-check --snapshot-root .
```

The public gate checks schemas and registries, the bounded mechanics benchmark, CTK bundles and successor behavior, the separate CTK runner, the included Go implementations, runtime lowering, TCK and selected public API tests. The Python and Go implementations belong to the same project; cross-language agreement does not establish organizational independence.

See the [OAC README](../../README.md) for CLI examples and installation. Historical archive replay needs the matching complete archive inputs and is outside this public source profile. Missing archives must not be treated as successful checks.

## Validation boundaries

| Boundary | Required check |
| --- | --- |
| Input admission | Exact sealed bytes, detached digest, expected Kind and Profile; see [CLI admission](CLI-ADMISSION.md) |
| Plan acceptance | Admitted source roots, obligation coverage, qualification, authority, ordering, evidence and explicit Unknown constraints |
| Runtime lowering | A contract-valid Plan lowers under the selected handler/effect ceiling; acceptance does not authorize execution |
| Execution and Outcome | Execution evidence records completion; an Outcome requires its own admitted observations and acceptance relation |
| Reproduction | Source, inputs, dependency identities and expected observations are bound to the supplied coordinate; see [raw-input reproduction](PLAN-REPRODUCTION-RAW-ADMISSION.md) |
| Historical compatibility | Frozen coordinates remain immutable; see [evidence versioning](HISTORICAL-EVIDENCE-VERSIONING.md) |

Reports must identify the exact inputs and implementation, rejected and unresolved cases, tested constraints and omitted coverage. `UNKNOWN` is preserved as unresolved knowledge, and protocol rejection is distinct from a completed domain rejection. The included reference checks do not establish complete OAC conformance, external human Ground Truth, enterprise effectiveness, production safety or authorization.
