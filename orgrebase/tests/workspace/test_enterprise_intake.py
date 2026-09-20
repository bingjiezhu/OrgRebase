from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from orgrebase import cli
from orgrebase.api import create_app
from orgrebase.workspace.api import enterprise_intake_view, workspace_profile_view
from orgrebase.workspace.profile import (
    AdmittedClaimCeiling,
    EnterpriseSeedAdmissionError,
    EnterpriseSeedAdmissionReceipt,
    EnterpriseSeedProfile,
    GrowthLevel,
    admit_enterprise_seed_profile,
    load_enterprise_seed_profile,
    northstar_acme_quote_profile,
    parse_enterprise_seed_profile,
    supplier_shadow_intake_profile,
)
from orgrebase.workspace.service import WorkspaceService

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_FIXTURE = ROOT / "examples/enterprise-seed/northstar-acme-reference-profile.json"
SHADOW_FIXTURE = ROOT / "examples/enterprise-seed/veracier-supplier-shadow-intake-only.json"


def _unsigned(profile: EnterpriseSeedProfile) -> dict[str, object]:
    return profile.model_dump(mode="json", exclude={"digest"})


def _run_main(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["orgrebase", *args])
    cli.main()


def test_two_seed_fixtures_are_deterministic_but_only_reference_is_runtime_compatible() -> None:
    reference = load_enterprise_seed_profile(REFERENCE_FIXTURE)
    shadow = load_enterprise_seed_profile(SHADOW_FIXTURE)
    assert reference == northstar_acme_quote_profile()
    assert shadow == supplier_shadow_intake_profile()

    reference_receipt = admit_enterprise_seed_profile(reference)
    shadow_receipt = admit_enterprise_seed_profile(shadow)
    assert reference_receipt == admit_enterprise_seed_profile(reference)
    assert reference_receipt.seed_ready
    assert reference_receipt.shadow_intake_admissible
    assert reference_receipt.reference_runtime_compatible
    assert shadow_receipt.seed_ready
    assert shadow_receipt.shadow_intake_admissible
    assert not shadow_receipt.reference_runtime_compatible
    assert shadow_receipt.admitted_claim_ceiling == AdmittedClaimCeiling.INTAKE_VALIDATED
    assert tuple(action.gap_ref for action in shadow_receipt.gap_actions) == (
        "gap:veracier:historical-outcome-baseline",
    )
    gap_action = shadow_receipt.gap_actions[0]
    assert gap_action.gap_digest == shadow.gaps[0].digest
    assert gap_action.owner_ref == "human:veracier-procurement-owner"
    assert gap_action.effective_blocks == gap_action.minimum_blocks
    assert gap_action.resolution_gate_ref == "gate:veracier-outcome-assurance@r1"
    assert shadow_receipt.canonical_target_writes == 0


def test_profile_and_nested_contracts_are_immutable() -> None:
    profile = northstar_acme_quote_profile()
    with pytest.raises(ValidationError):
        profile.organization_id = "org:mutated"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        profile.default_task.actor_id = "attacker"  # type: ignore[misc]
    assert isinstance(profile.source_roots, tuple)
    assert isinstance(profile.change_family, tuple)


def test_digest_tamper_is_rejected() -> None:
    payload = northstar_acme_quote_profile().model_dump(mode="json")
    payload["organization_id"] = "org:tampered"
    with pytest.raises(EnterpriseSeedAdmissionError, match="content digest mismatch"):
        parse_enterprise_seed_profile(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("organization_id", "org:attacker"),
        ("scenario_id", "attacker-shadow-scenario"),
    ),
)
def test_model_copy_cached_digest_bypass_is_revalidated_before_any_database_write(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    reference = northstar_acme_quote_profile()
    attacked = reference.model_copy(update={field: value})
    assert attacked.digest == reference.digest
    assert getattr(attacked, field) == value

    with pytest.raises(EnterpriseSeedAdmissionError, match="content digest mismatch"):
        parse_enterprise_seed_profile(attacked)
    with pytest.raises(EnterpriseSeedAdmissionError, match="content digest mismatch"):
        admit_enterprise_seed_profile(attacked)
    with pytest.raises(EnterpriseSeedAdmissionError, match="content digest mismatch"):
        workspace_profile_view(attacked)

    database = tmp_path / f"model-copy-{field}.sqlite"
    with pytest.raises(EnterpriseSeedAdmissionError, match="content digest mismatch"):
        WorkspaceService(store_path=database, profile=attacked)
    assert not database.exists()


def test_missing_governance_is_rejected() -> None:
    payload = _unsigned(northstar_acme_quote_profile())
    payload["governance"] = {
        "admission_authority_refs": [],
        "owner_refs": [],
        "rejection_path_ref": "procedure:reject@r1",
        "unknown_path_ref": "procedure:unknown@r1",
    }
    with pytest.raises(EnterpriseSeedAdmissionError, match="PROFILE_GOVERNANCE_PATH_MISSING"):
        parse_enterprise_seed_profile(payload)


def test_gap_erasure_is_rejected() -> None:
    payload = _unsigned(supplier_shadow_intake_profile())
    payload["gaps"] = []
    with pytest.raises(EnterpriseSeedAdmissionError, match="PROFILE_GAP_ERASURE_DETECTED"):
        parse_enterprise_seed_profile(payload)


def test_effect_ceiling_inflation_is_rejected() -> None:
    payload = _unsigned(supplier_shadow_intake_profile())
    payload["effect_ceiling"] = "EXTERNAL_EFFECTS"
    with pytest.raises(EnterpriseSeedAdmissionError, match="PROFILE_EFFECT_CEILING_INFLATION"):
        parse_enterprise_seed_profile(payload)


def test_submitter_cannot_inject_claim_or_free_form_limitation_into_public_summary() -> None:
    payload = _unsigned(supplier_shadow_intake_profile())
    payload["claim_ceiling"] = "PRODUCTION_READY_FOR_ALL_ENTERPRISES"
    with pytest.raises(EnterpriseSeedAdmissionError, match="extra_forbidden"):
        parse_enterprise_seed_profile(payload)

    payload = _unsigned(supplier_shadow_intake_profile())
    payload["declared_limitation_codes"] = ["PRODUCTION_READY_FOR_ALL_ENTERPRISES"]
    with pytest.raises(EnterpriseSeedAdmissionError, match="enum"):
        parse_enterprise_seed_profile(payload)

    view = workspace_profile_view(supplier_shadow_intake_profile())
    assert "claim_ceiling" not in view["profile"]
    assert "claim_ceiling" not in view["summary"]
    assert view["summary"]["admitted_claim_ceiling"] == "INTAKE_VALIDATED"
    assert view["summary"]["verified_growth_level"] == "G0_DESCRIBE"
    assert not view["summary"]["reference_runtime_compatible"]
    assert set(view["summary"]["limitations"]) >= {
        "INTAKE_ONLY",
        "NO_EXTERNAL_ENTERPRISE_VALIDATION",
        "NO_OUTCOME_ASSURANCE",
    }


def _supplier_with_authority_gap(*, blocks: list[str]) -> dict[str, object]:
    payload = _unsigned(supplier_shadow_intake_profile())
    components = list(payload["components"])  # type: ignore[arg-type]
    payload["components"] = [
        {**component, "completeness": "UNKNOWN"}
        if component["kind"] == "AUTHORITY"
        else component
        for component in components
    ]
    gaps = list(payload["gaps"])  # type: ignore[arg-type]
    gaps.append(
        {
            "id": "gap:veracier:authority-unknown",
            "kind": "UNKNOWN_AUTHORITY",
            "component": "AUTHORITY",
            "path": "authority.approvalPrincipal",
            "description": "Approval principal is not independently resolved.",
            "owner_ref": "human:veracier-risk-owner",
            "resolution_gate_ref": "gate:veracier-authority-resolution@r1",
            "blocks": blocks,
            "status": "OPEN",
        }
    )
    payload["gaps"] = gaps
    return payload


def test_gap_policy_rejects_submitter_underblocking_and_kind_component_mismatch() -> None:
    underblocked = _supplier_with_authority_gap(blocks=["OUTCOME_ASSURANCE"])
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="PROFILE_GAP_BLOCKS_UNDERESTIMATED",
    ):
        parse_enterprise_seed_profile(underblocked)

    mismatch = _supplier_with_authority_gap(
        blocks=["SHADOW_READY", "REFERENCE_RUNTIME", "OUTCOME_ASSURANCE"]
    )
    mismatch["gaps"][-1]["component"] = "KNOWLEDGE"  # type: ignore[index]
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="PROFILE_GAP_KIND_COMPONENT_MISMATCH",
    ):
        parse_enterprise_seed_profile(mismatch)


def test_authority_unknown_policy_blocks_shadow_and_emits_actionable_gap() -> None:
    payload = _supplier_with_authority_gap(
        blocks=["SHADOW_READY", "REFERENCE_RUNTIME", "OUTCOME_ASSURANCE"]
    )
    profile = parse_enterprise_seed_profile(payload)
    receipt = admit_enterprise_seed_profile(profile)
    assert receipt.seed_ready
    assert not receipt.shadow_intake_admissible
    assert not receipt.reference_runtime_compatible
    action = next(
        item for item in receipt.gap_actions if item.gap_ref == "gap:veracier:authority-unknown"
    )
    assert set(action.minimum_blocks) == {
        "SHADOW_READY",
        "REFERENCE_RUNTIME",
        "OUTCOME_ASSURANCE",
    }
    assert action.gap_digest.startswith("sha256:")
    assert action.owner_ref == "human:veracier-risk-owner"
    assert action.resolution_gate_ref == "gate:veracier-authority-resolution@r1"


def test_admission_receipt_rejects_cross_field_inconsistency() -> None:
    receipt = admit_enterprise_seed_profile(northstar_acme_quote_profile())

    bad_digest = receipt.model_dump(mode="json", exclude={"digest"})
    bad_digest["profile_digest"] = "sha256:not-a-digest"
    with pytest.raises(ValueError, match="PROFILE_ADMISSION_DIGEST_INVALID"):
        EnterpriseSeedAdmissionReceipt.model_validate(bad_digest)

    missing_dimension = receipt.model_dump(mode="json", exclude={"digest"})
    missing_dimension["dimensions"] = missing_dimension["dimensions"][:-1]
    with pytest.raises(ValueError, match="PROFILE_ADMISSION_DIMENSIONS_INVALID"):
        EnterpriseSeedAdmissionReceipt.model_validate(missing_dimension)

    impossible_chain = receipt.model_dump(mode="json", exclude={"digest"})
    impossible_chain["shadow_intake_admissible"] = False
    with pytest.raises(ValueError, match="PROFILE_ADMISSION_RUNTIME_WITHOUT_SHADOW"):
        EnterpriseSeedAdmissionReceipt.model_validate(impossible_chain)

    status_mismatch = receipt.model_dump(mode="json", exclude={"digest"})
    status_mismatch["dimensions"][1]["status"] = "HOLD"
    with pytest.raises(ValueError, match="PROFILE_ADMISSION_DIMENSION_STATUS_MISMATCH"):
        EnterpriseSeedAdmissionReceipt.model_validate(status_mismatch)

    claim_mismatch = receipt.model_dump(mode="json", exclude={"digest"})
    claim_mismatch["admitted_claim_ceiling"] = "INTAKE_VALIDATED"
    with pytest.raises(ValueError, match="PROFILE_ADMISSION_CLAIM_INCONSISTENT"):
        EnterpriseSeedAdmissionReceipt.model_validate(claim_mismatch)

    growth_mismatch = receipt.model_dump(mode="json", exclude={"digest"})
    growth_mismatch["verified_growth_level"] = "G0_DESCRIBE"
    with pytest.raises(ValueError, match="PROFILE_ADMISSION_GROWTH_INCONSISTENT"):
        EnterpriseSeedAdmissionReceipt.model_validate(growth_mismatch)

    missing_limitations = receipt.model_dump(mode="json", exclude={"digest"})
    missing_limitations["limitations"] = []
    with pytest.raises(ValueError, match="PROFILE_ADMISSION_LIMITATIONS_MISSING"):
        EnterpriseSeedAdmissionReceipt.model_validate(missing_limitations)


def test_synthetic_data_class_and_requested_growth_cannot_inflate_verified_growth() -> None:
    synthetic_mismatch = _unsigned(supplier_shadow_intake_profile())
    synthetic_mismatch["synthetic"] = False
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="PROFILE_SYNTHETIC_DATA_CLASS_MISMATCH",
    ):
        parse_enterprise_seed_profile(synthetic_mismatch)

    intake_growth = _unsigned(supplier_shadow_intake_profile())
    intake_growth["requested_growth_level"] = "G1_SELECT"
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="PROFILE_INTAKE_ONLY_GROWTH_INFLATION",
    ):
        parse_enterprise_seed_profile(intake_growth)

    non_exact_reference = _unsigned(northstar_acme_quote_profile())
    non_exact_reference["organization_id"] = "org:not-northstar"
    profile = parse_enterprise_seed_profile(non_exact_reference)
    assert profile.requested_growth_level == GrowthLevel.G1_SELECT
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="SOURCE_COMPONENT_ORGANIZATION_MISMATCH",
    ):
        admit_enterprise_seed_profile(profile)


@pytest.mark.parametrize(
    ("field", "replacement", "code"),
    (
        ("organization_id", "", "string_too_short"),
    ),
)
def test_empty_organization_or_change_family_is_rejected(
    field: str,
    replacement: object,
    code: str,
) -> None:
    payload = _unsigned(northstar_acme_quote_profile())
    payload[field] = replacement
    with pytest.raises(EnterpriseSeedAdmissionError, match=code):
        parse_enterprise_seed_profile(payload)


def test_duplicate_source_id_and_unknown_field_are_rejected() -> None:
    duplicate = _unsigned(northstar_acme_quote_profile())
    roots = list(duplicate["source_roots"])  # type: ignore[arg-type]
    roots[1] = {**roots[1], "id": roots[0]["id"]}
    duplicate["source_roots"] = roots
    with pytest.raises(EnterpriseSeedAdmissionError, match="PROFILE_DUPLICATE_SOURCE_ROOT_ID"):
        parse_enterprise_seed_profile(duplicate)

    extra = _unsigned(northstar_acme_quote_profile())
    extra["undeclared_extension"] = True
    with pytest.raises(EnterpriseSeedAdmissionError, match="extra_forbidden"):
        parse_enterprise_seed_profile(extra)

    with pytest.raises(ValueError, match="PROFILE_DUPLICATE_PROFILE_REF"):
        enterprise_intake_view((northstar_acme_quote_profile(), northstar_acme_quote_profile()))

    with pytest.raises(ValueError, match="PROFILE_INTAKE_BATCH_EMPTY"):
        enterprise_intake_view(())


def test_unsupported_handler_fails_before_database_or_canonical_write(tmp_path: Path) -> None:
    payload = _unsigned(northstar_acme_quote_profile())
    payload["runtime_compatibility"] = {
        "mode": "REFERENCE_HANDLER",
        "handler_profile": "some-other-handler-v1",
    }
    unsupported = parse_enterprise_seed_profile(payload)
    receipt = admit_enterprise_seed_profile(unsupported)
    assert not receipt.reference_runtime_compatible

    database = tmp_path / "unsupported.sqlite"
    with pytest.raises(EnterpriseSeedAdmissionError, match="UNSUPPORTED_HANDLER_PROFILE"):
        WorkspaceService(store_path=database, profile=unsupported)
    assert not database.exists()


def test_cross_profile_reopen_cannot_reuse_reference_idempotency_ledger(tmp_path: Path) -> None:
    database = tmp_path / "cross-profile.sqlite"
    reference = WorkspaceService(store_path=database)
    try:
        receipt = reference.form_quote()
        before_events = reference.store.event_records()
        before_idempotency = reference.store.connection.execute(
            "SELECT COUNT(*) FROM idempotency_records"
        ).fetchone()[0]
    finally:
        reference.close()

    with pytest.raises(EnterpriseSeedAdmissionError, match="UNSUPPORTED_HANDLER_PROFILE"):
        WorkspaceService(store_path=database, profile=supplier_shadow_intake_profile())

    reopened = WorkspaceService.reopen(database)
    try:
        assert reopened.form_quote().digest == receipt.digest
        assert reopened.store.event_records() == before_events
        assert reopened.store.connection.execute(
            "SELECT COUNT(*) FROM idempotency_records"
        ).fetchone()[0] == before_idempotency
    finally:
        reopened.close()


def test_service_formation_state_and_evidence_share_one_profile_digest(tmp_path: Path) -> None:
    service = WorkspaceService(store_path=tmp_path / "bound.sqlite")
    try:
        assert service.profile_digest == service.formation.profile_digest
        state = service.state()
        assert state["enterprise_seed_profile"]["digest"] == service.profile_digest
        assert (
            state["enterprise_seed_admission"]["profile_digest"]
            == service.profile_digest
        )
        assert state["enterprise_seed_runtime_binding"] is None
        service.form_quote()
        state = service.state()
        binding = state["enterprise_seed_runtime_binding"]
        assert binding["binding"]["profile"]["digest"] == service.profile_digest
        formation_event = next(
            item
            for item in service.store.event_envelopes()
            if item["event_type"] == "WORKSPACE_TASK_COMMITTED"
        )
        assert (
            formation_event["payload"]["enterprise_seed_profile_digest"]
            == service.profile_digest
        )
        assert (
            formation_event["payload"]["enterprise_seed_binding_artifact_digest"]
            == binding["artifact_digest"]
        )
        exported = service.export_evidence()
        quote_export = service.export_quote()
        assert exported["schema_version"] == "orgrebase.workspace-evidence-export.v2"
        assert quote_export["schema_version"] == "orgrebase.workspace-quote-export.v2"
        assert exported["enterprise_seed_profile"]["digest"] == service.profile_digest
        assert exported["enterprise_seed_admission"]["profile_digest"] == service.profile_digest
        assert (
            exported["enterprise_seed_runtime_binding"]["artifact_digest"]
            == binding["artifact_digest"]
        )
    finally:
        service.close()


def test_profile_and_intake_api_are_zero_write(tmp_path: Path) -> None:
    service = WorkspaceService(store_path=tmp_path / "api.sqlite")
    try:
        before_events = service.store.event_records()
        before_artifacts = service.store.connection.execute(
            "SELECT COUNT(*) FROM artifacts"
        ).fetchone()[0]
        with TestClient(create_app(workspace_service=service)) as client:
            profile_response = client.get("/api/workspace/profile")
            assert profile_response.status_code == 200
            assert profile_response.json()["canonical_target_writes"] == 0

            payload = json.loads(SHADOW_FIXTURE.read_text(encoding="utf-8"))
            intake_response = client.post("/api/workspace/intake", json=payload)
            assert intake_response.status_code == 200
            body = intake_response.json()
            assert body["canonical_target_writes"] == 0
            assert body["receipts"][0]["reference_runtime_compatible"] is False

        assert service.store.event_records() == before_events
        assert service.store.connection.execute(
            "SELECT COUNT(*) FROM artifacts"
        ).fetchone()[0] == before_artifacts
    finally:
        service.close()


def test_default_http_profile_and_intake_never_initialize_workspace_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "read-only" / "workspace.sqlite3"
    monkeypatch.setenv("ORGREBASE_WORKSPACE_DB", str(database))
    application = create_app()
    valid = supplier_shadow_intake_profile().model_dump(mode="json")
    gap_erasure = supplier_shadow_intake_profile().model_dump(
        mode="json", exclude={"digest"}
    )
    gap_erasure["gaps"] = []

    with TestClient(application) as client:
        assert application.state.workspace_service is None
        assert application.state.workspace_store_path is None
        assert not database.exists()

        profile = client.get("/api/workspace/profile")
        assert profile.status_code == 200
        assert profile.json()["canonical_target_writes"] == 0
        assert application.state.workspace_service is None
        assert not database.exists()

        accepted = client.post("/api/workspace/intake", json=valid)
        assert accepted.status_code == 200
        assert accepted.json()["canonical_target_writes"] == 0
        assert application.state.workspace_service is None
        assert not database.exists()

        rejected = client.post("/api/workspace/intake", json=gap_erasure)
        assert rejected.status_code == 422
        assert "PROFILE_GAP_ERASURE_DETECTED" in rejected.json()["detail"]["message"]
        assert application.state.workspace_service is None
        assert application.state.workspace_store_path is None
        assert not database.exists()

    assert application.state.workspace_service is None
    assert not database.exists()


def test_cli_intakes_two_profiles_in_one_zero_write_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "intake.json"
    _run_main(
        monkeypatch,
        "workspace-intake",
        str(REFERENCE_FIXTURE),
        str(SHADOW_FIXTURE),
        "--output",
        str(output),
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["intake_count"] == 2
    assert result["reference_runtime_compatible_count"] == 1
    assert result["canonical_target_writes"] == 0
    assert [item["admitted_claim_ceiling"] for item in result["receipts"]] == [
        "REFERENCE_PROFILE_INTAKE",
        "INTAKE_VALIDATED",
    ]

    profile_output = tmp_path / "profile.json"
    _run_main(
        monkeypatch,
        "workspace-profile",
        "--input",
        str(SHADOW_FIXTURE),
        "--output",
        str(profile_output),
    )
    profile_view = json.loads(profile_output.read_text(encoding="utf-8"))
    assert profile_view["summary"]["profile_id"] == "profile:veracier-supplier-shadow"
    assert not profile_view["admission_receipt"]["reference_runtime_compatible"]
    assert profile_view["canonical_target_writes"] == 0
