"""Manual price edits bind the reviewed fact to its displayed price source."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from orgrebase.api import create_app
from orgrebase.workspace.change_proposals import ChangeProposalInput
from tests.workspace.test_deliverable_set import make_dual_service
from tests.workspace.test_priced_quote_pack import BASKET, POLICY


@pytest.mark.parametrize("slot,value", [("quote_basket", BASKET), ("pricing_policy", POLICY)])
def test_manual_pricing_command_rejects_a_different_embedded_source(slot, value):
    arguments = {
        "event_id": "source-binding", "slot_id": slot, "base_version": "v1",
        "base_digest": "sha256:" + "a" * 64,
        "value": value, "source_ref": "source:reviewed-new-input@v2",
    }
    with pytest.raises(ValidationError, match="CHANGE_PROPOSAL_PRICING_SOURCE_MISMATCH"):
        ChangeProposalInput.model_validate(arguments)
    command = ChangeProposalInput.model_validate({
        **arguments, "value": {**value, "source_ref": arguments["source_ref"]},
    })
    assert command.value["source_ref"] == command.source_ref
    assert value["source_ref"] != command.source_ref  # The original input remains unchanged.


@pytest.mark.parametrize("slot,value", [("quote_basket", BASKET), ("pricing_policy", POLICY)])
def test_http_pricing_source_mismatch_creates_no_proposal_or_business_write(
    tmp_path: Path, slot, value,
):
    service, _, _ = make_dual_service(tmp_path, name="http-pricing-source-guard")
    try:
        service.form_quote()
        quote = service.current_quote()
        event_head = service.store.audit_head()
        with TestClient(create_app(workspace_service=service)) as client:
            fields = client.get("/api/workspace/change-options").json()["fields"]
            field = next(item for item in fields if item["slot_id"] == slot)
            response = client.post("/api/workspace/change-proposals", json={
                "event_id": "mismatched-source", "slot_id": slot,
                "base_version": field["current"]["version"],
                "base_digest": field["current"]["digest"],
                "value": value, "source_ref": "source:reviewed-new-input@v2",
            })
        assert response.status_code == 422, response.text
        assert "CHANGE_PROPOSAL_PRICING_SOURCE_MISMATCH" in response.text
        assert service.current_quote().digest == quote.digest
        assert service.store.audit_head() == event_head
        assert "mismatched-source" not in service.change_order
    finally:
        service.close()
