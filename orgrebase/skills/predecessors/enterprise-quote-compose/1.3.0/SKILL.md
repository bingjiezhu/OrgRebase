---
name: enterprise-quote-compose
description: Execute the exact evaluated quote-composition candidate as a side-effect-free controlled release artifact.
version: 1.3.0
contract: contract.json
---

# Enterprise Quote Compose

Use this package only for the exact hardened declarative program bound by
`package.json`. Version 1.2 adds field-driven fail-closed security gates before
the functional mapping. Version 1.2.1 bundles exact input/output JSON Schema
bytes and validates both sides without network Schema resolution.
The historical `SkillCandidateArtifact` remains `executable=false`; this package is
a separate release artifact authorized by the Skill Registry Authority.

## Required behavior

1. Require `skill_partition`, `candidate_program_digest_required`, non-zero
   `dependency_tool_receipt_digest` / `dependency_result_digest`, the exact
   `coalition_result_binding_digest`, and four explicit Product / Legal /
   Finance / GTM `domain_result_digests`.
2. Reject a caller whose requested program digest is not the exact packaged
   program content digest.
3. Execute only `REQUIRE_FIELDS`, `MAP_VALUE`, and `RETURN_FIELD` operations.
4. Return a candidate-only result with `target_writes=0`.
5. Deny permission expansion, safely abstain on injection, malformed input, or
   resource/deadline failure.
6. Refuse composition when any coalition domain root is absent, substituted,
   zero, duplicated under another domain, or outside the packaged Schema.
7. Permit rollback only through the Skill Registry Authority to the exact
   content-addressed `1.2.0` archive. The rollback controller must load and
   invoke those predecessor bytes; a digest-only decision is insufficient.

## Claim boundary

This controlled adapter proves exact packaging, loading, restricted invocation,
evaluation, and release wiring. It does not prove that the system learned a Skill
from employee behavior or that the mapping generalizes to a new enterprise.
Executable rollback is scoped to the retained `enterprise-quote-compose@1.2.0`
package and remains candidate-only with zero target writes.
