"""A known terminal Finance V4 can have one audited evidence-round successor."""

from __future__ import annotations

import json
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from orgrebase.api import create_app
from orgrebase.database import artifacts
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.impact import ImpactEngine
from orgrebase.workspace.change_proposals import ReviewObservationInput, record_review, submit_change
from orgrebase.workspace.change_recovery import resume_change, return_for_evidence
from orgrebase.workspace.finance_adoption import (
    RECOVERY_HOLD_MEDIA,
    SUCCESSION_MEDIA,
    USE_MEDIA,
    finance_cluster_digest,
    selection_for_bundle,
    use_ref,
)
from orgrebase.workspace.finance_use_projection import read_finance_use_projection
from orgrebase.workspace.model_provider import VERTEX_CANDIDATE_MODEL_ID
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_change_agentteams import configure
from tests.workspace.test_change_proposals import command
from tests.workspace.test_change_proposals import workspace as workspace
from tests.workspace.test_change_recovery import resume_command, return_command
from tests.workspace.test_finance_adoption import _as, _controlled_adoption
from tests.workspace.test_finance_advice_v4 import _provider


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_completed_finance_preview_has_exact_recovery_successor(
    workspace, tmp_path, monkeypatch, postgres_runtime, backend,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, backend,
    ) as (target, event_id, policy, policy_path, sent):
        configure(target, tmp_path / "native")
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        with _as(target, "actor:finance-operator", "operator"):
            first = target.preview_change(event_id)
            first_selection = selection_for_bundle(target, event_id, first)
        assert first_selection is not None
        first_use = target.store.load_artifact(use_ref(first_selection), USE_MEDIA)
        original_sends = len(sent)
        with _as(target, target.change_owner[event_id], "approver"):
            returned = return_for_evidence(
                target, event_id,
                return_command(target, event_id, operation="return-finance-terminal"),
            )
        assert returned["state"] == "NEEDS_EVIDENCE"
        with _as(target, "actor:finance-operator", "operator"):
            command = resume_command(target, event_id, operation="resume-finance-terminal")
            ready = resume_change(
                target, event_id, command,
            )
            second = target.preview_change(event_id)
            second_selection = selection_for_bundle(target, event_id, second)
        assert ready["state"] == "READY_FOR_REVIEW"
        assert second_selection is not None
        assert second_selection.attempt_key != first_selection.attempt_key
        assert second_selection.recovery_digest == ready["recovery_digest"]
        assert len(sent) > original_sends
        after_success = len(sent)
        with _as(target, "actor:finance-operator", "operator"):
            assert resume_change(target, event_id, command) == ready
        assert len(sent) == after_success
        reopened = WorkspaceService(
            store_path=target.store.path,
            store_tenant_id=target.profile.organization_id,
            store_migrate=backend == "sqlite",
            runtime_configuration=workspace.runtime_configuration,
            clock=workspace.clock, review_duration_seconds=0,
            advisory_provider=_provider(), advisory_model_id=VERTEX_CANDIDATE_MODEL_ID,
        )
        try:
            configure(reopened, tmp_path / "native-restarted")
            reopened.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
            with _as(reopened, "actor:finance-operator", "operator"):
                assert resume_change(reopened, event_id, command) == ready
                assert read_finance_use_projection(
                    reopened, event_id, attempt_key=first_selection.attempt_key,
                )["status"] == "PROVIDER_RESPONSE_RECEIVED"
            assert len(sent) == after_success
        finally:
            reopened.close()
        assert target.store.load_artifact(use_ref(first_selection), USE_MEDIA) == first_use
        assert target.store.load_artifact(use_ref(second_selection), USE_MEDIA).payload["target_writes"] == 0
        assert selection_for_bundle(target, event_id, first) == first_selection
        sidecar = target.store.load_artifact(
            "finance-adoption-succession:" + sha256_digest(second_selection.attempt_key)[7:],
            SUCCESSION_MEDIA,
        ).payload
        assert sidecar["predecessor_attempt_key"] == first_selection.attempt_key
        assert sidecar["predecessor_use_digest"] == first_use.payload_digest
        assert sidecar["recovery_resume_digest"] == ready["recovery_digest"]
        with _as(target, "actor:finance-operator", "operator"):
            old_view = read_finance_use_projection(
                target, event_id, attempt_key=first_selection.attempt_key,
            )
            new_view = read_finance_use_projection(
                target, event_id, attempt_key=second_selection.attempt_key,
            )
            assert read_finance_use_projection(target, event_id) == new_view
        assert old_view["status"] == new_view["status"] == "PROVIDER_RESPONSE_RECEIVED"
        assert old_view["recovery_round"] == 0
        assert new_view["recovery_round"] == 1
        assert [item["attempt_key"] for item in new_view["attempt_history"]] == [
            first_selection.attempt_key, second_selection.attempt_key,
        ]
        with _as(target, "actor:finance-operator", "operator"):
            record_review(target, event_id, ReviewObservationInput(
                observation_id="finance-second-round-review",
                preview_digest=second.preview.digest, active_ms=1200,
                action="PREVIEW", outcome="SUCCESS",
            ))
            assert read_finance_use_projection(
                target, event_id, attempt_key=first_selection.attempt_key,
            )["feedback"]["review_observations"] == []
            assert len(read_finance_use_projection(
                target, event_id, attempt_key=second_selection.attempt_key,
            )["feedback"]["review_observations"]) == 1
        with TestClient(create_app(workspace_service=target)) as client:
            use_path = f"/api/workspace/changes/{event_id}/finance-use"
            assert client.get(use_path).status_code in {401, 403}
            with _as(target, "actor:finance-operator", "operator"):
                response = client.get(use_path, params={"attempt_key": first_selection.attempt_key})
                assert response.status_code == 200, response.text
                assert response.json()["attempt_key"] == first_selection.attempt_key
                assert client.get(use_path).json()["attempt_key"] == second_selection.attempt_key
        policy_path.write_text(json.dumps(policy.model_copy(update={
            "mode": "OFF",
        }).model_dump(mode="json")), encoding="utf-8")
        with _as(target, "actor:finance-operator", "operator"):
            replay = resume_change(target, event_id, command)
        assert replay["state"] == "READY_FOR_REVIEW"
        assert replay["recovery_digest"] == ready["recovery_digest"]
        assert len(sent) == after_success


@pytest.mark.parametrize("blocker", ["policy_off", "owner_not_runtime_actor", "budget_exhausted"])
def test_recovery_preflight_rejects_before_advancing_business_state(
    workspace, tmp_path, monkeypatch, postgres_runtime, blocker,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, "sqlite",
    ) as (target, event_id, policy, path, sent):
        configure(target, tmp_path / "native")
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        if blocker == "budget_exhausted":
            spec = target.change_spec(event_id)
            fixture = target._fixture_for_change(spec)
            change_set = target.change_builder.build(fixture=fixture, spec=spec)
            cost = target.advisory_factory.cost_reservation(
                fixture=fixture, change_set=change_set,
                preview=ImpactEngine(fixture).preview(change_set),
            )
            assert cost is not None
            policy = policy.model_copy(update={"max_calls": cost["calls"]})
            path.write_text(json.dumps(policy.model_dump(mode="json")), encoding="utf-8")
        with _as(target, "actor:finance-operator", "operator"):
            target.preview_change(event_id)
        with _as(target, target.change_owner[event_id], "approver"):
            return_for_evidence(
                target, event_id,
                return_command(target, event_id, operation="return-before-preflight"),
            )
        before = len(sent)
        if blocker == "policy_off":
            path.write_text(json.dumps(policy.model_copy(update={
                "mode": "OFF",
            }).model_dump(mode="json")), encoding="utf-8")
        actor, role = (
            ("actor:other-operator", "operator") if blocker == "owner_not_runtime_actor"
            else ("actor:finance-operator", "operator")
        )
        with _as(target, actor, role), pytest.raises(IntegrityError, match=(
            "POLICY_CHANGED" if blocker == "policy_off" else
            "POLICY_BUDGET_EXHAUSTED" if blocker == "budget_exhausted" else
            "ACTOR_NOT_ADMITTED"
        )):
            resume_change(
                target, event_id,
                resume_command(target, event_id, operation="resume-after-preflight"),
            )
        assert target._change_status(event_id) == "EVIDENCE_REQUIRED"
        assert target._preview_record(event_id) is None
        assert len(sent) == before
        if blocker == "budget_exhausted":
            return
        if blocker == "policy_off":
            path.write_text(json.dumps(policy.model_dump(mode="json")), encoding="utf-8")
        with _as(target, "actor:finance-operator", "operator"):
            ready = resume_change(
                target, event_id,
                resume_command(target, event_id, operation="resume-after-preflight"),
            )
        assert ready["state"] == "READY_FOR_REVIEW"
        assert len(sent) > before


@pytest.mark.parametrize(("backend", "concurrent"), [
    ("sqlite", False), ("postgresql", False), ("postgresql", True),
])
def test_pending_successor_hold_prevents_second_event_from_spending_same_budget(
    workspace, tmp_path, monkeypatch, postgres_runtime, backend, concurrent,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, backend,
    ) as (target, first_id, policy, policy_path, sent):
        configure(target, tmp_path / "native")
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        second_id = "edit-2"
        submit_change(target, command(target, event_id=second_id, slot="currency", value="GBP"))

        def change_and_cost(event_id):
            spec = target.change_spec(event_id)
            fixture = target._fixture_for_change(spec)
            change_set = target.change_builder.build(fixture=fixture, spec=spec)
            cost = target.advisory_factory.cost_reservation(
                fixture=fixture, change_set=change_set,
                preview=ImpactEngine(fixture).preview(change_set),
            )
            assert cost is not None
            return change_set, cost

        first_change, first_cost = change_and_cost(first_id)
        second_change, second_cost = change_and_cost(second_id)
        assert second_cost == first_cost
        policy = policy.model_copy(update={
            "cluster_digests": tuple(sorted((
                finance_cluster_digest(target, first_id, first_change),
                finance_cluster_digest(target, second_id, second_change),
            ))),
            "max_cases": 2,
            "max_calls": first_cost["calls"] * 3,
            "max_reserved_microusd": first_cost["reserved_microusd"] * 3,
        })
        policy_path.write_text(json.dumps(policy.model_dump(mode="json")), encoding="utf-8")
        with _as(target, "actor:finance-operator", "operator"):
            target.preview_change(first_id)
            target.preview_change(second_id)
        before = len(sent)
        for event_id in (first_id, second_id):
            with _as(target, target.change_owner[event_id], "approver"):
                return_for_evidence(
                    target, event_id,
                    return_command(target, event_id, operation="return-" + event_id),
                )
        with _as(target, "actor:finance-operator", "operator"):
            first_resume = resume_command(
                target, first_id, operation="resume-hold-first",
            ).model_copy(update={"defer_candidate_preparation": True})
            second_resume = resume_command(
                target, second_id, operation="resume-hold-second",
            ).model_copy(update={"defer_candidate_preparation": True})
        if concurrent:
            reopened = WorkspaceService(
                store_path=target.store.path,
                store_tenant_id=target.profile.organization_id,
                store_migrate=False,
                runtime_configuration=workspace.runtime_configuration,
                clock=workspace.clock, review_duration_seconds=0,
                advisory_provider=_provider(), advisory_model_id=VERTEX_CANDIDATE_MODEL_ID,
            )
            try:
                configure(reopened, tmp_path / "native-concurrent")
                reopened.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
                barrier = threading.Barrier(2)

                def resume_in_worker(service, event_id, command):
                    with _as(service, "actor:finance-operator", "operator"):
                        barrier.wait(timeout=10)
                        try:
                            return ("READY", resume_change(service, event_id, command)["state"])
                        except IntegrityError as error:
                            return ("ERROR", str(error))

                with ThreadPoolExecutor(max_workers=2) as pool:
                    left = pool.submit(resume_in_worker, target, first_id, first_resume)
                    right = pool.submit(resume_in_worker, reopened, second_id, second_resume)
                    results = (left.result(timeout=30), right.result(timeout=30))
                assert sorted(item[0] for item in results) == ["ERROR", "READY"]
                assert next(item[1] for item in results if item[0] == "ERROR") == (
                    "FINANCE_ADOPTION_POLICY_BUDGET_EXHAUSTED"
                )
                winner = first_id if results[0][0] == "READY" else second_id
                loser = second_id if winner == first_id else first_id
            finally:
                reopened.close()
        else:
            with _as(target, "actor:finance-operator", "operator"):
                assert resume_change(target, first_id, first_resume)["state"] == "RESUMING"
                with pytest.raises(IntegrityError, match="FINANCE_ADOPTION_POLICY_BUDGET_EXHAUSTED"):
                    resume_change(target, second_id, second_resume)
            winner, loser = first_id, second_id
        assert target._change_status(loser) == "EVIDENCE_REQUIRED"
        assert target._preview_record(loser) is None
        assert len(sent) == before
        with _as(target, "actor:finance-operator", "operator"):
            target.preview_change(winner)
        assert target._preview_record(winner) is not None
        assert len(sent) > before


def _rewrite_artifact_payload(workspace, artifact_id, media_type, mutate):
    """Simulate a privileged store corruption with internally consistent bytes."""
    stored = workspace.store.load_artifact(artifact_id, media_type)
    body = mutate(dict(stored.payload))
    with workspace.store.transaction() as connection:
        workspace.store.execute(
            connection,
            update(artifacts)
            .where(artifacts.c.artifact_id == artifact_id)
            .values(payload_json=canonical_json(body), payload_digest=sha256_digest(body)),
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_three_rounds_convert_each_hold_once_and_reject_fourth_over_budget(
    workspace, tmp_path, monkeypatch, postgres_runtime, backend,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, backend,
    ) as (target, event_id, policy, policy_path, sent):
        configure(target, tmp_path / "native")
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        spec = target.change_spec(event_id)
        fixture = target._fixture_for_change(spec)
        change_set = target.change_builder.build(fixture=fixture, spec=spec)
        cost = target.advisory_factory.cost_reservation(
            fixture=fixture, change_set=change_set,
            preview=ImpactEngine(fixture).preview(change_set),
        )
        assert cost is not None
        policy = policy.model_copy(update={
            "max_calls": cost["calls"] * 3,
            "max_reserved_microusd": cost["reserved_microusd"] * 3,
        })
        policy_path.write_text(json.dumps(policy.model_dump(mode="json")), encoding="utf-8")
        selections = []
        with _as(target, "actor:finance-operator", "operator"):
            first = target.preview_change(event_id)
            selections.append(selection_for_bundle(target, event_id, first))
        assert selections[0] is not None
        for round_number in (1, 2):
            with _as(target, target.change_owner[event_id], "approver"):
                return_for_evidence(
                    target, event_id,
                    return_command(target, event_id, operation=f"return-budget-{round_number}"),
                )
            with _as(target, "actor:finance-operator", "operator"):
                resume = resume_command(
                    target, event_id, operation=f"resume-budget-{round_number}",
                ).model_copy(update={"defer_candidate_preparation": True})
                assert resume_change(target, event_id, resume)["state"] == "RESUMING"
                hold_rows = target.store.list_artifacts(
                    artifact_id_prefix="finance-adoption-recovery-hold:" + policy.digest[7:] + ":",
                    expected_media_type=RECOVERY_HOLD_MEDIA,
                )
                assert len(hold_rows) == round_number
                current = target.preview_change(event_id)
                selections.append(selection_for_bundle(target, event_id, current))
                assert selections[-1] is not None
                after_round = len(sent)
                replay = target.preview_change(event_id)
                assert selection_for_bundle(target, event_id, replay) == selections[-1]
                assert len(sent) == after_round
        assert len({item.attempt_key for item in selections}) == 3
        assert len(target.store.list_artifacts(
            artifact_id_prefix="finance-adoption-selection:" + policy.digest[7:] + ":",
        )) == 3
        assert sum(item.reserved_calls for item in selections) == policy.max_calls
        assert sum(item.reserved_microusd for item in selections) == policy.max_reserved_microusd
        assert len(sent) >= 3
        before = len(sent)
        with _as(target, target.change_owner[event_id], "approver"):
            return_for_evidence(
                target, event_id, return_command(target, event_id, operation="return-budget-3"),
            )
        with _as(target, "actor:finance-operator", "operator"), pytest.raises(
            IntegrityError, match="FINANCE_ADOPTION_POLICY_BUDGET_EXHAUSTED",
        ):
            resume_change(
                target, event_id,
                resume_command(target, event_id, operation="resume-budget-3"),
            )
        assert target._change_status(event_id) == "EVIDENCE_REQUIRED"
        assert target._preview_record(event_id) is None
        assert len(sent) == before


def test_corrupt_unconsumed_recovery_hold_blocks_dispatch(
    workspace, tmp_path, monkeypatch, postgres_runtime,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, "sqlite",
    ) as (target, event_id, policy, _policy_path, sent):
        configure(target, tmp_path / "native")
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        with _as(target, "actor:finance-operator", "operator"):
            target.preview_change(event_id)
        with _as(target, target.change_owner[event_id], "approver"):
            return_for_evidence(
                target, event_id, return_command(target, event_id, operation="return-hold-corrupt"),
            )
        with _as(target, "actor:finance-operator", "operator"):
            resume_change(
                target, event_id,
                resume_command(target, event_id, operation="resume-hold-corrupt").model_copy(
                    update={"defer_candidate_preparation": True},
                ),
            )
        hold_rows = target.store.list_artifacts(
            artifact_id_prefix="finance-adoption-recovery-hold:" + policy.digest[7:] + ":",
            expected_media_type=RECOVERY_HOLD_MEDIA,
        )
        assert len(hold_rows) == 1
        _rewrite_artifact_payload(
            target, hold_rows[0].artifact_id, RECOVERY_HOLD_MEDIA,
            lambda body: {**body, "predecessor_use_digest": sha256_digest("other-use")},
        )
        before = len(sent)
        with _as(target, "actor:finance-operator", "operator"), pytest.raises(
            IntegrityError, match="FINANCE_ADOPTION_RECOVERY_HOLD_MISSING",
        ):
            target.preview_change(event_id)
        assert len(sent) == before
        assert target._preview_record(event_id) is None


def test_recovery_use_history_rejects_tampered_predecessor_binding(
    workspace, tmp_path, monkeypatch, postgres_runtime,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, "sqlite",
    ) as (target, event_id, _policy, _policy_path, sent):
        configure(target, tmp_path / "native")
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        with _as(target, "actor:finance-operator", "operator"):
            first = target.preview_change(event_id)
            first_selection = selection_for_bundle(target, event_id, first)
        assert first_selection is not None
        with _as(target, target.change_owner[event_id], "approver"):
            return_for_evidence(
                target, event_id, return_command(target, event_id, operation="return-predecessor"),
            )
        with _as(target, "actor:finance-operator", "operator"):
            resume_change(
                target, event_id,
                resume_command(target, event_id, operation="resume-predecessor"),
            )
            second = target.preview_change(event_id)
            second_selection = selection_for_bundle(target, event_id, second)
        assert second_selection is not None
        sidecar_ref = "finance-adoption-succession:" + sha256_digest(second_selection.attempt_key)[7:]
        _rewrite_artifact_payload(
            target, sidecar_ref, SUCCESSION_MEDIA,
            lambda body: {**body, "predecessor_use_digest": sha256_digest("forged-previous-use")},
        )
        before = len(sent)
        with _as(target, "actor:finance-operator", "operator"), pytest.raises(
            IntegrityError, match="FINANCE_ADOPTION_SUCCESSION_BINDING_INVALID",
        ):
            read_finance_use_projection(
                target, event_id, attempt_key=second_selection.attempt_key,
            )
        assert len(sent) == before


def test_same_event_two_postgres_workers_share_one_recovery_dispatch(
    workspace, tmp_path, monkeypatch, postgres_runtime,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, "postgresql",
    ) as (target, event_id, policy, _policy_path, sent):
        configure(target, tmp_path / "native")
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        with _as(target, "actor:finance-operator", "operator"):
            first = target.preview_change(event_id)
            first_selection = selection_for_bundle(target, event_id, first)
        assert first_selection is not None
        initial_sends = len(sent)
        with _as(target, target.change_owner[event_id], "approver"):
            return_for_evidence(
                target, event_id, return_command(target, event_id, operation="return-two-workers"),
            )
        with _as(target, "actor:finance-operator", "operator"):
            same_command = resume_command(target, event_id, operation="resume-two-workers")
        reopened = WorkspaceService(
            store_path=target.store.path,
            store_tenant_id=target.profile.organization_id,
            store_migrate=False,
            runtime_configuration=workspace.runtime_configuration,
            clock=workspace.clock, review_duration_seconds=0,
            advisory_provider=_provider(), advisory_model_id=VERTEX_CANDIDATE_MODEL_ID,
        )
        try:
            configure(reopened, tmp_path / "native-concurrent")
            reopened.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
            regular_send = urllib.request.urlopen
            entered_send = threading.Event()
            release_send = threading.Event()
            successor_wires = []

            def held_send(request, *, timeout):
                successor_wires.append(request.data)
                entered_send.set()
                assert release_send.wait(15), "successor wire never released"
                return regular_send(request, timeout=timeout)

            monkeypatch.setattr(urllib.request, "urlopen", held_send)

            def resume_in_worker(service):
                with _as(service, "actor:finance-operator", "operator"):
                    try:
                        return ("READY", resume_change(service, event_id, same_command)["state"])
                    except IntegrityError as error:
                        return ("ERROR", str(error))

            with ThreadPoolExecutor(max_workers=2) as pool:
                first_worker = pool.submit(resume_in_worker, target)
                try:
                    assert entered_send.wait(15), "first worker did not reach the provider"
                    other_worker = pool.submit(resume_in_worker, reopened)
                    other_result = other_worker.result(timeout=15)
                    assert other_result == ("ERROR", "WORKSPACE_ADVISORY_IN_PROGRESS")
                finally:
                    release_send.set()
                first_result = first_worker.result(timeout=15)
            assert first_result == ("READY", "READY_FOR_REVIEW")
            assert len(successor_wires) == initial_sends
            assert len(sent) == initial_sends * 2
            with _as(reopened, "actor:finance-operator", "operator"):
                replay = resume_change(reopened, event_id, same_command)
                assert replay["state"] == "READY_FOR_REVIEW"
                assert read_finance_use_projection(
                    reopened, event_id, attempt_key=first_selection.attempt_key,
                )["status"] == "PROVIDER_RESPONSE_RECEIVED"
                latest = read_finance_use_projection(reopened, event_id)
                assert latest["recovery_round"] == 1
                assert latest["attempt_key"] != first_selection.attempt_key
            assert len(successor_wires) == initial_sends
            assert len(target.store.list_artifacts(
                artifact_id_prefix="finance-adoption-selection:" + policy.digest[7:] + ":",
            )) == 2
        finally:
            reopened.close()


def test_unknown_recovery_attempt_cannot_open_another_round_or_nonce(
    workspace, tmp_path, monkeypatch, postgres_runtime,
):
    with _controlled_adoption(
        workspace, tmp_path, monkeypatch, postgres_runtime, "sqlite",
    ) as (target, event_id, _policy, _policy_path, sent):
        configure(target, tmp_path / "native")
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        with _as(target, "actor:finance-operator", "operator"):
            target.preview_change(event_id)
        with _as(target, target.change_owner[event_id], "approver"):
            return_for_evidence(
                target, event_id, return_command(target, event_id, operation="return-before-unknown"),
            )
        with _as(target, "actor:finance-operator", "operator"):
            resume_change(
                target, event_id,
                resume_command(target, event_id, operation="resume-before-unknown").model_copy(
                    update={"defer_candidate_preparation": True},
                ),
            )
        wire_sends = []

        def ambiguous(request, *, timeout):
            assert timeout > 0
            wire_sends.append(request.data)
            raise TimeoutError("controlled ambiguous transport")

        monkeypatch.setattr(urllib.request, "urlopen", ambiguous)
        with _as(target, "actor:finance-operator", "operator"), pytest.raises(
            IntegrityError, match="WORKSPACE_ADVISORY_MODEL_INCOMPLETE",
        ):
            target.preview_change(event_id)
        assert len(wire_sends) == 1
        with _as(target, target.change_owner[event_id], "approver"), pytest.raises(
            IntegrityError, match="CHANGE_RECOVERY_RETURN_NOT_AVAILABLE",
        ):
            return_for_evidence(
                target, event_id,
                return_command(target, event_id, operation="return-after-unknown"),
            )
        with _as(target, "actor:finance-operator", "operator"), pytest.raises(
            IntegrityError, match=r"(RESULT_UNKNOWN|IN_PROGRESS)",
        ):
            target.preview_change(event_id)
        target.workflow_run_nonce = "nonce:forged-recovery-retry"
        with _as(target, "actor:finance-operator", "operator"), pytest.raises(
            IntegrityError, match=r"FINANCE_ADOPTION_(EVENT_ATTEMPT_BOUND|PREDECESSOR)",
        ):
            target.preview_change(event_id)
        assert len(wire_sends) == 1
        assert target._preview_record(event_id) is None
        assert len(sent) >= 1
