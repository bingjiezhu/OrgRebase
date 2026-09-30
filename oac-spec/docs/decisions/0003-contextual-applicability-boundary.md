# ADR 0003: Contextual applicability without a policy DSL

**Status:** Accepted

**Date:** 2026-08-23

## Context

A declared dependency does not make every enterprise change applicable to every downstream obligation. Subject, scope and contextual facts must participate in the Profile's transfer relation, and missing context must remain unresolved.

## Decision

Use a small versioned, three-valued contextual predicate vocabulary whose concrete instances are admitted Snapshot facts. Keep `scopeRefs` as explicit context anchors, distinguish root Unknown from derived gaps, and bind applicability results to their exact witnesses in the Plan.

Preserve legacy resource bytes and version new semantic material. Profile rules derive obligations from source semantics; case IDs, benchmark labels and expected reports are not routing inputs.

## Alternatives

| Alternative | Reason not selected |
| --- | --- |
| Case-specific routing | Leaks expected labels into implementation behavior |
| General executable policy language | Expands the standard into vendor policy execution and weakens portable verification |
| Global scope allowlist | Loses legitimate paths whose intermediate nodes are not explicit anchors |
| Unversioned tags or in-place predicate replacement | Changes source meaning without preserving historical identity |

## Consequences

`UNKNOWN` remains distinct from denial or acceptance. A Plan can be checked against the selected admitted Profile without claiming complete organizational knowledge. The reference compiler and verifier share a public semantic implementation; independently maintained semantics and qualified human Ground Truth remain separate validation requirements.
