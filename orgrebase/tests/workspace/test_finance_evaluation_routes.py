from __future__ import annotations

import urllib.request

from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.workspace.model_provider import VERTEX_CANDIDATE_MODEL_ID
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_change_proposals import workspace as workspace
from tests.workspace.test_finance_advice_v4 import _provider
from tests.workspace.test_finance_explanation_operations import _as, _fake_send, _prepare


def test_finance_evaluation_http_is_explicit_and_does_not_apply(
    workspace, tmp_path, monkeypatch,
):
    service = WorkspaceService(
        store_path=tmp_path / "finance-route.sqlite",
        store_tenant_id=workspace.profile.organization_id,
        runtime_configuration=workspace.runtime_configuration,
        advisory_provider=_provider(), advisory_model_id=VERTEX_CANDIDATE_MODEL_ID,
        clock=workspace.clock, review_duration_seconds=0,
    )
    try:
        service.form_quote()
        sent = []
        monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, service))
        event_id, _head = _prepare(service)
        original_quote = service.current_quote().digest
        path = f"/api/workspace/finance-explanation/evaluations/{event_id}"
        with TestClient(create_app(workspace_service=service)) as client:
            assert client.post(path, json={"operation_id": "eval:one"}).status_code in {401, 403}
            with _as(service, "actor:finance-evaluator", "operator"):
                first = client.post(path, json={"operation_id": "eval:one"})
                assert first.status_code == 200, first.text
                assert first.json()["status"] == "PROTOCOL_VALID"
                assert first.json()["quality_status"] == "NOT_EVALUATED"
                assert first.json()["target_writes"] == 0
                assert client.get(path).json()["result_digest"] == first.json()["result_digest"]
                repeated = client.post(path, json={"operation_id": "eval:one"})
                assert repeated.status_code == 200 and repeated.json() == first.json()
        assert service.current_quote().digest == original_quote
        assert service._approval_record(event_id) is None
        assert service._outcome_record(event_id) is None
        assert sent
    finally:
        service.close()
