"""Portable evidence for the existing disposable lab; no tool execution or admission service.

The caller supplies trusted host pins. All referenced extension payloads are
retained and rebuilt from that context before the public OAC verifier is called.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.evolution_evidence import _domain_root
from orgrebase.workspace.oac_wire import _resource_ref, _verify_oac_resource
from orgrebase.workspace.outcome_lab import (
    DIMENSIONS,
    OACPlanGate,
    OutcomeOracle,
    OutcomeTaskMapping,
    verify_outcome_receipt,
)
from orgrebase.workspace.outcome_runtime import (
    build_runtime_bundle,
)
from orgrebase.workspace.outcome_runtime import (
    profile_binding as _profile,
)
from orgrebase.workspace.outcome_runtime import (
    resource as _resource,
)

_SCOPE = "CONTROLLED_LOCAL_SCRIPT_ADMISSION"
_OUTCOME_SCOPE = "DISPOSABLE_LOCAL_STATE_EXPERIMENT"



def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False))


def _pack(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "digest": sha256_digest(body)}


def _require_digest(value: Mapping[str, Any], expected: str, code: str) -> None:
    body = {key: item for key, item in value.items() if key != "digest"}
    if value.get("digest") != expected or sha256_digest(body) != expected:
        raise IntegrityError(code)


@dataclass(frozen=True, slots=True)
class LabOutcomeTrust:
    """Pins captured by the trusted host, never recovered from an untrusted bundle.

    receipt_digest and approval_digest identify the actual controller run whose
    one-use token was consumed. They are not signatures or external IAM claims.
    """

    mapping: OutcomeTaskMapping
    oracle: OutcomeOracle
    controller_authority: str
    acting_authority: str
    lifecycle_digest: str
    receipt_digest: str
    approval_digest: str
    oracle_build_digest: str
    recorded_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        for digest in (
            self.lifecycle_digest,
            self.receipt_digest,
            self.approval_digest,
            self.oracle_build_digest,
        ):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise IntegrityError("LAB_PORTABLE_TRUST_PIN_INVALID")
        if not isinstance(self.recorded_at, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", self.recorded_at
        ):
            raise IntegrityError("LAB_PORTABLE_RECORDING_TIME_INVALID")
        try:
            datetime.fromisoformat(self.recorded_at)
        except ValueError as exc:
            raise IntegrityError("LAB_PORTABLE_RECORDING_TIME_INVALID") from exc


def _authorities(mapping: OutcomeTaskMapping, oracle: OutcomeOracle, controller: str, actor: str) -> None:
    if (
        oracle.digest != mapping.oracle_digest
        or len({controller, actor, oracle.authority}) != 3
        or not all((controller, actor, oracle.authority))
    ):
        raise IntegrityError("LAB_PORTABLE_AUTHORITY_CONTEXT_INVALID")


def _requester(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    roles = {
        role["roleId"]: role
        for role in snapshot["spec"]["roleDefinitions"]
        if role["admissionStatus"] == "admitted"
    }
    candidates = []
    for principal in snapshot["spec"]["principals"]:
        if principal["status"] != "active" or principal["admissionStatus"] != "admitted":
            continue
        for role_id in principal["eligibleRoleRefs"]:
            role = roles.get(role_id)
            if role is not None and set(role["requiredQualifications"]) <= set(
                principal["qualificationRefs"]
            ):
                candidates.append(
                    (
                        role_id != snapshot["metadata"]["ownerRef"],
                        role_id,
                        principal["principalId"],
                        principal,
                        role,
                    )
                )
    if not candidates:
        raise IntegrityError("LAB_PORTABLE_QUALIFIED_REQUESTER_REQUIRED")
    _, _, _, principal, role = min(candidates, key=lambda row: row[:3])
    return principal, role


def build_lab_lifecycle(
    gate: OACPlanGate,
    mapping: OutcomeTaskMapping,
    oracle: OutcomeOracle,
    snapshot: Mapping[str, Any],
    change: Mapping[str, Any],
    plan: Mapping[str, Any],
    controller_authority: str,
    acting_authority: str,
) -> dict[str, Any]:
    """Admit exact local experiment inputs and validate the demand before a run."""
    return _build_lifecycle(
        gate,
        mapping.revalidated(),
        oracle.revalidated(),
        _json(snapshot),
        _json(change),
        _json(plan),
        controller_authority,
        acting_authority,
        _now(),
    )


def _build_lifecycle(
    gate: OACPlanGate,
    mapping: OutcomeTaskMapping,
    oracle: OutcomeOracle,
    snapshot: dict[str, Any],
    change: dict[str, Any],
    plan: dict[str, Any],
    controller: str,
    actor: str,
    created_at: str,
) -> dict[str, Any]:
    _authorities(mapping, oracle, controller, actor)
    certificate = gate.verify(mapping, snapshot=snapshot, change=change, plan=plan)
    if plan["spec"]["effectCeiling"] != "zero_effect" or any(
        item["effectCeiling"] != "zero_effect"
        for key in ("roleInstances", "workUnits")
        for item in plan["spec"][key]
    ):
        raise IntegrityError("LAB_PORTABLE_PLAN_MUST_REMAIN_ZERO_EFFECT")
    principal, role = _requester(snapshot)
    metadata = snapshot["metadata"]
    payloads = []

    def add(kind: str, spec: dict[str, Any]) -> dict[str, Any]:
        resource = _resource(kind, spec, metadata, created_at)
        payloads.append(resource)
        return _resource_ref(resource)

    authorities = {
        name: add(
            "Principal",
            {
                "authority": value,
                "principalType": "scripted_local_service",
                "purpose": name,
                "claimScope": _SCOPE,
            },
        )
        for name, value in (("controller", controller), ("actor", actor), ("oracle", oracle.authority))
    }
    profile = add(
        "IntakeProfile",
        {
            "profileId": "orgrebase.disposable-local-intake/v1",
            "claimScope": _SCOPE,
            "rules": [
                "exact-source-digests",
                "public-plan-verification",
                "qualified-snapshot-requester",
                "separate-script-authorities",
                "no-execution-authority",
            ],
            "humanQualificationClaimed": False,
        },
    )
    manifest = add(
        "LocalIntakeManifest",
        {
            "mapping": mapping.model_dump(mode="json"),
            "oracle": oracle.model_dump(mode="json"),
            "snapshotRef": _resource_ref(snapshot),
            "changeRef": _resource_ref(change),
            "planRef": _resource_ref(plan),
            "planCertificateRef": _resource_ref(certificate),
            "authorities": authorities,
            "requester": principal,
            "accountableRole": role,
            "claimScope": _SCOPE,
            "unsupported": list(mapping.unsupported),
        },
    )
    source = _resource(
        "SourceAdmissionReceipt",
        {
            "admissionPurpose": "enterprise_intake",
            "subjectRefs": [_resource_ref(snapshot)],
            "intakeManifestDigest": manifest["digest"],
            "intakeProfileRef": profile,
            "ruleSetDigest": profile["digest"],
            "proposerRefs": [authorities["actor"]],
            "decisionAuthorityRef": authorities["controller"],
            "reviewerRefs": [authorities["oracle"]],
            "verdict": "ADMITTED",
            "reasonCodes": ["CONTROLLED_LOCAL_SCRIPT_ADMISSION", "EXACT_SOURCE_BYTES_ADMITTED"],
            "unresolvedRefs": [],
            "admittedSubjectRefs": [_resource_ref(snapshot)],
            "predecessorSourceRoot": None,
            "evolutionRoot": None,
            "candidateRef": None,
            "governanceDecisionRef": None,
        },
        metadata,
        created_at,
        [snapshot["metadata"]["id"], profile["resourceId"], authorities["controller"]["resourceId"]],
    )
    gate.validate_lifecycle(source)
    nodes = {node["nodeId"]: node for node in snapshot["spec"]["nodes"]}
    subjects = []
    for subject_id, path in sorted(mapping.subjects.items()):
        node = nodes.get(subject_id)
        if node is None or node["admissionStatus"] != "admitted":
            raise IntegrityError("LAB_PORTABLE_SUBJECT_NOT_ADMITTED")
        subjects.append(
            add(
                "OrganizationSubject",
                {"snapshotNode": node, "environmentPath": path, "snapshotRef": _resource_ref(snapshot)},
            )
        )
    desired = add(
        "OutcomeCriterion",
        {
            "mappingDigest": mapping.digest,
            "oracle": oracle.model_dump(mode="json"),
            "claimScope": _OUTCOME_SCOPE,
        },
    )
    obligations = [
        add("EvidenceObligation", {"evidenceId": name, "mappingDigest": mapping.digest})
        for name in mapping.required_evidence
    ]
    constraint = add(
        "EffectConstraint",
        {
            "planEffectCeiling": "zero_effect",
            "executionAuthority": "separate-one-use-disposable-controller-grant",
            "productionAuthority": False,
        },
    )
    demand = _resource(
        "OrganizationalDemand",
        {
            "snapshotRef": _resource_ref(snapshot),
            "requesterPrincipalRef": principal["principalId"],
            "accountableRoleRef": role["roleId"],
            "objective": "objective:observe-disposable-local-task-outcome",
            "subjectRefs": subjects,
            "triggerRefs": [_resource_ref(change)],
            "desiredOutcomeRefs": [desired],
            "evidenceObligationRefs": obligations,
            "constraintRefs": [constraint],
            "priority": 1,
            "effectCeiling": "zero_effect",
        },
        {**metadata, "ownerRef": role["roleId"]},
        created_at,
        [snapshot["metadata"]["id"], *(ref["resourceId"] for ref in subjects), change["metadata"]["id"]],
    )
    gate.validate_lifecycle(demand, snapshot=snapshot)
    return _pack(
        {
            "schema_version": "orgrebase.lab-lifecycle.v1",
            "claim_scope": _SCOPE,
            "recorded_at": created_at,
            "snapshot": snapshot,
            "change": change,
            "plan": plan,
            "plan_certificate": certificate,
            "source_admission": source,
            "demand": demand,
            "payloads": payloads,
        }
    )


def _verified_lifecycle(
    gate: OACPlanGate, lifecycle: Mapping[str, Any], trust: LabOutcomeTrust
) -> dict[str, Any]:
    _require_digest(lifecycle, trust.lifecycle_digest, "LAB_PORTABLE_LIFECYCLE_PIN_MISMATCH")
    expected = _build_lifecycle(
        gate,
        trust.mapping.revalidated(),
        trust.oracle.revalidated(),
        _json(lifecycle["snapshot"]),
        _json(lifecycle["change"]),
        _json(lifecycle["plan"]),
        trust.controller_authority,
        trust.acting_authority,
        lifecycle["recorded_at"],
    )
    if sha256_digest(expected) != sha256_digest(lifecycle):
        raise IntegrityError("LAB_PORTABLE_LIFECYCLE_CLOSURE_MISMATCH")
    return expected


def build_portable_lab_outcome(
    gate: OACPlanGate, lifecycle: Mapping[str, Any], receipt: Mapping[str, Any], trust: LabOutcomeTrust
) -> dict[str, Any]:
    """Bind a verified controller receipt and every payload to the explicit OAC profile."""
    lifecycle, receipt = _json(lifecycle), _json(receipt)
    lifecycle = _verified_lifecycle(gate, lifecycle, trust)
    _require_digest(receipt, trust.receipt_digest, "LAB_PORTABLE_RECEIPT_PIN_MISMATCH")
    _require_digest(receipt["approval_receipt"], trust.approval_digest, "LAB_PORTABLE_APPROVAL_PIN_MISMATCH")
    mapping, oracle = trust.mapping.revalidated(), trust.oracle.revalidated()
    certificate = lifecycle["plan_certificate"]
    verify_outcome_receipt(
        receipt,
        mapping=mapping,
        oracle=oracle,
        plan_certificate_digest=certificate["digest"],
        controller_authority=trust.controller_authority,
        acting_authority=trust.acting_authority,
    )
    metadata = lifecycle["snapshot"]["metadata"]
    payloads: list[dict[str, Any]] = []

    def add(kind: str, spec: dict[str, Any]) -> dict[str, Any]:
        resource = _resource(kind, spec, metadata, trust.recorded_at)
        _verify_oac_resource(resource, kind)
        payloads.append(resource)
        return _resource_ref(resource)

    snapshot, source, demand, change, plan, plan_certificate = (
        _resource_ref(lifecycle[key])
        for key in ("snapshot", "source_admission", "demand", "change", "plan", "plan_certificate")
    )
    runtime = receipt["runtime_bundle"]
    expected_runtime = build_runtime_bundle(
        mapping,
        receipt["approval_receipt"],
        plan_ref=plan,
        certificate_ref=plan_certificate,
        metadata=metadata,
        acting_authority=trust.acting_authority,
    )
    if sha256_digest(runtime) != sha256_digest(expected_runtime):
        raise IntegrityError("LAB_PORTABLE_RUNTIME_BUNDLE_MISMATCH")
    payloads.extend(runtime[key] for key in ("mapping", "grant", "binding", "bundle"))
    mapping_ref, grant, binding, bundle = (
        _resource_ref(runtime[key]) for key in ("mapping", "grant", "binding", "bundle")
    )
    oracle_ref = add(
        "OutcomeOracle",
        {
            "oracle": oracle.model_dump(mode="json"),
            "authority": oracle.authority,
            "buildDigest": trust.oracle_build_digest,
            "claimScope": _OUTCOME_SCOPE,
        },
    )
    observation_profile = add(
        "ObservationProfile",
        {
            "profileBinding": _profile(),
            "mappingRef": mapping_ref,
            "oracleRef": oracle_ref,
            "dimensions": list(DIMENSIONS),
            "oraclePolicyDigest": oracle.policy_source_digest,
            "oracleBuildDigest": trust.oracle_build_digest,
        },
    )
    actor_ref = add(
        "Principal",
        {
            "authority": trust.acting_authority,
            "principalType": "scripted_local_service",
            "claimScope": _OUTCOME_SCOPE,
        },
    )
    execution = add(
        "ExecutionReceipt",
        {
            "labReceiptDigest": receipt["digest"],
            "runtimeBindingRef": binding,
            "runtimeBundleRef": bundle,
            "executionAuthorizationRef": grant,
        },
    )
    trace = add(
        "ExecutionEvidence",
        {
            "executionReceiptRef": execution,
            "trace": receipt["trace"],
            "traceDigest": receipt["trace_digest"],
            "toolTimings": receipt["tool_timings"],
        },
    )
    states = add(
        "ExecutionEvidence",
        {
            "executionReceiptRef": execution,
            "labReceiptDigest": receipt["digest"],
            "stateObservationRoots": sorted(receipt["state_observations"]),
            "initialRoot": receipt["initial_root"],
            "finalRoot": receipt["final_root"],
            "resetReceipt": receipt["reset_receipt"],
        },
    )
    observation = add(
        "OutcomeObservation",
        {
            "oracleRef": oracle_ref,
            "executionReceiptRef": execution,
            "traceRef": trace,
            "statesRef": states,
            "goals": receipt["goals"],
            "forbiddenPaths": receipt["forbidden_paths"],
            "readonlyEffects": receipt["readonly_effects"],
            "observedEvidence": receipt["observed_evidence"],
            "dimensions": receipt["dimensions"],
            "unresolved": receipt["unresolved"],
        },
    )
    evidence_refs, unresolved_refs, dimensions, reasons = [], [], [], set()
    for name in DIMENSIONS:
        verdict = receipt["dimensions"][name]
        evidence = add(
            "OutcomeEvidence",
            {
                "dimension": name,
                "verdict": verdict,
                "observationRef": observation,
                "oracleRef": oracle_ref,
                "executionReceiptRef": execution,
            },
        )
        evidence_refs.append(evidence)
        codes, missing = [], []
        if verdict == "FAIL":
            codes = [
                "OUTCOME_FORBIDDEN_EFFECT_OBSERVED"
                if name == "forbidden_effects"
                else "LAB_" + name.upper() + "_FAILED"
            ]
        elif verdict == "UNKNOWN":
            codes = ["OUTCOME_OBSERVATION_UNRESOLVED"]
            missing = [
                add(
                    "OutcomeObservation",
                    {
                        "dimension": name,
                        "verdict": "UNKNOWN",
                        "receiptUnresolved": receipt["unresolved"],
                        "observationRef": observation,
                    },
                )
            ]
            unresolved_refs.extend(missing)
        reasons.update(codes)
        dimensions.append(
            {
                "name": name,
                "verdict": verdict,
                "reasonCodes": codes,
                "evidenceRefs": [evidence],
                "unresolvedRefs": missing,
            }
        )
    source_root = _domain_root(
        "oac.root/source/v0.1", [("snapshot", [snapshot]), ("sourceAdmissionReceipts", [source])]
    )
    demand_root = _domain_root("oac.root/demand/v0.1", [("demand", [demand]), ("changes", [change])])
    plan_root = _domain_root(
        "oac.root/plan/v0.1", [("plan", [plan]), ("planCertificate", [plan_certificate])]
    )
    execution_root = _domain_root(
        "oac.state/disposable-execution-evidence/v0.1",
        [
            *_profile().items(),
            ("executionAuthorization", [grant]),
            ("runtimeBinding", [binding]),
            ("runtimeBundle", [bundle]),
            ("executionReceipt", [execution]),
            ("executionEvidence", [trace, states]),
        ],
    )
    sources = [
        snapshot,
        source,
        demand,
        change,
        plan,
        plan_certificate,
        binding,
        bundle,
        execution,
        trace,
        states,
        observation,
        oracle_ref,
        observation_profile,
        *evidence_refs,
        *unresolved_refs,
        grant,
    ]
    outcome = _resource(
        "OutcomeCertificate",
        {
            "profileBinding": _profile(),
            "executionAuthorizationRef": grant,
            "sourceRoot": source_root,
            "demandRoot": demand_root,
            "planRoot": plan_root,
            "executionRoot": execution_root,
            "snapshotRef": snapshot,
            "sourceAdmissionReceiptRefs": [source],
            "demandRef": demand,
            "changeRefs": [change],
            "planRef": plan,
            "planCertificateRef": plan_certificate,
            "runtimeBindingRef": binding,
            "runtimeBundleRef": bundle,
            "executionReceiptRef": execution,
            "executionEvidenceRefs": [trace, states],
            "observationRefs": [observation],
            "actingPrincipalRefs": [actor_ref],
            "oracleRef": oracle_ref,
            "oracleBuildDigest": trust.oracle_build_digest,
            "observationProfileRef": observation_profile,
            "verdict": receipt["verdict"],
            "dimensions": dimensions,
            "reasonCodes": sorted(reasons),
            "evidenceRefs": evidence_refs,
            "unresolvedRefs": unresolved_refs,
        },
        metadata,
        trust.recorded_at,
        [ref["resourceId"] for ref in sources],
    )
    gate.validate_lifecycle(outcome)
    return _pack(
        {
            "schema_version": "orgrebase.portable-lab-outcome.v2",
            "claim_scope": _OUTCOME_SCOPE,
            "lifecycle": lifecycle,
            "receipt": receipt,
            "recorded_at": trust.recorded_at,
            "outcome_certificate": outcome,
            "payloads": payloads,
        }
    )


def verify_portable_lab_outcome(gate: OACPlanGate, bundle: Mapping[str, Any], trust: LabOutcomeTrust) -> None:
    """Reject any substitution, omission or addition against independently trusted pins."""
    try:
        bundle = _json(bundle)
        expected = build_portable_lab_outcome(gate, bundle["lifecycle"], bundle["receipt"], trust)
        if sha256_digest(expected) != sha256_digest(bundle):
            raise IntegrityError("LAB_PORTABLE_PAYLOAD_CLOSURE_MISMATCH")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise IntegrityError("LAB_PORTABLE_BUNDLE_MALFORMED") from exc
