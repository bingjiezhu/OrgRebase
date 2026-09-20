from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.domain import ObjectState, VersionedObject
from orgrebase.workspace.pilot import (
    EnterpriseQuotePilotPackError,
    enterprise_quote_pilot_run_id,
    load_enterprise_quote_pilot_pack,
)
from orgrebase.workspace.profile_contracts import EnterpriseSeedAdmissionError
from orgrebase.workspace.service import WorkspaceService

ROOT = Path(__file__).resolve().parents[2]
EVERGREEN_PACK = ROOT / "examples" / "enterprise-quote-pilot" / "evergreen"


def _copy_pack(tmp_path: Path) -> Path:
    selected = tmp_path / "enterprise-pack"
    shutil.copytree(EVERGREEN_PACK, selected)
    return selected


def test_evergreen_pack_loads_as_deployable_runtime() -> None:
    runtime = load_enterprise_quote_pilot_pack(EVERGREEN_PACK)

    assert runtime.profile.organization_id == "org:evergreen-industries"
    assert runtime.quote_object_id == "work:quote-blue-harbor"
    assert runtime.default_run_id == "run:workspace:quote-blue-harbor@v1"
    assert runtime.change_order == ("launch_date", "currency")
    assert runtime.snapshot_scope_roots == (
        "claim:product.launch_date",
        "policy:finance.currency",
    )
    assert runtime.source_admission.verdict == "ADMITTED"
    assert runtime.runtime_projection.verdict == "MATCH"
    assert runtime.boundaries["external_writes"] == "DISABLED"
    assert runtime.boundaries["canonical_state_owner"] == ("OrgRebase StateStore and RebaseWorkflow")
    assert runtime.pack_digest.startswith("sha256:")
    assert runtime.pack_id == "pack:evergreen-enterprise-quote"
    assert runtime.pack_revision == "r1"
    assert runtime.adapter_id == "enterprise-quote-v1"
    assert enterprise_quote_pilot_run_id(runtime) == (
        "run:enterprise-pilot:evergreen-industries:"
        f"{runtime.pack_digest.split(':', 1)[1][:16]}@v1"
    )
    assert len(runtime.seed_objects) == 14
    assert runtime.support_fixture.organization_id == runtime.profile.organization_id


def test_default_pack_is_the_exact_retained_pilot_evidence_root() -> None:
    retained = ROOT / "evidence/enterprise-quote-pilot/latest/pack-sealed"
    default_files = {
        path.relative_to(EVERGREEN_PACK).as_posix(): path
        for path in EVERGREEN_PACK.rglob("*")
        if path.is_file()
    }
    retained_files = {
        path.relative_to(retained).as_posix(): path
        for path in retained.rglob("*")
        if path.is_file()
    }

    assert set(default_files) == set(retained_files)
    assert all(
        default_files[relative].read_bytes() == retained_files[relative].read_bytes()
        for relative in default_files
    )
    default_runtime = load_enterprise_quote_pilot_pack(EVERGREEN_PACK)
    retained_runtime = load_enterprise_quote_pilot_pack(retained)
    assert default_runtime.pack_digest == retained_runtime.pack_digest
    assert enterprise_quote_pilot_run_id(default_runtime) == enterprise_quote_pilot_run_id(
        retained_runtime
    )


def test_pilot_state_and_exports_expose_one_read_only_execution_root(
    tmp_path: Path,
) -> None:
    runtime = load_enterprise_quote_pilot_pack(EVERGREEN_PACK)
    run_id = enterprise_quote_pilot_run_id(runtime)
    service = WorkspaceService(
        store_path=tmp_path / "execution-view.sqlite3",
        workflow_run_id=run_id,
        runtime_configuration=runtime,
    )
    try:
        initial_lineage = service.state()["enterprise_data_lineage"]
        assert initial_lineage["status"] == "WAITING_FOR_FORMATION"
        assert initial_lineage["read_model_target_writes"] == 0
        assert initial_lineage["source"]["status"] == "ADMITTED_AND_MATCHED"
        assert initial_lineage["context"]["status"] == "WAITING_FOR_FORMATION"
        assert initial_lineage["quotes"] == {
            "status": "NOT_OBSERVED",
            "current_version": None,
            "versions": [],
        }
        assert all(
            item["status"] == "NOT_OBSERVED"
            for item in initial_lineage["changes"]
        )
        service.form_quote_with_dependency_evidence()
        state = service.state()
        expected_execution = {
            "schema_version": "orgrebase.workspace-execution-view.v1",
            "run_id": run_id,
            "scenario_id": runtime.scenario["id"],
            "mode": "LOCAL_DETERMINISTIC",
            "candidate_runtime": "DETERMINISTIC_DOMAIN_PROVIDERS",
            "canonical_authority": "OrgRebase StateStore and RebaseWorkflow",
            "agentteams": "NOT_RUN",
            "oac_admission": "NOT_USED_IN_THIS_RUN",
            "external_writes": "DISABLED",
        }
        assert {
            key: state["execution"][key] for key in expected_execution
        } == expected_execution
        assert state["execution"]["oac_activation"]["status"] == (
            "NOT_USED_IN_THIS_RUN"
        )
        assert state["dependency_evidence_tool"]["invocation"]["receipt"][
            "workflow_run_id"
        ] == run_id
        for kind in ("launch_date", "currency"):
            preview = service.preview_command(kind)
            preview_change = next(
                item
                for item in service.state()["enterprise_data_lineage"]["changes"]
                if item["kind"] == kind
            )
            assert preview_change["status"] == "PREVIEWED"
            assert preview_change["preview_evidence_status"] == "VMRC_EXACT_BOUND"
            assert preview_change["candidate_metrics"] == {
                "affected_hard": 1,
                "bounded_unaffected": 2,
                "unknown": 1,
                "human_review": 1,
                "skill_requalification": 0,
            }
            assert preview_change["metrics"] is None
            approval = service.approve_change(
                kind,
                actor_id=runtime.change_owners[kind],
                preview_digest=preview["preview_digest"],
            )
            service.apply_approved_change(
                kind,
                approval_digest=approval["approval_digest"],
            )

        final_state = service.state()
        lineage = final_state["enterprise_data_lineage"]
        assert lineage["schema_version"] == (
            "orgrebase.enterprise-data-lineage-view.v1"
        )
        # This direct Pack run deliberately did not consume an OAC activation
        # binding, so the complete Quote/Rebase data chain remains IN_PROGRESS
        # instead of being promoted to the required-OAC READY claim.
        assert lineage["status"] == "IN_PROGRESS"
        assert lineage["run_id"] == run_id
        assert lineage["read_model_target_writes"] == 0
        assert lineage["data_class"] == "SYNTHETIC_FIXTURE"
        assert lineage["source"]["component_count"] == 5
        assert tuple(
            item["kind"] for item in lineage["source"]["components"]
        ) == ("DOMAIN", "KNOWLEDGE", "AUTHORITY", "CAPABILITY", "DEPENDENCY")
        assert len(lineage["source"]["values"]) == 9
        assert lineage["source"]["task"]["task_ref"] == (
            "task:quote-blue-harbor"
        )
        assert lineage["source"]["task"]["customer_id"] == (
            "customer:blue-harbor"
        )
        assert lineage["oac"]["source_admission_verdict"] == "ADMITTED"
        assert lineage["oac"]["runtime_projection_verdict"] == "MATCH"
        assert lineage["oac"]["activation_status"] == "NOT_USED_IN_THIS_RUN"
        assert lineage["oac"]["canonical_target_writes"] == 0
        assert tuple(
            item["kind"] for item in lineage["oac"]["component_bindings"]
        ) == ("DOMAIN", "KNOWLEDGE", "AUTHORITY", "CAPABILITY", "DEPENDENCY")

        context = lineage["context"]
        assert context["status"] == "READY"
        assert context["authority"] == (
            "ORGREBASE_CONTROL_PLANE_TASK_CONTEXT_COMPILER"
        )
        assert context["slot_count"] == 8
        assert context["actor_count"] == 5
        assert context["domain_actor_count"] == 4
        actors = {item["actor_id"]: item for item in context["actors"]}
        assert actors["product-steward"]["included_slot_ids"] == [
            "product_plan",
            "launch_date",
            "data_residency",
        ]
        assert actors["legal-steward"]["included_slot_ids"] == [
            "notice_required"
        ]
        assert actors["finance-steward"]["included_slot_ids"] == [
            "currency",
            "price_band",
        ]
        assert actors["gtm-steward"]["included_slot_ids"] == [
            "partner_terms",
            "quote_compose_skill",
        ]
        assert actors["workspace-renderer"]["included_count"] == 8
        assert actors["workspace-renderer"]["excluded_count"] == 0
        assert actors["workspace-renderer"]["excluded_reason_counts"] == {}
        assert actors["legal-steward"]["excluded_reason_counts"] == {
            "OTHER_AUTHORITY_DOMAIN": 7,
        }
        for actor in actors.values():
            assert sum(actor["excluded_reason_counts"].values()) == actor["excluded_count"]
        assert "source:legal.customer-contract" not in json.dumps(context)
        assert "internal_cost_floor" not in json.dumps(context)

        assert lineage["quotes"]["status"] == "READY"
        quote_versions = lineage["quotes"]["versions"]
        assert [item["version"] for item in quote_versions] == ["v1", "v2", "v3"]
        assert [item["payload"]["launch_date"] for item in quote_versions] == [
            "2026-10-01",
            "2026-10-15",
            "2026-10-15",
        ]
        assert [item["payload"]["currency"] for item in quote_versions] == [
            "USD",
            "USD",
            "EUR",
        ]

        changes_by_kind = {item["kind"]: item for item in lineage["changes"]}
        expected_changes = {
            "launch_date": {
                "base": "2026-10-01",
                "proposed": "2026-10-15",
                "owner": "human:evergreen-product-owner",
                "predecessor": "work:quote-blue-harbor@v1",
                "successor": "work:quote-blue-harbor@v2",
            },
            "currency": {
                "base": "USD",
                "proposed": "EUR",
                "owner": "human:evergreen-finance-owner",
                "predecessor": "work:quote-blue-harbor@v2",
                "successor": "work:quote-blue-harbor@v3",
            },
        }
        for kind, expected in expected_changes.items():
            change = changes_by_kind[kind]
            assert change["status"] == "COMPLETED"
            assert change["delta"]["base_value"] == expected["base"]
            assert change["delta"]["proposed_value"] == expected["proposed"]
            assert change["owner_id"] == expected["owner"]
            assert change["approval_status"] == "APPROVED"
            assert change["approval_actor_id"] == expected["owner"]
            assert change["predecessor_ref"] == expected["predecessor"]
            assert change["successor_ref"] == expected["successor"]
            assert change["receipt_status"] == "COMPLETED"
            assert change["workspace_receipt_status"] == "COMPLETED"
            assert change["receipt_digest"].startswith("sha256:")
            assert change["workspace_receipt_digest"].startswith("sha256:")
            assert change["metrics"] == {
                "facts_changed": 1,
                "work_items_rebased": 1,
                "bounded_unaffected": 2,
                "unknown": 1,
                "skills_requalified": 0,
                "unauthorized_disclosures": 0,
                "false_invalidations": 0,
            }
        serialized_lineage = json.dumps(lineage, ensure_ascii=False, sort_keys=True)
        assert "raw_private_value" not in serialized_lineage
        assert "internal_cost_floor" not in serialized_lineage
        assert "85000" not in serialized_lineage

        activity = final_state["execution_activity"]
        assert activity["schema_version"] == "orgrebase.workspace-execution-activity.v1"
        assert activity["run_id"] == run_id
        assert activity["row_count"] == len(activity["rows"])
        assert activity["read_model_target_writes"] == 0
        assert activity["canonical_write_rows"] == 3
        assert {row["plane"] for row in activity["rows"]} >= {
            "ADMISSION",
            "FORMATION",
            "TOOL",
            "ADVISORY",
            "HANDOFF",
            "HUMAN",
            "CONTROL",
        }
        assert all(row["run_id"] == run_id for row in activity["rows"])
        assert all(row["source_run_id"] == run_id for row in activity["rows"])
        assert all(
            row["target_writes"] == 0
            for row in activity["rows"]
            if row["permission"] in {"CANDIDATE_ONLY", "READ_ONLY", "APPROVAL"}
        )
        for change in final_state["changes"].values():
            assert change["approval"]["binding"]["workflow_run_id"] == run_id
            assert change["outcome"]["outcome"]["rebase_receipt"][
                "workflow_run_id"
            ] == run_id
        tool_run_id = service.export_evidence()["dependency_evidence_tool"][
            "invocation"
        ]["receipt"]["workflow_run_id"]
        assert tool_run_id == state["execution"]["run_id"]
        assert "execution" not in service.export_quote()
        assert "execution" not in service.export_evidence()
    finally:
        service.close()


def test_data_lineage_does_not_promote_a_reference_profile_to_pack_input(
    tmp_path: Path,
) -> None:
    service = WorkspaceService(store_path=tmp_path / "reference-profile.sqlite3")
    try:
        lineage = service.state()["enterprise_data_lineage"]
        assert lineage["status"] == "WAITING_FOR_FORMATION"
        assert lineage["source"]["status"] == "UNOBSERVED"
        assert lineage["source"]["pack_ref"] is None
        assert lineage["source"]["pack_digest"] is None
        assert lineage["source"]["values"] == []
        assert lineage["context"]["status"] == "WAITING_FOR_FORMATION"
        assert "manifest_digest" not in lineage["context"]
        assert lineage["quotes"]["status"] == "NOT_OBSERVED"
        assert lineage["read_model_target_writes"] == 0
    finally:
        service.close()


@pytest.mark.parametrize(
    ("receipt_attribute", "non_exact_verdict"),
    (
        ("source_admission", "REJECTED"),
        ("runtime_projection", "MISMATCH"),
    ),
)
def test_data_lineage_source_values_fail_closed_without_both_exact_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt_attribute: str,
    non_exact_verdict: str,
) -> None:
    runtime = load_enterprise_quote_pilot_pack(EVERGREEN_PACK)
    service = WorkspaceService(
        store_path=tmp_path / f"source-fail-closed-{receipt_attribute}.sqlite3",
        runtime_configuration=runtime,
    )
    try:
        admitted = service._enterprise_data_source_view()
        assert admitted["status"] == "ADMITTED_AND_MATCHED"
        assert len(admitted["values"]) == 9

        monkeypatch.setattr(
            service,
            receipt_attribute,
            SimpleNamespace(verdict=non_exact_verdict),
        )
        unobserved = service._enterprise_data_source_view()
        assert unobserved["status"] == "UNOBSERVED"
        assert unobserved["values"] == []
    finally:
        service.close()


def test_data_lineage_fails_closed_when_an_actor_projection_is_missing(
    tmp_path: Path,
) -> None:
    runtime = load_enterprise_quote_pilot_pack(EVERGREEN_PACK)
    service = WorkspaceService(
        store_path=tmp_path / "missing-projection.sqlite3",
        workflow_run_id=enterprise_quote_pilot_run_id(runtime),
        runtime_configuration=runtime,
    )
    try:
        service.form_quote_with_dependency_evidence()
        ready_context = service.state()["enterprise_data_lineage"]["context"]
        assert ready_context["status"] == "READY"
        missing_ref = ready_context["actors"][0]["projection_ref"]
        with service.store.transaction() as connection:
            connection.execute(
                "DELETE FROM artifacts WHERE artifact_id=?",
                (missing_ref,),
            )

        lineage = service.state()["enterprise_data_lineage"]
        assert lineage["status"] == "IN_PROGRESS"
        assert lineage["context"]["status"] == "UNOBSERVED"
        assert lineage["context"]["actors"] == []
        assert lineage["read_model_target_writes"] == 0
    finally:
        service.close()


def test_evergreen_pack_runs_existing_governed_loop_without_code_edits(
    tmp_path: Path,
) -> None:
    runtime = load_enterprise_quote_pilot_pack(EVERGREEN_PACK)
    result = WorkspaceService.run_explicit_local_product_loop(
        tmp_path / "workspace.sqlite3",
        allow_scripted_approval=True,
        runtime_configuration=runtime,
        probe_wrong_owner=True,
    )

    quote = result["final_quote"]
    assert quote.id == "work:quote-blue-harbor"
    assert quote.version == "v3"
    assert quote.payload["launch_date"] == "2026-10-15"
    assert quote.payload["currency"] == "EUR"
    assert result["restart"]["closed_stage"] == "CURRENT"
    assert result["restart"]["reopened_stage"] == "CURRENT"
    assert result["event_chain"]["status"] == "PASS"
    assert result["approval_commands"]["launch_date"]["wrong_owner_probe"]["status"] == ("REJECTED")
    assert result["approval_commands"]["currency"]["wrong_owner_probe"]["status"] == ("REJECTED")
    for kind in runtime.change_order:
        stale = result["approval_commands"][kind]["stale_approval_probe"]
        assert stale["status"] == "REJECTED"
        assert stale["error_code"] == "WORKSPACE_APPROVAL_DIGEST_MISMATCH"
        assert stale["before_counts"] == stale["after_counts"]
        assert stale["canonical_target_writes"] == 0
    assert result["boundaries"]["deployment_maturity"] == "SINGLE_ENTERPRISE_PILOT"


def test_enterprise_pack_drives_browser_api_with_declared_owners(tmp_path: Path) -> None:
    runtime = load_enterprise_quote_pilot_pack(EVERGREEN_PACK)
    workspace = WorkspaceService(
        store_path=tmp_path / "browser.sqlite3",
        runtime_configuration=runtime,
    )
    try:
        client = TestClient(create_app(workspace_service=workspace))
        readiness = client.get("/readyz")
        assert readiness.status_code == 200
        assert readiness.json()["enterprise_pack_digest"] == runtime.pack_digest

        state = client.get("/api/workspace/state").json()
        assert state["scenario"]["organization_id"] == "org:evergreen-industries"
        assert state["stage"] == "EMPTY"
        assert client.post("/api/workspace/form").json()["state"]["stage"] == "CURRENT"

        for kind, expected_stage in (
            ("launch_date", "CURRENT"),
            ("currency", "CURRENT"),
        ):
            preview = client.post(f"/api/workspace/preview/{kind}").json()
            state = preview["state"]
            owner_id = state["actions"]["owner_id"]
            approved = client.post(
                f"/api/workspace/approve/{kind}",
                json={"actor_id": owner_id, "preview_digest": preview["preview_digest"]},
            )
            assert approved.status_code == 200
            approval_digest = approved.json()["approval_digest"]
            applied = client.post(
                f"/api/workspace/apply/{kind}",
                json={"approval_digest": approval_digest},
            )
            assert applied.status_code == 200
            assert applied.json()["state"]["stage"] == expected_stage

        final = client.get("/api/workspace/state").json()
        assert final["quote"]["id"] == "work:quote-blue-harbor"
        assert final["quote"]["version"] == "v3"
    finally:
        workspace.close()


def test_same_database_rejects_different_valid_pack_before_writes(
    tmp_path: Path,
) -> None:
    store = tmp_path / "workspace.sqlite3"
    original = load_enterprise_quote_pilot_pack(EVERGREEN_PACK)
    service = WorkspaceService(store_path=store, runtime_configuration=original)
    try:
        service.form_quote_with_dependency_evidence()
    finally:
        service.close()

    changed_pack = _copy_pack(tmp_path)
    manifest_path = changed_pack / "pack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scenario"]["label"] = "A different valid deployment declaration"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    changed = load_enterprise_quote_pilot_pack(changed_pack)
    assert changed.profile.digest == original.profile.digest
    assert changed.pack_digest != original.pack_digest

    with sqlite3.connect(store) as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("domain_events", "artifacts", "object_versions", "current_pointers")
        }
    with pytest.raises(RuntimeError, match="PILOT_PACK_STORE_BINDING_MISMATCH"):
        WorkspaceService(store_path=store, runtime_configuration=changed)
    with sqlite3.connect(store) as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
    assert after == before


def test_pilot_approval_binds_pack_run_and_quote_predecessor(tmp_path: Path) -> None:
    runtime = load_enterprise_quote_pilot_pack(EVERGREEN_PACK)
    store = tmp_path / "approval-binding.sqlite3"
    run_id = "run:test:evergreen-approval-binding@v1"
    service = WorkspaceService(
        store_path=store,
        workflow_run_id=run_id,
        runtime_configuration=runtime,
    )
    try:
        service.form_quote_with_dependency_evidence()
        preview = service.preview_command("launch_date")
        approval = service.approve_change(
            "launch_date",
            actor_id=runtime.change_owners["launch_date"],
            preview_digest=preview["preview_digest"],
        )
        binding = approval["binding"]
        assert binding["workflow_run_id"] == run_id
        assert binding["pack_digest"] == runtime.pack_digest
        assert binding["profile_digest"] == runtime.profile.digest
        assert binding["predecessor_ref"] == "work:quote-blue-harbor@v1"

        current = service.current_quote()
        drifted_payload = current.model_dump(mode="json", exclude={"digest"})
        drifted_payload.update({"version": "v99", "state": ObjectState.CURRENT})
        drifted = VersionedObject.model_validate(drifted_payload)
        with service.store.transaction() as connection:
            service.store.insert_version(connection, drifted, make_current=True)
        before = {
            table: service.store.connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in ("domain_events", "artifacts", "object_versions", "current_pointers")
        }
        with pytest.raises(RuntimeError, match="WORKSPACE_APPROVAL_PREDECESSOR_STALE"):
            service.apply_approved_change(
                "launch_date",
                approval_digest=approval["approval_digest"],
            )
        after = {
            table: service.store.connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in before
        }
        assert after == before
    finally:
        service.close()


def test_evergreen_pack_has_no_reference_fixture_identity() -> None:
    runtime = load_enterprise_quote_pilot_pack(EVERGREEN_PACK)
    serialized = json.dumps(
        {
            "profile": runtime.profile.model_dump(mode="json"),
            "universe": runtime.universe.model_dump(mode="json"),
            "scenario": dict(runtime.scenario),
            "quote_object_id": runtime.quote_object_id,
            "task_receipt_id": runtime.task_receipt_id,
            "default_run_id": runtime.default_run_id,
            "graph_snapshot_id": runtime.graph_snapshot_id,
        },
        sort_keys=True,
    ).lower()

    assert "northstar" not in serialized
    assert "acme" not in serialized


def test_tampered_component_bytes_fail_closed(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    selected = pack / "components" / "knowledge.json"
    original = selected.read_text(encoding="utf-8")
    selected.write_text(f"{original} ", encoding="utf-8")

    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="SOURCE_ROOT_DECLARED_DIGEST_MISMATCH",
    ):
        load_enterprise_quote_pilot_pack(pack)


def test_unknown_pack_field_is_rejected(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    selected = pack / "pack.json"
    value = json.loads(selected.read_text(encoding="utf-8"))
    value["implicit_trust"] = True
    selected.write_text(json.dumps(value, indent=2), encoding="utf-8")

    with pytest.raises(
        EnterpriseQuotePilotPackError,
        match="PILOT_PACK_SCHEMA_INVALID",
    ):
        load_enterprise_quote_pilot_pack(pack)


def test_component_path_traversal_is_rejected_before_resolution(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    selected = pack / "pack.json"
    value = json.loads(selected.read_text(encoding="utf-8"))
    value["components"][0]["path"] = "../domain.json"
    selected.write_text(json.dumps(value, indent=2), encoding="utf-8")

    with pytest.raises(
        EnterpriseQuotePilotPackError,
        match="PILOT_PACK_SCHEMA_INVALID",
    ):
        load_enterprise_quote_pilot_pack(pack)


def test_component_symlink_is_rejected(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    selected = pack / "components" / "domain.json"
    real = pack / "domain-real.json"
    selected.replace(real)
    selected.symlink_to(real)

    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="SOURCE_DIRECTORY_SYMLINK_FORBIDDEN",
    ):
        load_enterprise_quote_pilot_pack(pack)
