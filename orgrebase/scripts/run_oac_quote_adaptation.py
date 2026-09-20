#!/usr/bin/env python3
"""Run and retain the Spec 062 controlled-local OAC adaptation proof.

This producer is allowed to call the product service.  Its sibling verifier is
deliberately black-box: it imports no OrgRebase product module and rechecks the
retained JSON plus the public OAC CLI.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.store import StateStore
from orgrebase.workspace.oac_quote_adaptation import (
    OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
    OACAdaptationDraft,
    OACAdaptationReviewGatePending,
    OACQuoteAdaptationService,
)
from orgrebase.workspace.oac_wire import OACBlackBoxCLI
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.reference_profiles import supplier_shadow_intake_profile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "evidence" / "oac-quote-adaptation" / "latest"
DEFAULT_PACK_ROOT = ROOT / "examples" / "enterprise-quote-pilot" / "evergreen"
DEFAULT_GOLDEN_ROOT = ROOT / "evidence" / "golden-competition" / "latest" / "pilot"
ZERO_DIGEST = "sha256:" + "0" * 64


class OACQuoteAdaptationEvidenceError(RuntimeError):
    """Stable producer failure for an incomplete evidence journey."""


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise OACQuoteAdaptationEvidenceError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _record(value: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(value)
    payload.pop("digest", None)
    return {**payload, "digest": sha256_digest(payload)}


def _golden_summary_path(root: Path) -> Path:
    if root.is_file():
        return root
    direct = root / "summary.json"
    if direct.is_file():
        return direct
    nested = root / "golden-run" / "summary.json"
    if nested.is_file():
        return nested
    raise OACQuoteAdaptationEvidenceError("GOLDEN_SUMMARY_NOT_FOUND")


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(root: Path, generated_at: str) -> dict[str, Any]:
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _file_digest(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().encode())
        if path.is_file() and path != root / "manifest.json"
    ]
    payload = {
        "schema_version": "orgrebase.oac-quote-adaptation-evidence-manifest.v1",
        "status": "CLOSED_WORLD",
        "evidence_class": "VALIDATED_CONTROLLED_LOCAL",
        "generated_at": generated_at,
        "entry_count": len(entries),
        "entries": entries,
        "pack_digest": sha256_digest(entries),
    }
    return _record(payload)


def _iso_from_epoch_ms(epoch_ms: int) -> str:
    return (
        datetime.fromtimestamp(epoch_ms / 1000, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _mutate_org_record(value: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    selected = copy.deepcopy(value)
    selected.update(update)
    return _record(selected)


def _seal_oac_resource(cli: OACBlackBoxCLI, resource: dict[str, Any]) -> dict[str, Any]:
    selected = copy.deepcopy(resource)
    selected["digest"] = None
    with tempfile.TemporaryDirectory(prefix="orgrebase-oac-mutation-") as raw:
        path = Path(raw) / "resource.json"
        _write(path, selected)
        observed = cli.run("digest", str(path))
    digest = observed.get("digest")
    if not isinstance(digest, str):
        raise OACQuoteAdaptationEvidenceError("OAC_MUTATION_DIGEST_MISSING")
    selected["digest"] = digest
    return selected


def _mutation(
    mutation_id: str,
    *,
    mutation_class: str,
    target_kind: str,
    expected_rejection_code: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _record(
        {
            "schema_version": "orgrebase.oac-quote-adaptation-mutation.v1",
            "mutation_id": mutation_id,
            "mutation_class": mutation_class,
            "target_kind": target_kind,
            "expected_rejection_code": expected_rejection_code,
            "payload": payload,
        }
    )


def _build_mutations(
    *,
    draft: dict[str, Any],
    hold: dict[str, Any],
    approval: dict[str, Any],
    source_admission: dict[str, Any],
    parity: dict[str, Any],
    capsule: dict[str, Any],
    veracier_profile_digest: str,
    cli: OACBlackBoxCLI,
) -> dict[str, dict[str, Any]]:
    digest_substitution = _mutate_org_record(
        capsule,
        {"mapping_set_digest": ZERO_DIGEST},
    )

    unknown_erasure = copy.deepcopy(hold)
    unknown_erasure["gaps"] = []
    remapped = []
    for mapping in unknown_erasure["candidate_mappings"]:
        remapped.append(
            _mutate_org_record(
                mapping,
                {"declared_unknowns": [], "reason_codes": []},
            )
        )
    unknown_erasure["candidate_mappings"] = remapped
    unknown_erasure["mapping_set_digest"] = sha256_digest(
        [item["digest"] for item in remapped]
    )
    unknown_erasure = _record(unknown_erasure)

    self_approval = copy.deepcopy(source_admission)
    self_approval["spec"]["decisionAuthorityRef"] = copy.deepcopy(
        self_approval["spec"]["proposerRefs"][0]
    )
    self_approval = _seal_oac_resource(cli, self_approval)

    cross_profile_capsule = _mutate_org_record(
        capsule,
        {"profile_digest": veracier_profile_digest},
    )

    gate = draft["review_gate"]
    stale_epoch = int(gate["not_before_epoch_ms"]) - 1
    stale_approval = _mutate_org_record(
        approval,
        {
            "approved_at_epoch_ms": stale_epoch,
            "approved_at": _iso_from_epoch_ms(stale_epoch),
        },
    )

    parity_omission = copy.deepcopy(parity)
    parity_omission["task_attempts"] = parity_omission["task_attempts"][1:]
    parity_omission["attempt_set_digest"] = sha256_digest(
        [item["digest"] for item in parity_omission["task_attempts"]]
    )
    parity_omission["obligation_coverage_digest"] = sha256_digest(
        {
            obligation: [
                item["task_id"]
                for item in parity_omission["task_attempts"]
                if item["obligation_ref"] == obligation
            ]
            for obligation in (
                "PRODUCT_FACTS",
                "LEGAL_POLICY",
                "FINANCE_POLICY",
                "GTM_COMPOSITION",
                "INDEPENDENT_REVIEW",
            )
        }
    )
    parity_omission["happens_before_digest"] = sha256_digest(
        [
            {
                "task_id": item["task_id"],
                "predecessor_task_refs": item["predecessor_task_refs"],
            }
            for item in parity_omission["task_attempts"]
        ]
    )
    parity_omission = _record(parity_omission)

    return {
        "digest-substitution.json": _mutation(
            "MUT-062-DIGEST-SUBSTITUTION",
            mutation_class="DIGEST_SUBSTITUTION",
            target_kind="OACAdapterCapsule",
            expected_rejection_code="CAPSULE_MAPPING_BINDING_MISMATCH",
            payload=digest_substitution,
        ),
        "unknown-erasure.json": _mutation(
            "MUT-062-UNKNOWN-ERASURE",
            mutation_class="UNKNOWN_ERASURE",
            target_kind="OACAdaptationDraft",
            expected_rejection_code="HOLD_GAPS_REQUIRED",
            payload=unknown_erasure,
        ),
        "self-approval.json": _mutation(
            "MUT-062-SELF-APPROVAL",
            mutation_class="SELF_APPROVAL",
            target_kind="SourceAdmissionReceipt",
            expected_rejection_code="EVOLUTION_SELF_ADMISSION_FORBIDDEN",
            payload=self_approval,
        ),
        "cross-profile-capsule.json": _mutation(
            "MUT-062-CROSS-PROFILE-CAPSULE",
            mutation_class="CROSS_PROFILE_CAPSULE",
            target_kind="OACAdapterCapsule",
            expected_rejection_code="CAPSULE_PROFILE_BINDING_MISMATCH",
            payload=cross_profile_capsule,
        ),
        "stale-approval.json": _mutation(
            "MUT-062-STALE-APPROVAL",
            mutation_class="STALE_APPROVAL",
            target_kind="OACSourceAdmissionApproval",
            expected_rejection_code="APPROVAL_BEFORE_NOT_BEFORE",
            payload=stale_approval,
        ),
        "parity-omission.json": _mutation(
            "MUT-062-PARITY-OMISSION",
            mutation_class="PARITY_OMISSION",
            target_kind="QuoteFormationParityReceipt",
            expected_rejection_code="PARITY_TASK_COUNT_INVALID",
            payload=parity_omission,
        ),
    }


def _archive_existing(output_root: Path) -> None:
    if not output_root.exists():
        return
    archive_root = output_root.parent / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output_root.rename(archive_root / f"{output_root.name}-{timestamp}")


def run_evidence(
    *,
    output_root: Path,
    pack_root: Path,
    oac_root: Path | None,
    golden_root: Path,
    review_buffer_ms: int = 75,
) -> dict[str, Any]:
    if review_buffer_ms < 0 or review_buffer_ms > 1000:
        raise OACQuoteAdaptationEvidenceError("REVIEW_BUFFER_MS_INVALID")
    runtime = load_enterprise_quote_pilot_pack(pack_root)
    veracier = supplier_shadow_intake_profile()
    golden_summary = _load(_golden_summary_path(golden_root))
    generated_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )

    output_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".oac-quote-adaptation-stage-", dir=output_root.parent
    ) as raw_stage, tempfile.TemporaryDirectory(
        prefix="orgrebase-oac-adaptation-runtime-"
    ) as raw_runtime:
        stage = Path(raw_stage)
        runtime_root = Path(raw_runtime)

        evergreen_store = StateStore(runtime_root / "evergreen.sqlite3")
        try:
            service = OACQuoteAdaptationService(
                store=evergreen_store,
                profile=runtime.profile,
                runtime=runtime,
                oac_root=oac_root,
                golden_root=golden_root,
                execution_run_id=(
                    "run:orgrebase:oac-bound:evergreen-quote-adaptation@v1"
                ),
            )
            wait_started = time.monotonic()
            prepared_view = service.prepare(command_id="cmd:oac-adaptation:prepare@v1")
            if prepared_view.get("status") != "OWNER_REVIEW_PENDING":
                raise OACQuoteAdaptationEvidenceError(
                    "EVERGREEN_OWNER_REVIEW_PENDING_REQUIRED"
                )
            draft = OACAdaptationDraft.model_validate(
                {
                    key: prepared_view[key]
                    for key in OACAdaptationDraft.model_fields
                    if key in prepared_view
                }
            ).model_dump(mode="json")
            try:
                service.approve(
                    actor_id=service.human_authority_ref,
                    candidate_digest=str(prepared_view["candidate_digest"]),
                    command_id="cmd:oac-adaptation:early-approve-probe@v1",
                    owner_review_summary_digest=str(
                        prepared_view["owner_review_summary"]["digest"]
                    ),
                    acknowledgements=OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
                )
            except OACAdaptationReviewGatePending as exc:
                early_probe = {
                    "status": "REJECTED",
                    "error_code": exc.code,
                    "remaining_ms": exc.remaining_ms,
                    "canonical_target_writes": 0,
                }
            else:
                raise OACQuoteAdaptationEvidenceError(
                    "EARLY_OWNER_APPROVAL_WAS_NOT_REJECTED"
                )

            gate = draft["review_gate"]
            remaining_ms = max(
                0,
                int(gate["not_before_epoch_ms"]) - int(time.time() * 1000),
            )
            time.sleep((remaining_ms + review_buffer_ms) / 1000)
            ready_view = service.approve(
                actor_id=service.human_authority_ref,
                candidate_digest=str(prepared_view["candidate_digest"]),
                command_id="cmd:oac-adaptation:scripted-owner-approve@v1",
                owner_review_summary_digest=str(
                    prepared_view["owner_review_summary"]["digest"]
                ),
                acknowledgements=OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
            )
            waited_ms = int((time.monotonic() - wait_started) * 1000)
            if ready_view.get("status") != "READY_FOR_ORGREBASE" or waited_ms < 4000:
                raise OACQuoteAdaptationEvidenceError(
                    "EVERGREEN_READY_AFTER_REAL_FOUR_SECOND_WAIT_REQUIRED"
                )
        finally:
            evergreen_store.close()

        veracier_store = StateStore(runtime_root / "veracier.sqlite3")
        try:
            hold_service = OACQuoteAdaptationService(
                store=veracier_store,
                profile=veracier,
                runtime=None,
                oac_root=oac_root,
                golden_root=golden_root,
                execution_run_id="run:orgrebase:oac-bound:veracier-not-started@v1",
            )
            hold_view = hold_service.prepare(command_id="cmd:oac-adaptation:hold@v1")
            if hold_view.get("status") != "HOLD":
                raise OACQuoteAdaptationEvidenceError("VERACIER_HOLD_REQUIRED")
            hold = OACAdaptationDraft.model_validate(
                {
                    key: hold_view[key]
                    for key in OACAdaptationDraft.model_fields
                    if key in hold_view
                }
            ).model_dump(mode="json")
            try:
                hold_service.approve(
                    actor_id=hold_service.human_authority_ref,
                    candidate_digest=str(hold_view["candidate_digest"]),
                    command_id="cmd:oac-adaptation:hold-approval-probe@v1",
                    owner_review_summary_digest="sha256:" + "0" * 64,
                    acknowledgements=OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
                )
            except RuntimeError as exc:
                hold_rejection = _record(
                    {
                        "schema_version": (
                            "orgrebase.oac-quote-adaptation-rejection.v1"
                        ),
                        "status": "REJECTED",
                        "adaptation_run_id": hold["adaptation_run_id"],
                        "profile_digest": hold["profile_digest"],
                        "error_code": str(exc).split(":", 1)[0],
                        "capsule_produced": False,
                        "execution_started": False,
                        "canonical_target_writes": 0,
                    }
                )
            else:
                raise OACQuoteAdaptationEvidenceError(
                    "VERACIER_HOLD_APPROVAL_WAS_NOT_REJECTED"
                )
        finally:
            veracier_store.close()

        approval = dict(ready_view["approval"])
        source_admission = dict(approval["source_admission_receipt"])
        parity = dict(ready_view["quote_formation_parity_receipt"])
        capsule = dict(ready_view["adapter_capsule"])
        activation = dict(ready_view["activation_binding"])
        snapshot = dict(draft["organization_snapshot"])
        demand = dict(draft["organizational_demand"])

        review_observation = _record(
            {
                "schema_version": "orgrebase.oac-review-observation.v1",
                "status": "PASS",
                "adaptation_run_id": draft["adaptation_run_id"],
                "review_mode": "SCRIPTED_CONTROLLED_LOCAL_APPROVAL",
                "real_enterprise_human_review": "NOT_RUN",
                "server_review_duration_ms": gate["review_duration_ms"],
                "observed_elapsed_ms": waited_ms,
                "early_approval_probe": early_probe,
                "approved_actor_id": approval["actor_id"],
                "approved_candidate_digest": approval["candidate_digest"],
                "approved_owner_review_summary_digest": approval[
                    "owner_review_summary_digest"
                ],
                "acknowledgements": approval["acknowledgements"],
                "canonical_target_writes": 0,
            }
        )

        artifact_values = {
            "artifacts/evergreen/adaptation-draft.json": draft,
            "artifacts/evergreen/owner-review-summary.json": dict(
                draft["owner_review_summary"]
            ),
            "artifacts/evergreen/organization-snapshot.json": snapshot,
            "artifacts/evergreen/organizational-demand.json": demand,
            "artifacts/evergreen/review-gate.json": dict(draft["review_gate"]),
            "artifacts/evergreen/review-observation.json": review_observation,
            "artifacts/evergreen/approval.json": approval,
            "artifacts/evergreen/source-admission-receipt.json": source_admission,
            "artifacts/evergreen/quote-formation-parity-receipt.json": parity,
            "artifacts/evergreen/adapter-capsule.json": capsule,
            "artifacts/evergreen/activation-binding.json": activation,
            "artifacts/evergreen/golden-summary.json": golden_summary,
            "artifacts/veracier/hold-draft.json": hold,
            "artifacts/veracier/approval-rejection.json": hold_rejection,
        }
        for relative, value in artifact_values.items():
            _write(stage / relative, value)

        cli = OACBlackBoxCLI(oac_root)
        mutations = _build_mutations(
            draft=draft,
            hold=hold,
            approval=approval,
            source_admission=source_admission,
            parity=parity,
            capsule=capsule,
            veracier_profile_digest=veracier.digest,
            cli=cli,
        )
        for filename, value in mutations.items():
            _write(stage / "mutations" / filename, value)

        summary = _record(
            {
                "schema_version": "orgrebase.oac-quote-adaptation-evidence-summary.v1",
                "status": "PASS",
                "evidence_class": "VALIDATED_CONTROLLED_LOCAL",
                "claim_ceiling": "CONTROLLED_LOCAL_ADAPTATION_MECHANISM_ONLY",
                "generated_at": generated_at,
                "evergreen": {
                    "status": "READY_FOR_ORGREBASE",
                    "adaptation_run_id": draft["adaptation_run_id"],
                    "profile_digest": draft["profile_digest"],
                    "pack_digest": draft["pack_digest"],
                    "mapping_count": 5,
                    "gap_count": 0,
                    "owner_review_summary_digest": draft["owner_review_summary"][
                        "digest"
                    ],
                    "organization_snapshot_digest": snapshot["digest"],
                    "organizational_demand_digest": demand["digest"],
                    "source_admission_receipt_digest": source_admission["digest"],
                    "quote_formation_parity_digest": parity["digest"],
                    "adapter_capsule_digest": capsule["digest"],
                    "activation_binding_digest": activation["digest"],
                    "execution_run_id": activation["execution_run_id"],
                    "bound_execution_started": False,
                },
                "veracier": {
                    "status": "HOLD",
                    "adaptation_run_id": hold["adaptation_run_id"],
                    "profile_digest": hold["profile_digest"],
                    "gap_count": len(hold["gaps"]),
                    "approval": "REJECTED",
                    "capsule_produced": False,
                    "execution_started": False,
                },
                "human_review": {
                    "mode": "SCRIPTED_CONTROLLED_LOCAL_APPROVAL",
                    "real_enterprise_human_review": "NOT_RUN",
                    "minimum_review_duration_ms": 4000,
                    "observed_elapsed_ms": waited_ms,
                    "early_approval": "REJECTED",
                },
                "formation_parity": {
                    "authority": "ORGREBASE_CONTROL_PLANE",
                    "binding_timing": "POST_RUN_SAME_RUN_REPLAY",
                    "logical_obligation_count": 5,
                    "dynamic_task_attempt_count": 7,
                    "golden_run_id": parity["golden_run_id"],
                    "golden_summary_digest": parity["golden_summary_digest"],
                },
                "oac_boundary": {
                    "source_and_demand_validated": True,
                    "source_admission_validated": True,
                    "oac_cli_version": draft["oac_public_validation"][
                        "oac_cli_version"
                    ],
                    "oac_source_fingerprint": draft["oac_public_validation"][
                        "oac_source_fingerprint"
                    ],
                    "oac_plan_produced": False,
                    "oac_plan_certificate_produced": False,
                    "oac_runtime_invoked": False,
                },
                "mutation_count": len(mutations),
                "canonical_target_writes": 0,
                "real_enterprise_connectors": "NOT_RUN",
                "real_enterprise_data": "NOT_RUN",
                "enterprise_uat": "NOT_RUN",
                "production_sla_ha_dr": "NOT_RUN",
            }
        )
        _write(stage / "summary.json", summary)
        _write(stage / "manifest.json", _manifest(stage, generated_at))

        _archive_existing(output_root)
        shutil.copytree(stage, output_root)

    verifier = ROOT / "scripts" / "verify_oac_quote_adaptation.py"
    command = [sys.executable, str(verifier), "--root", str(output_root)]
    if oac_root is not None:
        command.extend(("--oac-root", str(oac_root)))
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise OACQuoteAdaptationEvidenceError(
            "INDEPENDENT_VERIFIER_FAILED:"
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict) or value.get("status") != "PASS":
        raise OACQuoteAdaptationEvidenceError("INDEPENDENT_VERIFIER_DID_NOT_PASS")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--pack-root", type=Path, default=DEFAULT_PACK_ROOT)
    parser.add_argument("--oac-root", type=Path)
    parser.add_argument("--golden-root", type=Path, default=DEFAULT_GOLDEN_ROOT)
    parser.add_argument("--review-buffer-ms", type=int, default=75)
    arguments = parser.parse_args()
    try:
        result = run_evidence(
            output_root=arguments.output_root,
            pack_root=arguments.pack_root,
            oac_root=arguments.oac_root,
            golden_root=arguments.golden_root,
            review_buffer_ms=arguments.review_buffer_ms,
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
