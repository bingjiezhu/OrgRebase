# ADR 0005: A1 Supplier v0.2 Portability Capsule Hard Grill

**Status**: accepted for implementation
**Date**: 2026-08-23
**Pressure**: Hard
**Scope**: choose the next evidence-increasing OAC MVP slice after Spec 003 A0

**Historical evidence note (2026-08-24)**: ADR 0005 remains the record of why seed-1 was built and of
the defect it exposed. ADR 0006 supersedes seed-1 as current claim evidence after a wider semantic
branch sweep found disagreements outside its selected set and identified capability/generator/install
binding debt. The historical 34 selected observations are not a full-profile or final installed result.

## Target recalled from prior alignment

OAC is not another Agent runtime. Its falsifiable claim is that frozen enterprise facts and a semantic
change can determine a topology-independent organizational obligation contract; replaceable compilers
may then grow different Agent organizations inside that contract, while independent verifiers judge
authorization, evidence, order, completeness, and Unknown preservation.

The next slice must therefore test whether the public contract is sufficient for another semantic
implementation. It must not optimize for UI breadth, connector count, or a more impressive single
reference demo.

## Question 1: Which unfinished Spec most directly tests the OAC thesis?

**Options**: A. Spec 004 human Gold; B. Spec 005 sandbox outcome; C. Spec 003 A1 derivation then
fixed-plan parity; D. Spec 006 Skill learning; E. another runtime/compiler demo.

**Recommended answer**: C.

**Why it matters**: 004 lacks qualified reviewers, 005 would execute semantics not yet independently
reproduced, 006 would learn from unverified outcomes, and E would only strengthen one implementation.

**Error cost**: B, D, or E can produce a compelling demo while leaving the standard implementable only
by its author.

## Question 2: What observable result is strong enough?

**Options**: A. equal final ACCEPT; B. byte-identical topology-free reports; C. identical Agent graph;
D. one reference benchmark win; E. one public sandbox success.

**Recommended answer**: B now, followed by plural fixed-plan verification. Final verdict equality can
hide different closures; graph equality would contradict plural-valid organization semantics.

**Error cost**: a golden graph turns OAC into a workflow generator, while a scalar verdict can conceal
opposite reasons and obligations.

## Question 3: Is the current public resource contract implementable without copying Python behavior?

**Recommended answer**: No. Freeze `SealedResource/v1` first.

The Core says a resource digest is JCS over the raw decoded object with only top-level `digest`
removed. The reference currently parses into Pydantic before hashing; that can trim Identifier values,
insert defaults, or normalize timestamps. Generated schemas also allow omission of fields that the A1
wire needs to be explicit. A conforming second implementation could therefore disagree before Supplier
semantics run.

`SealedResource/v1` requires:

1. raw bytes survive duplicate-key and finite I-JSON admission;
2. `apiVersion`, `kind`, and `digest` are explicit;
3. digest is computed over the raw decoded map minus `digest`;
4. typed parsing validates but MUST NOT trim, remove, or rewrite explicit semantic values; published
   schema omission defaults may guide semantics but never change raw identity;
5. `NonBlankString/v1` is a predicate, not a normalizer;
6. timestamps use one frozen whole-second UTC lexical form;
7. a typed round-trip unequal to the admitted raw map is `CANONICAL_ADMISSION_MISMATCH`.

**Error cost**: without this, every Python/Go mismatch is untriageable between parsing,
canonicalization, Schema, and Supplier semantics.

## Question 4: Should A1 mutate the frozen Phase A stdio-v1 bundle?

**Recommended answer**: No. Keep its digest and 24 cases immutable. Add experimental
`oac.ctk.stdio/v2` raw-resource operations:

- `validateResource(expectedKind, rawBase64)`;
- `derive(snapshotBase64, changeBase64)`.

`verify(snapshotBase64, changeBase64, planBase64)` follows after derivation parity. The runner
independently recomputes every returned report digest.

**Error cost**: rewriting v1 destroys the evidence coordinate already published for Phase A.

## Question 5: Which data forms the first derivation set?

**Options**: A. all ten cases; B. SC-008 only; C. SC-008/009/010; D. a toy graph; E. EnterpriseOps-Gym.

**Recommended answer**: C, then promote all ten in a successor. These cases cover known contextual
propagation, derived/candidate Unknown, and root Unknown under truncated coverage. Their report digests
are development oracles, not human Ground Truth.

**Error cost**: B is easy to hard-code and misses Unknown; A mixes legacy compatibility into the first
parser cut; D proves a toy; E has outcome oracles but no OAC organization labels.

## Question 6: How do we prevent three golden reports from becoming canned output?

**Recommended answer**: combine frozen cases with public, digest-bound generated/metamorphic inputs:
reorder set-owned fields, remove scope, change admission, change missing behavior, add irrelevant
Sources, cross resource limits, and reject a one-report mutant. The SUT receives no case ID,
expectation, path, or expected digest. This does not establish a genuinely held-out/hidden suite.

**Error cost**: fixed vectors prove protocol compatibility but not computation of the relation.

## Question 7: Does internally authored Go parity prove independence?

**Recommended answer**: No. It is cross-language, no-runtime-semantic-sharing differential evidence.
It cannot self-award clean-room, organizational independence, complete conformance, enterprise
correctness, or standard consensus.

**Error cost**: inflated independence claims erase the M2 gate that makes OAC credible.

## Question 8: What falsifies this slice?

**Recommended answer**: revise or stop if the second kernel needs reference source; parity needs fixture
IDs or expected digests; generated variants diverge; multiple obligation-equivalent Plans cannot later
pass; or a graph-only baseline reproduces the same guarantees with less machinery.

## Research boundary

Dynamic Agent graph construction is already covered by
[MaAS](https://proceedings.mlr.press/v267/zhang25bi.html),
[G-Designer](https://proceedings.mlr.press/v267/zhang25cu.html), and
[ReSo](https://aclanthology.org/2025.emnlp-main.808/). Contract verification of multi-Agent systems
also exists in [AAAI 2025](https://ojs.aaai.org/index.php/AAAI/article/view/34480). OAC must not claim
novelty for either primitive.

The closest engineering precedent is the
[Cedar specification](https://github.com/cedar-policy/cedar-spec): executable semantics,
cross-implementation differential testing, and generated properties. The closest evaluation idea is
[ETOM](https://aclanthology.org/2026.findings-eacl.75/), which evaluates equal functional sets rather
than one action sequence. OAC applies that freedom to organizational topologies while requiring a
deterministic obligation projection first.

No public dataset supplies enterprise change, impacted obligations, roles, authority, evidence,
ordering, plural legal organizations, and independent outcomes together. EnterpriseOps-Gym is for
Spec 005 outcomes; EDiTh, EnterpriseRAG-Bench, OCEL, and BPI remain separate evidence tracks and MUST
NOT be merged into fictitious organization Ground Truth.

## Decision

```text
SealedResource/v1 + normative rule projection + path accounting
                              ↓
               stdio/v2 validateResource
                              ↓
             stdio/v2 deriveProfileReport
                              ↓
       Python / internal Go exact report-byte parity
                              ↓
   frozen + public generated/metamorphic disagreement evidence
                              ↓
            next: plural fixed-plan verification
```

This was ADR 0005's intended seed-1 transition. ADR 0006 now inserts the semantic branch matrix and
durable evidence refresh before fixed-plan verification.

The present turn is complete only when the implemented slice has reproducible evidence and an exact
claim boundary. It does not wait for human Gold, runtime execution, or external maintainers.

## Defect exposed while implementing the decision

The blocker was not hypothetical. The historical SC-008/009/010 change resources carry digests over
the reference model projection, which materialized omitted optional null defaults. Their raw-map
detached digests differ:

| Change | Historical typed-projection digest | SealedResource/v1 raw-map digest |
|---|---|---|
| SC-008 | `sha256:69ec666f7453286983111ec0254f8c67213156561224e05d958eea8aedc8d40c` | `sha256:004a9369bb7a2ff342005181200a151ee2fbd3ea88de15b93b3d098717b3c45a` |
| SC-009 | `sha256:1cb4f27562a8a156cb6448bf9b65e2060905b917c16a696d1464fb5b1ffaac56` | `sha256:d98a8a0754a8915f8d1740fca77fbe9253e17a35fb2d2e4a453e9194dc11566f` |
| SC-010 | `sha256:be2131dc99c711e30031adb336fd9da4ee3f611e8b56d42239297951f39354d2` | `sha256:3a99b52f09cc400f0be08d42fc04f06b86f6449357de764a5b389fa4b72ea3b7` |

Classification: `SPECIFICATION_DEFECT` plus reference canonicalization debt. The historical resources
and A0 bundle remain immutable. The A1 capsule copies and raw-reseals them under a successor evidence
coordinate, and its reference entrypoint consumes immutable `AdmittedSealedResource` records so it
cannot accidentally reapply the legacy typed digest. This is precisely why parser/canonical-byte
agreement precedes semantic parity.

A second evolution defect was also prevented: the old A0 bundle builder discovered CTK schemas through
a wildcard. Adding A1 schemas would silently rewrite the frozen seven-schema/24-case bundle. A0 now
uses an explicit schema allowlist; successor schemas belong only to successor capsules.

## Resolved differential: `generated:admission`

The first complete Python/Go run did not pass cleanly. A publicly generated variant changed
`production:energy.admissionStatus` from `admitted` to `candidate`. The Python reference returned
`ERROR/PREREQUISITE_OBLIGATION_ORDER_MISSING`; the Go kernel returned a completed report.

The public Supplier rule, rather than implementation status, resolved the disagreement: every admitted
non-FALSE contextual rule carrying a `prerequisiteObligationType` requires exactly one predecessor and
successor obligation. Candidate authority prevented the successor obligation from materializing, so a
completed partial order was illegal. The Go kernel now fails closed for missing, ambiguous, same-role,
and cyclic prerequisites and contains a direct regression.

The exact original observations, generator/source digests, resolution, and regression reference are
preserved in
`experiments/supplier-v02-portability/disagreements/DIS-001-generated-admission.json`. Classification:
`IMPLEMENTATION_DEFECT`. The final seed-1 run recorded 34/34 selected observations with no open
disagreement inside that selected set; the historical record remains evidence that public generation
falsified a real implementation assumption. ADR 0006 later found additional branch disagreements
outside the selected set and supersedes this result as current claim evidence. It also requires a new
digest-bound generator, capability set, summary, and installed replay rather than retroactively
rewriting seed-1.
