# Related work and contribution boundaries

**Status: informative, non-normative. Reviewed 2026-09-14.** This note explains design relationships;
it does not change wire formats, acceptance rules, frozen conformance bundles, or implementation claims.

OAC addresses the contract between admitted organizational facts, a demand or semantic change, a
candidate work organization, and bounded plan/outcome assurance. Its proposed contribution is the
combination of these requirements in an explicit, inspectable contract. Versioning, authorization,
three-valued logic, content addressing, provenance, and independent checking are established ideas,
not inventions claimed by this project.

## Closely related mechanisms

| Work | Shared concern | Boundary of the comparison |
|---|---|---|
| [Zanzibar, USENIX ATC 2019, sections 2.2 and 2.4](https://www.usenix.org/system/files/atc19-pang.pdf) | Authorization checks must respect the causal order of permission and content changes. A zookie can be stored alongside a content version. | The usual check requires a snapshot **at least as fresh** as the token; it does not freeze all later checks to the approval-time snapshot. This is authorization consistency, not an organizational obligation derivation or a business-successor approval contract. |
| [SpiceDB consistency and ZedTokens](https://authzed.com/docs/spicedb/concepts/consistency) | Explicit consistency coordinates prevent stale permission decisions. | `at_least_as_fresh` permits newer data. `at_exact_snapshot` also exists, subject to snapshot retention. ZedTokens are opaque datastore coordinates; OAC digests identify frozen artifact bytes. Neither a token nor a digest independently proves that an enterprise source was admitted. |
| [Kubernetes API resource versions](https://kubernetes.io/docs/reference/using-api/api-concepts/) and [admission failure policy](https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/#failure-policy) | Reject stale writes and separate admission from persistence. | A stale update can return 409. `failurePolicy: Fail` rejects webhook invocation failures; an explicit webhook denial rejects independently of that setting. These mechanisms do not themselves define which organizational obligations a semantic change creates. |
| [W3C PROV-DM](https://www.w3.org/TR/prov-dm/) | Represent entities, activities, responsible agents, and derivation. | Provenance supports assessment of an artifact's origin. It is not permission to execute or to promote a generated claim into admitted Source. OAC leaves PROV bindings external and adds its own bounded organizational constraints. |
| [SpiceDB caveats](https://authzed.com/docs/spicedb/concepts/caveats) | Preserve uncertainty when necessary context is absent. | SpiceDB can return conditional permissionship and missing context fields. OAC therefore does not claim that distinguishing an unresolved result from denial is unique. Its applicability and Plan verdicts have their own explicit semantics. |
| [OpenAI Presence, July 2026](https://openai.com/index/introducing-openai-presence/) | Combine enterprise policy, scoped access, approved actions, human escalation, evaluation, and controlled updates. | Governed agents and ongoing improvement are already product capabilities in the field. OAC's comparison concerns published contract and evidence surfaces; an overview's silence about a particular invariant is not evidence that another product lacks it. |
| [Anthropic: Trustworthy agents in practice, April 2026](https://www.anthropic.com/research/trustworthy-agents) | Keep humans in control and place oversight in the model, harness, tools, and operating environment. | These principles motivate explicit boundaries, but do not certify OAC or establish that its authority separation is unprecedented. |

## What this contract makes explicit

The [Core](../../standard/oac-core-v0.1.md) combines admitted organization and dependency inputs,
semantic impact-transfer rules, obligation coverage, qualifications, separation of duties, partial
orders, evidence duties, and bounded completeness. A replaceable compiler chooses a Plan; the
verifier checks the declared relation rather than similarity to a preferred compiler output.

The [bounded plural-Plan relation](../../standard/oac-plan-verification-relation-v0.1.md) permits
materially different accepted Plans under the same frozen roots. This is a contract choice, not proof
that constraint-based planning or plural solutions were invented here. The reference compiler and
verifier share the Supplier Profile oracle; compiler/topology independence does not establish
independent semantics or an independently maintained implementation.

Strong Kleene applicability uses `TRUE`, `FALSE`, and `UNKNOWN`. Plan verification instead yields
`ACCEPT`, `REJECT`, `PROVISIONAL`, or `UNKNOWN`. `UNKNOWN` is not acceptance or authorization;
neither `ACCEPT` nor a matching digest grants runtime permission or human approval. Protocol errors
and incomplete domain knowledge also remain distinct. A digest proves the captured bytes, while
source admission, identity, and current authority require their separately declared trust boundaries.

An external application such as OrgRebase can use these contracts to connect organizational changes
to affected work, exact-owner approval, and selective successor creation. That integration is the
business problem to evaluate. OAC itself does not execute it. The application's candidate, admission,
human-approval, and canonical-write boundaries are distinct from OAC's SOURCE, PLAN, EVIDENCE,
CONFORMANCE, and BENCHMARK document classes; neither classification claims a new authorization primitive.

## Evidence and continuing comparison

[PROV-AGENT (2025)](https://arxiv.org/abs/2508.02866v3) extends provenance to agent interactions and
workflow context. [AgentS4D (July 2026 preprint)](https://arxiv.org/abs/2607.27294v1) evaluates runtime
risks through actions, side effects, and state changes, including unsafe runs that still complete.
Both are useful comparison points for evidence design. Their results are not OAC evaluations, and
an execution trace alone does not establish business correctness, authority, or safety.

Current OAC claims remain tied to exact profiles, artifacts, implementations, and test coordinates.
They do not establish a world-first result, complete conformance, independent enterprise adoption,
production safety, or measured customer value. The relevant product question is whether the complete
change-to-successor path reduces missed updates and unnecessary work while preserving authority;
that requires separately scoped application and customer evidence.
