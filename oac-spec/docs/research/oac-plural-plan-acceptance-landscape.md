# OAC Plural-Plan Acceptance Landscape

**Research date**: 2026-08-24

**Question**: Can one public organizational contract accept multiple independently chosen
`RoleInstance`/`WorkUnit` topologies without reducing conformance to comparison with one reference
Plan?

**Scope**: declarative conformance versus exact workflow topology, partial-order semantics, hierarchical
decomposition, multiple valid plans under constraints, conformance assertions, metamorphic testing, and
mutation testing

**Source policy**: standards bodies, official project documentation/repositories, and original or
authoritative peer-reviewed papers. Older sources are retained only where they define the foundational
semantics being reused.

## Executive conclusion

ADR 0007 asks the right falsifiable next question, but its mathematical object must be described
carefully. OAC is not yet defining an equivalence relation between two Plans. It is defining a public,
versioned acceptance predicate over each Plan independently, and then demonstrating that the predicate's
accepted set contains more than one substantively different topology for one frozen Snapshot/Change
pair.

The closest prior foundations are complementary rather than identical:

- CMMN already standardizes run-time planning, discretionary tasks, role authorization, applicability
  rules, and an evolving case plan.
- Declare and DCR Graphs already constrain what may happen without prescribing one imperative flow.
- PDDL/VAL already separate plan generation from plan validation and accept any Plan satisfying public
  semantics.
- Partial-order and HTN planning already represent multiple schedules and multiple task decompositions,
  and study verification of a supplied Plan.
- OASIS test assertions already separate normative source, target, prerequisite, predicate, and
  prescription level.
- Metamorphic and mutation testing already provide techniques for testing systems that lack one exact
  expected output.

The surveyed sources do not establish OAC's exact composition: Snapshot/Change-derived organizational
obligations plus authority, qualification, separation of duties, evidence, Unknown preservation, effect
ceilings, topology-plural fixed-Plan verification, normative reason policies, cross-implementation
observations, and digest-bound disagreement artifacts. Failure to find a direct isomorph is not a
novelty proof. Academic or standards novelty still requires a reproducible literature search, formal
semantics, external implementations, public benchmarks, and comparative evaluation.

## 1. Problem definition: accepted-set membership, not equivalence

Let:

- `R` be one immutable `RequirementSet`;
- `S` be one sealed `OrganizationSnapshot`;
- `C` be one sealed `SemanticChangeSet`; and
- `P` be one candidate `OrganizationPlan`.

The bounded verifier relation should be understood as:

```text
Verify_R(S, C, P) -> DecisionReport

Accept_R(S, C, P) :=
  Verify_R(S, C, P).domainVerdict == ACCEPT

Fiber_R(S, C) :=
  { P | Accept_R(S, C, P) }

Plural_R(S, C) :=
  exists P1, P2 in Fiber_R(S, C):
    SubstantivelyDistinct(P1, P2)
```

`Plural_R(S,C)` says that at least two independently supplied Plans are legal members of the same
acceptance fiber. It does **not** imply:

- byte equality;
- graph isomorphism;
- trace equivalence;
- bisimulation;
- identical cost, latency, or enterprise outcome; or
- interchangeability under every possible future execution environment.

This distinction matters because workflow-equivalence research contains multiple non-coincident notions
of equivalence. Hidders et al. show that an observation boundary—such as offered work sets, external
inputs, and silent steps—changes what it means for workflows to be the same. OAC has not yet defined a
complete execution semantics or observation boundary, so naming its current relation "workflow
equivalence" would overstate the result.

The narrower accepted-set formulation is sufficient for ADR 0007's purpose: prove that the contract
constrains organizational meaning without prescribing one producer topology.

## 2. Primary-source landscape

| Source | Established idea relevant to OAC | Important limit for OAC |
|---|---|---|
| [OMG CMMN 1.1](https://www.omg.org/spec/CMMN/1.1/PDF), December 2016 | Run-time planning is fundamental; a plan may evolve by adding discretionary tasks. Planning tables expose items according to role authorization and applicability rules over a CaseFile. | It is a case-management model and notation centered on case workers. It does not define OAC's knowledge/tool/evidence contract, fixed reason policy, or multi-kernel conformance evidence chain. |
| [Pesic and van der Aalst, *A Declarative Approach for Flexible Business Processes Management*](https://doi.org/10.1007/11837862_18), BPM Workshops 2006 | ConDec/Declare specifies what must hold using temporal constraints instead of prescribing how every step must be executed. One model admits many traces. | It primarily constrains activity traces; it is not an organizational Plan verifier over roles, principals, WorkUnits, evidence, and effects. |
| [Hildebrandt and Mukkamala, *Declarative Event-Based Workflow as Distributed Dynamic Condition Response Graphs*](https://doi.org/10.4204/EPTCS.69.5), 2011 | DCR Graphs use condition, response, include, and exclude relations. The distributed form explicitly assigns roles to events and roles to principals. | Its core semantic object is an event run with acceptance conditions, not OAC's sealed Snapshot/Change/Plan relation. |
| [De Giacomo and Vardi, *Linear Temporal Logic and Linear Dynamic Logic on Finite Traces*](https://repository.rice.edu/items/08ed8c8b-2c03-4e0e-9bea-f06fc9ca5dd3), IJCAI 2013 | LTLf/LDLf provide formal model checking over finite traces and are a possible foundation for bounded order/response constraints. | Two-valued finite-trace logic does not by itself supply OAC's Strong Kleene Unknown, organizational authority, or evidence semantics. |
| [Fox and Long, *PDDL2.1*](https://jair.org/index.php/jair/article/view/10352), JAIR 2003; [KCL VAL](https://github.com/KCL-Planning/VAL) | The public domain/problem semantics are separate from the planner. A validator can accept a hand-written or independently generated Plan and explain violated preconditions or invariants. | PDDL validates action-state semantics, not OAC's organization-specific obligations and governance. |
| [Memon, Pollack, and Soffa, *Using a Goal-Driven Approach to Generate Test Cases for GUIs*](https://www.cs.umd.edu/~atif/papers/MemonICSE1999.pdf), ICSE 1999 | A partial-order Plan compactly represents a family of linearizations; the cited formulation requires every consistent linearization to meet the solution conditions. | Different linearizations of one action set are weaker plurality than OAC's different WorkUnit partitions and role topology. |
| [Muise et al., *Optimization of Partial-Order Plans via MaxSAT*](https://www.cs.toronto.edu/~sheila/publications/mui-etal-coplas11.pdf), 2011 | Separates hard validity from soft optimization, represents causal links, and distinguishes validity, action count, ordering count, and flexibility. | Its reordering/deordering definitions retain the same action set; they do not establish semantic equivalence of grouped and split organizational work. |
| [Hidders et al., *When Are Two Workflows the Same?*](https://www.vdaalst.com/publications/p267.pdf), CATS 2005 | Shows workflow variability and defines observation-dependent equivalence notions; several notions differ in the presence of silent steps. | It focuses on control-flow observation and treats organizational/operational perspectives as ancillary. OAC must not import one equivalence label without choosing its observation boundary. |
| [HDDL input language, IPC 2020](https://ipc2020.hierarchical-task.net/benchmarks/input-language); [Höller et al., *Compiling HTN Plan Verification Problems into HTN Planning Problems*](https://ojs.aaai.org/index.php/ICAPS/article/view/19795), ICAPS 2022 | A high-level task may have multiple formal decomposition methods; verification asks whether a supplied action sequence/task network is a valid solution. | HTN validity normally depends on a predefined decomposition library. OAC aims to accept producer-selected decompositions from public organizational constraints without fixing an exhaustive method library. |
| [OASIS Test Assertions Guidelines 1.0](https://docs.oasis-open.org/tag/guidelines/v1.0/testassertionsguidelines.html), 19 June 2013 | A test assertion binds an identifier, normative source, target, predicate, prerequisite, and prescription level. Assertions support requirement-to-test traceability and coverage analysis. | It does not define OAC's domain report, reason-code registry, witness bindings, or topology-plural oracle. |
| [Open Policy Agent policy language](https://www.openpolicyagent.org/docs/policy-language) | Declarative policy evaluates structured input independently of application control flow. Official documentation also makes `undefined` behavior visible when no default applies. | A general policy engine is an implementation option, not the OAC organizational standard. Implicit `undefined` cannot replace OAC's explicit domain Unknown semantics. |
| [Liu et al., *How Effectively Does Metamorphic Testing Alleviate the Oracle Problem?*](https://nottingham-repository.worktribe.com/output/720879/how-effectively-does-metamorphic-testing-alleviate-the-oracle-problem), IEEE TSE 2014 | Metamorphic relations test necessary relationships between multiple executions when an exact output oracle is absent or too expensive. | It is a testing method; the necessary relations must still come from the OAC normative contract. |
| [Jia and Harman, *An Analysis and Survey of the Development of Mutation Testing*](https://crest.cs.ucl.ac.uk/fileadmin/crest/sebasepaper/JiaH10.pdf), IEEE TSE 2011; [Belli et al., model-based mutation testing](https://doi.org/10.1016/j.scico.2016.01.003), 2016 | Requirement/model faults can be seeded deliberately to assess whether a suite detects omission, insertion, or altered behavior. Equivalent mutants are a known validity problem. | A total mutation score cannot establish complete semantics. OAC must bind mutants to normative fault classes and exclude or quarantine equivalent mutants. |

### 2.1 Closest conceptual predecessor: CMMN

CMMN is more relevant to OAC's dynamic-organization objective than BPMN alone. Its normative text says
that run-time planning is fundamental to case management, permits task selection and ordering during a
case, supports ad-hoc collaboration, and allows the plan to evolve by adding discretionary tasks.
`PlanningTable`, `authorizedRoleRefs`, and `ApplicabilityRule` establish that a standard can expose a
bounded space of runtime choices rather than one predefined sequence.

OAC should therefore avoid presenting dynamic runtime planning as unprecedented. Its differentiation
must be narrower: deriving organization-specific obligations from sealed knowledge/change resources,
verifying arbitrary organization Plans against authority and evidence boundaries, and publishing a
portable conformance/evidence protocol.

### 2.2 Closest verification architecture: PDDL plus VAL

PDDL/VAL demonstrates the architectural separation OAC needs. The planner chooses a candidate; the
validator independently evaluates that candidate against a public domain and problem. The candidate is
not compared with the planner author's preferred Plan.

The transferable pattern is:

```text
Agent/compiler -> arbitrary candidate OrganizationPlan
                         |
                         v
public verifier(Snapshot, Change, Plan, RequirementSet)
                         |
                         v
domain verdict + normative reasons + witnesses
```

This does not make VAL reusable as OAC semantics, but it validates the generator/verifier boundary as a
mature design pattern.

### 2.3 Why partial-order equivalence is insufficient

A DAG may differ in three materially different ways:

1. only by identifiers or serialization order;
2. only by redundant transitive edges while denoting the same partial order; or
3. by its actual division of obligations, role bindings, and allowed orderings.

Only the third kind demonstrates ADR 0007's intended plurality. Classical partial-order planning often
holds the action set fixed and varies order constraints or linearizations. OAC's grouped-versus-split
WorkUnits change the partition of obligation-bearing work itself. That is closer to hierarchical
refinement, but OAC currently lacks an HTN-style exhaustive decomposition library. The bounded MVP
should therefore define a specific semantic projection and structural witness rather than claiming
general partial-order or workflow equivalence.

## 3. Eight implementable design constraints

### Constraint 1 — Specify membership, never a golden topology

Each scored input MUST have one expected `domainVerdict`. Plurality MUST be represented by separate
candidate Plans that independently receive `ACCEPT`, not by an array of acceptable verdicts and not by
similarity to a reference Plan.

The SUT request MUST contain only the sealed Snapshot, Change, candidate Plan, and exact bounded
capability coordinate. It MUST NOT receive case IDs, expectations, reference reports, mutation names,
or a trusted compiler certificate that bypasses verification.

### Constraint 2 — Publish a substantive-topology predicate

The lab needs a machine-readable, runner-owned `PluralityWitness` proving that its positive pair is not
cosmetic. This witness is test metadata and MUST NOT be sent to the SUT.

A bounded definition can normalize each Plan by:

- alpha-renaming compiler-owned identifiers;
- sorting set-like collections;
- reducing raw order edges to their obligation-level reachability relation;
- recording the partition of obligation refs induced by WorkUnits;
- recording role/principal bindings, evidence duties, and effect ceilings; and
- ignoring redundant transitive edges.

For the first capability, the positive pair SHOULD differ in obligation partition—one Plan groups a
compatible pair while the other splits it. Renaming IDs, reordering a set-like array, or adding an edge
already implied by transitivity MUST NOT satisfy the distinctness gate.

### Constraint 3 — Verify every Plan independently from public semantics

The verifier SHOULD derive a topology-neutral semantic projection before deciding:

```text
PlanSemanticProjection = {
  coveredObligations,
  obligationPartition,
  authorityBindings,
  qualificationBindings,
  separationRelations,
  obligationOrderReachability,
  requiredEvidence,
  unknownPreservation,
  effectCeilings
}
```

Each Plan independently satisfies or violates the RequirementSet. Positive Plans are not required to
have identical projection bytes because their obligation partitions intentionally differ. They are
required to preserve the same frozen obligations and independently satisfy the same normative
authority, evidence, order, Unknown, effect, and minimality predicates.

The public definition of "compatible work" MUST be explicit. In the current model, obligations may be
grouped only when their authority/effect boundaries are compatible, no separation rule forbids the
combination, and no mandatory relative order would be hidden inside a WorkUnit that lacks internal
ordering semantics.

### Constraint 4 — Freeze the quantifier over allowed schedules

If absence of a `happensBefore` edge grants the runtime freedom to choose either order or concurrency,
validity cannot be established by simulating one arbitrary topological sort.

For the bounded capability:

- authority, separation, evidence prerequisites, Unknown preservation, and effect safety SHOULD hold
  for every linearization permitted by the Plan;
- order validation SHOULD reason over transitive reachability or causal threats instead of enumerating
  all factorially many schedules; and
- a Plan that permits one unsafe schedule SHOULD be rejected even if one safe schedule exists.

If a future Profile chooses existential completion semantics for a specific property, that quantifier
must be separately named and versioned. It cannot be inferred from implementation behavior.

### Constraint 5 — Separate hard conformance from quality and optimization

PDDL2.1 separates logical Plan validity from an optional plan metric, and explicitly notes that action
count is a poor universal quality measure. OAC needs the same separation.

The existing contract-relative `inclusion_minimal` meaning is appropriate: a selected role binding is
non-removable when removing it makes mandatory obligation coverage, qualification, separation,
ordering, evidence, or Unknown constraints fail. It is not proof of global optimality.

Consequently:

- the verifier MUST NOT reject a fine-grained positive merely because it has more WorkUnits than a
  grouped positive;
- the verifier MUST NOT compare node count with a reference Plan;
- a `non-minimal` mutant SHOULD add an empty WorkUnit, duplicate complete coverage, add a removable
  role binding, or add work unsupported by any obligation/evidence/effect requirement; and
- cost, latency, token use, labor, resilience, and enterprise value belong to future soft
  `PlanQuality` observations, not this capability's `ACCEPT` predicate.

This resolves the apparent tension in ADR 0007 between accepting a more finely split topology and
rejecting redundant work.

### Constraint 6 — Make reasons and Unknown part of the public decision contract

Following the OASIS assertion anatomy, every normative verifier check SHOULD bind:

```text
requirementId
normativeSource
target
prerequisites
predicate
prescriptionLevel
```

The `DecisionReport` SHOULD distinguish:

- evaluated requirement IDs;
- violated and Unknown requirement IDs;
- required core reason codes;
- forbidden reason codes;
- namespaced implementation-extension reasons; and
- evidence/witness refs.

The case oracle SHOULD compare the one exact domain verdict plus required/forbidden normative reason
policies. It SHOULD NOT require two different positive Plans to emit one byte-identical report.

`UNKNOWN` remains an OAC business-semantics verdict or value. It MUST NOT encode timeout, crash,
malformed runner input, resource exhaustion, or missing harness metadata. Those remain separate
protocol/harness outcomes. Missing values MUST NOT silently inherit a host language's `undefined`,
`null`, false, or empty-container behavior.

### Constraint 7 — Use metamorphic relations for topology plurality

The absence of one golden Plan is an oracle problem suited to metamorphic testing. Freeze relations
before executing either semantic kernel:

- alpha-renaming of opaque IDs preserves the verdict and normative reason core;
- reordering a set-like collection preserves the verdict;
- splitting compatible work while lifting obligation coverage and order preserves `ACCEPT`;
- merging compatible work preserves `ACCEPT`;
- omitting an obligation changes the verdict to `REJECT` with the omission reason;
- erasing Unknown changes the verdict according to the frozen Unknown rule; and
- reversing a required order changes the verdict to `REJECT` with the order reason.

Metamorphic equality applies to declared observable fields, not to complete report bytes: plan digests,
opaque refs, and structural witnesses legitimately change between source and follow-up cases.

### Constraint 8 — Score a requirement-linked mutation lattice, not a raw mutant count

Every negative-control class SHOULD identify:

- the normative requirement it violates;
- the exact transformation from the admitted positive source;
- the expected verdict transition;
- required and forbidden reasons; and
- evidence that the mutant is not semantically equivalent under the bounded relation.

The initial lattice should cover obligation omission, path/evaluation forgery, Unknown erasure,
unauthorized principal/role binding, qualification failure, separation failure, missing evidence,
missing/reversed order, effect-ceiling breach, and contract-relative redundancy.

Results SHOULD be reported as a per-class kill vector. A high aggregate mutation score cannot conceal a
missed authority or Unknown class. Equivalent or duplicate mutants MUST be removed or explicitly
quarantined from the denominator. An accept-all mutant and a reason-erasure mutant are mandatory
meta-controls because they test the harness/oracle, not just business code.

All rules, positive sources, transformations, generated inputs, implementation closures, complete
observations, and disagreements SHOULD remain digest-bound. Cross-implementation agreement is bounded
evidence; it is not proof that both implementations are correct.

## 4. What OAC may and may not claim

### 4.1 Claim supported by a successful bounded increment

If ADR 0007's exit gates pass, a defensible statement is:

> For the named Supplier capability, frozen RequirementSet, public input pair, positive Plan set, and
> mutation lattice, the tested Python and internal Go semantic kernels independently returned the one
> required verdict and compliant normative reasons while accepting at least two substantively different
> WorkUnit partitions. The result is bounded differential evidence that the published acceptance
> relation does not prescribe one tested producer topology.

The statement must name implementation, source/artifact, bundle, and RequirementSet identities and
retain the existing exclusions for clean-room independence, complete Profile coverage, enterprise
correctness, runtime effects, and clean-archive reproducibility.

### 4.2 Claims not supported

The result does not justify saying that OAC is:

- the first standard for dynamic enterprise workflows;
- the first system to permit multiple valid Plans;
- the first declarative organization or agent orchestration standard;
- behaviorally equivalent across accepted Plans;
- complete for Supplier or for all OAC Profiles;
- proven correct because two implementations agree;
- externally independent while both kernels share the same project governance or prior artifacts;
- validated for enterprise outcomes or production effects; or
- universally applicable to every enterprise.

CMMN alone prevents a credible "first dynamic runtime planning standard" claim. Declare/DCR prevent a
credible "first constraint-based flexible workflow" claim. PDDL/VAL and HTN verification prevent a
credible "first generator-independent Plan validator" claim. Metamorphic and mutation testing prevent
presenting the test strategy itself as new.

### 4.3 Narrow differentiation hypothesis

The research-supported hypothesis worth testing is narrower:

> OAC composes organization-specific knowledge/change roots, public obligation derivation, authority,
> evidence, Unknown, effect, and partial-order constraints into a portable Plan acceptance contract that
> deliberately permits producer-selected Agent/WorkUnit topology and makes fixed-input verdict/reason
> agreement independently testable and digest-auditable.

This is a plausible design differentiation, not yet a research novelty claim.

## 5. Novelty status and evidence still required

No directly isomorphic public standard or original paper was found in this bounded search. In
particular, none of the surveyed sources combined all of the following in one conformance object:

1. sealed organization knowledge and change roots;
2. derived cross-domain obligations;
3. role/principal/tool/qualification and separation boundaries;
4. evidence and Unknown-preservation duties;
5. effect ceilings;
6. multiple accepted organization topologies for one fixed input;
7. one normative verdict with required/forbidden reasons;
8. multiple black-box semantic kernels and durable disagreement records; and
9. digest-bound replay materials.

This negative search result has limited force. Terminology differs across business process management,
case management, automated planning, policy engines, multi-agent systems, conformance testing, and
formal methods. Relevant proprietary systems may also be undisclosed. "No direct isomorph found" must
therefore remain research-log wording, never proof of novelty.

Before a stronger claim, OAC needs:

- a published formal definition of the accepted-set relation and its observation boundary;
- external review of the normative requirements and reason policies;
- at least one separately maintained implementation developed from public prose/data rather than
  reference source;
- a public benchmark with positive plurality and requirement-linked negative classes;
- comparison against at least CMMN, DCR/Declare, PDDL/VAL, and HTN verification baselines;
- ablation showing what authority, evidence, Unknown, and effect constraints add beyond a generic
  graph/policy validator; and
- an enterprise outcome experiment kept separate from structural Plan acceptance.

## 6. Recommended ADR 0007 wording changes before implementation

1. Use **plural accepted Plans** or **topology-plural acceptance**, not workflow equivalence.
2. Add the `Fiber_R(S,C)` membership definition to the normative design note.
3. Define `SubstantivelyDistinct` through obligation partition and obligation-level reachability after
   alpha-renaming and transitive normalization.
4. State the universal/existential quantifier for every order and safety property.
5. Clarify that `inclusion_minimal` is contract-relative role-binding irreducibility, never minimum
   WorkUnit count or global optimality.
6. Keep the `PluralityWitness` in runner-owned expectation metadata and out of the SUT request.
7. Compare exact verdict plus required/forbidden reasons across implementations for the same Plan;
   compare only declared metamorphic fields across different Plans.
8. Report mutation coverage by normative fault class and quarantine equivalent mutants.

These changes preserve ADR 0007's intent while preventing three foreseeable overclaims: mistaking set
membership for equivalence, mistaking a finer topology for non-minimality, and mistaking one safe
schedule for safety of the partial-order Plan.

## References

### Standards and official project material

- Object Management Group. [Case Management Model and Notation 1.1](https://www.omg.org/spec/CMMN/1.1/PDF).
  December 2016.
- Object Management Group. [Business Process Model and Notation 2.0.2](https://www.omg.org/spec/BPMN/2.0.2/PDF).
  December 2013 / formal publication January 2014.
- OASIS Test Assertions Guidelines TC. [Test Assertions Guidelines Version 1.0, Committee Note
  02](https://docs.oasis-open.org/tag/guidelines/v1.0/testassertionsguidelines.html). 19 June 2013.
- International Planning Competition. [IPC 2020 HDDL Input
  Language](https://ipc2020.hierarchical-task.net/benchmarks/input-language). 2020.
- KCL Planning. [VAL: the Plan Validation System](https://github.com/KCL-Planning/VAL). Official
  repository, accessed 24 August 2026.
- Open Policy Agent. [Policy Language](https://www.openpolicyagent.org/docs/policy-language). Official
  documentation, accessed 24 August 2026.

### Original and authoritative research

- Maja Pesic and Wil M. P. van der Aalst. [A Declarative Approach for Flexible Business Processes
  Management](https://doi.org/10.1007/11837862_18). BPM Workshops 2006, LNCS 4103, pp. 169–180.
- Thomas T. Hildebrandt and Raghava Rao Mukkamala. [Declarative Event-Based Workflow as Distributed
  Dynamic Condition Response Graphs](https://doi.org/10.4204/EPTCS.69.5). EPTCS 69, 2011, pp. 59–73.
- Giuseppe De Giacomo and Moshe Y. Vardi. [Linear Temporal Logic and Linear Dynamic Logic on Finite
  Traces](https://repository.rice.edu/items/08ed8c8b-2c03-4e0e-9bea-f06fc9ca5dd3). IJCAI 2013,
  pp. 854–860.
- Maria Fox and Derek Long. [PDDL2.1: An Extension to PDDL for Expressing Temporal Planning
  Domains](https://jair.org/index.php/jair/article/view/10352). JAIR 20, 2003, pp. 61–124.
- Atif M. Memon, Martha E. Pollack, and Mary Lou Soffa. [Using a Goal-Driven Approach to Generate Test
  Cases for GUIs](https://www.cs.umd.edu/~atif/papers/MemonICSE1999.pdf). ICSE 1999.
- Christian Muise, J. Christopher Beck, and Sheila A. McIlraith. [Optimization of Partial-Order Plans
  via MaxSAT](https://www.cs.toronto.edu/~sheila/publications/mui-etal-coplas11.pdf). COPLAS 2011.
- Jan Hidders, Marlon Dumas, Wil M. P. van der Aalst, Arthur H. M. ter Hofstede, and Jan Verelst.
  [When Are Two Workflows the Same?](https://www.vdaalst.com/publications/p267.pdf). CATS 2005.
- Daniel Höller, Julia Wichlacz, Pascal Bercher, and Gregor Behnke. [Compiling HTN Plan Verification
  Problems into HTN Planning Problems](https://ojs.aaai.org/index.php/ICAPS/article/view/19795).
  ICAPS 2022, pp. 145–150.
- Huai Liu, Fei-Ching Kuo, Dave Towey, and Tsong Yueh Chen. [How Effectively Does Metamorphic Testing
  Alleviate the Oracle Problem?](https://nottingham-repository.worktribe.com/output/720879/how-effectively-does-metamorphic-testing-alleviate-the-oracle-problem).
  IEEE Transactions on Software Engineering 40(1), 2014, pp. 4–22.
- Yue Jia and Mark Harman. [An Analysis and Survey of the Development of Mutation
  Testing](https://crest.cs.ucl.ac.uk/fileadmin/crest/sebasepaper/JiaH10.pdf). IEEE Transactions on
  Software Engineering 37(5), 2011, pp. 649–678.
- Fevzi Belli, Christof J. Budnik, Axel Hollmann, Tugkan Tuglular, and W. Eric Wong. [Model-Based
  Mutation Testing—Approach and Case Studies](https://doi.org/10.1016/j.scico.2016.01.003). Science of
  Computer Programming 120, 2016, pp. 25–48.
