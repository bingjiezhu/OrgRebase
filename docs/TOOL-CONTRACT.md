# Agent tool contract

OrgRebase exposes two `ToolContract` surfaces. Agents may call the read-only evidence tool. Canonical writes use a separate control-plane Git contract.

## `orgrebase.read_dependency_evidence`

This is the Agent-facing tool. It reads admitted dependency evidence for an exact graph revision and cannot write canonical state.

The canonical machine contract is returned by
`GET /api/tools/v1/dependency-evidence/contract`. Invocation is
`POST /api/tools/v1/dependency-evidence` with `X-OrgRebase-Actor`, `target_ids`,
`graph_revision`, and `idempotency_key`.

The response wrapper is `{contract, result, receipt}`. `result` contains
`graph_revision`, admitted `edges`, and manifest `coverage`; `receipt` is a
schema-exported `ToolInvocationReceipt`.

The contract declares protocol, authentication replacement, JSON input/output, errors,
permission ceiling, retry policy, idempotency, audit fields, degradation, and MCP migration.
Every successful local invocation emits an immutable `ToolInvocationReceipt` and a
digest-chained `TOOL_INVOKED` event. If the tool is unavailable, certainty may fall to
`UNKNOWN`; failure can never be interpreted as `UNAFFECTED`.

The Workspace demo executes this tool after Quote v1. The invocation and matching
`ToolCalledEvent` are exported under `evidence/workspace/latest/tool/` and share
`run:workspace:complete@v1`, tool ref, receipt ref, request digest, and result digest.

## `orgrebase.git_downstream_artifact`

Canonical writes use this control-plane-only contract. It rejects Agent identities, network access, paths outside
`downstream/`, dirty worktrees, HEAD/content drift and cross-run compensation. PATCH creates
a real commit with approval/request trailers; COMPENSATE creates a real `git revert` and
verifies the original byte digest plus `git fsck`. A three-stage SQLite journal closes the
external-commit/local-receipt crash window. The contract is served at
`GET /api/tools/v1/git-artifact/contract`.

MCP is not required for these two boundaries. A future MCP adapter reuses the same
schemas and domain services without changing authorization or business logic. The
`mcp_migration` field on each `ToolContract` records that cost: protocol adapter only,
not a redesign of the call chain.

## Equivalent integration contract (contest §9.2)

MCP is recommended, not required. The handbook still requires an equivalent tool
contract when MCP is unused. Both OrgRebase tools publish this machine contract:

| Required field | `orgrebase.read_dependency_evidence` | `orgrebase.git_downstream_artifact` |
|---|---|---|
| Protocol | HTTP POST JSON (`/api/tools/v1/dependency-evidence`) | HTTP/local control-plane contract (`/api/tools/v1/git-artifact/contract`) |
| Authentication | `X-OrgRebase-Actor`; production replacement is short-lived workload identity | Rejects Agent identities; control-plane only |
| Input / output Schema | JSON Schema on `ToolContract`; additionalProperties false | PATCH/COMPENSATE schemas with digest fields |
| Error handling | `AUTHZ_DENIED`, `GRAPH_REVISION_MISMATCH`, `INVALID_TARGET`, `IDEMPOTENCY_CONFLICT` | Dirty tree, path escape, digest drift, cross-run compensation |
| Audit | Immutable `ToolInvocationReceipt` + `TOOL_INVOKED` event | Commit/revert trailers + SQLite journal |
| Retry / idempotency | Safe retry; actor+request idempotency; conflict fail-closed | Compensation is `git revert` of the exact commit |
| Degradation | Failure may yield `UNKNOWN`; never `UNAFFECTED` | Fail closed; no silent skip |
| MCP migration | Same JSON Schemas as one read-only MCP tool; business logic unchanged | Same; control-plane write tool stays out of Agent MCP surface |

Name, entrypoint, parameter Schema, return structure, permission ceiling, retry,
idempotency, audit log, and degradation are all on `ToolContract` in `src/orgrebase/domain.py`.
