# Compile and verify share exact input admission

The public `oac compile` and `oac verify` commands both require `SealedResource/v1`
inputs. The JSON object must carry its raw-map digest: SHA-256 of RFC 8785 JCS
after removing only the top-level `digest`. Schema defaults do not enter that
identity when they are absent from the supplied JSON. Duplicate members,
incorrect digests, invalid kinds and admission mismatches fail before a plan is
written. The commands do not repair or reseal the user's input.

For an admitted Snapshot and Change, the generated Plan retains their exact
resource digests. The same files can therefore be passed directly to `verify`.
Supplier remains the default profile. Retail continues to require the explicit
`--profile oac.retail.cancellation-review/v0.1` selection on both commands.

Python callers holding admission records use
`oac.compiler.compile_change_from_admitted(snapshot_admission, change_admission,
profile=...)`. The entrypoint revalidates these data records; constructing a
dataclass does not grant admission. It uses the existing admitted derivation
relation and the same plan construction strategy as the typed SDK.

`compile_change`, `compile_supplier_change` and `oac demo` retain the historical
typed-resource contract. Historical v0alpha1 fixtures may carry digests of the
model-materialized projection and need not pass raw-map admission. Their bytes
and historical demo digests remain unchanged. Such a fixture is rejected by
`compile` at admission, rather than producing a plan that the current `verify`
command cannot consume. No implicit legacy fallback or second compiler exists.

The previous command mismatch was reproduced both on the exact 861-file shared
baseline and on an independently installed integration wheel. The correction
is covered by real subprocess compile-to-verify tests for dense and sparse
Supplier and retail inputs, invalid raw inputs, forged admission records and
the unchanged historical demo. It changes CLI input admission, not the plan
relation, schemas, profile scope or execution authority.
