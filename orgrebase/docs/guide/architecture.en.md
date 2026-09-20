# Architecture and authority

## One business flow, four decision boundaries

| Layer | Responsibility | Does not own |
|---|---|---|
| AgentTeams tasks and candidates | Task creation, delegation, handoff, terminal execution facts and candidate provenance | Business admission or approval |
| Deterministic control-plane admission | Source, scope, dependency, authorization and completeness checks | The owner's high-risk business decision |
| Exact Human Owner approval | A specific digest and version within the owner's scope | Another owner's object or a stale version |
| StateStore / RebaseWorkflow canonical write | Transactional successor objects, graph, receipts and pointers | Trust in unadmitted candidates |

These do not require four separate services. `AT completed`, `Candidate admitted`, `Reviewer accepted`, `Human approved` and `Canonical applied` are different facts.

## OAC and team formation

OAC supplies contracts and validation semantics for organization, knowledge, authority, capabilities and handoffs. In the current Quote path, the OrgRebase control plane forms the team and compiles the AT execution plan. Quote parity receipts are not OAC OrganizationPlans or PlanCertificates. Teams may change across tasks. An admitted plan is immutable; evidence recovery creates a new execution round and preserves earlier plans, inputs, candidates and receipts.

## Selective Rebase

Observed reads and exact source versions form dependencies. Preview distinguishes required rebuilds, preservation within declared coverage, and unknowns. Incomplete coverage is not evidence of non-impact. Approval binds the exact preview, owner and validity window. Internal successor state commits transactionally; external effects use separately authorized requests and reconciliation, not an assumed atomic transaction across arbitrary CRMs.

Detailed original-language references: [Architecture](../ARCHITECTURE.md), [Apply transaction](../APPLY-TRANSACTION.md), [OAC boundary](../OAC-ORGREBASE-PRODUCT-BOUNDARY.md).
