# Spec 003 Conformance Landscape and OAC Migration

**Research date**: 2026-08-23

**Scope**: bundle identity, capability declaration, frozen requirements, expected outcomes, reason
codes, black-box adapters, differential disagreement, independence, and bounded claims

**Source policy**: standards bodies, official project specifications, and official repositories only

**Repository evidence snapshot**: the self-contained 36-artifact Phase A bundle is
`sha256:5a8f498a1b1be52a1c61eaf5f1f2f073970b7a20f89f65f6aca3158bace3f526`. This is a
mechanics/test-bundle identity, not a conformance or independence badge.

## Executive conclusion

No surveyed agent standard supplies OAC's organizational derivation oracle. The strongest design is a
composition of mature conformance patterns:

```text
MCP frozen requirements + harness/SUT separation
Kubernetes non-optional core and no-skip certification discipline
OPA bundle/capability manifests
W3C declarative action/result/error/report vocabularies
Cedar independent semantic model + differential generation
NIST requirement-to-test and repeatability discipline
OpenTelemetry exact version/stability boundaries
RFC 8785/6901/3986/6920 byte and identifier foundations
```

The immediate project implication is not “write another verifier.” It is to freeze a versioned
black-box experiment that cannot hide required failures, reveal expected answers to the SUT, prescribe
one Agent topology, or resolve disagreement by reference preference. The current experiment chooses
JSON/stdio v1; transport neutrality is a future property, not a current claim.

## 1. MCP Conformance: closest operational harness

The official [MCP Conformance Framework](https://github.com/modelcontextprotocol/conformance/blob/main/README.md)
tests
clients through a command and servers through a URL, captures protocol interactions, validates the wire
schema, and emits structured checks. Two details directly address OAC risks:

1. `wire-schema-valid` identifies SUT output invalidity, while `wire-schema-harness-error` identifies an
   invalid message sent by the harness.
2. Its [Conformance Requirements](https://github.com/modelcontextprotocol/conformance/blob/main/README.md#conformance-requirements)
   use dated `requirements/<revision>.yaml` files to freeze which scenarios were required for a
   protocol release.
   Dynamic suite/version filtering cannot answer that question after the suite grows.

The MCP requirement-set documentation also separates required from not-scored extension,
added-after-release, and pending scenarios. Its
[Expected Failures](https://github.com/modelcontextprotocol/conformance/blob/main/README.md#expected-failures)
section keeps the implementation baseline distinct: a baselined failure remains a failure against the
requirement set, and a stale baseline entry fails when its test starts passing.

### OAC migration

- Replace a dynamic “run all current TCK cases” claim with a digest-bound `RequirementSet`.
- Add `HARNESS_ERROR` as a case outcome; never map runner invalidity to OAC `REJECT`/`UNKNOWN`.
- Keep implementation expected-failure baselines outside requirement scoring.
- Start one SUT adapter with an opaque request, and validate the runner's own request before launch.
- Freeze required cases by exact OAC standard/Profile/wire revision.

### Limit

MCP scenarios contain executable check logic. OAC may use executable orchestration, but Supplier
semantics cannot live only inside the runner, because that would make the runner a second hidden
reference implementation.

## 2. Kubernetes: non-optional core and claim governance

The official [Kubernetes Conformance Test Requirements](https://github.com/kubernetes/community/blob/main/contributors/devel/sig-architecture/conformance-tests.md)
limit core conformance tests to GA, non-optional, provider-independent behavior, require stability/soak,
and require each promoted test to carry release, test name, and an RFC 2119 behavior description. The
[CNCF submission instructions](https://github.com/cncf/k8s-conformance/blob/master/instructions.md)
state that a valid certification run may not skip any conformance test.

Kubernetes also avoids binding volatile Condition Reason/Message text unless the API guarantees it.

### OAC migration

- A Supplier Profile core capability cannot be omitted to produce a skip.
- Every case binds a normative requirement and human-readable test purpose.
- Normative OAC reason codes must be explicitly registered and versioned; implementation messages and
  incidental diagnostics remain unscored.
- Test promotion into a frozen RequirementSet is a reviewed, versioned change, not an automatic effect
  of adding a test file.
- Passing a CTK is not itself authorization to use a compatibility brand; claim governance is separate.

### Limit

Kubernetes checks runtime behavior and generally seeks one pass/fail property, while OAC permits many
valid Plan topologies. OAC therefore cannot use one serialized golden plan as its compiler oracle.

## 3. OPA: bundles and versioned capabilities

[OPA Bundles](https://www.openpolicyagent.org/docs/management-bundles) package policy/data with a
manifest, revision, roots, per-file content, optional signatures, and a published manifest schema.
[OPA CLI capabilities](https://www.openpolicyagent.org/docs/cli#capabilities) describe available
builtins, future keywords, and Wasm ABI features and may be selected by OPA version.

OPA's bundle signature model hashes listed files. Its current manifest design allows unknown top-level
members while keeping selected sub-records closed.

### OAC migration

- Publish an artifact ledger containing logical path, media type, byte size, and SHA-256.
- Derive bundle identity from the RFC 8785 manifest projection, not tar timestamps/order/compression.
- Reject unlisted or missing files and unsafe archive entry types before SUT execution.
- Keep publication authority outside the self-described bundle: the frozen runner pins one reviewed
  coordinate/digest; a future multi-suite runner needs a signed/versioned trust registry.
- Define capability as `role × operation × Profile × Profile version × wire version`, not a marketing
  boolean.
- Preserve extension freedom only inside an explicit namespaced `extensions` envelope; reject unknown
  core fields so spelling errors cannot disappear silently.

### Limit

OPA capabilities describe policy-language/runtime features, not permission to skip mandatory semantics.
OAC RequirementSet membership remains authoritative over core scoring.

## 4. W3C JSON-LD, SHACL, and EARL: declarative expectations

The official [JSON-LD test vocabulary](https://w3c.github.io/json-ld-api/tests/vocab.html) defines an
input action, exact expected result for positive evaluation tests, and `expectErrorCode` for negative
evaluation tests. Syntax tests and evaluation tests remain distinct.

[SHACL](https://www.w3.org/TR/shacl/) separates the overall `sh:conforms` boolean from structured
validation results such as focus node, result path, value, source shape, and source constraint
component. The current [SHACL 1.2 draft](https://www.w3.org/TR/shacl12-core/) explicitly warns that
passing every published test proves only the tested aspects, not complete specification conformance.

[EARL](https://www.w3.org/WAI/standards-guidelines/act/report/earl/) defines vendor-neutral test
assertions with passed, failed, inapplicable, cannot-tell, and untested outcomes.

### OAC migration

- Make case action/inputs and expectation first-class data rather than embedding all truth in test code.
- A fixed verifier case has one domain verdict and separate required/forbidden reason and witness sets.
- Separate `PASS/FAIL/NOT_SCORED/NOT_RUN/INDETERMINATE/HARNESS_ERROR` from the OAC domain verdict.
- Compare human-readable messages never; compare only explicitly stable normative codes/fields.
- Publish the claim ceiling that a passing set covers only its listed requirements.

### Limit

Exact JSON output works for a deterministic JSON-LD operation. It is inappropriate for an OAC compiler
whose valid topology is intentionally plural. OAC uses exact output only for canonical bytes and the
topology-free derivation projection; compiler plans are checked against the public relation.

## 5. Cedar: semantic differential testing

The official [cedar-spec repository](https://github.com/cedar-policy/cedar-spec) contains a Lean
definitional implementation, a production Rust implementation counterpart, generators for schemas,
entities, policies, and requests, and infrastructure for differential randomized testing between the
formalization and production implementation.

### OAC migration

- Add a second semantic kernel rather than a second adapter around the same kernel.
- Compare a canonical topology-free `ProfileDerivationReport` before the final verdict.
- Add seeded generation across predicate atoms and bounded micrographs.
- Persist generator version/digest, seed, both observations, and a minimized regression case.
- Treat agreement as evidence, not proof; disagreement remains open until a normative resolution.

### Limit

A definitional/formal implementation and production implementation can still share maintainers,
assumptions, or dependencies. This pattern supports code/semantic differential evidence; it does not by
itself satisfy OAC's external organizational-independence gate.

## 6. NIST: what makes a conformance program real

[NIST Conformance Testing](https://www.nist.gov/itl/ai/applied-ai-research-group/conformance-testing)
states that conformance criteria must first exist in a standard/specification, that test suites combine
legal/illegal inputs with expected results, and that testing should be platform-independent and
repeatable. It separates a test tool from a wider testing/certification program and notes that a
reference implementation is most useful in early suite development.

### OAC migration

- Link every scored case to a published normative requirement.
- Keep test-tool completion distinct from certification/branding/governance.
- Publish enough prose and data that a second implementation does not need reference source.
- Treat the reference implementation as an early evidence generator, never as unversioned normative
  truth.
- Require clean-environment repeatability and exact artifact identities.

## 7. OpenTelemetry: version and stability discipline

[OpenTelemetry versioning and stability](https://opentelemetry.io/docs/specs/otel/versioning-and-stability/)
separates specification, language implementation, API, SDK, semantic-convention, and contrib versions;
one implementation package version need not equal the specification version. Stable surfaces receive
explicit backward-compatibility guarantees.

The official [OTLP specification](https://github.com/open-telemetry/opentelemetry-proto/blob/main/docs/specification.md)
distinguishes mandatory and optional capabilities and requires interoperability to regress to the
lowest common functional denominator where versions differ.

### OAC migration

- Record implementation version, standard version, Profile version, wire version, RequirementSet, and
  capability version independently.
- Test exact version pairs, not presumed “latest is compatible” behavior.
- Keep stable core and experimental extensions in separately scored surfaces.
- Do not infer identifier or semantic version from `compilerVersion`.

### Limit

OpenTelemetry provides strong compatibility discipline but not an OAC-like organizational closure
oracle or a directly reusable semantic CTK.

## 8. RFC foundations

### RFC 8785

[RFC 8785](https://www.rfc-editor.org/rfc/rfc8785) defines deterministic JSON serialization, ECMAScript
number serialization, and UTF-16 code-unit ordering for object property names.

OAC A0 therefore needs explicit vectors for duplicate keys, `-0`, lone surrogates, and an object-key
pair whose UTF-16 and Unicode-scalar orders differ (`U+10000` and `U+E000`). RFC 8785 Appendix B is
also decisive at the integer-looking boundary: the finite binary64 value `9007199254740992` is a valid
JCS number even though schema-declared application integers should normally remain within the safe
integer range. Both current kernels now carry the Appendix B serializable vectors.

### RFC 6901 and RFC 3986

[RFC 6901](https://www.rfc-editor.org/rfc/rfc6901) defines JSON Pointer and its `~0`/`~1` token escaping.
[RFC 3986](https://www.rfc-editor.org/rfc/rfc3986) defines percent encoding and URI component syntax.

OAC combines them as two separately encoded UTF-8 components separated by one literal `#`. Strict
uppercase escapes and decode/re-encode equality create one canonical wire representation while simple
existing `resource#/pointer` refs remain byte-identical.

### RFC 6920

[RFC 6920](https://www.rfc-editor.org/rfc/rfc6920) is primary precedent for algorithm-identified hash
names. OAC uses a domain-separated custom URN with explicit `sha256:v1:kind`, not an RFC 6920 `ni` URI.

### Unicode, JSON Schema, and fd-anchored loading

[Unicode 17.0 PropList](https://www.unicode.org/Public/17.0.0/ucd/PropList.txt) enumerates exactly 25
`White_Space` code points. Python, Go, and schema regex engines expose different convenience
predicates; [JSON Schema 2020-12](https://json-schema.org/draft/2020-12/json-schema-validation)
recommends, but does not make every validator implement, the ECMA-262 regex dialect. OAC therefore
freezes `NonBlankString/v1` as an explicit character set and carries boundary discriminators instead
of treating `isspace`, `unicode.IsSpace`, or `\S` as specification text.

The Linux [`open(2)`/`openat(2)` documentation](https://man7.org/linux/man-pages/man2/openat.2.html)
states that `O_NOFOLLOW` on a multi-component path protects only the trailing component and motivates
directory-fd-relative APIs to avoid prefix races. The portable A0 resolution uses an anchored root fd
and component-by-component no-follow opens on macOS/Linux. It still requires immutable staging because
a directory walk is not an atomic snapshot of a filesystem under arbitrary concurrent writes.

## 9. Repository migration after the Phase A slice

The following is the exact observed boundary after implementation; an implemented mechanism is not the
same as an admitted interoperability claim:

| Surface now present | What it establishes | Remaining migration |
|---|---|---|
| `ctk/bundles/phase-a-v0.1/bundle.json` | self-contained 36-artifact v0alpha1 directory ledger plus external frozen coordinate/digest admission in the runner | archive/signature publication, multi-suite trust registry, and successor-format governance |
| `requirements.json` | 24 required, zero not-scored cases pinned by the bundle ledger | map complete Core/Profile requirements; version future promotions |
| independent `oac-ctk-runner` package | no import/dependency on product `oac`; one-process stdio firewall; pinned-schema and external-digest admission | clean install evidence and a genuinely independent SUT adapter |
| reference `oac.ctk_adapter` | executable oracle for six small Phase A operations | it is not independent evidence; do not count it as a second implementation |
| internal Go Phase A adapter | cross-language Appendix-B/error/closure differential seed that found real defects | same-repository authorship is not clean-room, organizational, or complete Supplier evidence |
| `ClosureMicroReport` | a topology-neutral micrograph relation with five cases | not full Supplier selectors, obligations, orders, derivation, or verification |
| `ProfileDerivationReport` model/schema | reference-side topology-free Supplier projection | expose as a future CTK derive operation and reproduce independently |
| exact legacy ID helpers/vectors | public Evaluation/Path/Obligation preimages and 96-bit compatibility boundary | expand negative/order vectors; never use truncation as a security name |
| full compiler-owned IDs | `{scheme,kind,roots,body}` full SHA-256 URNs | more WorkUnit/Decision vectors; keep verifier IDs opaque |
| canonical witness helper | strict UTF-8, uppercase percent escapes, RFC 6901, one delimiter | expand malformed/double-decode CTK vectors |
| Supplier ResourceProfile v1 | machine limits and non-partial exhaustion protocol | every-counter boundary vectors; new limits require a successor Profile |
| semantic rule development registry | explicit Python `implementationBindings` are structurally separated | emit a separately digest-bound normative projection excluding bindings |
| RunResult v0alpha1 | layered case/SUT observations and pinned bundle/requirements/capability/environment | resource/build/diagnostics digests and DisagreementRecord emission |
| claim text | limits current result to code-independent Phase A harness mechanics | independent semantic kernel, cross-implementation dispute resolution, then M2 |

## 10. Recommended next minimum

The A0 bundle/runner is now the experiment shell. Before claiming A1 parity, add only the missing
evidence resources and full semantic operations:

```text
DisagreementRecord
IndependenceStatement
independent derive + verify operations
plural-plan acceptance cases
mutation/generation/minimization evidence
```

The resulting evidence flow is:

```text
current frozen requirements + opaque inputs
                ↓
external non-semantic runner
                ↓
reference adapter + separately implemented black-box adapter
                ↓
canonical stage observations
                ↓
declarative comparison
                ↓
PASS / FAIL / NOT_SCORED / NOT_RUN / INDETERMINATE / HARNESS_ERROR
                ↓
versioned disagreement resolution, never majority/reference-wins
```

This is the smallest next design that can test OAC's central thesis: organizational topology may vary
while obligations and verification semantics remain independently reproducible. The present 24-case
reference pass proves the shell and micro-relation, not that thesis itself.

## 11. 2026 A1 research refresh: novelty and data boundaries

The closest work makes the next claim boundary sharper:

- [Contract-based Design and Verification of Multi-Agent Systems, AAAI 2025](https://ojs.aaai.org/index.php/AAAI/article/view/34480)
  already provides assume-guarantee composition for multi-Agent tasks and shared safety constraints.
  OAC cannot claim that contracts for MAS are new.
- [MaAS, ICML 2025](https://proceedings.mlr.press/v267/zhang25bi.html),
  [G-Designer, ICML 2025](https://proceedings.mlr.press/v267/zhang25cu.html), and
  [ReSo, EMNLP 2025](https://aclanthology.org/2025.emnlp-main.808/) already select Agents, roles, and
  communication topology dynamically. Automatically growing a graph is not sufficient novelty.
- [MASFactory, ACL 2026](https://aclanthology.org/2026.acl-demo.35/) and contract-driven workflow
  builders further reduce the defensibility of “natural language to Agent graph” as the core result.
- [ETOM, EACL Findings 2026](https://aclanthology.org/2026.findings-eacl.75/) evaluates equal functional
  sets instead of one golden action sequence. OAC adopts the same evaluation principle at the
  organizational level: deterministic obligations, plural legal topologies.
- [Cedar Spec](https://github.com/cedar-policy/cedar-spec) combines executable semantics, a production
  implementation, property generation, and randomized differential testing. This is the direct A1
  engineering precedent.

The defensible OAC innovation hypothesis is therefore the composition:

```text
versioned admitted enterprise facts + semantic change
                    ↓
       topology-free obligation derivation
                    ↓
 multiple replaceable organization compilers
                    ↓
 compiler-independent authority/evidence/order/Unknown assurance
```

It remains a hypothesis until a second semantic kernel and later an external maintainer reproduce it.

### Public-data evidence tracks are not interchangeable

| Source | Actual public oracle | Missing OAC oracle | Proper use |
|---|---|---|---|
| [EnterpriseOps-Gym](https://github.com/ServiceNow/EnterpriseOps-Gym) | seeded state, tools, task and SQL/policy/side-effect verifiers | impacted roles, obligations, authority, plural organization | Spec 005 outcome shadow |
| [EDiTh/Véracier](https://huggingface.co/datasets/lightonai/veracier-industries) | synthetic enterprise documents and retrieval/classification keys | event-to-organization truth | Supplier evidence and future human review |
| [EnterpriseRAG-Bench](https://github.com/onyx-dot-app/EnterpriseRAG-Bench) | questions, answers, many relevant-document IDs, conflict/missing information | organizational impact and duties | evidence retrieval and Unknown stress |
| [OCEL order management](https://ocel-standard.org/event-logs/simulations/order-management/) | generated object-centric process/event relations | normative authority and obligations | structural impact/metamorphic tests |
| [Process Discovery Contest](https://www.tf-pm.org/competitions-awards/discovery-contest) | known-model positive/negative traces | resources, roles, authority | process-structure mutation |
| [Enron hierarchy Gold](https://aclanthology.org/P12-2032/) | static direct/transitive reporting relations | change impact and legal work organization | Snapshot hierarchy validation |

No source supplies the complete chain from enterprise change through duties, authority, evidence,
order, plural organizations, and independent outcomes. Sources from different companies MUST NOT be
joined into a fictitious single-enterprise Ground Truth. The first A1 result is therefore formal
portability against frozen project Profiles, not empirical enterprise correctness.

### Result that would materially strengthen the MVP

The next experiment should require:

1. exact raw-resource digest agreement before semantics;
2. exact `ProfileDerivationReport` JCS byte/digest parity for frozen and hidden generated cases;
3. no false accept across obligation/evidence/order/Unknown mutations;
4. multiple topology-distinct Plans accepted by both verifiers;
5. every mismatch recorded and minimized rather than resolved by reference-wins.

If a graph-only or unconstrained planner later matches these guarantees and EnterpriseOps outcomes with
less machinery, the additional OAC layer has not yet demonstrated value. That is an explicit
falsification condition, not a result to hide.
