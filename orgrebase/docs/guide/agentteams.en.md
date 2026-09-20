# AgentTeams collaboration

OrgRebase uses a pinned AgentTeams source identity. Exact versions, commits and file digests are recorded in `agentteams/source-lock.json` and `agentteams/teamharness-lock.json`. A label such as “latest” cannot replace those bindings.

## Roles and tasks

Product, Legal, Finance and GTM prepare candidates within their own domains. The four domain Workers in the native reference path are deterministic programs; the Reviewer can call the configured model provider. A subsequent ChangeSet uses deterministic independent contract review. The Reviewer checks the domain set, result roots and contract bindings; a model's opinion has no final approval authority. Context is bounded by task and purpose. Restricted legal source text does not become available to GTM merely because GTM produces the deliverable.

The native task flow creates, delegates, acknowledges, submits, checks and completes tasks through TeamHarness projectflow/taskflow. Each subsequent change binds its own ChangeSet, preview and task receipts. Initial Formation and later changes can be inspected separately.

## Evidence recovery for the same ChangeSet {#evidence-recovery}

In native execution mode, the exact approval owner can return an unapproved, ungrouped proposal for evidence. A user with proposal permission can then resume execution:

1. The approval owner selects a domain task, gives a reason and specifies evidence references within that task's scope.
2. A user with proposal permission supplies current evidence references and exact digests from the API, and selects a registered primary or backup executor instance.
3. The system preserves the original event, ChangeSet, prior context and candidate, and creates a new recovery round, preview, independent review and AT task receipts.
4. The owner reviews the new result and approves its updated recovery digest. An executor then uses the existing Apply entry point to make the Quote effective.

Recovery accepts only already admitted, same-domain source objects in CURRENT or ACTIVE state. Their complete content and the prior candidate enter the bounded execution context. This entry point does not upload arbitrary material or change the business owner. A backup instance uses the registered capability and the same provider, currently through controlled local AT execution. It is not an enterprise personnel authority transfer or a new remote Agent service.

| API | Input and purpose |
|---|---|
| `GET /api/workspace/changes/{event_id}/recovery` | Read state, `allowed_actions`, task/evidence/executor options, context digests and round history |
| `POST /api/workspace/changes/{event_id}/return-for-evidence` | `operation_id`, `expected_context_digest`, `task_id`, `reason`, `required_evidence_refs`; requires the exact approval owner |
| `POST /api/workspace/changes/{event_id}/resume` | `operation_id`, `recovery_digest`, `executor_id`, and `ref`/`digest` entries in `evidence`; requires proposal permission |

Use the current GET response for available actions and field values. Cookie sessions still require Origin and CSRF validation. After recovery, approval must bind the new `recovery_digest`. Replaying the same operation identity and request reuses existing records without redispatching the model. After an explicit FAILED result, the owner must open a new evidence round. RESULT_UNKNOWN does not enable return and redispatch. Approved, applied, rejected or grouped proposals cannot use this recovery entry point. Evidence submission and review do not write the official Quote.

## Execution scope

Use [Vertex](models-vertex.en.md) or [DeepSeek](models-deepseek.en.md) for the documented cloud-model paths. A real model call, a local AT task flow and a production distributed deployment are separate claims. Reference transport uses controlled local Matrix/object-storage fixtures, not an already deployed external Element cluster.

The optional Element observer publishes minimal observations of the same run. It cannot approve or apply a change and does not prove that Workers received tasks over Matrix. Inspect the business result in WebUI first, then inspect task, Tool and Skill evidence.

Original-language references: [Agent roles](../AGENT-IDENTITIES.md), [Matrix observation](../MATRIX-OBSERVATION.md).
