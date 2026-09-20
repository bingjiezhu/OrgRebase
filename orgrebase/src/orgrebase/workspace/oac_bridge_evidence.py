"""Portable evidence export and offline replay for OAC admission."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError, ObjectState, VersionedObject
from orgrebase.store import StateStore
from orgrebase.workspace.models import (
    OACFormationControlRecord,
    OACFormationPreview,
    OACRuntimeApproval,
    OACRuntimeCapsule,
    RuntimeAdmissionReceipt,
)
from orgrebase.workspace.oac_admission import (
    OACRuntimeAdmissionBridge,
    OACRuntimeAdmissionVerifier,
)
from orgrebase.workspace.oac_wire import (
    _CHECKS,
    _OAC_SOURCE_MANIFEST_SCHEMA,
    _ZERO_DIGEST,
    DEFAULT_POLICY_PATH,
    FORMATION_MEDIA_TYPE,
    RECEIPT_MEDIA_TYPE,
    OACBlackBoxCLI,
    _dict,
    _list,
    _str,
    load_runtime_policy,
)

_CASE_IDS = ("BASE", "SPLIT")
_CASE_ARTIFACT_NAMES = ("approval", "capsule", "formation", "preview", "receipt")
_ROOT_ARTIFACT_NAMES = (
    "bridge-demo.json",
    "event-chain.json",
    "policy.json",
)
_EXPECTED_ARTIFACT_PATHS = tuple(
    sorted(
        (
            *_ROOT_ARTIFACT_NAMES,
            *(
            f"cases/{case_id}/{name}.json"
            for case_id in _CASE_IDS
            for name in _CASE_ARTIFACT_NAMES
            ),
        )
    )
)
_EXPECTED_ALL_JSON_PATHS = frozenset((*_EXPECTED_ARTIFACT_PATHS, "evidence-index.json"))


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _clean_managed_json(root: Path) -> None:
    for name in (*_ROOT_ARTIFACT_NAMES, "evidence-index.json"):
        path = root / name
        if path.is_file() or path.is_symlink():
            path.unlink()
    cases_root = root / "cases"
    if cases_root.is_symlink():
        raise IntegrityError("OAC_BRIDGE_MANAGED_PATH_SYMLINK_FORBIDDEN")
    if not cases_root.is_dir():
        return
    for case_root in cases_root.iterdir():
        if case_root.is_symlink():
            raise IntegrityError("OAC_BRIDGE_MANAGED_PATH_SYMLINK_FORBIDDEN")
        if not case_root.is_dir():
            continue
        for name in _CASE_ARTIFACT_NAMES:
            path = case_root / f"{name}.json"
            if path.is_file() or path.is_symlink():
                path.unlink()
        with suppress(OSError):
            case_root.rmdir()
    with suppress(OSError):
        cases_root.rmdir()


def _formation_object(
    case_id: str,
    capsule: OACRuntimeCapsule,
    preview: OACFormationPreview,
    approval: OACRuntimeApproval,
    formation: OACFormationControlRecord,
) -> VersionedObject:
    return VersionedObject(
        id=formation.id,
        version=formation.version,
        kind="OAC_FORMATION_CONTROL_PLANE",
        label=f"OAC {case_id} local Formation",
        domain="runtime-control-plane",
        state=ObjectState.ACTIVE,
        payload=formation.model_dump(mode="json"),
        source_refs=(capsule.digest, preview.digest, approval.digest),
    )


def _verify_exported_event_chain(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    previous = _ZERO_DIGEST
    for expected_sequence, event in enumerate(events, start=1):
        envelope = {
            "sequence_no": expected_sequence,
            "event_type": event.get("event_type"),
            "payload": event.get("payload"),
            "previous_digest": previous,
        }
        expected = sha256_digest(envelope)
        if (
            event.get("sequence_no") != expected_sequence
            or event.get("previous_digest") != previous
            or event.get("event_digest") != expected
        ):
            raise IntegrityError(f"OAC_BRIDGE_EVENT_CHAIN_INVALID:{expected_sequence}")
        previous = expected
    return {"status": "PASS", "events": len(events), "head_digest": previous}


def export_oac_bridge_evidence(
    output_dir: str | Path,
    *,
    cases: Sequence[Mapping[str, Any]],
    event_envelopes: Sequence[Mapping[str, Any]],
    restart: Mapping[str, Any],
    policy: Mapping[str, Any],
    policy_digest: str,
) -> dict[str, Any]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    _clean_managed_json(root)
    case_ids = tuple(str(case["capsule"].case_id) for case in cases)
    if case_ids != _CASE_IDS:
        raise IntegrityError("OAC_BRIDGE_EXPORT_CASE_SET_INVALID")
    source_fingerprints = {case["capsule"].oac_source_fingerprint for case in cases}
    cli_versions = {case["capsule"].oac_cli_version for case in cases}
    if len(source_fingerprints) != 1 or len(cli_versions) != 1:
        raise IntegrityError("OAC_BRIDGE_EXPORT_SOURCE_SNAPSHOT_MISMATCH")
    _write_json(root / "policy.json", policy)
    for case in cases:
        case_id = str(case["capsule"].case_id)
        case_root = root / "cases" / case_id
        for name in ("capsule", "preview", "approval", "formation", "receipt"):
            _write_json(case_root / f"{name}.json", case[name])
    _write_json(root / "event-chain.json", list(event_envelopes))
    summary = {
        "schema_version": "orgrebase.workspace-oac-admission-demo.v1",
        "status": "LOCAL_RUNTIME_ADMISSION_PASS",
        "profile": "oac.runtime-lowering/zero-effect/v0.1",
        "oac_source": {
            "manifest_schema": _OAC_SOURCE_MANIFEST_SCHEMA,
            "cli_version": next(iter(cli_versions)),
            "fingerprint": next(iter(source_fingerprints)),
        },
        "case_count": len(cases),
        "cases": [
            {
                "case_id": case["capsule"].case_id,
                "plan_source": case["capsule"].plan_source,
                "plan_digest": case["capsule"].plan["digest"],
                "capsule_digest": case["capsule"].digest,
                "certificate_digest": case["capsule"].plan_certificate["digest"],
                "runtime_binding_digest": case["capsule"].runtime_binding["digest"],
                "runtime_bundle_digest": case["capsule"].runtime_bundle["digest"],
                "lowering_receipt_digest": case["capsule"].runtime_lowering_receipt[
                    "digest"
                ],
                "topology_digest": case["preview"].topology_digest,
                "step_count": case["preview"].step_count,
                "preview_digest": case["preview"].digest,
                "approval_digest": case["approval"].digest,
                "approval_command_id": case["approval"].command_id,
                "formation_digest": case["formation"].digest,
                "receipt_digest": case["receipt"].digest,
                "status": case["receipt"].status,
                "runtime_control_plane_writes": 1,
                "target_writes": 0,
            }
            for case in cases
        ],
        "policy_digest": policy_digest,
        "restart": dict(restart),
        "boundaries": {
            "maturity": "LOCAL_RUNTIME_ADMISSION_PASS",
            "oac_lowering": "PRODUCED",
            "oac_lowering_runtime_invoked": False,
            "runtime_owner_approval_input": "CONTROLLED_LOCAL_SCRIPTED_COMMAND",
            "external_human_approval": "NOT_RUN",
            "handler_execution": "NOT_RUN",
            "agent_execution": "NOT_RUN",
            "outcome_certificate": "NOT_IMPLEMENTED",
            "real_enterprise": "NOT_RUN",
            "target_writes": 0,
        },
        "event_chain": _verify_exported_event_chain(event_envelopes),
    }
    _write_json(root / "bridge-demo.json", summary)
    artifacts = sorted(
        (path for path in root.rglob("*.json") if path.name != "evidence-index.json"),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    entries = [
        {
            "artifact_ref": path.relative_to(root).as_posix(),
            "sha256": _file_digest(path),
        }
        for path in artifacts
    ]
    if tuple(entry["artifact_ref"] for entry in entries) != _EXPECTED_ARTIFACT_PATHS:
        raise IntegrityError("OAC_BRIDGE_EVIDENCE_PATH_SET_INVALID")
    index = {
        "schema_version": "orgrebase.oac-bridge-evidence-index.v1",
        "entries": entries,
        "pack_digest": sha256_digest(entries),
        "status": "PASS",
    }
    _write_json(root / "evidence-index.json", index)
    return summary


def verify_oac_bridge_evidence(
    directory: str | Path,
    *,
    policy_path: str | Path = DEFAULT_POLICY_PATH,
) -> dict[str, Any]:
    root = Path(directory).resolve()
    index = _dict(
        json.loads((root / "evidence-index.json").read_text(encoding="utf-8")),
        "OAC_BRIDGE_EVIDENCE_INDEX_INVALID",
    )
    if set(index) != {"schema_version", "entries", "pack_digest", "status"} or index.get(
        "schema_version"
    ) != "orgrebase.oac-bridge-evidence-index.v1" or index.get("status") != "PASS":
        raise IntegrityError("OAC_BRIDGE_EVIDENCE_INDEX_INVALID")
    entries = _list(index.get("entries"), "OAC_BRIDGE_EVIDENCE_INDEX_INVALID")
    if len(entries) != len(_EXPECTED_ARTIFACT_PATHS) or index.get(
        "pack_digest"
    ) != sha256_digest(entries):
        raise IntegrityError("OAC_BRIDGE_EVIDENCE_INDEX_DIGEST_MISMATCH")
    seen: set[str] = set()
    for raw_entry in entries:
        entry = _dict(raw_entry, "OAC_BRIDGE_EVIDENCE_ENTRY_INVALID")
        if set(entry) != {"artifact_ref", "sha256"}:
            raise IntegrityError("OAC_BRIDGE_EVIDENCE_ENTRY_INVALID")
        relative = _str(entry.get("artifact_ref"), "OAC_BRIDGE_EVIDENCE_ENTRY_INVALID")
        if relative in seen:
            raise IntegrityError("OAC_BRIDGE_EVIDENCE_DUPLICATE_ENTRY")
        seen.add(relative)
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file() or _file_digest(path) != entry.get("sha256"):
            raise IntegrityError(f"OAC_BRIDGE_EVIDENCE_FILE_INVALID:{relative}")
    if tuple(seen_entry["artifact_ref"] for seen_entry in entries) != _EXPECTED_ARTIFACT_PATHS:
        raise IntegrityError("OAC_BRIDGE_EVIDENCE_PATH_SET_INVALID")
    actual_json = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.json")
        if path.is_file()
    }
    if actual_json != _EXPECTED_ALL_JSON_PATHS:
        raise IntegrityError("OAC_BRIDGE_EVIDENCE_PATH_SET_INVALID")
    policy, policy_digest = load_runtime_policy(policy_path)
    exported_policy = _dict(
        json.loads((root / "policy.json").read_text(encoding="utf-8")),
        "OAC_RUNTIME_POLICY_INVALID",
    )
    if exported_policy != policy or sha256_digest(exported_policy) != policy_digest:
        raise IntegrityError("OAC_BRIDGE_EXPORTED_POLICY_MISMATCH")
    verifier = OACRuntimeAdmissionVerifier(policy, policy_digest)
    summary = _dict(
        json.loads((root / "bridge-demo.json").read_text(encoding="utf-8")),
        "OAC_BRIDGE_SUMMARY_INVALID",
    )
    expected_summary_keys = {
        "schema_version",
        "status",
        "profile",
        "oac_source",
        "case_count",
        "cases",
        "policy_digest",
        "restart",
        "boundaries",
        "event_chain",
    }
    if (
        set(summary) != expected_summary_keys
        or summary.get("schema_version") != "orgrebase.workspace-oac-admission-demo.v1"
        or summary.get("status") != "LOCAL_RUNTIME_ADMISSION_PASS"
        or summary.get("profile") != "oac.runtime-lowering/zero-effect/v0.1"
        or summary.get("case_count") != 2
        or summary.get("policy_digest") != policy_digest
        or summary.get("boundaries")
        != {
            "maturity": "LOCAL_RUNTIME_ADMISSION_PASS",
            "oac_lowering": "PRODUCED",
            "oac_lowering_runtime_invoked": False,
            "runtime_owner_approval_input": "CONTROLLED_LOCAL_SCRIPTED_COMMAND",
            "external_human_approval": "NOT_RUN",
            "handler_execution": "NOT_RUN",
            "agent_execution": "NOT_RUN",
            "outcome_certificate": "NOT_IMPLEMENTED",
            "real_enterprise": "NOT_RUN",
            "target_writes": 0,
        }
    ):
        raise IntegrityError("OAC_BRIDGE_SUMMARY_STATUS_INVALID")
    receipt_digests: set[str] = set()
    topology_by_case: dict[str, str] = {}
    case_records: dict[
        str,
        tuple[
            OACRuntimeCapsule,
            OACFormationPreview,
            OACRuntimeApproval,
            OACFormationControlRecord,
            RuntimeAdmissionReceipt,
            VersionedObject,
        ],
    ] = {}
    summary_cases = _list(summary.get("cases"), "OAC_BRIDGE_SUMMARY_CASES_INVALID")
    if tuple(
        _dict(item, "OAC_BRIDGE_SUMMARY_CASE_INVALID").get("case_id")
        for item in summary_cases
    ) != _CASE_IDS:
        raise IntegrityError("OAC_BRIDGE_SUMMARY_CASES_INVALID")
    for raw_summary_case in summary_cases:
        summary_case = _dict(raw_summary_case, "OAC_BRIDGE_SUMMARY_CASE_INVALID")
        case_id = _str(summary_case.get("case_id"), "OAC_BRIDGE_CASE_ID_INVALID")
        case_root = root / "cases" / case_id
        capsule = OACRuntimeCapsule.model_validate(
            json.loads((case_root / "capsule.json").read_text(encoding="utf-8"))
        )
        preview = OACFormationPreview.model_validate(
            json.loads((case_root / "preview.json").read_text(encoding="utf-8"))
        )
        approval = OACRuntimeApproval.model_validate(
            json.loads((case_root / "approval.json").read_text(encoding="utf-8"))
        )
        formation = OACFormationControlRecord.model_validate(
            json.loads((case_root / "formation.json").read_text(encoding="utf-8"))
        )
        receipt = RuntimeAdmissionReceipt.model_validate(
            json.loads((case_root / "receipt.json").read_text(encoding="utf-8"))
        )
        verified = verifier.verify(capsule)
        formation_object = _formation_object(
            case_id,
            capsule,
            preview,
            approval,
            formation,
        )
        expected_suffix = str(capsule.runtime_bundle["digest"]).removeprefix("sha256:")[:20]
        expected_summary_case = {
            "case_id": case_id,
            "plan_source": capsule.plan_source,
            "plan_digest": capsule.plan["digest"],
            "capsule_digest": capsule.digest,
            "certificate_digest": capsule.plan_certificate["digest"],
            "runtime_binding_digest": capsule.runtime_binding["digest"],
            "runtime_bundle_digest": capsule.runtime_bundle["digest"],
            "lowering_receipt_digest": capsule.runtime_lowering_receipt["digest"],
            "topology_digest": preview.topology_digest,
            "step_count": preview.step_count,
            "preview_digest": preview.digest,
            "approval_digest": approval.digest,
            "approval_command_id": approval.command_id,
            "formation_digest": formation.digest,
            "receipt_digest": receipt.digest,
            "status": receipt.status,
            "runtime_control_plane_writes": 1,
            "target_writes": 0,
        }
        if (
            summary_case != expected_summary_case
            or preview.id
            != f"oac-formation-preview:{case_id.lower()}:{capsule.digest.removeprefix('sha256:')[:20]}"
            or preview.capsule_digest != capsule.digest
            or preview.policy_digest != policy_digest
            or preview.plan_digest != capsule.plan["digest"]
            or preview.certificate_digest != capsule.plan_certificate["digest"]
            or preview.runtime_binding_digest != capsule.runtime_binding["digest"]
            or preview.runtime_bundle_digest != capsule.runtime_bundle["digest"]
            or preview.lowering_receipt_digest
            != capsule.runtime_lowering_receipt["digest"]
            or preview.runtime_owner_id != verified["runtime_owner_id"]
            or preview.topology_digest != verified["topology_digest"]
            or preview.obligation_contract_digest
            != verified["obligation_contract_digest"]
            or preview.step_count != verified["step_count"]
            or preview.work_unit_refs != verified["work_unit_refs"]
            or approval.preview_ref != preview.id
            or approval.preview_digest != preview.digest
            or approval.capsule_digest != capsule.digest
            or approval.runtime_binding_digest != preview.runtime_binding_digest
            or approval.runtime_owner_id != preview.runtime_owner_id
            or approval.target_writes != 0
            or formation.id != f"oac-formation:{case_id.lower()}:{expected_suffix}"
            or formation.capsule_digest != capsule.digest
            or formation.preview_digest != preview.digest
            or formation.approval_digest != approval.digest
            or formation.plan_digest != preview.plan_digest
            or formation.runtime_binding_digest != preview.runtime_binding_digest
            or formation.runtime_bundle_digest != preview.runtime_bundle_digest
            or formation.topology_digest != preview.topology_digest
            or formation.work_unit_refs != preview.work_unit_refs
            or formation.activated_at != approval.approved_at
            or formation.target_writes != 0
            or receipt.id
            != f"oac-runtime-admission-receipt:{case_id.lower()}:{expected_suffix}"
            or receipt.capsule_digest != capsule.digest
            or receipt.preview_digest != preview.digest
            or receipt.approval_digest != approval.digest
            or receipt.formation_ref != f"{formation.id}@{formation.version}"
            or receipt.formation_digest != formation.digest
            or receipt.plan_digest != preview.plan_digest
            or receipt.certificate_digest != preview.certificate_digest
            or receipt.runtime_binding_digest != preview.runtime_binding_digest
            or receipt.runtime_bundle_digest != preview.runtime_bundle_digest
            or receipt.lowering_receipt_digest != preview.lowering_receipt_digest
            or receipt.policy_digest != policy_digest
            or receipt.checked != _CHECKS
            or receipt.status != "LOCAL_RUNTIME_ADMISSION_PASS"
            or receipt.admitted_at != approval.approved_at
            or receipt.target_writes != 0
            or receipt.runtime_control_plane_writes != 1
            or receipt.lowering_status != "PRODUCED"
            or receipt.lowering_runtime_invoked is not False
            or formation_object.payload.get("digest") != formation.digest
        ):
            raise IntegrityError(f"OAC_BRIDGE_CASE_BINDING_INVALID:{case_id}")
        receipt_digests.add(receipt.digest)
        topology_by_case[case_id] = preview.topology_digest
        case_records[case_id] = (
            capsule,
            preview,
            approval,
            formation,
            receipt,
            formation_object,
        )
    if (
        set(topology_by_case) != {"BASE", "SPLIT"}
        or topology_by_case["BASE"] == topology_by_case["SPLIT"]
    ):
        raise IntegrityError("OAC_BRIDGE_TOPOLOGY_VARIANTS_NOT_PROVEN")
    events = _list(
        json.loads((root / "event-chain.json").read_text(encoding="utf-8")),
        "OAC_BRIDGE_EVENT_CHAIN_INVALID",
    )
    chain = _verify_exported_event_chain(
        tuple(_dict(event, "OAC_BRIDGE_EVENT_INVALID") for event in events)
    )
    if len(events) != len(_CASE_IDS):
        raise IntegrityError("OAC_BRIDGE_EVENT_RECEIPT_COVERAGE_MISMATCH")
    event_receipts: set[str] = set()
    for case_id, raw_event in zip(_CASE_IDS, events, strict=True):
        event = _dict(raw_event, "OAC_BRIDGE_EVENT_INVALID")
        capsule, preview, approval, formation, receipt, _ = case_records[case_id]
        expected_payload = {
            "case_id": case_id,
            "capsule_digest": capsule.digest,
            "preview_digest": preview.digest,
            "approval_digest": approval.digest,
            "formation_ref": f"{formation.id}@{formation.version}",
            "formation_digest": formation.digest,
            "runtime_admission_receipt_digest": receipt.digest,
            "status": "LOCAL_RUNTIME_ADMISSION_PASS",
            "target_writes": 0,
            "handler_execution": "NOT_RUN",
            "agent_execution": "NOT_RUN",
        }
        if (
            event.get("event_type") != "OAC_RUNTIME_FORMATION_ACTIVATED"
            or _dict(event.get("payload"), "OAC_BRIDGE_EVENT_PAYLOAD_INVALID")
            != expected_payload
        ):
            raise IntegrityError(f"OAC_BRIDGE_EVENT_SEMANTICS_INVALID:{case_id}")
        event_receipts.add(receipt.digest)
    if event_receipts != receipt_digests or summary.get("event_chain") != chain:
        raise IntegrityError("OAC_BRIDGE_EVENT_RECEIPT_COVERAGE_MISMATCH")
    restart = _dict(summary.get("restart"), "OAC_BRIDGE_RESTART_INVALID")
    reconstructed_state = {
        record[3].id: record[5].model_dump(mode="json")
        for record in case_records.values()
    }
    expected_state_digest = sha256_digest(reconstructed_state)
    if restart != {
        "store_profile": "FILE_BACKED_SQLITE",
        "formation_count": len(case_records),
        "receipt_count_after_reopen": len(receipt_digests),
        "state_digest_before_close": expected_state_digest,
        "state_digest_after_reopen": expected_state_digest,
    }:
        raise IntegrityError("OAC_BRIDGE_RESTART_INVALID")
    source = _dict(summary.get("oac_source"), "OAC_BRIDGE_SOURCE_SUMMARY_INVALID")
    source_values = {
        (record[0].oac_cli_version, record[0].oac_source_fingerprint)
        for record in case_records.values()
    }
    if len(source_values) != 1:
        raise IntegrityError("OAC_BRIDGE_SOURCE_SUMMARY_INVALID")
    cli_version, source_fingerprint = next(iter(source_values))
    if source != {
        "manifest_schema": _OAC_SOURCE_MANIFEST_SCHEMA,
        "cli_version": cli_version,
        "fingerprint": source_fingerprint,
    }:
        raise IntegrityError("OAC_BRIDGE_SOURCE_SUMMARY_INVALID")
    return {
        "status": "PASS",
        "maturity": "LOCAL_RUNTIME_ADMISSION_PASS",
        "cases": len(receipt_digests),
        "entries": len(entries),
        "event_chain_head": chain["head_digest"],
        "target_writes": 0,
        "runtime_owner_approval_input": "CONTROLLED_LOCAL_SCRIPTED_COMMAND",
        "external_human_approval": "NOT_RUN",
        "handler_execution": "NOT_RUN",
        "agent_execution": "NOT_RUN",
        "outcome_certificate": "NOT_IMPLEMENTED",
        "real_enterprise": "NOT_RUN",
    }


def run_oac_admission_demo(
    output_dir: str | Path,
    *,
    oac_root: str | Path | None = None,
    policy_path: str | Path = DEFAULT_POLICY_PATH,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    cli = OACBlackBoxCLI(oac_root)
    policy, policy_digest = load_runtime_policy(policy_path)
    with tempfile.TemporaryDirectory(prefix="orgrebase-oac-admission-") as raw:
        database = Path(raw) / "workspace.sqlite"
        store = StateStore(database)
        cases: list[dict[str, Any]] = []
        try:
            bridge = OACRuntimeAdmissionBridge(store, policy_path=policy_path)
            for case_id in ("BASE", "SPLIT"):
                capsule = cli.build_capsule(case_id)
                preview = bridge.prepare(capsule)
                approval = bridge.approve(
                    preview,
                    actor_id=preview.runtime_owner_id,
                    preview_digest=preview.digest,
                    command_id=f"demo-{case_id.lower()}-001",
                    approved_at="2026-08-25T00:00:00Z",
                )
                receipt = bridge.apply(capsule, preview, approval)
                formation = OACFormationControlRecord.model_validate(
                    store.load_artifact(
                        receipt.formation_ref.rsplit("@", 1)[0], FORMATION_MEDIA_TYPE
                    ).payload
                )
                cases.append(
                    {
                        "capsule": capsule,
                        "preview": preview,
                        "approval": approval,
                        "formation": formation,
                        "receipt": receipt,
                    }
                )
            before = {
                case["formation"].id: store.get_object(case["formation"].id).model_dump(mode="json")
                for case in cases
            }
            before_digest = sha256_digest(before)
        finally:
            store.close()
        reopened = StateStore(database)
        try:
            after = {
                case["formation"].id: reopened.get_object(case["formation"].id).model_dump(mode="json")
                for case in cases
            }
            after_digest = sha256_digest(after)
            if before_digest != after_digest:
                raise IntegrityError("OAC_BRIDGE_SQLITE_REOPEN_STATE_MISMATCH")
            for case in cases:
                reopened.load_artifact(case["receipt"].id, RECEIPT_MEDIA_TYPE)
            chain = reopened.verify_event_chain()
            if chain["status"] != "PASS":
                raise IntegrityError("OAC_BRIDGE_EVENT_CHAIN_INVALID")
            events = reopened.event_envelopes()
        finally:
            reopened.close()
    restart = {
        "store_profile": "FILE_BACKED_SQLITE",
        "formation_count": len(cases),
        "receipt_count_after_reopen": len(cases),
        "state_digest_before_close": before_digest,
        "state_digest_after_reopen": after_digest,
    }
    summary = export_oac_bridge_evidence(
        output,
        cases=cases,
        event_envelopes=events,
        restart=restart,
        policy=policy,
        policy_digest=policy_digest,
    )
    verify_oac_bridge_evidence(output, policy_path=policy_path)
    return summary
