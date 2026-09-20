# Enterprise Quote Compose Guide

## Use this Skill when

The request asks to compose an enterprise quote, combine Product, Legal,
Finance, and GTM results, or produce a quote candidate. A request may be
written in English, Chinese, mixed language, or refer to a localized filename.
This Skill consumes already-produced domain candidates; it does not collect raw
enterprise data, admit facts, or write to a quoting system.

## Inputs and execution

1. Require an allowed `skill_partition`.
2. Bind `candidate_program_digest_required` to the exact packaged program.
3. Require non-zero Tool receipt/result, coalition root, and distinct Product,
   Legal, Finance, and GTM roots.
4. Execute only the packaged declarative mapping and return a quote candidate.

## Failure semantics

- Permission, restricted-source, or target-write expansion: `DENY`.
- Injection, malformed, stale, deadline, or resource failure: `ABSTAIN`.
- Negative transfer: `KEEP_CURRENT`.

## Output and authority

The output binds the Tool, coalition, and domain digests and remains
`candidate_only=true` with `target_writes=0`. Only the Skill Registry Authority
may move the release head or execute the exact retained predecessor rollback.
