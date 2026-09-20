# OAC Research and Standards Boundary — 2026 Snapshot

This review uses primary specifications, official repositories, and peer-reviewed papers to prevent
OAC from claiming adjacent work as novelty.

## Upstream organization semantics

[OMG BACM 1.0](https://www.omg.org/spec/BACM/1.0/About-BACM) already defines enterprise Capability,
Outcome, Role, Performer, Organization Unit, Process, and Business Object semantics. OAC should offer a
small alignment/import Profile, not another complete enterprise ontology. BACM 1.0 is the stable target;
[BACM 1.1 beta](https://www.omg.org/spec/BACM/1.1/Beta1/About-BACM) remains informative for now.

## Downstream execution descriptions

[Oracle Agent Spec 26.1.2](https://oracle.github.io/agent-spec/26.1.2/agentspec/language_spec_26_1_2.html)
already provides portable Agent, Flow, RemoteAgent, Swarm, ManagerWorkers, and adapter abstractions.
[Open Workflow Specification](https://github.com/open-workflow-specification/specification) already
defines durable control flow, calls, events, retries, timeouts, A2A/MCP integrations, and a CTK. OAC
therefore owns organizational reasons and obligations, not an Agent topology or workflow DSL.

## Dynamic multi-agent research

GPTSwarm, ADAS, AFlow, AgentSquare, MaAS, AnyMAC, ARG-Designer, and related work already select or
optimize roles, modules, communication edges, or workflows. “The system dynamically grows an Agent
team” is prior art. OAC's testable hypothesis is that enterprise-admitted facts constrain this search
and make its completeness independently checkable.

## Constraint and conformance precedents

[W3C SHACL](https://www.w3.org/TR/shacl/) validates data graphs against shape graphs and emits reports.
[VAL](https://github.com/KCL-Planning/VAL) validates candidate plans against a domain/problem.
[A2A TCK](https://github.com/a2aproject/a2a-tck),
[MCP Conformance](https://github.com/modelcontextprotocol/conformance), and the Open Workflow CTK show
capability-scoped, versioned, machine-readable conformance practice. OAC reuses these design lessons;
constraint validation or TCKs alone are not its novelty.

## Enterprise data and benchmarks

- [EDiTh / Véracier Industries](https://huggingface.co/datasets/lightonai/veracier-industries) provides
  1,004 synthetic enterprise PDFs, 36 use cases, retrieval/classification labels, and a supplier
  bankruptcy case. `PROC-01` has document classifications but an empty answer-summary object. It has no
  event-to-organization Ground Truth.
- [EnterpriseOps-Gym](https://github.com/ServiceNow/EnterpriseOps-Gym) provides stateful enterprise
  tasks, 8 domains, 512 tools, 164 tables, and SQL final-state verifiers. Its current card headline and
  enumerated domain total differ (1,150 versus 1,115), so OAC records the exact dataset revision rather
  than repeating one unstable count. It has no organization-compilation truth.
- [EnterpriseBench](https://aclanthology.org/2025.emnlp-main.466/) provides 500 cross-domain enterprise
  tasks but not impacted-role/obligation/partial-order truth.
- [EnterpriseLab/EnterpriseArena](https://ast-fri.github.io/EnterpriseLab/) provides a 2026 multi-app,
  MCP-grounded enterprise environment and trajectory generation; it is execution research, not an
  organization contract.

No public dataset found contains the complete chain:

```text
semantic event → impacted obligations → admissible domains/roles
→ authority/evidence/order constraints → plural-valid organization → outcome
```

Therefore OAC must build an independent annotation layer and must not relabel QA answers or SQL outcome
checks as organizational Ground Truth.

## Defensible innovation statement

OAC is a **combination innovation hypothesis**: a thin, portable boundary that compiles admitted
organization/source semantics and one semantic change into an obligation-complete task organization,
then lets a compiler-independent verifier accept any conforming witness while preserving Unknown and
evidence boundaries. The current verifier shares the executable Profile oracle, so semantic
independence remains unproven. Empirical novelty remains unproven until independent annotation, a
second implementation, sandbox outcomes, and cross-enterprise evidence exist; matched exploratory
baselines are engineering evidence only.
