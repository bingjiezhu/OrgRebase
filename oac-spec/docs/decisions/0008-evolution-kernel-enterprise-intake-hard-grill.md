# ADR 0008: Evolution Kernel and Enterprise Intake Hard Grill

**Status**: accepted as design and next-step authorization; not implemented  
**Date**: 2026-08-25  
**Pressure**: Hard  
**Decision authority**: user-authorized self-questioning; every answer below is the recommended answer  
**Depends on**: ADR 0007 and Specs 004–007

## Target

The project goal is not a fixed Agent team or another general Agent runtime. It is a portable
organizational contract and control layer through which an enterprise can provide governed material,
humans and AI can form task-specific organizations, and repeated evidence can propose better company
capabilities without each enterprise rebuilding orchestration semantics from scratch.

This ADR stress-tests what must be standardized next and what must remain outside the claim.

## Question 1: What is the product-level invariant?

**Options**: A. one optimal Agent graph; B. one vendor runtime; C. a proof-carrying organizational
compilation and evolution contract; D. a knowledge-base chatbot; E. an autonomous self-modifying Agent.

**Recommended answer**: C.

Team topology, models, tools, and runtime may vary. Admitted organization facts, bounded demand,
authority, obligations, evidence, Unknown, independent assurance, and exact lineage are portable
invariants.

## Question 2: Does “the enterprise only provides data” mean arbitrary documents are enough?

**Recommended answer**: no. The enterprise provides a governed Intake package: source identity and
provenance, namespace, owner/governance, roles/principals, domains, typed dependencies, completeness,
knowledge/evidence locators, capabilities, demand, and changes. Missing elements stay Unknown. Raw RAG
content cannot establish authority or business dependency by similarity.

## Question 3: Should Enterprise Intake be a new OAC top-level Kind?

**Recommended answer**: no. It is a transport and admission protocol. Making every connector package a
Core Kind would bind OAC to storage/transport formats and invite open extension payloads. OAC registers
the admitted semantic resources and receipts, not the delivery container.

## Question 4: Which top-level semantic gaps must be filled first?

**Options**: A. add Agent, Team, Memory, Tool, Trace, Evolution, and Intake Kinds; B. add only
`OrganizationalDemand` and `SourceAdmissionReceipt`; C. add no new Kind and keep free IDs; D. embed
demand and admission into prompts; E. copy a workflow schema.

**Recommended answer**: B.

`SemanticChangeSet` already carries `demandRef` but there is no exact accountable Demand resource.
Core explicitly treats source admission as upstream, but there is no evidence resource binding that
decision. These two gaps block real intake. Other layers already have owners or need later evidence.

## Question 5: Can an AI-created fact, rule, dependency, role, or Skill become admitted automatically?

**Recommended answer**: no. AI may propose only `CANDIDATE` Source/evolution artifacts. Another AI is not
automatically an independent authority. Admission requires a distinct external governance authority,
qualification/separation rules, and an exact digest-bound decision.

## Question 6: Can Plan acceptance be treated as Source admission?

**Recommended answer**: no. A Plan is a replaceable witness under frozen Source constraints. A
`PlanCertificate` can say the Plan belongs to the accepted set; it cannot make plan choices into
enterprise truth or grant runtime credentials.

## Question 7: What is the smallest complete evolution model?

**Recommended answer**: five immutable roots:

1. admitted Source root;
2. accountable Demand root;
3. verified Plan root;
4. independently assured Outcome root; and
5. governed evolution-candidate/decision root.

Runtime binding, execution receipts, and observations form a separate evidence state, not a sixth root.
Only an externally admitted evolution decision can create a successor Source/Profile revision. No
successful trace or model score skips directly from Plan/Outcome to Source.

## Question 8: Is one lifecycle status enough?

**Recommended answer**: no. Freeze eight orthogonal axes: Source authority, observability,
applicability/impact, Plan assurance, runtime admission, execution, outcome assurance, and evolution
governance. A single `SUCCESS` would erase the project's central authority/evidence boundaries.

## Question 9: If execution succeeds, did the system work?

**Recommended answer**: not necessarily. Execution success only says the runtime completed under its
own protocol. Independent outcome evidence must test intended effects, forbidden effects, evidence
completion, scope, and replayability. A self-reported Agent answer is not an outcome oracle.

## Question 10: If an independent outcome passes, should the Skill/Profile be promoted?

**Recommended answer**: no. One outcome may be lucky, confounded, or non-transferable. Promotion needs
support and counterexamples, transfer bounds, held-out/counterfactual replay, effect/authority ceiling,
rollback, and an external governance decision. Until then it is Candidate.

## Question 11: Should governed learning be implemented before intake?

**Recommended answer**: no. Learning from hand-authored roots would optimize a fixture rather than prove
enterprise portability. The next implementation is the Demand/source-admission boundary and its
negative controls. Outcome and evolution remain later evidence gates.

## Question 12: What does the current code actually prove?

**Recommended answer**: the bounded Supplier Profile can derive and verify plural-valid zero-effect
plans; the G1a lowerer can deterministically project two accepted topologies into distinct zero-write
bundles while retaining a separate runtime-admission boundary. It does not prove Enterprise Intake,
Agent execution, OutcomeCertificate, governed promotion, a complete five-root loop, or production
enterprise applicability.

## Question 13: Should the Supplier fixture be rewritten as a generic enterprise system now?

**Recommended answer**: no. Preserve it as a bounded reference Profile. E0a should wrap one case through
the new admitted Demand/source boundary, then add a separate Profile/enterprise fixture later. Hidden
conditionals keyed to Supplier case IDs would fail the portability thesis.

## Question 14: Does OAC replace MCP, A2A, OASF, IAM, telemetry, or a runtime?

**Recommended answer**: no. Those systems own tool/context exchange, Agent communication/discovery,
identity/policy, observability, and execution. OAC owns the organizational meaning and proof boundary
above/between them: admitted enterprise roots, dynamic plan acceptability, and governed evolution.

## Question 15: What is the next implementation slice?

**Recommended answer**: E0a only:

1. add strict `OrganizationalDemand` and `SourceAdmissionReceipt` resources;
2. implement deterministic Enterprise Intake admission from a closed manifest;
3. enforce Candidate-only AI and distinct decision authority;
4. bind Snapshot/Demand/Change exact roots;
5. freeze positive, negative, Unknown, and mutant controls; and
6. reproduce one bounded Supplier flow without widening its claim.

No runtime execution, OutcomeCertificate, skill generation, or auto-promotion enters E0a.

## Question 16: What is the E0a exit condition?

**Recommended answer**: schemas/registries/rules are drift-free; same inputs repeat byte-for-byte;
malformed, unresolved, self-approved, stale, cross-root, and state-collapse cases fail for stable public
reasons; required mutants are killed; source and isolated-install observations agree; full repository
and clean-archive gates pass; and the validation report still calls scripted admission controlled-local,
not human or enterprise validation.

## Decision

```text
freeze five roots + eight independent state axes
                  ↓
keep Enterprise Intake as external envelope
                  ↓
add only Demand + Source admission evidence
                  ↓
enforce AI → Candidate, never self-admitted authority
                  ↓
bind Snapshot/Demand/Change exact roots and negative lattice
                  ↓
only after outcome evidence: governed evolution experiment
```

Spec 008 authorizes E0a and no more. Documentation completion is not implementation evidence.

## Consequences

- The project can explain the enterprise “data in” boundary without pretending data is truth.
- Dynamic Agent topology remains a compiler choice constrained by portable organization contracts.
- Human–AI co-evolution becomes an evidence and governance loop, not silent self-modification.
- Product views must expose distinct lifecycle axes instead of a single completion badge.
- Current Supplier and G1a evidence stays valid within its existing bounded coordinate.
- Enterprise portability, actual outcomes, and self-improving capabilities remain open, falsifiable
  gates.
