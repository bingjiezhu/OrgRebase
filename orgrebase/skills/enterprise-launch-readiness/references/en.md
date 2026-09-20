# Enterprise Launch Readiness Guide

## Use this Skill when

The request asks whether an impact requires Rebase, asks for the minimum action
for an `ImpactResult`, or checks enterprise launch readiness. Requests may be
English, Chinese, mixed language, or reference localized filenames. The input
must be a compiled control-plane impact result, not free-form authority changes.

## Decision mapping

- Hard dependency affected: `REBASE`.
- Human judgement required: `REVIEW`.
- Informational impact only: `NOTIFY`.
- Unaffected inside the declared boundary: `KEEP_CURRENT`.
- Insufficient evidence: `ESCALATE`.
- Invalid input: `ABSTAIN`.

## Inputs, output, and authority

Bind `object_id`, `reason_code`, Preview receipt, and approval receipt. The
output is only an action candidate and remains `candidate_only=true` with
`target_writes=0`. It cannot change canonical state, publish itself, or convert
an evaluation pass into enterprise approval.

## Failure semantics

Any digest, Schema, permission, freshness, or scope failure must fail closed and
request control-plane review.
