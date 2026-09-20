"""Independent OAC wire construction and evidence helpers for Spec 039."""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import rfc8785

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.evolution_contracts import (
    EVOLUTION_SOURCE_CHANGED_PATHS,
    EvolutionProofArtifact,
    EvolutionProposal,
    ExecutionEvidence,
    GovernanceDecision,
    LocalOutcomeDecision,
    OACExecutionApproval,
    OACExecutionReceipt,
    OutcomeObservation,
    OutcomeVerdict,
    PreparedEvolutionSuccessors,
    ProcedureContractCandidate,
)
from orgrebase.workspace.evolution_runtime import program_policy_digest
from orgrebase.workspace.oac_wire import (
    OACBlackBoxCLI,
    _jcs_digest,
    _oac_projection,
    _resource_ref,
)
from orgrebase.workspace.outcome_assurance import oracle_policy_digest
from orgrebase.workspace.profile_contracts import EnterpriseSeedProfile
from orgrebase.workspace.reference_profiles import (
    VERACIER_SC008_DEMAND_REF,
    VERACIER_SC008_SUBJECT_REF,
    supplier_sc008_source_aligned_profile,
    supplier_shadow_intake_profile,
)
from orgrebase.workspace.source_admission import (
    EnterpriseSeedProfileMigrationReceipt,
    EnterpriseSeedSourceAdmissionReceipt,
    admit_enterprise_seed_sources,
    admit_veracier_sc008_profile_migration,
)

NAMESPACE = "oac.examples.supplier"
CREATED_AT = "2026-08-26T00:00:00Z"


@dataclass(frozen=True, slots=True)
class AdmittedLifecycleRoots:
    profile_digest: str
    source_receipt: EnterpriseSeedSourceAdmissionReceipt
    migration_receipt: EnterpriseSeedProfileMigrationReceipt
    oac_source_admission: dict[str, Any]
    demand: dict[str, Any]


def _dict(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IntegrityError(code)
    return cast(dict[str, Any], value)


def _ref(kind: str, resource_id: str, digest: str, *, revision: int = 1) -> dict[str, Any]:
    return {
        "apiVersion": "oac.dev/v0alpha1",
        "kind": kind,
        "namespace": NAMESPACE,
        "resourceId": resource_id,
        "revision": revision,
        "digest": digest,
    }


def _identity_digest(kind: str, resource_id: str, value: object | None = None) -> str:
    return sha256_digest({"kind": kind, "resource_id": resource_id, "value": value})


def _seal(resource: dict[str, Any]) -> dict[str, Any]:
    value = dict(resource)
    value["digest"] = _jcs_digest(_oac_projection(value))
    return value


def prepare_evolution_successors(
    *,
    profile: EnterpriseSeedProfile,
    predecessor_snapshot: dict[str, Any],
    predecessor_source_ref: str,
    predecessor_source_digest: str,
    candidate: ProcedureContractCandidate,
    published_at: str,
) -> PreparedEvolutionSuccessors:
    profile = EnterpriseSeedProfile.model_validate(profile.model_dump(mode="json"))
    candidate = candidate.revalidated()
    profile_payload = profile.model_dump(mode="json", exclude={"digest"})
    profile_payload.update(revision="r3", requested_growth_level="G1_SELECT")
    profile_payload["declared_limitation_codes"] = [
        code for code in profile_payload["declared_limitation_codes"] if code != "INTAKE_ONLY"
    ]
    compatibility = cast(dict[str, Any], profile_payload["runtime_compatibility"])
    compatibility.update(
        mode="REFERENCE_HANDLER",
        handler_profile=f"{candidate.candidate_id}@{candidate.digest}",
    )
    evolved_profile = EnterpriseSeedProfile.model_validate(profile_payload)
    snapshot_payload = deepcopy(predecessor_snapshot)
    metadata = cast(dict[str, Any], snapshot_payload["metadata"])
    predecessor_sources = tuple(cast(list[str], metadata["sourceRefs"]))
    metadata.update(revision=int(metadata["revision"]) + 1, createdAt=published_at)
    metadata["sourceRefs"] = [
        *predecessor_sources,
        f"{candidate.candidate_id}@{candidate.digest}",
    ]
    spec = cast(dict[str, Any], snapshot_payload["spec"])
    spec["nodes"] = [
        *cast(list[dict[str, Any]], spec["nodes"]),
        {
            "nodeId": candidate.candidate_id,
            "nodeType": "procedure-contract",
            "domainRef": "domain:quality",
            "ownerRoleRef": "role:quality-qualification",
            "admissionStatus": "admitted",
        },
    ]
    completeness = cast(dict[str, Any], spec["completeness"])
    completeness["coveredNodeRefs"] = [
        *cast(list[str], completeness["coveredNodeRefs"]),
        candidate.candidate_id,
    ]
    snapshot_payload = _seal(snapshot_payload)
    source_delta = {
        "revision": "r2",
        "predecessor_ref": predecessor_source_ref,
        "predecessor_digest": predecessor_source_digest,
        "profile_ref": evolved_profile.ref,
        "profile_digest": evolved_profile.digest,
        "snapshot_ref": f"{metadata['id']}@{metadata['revision']}",
        "snapshot_digest": snapshot_payload["digest"],
        "admitted_at": published_at,
    }
    return PreparedEvolutionSuccessors(
        predecessor_source_refs=predecessor_sources,
        candidate_ref=candidate.candidate_id,
        candidate_digest=candidate.digest,
        profile_payload=evolved_profile,
        snapshot_payload_jcs=rfc8785.dumps(snapshot_payload).decode("utf-8"),
        source_changed_paths=EVOLUTION_SOURCE_CHANGED_PATHS,
        source_delta_digest=sha256_digest(source_delta),
        published_at=published_at,
    )


def _validate_evolution(
    cli: OACBlackBoxCLI,
    resource: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any] | None = None,
) -> None:
    with tempfile.TemporaryDirectory(prefix="orgrebase-evolution-wire-") as raw:
        root = Path(raw)
        resource_path = root / "resource.json"
        resource_path.write_bytes(rfc8785.dumps(dict(resource)) + b"\n")
        arguments = ["validate-evolution", str(resource_path)]
        if snapshot is not None:
            snapshot_path = root / "snapshot.json"
            snapshot_path.write_bytes(rfc8785.dumps(dict(snapshot)) + b"\n")
            arguments.extend(("--snapshot", str(snapshot_path)))
        cli.run(*arguments)


def _subject_ref(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    spec = _dict(snapshot.get("spec"), "EVOLUTION_SNAPSHOT_SPEC_INVALID")
    nodes = spec.get("nodes")
    if not isinstance(nodes, list):
        raise IntegrityError("EVOLUTION_SNAPSHOT_NODES_INVALID")
    node = next(
        (
            item
            for item in nodes
            if isinstance(item, dict) and item.get("nodeId") == VERACIER_SC008_SUBJECT_REF
        ),
        None,
    )
    if node is None:
        raise IntegrityError("EVOLUTION_SC008_SUBJECT_NOT_IN_SNAPSHOT")
    return _ref(
        "OrganizationSubject",
        VERACIER_SC008_SUBJECT_REF,
        _identity_digest("OrganizationSubject", VERACIER_SC008_SUBJECT_REF, node),
    )


def build_initial_lifecycle_roots(
    cli: OACBlackBoxCLI,
    snapshot: Mapping[str, Any],
    change: Mapping[str, Any],
) -> AdmittedLifecycleRoots:
    historical = supplier_shadow_intake_profile()
    profile = supplier_sc008_source_aligned_profile()
    migration = admit_veracier_sc008_profile_migration(historical, profile)
    source_receipt = admit_enterprise_seed_sources(profile)
    snapshot_ref = _resource_ref(snapshot)
    profile_ref = _ref("EnterpriseSeedProfile", profile.profile_id, profile.digest, revision=2)
    proposer = _ref(
        "Principal",
        "principal:procurement-agent",
        _identity_digest("Principal", "principal:procurement-agent"),
    )
    if len(profile.governance.admission_authority_refs) != 1:
        raise IntegrityError("EVOLUTION_SOURCE_AUTHORITY_NOT_UNIQUE")
    authority_id = profile.governance.admission_authority_refs[0]
    authority = _ref(
        "Principal",
        authority_id,
        _identity_digest("Principal", authority_id),
    )
    reviewer = _ref(
        "Principal",
        "principal:quality-agent",
        _identity_digest("Principal", "principal:quality-agent"),
    )
    snapshot_metadata = _dict(snapshot.get("metadata"), "EVOLUTION_SNAPSHOT_METADATA_INVALID")
    source = _seal(
        {
            "apiVersion": "oac.dev/v0alpha1",
            "kind": "SourceAdmissionReceipt",
            "metadata": {
                "id": "source-admission:veracier-sc008-r2",
                "namespace": NAMESPACE,
                "revision": 1,
                "ownerRef": snapshot_metadata["ownerRef"],
                "governanceRef": snapshot_metadata["governanceRef"],
                "createdAt": CREATED_AT,
                "effectiveFrom": None,
                "effectiveTo": None,
                "sourceRefs": [
                    snapshot_ref["resourceId"],
                    profile_ref["resourceId"],
                    authority["resourceId"],
                ],
            },
            "spec": {
                "admissionPurpose": "enterprise_intake",
                "subjectRefs": [snapshot_ref],
                "intakeManifestDigest": source_receipt.digest,
                "intakeProfileRef": profile_ref,
                "ruleSetDigest": migration.digest,
                "proposerRefs": [proposer],
                "decisionAuthorityRef": authority,
                "reviewerRefs": [reviewer],
                "verdict": "ADMITTED",
                "reasonCodes": [
                    "EXACT_SOURCE_BYTES_ADMITTED",
                    "ROOT_IDENTITY_ALIGNMENT_VERIFIED",
                ],
                "unresolvedRefs": [],
                "admittedSubjectRefs": [snapshot_ref],
                "predecessorSourceRoot": None,
                "evolutionRoot": None,
                "candidateRef": None,
                "governanceDecisionRef": None,
            },
        }
    )
    _validate_evolution(cli, source)
    subject = _subject_ref(snapshot)
    desired = _ref(
        "OutcomeCriterion",
        "criterion:sc008-controlled-process-complete",
        _identity_digest(
            "OutcomeCriterion",
            "criterion:sc008-controlled-process-complete",
            "synthetic_process_assurance",
        ),
    )
    evidence_obligations = [
        _ref(
            "EvidenceObligation",
            f"evidence-obligation:{name}",
            _identity_digest("EvidenceObligation", name),
        )
        for name in (
            "approved-qualification-record",
            "commercial-switch-review",
            "impact-assessment",
            "selected-continuity-option",
        )
    ]
    constraint = _ref(
        "EffectConstraint",
        "constraint:zero-effect",
        _identity_digest("EffectConstraint", "constraint:zero-effect", 0),
    )
    demand = _seal(
        {
            "apiVersion": "oac.dev/v0alpha1",
            "kind": "OrganizationalDemand",
            "metadata": {
                "id": VERACIER_SC008_DEMAND_REF,
                "namespace": NAMESPACE,
                "revision": 1,
                "ownerRef": "role:procurement-owner",
                "governanceRef": snapshot_metadata["governanceRef"],
                "createdAt": CREATED_AT,
                "effectiveFrom": None,
                "effectiveTo": None,
                "sourceRefs": [
                    snapshot_ref["resourceId"],
                    subject["resourceId"],
                    _resource_ref(change)["resourceId"],
                ],
            },
            "spec": {
                "snapshotRef": snapshot_ref,
                "requesterPrincipalRef": "principal:procurement-agent",
                "accountableRoleRef": "role:procurement-owner",
                "objective": "objective:assure-sc008-zero-effect-process",
                "subjectRefs": [subject],
                "triggerRefs": [_resource_ref(change)],
                "desiredOutcomeRefs": [desired],
                "evidenceObligationRefs": evidence_obligations,
                "constraintRefs": [constraint],
                "priority": 1,
                "effectCeiling": "zero_effect",
            },
        }
    )
    _validate_evolution(cli, demand, snapshot=snapshot)
    return AdmittedLifecycleRoots(
        profile_digest=profile.digest,
        source_receipt=source_receipt,
        migration_receipt=migration,
        oac_source_admission=source,
        demand=demand,
    )


def build_execution_approval(
    *,
    command_id: str,
    capsule_digest: str,
    runtime_admission_digest: str,
    source_admission: Mapping[str, Any],
    demand: Mapping[str, Any],
    runtime_bundle_digest: str,
    runtime_owner_id: str,
    approved_at: str = "2026-08-26T00:00:30Z",
) -> OACExecutionApproval:
    demand_metadata = _dict(demand.get("metadata"), "EVOLUTION_DEMAND_METADATA_INVALID")
    source_metadata = _dict(
        source_admission.get("metadata"),
        "EVOLUTION_SOURCE_ADMISSION_METADATA_INVALID",
    )
    return OACExecutionApproval(
        approval_id=f"oac-execution-approval:{command_id}",
        command_id=command_id,
        capsule_digest=capsule_digest,
        runtime_admission_digest=runtime_admission_digest,
        source_admission_ref=str(source_metadata["id"]),
        source_admission_digest=str(source_admission["digest"]),
        demand_ref=str(demand_metadata["id"]),
        demand_digest=str(demand["digest"]),
        runtime_bundle_digest=runtime_bundle_digest,
        program_policy_digest=program_policy_digest(),
        runtime_owner_id=runtime_owner_id,
        approved_at=approved_at,
    )


def _domain_root(domain: str, sections: Sequence[tuple[str, object]]) -> str:
    return "sha256:" + hashlib.sha256(rfc8785.dumps({"domain": domain, **dict(sections)})).hexdigest()


def build_promotion_source_admission(
    *,
    candidate: ProcedureContractCandidate,
    governance_profile: EnterpriseSeedProfile,
    successor_profile: EnterpriseSeedProfile,
    snapshot_payload: Mapping[str, Any],
    proposal: EvolutionProposal,
    decision: GovernanceDecision,
    support: tuple[tuple[str, str], ...],
    counterexamples: tuple[tuple[str, str], ...],
    replay_evidence: EvolutionProofArtifact,
    predecessor_source_digest: str,
    created_at: str,
) -> dict[str, Any]:
    candidate_ref = _ref("ProcedureContractCandidate", candidate.candidate_id, candidate.digest)
    profile_ref = _ref(
        "EnterpriseSeedProfile",
        successor_profile.profile_id,
        successor_profile.digest,
        revision=3,
    )
    snapshot_metadata = _dict(
        snapshot_payload.get("metadata"),
        "EVOLUTION_SNAPSHOT_METADATA_INVALID",
    )
    snapshot_ref = _ref(
        "OrganizationSnapshot",
        proposal.predecessor_snapshot_ref,
        str(snapshot_payload["digest"]),
        revision=int(snapshot_metadata["revision"]),
    )
    decision_ref = _ref("GovernanceDecision", decision.decision_id, decision.digest)
    evolution_root = _domain_root(
        "oac.root/evolution/v0.1",
        (
            ("candidates", [candidate_ref]),
            ("supportingOutcomes", [_ref("OutcomeCertificate", *item) for item in support]),
            (
                "counterexampleOutcomes",
                [_ref("OutcomeCertificate", *item) for item in counterexamples],
            ),
            (
                "replayEvidence",
                [_ref("ReplayEvidence", replay_evidence.artifact_id, replay_evidence.digest)],
            ),
            ("governanceDecision", [decision_ref]),
        ),
    )
    proposer_ref = _ref(
        "Principal",
        proposal.proposal_author_id,
        _identity_digest("Principal", proposal.proposal_author_id),
    )
    authority_ref = _ref(
        "Principal",
        decision.actor_id,
        _identity_digest("Principal", decision.actor_id),
    )
    reviewer_id = next(
        (
            item
            for item in governance_profile.governance.owner_refs
            if item not in {decision.actor_id, proposal.proposal_author_id}
        ),
        "human:veracier-risk-owner",
    )
    reviewer_ref = _ref("Principal", reviewer_id, _identity_digest("Principal", reviewer_id))
    intake_profile_ref = _ref(
        "EvolutionAdmissionProfile",
        "profile:orgrebase-controlled-procedure-evolution-v1",
        sha256_digest("orgrebase-controlled-procedure-evolution-v1"),
    )
    subjects = [candidate_ref, profile_ref, snapshot_ref]
    return _seal(
        {
            "apiVersion": "oac.dev/v0alpha1",
            "kind": "SourceAdmissionReceipt",
            "metadata": {
                "id": "source-admission:veracier-procedure-r2",
                "namespace": NAMESPACE,
                "revision": 1,
                "ownerRef": decision.actor_authority_ref,
                "governanceRef": "policy:oac-shadow-zero-effect",
                "createdAt": created_at,
                "effectiveFrom": None,
                "effectiveTo": None,
                "sourceRefs": [
                    candidate.candidate_id,
                    successor_profile.profile_id,
                    proposal.predecessor_snapshot_ref,
                    intake_profile_ref["resourceId"],
                    decision.actor_id,
                ],
            },
            "spec": {
                "admissionPurpose": "successor_promotion",
                "subjectRefs": subjects,
                "intakeManifestDigest": proposal.digest,
                "intakeProfileRef": intake_profile_ref,
                "ruleSetDigest": proposal.regression_suite_digest,
                "proposerRefs": [proposer_ref],
                "decisionAuthorityRef": authority_ref,
                "reviewerRefs": [reviewer_ref],
                "verdict": "ADMITTED",
                "reasonCodes": ["COUNTEREXAMPLE_RETAINED", "EXACT_GOVERNANCE_DECISION_BOUND"],
                "unresolvedRefs": [],
                "admittedSubjectRefs": subjects,
                "predecessorSourceRoot": predecessor_source_digest,
                "evolutionRoot": evolution_root,
                "candidateRef": candidate_ref,
                "governanceDecisionRef": decision_ref,
            },
        }
    )


def _root_refs(refs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(item) for item in refs]


def build_portable_outcome_certificate(
    cli: OACBlackBoxCLI,
    *,
    capsule: Mapping[str, Any],
    roots: AdmittedLifecycleRoots,
    execution: OACExecutionReceipt,
    evidence: tuple[ExecutionEvidence, ...],
    observation: OutcomeObservation,
    decision: LocalOutcomeDecision,
) -> dict[str, Any]:
    snapshot = _dict(capsule["snapshot"], "EVOLUTION_CAPSULE_SNAPSHOT_INVALID")
    change = _dict(capsule["change"], "EVOLUTION_CAPSULE_CHANGE_INVALID")
    plan = _dict(capsule["plan"], "EVOLUTION_CAPSULE_PLAN_INVALID")
    plan_certificate = _dict(capsule["plan_certificate"], "EVOLUTION_CAPSULE_CERTIFICATE_INVALID")
    binding = _dict(capsule["runtime_binding"], "EVOLUTION_CAPSULE_BINDING_INVALID")
    bundle = _dict(capsule["runtime_bundle"], "EVOLUTION_CAPSULE_BUNDLE_INVALID")
    source_ref = _resource_ref(roots.oac_source_admission)
    demand_ref = _resource_ref(roots.demand)
    if (
        execution.source_admission_ref != source_ref["resourceId"]
        or execution.source_admission_digest != source_ref["digest"]
        or execution.demand_ref != demand_ref["resourceId"]
        or execution.demand_digest != demand_ref["digest"]
    ):
        raise IntegrityError("EVOLUTION_EXECUTION_LIFECYCLE_ROOT_MISMATCH")
    execution_ref = _ref("ExecutionReceipt", execution.receipt_id, execution.digest)
    evidence_by_id = {item.evidence_id: item for item in evidence}
    evidence_refs = [
        _ref("ExecutionEvidence", item.evidence_id, item.digest)
        for item in sorted(evidence, key=lambda value: value.evidence_id.encode())
    ]
    source_root = _domain_root(
        "oac.root/source/v0.1",
        (
            ("snapshot", [_resource_ref(snapshot)]),
            ("sourceAdmissionReceipts", [source_ref]),
        ),
    )
    demand_root = _domain_root(
        "oac.root/demand/v0.1",
        (("demand", [demand_ref]), ("changes", [_resource_ref(change)])),
    )
    plan_root = _domain_root(
        "oac.root/plan/v0.1",
        (("plan", [_resource_ref(plan)]), ("planCertificate", [_resource_ref(plan_certificate)])),
    )
    execution_root = _domain_root(
        "oac.state/execution-evidence/v0.1",
        (
            ("runtimeBinding", [_resource_ref(binding)]),
            ("runtimeBundle", [_resource_ref(bundle)]),
            ("executionReceipt", [execution_ref]),
            ("executionEvidence", evidence_refs),
        ),
    )
    runtime_principals = sorted(
        {str(item["principalRef"]) for item in binding["spec"]["roleBindings"]},
        key=str.encode,
    )
    acting_refs = [
        _ref("Principal", principal, _identity_digest("Principal", principal))
        for principal in runtime_principals
    ]
    observation_ref = _ref("OutcomeObservation", observation.observation_id, observation.digest)
    decision_ref = _ref("OutcomeEvidence", decision.decision_id, decision.digest)
    dimension_values = []
    for dimension in decision.dimensions:
        bound_refs = []
        for ref in dimension.evidence_refs:
            if ref == execution.receipt_id:
                bound_refs.append(execution_ref)
            elif ref == observation.observation_id:
                bound_refs.append(observation_ref)
            else:
                item = evidence_by_id.get(ref)
                if item is None:
                    raise IntegrityError("EVOLUTION_OUTCOME_EVIDENCE_REF_UNKNOWN")
                bound_refs.append(_ref("ExecutionEvidence", ref, item.digest))
        dimension_values.append(
            {
                "name": dimension.name,
                "verdict": dimension.verdict,
                "reasonCodes": list(dimension.reason_codes),
                "evidenceRefs": bound_refs,
                "unresolvedRefs": [],
            }
        )
    oracle_digest = oracle_policy_digest()
    outcome_source_refs = [
        snapshot["metadata"]["id"],
        source_ref["resourceId"],
        demand_ref["resourceId"],
        change["metadata"]["id"],
        plan["metadata"]["id"],
        plan_certificate["metadata"]["id"],
        binding["metadata"]["id"],
        bundle["metadata"]["id"],
        execution_ref["resourceId"],
        *(ref["resourceId"] for ref in evidence_refs),
        observation_ref["resourceId"],
        "oracle:veracier-sc008-controlled-process",
        observation.observation_profile,
        observation_ref["resourceId"],
        decision_ref["resourceId"],
    ]
    outcome = _seal(
        {
            "apiVersion": "oac.dev/v0alpha1",
            "kind": "OutcomeCertificate",
            "metadata": {
                "id": f"outcome-certificate:{execution.run_id}",
                "namespace": NAMESPACE,
                "revision": 1,
                "ownerRef": "authority:veracier-controlled-outcome-assurance",
                "governanceRef": snapshot["metadata"]["governanceRef"],
                "createdAt": decision.issued_at,
                "effectiveFrom": None,
                "effectiveTo": None,
                "sourceRefs": outcome_source_refs,
            },
            "spec": {
                "sourceRoot": source_root,
                "demandRoot": demand_root,
                "planRoot": plan_root,
                "executionRoot": execution_root,
                "snapshotRef": _resource_ref(snapshot),
                "sourceAdmissionReceiptRefs": [source_ref],
                "demandRef": demand_ref,
                "changeRefs": [_resource_ref(change)],
                "planRef": _resource_ref(plan),
                "planCertificateRef": _resource_ref(plan_certificate),
                "runtimeBindingRef": _resource_ref(binding),
                "runtimeBundleRef": _resource_ref(bundle),
                "executionReceiptRef": execution_ref,
                "executionEvidenceRefs": evidence_refs,
                "observationRefs": [observation_ref],
                "actingPrincipalRefs": acting_refs,
                "oracleRef": _ref("OutcomeOracle", "oracle:veracier-sc008-controlled-process", oracle_digest),
                "oracleBuildDigest": oracle_digest,
                "observationProfileRef": _ref(
                    "ObservationProfile", observation.observation_profile, oracle_digest
                ),
                "verdict": decision.verdict.value,
                "dimensions": dimension_values,
                "reasonCodes": []
                if decision.verdict is OutcomeVerdict.ACCEPT
                else list(decision.reason_codes),
                "evidenceRefs": [observation_ref, decision_ref],
                "unresolvedRefs": [],
            },
        }
    )
    _validate_evolution(cli, outcome)
    return outcome
