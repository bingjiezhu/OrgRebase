# Disposable lab lifecycle and portable outcome evidence

`orgrebase.workspace.outcome_portability` adapts the existing `OutcomeLab` into the explicit `oac.outcome.disposable-local/v0.1` contract. It does not execute tools, dispatch a second runtime, grant credentials, or automatically promote a learned skill. The original accepted organization plan, roles and work units retain `zero_effect` throughout.

The adapter builds and validates the local Source admission and Demand before a run. Before dispatch, the controller creates and retains the exact mapping/grant/binding/RuntimeBundle package. Its sole execution entrypoint requires that package and a separate one-use token. After execution, the adapter verifies the exact returned receipt, reuses the original runtime package, retains every observation payload, and emits a portable `OutcomeCertificate`. All lifecycle semantic validation goes through `OACPlanGate.validate_lifecycle` and the configured public `oac validate-evolution` CLI. Product code does not import a sibling OAC implementation.

## Trusted host sequence

```python
from orgrebase.workspace.outcome_portability import (
    LabOutcomeTrust,
    build_lab_lifecycle,
    build_portable_lab_outcome,
    verify_portable_lab_outcome,
)

# gate, mapping, oracle, snapshot, change and plan are frozen host inputs.
# controller_authority and acting_authority are distinct local script identities;
# oracle.authority must be distinct from both. They are not human credentials.
lifecycle = build_lab_lifecycle(
    gate, mapping, oracle, snapshot, change, plan,
    controller_authority, acting_authority,
)
# Persist lifecycle.digest through the host's trusted evidence store here.
# lab is the existing OutcomeLab constructed from those same frozen inputs.
approval = lab.approve("oac", authority=controller_authority)
# Retain approval["runtime_bundle"] before dispatch; it contains no secret token.
receipt = lab.run(
    approval["token"], requests, runtime_bundle=approval["runtime_bundle"],
)

trust = LabOutcomeTrust(
    mapping=mapping,
    oracle=oracle,
    controller_authority=controller_authority,
    acting_authority=acting_authority,
    lifecycle_digest=lifecycle["digest"],
    receipt_digest=receipt["digest"],
    approval_digest=approval["receipt"]["digest"],
    # Supply the actual admitted observer implementation/source-closure build pin.
    # This is distinct from oracle.digest, which identifies its policy/configuration.
    oracle_build_digest=oracle_build_digest,
)
# Persist this context from the trusted controller, separately from exported data.
portable = build_portable_lab_outcome(gate, lifecycle, receipt, trust)
verify_portable_lab_outcome(gate, portable, trust)
# Persist portable; its outcome_certificate is accepted by the public OAC CLI.
```

`LabOutcomeTrust` is a frozen host-side context. Its lifecycle, receipt, approval and oracle build digests must come from trusted admission/controller records. Never construct it by copying pins from an imported bundle. A digest proves content identity, not an issuer signature, human qualification or enterprise IAM. Its `recorded_at` is the time of evidence packaging, not a claim about when a business effect occurred.

The public OAC validator checks reference/root/verdict consistency. The product verifier additionally reconstructs the entire local payload closure from the independently pinned lifecycle and receipt. Replacing, omitting or adding a payload, changing grant/receipt/trace content, or resealing a forged certificate still fails. The grant is part of the original pre-execution package. The controller consumes its token even if a supplied package is rejected; a package or a digest alone cannot authorize execution. This adapter cannot independently authenticate a controller or authorize a new execution.

## Retained evidence

The lifecycle contains the actual snapshot, change, accepted plan/certificate, selected qualified requester from the snapshot, exact declared subjects, criterion, evidence obligations, zero-effect constraint, local authority/profile payloads and a full intake manifest. It records `CONTROLLED_LOCAL_SCRIPT_ADMISSION` and explicitly denies a human qualification claim. It does not relabel local fixture principals as customers or independent reviewers.

The strict v2 result contains the original lifecycle and full raw v2 controller receipt, including its original execution package, plus exact, content-bound payloads for:

- `DisposableExecutionGrant`: the actual approval receipt, controller identity, mapping and plan certificate; disposable scope only.
- `DisposableRuntimeBinding` and `DisposableRuntimeBundle`: the same independent grant, profile, seed/environment pins, budget, exact tools/arguments, roles, work units and predecessor relation. Production authority is false; plan effect ceiling stays zero-effect.
- `ExecutionReceipt`: the exact digest of the verified controller receipt with binding, bundle and grant refs. The full controller receipt occurs once at the top of the portable bundle.
- Execution evidence: exact controller-receipt digest and state observation roots, initial/final/reset roots, trace and tool timing records. Every complete state observation remains in the single full controller receipt, including missing or unresolved observations. The verifier rebuilds these references from that receipt; changing a pointer and resealing the envelope does not pass.
- Independent observation, policy/build profile and six dimension evidence payloads. Unknown dimensions retain exact unresolved observation refs and reasons.
- `OutcomeCertificate`: exact `S/D/P/X` roots, the pinned disposable profile, separate grant and all six dimensions.

Failures and uncertainty follow the controller's recomputed evidence. A missing evidence obligation can yield `REJECT` while other dimensions remain `UNKNOWN`. A completed task with an unobservable reset can yield overall `UNKNOWN`. The adapter never promotes these outcomes to acceptance. Generic product failure reasons use `LAB_<DIMENSION>_FAILED`; forbidden effects and unknown observations use the published OAC reason codes.

## Verification

`tests/workspace/test_outcome_portability.py` exercises the actual public OAC CLI and the existing controller on a small local state environment. It reuses frozen structural retail plan inputs from `tests/fixtures/outcome-lab-retail`; its state operations are deliberately labelled controlled-local test behavior, not a fresh upstream benchmark run. Tests cover accepted and rejected state changes, worker loss, reset uncertainty, consumed-token reuse denial and resealed substitutions across every payload class and trusted pin.

The default test CLI uses `ORGREBASE_OAC_ROOT` or the sibling OAC checkout. To test an installed wheel without importing checkout source into the product, set `ORGREBASE_LAB_OAC_COMMAND_JSON` to a JSON array containing the installed `oac` executable. This variable selects only the test fixture's existing `OACPlanGate`; it adds no production configuration or execution path.

These checks establish a local, reference-consistent evidence bridge. They do not establish public-benchmark coverage, real human independence, broad skill-learning gains, production connector authorization or customer cost savings. Final release claims require a fresh integrated source closure and full parent-workspace verification; old seed-2 identity evidence remains immutable.

The pre-execution package is built once by `outcome_runtime.build_runtime_bundle` using the controller's verified plan references, immutable mapping, approval, actor and metadata. Execution compares the caller's package to the controller-owned copy before dispatch, then uses the private copy. Receipt verification rebuilds its data closure; portable verification additionally compares its plan references and metadata to the independently pinned lifecycle. Full state observations remain stored once, rather than copied into each execution evidence payload.

The unpublished v1 product receipt and portable encoding are not accepted by this v2 writer/verifier. Historical runs retain their own frozen verifier and original bytes. They cannot be relabelled as executions of the new contract.
