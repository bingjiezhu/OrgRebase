from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from orgrebase.api import _workspace_current_run_archive_view
from orgrebase.workspace.completed_run_observability import (
    build_completed_run_observability_from_trusted_state,
)
from orgrebase.workspace.source_readmission import apply_group, approve_group
from tests.workspace.test_continuous_changes import ReviewClock, apply, make_service, proposal
from tests.workspace.test_source_readmission_groups import command, prepare


@pytest.fixture
def completed_group(tmp_path):
    clock = ReviewClock()
    service = make_service(tmp_path / "group-archive.sqlite", review_clock=clock)
    try:
        detail = prepare(service)
        clock.advance()
        for owner in detail["owners"]:
            approve_group(service, detail["group"]["id"], command(detail, owner["owner_id"]))
        apply_group(service, detail["group"]["id"], command(detail))
        yield service
    finally:
        service.close()


def test_group_archive_counts_owner_approvals_and_quote_successors_separately(completed_group):
    archive = _workspace_current_run_archive_view(completed_group)
    assert archive["status"] == "ARCHIVED", archive["failures"]
    assert archive["schema_version"] == "orgrebase.workspace-current-run-archive-view.v3"
    record = archive["record"]
    assert record["human_approval_count"] == 4
    assert [item["successor_quote_version"] for item in record["selective_rebase_receipts"]] == ["v2", "v3", "v4"]
    group = record["selective_rebase_receipts"][-1]
    assert group["kind"] == "source_readmission_group" and len(group["event_ids"]) == 2
    assert len(group["owner_approvals"]) == 2
    assert group["successor_quote_digest"] == completed_group.current_quote().digest
    state = {**completed_group.state(), **completed_group.completion_history()}
    observability = build_completed_run_observability_from_trusted_state(state, archive)
    assert observability["status"] == "PROJECTED", observability["failures"]
    assert observability["completion_binding"]["approval_count"] == 4
    assert observability["completion_binding"]["rebase_count"] == 3
    assert observability["projection_receipt"]["synthetic_spans_created"] == 0


def test_ordinary_change_after_group_keeps_complete_quote_receipt_lineage(completed_group):
    event = proposal(completed_group, "after-recovery", "product_plan", "Reviewed after recovery")
    completed_group.register_change(event)
    apply(completed_group, event.event_id)
    archive = _workspace_current_run_archive_view(completed_group)
    assert archive["status"] == "ARCHIVED", archive["failures"]
    receipts = archive["record"]["selective_rebase_receipts"]
    assert [item["successor_quote_version"] for item in receipts] == ["v2", "v3", "v4", "v5"]
    assert [item["kind"] for item in receipts[-2:]] == ["source_readmission_group", event.event_id]
    assert receipts[-1]["successor_quote_digest"] == completed_group.current_quote().digest
    state = {**completed_group.state(), **completed_group.completion_history()}
    result = build_completed_run_observability_from_trusted_state(state, archive)
    assert result["status"] == "PROJECTED", result["failures"]
    assert result["completion_binding"]["approval_count"] == 5
    assert result["completion_binding"]["rebase_count"] == 4


@pytest.mark.parametrize("mutation", ["missing-digest", "different-digest"])
def test_intermediate_ordinary_quote_digest_is_bound_before_final_group(completed_group, mutation):
    archive = _workspace_current_run_archive_view(completed_group)
    receipt = archive["record"]["selective_rebase_receipts"][0]
    assert receipt["kind"] != "source_readmission_group"
    if mutation == "missing-digest":
        receipt.pop("successor_quote_digest", None)
    else:
        receipt["successor_quote_digest"] = "sha256:" + "0" * 64
    state = {**completed_group.state(), **completed_group.completion_history()}
    result = build_completed_run_observability_from_trusted_state(state, archive)
    assert result["status"] == "INVALID" and result["otlp"] is None


@pytest.mark.parametrize("mutation", ["missing-owner", "missing-event", "event-digest", "owner-scope", "review",
                                      "duplicate-group", "successor", "run", "group-status", "approval-set"])
def test_group_archive_rejects_incomplete_or_changed_proof(completed_group, mutation):
    state = completed_group.state()
    history = deepcopy(completed_group.completion_history())
    group = history["source_readmission_groups"][0]
    if mutation == "missing-owner":
        group["owners"].pop()
    elif mutation == "missing-event":
        group["source_events"].pop()
    elif mutation == "event-digest":
        group["source_events"][0]["digest"] = "sha256:" + "0" * 64
    elif mutation == "owner-scope":
        group["owners"][0]["source_ids"] = group["owners"][1]["source_ids"]
    elif mutation == "review":
        group["owners"][0]["review_evidence"]["review_wait_satisfied"] = False
    elif mutation == "duplicate-group":
        history["source_readmission_groups"].append(deepcopy(group))
    elif mutation == "successor":
        group["outcome"]["quote"]["version"] = "v5"
    elif mutation == "run":
        group["group"]["execution_run_id"] = "run:another"
    elif mutation == "group-status":
        group["state"] = "REVIEW"
    else:
        group["outcome"]["approval_set"]["members"].pop()
    view = SimpleNamespace(state=lambda: state, completion_history=lambda: history)
    result = _workspace_current_run_archive_view(view)
    assert result["status"] == "INVALID" and result["record"] is None


@pytest.mark.parametrize("mutation", ["event-body", "duplicate-receipt", "approval-count", "missing-clock"])
def test_completed_counts_require_actual_chain_and_unique_receipts(completed_group, mutation):
    archive = _workspace_current_run_archive_view(completed_group)
    state = {**completed_group.state(), **completed_group.completion_history()}
    if mutation == "event-body":
        state["event_chain"]["envelopes"][0]["payload"]["unexpected"] = True
    elif mutation == "duplicate-receipt":
        archive["record"]["selective_rebase_receipts"].append(
            deepcopy(archive["record"]["selective_rebase_receipts"][-1]))
        archive["record"]["human_approval_count"] += 2
    elif mutation == "approval-count":
        archive["record"]["human_approval_count"] = 500
    else:
        state.pop("completion_observed_at")
    result = build_completed_run_observability_from_trusted_state(state, archive)
    assert result["status"] == "INVALID" and result["otlp"] is None
