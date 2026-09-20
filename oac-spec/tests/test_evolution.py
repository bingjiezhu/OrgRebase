from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from oac.canonical import OACValidationError, parse_resource, resource_ref, seal_resource
from oac.cli import main
from oac.evolution import (
    project_demand_root,
    project_evolution_root,
    project_execution_root,
    project_outcome_root,
    project_plan_root,
    project_source_root,
    verify_organizational_demand,
    verify_outcome_certificate,
    verify_source_admission_receipt,
    verify_successor_admission,
)
from oac.models import (
    AdmissionVerdict,
    DimensionVerdict,
    OrganizationalDemand,
    OrganizationalDemandSpec,
    OrganizationSnapshot,
    OutcomeCertificate,
    OutcomeCertificateSpec,
    OutcomeDimension,
    OutcomeVerdict,
    ResourceMetadata,
    ResourceRef,
    SourceAdmissionReceipt,
    SourceAdmissionReceiptSpec,
)
from oac.registry import KIND_MODELS

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "oac.examples.supplier"
NOW = datetime(2026, 8, 26, tzinfo=UTC)


def _digest(seed: str) -> str:
    return f"sha256:{hashlib.sha256(seed.encode()).hexdigest()}"


def _ref(kind: str, resource_id: str, *, namespace: str = NAMESPACE) -> ResourceRef:
    return ResourceRef(
        kind=kind,
        namespace=namespace,
        resourceId=resource_id,
        revision=1,
        digest=_digest(resource_id),
    )


def _snapshot() -> OrganizationSnapshot:
    value = parse_resource(
        (
            ROOT
            / "profiles"
            / "supplier-change"
            / "inputs"
            / "veracier-proc01.snapshot.json"
        ).read_bytes(),
        verify_digest=True,
    )
    assert isinstance(value, OrganizationSnapshot)
    return value


def _intake_receipt(snapshot: OrganizationSnapshot) -> SourceAdmissionReceipt:
    snapshot_ref = resource_ref(snapshot)
    profile = _ref("IntakeProfile", "profile:supplier-intake@1")
    proposer = _ref("Principal", "principal:data-owner")
    authority = _ref("Principal", "principal:governance-owner")
    reviewer = _ref("Principal", "principal:qualified-reviewer")
    receipt = SourceAdmissionReceipt(
        metadata=ResourceMetadata(
            id="receipt:supplier-intake-1",
            namespace=NAMESPACE,
            revision=1,
            ownerRef="role:evidence-discovery",
            governanceRef=snapshot.metadata.governance_ref,
            createdAt=NOW,
            sourceRefs=(
                snapshot_ref.resource_id,
                profile.resource_id,
                authority.resource_id,
            ),
        ),
        spec=SourceAdmissionReceiptSpec(
            admissionPurpose="enterprise_intake",
            subjectRefs=(snapshot_ref,),
            intakeManifestDigest=_digest("supplier-intake-manifest"),
            intakeProfileRef=profile,
            ruleSetDigest=_digest("supplier-intake-rules"),
            proposerRefs=(proposer,),
            decisionAuthorityRef=authority,
            reviewerRefs=(reviewer,),
            verdict=AdmissionVerdict.ADMITTED,
            admittedSubjectRefs=(snapshot_ref,),
        ),
    )
    return seal_resource(receipt)


def _demand(snapshot: OrganizationSnapshot) -> OrganizationalDemand:
    subject = _ref("OrganizationSubject", "supplier:aurora-components")
    trigger = _ref("SemanticChangeSet", "change:SC-008")
    demand = OrganizationalDemand(
        metadata=ResourceMetadata(
            id="demand:supplier-status-review",
            namespace=NAMESPACE,
            revision=1,
            ownerRef="role:procurement-owner",
            governanceRef=snapshot.metadata.governance_ref,
            createdAt=NOW,
            sourceRefs=(snapshot.metadata.id, subject.resource_id, trigger.resource_id),
        ),
        spec=OrganizationalDemandSpec(
            snapshotRef=resource_ref(snapshot),
            requesterPrincipalRef="principal:procurement-agent",
            accountableRoleRef="role:procurement-owner",
            objective="objective:review-supplier-status-with-zero-effects",
            subjectRefs=(subject,),
            triggerRefs=(trigger,),
            desiredOutcomeRefs=(_ref("OutcomeCriterion", "criterion:review-complete"),),
            evidenceObligationRefs=(
                _ref("EvidenceObligation", "obligation:review-evidence"),
            ),
        ),
    )
    return seal_resource(demand)


def _outcome_certificate(
    snapshot: OrganizationSnapshot,
    intake: SourceAdmissionReceipt,
    demand: OrganizationalDemand,
    *,
    verdict: OutcomeVerdict = OutcomeVerdict.REJECT,
) -> OutcomeCertificate:
    snapshot_ref = resource_ref(snapshot)
    intake_ref = resource_ref(intake)
    demand_ref = resource_ref(demand)
    change = demand.spec.trigger_refs[0]
    plan = _ref("OrganizationPlan", "plan:SC-008:BASE")
    plan_certificate = _ref("PlanCertificate", "certificate:SC-008:BASE")
    binding = _ref("RuntimeBinding", "binding:SC-008:BASE")
    bundle = _ref("ZeroEffectRuntimeBundle", "bundle:SC-008:BASE")
    execution_receipt = _ref(
        "ExecutionReceipt", "execution:SC-008:BASE:COMPLETED"
    )
    execution_evidence = _ref("ExecutionEvidence", "evidence:runtime:SC-008")
    observation = _ref("OutcomeObservation", "observation:independent")
    oracle = _ref("OutcomeOracle", "oracle:supplier-status@1")
    observation_profile = _ref(
        "ObservationProfile", "profile:supplier-outcome@1"
    )
    outcome_evidence = _ref("OutcomeEvidence", "evidence:oracle:SC-008")
    source = project_source_root(snapshot_ref, (intake_ref,))
    demand_root = project_demand_root(demand_ref, (change,))
    plan_root = project_plan_root(plan, plan_certificate)
    execution = project_execution_root(
        binding, bundle, execution_receipt, (execution_evidence,)
    )
    unresolved_refs: tuple[ResourceRef, ...] = ()
    if verdict is OutcomeVerdict.ACCEPT:
        dimensions = (
            OutcomeDimension(name="task_goal", verdict=DimensionVerdict.PASS),
            OutcomeDimension(name="forbidden_effects", verdict=DimensionVerdict.PASS),
        )
        reason_codes: tuple[str, ...] = ()
    elif verdict is OutcomeVerdict.UNKNOWN:
        dimensions = (
            OutcomeDimension(
                name="task_goal",
                verdict=DimensionVerdict.UNKNOWN,
                reasonCodes=("OUTCOME_OBSERVATION_UNRESOLVED",),
            ),
            OutcomeDimension(name="forbidden_effects", verdict=DimensionVerdict.PASS),
        )
        reason_codes = ("OUTCOME_OBSERVATION_UNRESOLVED",)
        unresolved_refs = (
            _ref("OutcomeObservation", "observation:required-but-unavailable"),
        )
    else:
        dimensions = (
            OutcomeDimension(name="task_goal", verdict=DimensionVerdict.PASS),
            OutcomeDimension(
                name="forbidden_effects",
                verdict=DimensionVerdict.FAIL,
                reasonCodes=("OUTCOME_FORBIDDEN_EFFECT_OBSERVED",),
            ),
        )
        reason_codes = ("OUTCOME_FORBIDDEN_EFFECT_OBSERVED",)
    certificate = OutcomeCertificate(
        metadata=ResourceMetadata(
            id=f"outcome:SC-008:{verdict.value}",
            namespace=NAMESPACE,
            revision=1,
            ownerRef="role:evidence-discovery",
            governanceRef=snapshot.metadata.governance_ref,
            createdAt=NOW,
            sourceRefs=(
                snapshot_ref.resource_id,
                intake_ref.resource_id,
                demand_ref.resource_id,
                change.resource_id,
                plan.resource_id,
                plan_certificate.resource_id,
                binding.resource_id,
                bundle.resource_id,
                execution_receipt.resource_id,
                execution_evidence.resource_id,
                observation.resource_id,
                oracle.resource_id,
                observation_profile.resource_id,
                outcome_evidence.resource_id,
                *(ref.resource_id for ref in unresolved_refs),
            ),
        ),
        spec=OutcomeCertificateSpec(
            sourceRoot=source,
            demandRoot=demand_root,
            planRoot=plan_root,
            executionRoot=execution,
            snapshotRef=snapshot_ref,
            sourceAdmissionReceiptRefs=(intake_ref,),
            demandRef=demand_ref,
            changeRefs=(change,),
            planRef=plan,
            planCertificateRef=plan_certificate,
            runtimeBindingRef=binding,
            runtimeBundleRef=bundle,
            executionReceiptRef=execution_receipt,
            executionEvidenceRefs=(execution_evidence,),
            observationRefs=(observation,),
            actingPrincipalRefs=(_ref("Principal", "principal:runtime-agent"),),
            oracleRef=oracle,
            oracleBuildDigest=_digest("oracle:supplier-status@1:build"),
            observationProfileRef=observation_profile,
            verdict=verdict,
            dimensions=dimensions,
            reasonCodes=reason_codes,
            evidenceRefs=(outcome_evidence,),
            unresolvedRefs=unresolved_refs,
        ),
    )
    return seal_resource(certificate)


def test_frozen_root_tck_vector_and_runtime_extension_boundary() -> None:
    vector = json.loads(
        (ROOT / "tck" / "fixtures" / "evolution" / "root-vector.json").read_bytes()
    )
    refs = {name: ResourceRef.model_validate(value) for name, value in vector["refs"].items()}
    roots = {
        "source": project_source_root(refs["snapshot"], (refs["sourceAdmission"],)),
        "demand": project_demand_root(refs["demand"], (refs["change"],)),
        "plan": project_plan_root(refs["plan"], refs["planCertificate"]),
        "execution": project_execution_root(
            refs["runtimeBinding"],
            refs["runtimeBundle"],
            refs["executionReceipt"],
            (refs["executionEvidence"],),
        ),
    }
    roots["outcome"] = project_outcome_root(
        roots["source"],
        roots["demand"],
        roots["plan"],
        roots["execution"],
        refs["outcomeCertificate"],
    )
    assert roots == vector["expectedRoots"]
    assert {
        "ExecutionReceipt",
        "EvolutionProposal",
        "OutcomeObservation",
        "ProcedureContract",
    }.isdisjoint(KIND_MODELS)


def test_lifecycle_root_projections_fail_closed_on_missing_or_ambiguous_material() -> None:
    snapshot = _ref("OrganizationSnapshot", "snapshot:root-controls")
    admission = _ref("SourceAdmissionReceipt", "receipt:root-controls")
    demand = _ref("OrganizationalDemand", "demand:root-controls")
    plan = _ref("OrganizationPlan", "plan:root-controls")
    certificate = _ref("PlanCertificate", "certificate:root-controls")
    binding = _ref("RuntimeBinding", "binding:root-controls")
    bundle = _ref("ZeroEffectRuntimeBundle", "bundle:root-controls")
    execution = _ref("ExecutionReceipt", "execution:root-controls")
    outcome = _ref("OutcomeCertificate", "outcome:root-controls")
    candidate = _ref("OrganizationSnapshot", "candidate:root-controls")
    replay = _ref("ReplayEvidence", "replay:root-controls")
    governance = _ref("GovernanceDecision", "governance:root-controls")

    controls = (
        (lambda: project_source_root(snapshot, ()), "EVOLUTION_ROOT_INCOMPLETE"),
        (lambda: project_demand_root(demand, ()), "EVOLUTION_ROOT_INCOMPLETE"),
        (
            lambda: project_execution_root(binding, bundle, execution, ()),
            "EVOLUTION_ROOT_INCOMPLETE",
        ),
        (
            lambda: project_outcome_root("not-a-digest", _digest("d"), _digest("p"), _digest("x"), outcome),
            "EVOLUTION_ROOT_MISMATCH",
        ),
        (
            lambda: project_plan_root(plan, _ref("PlanCertificate", "other", namespace="other")),
            "EVOLUTION_NAMESPACE_MISMATCH",
        ),
        (
            lambda: project_plan_root(_ref("WrongKind", "wrong"), certificate),
            "EVOLUTION_REF_KIND_MISMATCH",
        ),
        (
            lambda: project_evolution_root((), (outcome,), (outcome,), (replay,), governance),
            "EVOLUTION_ROOT_INCOMPLETE",
        ),
        (
            lambda: project_evolution_root(
                (candidate, candidate), (outcome,),
                (_ref("OutcomeCertificate", "counterexample"),), (replay,), governance
            ),
            "EVOLUTION_DUPLICATE_REF",
        ),
        (
            lambda: project_source_root(snapshot, (admission, admission)),
            "EVOLUTION_DUPLICATE_REF",
        ),
    )
    for operation, reason in controls:
        with pytest.raises(OACValidationError) as error:
            operation()
        assert error.value.reason_code == reason


def test_demand_and_source_admission_are_exact_and_candidate_only() -> None:
    snapshot = _snapshot()
    demand = _demand(snapshot)
    receipt = _intake_receipt(snapshot)
    verify_organizational_demand(demand, snapshot)
    verify_source_admission_receipt(receipt)

    forged = receipt.model_copy(
        update={
            "spec": receipt.spec.model_copy(
                update={"reviewer_refs": receipt.spec.proposer_refs}
            )
        }
    )
    forged = seal_resource(forged.model_copy(update={"digest": None}))
    with pytest.raises(OACValidationError) as error:
        verify_source_admission_receipt(forged)
    assert error.value.reason_code == "EVOLUTION_SELF_ADMISSION_FORBIDDEN"


def test_demand_admission_and_outcome_semantic_controls_are_independent() -> None:
    snapshot = _snapshot()
    demand = _demand(snapshot)
    intake = _intake_receipt(snapshot)

    demand_controls = (
        (
            demand.model_copy(
                update={"spec": demand.spec.model_copy(update={"snapshot_ref": _ref("OrganizationSnapshot", "forged")})}
            ),
            "DEMAND_ROOT_MISMATCH",
        ),
        (
            demand.model_copy(
                update={"spec": demand.spec.model_copy(update={"requester_principal_ref": "principal:absent"})}
            ),
            "DEMAND_AUTHORITY_UNRESOLVED",
        ),
        (
            demand.model_copy(
                update={"metadata": demand.metadata.model_copy(update={"source_refs": ("forged",)})}
            ),
            "EVOLUTION_PROVENANCE_MISMATCH",
        ),
    )
    for candidate, reason in demand_controls:
        candidate = seal_resource(candidate.model_copy(update={"digest": None}))
        with pytest.raises(OACValidationError) as error:
            verify_organizational_demand(candidate, snapshot)
        assert error.value.reason_code == reason

    unrelated = _ref("OrganizationSnapshot", "snapshot:unrelated")
    admission_controls = (
        (
            intake.spec.model_copy(update={"admitted_subject_refs": (unrelated,)}),
            "SOURCE_ADMISSION_SUBJECT_MISMATCH",
        ),
        (
            intake.spec.model_copy(update={"admitted_subject_refs": ()}),
            "SOURCE_ADMISSION_VERDICT_INVALID",
        ),
        (
            intake.spec.model_copy(
                update={
                    "verdict": AdmissionVerdict.REJECTED,
                    "admitted_subject_refs": (),
                    "reason_codes": (),
                }
            ),
            "SOURCE_ADMISSION_VERDICT_INVALID",
        ),
        (
            intake.spec.model_copy(
                update={
                    "verdict": AdmissionVerdict.UNKNOWN,
                    "admitted_subject_refs": (),
                    "reason_codes": (),
                }
            ),
            "SOURCE_ADMISSION_VERDICT_INVALID",
        ),
        (
            intake.spec.model_copy(update={"reason_codes": ("Z_REASON", "A_REASON")}),
            "EVOLUTION_DUPLICATE_REF",
        ),
        (
            intake.spec.model_copy(update={"predecessor_source_root": _digest("unexpected")}),
            "SUCCESSOR_LINEAGE_INCOMPLETE",
        ),
    )
    for spec, reason in admission_controls:
        candidate = seal_resource(
            intake.model_copy(update={"spec": spec, "digest": None})
        )
        with pytest.raises(OACValidationError) as error:
            verify_source_admission_receipt(candidate)
        assert error.value.reason_code == reason

    accepted = _outcome_certificate(
        snapshot, intake, demand, verdict=OutcomeVerdict.ACCEPT
    )
    verify_outcome_certificate(accepted)
    rejected = _outcome_certificate(snapshot, intake, demand)
    outcome_controls = (
        (
            rejected.spec.model_copy(
                update={"oracle_ref": rejected.spec.execution_receipt_ref}
            ),
            "OUTCOME_SELF_CERTIFICATION_FORBIDDEN",
        ),
        (
            rejected.spec.model_copy(
                update={"dimensions": (rejected.spec.dimensions[0], rejected.spec.dimensions[0])}
            ),
            "OUTCOME_VERDICT_INVALID",
        ),
        (
            accepted.spec.model_copy(update={"reason_codes": ("UNEXPECTED",)}),
            "OUTCOME_VERDICT_INVALID",
        ),
    )
    for spec, reason in outcome_controls:
        candidate = seal_resource(
            rejected.model_copy(update={"spec": spec, "digest": None})
        )
        with pytest.raises(OACValidationError) as error:
            verify_outcome_certificate(candidate)
        assert error.value.reason_code == reason


def test_completed_execution_can_have_independently_rejected_outcome() -> None:
    snapshot = _snapshot()
    intake = _intake_receipt(snapshot)
    demand = _demand(snapshot)
    certificate = _outcome_certificate(snapshot, intake, demand)
    assert certificate.spec.execution_receipt_ref.resource_id.endswith("COMPLETED")
    assert certificate.spec.verdict is OutcomeVerdict.REJECT
    verify_outcome_certificate(certificate)
    unknown = _outcome_certificate(
        snapshot, intake, demand, verdict=OutcomeVerdict.UNKNOWN
    )
    verify_outcome_certificate(unknown)
    assert unknown.spec.verdict is OutcomeVerdict.UNKNOWN

    forged = certificate.model_copy(
        update={"spec": certificate.spec.model_copy(update={"source_root": _digest("forged")})}
    )
    forged = seal_resource(forged.model_copy(update={"digest": None}))
    with pytest.raises(OACValidationError) as error:
        verify_outcome_certificate(forged)
    assert error.value.reason_code == "EVOLUTION_ROOT_MISMATCH"

    actor = certificate.spec.acting_principal_refs[0]
    self_certified = certificate.model_copy(
        update={"spec": certificate.spec.model_copy(update={"oracle_ref": actor})}
    )
    self_certified = seal_resource(self_certified.model_copy(update={"digest": None}))
    with pytest.raises(OACValidationError) as error:
        verify_outcome_certificate(self_certified)
    assert error.value.reason_code == "OUTCOME_SELF_CERTIFICATION_FORBIDDEN"


def test_public_cli_runs_semantic_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = _snapshot()
    certificate = _outcome_certificate(
        snapshot, _intake_receipt(snapshot), _demand(snapshot)
    )
    path = tmp_path / "outcome.json"
    path.write_text(certificate.model_dump_json(by_alias=True), encoding="utf-8")
    assert main(["validate-evolution", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "kind": "OutcomeCertificate",
        "valid": True,
    }
    assert main(["registry", "evolution-semantic-validation-rules"]) == 0
    registry = json.loads(capsys.readouterr().out)
    assert registry["profile"] == "oac.evolution.minimum/v0.1"
    assert {rule["ruleId"] for rule in registry["rules"]} == {
        "OAC-EVO-001",
        "OAC-EVO-002",
        "OAC-EVO-003",
        "OAC-EVO-004",
    }


def test_governed_successor_binds_counterexample_and_predecessor() -> None:
    snapshot = _snapshot()
    intake = _intake_receipt(snapshot)
    demand = _demand(snapshot)
    accepted = _outcome_certificate(
        snapshot, intake, demand, verdict=OutcomeVerdict.ACCEPT
    )
    rejected = _outcome_certificate(snapshot, intake, demand)
    predecessor = project_source_root(resource_ref(snapshot), (resource_ref(intake),))
    candidate = _ref("OrganizationSnapshot", "snapshot:veracier-proc01@successor-candidate")
    replay = _ref("ReplayEvidence", "replay:supplier-pattern@1")
    governance = _ref("GovernanceDecision", "decision:promote-supplier-pattern@1")
    evolution_profile = _ref("IntakeProfile", "profile:evolution@1")
    evolution = project_evolution_root(
        (candidate,),
        (resource_ref(accepted),),
        (resource_ref(rejected),),
        (replay,),
        governance,
    )
    proposer = _ref("Principal", "principal:evolution-agent")
    authority = _ref("Principal", "principal:governance-owner")
    reviewer = _ref("Principal", "principal:qualified-reviewer")
    receipt = seal_resource(
        SourceAdmissionReceipt(
            metadata=ResourceMetadata(
                id="receipt:successor-promotion-1",
                namespace=NAMESPACE,
                revision=1,
                ownerRef="role:evidence-discovery",
                governanceRef=snapshot.metadata.governance_ref,
                createdAt=NOW,
                sourceRefs=(
                    candidate.resource_id,
                    evolution_profile.resource_id,
                    authority.resource_id,
                ),
            ),
            spec=SourceAdmissionReceiptSpec(
                admissionPurpose="successor_promotion",
                subjectRefs=(candidate,),
                intakeManifestDigest=_digest("successor-candidate-manifest"),
                intakeProfileRef=evolution_profile,
                ruleSetDigest=_digest("evolution-admission-rules"),
                proposerRefs=(proposer,),
                decisionAuthorityRef=authority,
                reviewerRefs=(reviewer,),
                verdict=AdmissionVerdict.ADMITTED,
                admittedSubjectRefs=(candidate,),
                predecessorSourceRoot=predecessor,
                evolutionRoot=evolution,
                candidateRef=candidate,
                governanceDecisionRef=governance,
            ),
        )
    )
    assert verify_successor_admission(
        receipt,
        predecessor_source_root=predecessor,
        candidate_refs=(candidate,),
        supporting_outcome_refs=(resource_ref(accepted),),
        counterexample_outcome_refs=(resource_ref(rejected),),
        replay_evidence_refs=(replay,),
        governance_decision_ref=governance,
    ) == evolution

    drifted = receipt.model_copy(
        update={
            "spec": receipt.spec.model_copy(
                update={"predecessor_source_root": _digest("another-source-root")}
            )
        }
    )
    drifted = seal_resource(drifted.model_copy(update={"digest": None}))
    with pytest.raises(OACValidationError) as error:
        verify_successor_admission(
            drifted,
            predecessor_source_root=predecessor,
            candidate_refs=(candidate,),
            supporting_outcome_refs=(resource_ref(accepted),),
            counterexample_outcome_refs=(resource_ref(rejected),),
            replay_evidence_refs=(replay,),
            governance_decision_ref=governance,
        )
    assert error.value.reason_code == "SUCCESSOR_PREDECESSOR_MISMATCH"
