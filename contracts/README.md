# Machine contracts

- `handoff.schema.json` fixes the Agent → control-plane trust boundary.
- `skill-action.schema.json` fixes the portable Skill output boundary.
- `agent-candidate-result.schema.json` requires exact digest-bound inputs and explicit
  authority prohibitions before a live Worker result can reach the ingestion verifier.
- Generated schemas under `schemas/` fix the DependencyManifest, ImpactCertificate,
  MinimalRebaseCertificate, and live Agent candidate-ingestion boundaries. Run
  `uv run python scripts/export_contract_schemas.py --check` to detect model/schema drift.
- The HTTP contract is generated from the running FastAPI service at `/openapi.json`.
- The receipt contract is the strict Pydantic `RebaseReceipt` model and its generated
  JSON Schema; exported receipts are additionally protected by a content digest.

Every contract defaults to closed (`additionalProperties: false` or Pydantic
`extra="forbid"`). Agents produce candidates; no agent-facing contract exposes a
canonical state mutation.
