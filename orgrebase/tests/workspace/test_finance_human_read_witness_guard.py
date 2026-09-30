"""A human Finance proposal's formally captured read witness stays live."""

from __future__ import annotations

import urllib.request

import pytest
from enterprise_pack_factory import make_enterprise_pack

from orgrebase.domain import FreshnessError, IntegrityError
from orgrebase.workspace.change_proposals import submit_change
from orgrebase.workspace.finance_experiment import REVIEWED_FINANCE_RUBRIC_DIGEST, FinanceIndependentOracle
from orgrebase.workspace.finance_explanation_operations import evaluate_static_finance
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.read_dependencies import (
    ReadDependencies,
    ReadDependencyError,
    ReadQuery,
    capture_read_witness,
    dependency_payload,
)
from orgrebase.workspace.skill_evolution_v2 import FinanceSkillHeadService
from orgrebase.workspace.source_bindings import active_binding, source_coverage
from tests.workspace.test_change_proposals import command
from tests.workspace.test_finance_adoption_source_memory import (
    A_RECORD,
    B_FIELD,
    B_RECORD,
    _publish_source_lesson,
    _source_sync,
    _source_workspace,
)
from tests.workspace.test_finance_advice_v4 import _provider
from tests.workspace.test_finance_explanation_operations import _as, _fake_send


@pytest.mark.parametrize("backend", ("sqlite", "postgres"))
def test_human_finance_witness_stale_before_first_preview_never_dispatches(
    tmp_path, monkeypatch, postgres_runtime, backend,
):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    with _source_workspace(tmp_path, runtime, backend, postgres_runtime) as (
        workspace, principal, config, mappings,
    ):
        sent = []
        monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
        event_a, synchronizer, _reader, rows, page = _source_sync(
            workspace, principal, config, mappings,
        )
        _publish_source_lesson(workspace, principal, event_a)
        with principal("operator"):
            binding = active_binding(workspace, config)
            witness = capture_read_witness(workspace, ReadQuery(
                connector_id=binding["connector_id"], record_ids=(B_RECORD,),
                operator="eq", field=B_FIELD, value="USD",
            ))
            event_id = submit_change(workspace, command(
                workspace, event_id="human-witness-before-preview", slot="currency", value="EUR",
                read_dependencies=ReadDependencies.model_validate(dependency_payload(witness)),
            ))["event"]["event_id"]
        before_drift = len(sent)
        original_quote = workspace.current_quote().digest
        rows[B_RECORD] = {**rows[B_RECORD], "@odata.etag": 'W/"source-b2"'}
        page.update(records=(rows[B_RECORD],), cursor="b2")
        with principal("operator"):
            assert synchronizer.sync_page()["records_admitted"] == 2
            assert source_coverage(workspace, config)["status"] == "COMPLETE"
            with pytest.raises(RuntimeError, match="WORKSPACE_CHANGE_NOT_PREVIEWABLE"):
                workspace.preview_change(event_id)
        assert workspace._change_status_with_reason(event_id) == ("STALE", "READ_DEPENDENCY_CHANGED")
        assert workspace._preview_record(event_id) is None
        assert workspace._approval_record(event_id) is None
        assert workspace.current_quote().digest == original_quote
        assert len(sent) == before_drift


@pytest.mark.parametrize("backend", ("sqlite", "postgres"))
@pytest.mark.parametrize("drift_point", ("BEFORE_APPROVAL", "AFTER_APPROVAL"))
def test_human_finance_witness_revision_change_holds_every_normal_read_boundary(
    tmp_path, monkeypatch, postgres_runtime, backend, drift_point,
):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    with _source_workspace(tmp_path, runtime, backend, postgres_runtime) as (
        workspace, principal, config, mappings,
    ):
        sent = []
        monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
        event_a, synchronizer, _reader, rows, page = _source_sync(
            workspace, principal, config, mappings,
        )
        # A is a separate, already applied source fact. B is human-authored;
        # its read witness is an independently captured current dependency.
        _publish_source_lesson(workspace, principal, event_a)
        with principal("operator"):
            binding = active_binding(workspace, config)
            witness = capture_read_witness(workspace, ReadQuery(
                connector_id=binding["connector_id"], record_ids=(B_RECORD,),
                operator="eq", field=B_FIELD, value="USD",
            ))
            assert witness.result == "TRUE"
            dependencies = ReadDependencies.model_validate(dependency_payload(witness))
            event_id = submit_change(workspace, command(
                workspace, event_id="human-finance-witness", slot="currency", value="EUR",
                read_dependencies=dependencies,
            ))["event"]["event_id"]
            assert workspace.changes.get(event_id).proposal.version.startswith("proposal-")
            bundle = workspace.preview_change(event_id)
        with _as(workspace, "actor:finance-governor", "governor"):
            FinanceSkillHeadService(
                workspace.store, tenant_id=workspace.profile.organization_id,
            ).bootstrap()
        approval = None
        if drift_point == "AFTER_APPROVAL":
            with principal("currency"):
                approval = workspace.approve_change(
                    event_id, actor_id=workspace.change_owner[event_id],
                    preview_digest=bundle.preview.digest,
                )
        original_quote = workspace.current_quote().digest
        sent_before_drift = len(sent)
        rows[B_RECORD] = {**rows[B_RECORD], "@odata.etag": 'W/"source-b2"'}
        page.update(records=(rows[B_RECORD],), cursor="b2")
        with principal("operator"):
            assert synchronizer.sync_page()["records_admitted"] == 2
            coverage = source_coverage(workspace, config)
            assert coverage["status"] == "COMPLETE"
            assert coverage["records"][A_RECORD]["revision"] == 'W/"source-a1"'
            assert coverage["records"][B_RECORD]["revision"] == 'W/"source-b2"'
        assert workspace._change_status_with_reason(event_id) == ("STALE", "READ_DEPENDENCY_CHANGED")
        with principal("operator"), pytest.raises(ReadDependencyError):
            workspace.preview_change(event_id)
        if approval is None:
            with principal("currency"), pytest.raises(ReadDependencyError):
                workspace.approve_change(
                    event_id, actor_id=workspace.change_owner[event_id],
                    preview_digest=bundle.preview.digest,
                )
            assert workspace._approval_record(event_id) is None
        else:
            with principal("operator"), pytest.raises(
                (ReadDependencyError, FreshnessError, IntegrityError, RuntimeError),
            ):
                workspace.apply_approved_change(
                    event_id, approval_digest=approval["approval_digest"],
                )
            assert workspace._outcome_record(event_id) is None
        # Static Finance and the independent gold freeze share the same
        # _current_case guard; neither may mint a new model/trial input.
        with _as(workspace, "actor:finance-operator", "operator"), pytest.raises(
            (ReadDependencyError, IntegrityError),
        ):
            evaluate_static_finance(
                workspace, event_id=event_id, operation_id="static:witness-stale",
                provider_override=_provider(),
            )
        oracle = FinanceIndependentOracle(
            workspace, gold_owner_actor_id="actor:gold-owner",
            assessor_actor_id="actor:assessor", candidate_author_actor_id="actor:author",
            optimizer_actor_id="actor:optimizer", release_governor_actor_id="actor:release",
        )
        with _as(workspace, "actor:gold-owner", "governor"), pytest.raises(
            (ReadDependencyError, IntegrityError),
        ):
            oracle.freeze_gold(
                event_id=event_id, suite_id="witness-guard",
                independence_cluster_id="cluster:witness-guard",
                required_concepts=("current-source",), forbidden_claims=("stale-source",),
                next_step_concepts=("review",), rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
            )
        assert workspace.current_quote().digest == original_quote
        assert len(sent) == sent_before_drift
        assert not workspace.store.list_artifacts(artifact_id_prefix="finance-evaluation-intent:")
        assert not workspace.store.list_artifacts(artifact_id_prefix="finance-experiment-gold:")
