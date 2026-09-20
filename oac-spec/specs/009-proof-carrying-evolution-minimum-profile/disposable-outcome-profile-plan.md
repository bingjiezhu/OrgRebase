# Spec 009 extension plan — disposable local Outcome profile

The product's Spec 005 sandbox can perform real writes in a disposable environment under a separately granted, one-use controller authorization. The core accepted plan remains zero-effect and grants no execution authority. The original Spec 009 `X` root only accepts `RuntimeBinding` and `ZeroEffectRuntimeBundle`; lab writes cannot truthfully use those kinds.

Implement one explicit, package-pinned profile in the existing Outcome verifier. The default wire and root semantics remain unchanged. The profile requires disposable binding/bundle references, the existing external execution receipt, an independent sandbox grant, six dimensions and exact failure/unknown retention. It binds descriptor identity/version/digest and grant in a distinct execution-root domain. No runtime dispatcher, generic callback, new compiler, second outcome interpreter, production executor or automatic admission is introduced.

The profile contract is `profiles/outcome-profiles/disposable-local-v0.1/README.md`; its descriptor is normative for the explicitly selected extension. Registered resource schemas remain generated through `scripts/export_schemas.py`; descriptor and semantic registry are additional exports. The current `validate-evolution` CLI is the semantic verification entry point.

Validation covers default fixture bytes, original root vectors and two-dimension semantics; explicit ACCEPT/REJECT/UNKNOWN and mixed failure/unknown examples; profile/kind/grant/root/provenance attacks; missing dimensions; incomplete or cross-namespace evidence; oracle identity relabelling; schema/CLI/wheel consistency. Existing aggregate gates are run and reported truthfully. Any expected source-identity rejection is retained; root integration must freeze a new complete source closure.

The parent task owns actual controller payload validation, WorkUnit/happensBefore binding, public sandbox observations, product bridging and new integrated evidence. Contract fixtures do not count as real outcomes or independent human qualification.

Shared reference checks and domain-separated root projections live in `src/oac/evolution_roots.py` and are re-exported by `oac.evolution`; there is one implementation. This preserves the existing 500-nonblank-line semantic-gate boundary. The original default domains and public projection names remain available.
