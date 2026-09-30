# Public source iterations

The development workspace is the canonical source for public code, documentation,
package metadata, licenses and contributor automation. A public iteration selects
files from that source without changing their contents or executable bits.

From the workspace containing `orgrebase/` and `oac-spec/`, create a new iteration:

```bash
python3 -B orgrebase/scripts/export_public_release.py build \
  --workspace . --output 可执行代码版本迭代/2026.9.30
python3 -B orgrebase/scripts/export_public_release.py check \
  --workspace . --release 可执行代码版本迭代/2026.9.30
```

The output directory must not already exist. The exporter creates:

- `github-root/`: the fixed set of formal repository files, copied from the
  workspace root. Place their contents at the GitHub repository root.
- `orgrebase/` and `oac-spec/`: product source selected by the existing GitHub
  source snapshot policy.
- `SOURCE-MANIFEST.json`: relative source paths, SHA-256 digests, executable bits,
  selection policy and exclusion reasons. Keep this beside the upload trees as
  the local iteration record.

Only the three upload trees form the GitHub source layout. Internal plans,
acceptance work records, credentials, customer materials, local databases,
caches and earlier release directories are excluded by policy. Exclusion does
not permit a public README, source file, version string or license to differ from
its canonical source. The exporter rejects symlinks, unsafe files, detected
credentials and machine paths using the source snapshot checks.

Make changes in the canonical workspace first. Run the checks appropriate to the
change, then create a new iteration and run the parity check before preparing an
upload. Preserve earlier iterations. Changing admitted source after export makes
the previous iteration fail the current-source check; create a subsequent
iteration rather than modifying its archived files or manifest.

The parity check verifies every admitted file in all three trees. Modified,
missing or extra files, changed executable bits, newly admitted source files and
changed selection policies fail the check. It does not publish to GitHub, build
distribution artifacts, run product tests or establish production qualification.
Build and verify source archives, packages and the documentation site separately
from the same frozen source.

## Historical facts

The packaged `evidence/release-facts.json` is the immutable `0.4.0` compatibility
coordinate. `generate_release_facts.py --check` verifies its pinned digest and
recomputes its retained evidence projection. The comparison excludes only the
current version and observations of mutable project/lock hashes; it still checks
the retained artifact hashes, results and claim boundaries. This does not qualify
the current build. The HTTP API labels the historical release separately from
the running version.

New development aggregation defaults to `evidence/development-release-facts.json`,
which is excluded from public source and package assets. The generator rejects
writes to the frozen path. A new release fact coordinate requires its own evidence
and a separately reviewed admission, rather than relabeling retained results.

## License metadata and SBOM

Normal and offline distributions emit the current SPDX `License-Expression` and
retain the original license and notice files. The SBOM binds the project metadata,
lock and artifact hashes. To include supplier declarations, repeat
`--license-metadata path/to/METADATA` for captured distribution metadata. Only exact
package name/version matches with a valid SPDX expression produce a declaration.
Missing, ambiguous, invalid or legacy-only license fields remain `UNKNOWN`;
classifiers and package names are not inferred license grants. Metadata hashes
contribute to SBOM identity, and conflicting evidence is rejected. This inventory
does not certify redistribution compliance or replace third-party terms.

The dependency graph is the full lock's **potential graph**, including optional
extras and development groups on every supported platform. It does not claim
that all listed packages are installed in one runtime. Each component retains
its lock edges, selected extras, group names and environment markers; consumers
can distinguish conditional branches from unconditional dependencies. Explicit
version and source selectors must resolve to one locked package. Missing or
ambiguous targets fail generation. The graph policy and resolved edges are
included in the deterministic SBOM identity.
