# OAC roadmap and evidence requirements

OAC develops the organizational contract and its verification boundary separately from runtime execution and enterprise adoption. Extending a resource vocabulary or passing a reference test does not qualify the later layers automatically.

## Supported reference surfaces

| Surface | Public material | Qualification boundary |
| --- | --- | --- |
| Sealed resources | Strict Kinds, registries, schemas, canonical and detached digests | Input integrity and selected semantic checks |
| Context and obligations | Supplier applicability, explicit Unknown and bounded derivation | Reference Profile semantics; complete enterprise knowledge is not established |
| Plan acceptance | Compiler, verifier, CTK/TCK and included Python/Go implementations | Plural valid Plans under selected constraints; independently maintained semantics remain open |
| Enterprise intake | Exact material bytes, rule/authority pins and admission receipts | Bounded structured Supplier inputs; external identity and source trust are integration responsibilities |
| Runtime lowering | Contract-valid zero-effect bundles and explicit handlers | Lowering does not grant execution authority |
| Demand, Admission and Outcome | Lifecycle roots, receipt and observation relationships | Portable reference contracts; business correctness requires admitted observations |
| Governed successors | Versioned references, exact lineage and negative controls | Repeated enterprise learning and autonomous promotion are not established |

The [architecture](architecture/README.md) describes implementation boundaries. [Validation](validation/README.md) lists the executable checks included in the public source profile.

## Evidence required for wider claims

An externally maintained implementation must demonstrate agreement from public contracts, schemas and conformance inputs without sharing the reference semantic oracle. Human Ground Truth requires qualified independent annotation and adjudication. Enterprise outcomes require an authorized mapping and observation contract with actual target effects and separate acceptance.

Cross-case learning requires retained counterexamples, frozen evaluation inputs, independent quality assessment and controlled successor publication. Production profiles require their own identity, effect, operational and customer acceptance boundaries. These are distinct requirements; a synthetic zero-effect reference cannot close them.

A new Profile or evidence coordinate must preserve its predecessor and identify its exact material closure. Unsupported conditions retain `UNKNOWN`, `HOLD` or `NOT_RUN`, as applicable. The [evidence versioning rules](validation/HISTORICAL-EVIDENCE-VERSIONING.md) define the historical and successor distinction.
