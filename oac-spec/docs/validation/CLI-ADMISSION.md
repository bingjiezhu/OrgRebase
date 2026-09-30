# CLI input admission

The public `oac compile` and `oac verify` commands require `SealedResource/v1` inputs. Each JSON object carries a raw-map digest: SHA-256 of RFC 8785 canonical JSON after removing only its top-level `digest`. Schema defaults omitted from the supplied input do not enter that identity.

Duplicate members, incorrect digests, invalid Kinds and Profile mismatches are rejected before a Plan is written. The commands do not repair, reseal or silently replace user inputs.

For an admitted Snapshot and Change, the generated Plan retains their exact resource digests. The same files can be supplied to `verify`. Supplier is the default Profile; retail requires the explicit `--profile oac.retail.cancellation-review/v0.1` selection on both commands. Use the [public CLI examples](../../README.md) with inputs belonging to the selected Profile.

Python callers holding admission records can use `oac.compiler.compile_change_from_admitted(snapshot_admission, change_admission, profile=...)`. The entry point revalidates the records; constructing a dataclass does not grant admission.

The typed `compile_change`, `compile_supplier_change` and `oac demo` interfaces retain their declared typed-resource contract. A historical typed fixture may have a model-materialized digest that differs from raw-map admission. The raw CLI rejects such an input rather than selecting an implicit legacy fallback. Exact admission and [Plan reproduction](PLAN-REPRODUCTION-RAW-ADMISSION.md) use the same declared resource identity.
