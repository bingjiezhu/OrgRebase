# Enterprise Intake state projection

`oac.intake_state.project_intake_state` exposes the eight independent axes of the
bounded Enterprise Intake profile. The projection coordinate is
`oac.enterprise-intake/eight-axis/v0.1`. It is a computed transport view, has no
`kind`, and is not registered or persisted as a new authority resource.

The function accepts exact `AdmittedSealedResource` inputs and existing OAC
receipts. It revalidates admitted raw maps, preserving omitted members and explicit
nulls. It calls the existing Supplier derivation, Plan verifier and G1a lowerer;
there is no second impact algorithm, Plan acceptance relation or runtime path.

| Axis | Evidence and scope |
|---|---|
| Source authority | An exact intake receipt admits the Snapshot envelope. Missing, rejected or unknown admission leaves the supplied material a candidate. This says nothing about promotion of nested candidate, disputed or retracted facts. |
| Observability | The Change's explicit **after-values**. Not-observable and unknown values remain visible; they are not inferred from Plan status. A mixture with known values retains the uncertainty. All not-applicable values produce `NOT_APPLICABLE`. |
| Applicability / impact | Existing Supplier derivation over the exact Snapshot and Change. Any unresolved scoped path or derivation failure yields `UNKNOWN`; otherwise an affected path yields `TRUE`. `FALSE` requires every path to be proven unaffected or explicitly outside the declared scope. |
| Plan assurance | The supplied certificate must equal the independently recomputed certificate for the exact Plan and source inputs. No supplied certificate means no Plan verdict. |
| Runtime admission | A binding requires the exact Plan and certificate. Supplied lowering evidence must equal the existing G1a result for the caller's binding allowlist. A produced bundle is `BOUND_NOT_ADMITTED`; a blocked lowering is `BLOCKED`. Neither has invoked a runtime. |
| Execution | `NOT_RUN`: this profile has no execution input or transition. |
| Outcome assurance | `NOT_EVALUATED`: this profile has no business oracle. |
| Evolution governance | `OBSERVATION`: reading a state view cannot admit a Source successor. |

Each axis carries its evidence references and the scope of the projection. The
source root exists only for an admitted Snapshot. The demand root additionally
requires the exact Demand and Change among the receipt's admitted subjects, and
the Demand must bind the same Snapshot, namespace, governance and Change demand
identifier. Its trigger set must contain the actual Change's complete reference,
including revision and digest; the intake gate and view share this check.
The same Demand verifier also resolves the admitted requester and
accountable role and checks eligibility; an exact receipt cannot override an
invalid requester or role. A Plan root binds the actual certificate, including a non-accepting
certificate; the existence of a root is not a success flag. Execution, outcome and
evolution roots remain absent in this profile.

Receipt authenticity and the binding allowlist remain caller-owned authority
boundaries. The view checks their internal evidence coherence; it does not turn an
unsigned reference into authenticated enterprise identity. It has no `allGood`
field, accepts no caller-selected axis states, and must not be used as an admission
or execution command. Actual runtime execution and business Outcome evidence have
their own owning profiles; this E0a view does not invent those capabilities.

`tests/test_intake_state.py` exercises admitted and non-admitted sources, exact
Demand admission, omitted JSON members, independent certificate verification,
blocked and produced G1a results, cross-stage substitution and missing evidence.
It also verifies that unresolved Supplier derivation does not erase separately
observable Change values.
