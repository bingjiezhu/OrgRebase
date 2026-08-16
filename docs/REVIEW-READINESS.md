# Review readiness

Companion to `configs/workspace/review-readiness.json` and `scripts/verify_review_readiness.py`.

## Current result

| Review question | Result | What is proven | Scope boundary |
|---|---|---|---|
| Is the real user and application scenario clear? | **Yes for the documented synthetic slice** | Sales Quote user story, Domain stakeholders, current workflow, product experience, deployment path, success criteria, and external validation protocol | Buyer demand and user usefulness are not yet externally validated; user study remains `NOT_RUN` |
| Do Agents form a complete task loop? | **Yes for the local deterministic reference profile** | Task → coalition → Domain candidates → admission/context → monitored Quote → dependencies/graph → two changes/restart → Skill → evidence | Workspace-specific K8s/Matrix/model AgentTeams run remains `NOT_RUN` without external evidence |
| Do core functions have verifiable material? | **Yes** | Tests, schemas, 192-case benchmark, baselines, ablations, receipts, certificates, 39-entry evidence index, negative paths, and legacy proof pack | Synthetic/local results are not production ROI or global graph completeness |
| Are model, Agent architecture, and tool interfaces clear? | **Yes** | Structured model requests/receipts, bounded repair, Agent authority matrix, Domain read contracts, reference monitor, AgentTeams transport, APIs, and tool ceilings | Live model/provider behavior is optional and needs provider request IDs |
| Are data authorization, licensing, and privacy risks addressed? | **Yes for the bundled synthetic release** | Asset/license inventory, minimum-disclosure flow, canaries, logging/retention policy, external-data gate, consent protocol, and risk register | Production connector retention, multi-tenant isolation, provider terms, DLP, and general inference privacy require deployment-specific work |

## Reproduction

```bash
make workspace-check
make workspace-review-readiness-check
```

The second command writes `evidence/workspace/latest/review-readiness.json` and fails if a required document/code/test/evidence path disappears, if local evidence drifts from the published state, if zero-participant user validation is mislabeled, if static/live AgentTeams evidence is mislabeled, if benchmark license/PII facts drift, or if macOS metadata re-enters the source tree.

## Current external milestones

```text
Workspace local deterministic E2E: PASS
OWB v1.1 open-core conformance: PASS on reference implementation
Governed Skill candidate: CANARY in deterministic evaluation
Workspace AgentTeams static/conformance: PASS
Workspace-specific live AgentTeams run: NOT_RUN
Real consented user validation: NOT_RUN
Real enterprise connectors / production ROI: NOT_RUN / not claimed
```

The older five-Agent live advisory evidence is preserved for the original Core slice. It does not upgrade the Workspace formation coalition.
