# ADR 0003: Contextual applicability without a policy DSL

**Status**: Accepted
**Date**: 2026-08-23
**Decision authority**: user-authorized Hard self-grill

## Decision

Implement Spec 002 before a second verifier or runtime. Add one small versioned, tri-valued contextual
predicate language whose concrete instances are admitted Snapshot facts. Preserve legacy source bytes,
keep `scopeRefs` as opt-in context anchors, separate root Unknown from derived gaps, and bind every
applicability result to exact witnesses in the Plan.

## Why this is first

SC-008 through SC-010 expose observed subject, scope, and Unknown failures. Specs 003–006 depend on a
stable Profile surface or unavailable external evidence. Replicating v0alpha1 in a second language now
would only duplicate known ambiguity; implementing Human Gold, enterprise outcomes, or learning without
reviewers/runtime oracles would create false evidence.

## Rejected alternatives

- **Case-specific routing**: reaches 10/10 by leaking exploratory labels and proves nothing reusable.
- **General policy language**: duplicates OPA/Cedar-style concerns and makes independent verification
  depend on executable vendor logic.
- **Global scope allowlist**: breaks legacy paths whose intermediate nodes are not named anchors.
- **Unversioned tags**: turns ungoverned strings into business facts; deferred until an admitted context
  fact model exists.
- **Rewrite old predicates in place**: changes canonical roots and destroys the existing evidence chain.
- **Second implementation first**: standardizes an already observed semantic hole.

## Observed result and claim boundary

The implementation closes the three contextual role/verdict failures without case-ID inspection:
candidate roles and verdicts agree on 10/10 development cases. A newly explicit obligation-type
metric agrees completely on only 5/10; SC-003 through SC-007 remain visible Profile-evidence gaps.
Therefore the result is bounded internal consistency on two dimensions, not “10/10 all constraints”.

The reference compiler and verifier still share one public semantic implementation. Spec 003,
qualified human review, and enterprise outcome evidence remain separate gates.
