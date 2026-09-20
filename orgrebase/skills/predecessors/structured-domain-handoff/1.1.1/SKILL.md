---
name: structured-domain-handoff
description: Return a task-bound, authority-scoped Domain candidate bundle for OrgRebase Workspace without normative write authority.
version: 1.1.1
output_schema: orgrebase.domain-transport-candidate.v1
---

# Structured Domain Handoff

Use this Skill only for an exact
`orgrebase.structured-domain-handoff-input.v1` adapter request. The adapter input
binds run, task, delegation, delegation-task digest, context-projection digest,
and a structured candidate bundle. It is not the richer normative
`DomainDelegationTask`; the control plane owns that contract and compiles this
minimal package-specific projection. The package carries exact input/output JSON
Schema bytes; the runtime uses only local bundled references and fails closed
instead of resolving a network Schema.

## Required behavior

1. Read only the supplied actor context projection and the declared Domain tools.
2. Return a schema-valid, candidate-only Domain bundle whose bytes and digest are
   preserved by the transport.
3. Cite exact source refs and input refs for every proposed Claim.
4. Preserve the run ID, task ID, delegation ID, delegation-task digest, and
   context-projection digest.
5. Abstain on missing authority, stale inputs, purpose mismatch, schema failure,
   deadline expiry, or any request to expand context, tools, output schema, or
   recipient scope.

## Authority boundary

- This Skill cannot admit Claims or Policies.
- It cannot mark a Work item current, stale, affected, or unaffected.
- It cannot write the Workspace graph or choose VMRC effects.
- It cannot approve or apply a ChangeSet.
- It cannot publish or activate itself.
- Legal raw restricted source text must never leave the Legal Worker; only a
  purpose-bound minimal derived Claim may be returned.

## Evidence

The controlled-local package only proves deterministic adapter wiring. A live
result remains usable only when Kubernetes Worker identity and generation,
Matrix membership/sender/event, exact candidate artifact bytes, Skill digest,
Workspace contract digest, run nonce, and provider request ID correlate to the
same frozen run. That external proof remains `NOT_RUN`.
