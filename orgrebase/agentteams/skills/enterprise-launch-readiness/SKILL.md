---
name: enterprise-launch-readiness
description: Map a fresh OrgRebase impact receipt to bounded launch-rebase actions without deciding canonical state.
version: 1.3.0
contract: ../../../skills/enterprise-launch-readiness/legacy-contract-v1.3.json
---

# Enterprise Launch Readiness

Use this Skill only when the input is a schema-valid `orgrebase.impact-result.v1`
from a fresh, approved Preview. The legacy adapter Skill Contract version is `1.3` in
`skills/enterprise-launch-readiness/legacy-contract-v1.3.json`; this file is the AgentTeams adapter
(`version: 1.3.0` is the adapter label).

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
- Output the input Preview digest, object id, action, reason code, adapter version,
  canonical Skill Contract digest, and candidate digest.
- Accept only a compiled delegation task whose plan and task digests bind the exact
  ChangeSet, Preview, and RevisionLock; do not accept free-form scope expansion.
- On digest, schema, permission, or freshness failure, fail closed and request control-plane review.

## Validation and release

The deterministic verifier compares no-skill, v1.2 and v1.3 on the same frozen
replay, held-out, permission, prompt-injection, malformed-input and negative-transfer
cases. This adapter cannot publish itself; a passing report only makes it Canary-eligible.
