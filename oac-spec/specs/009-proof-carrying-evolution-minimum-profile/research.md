# Research — Spec 009

Existing standards already cover important adjacent layers: MCP covers tool/resource interaction; A2A covers
Agent discovery, tasks, messages and artifacts; OASF covers Agent/skill/domain metadata; CloudEvents covers an
event envelope; OpenTelemetry covers telemetry; TOSCA and Kubernetes controllers show declarative
reconciliation patterns. None of those records the full enterprise chain from admitted organizational Source
and Demand through independently certified outcome to governed immutable successor.

Dynamic Agent graphs are also prior art: GPTSwarm, AFlow, AgentNet and ADAS optimize Agent/workflow structures.
Therefore OAC must not claim novelty for dynamic orchestration. The defensible narrow waist is
proof-carrying organizational reconciliation with explicit authority separation and counterexample-retaining
governed evolution.

Experience-driven self-evolution can degrade safety even when experience appears benign. That finding supports
the permanent rule that execution experience is candidate evidence, never self-promoting authority. A public
ground-truth portability experiment belongs in the product's separately authorized Spec 040; it is not evidence
for this bounded standard slice.

Primary references:

- [A2A specification](https://a2a-protocol.org/latest/specification/)
- [MCP specification](https://modelcontextprotocol.io/specification/2026-07-28)
- [OASF](https://github.com/agntcy/oasf)
- [GPTSwarm, ICML 2024](https://proceedings.mlr.press/v235/zhuge24a.html)
- [AFlow, ICLR 2025](https://openreview.net/forum?id=z5uVAKwmjf)
- [Experience-driven self-evolving Agent safety, ACL Findings 2026](https://aclanthology.org/2026.findings-acl.2091/)

## Explicit boundary matrix

| Adjacent standard/system | Reuse | OAC does not duplicate |
|---|---|---|
| A2A | Agent discovery, task/message/artifact transport | organizational Source authority, plan admissibility, Outcome or promotion |
| MCP | context, resource and tool exposure | organization graph truth, business-outcome certification or evolution governance |
| OASF | Agent/skill/domain metadata and discovery taxonomy | case-specific Demand/Plan roots or successor admission |
| Open Agent Specification | portable Agent/Flow implementation description | acceptable-plan-set verification or organizational lineage |
| CloudEvents | interoperable event envelope | semantic authority or evidence validity |
| OpenTelemetry | spans, logs, metrics and correlation | ExecutionReceipt truth, business Outcome or governance decision |
| Temporal | durable workflow execution, retry and recovery | OAC root semantics or independent assurance |
| TOSCA | deployment topology, nodes, relations and operations | enterprise organizational semantics or learned-Source admission |
| Kubernetes Filter→Score | hard feasibility before preference ranking | one canonical Plan; scoring never upgrades an invalid Plan or grants execution |

The design rule is consequently: A2A/MCP/CloudEvents may carry OAC resources, OASF/Open Agent Spec may
describe their producers or lowering targets, OpenTelemetry may locate execution evidence, and Temporal may
host a durable runtime. None of those layers may issue an OAC admission or Outcome verdict merely from
transport, telemetry, runtime completion, or rank.

## Public benchmark version gate

Any later public ground-truth portability experiment MUST use
[`sierra-research/tau2-bench`](https://github.com/sierra-research/tau2-bench) as τ³-bench, pinned to
`v1.0.1` or a later explicitly reviewed tag and exact commit. The older
[`sierra-research/tau-bench`](https://github.com/sierra-research/tau-bench) repository explicitly says its
airline/retail tasks are outdated; it MUST NOT be used as current ground truth. Benchmark evidence remains a
product-profile experiment, not proof of OAC-wide enterprise generality.
