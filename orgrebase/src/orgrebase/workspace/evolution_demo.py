"""One-command, evidence-first OAC governed-evolution reference journey."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.evolution_contracts import (
    AcceptedEvolutionSupport,
    EvolutionProofArtifact,
    ExecutionEvidence,
    GovernanceEvidenceMode,
    HandlerContract,
    OACExecutionApproval,
    OACExecutionReceipt,
)
from orgrebase.workspace.evolution_evidence import (
    build_execution_approval,
    build_initial_lifecycle_roots,
    build_portable_outcome_certificate,
)
from orgrebase.workspace.evolution_runtime import (
    EXECUTION_EVIDENCE_MEDIA_TYPE,
    EXECUTION_RECEIPT_MEDIA_TYPE,
    ZeroEffectOACExecutor,
)
from orgrebase.workspace.governed_evolution import (
    SOURCE_OBJECT_ID,
    GovernedProcedureEvolution,
)
from orgrebase.workspace.models import (
    OACFormationPreview,
    OACRuntimeApproval,
    OACRuntimeCapsule,
    RuntimeAdmissionReceipt,
)
from orgrebase.workspace.oac_admission import OACRuntimeAdmissionBridge
from orgrebase.workspace.oac_wire import DEFAULT_POLICY_PATH, OACBlackBoxCLI
from orgrebase.workspace.outcome_assurance import (
    ControlledOutcomeObservationProducer,
    ControlledProcessOutcomeAssurance,
    controlled_reference_actual_values,
)
from orgrebase.workspace.reference_profiles import (
    supplier_sc008_source_aligned_profile,
    supplier_shadow_intake_profile,
)
from orgrebase.workspace.service import WorkspaceService

_QUOTE_SEMANTICS = {
    "customer_id": "customer:acme", "currency": "EUR", "data_residency": "US region supported",
    "deliverable_kind": "QUOTE", "launch_date": "2026-09-15", "notice_required": True,
    "owner": "sales-owner", "partner_terms_code": "legal-review", "price_band": "strategic",
    "product_plan": "enterprise-plan-v4",
}
_REPLAY_REF = "replay:veracier-sc008-cross-topology"
_REGRESSION_REF = "regression-suite:orgrebase-preliminary-and-sc008"
_RUNS = (
    ("base-accepted", "BASE"),
    ("split-accepted", "SPLIT"),
    ("split-rejected", "SPLIT"),
)

_ROOT_PATHS = ("event-chain.json", "summary.json")
_SOURCE_PATHS = tuple(
    f"source/{name}.json"
    for name in (
        "change",
        "demand",
        "profile-migration",
        "profile-r1",
        "profile-r2",
        "snapshot-r2",
        "source-admission-local",
        "source-admission-oac",
    )
)
_PLAN_PATHS = tuple(
    f"plans/{case}/{name}.json"
    for case in ("BASE", "SPLIT")
    for name in ("capsule", "runtime-admission", "runtime-approval", "runtime-preview")
)
_RUN_PATHS = tuple(
    f"runs/{run}/{name}.json"
    for run, _ in _RUNS
    for name in (
        "evidence",
        "execution-approval",
        "execution-receipt",
        "outcome-certificate",
        "outcome-decision",
        "outcome-observation",
    )
)
_EVOLUTION_PATHS = tuple(
    f"evolution/{name}.json"
    for name in (
        "governance-decision",
        "pointer-transition",
        "portable-source-admission",
        "procedure-candidate",
        "profile-successor",
        "profile-successor-lineage",
        "promotion-receipt",
        "proposal",
        "regression-evidence",
        "replay-evidence",
        "rollback-receipt",
        "snapshot-successor",
        "snapshot-successor-lineage",
        "source-predecessor",
        "source-successor",
    )
)
EXPECTED_ARTIFACT_PATHS = tuple(
    sorted((*_ROOT_PATHS, *_SOURCE_PATHS, *_PLAN_PATHS, *_RUN_PATHS, *_EVOLUTION_PATHS))
)
EXPECTED_JSON_PATHS = frozenset((*EXPECTED_ARTIFACT_PATHS, "evidence-index.json"))


def _json(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_json(item) for item in value]
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _sealed_evidence(payload: Mapping[str, object]) -> dict[str, object]:
    value = dict(payload)
    value["digest"] = sha256_digest(value)
    return value


def _admit_plan(
    store: StateStore,
    capsule: OACRuntimeCapsule,
    *,
    policy_path: str | Path,
) -> tuple[OACFormationPreview, OACRuntimeApproval, RuntimeAdmissionReceipt]:
    bridge = OACRuntimeAdmissionBridge(store, policy_path=policy_path)
    preview = bridge.prepare(capsule)
    approval = bridge.approve(
        preview,
        actor_id=preview.runtime_owner_id,
        preview_digest=preview.digest,
        command_id=f"evolution-{capsule.case_id.lower()}-formation",
        approved_at="2026-08-26T00:00:00Z",
    )
    return preview, approval, bridge.apply(capsule, preview, approval)


def _load_execution(
    store: StateStore,
    receipt: OACExecutionReceipt,
) -> tuple[OACExecutionReceipt, tuple[ExecutionEvidence, ...]]:
    expected = receipt.revalidated()
    persisted_receipt = OACExecutionReceipt.model_validate(
        store.load_artifact(receipt.receipt_id, EXECUTION_RECEIPT_MEDIA_TYPE).payload
    )
    if persisted_receipt.digest != expected.digest:
        raise IntegrityError("OAC_EVOLUTION_RESTART_RECEIPT_DRIFT")
    evidence = tuple(
        ExecutionEvidence.model_validate(store.load_artifact(ref, EXECUTION_EVIDENCE_MEDIA_TYPE).payload)
        for ref in persisted_receipt.evidence_refs
    )
    if tuple(item.digest for item in evidence) != persisted_receipt.evidence_digests:
        raise IntegrityError("OAC_EVOLUTION_RESTART_EVIDENCE_DRIFT")
    return persisted_receipt, evidence


def _artifact_count(store: StateStore) -> int:
    return int(store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0])


def _observation_values(run_id: str) -> dict[str, str]:
    values = controlled_reference_actual_values()
    if run_id == "split-rejected":
        values["qualification-evidence-check"] = "qualification_record_expired"
    return values


def _handler_contracts(
    accepted: Sequence[AcceptedEvolutionSupport],
    evidence_sets: Sequence[tuple[ExecutionEvidence, ...]],
) -> tuple[tuple[HandlerContract, ...], tuple[str, ...]]:
    evidence = {item.evidence_id: item for values in evidence_sets for item in values}
    contracts: dict[str, HandlerContract] = {}
    for support in accepted:
        for step in support.execution.steps:
            for handler in step.handlers:
                contracts[handler.obligation_type] = HandlerContract(
                    obligation_type=handler.obligation_type,
                    handler_ref=handler.handler_ref,
                    handler_digest=handler.handler_digest,
                    capability_ref=handler.capability_ref,
                    evidence_types=tuple(
                        sorted(
                            {evidence[ref].evidence_type for ref in handler.evidence_refs},
                            key=str.encode,
                        )
                    ),
                )
    return (
        tuple(sorted(contracts.values(), key=lambda item: item.obligation_type.encode())),
        tuple(sorted({item.evidence_type for item in evidence.values()}, key=str.encode)),
    )


def _quote_regression(database: Path) -> dict[str, object]:
    result = WorkspaceService.run_explicit_local_product_loop(
        database,
        allow_scripted_approval=True,
    )
    quote = result["final_quote"]
    actual_semantics = {field: quote.payload.get(field) for field in _QUOTE_SEMANTICS}
    if (sha256_digest(actual_semantics) != sha256_digest(_QUOTE_SEMANTICS) or quote.id != "work:quote_acme"
            or quote.version != "v3" or quote.state.value != "CURRENT"
            or result["restart"]["state_digest_before_close"] != result["restart"]["state_digest_after_reopen"]
            or result["event_chain"]["status"] != "PASS"):
        raise IntegrityError("PRELIMINARY_QUOTE_SEMANTIC_REGRESSION")
    return _sealed_evidence(
        {
            "schema_version": "orgrebase.preliminary-quote-regression.v2",
            "status": "PASS",
            "journey": "NORTHSTAR_QUOTE_V1_TO_V3",
            "expected_quote_semantics": _QUOTE_SEMANTICS,
            "actual_quote_semantics": actual_semantics,
            "actual_quote_digest": quote.digest,
            "final_quote": quote.model_dump(mode="json"),
            "restart": result["restart"],
            "event_chain": result["event_chain"],
            "claim_boundary": "LOCAL_DETERMINISTIC_SYNTHETIC_REGRESSION_ONLY",
        }
    )


def _export_pack(output_dir: Path, artifacts: Mapping[str, object]) -> dict[str, Any]:
    if tuple(sorted(artifacts)) != EXPECTED_ARTIFACT_PATHS:
        raise IntegrityError("OAC_EVOLUTION_EVIDENCE_PATH_SET_INVALID")
    if output_dir.is_symlink() or (output_dir.exists() and not output_dir.is_dir()):
        raise IntegrityError("OAC_EVOLUTION_OUTPUT_PATH_INVALID")
    output_dir.mkdir(parents=True, exist_ok=True)
    discovered = tuple(output_dir.rglob("*"))
    if any(path.is_symlink() for path in discovered):
        raise IntegrityError("OAC_EVOLUTION_MANAGED_PATH_SYMLINK_FORBIDDEN")
    existing = {path.relative_to(output_dir).as_posix() for path in discovered if path.is_file()}
    if existing - EXPECTED_JSON_PATHS:
        raise IntegrityError("OAC_EVOLUTION_OUTPUT_CONTAINS_UNMANAGED_FILES")
    for relative in EXPECTED_ARTIFACT_PATHS:
        _write_json(output_dir / relative, artifacts[relative])
    entries = [
        {"artifact_ref": relative, "sha256": _file_digest(output_dir / relative)}
        for relative in EXPECTED_ARTIFACT_PATHS
    ]
    index = {
        "schema_version": "orgrebase.oac-evolution-evidence-index.v1",
        "entries": entries,
        "pack_digest": sha256_digest(entries),
        "status": "PASS",
    }
    _write_json(output_dir / "evidence-index.json", index)
    actual = {
        path.relative_to(output_dir).as_posix() for path in output_dir.rglob("*.json") if path.is_file()
    }
    if actual != EXPECTED_JSON_PATHS:
        raise IntegrityError("OAC_EVOLUTION_EVIDENCE_PATH_SET_INVALID")
    return index


def run_oac_evolution_demo(
    output_dir: str | Path,
    *,
    oac_root: str | Path | None = None,
    policy_path: str | Path = DEFAULT_POLICY_PATH,
) -> dict[str, Any]:
    """Run the bounded reference lifecycle and export its exact evidence pack."""

    cli = OACBlackBoxCLI(oac_root)
    capsules = {case: cli.build_capsule(case) for case in ("BASE", "SPLIT")}
    if any(
        capsules["BASE"].model_dump(mode="json")[name]["digest"]
        != capsules["SPLIT"].model_dump(mode="json")[name]["digest"]
        for name in ("snapshot", "change")
    ):
        raise IntegrityError("OAC_EVOLUTION_INPUT_ROOTS_DIVERGE")
    if any(
        capsule.plan_certificate.get("spec", {}).get("verdict") != "ACCEPT" for capsule in capsules.values()
    ):
        raise IntegrityError("OAC_EVOLUTION_PLAN_NOT_ACCEPTED")
    topology_digests = {
        str(capsule.runtime_bundle["spec"]["topologyDigest"]) for capsule in capsules.values()
    }
    if len(topology_digests) != 2:
        raise IntegrityError("OAC_EVOLUTION_TOPOLOGY_DIVERSITY_MISSING")
    roots = build_initial_lifecycle_roots(cli, capsules["BASE"].snapshot, capsules["BASE"].change)
    profile_r1 = supplier_shadow_intake_profile()
    profile_r2 = supplier_sc008_source_aligned_profile()
    artifacts: dict[str, object] = {
        "source/profile-r1.json": profile_r1,
        "source/profile-r2.json": profile_r2,
        "source/source-admission-local.json": roots.source_receipt,
        "source/profile-migration.json": roots.migration_receipt,
        "source/source-admission-oac.json": roots.oac_source_admission,
        "source/demand.json": roots.demand,
        "source/snapshot-r2.json": capsules["BASE"].snapshot,
        "source/change.json": capsules["BASE"].change,
    }
    plan_records: dict[
        str,
        tuple[OACFormationPreview, OACRuntimeApproval, RuntimeAdmissionReceipt],
    ] = {}
    execution_approvals: dict[str, OACExecutionApproval] = {}
    execution_receipts: dict[str, OACExecutionReceipt] = {}
    execution_evidence_digests: dict[str, tuple[str, ...]] = {}
    with tempfile.TemporaryDirectory(prefix="orgrebase-oac-evolution-") as raw:
        temporary_root = Path(raw)
        database = temporary_root / "evolution.sqlite"
        store = StateStore(database)
        try:
            for case, capsule in capsules.items():
                preview, approval, admission = _admit_plan(
                    store,
                    capsule,
                    policy_path=policy_path,
                )
                plan_records[case] = (preview, approval, admission)
                artifacts.update(
                    {
                        f"plans/{case}/capsule.json": capsule,
                        f"plans/{case}/runtime-preview.json": preview,
                        f"plans/{case}/runtime-approval.json": approval,
                        f"plans/{case}/runtime-admission.json": admission,
                    }
                )
            executor = ZeroEffectOACExecutor(store, policy_path=policy_path)
            for run_id, case in _RUNS:
                capsule = capsules[case]
                admission = plan_records[case][2]
                approval = build_execution_approval(
                    command_id=f"execute-{run_id}",
                    capsule_digest=capsule.digest,
                    runtime_admission_digest=admission.digest,
                    source_admission=roots.oac_source_admission,
                    demand=roots.demand,
                    runtime_bundle_digest=str(capsule.runtime_bundle["digest"]),
                    runtime_owner_id=plan_records[case][0].runtime_owner_id,
                )
                execution_approvals[run_id] = approval
                receipt, evidence = executor.execute(
                    capsule,
                    roots.demand,
                    admission,
                    approval,
                    source_admission=roots.oac_source_admission,
                    run_id=run_id,
                )
                execution_receipts[run_id] = receipt
                execution_evidence_digests[run_id] = tuple(item.digest for item in evidence)
            before_restart = store.verify_event_chain()
            artifacts_before_restart = _artifact_count(store)
        finally:
            store.close()

        store = StateStore(database)
        try:
            accepted: list[AcceptedEvolutionSupport] = []
            accepted_evidence: list[tuple[ExecutionEvidence, ...]] = []
            outcomes: dict[str, dict[str, Any]] = {}
            persisted_digests: list[str] = []
            reopened_executor = ZeroEffectOACExecutor(store, policy_path=policy_path)
            for run_id, case in _RUNS:
                replayed_receipt, replayed_evidence = reopened_executor.execute(
                    capsules[case],
                    roots.demand,
                    plan_records[case][2],
                    execution_approvals[run_id],
                    source_admission=roots.oac_source_admission,
                    run_id=run_id,
                )
                if (
                    replayed_receipt.digest != execution_receipts[run_id].digest
                    or tuple(item.digest for item in replayed_evidence) != execution_evidence_digests[run_id]
                ):
                    raise IntegrityError("OAC_EVOLUTION_RESTART_REPLAY_DRIFT")
            after_restart_replay = store.verify_event_chain()
            artifacts_after_restart_replay = _artifact_count(store)
            if after_restart_replay != before_restart:
                raise IntegrityError("OAC_EVOLUTION_RESTART_REPLAY_DUPLICATED_EFFECTS")
            if artifacts_after_restart_replay != artifacts_before_restart:
                raise IntegrityError("OAC_EVOLUTION_RESTART_REPLAY_DUPLICATED_ARTIFACTS")
            for run_id, case in _RUNS:
                execution, evidence = _load_execution(store, execution_receipts[run_id])
                persisted_digests.append(execution.digest)
                if {item.observed_value for item in evidence} != {"zero_effect_handler_completed"}:
                    raise IntegrityError("OAC_EVOLUTION_RUNTIME_EVIDENCE_NOT_NEUTRAL")
                observation = ControlledOutcomeObservationProducer(store).record(
                    execution,
                    observation_id=f"outcome-observation:{run_id}",
                    actual_values=_observation_values(run_id),
                )
                _, decision = ControlledProcessOutcomeAssurance(store).evaluate(
                    execution,
                    evidence,
                    observation,
                    decision_id=f"outcome-decision:{run_id}",
                )
                outcome = build_portable_outcome_certificate(
                    cli,
                    capsule=capsules[case].model_dump(mode="json"),
                    roots=roots,
                    execution=execution,
                    evidence=evidence,
                    observation=observation,
                    decision=decision,
                )
                outcomes[run_id] = outcome
                artifacts.update(
                    {
                        f"runs/{run_id}/execution-approval.json": execution_approvals[run_id],
                        f"runs/{run_id}/execution-receipt.json": execution,
                        f"runs/{run_id}/evidence.json": evidence,
                        f"runs/{run_id}/outcome-observation.json": observation,
                        f"runs/{run_id}/outcome-decision.json": decision,
                        f"runs/{run_id}/outcome-certificate.json": outcome,
                    }
                )
                if run_id != "split-rejected":
                    accepted.append(
                        AcceptedEvolutionSupport(
                            outcome=outcome,
                            execution=execution,
                            topology_digest=str(capsules[case].runtime_bundle["spec"]["topologyDigest"]),
                        )
                    )
                    accepted_evidence.append(evidence)

            accepted_support = tuple(accepted)
            contracts, evidence_types = _handler_contracts(accepted_support, accepted_evidence)
            quote_regression = _quote_regression(temporary_root / "quote-regression.sqlite")
            initial_source = GovernedProcedureEvolution._initial_source(
                profile_r2,
                str(capsules["BASE"].snapshot["metadata"]["id"]),
                str(capsules["BASE"].snapshot["digest"]),
            )
            candidate = GovernedProcedureEvolution.build_candidate(
                profile=profile_r2,
                demand_digest=str(roots.demand["digest"]),
                accepted=accepted_support,
                rejected_outcomes=(outcomes["split-rejected"],),
                handler_contracts=contracts,
                required_evidence_types=evidence_types,
            )
            candidate_subject = f"{candidate.candidate_id}@{candidate.digest}"
            replay = EvolutionProofArtifact(
                artifact_id=_REPLAY_REF,
                artifact_type="REPLAY_EVIDENCE",
                subject_ref=candidate_subject,
                source_refs=tuple(
                    sorted(
                        (
                            *(
                                f"{item.outcome['metadata']['id']}@{item.outcome['digest']}"
                                for item in accepted_support
                            ),
                            *(
                                f"{item.execution.receipt_id}@{item.execution.digest}"
                                for item in accepted_support
                            ),
                        ),
                        key=str.encode,
                    )
                ),
                assertions=tuple(
                    sorted(
                        (
                            "DISTINCT_TOPOLOGIES_SAME_OBLIGATION_CONTRACT",
                            "TWO_ACCEPTED_EXECUTIONS_REPLAYED_AFTER_SQLITE_REOPEN",
                        ),
                        key=str.encode,
                    )
                ),
                created_at="2026-08-26T00:03:10Z",
            )
            regression = EvolutionProofArtifact(
                artifact_id=_REGRESSION_REF,
                artifact_type="REGRESSION_SUITE",
                subject_ref=candidate_subject,
                source_refs=tuple(
                    sorted(
                        (
                            f"quote-digest:{quote_regression['actual_quote_digest']}",
                            (
                                f"{outcomes['split-rejected']['metadata']['id']}@"
                                f"{outcomes['split-rejected']['digest']}"
                            ),
                        ),
                        key=str.encode,
                    )
                ),
                assertions=tuple(
                    sorted(
                        (
                            "COMPLETED_EXECUTION_CAN_BE_OUTCOME_REJECTED",
                            "PRELIMINARY_QUOTE_SEMANTICS_AND_RESTART_VERIFIED",
                        ),
                        key=str.encode,
                    )
                ),
                created_at="2026-08-26T00:03:11Z",
            )
            proposal = GovernedProcedureEvolution.build_proposal(
                profile=profile_r2,
                candidate=candidate,
                predecessor_source_ref=f"{SOURCE_OBJECT_ID}@r1",
                predecessor_source_digest=initial_source.digest,
                predecessor_snapshot=dict(capsules["BASE"].snapshot),
                accepted=accepted_support,
                rejected_outcomes=(outcomes["split-rejected"],),
                replay_evidence=replay,
                regression_evidence=regression,
                declared_benefit=(
                    "Reuse one evidence-bound semantic procedure across independently compiled "
                    "topologies without freezing either DAG."
                ),
                expires_at="2026-08-27T00:00:00Z",
            )
            decision = GovernedProcedureEvolution.decide(
                proposal,
                candidate,
                profile_r2,
                actor_id="scripted:veracier-governance-demo",
                actor_mode=GovernanceEvidenceMode.SCRIPTED_GOVERNANCE_IDENTITY,
                actor_authority_ref="human:veracier-shadow-owner",
            )
            evolution = GovernedProcedureEvolution(store, cli)
            promoted = evolution.promote(
                profile=profile_r2,
                predecessor_snapshot=dict(capsules["BASE"].snapshot),
                candidate=candidate,
                proposal=proposal,
                decision=decision,
                supporting_outcomes=tuple(item.outcome for item in accepted_support),
                counterexample_outcomes=(outcomes["split-rejected"],),
                replay_evidence=replay,
                regression_evidence=regression,
            )
            after_promotion = store.get_pointer(SOURCE_OBJECT_ID)
            active_after_promotion = store.get_object(SOURCE_OBJECT_ID).model_dump(mode="json")
            rollback = evolution.rollback(
                actor_id="scripted:veracier-governance-demo",
                actor_mode=GovernanceEvidenceMode.SCRIPTED_GOVERNANCE_IDENTITY,
                actor_authority_ref="human:veracier-shadow-owner",
            )
            after_rollback = store.get_pointer(SOURCE_OBJECT_ID)
            active_after_rollback = store.get_object(SOURCE_OBJECT_ID).model_dump(mode="json")
            snapshot_successor = json.loads(promoted.snapshot_successor.snapshot_payload_jcs)
            pointer_transition = {
                "schema_version": "orgrebase.oac-evolution-pointer-transition.v1",
                "predecessor": {
                    "ref": f"{SOURCE_OBJECT_ID}@r1",
                    "digest": initial_source.digest,
                },
                "after_promotion": after_promotion,
                "active_object_after_promotion": active_after_promotion,
                "after_rollback": after_rollback,
                "active_object_after_rollback": active_after_rollback,
                "history_retained": {
                    "r1": store.artifact_exists(f"{SOURCE_OBJECT_ID}@r1"),
                    "r2": store.artifact_exists(f"{SOURCE_OBJECT_ID}@r2"),
                },
                "target_writes": 0,
            }
            artifacts.update(
                {
                    "evolution/replay-evidence.json": replay,
                    "evolution/regression-evidence.json": regression,
                    "evolution/procedure-candidate.json": candidate,
                    "evolution/proposal.json": proposal,
                    "evolution/governance-decision.json": decision,
                    "evolution/profile-successor-lineage.json": promoted.profile_successor,
                    "evolution/profile-successor.json": promoted.profile_successor.profile_payload,
                    "evolution/snapshot-successor-lineage.json": promoted.snapshot_successor,
                    "evolution/snapshot-successor.json": snapshot_successor,
                    "evolution/source-predecessor.json": initial_source,
                    "evolution/source-successor.json": promoted.source_successor,
                    "evolution/portable-source-admission.json": promoted.portable_source_admission,
                    "evolution/promotion-receipt.json": promoted.receipt,
                    "evolution/rollback-receipt.json": rollback,
                    "evolution/pointer-transition.json": pointer_transition,
                }
            )
            chain = store.verify_event_chain()
            records = list(store.event_envelopes())
            expected_event_types = (
                "OAC_RUNTIME_FORMATION_ACTIVATED",
                "OAC_RUNTIME_FORMATION_ACTIVATED",
                "OAC_EXECUTION_COMPLETED",
                "OAC_EXECUTION_COMPLETED",
                "OAC_EXECUTION_COMPLETED",
                "OAC_CONTROLLED_OBSERVATION_RECORDED",
                "OAC_CONTROLLED_OUTCOME_ISSUED",
                "OAC_CONTROLLED_OBSERVATION_RECORDED",
                "OAC_CONTROLLED_OUTCOME_ISSUED",
                "OAC_CONTROLLED_OBSERVATION_RECORDED",
                "OAC_CONTROLLED_OUTCOME_ISSUED",
                "OAC_PROCEDURE_SOURCE_PROMOTED",
                "OAC_PROCEDURE_SOURCE_ROLLED_BACK",
            )
            if (
                chain["events"] != 13
                or tuple(record["event_type"] for record in records) != expected_event_types
            ):
                raise IntegrityError("OAC_EVOLUTION_EVENT_SEQUENCE_INVALID")
            for index, (run_id, _) in enumerate(_RUNS):
                observation_event = records[5 + index * 2]
                outcome_event = records[6 + index * 2]
                if (
                    observation_event["payload"].get("observation_ref") != f"outcome-observation:{run_id}"
                    or outcome_event["payload"].get("decision_ref") != f"outcome-decision:{run_id}"
                ):
                    raise IntegrityError("OAC_EVOLUTION_OBSERVATION_OUTCOME_ORDER_INVALID")
            event_chain = {**chain, "records": records}
            summary = {
                "schema_version": "orgrebase.workspace-oac-evolution-demo.v2",
                "status": "PASS",
                "maturity": "SYNTHETIC_CONTROLLED_REFERENCE_MVP",
                "product": "OrgRebase governed organizational evolution",
                "source": {
                    "historical_profile_ref": profile_r1.ref,
                    "historical_profile_digest": profile_r1.digest,
                    "aligned_profile_ref": profile_r2.ref,
                    "aligned_profile_digest": profile_r2.digest,
                    "migration_digest": roots.migration_receipt.digest,
                    "demand_digest": roots.demand["digest"],
                },
                "plans": {
                    case: {
                        "plan_digest": capsule.plan["digest"],
                        "topology_digest": capsule.runtime_bundle["spec"]["topologyDigest"],
                        "runtime_admission": plan_records[case][2].status,
                    }
                    for case, capsule in capsules.items()
                },
                "runs": {
                    run_id: {
                        "plan_status": "ACCEPT",
                        "execution_status": execution_receipts[run_id].status.value,
                        "outcome_verdict": outcomes[run_id]["spec"]["verdict"],
                    }
                    for run_id, _ in _RUNS
                },
                "restart": {
                    "store_profile": "FILE_BACKED_SQLITE",
                    "closed_after_execution": True,
                    "idempotent_execution_replay": "PASS",
                    "events_before_close": before_restart["events"],
                    "events_after_replay": after_restart_replay["events"],
                    "artifacts_before_close": artifacts_before_restart,
                    "artifacts_after_replay": artifacts_after_restart_replay,
                    "replay_event_delta": 0,
                    "replay_artifact_delta": 0,
                    "event_head_unchanged": (
                        before_restart["head_digest"] == after_restart_replay["head_digest"]
                    ),
                    "execution_receipt_digests_after_reopen": sorted(persisted_digests),
                },
                "evolution": {
                    "candidate_digest": candidate.digest,
                    "proposal_digest": proposal.digest,
                    "governance_actor": decision.actor_id,
                    "governance_mode": decision.actor_mode.value,
                    "human_review": promoted.receipt.human_review_status,
                    "profile_successor_digest": promoted.receipt.profile_successor_digest,
                    "snapshot_successor_digest": promoted.receipt.snapshot_successor_digest,
                    "promotion": "r1_TO_r2",
                    "rollback": "r2_TO_r1",
                    "active_pointer": after_rollback["version"],
                },
                "preliminary_regression": quote_regression,
                "boundaries": {
                    "data": "SYNTHETIC_FIXTURE",
                    "assurance": "SYNTHETIC_CONTROLLED_PROCESS_ASSURANCE_ONLY",
                    "observation": ("SYNTHETIC_CONTROLLED_OBSERVATION_NOT_EXTERNAL_GROUND_TRUTH"),
                    "runtime_evidence": "ZERO_EFFECT_HANDLER_COMPLETION_NOT_BUSINESS_TRUTH",
                    "demand_admission": ("NO_INDEPENDENT_KIND_SCHEMA_VALIDATED_AND_EXECUTION_APPROVAL_BOUND"),
                    "source_admission_authority": (
                        roots.oac_source_admission["spec"]["decisionAuthorityRef"]["resourceId"]
                    ),
                    "authority_assurance": "DECLARED_NOT_AUTHENTICATED",
                    "human_review": "NOT_RUN",
                    "real_enterprise": "NOT_RUN",
                    "external_effects": "NONE",
                    "target_writes": 0,
                    "production_ready": False,
                },
                "event_chain": chain,
            }
            artifacts["event-chain.json"] = event_chain
            artifacts["summary.json"] = summary
        finally:
            store.close()

    index = _export_pack(Path(output_dir), artifacts)
    return {**summary, "evidence_index": index}
