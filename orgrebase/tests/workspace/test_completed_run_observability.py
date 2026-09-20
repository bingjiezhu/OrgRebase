from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import _workspace_current_run_archive_view, create_app
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.completed_run_observability import (
    CLAIM_BOUNDARY,
    EVIDENCE_CLASS,
    LAYER_ORDER,
    LAYER_STATUSES,
    _projection,
    build_completed_run_observability_from_trusted_state,
    verify_completed_run_observability_against_trusted_state,
)

ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = ROOT / "evidence/golden-competition/latest/pilot/state.json"


class _WorkspaceStub:
    def __init__(self, state: dict[str, Any]) -> None:
        self.store_path = ":memory:"
        self._state = state
        self.state_calls = 0

    def state(self) -> dict[str, Any]:
        self.state_calls += 1
        return self._state


def _state() -> dict[str, Any]:
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _archive(state: dict[str, Any]) -> dict[str, Any]:
    return _workspace_current_run_archive_view(_WorkspaceStub(state))


def _seal(record: dict[str, Any]) -> None:
    record["digest"] = sha256_digest(
        {key: value for key, value in record.items() if key != "digest"}
    )


def _valid() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    state = _state()
    archive = _archive(state)
    view = build_completed_run_observability_from_trusted_state(state, archive)
    assert view["status"] == "PROJECTED", view["failures"]
    return state, archive, view


def _candidate_with_binding(
    view: dict[str, Any], **updates: Any
) -> dict[str, Any]:
    candidate = copy.deepcopy(view)
    binding = candidate["completion_binding"]
    binding.update(updates)
    _seal(binding)
    otlp, receipt = _projection(binding)
    candidate.update(
        run_id=binding["run_id"],
        completion_binding=binding,
        otlp=otlp,
        projection_receipt=receipt,
    )
    return candidate


def _span_attributes(span: Mapping[str, Any]) -> dict[str, Any]:
    return {
        item["key"]: next(iter(item["value"].values()))
        for item in span["attributes"]
    }


def test_pending_has_no_projection_before_quote_v3() -> None:
    state = _state()
    state["stage"] = "QUOTE_V2"
    view = build_completed_run_observability_from_trusted_state(state, _archive(state))

    assert view["status"] == "PENDING"
    assert view["completion_binding"] is view["otlp"] is view["projection_receipt"] is None


def test_archived_run_binds_admitted_lineage_and_exact_seven_layers() -> None:
    state, archive, first = _valid()
    second = build_completed_run_observability_from_trusted_state(state, archive)

    assert first == second
    assert verify_completed_run_observability_against_trusted_state(
        state, archive, first
    ) == first
    binding = first["completion_binding"]
    lineage = state["enterprise_data_lineage"]
    assert binding["lineage_digest"] == lineage["digest"]
    assert binding["source_status"] == "ADMITTED_AND_MATCHED"
    assert binding["source_data_class"] == "SYNTHETIC_FIXTURE"
    assert binding["run_id"] == lineage["run_id"] == archive["run_id"]
    assert binding["final_quote_ref"].endswith("@v3")
    assert binding["skill_package_id"].startswith(
        "skill-package:enterprise-quote-compose@"
    )
    assert len(binding["approval_apply_summaries"]) == 2
    spans = first["otlp"]["traces"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    attributes = [_span_attributes(span) for span in spans]
    assert [item["orgrebase.chain.layer"] for item in attributes] == list(LAYER_ORDER)
    assert [item["orgrebase.chain.status"] for item in attributes] == [
        LAYER_STATUSES[layer] for layer in LAYER_ORDER
    ]
    assert all(item["orgrebase.workflow.run_id"] == archive["run_id"] for item in attributes)
    assert first["claim_ceiling"] == EVIDENCE_CLASS
    assert "NOT_REALTIME" in first["claim_boundary"] == CLAIM_BOUNDARY


def test_api_reads_one_trusted_snapshot_without_business_writes() -> None:
    state = _state()
    before = copy.deepcopy(state)
    workspace = _WorkspaceStub(state)
    with TestClient(create_app(workspace_service=workspace)) as client:
        response = client.get("/api/workspace/run-observability")

    assert response.status_code == 200
    assert response.json()["status"] == "PROJECTED"
    assert workspace.state_calls == 1
    assert state == before


@pytest.mark.parametrize(
    ("updates", "attack"),
    [
        ({"run_id": "run:external:self-consistent"}, "self-consistent-external-run"),
        (
            {"skill_package_id": "skill-package:malicious-compose@9.9.9"},
            "malicious-skill",
        ),
        ({"tool_receipt_digest": "sha256:" + "a" * 64}, "arbitrary-tool"),
        ({"agentteams_project_id": "project:arbitrary"}, "arbitrary-at-project"),
        ({"final_quote_ref": "work:quote-blue-harbor@v1"}, "quote-ref-v1"),
        ({"final_quote_digest": "sha256:" + "b" * 64}, "arbitrary-quote-digest"),
    ],
)
def test_self_consistent_output_attacks_fail_against_trusted_rebuild(
    updates: dict[str, Any], attack: str
) -> None:
    state, archive, view = _valid()
    candidate = _candidate_with_binding(view, **updates)

    with pytest.raises(IntegrityError, match="TRUSTED_REBUILD_MISMATCH"):
        verify_completed_run_observability_against_trusted_state(
            state, archive, candidate
        )
    assert attack


def test_early_event_type_tamper_with_unchanged_digest_fails_closed() -> None:
    state = _state()
    archive = _archive(state)
    state["event_chain"]["records"][1]["event_type"] = "OAC_QUOTE_ADAPTATION_BYPASSED"

    view = build_completed_run_observability_from_trusted_state(state, archive)

    assert view["status"] == "INVALID"
    assert view["otlp"] is None
    assert view["failures"] == ["EVENT_TYPE_SEQUENCE_INVALID"]


def test_quote_payload_tamper_with_unchanged_digest_fails_lineage_binding() -> None:
    state = _state()
    archive = _archive(state)
    state["quote"]["payload"]["currency"] = "JPY"

    view = build_completed_run_observability_from_trusted_state(state, archive)

    assert view["status"] == "INVALID"
    assert view["otlp"] is None
    assert view["failures"] == ["FINAL_QUOTE_LINEAGE_BINDING_INVALID"]


def test_source_pass_requires_sealed_same_run_admitted_lineage() -> None:
    state = _state()
    archive = _archive(state)
    state["enterprise_data_lineage"]["source"]["status"] = "UNVERIFIED"
    _seal(state["enterprise_data_lineage"])

    view = build_completed_run_observability_from_trusted_state(state, archive)

    assert view["status"] == "INVALID"
    assert view["otlp"] is None
    assert view["failures"] == ["ENTERPRISE_SOURCE_NOT_ADMITTED_AND_MATCHED"]


@pytest.mark.parametrize(
    "target", ["task", "plan_revision", "receipt", "change", "successor", "quote_version"]
)
def test_malformed_nested_types_and_empty_organization_fail_closed(target: str) -> None:
    state = _state()
    archive = _archive(state)
    if target == "task":
        plan = state["competition_evidence"]["agent_collaboration"]["orchestration_plan"]
        plan["tasks"][0] = 42
        _seal(plan)
        _seal(state["competition_evidence"]["agent_collaboration"])
        _seal(state["competition_evidence"])
    elif target == "plan_revision":
        plan = state["competition_evidence"]["agent_collaboration"]["orchestration_plan"]
        plan["plan_revisions"] = [42]
        _seal(plan)
        _seal(state["competition_evidence"]["agent_collaboration"])
        _seal(state["competition_evidence"])
    elif target == "receipt":
        archive["record"]["selective_rebase_receipts"][0] = 42
    elif target == "change":
        state["enterprise_data_lineage"]["changes"][0] = 42
        _seal(state["enterprise_data_lineage"])
    elif target == "successor":
        state["enterprise_data_lineage"]["changes"][0]["successor_ref"] = 42
        _seal(state["enterprise_data_lineage"])
    else:
        state["enterprise_data_lineage"]["quotes"]["versions"] = [42]
        _seal(state["enterprise_data_lineage"])

    view = build_completed_run_observability_from_trusted_state(state, archive)

    assert view["status"] == "INVALID"
    assert view["otlp"] is None
    assert view["failures"]

    state = _state()
    archive = _archive(state)
    state["task_intake"]["candidate_receipt"]["task_request"]["organization_id"] = ""
    empty_org = build_completed_run_observability_from_trusted_state(state, archive)
    assert empty_org["status"] == "INVALID"
    assert empty_org["failures"] == ["TASK_ORGANIZATION_ID_INVALID"]


def test_real_workspace_api_fails_closed_when_upstream_event_chain_is_corrupt(
    workspace_service,
) -> None:
    workspace_service.store.connection.execute(
        "UPDATE domain_events SET event_digest=? WHERE sequence_no=1",
        ("sha256:" + "f" * 64,),
    )
    workspace_service.store.connection.commit()

    with TestClient(create_app(workspace_service=workspace_service)) as client:
        response = client.get("/api/workspace/run-observability")

    assert response.status_code == 200
    assert response.json()["status"] == "INVALID"
    assert response.json()["otlp"] is None
