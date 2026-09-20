---
name: enterprise-launch-readiness
description: Map a fresh OrgRebase impact receipt to bounded launch-rebase actions without deciding canonical state.
version: 1.4.1
contract: contract.json
---

# Enterprise Launch Readiness

Use this Skill only when the input is a schema-valid
`orgrebase.launch-readiness-input.v1` adapter request with non-zero Preview and
approval receipt digests. The canonical Skill Contract version is `1.4` in
the adjacent `contract.json`; the original AgentTeams adapter remains at
`agentteams/skills/enterprise-launch-readiness/SKILL.md`.
(`version: 1.4.1` is the adapter label).

The package includes exact `input.schema.json` and `output.schema.json` bytes.
Runtime validation resolves only local bundled references and never fetches a
Schema from the network.

## Action mapping

- `AFFECTED_HARD` → propose `REBASE`.
- `AFFECTED_REVIEW` → propose `REVIEW`.
- `AFFECTED_INFORMATIONAL` → propose `NOTIFY`.
- `UNAFFECTED_WITHIN_DECLARED_BOUNDARY` → propose `KEEP_CURRENT` and preserve proof.
- `UNKNOWN` → propose `ESCALATE`; never guess affected or unaffected.
- Missing/invalid classification → `ABSTAIN`.

## Boundaries

- Never alter canonical Claim, Work, Deliverable, Preview, evaluator, or release state.
- Never read restricted Legal source text; only consume admitted minimal derived Claims.
- Never widen tools, domains, sensitivity, side effects, budget, or canary scope.
- Output the bound Preview and approval receipt digests, object id, action, reason code,
  canonical Skill Contract digest, and candidate digest.
- Accept only a compiled delegation task whose plan and task digests bind the exact
  ChangeSet, Preview, and RevisionLock; do not accept free-form scope expansion.
- On digest, schema, permission, or freshness failure, fail closed and request control-plane review.

## Validation and release

The deterministic verifier compares the v1.3 predecessor decision boundary and
v1.4 adapter on the same frozen
replay, held-out, permission, prompt-injection, malformed-input and negative-transfer
cases. These digests are caller attestations in the controlled-local slice, not
independent proof that an enterprise approval backend accepted the request. This
adapter cannot publish itself; a passing report only makes it Canary-eligible.
