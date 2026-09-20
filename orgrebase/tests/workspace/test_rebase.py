from __future__ import annotations

import pytest

from orgrebase.certificates import MinimalRebaseCertificateVerifier
from orgrebase.domain import Approval, FreshnessError, ImpactClassification, IntegrityError


def test_launch_date_change_uses_existing_core_and_rebuilds_quote(
    workspace_service, apply_staged_change
) -> None:
    workspace_service.form_quote()
    result = apply_staged_change(workspace_service, "launch_date")
    classifications = {
        item.object_id: item.classification for item in result["preview"].results
    }
    assert classifications == {
        "work:quote_acme": ImpactClassification.AFFECTED_HARD,
        "work:finance_analysis_d": ImpactClassification.UNAFFECTED_WITHIN_DECLARED_BOUNDARY,
        "work:partner_brief_e": ImpactClassification.UNKNOWN,
        "skill:enterprise-launch-readiness": ImpactClassification.REQUALIFICATION_REQUIRED,
    }
    quote = result["quote"]
    assert quote.ref == "work:quote_acme@v2"
    assert quote.payload["launch_date"] == "2026-09-15"
    assert quote.payload["currency"] == "USD"
    assert result["graph_pointer"].version == "v2"
    assert result["workspace_rebase_receipt"].status == "COMPLETED"


def test_vmrc_missing_or_extra_rebuild_is_rejected(workspace_service) -> None:
    workspace_service.form_quote()
    bundle = workspace_service.preview_change("launch_date")
    fixture = workspace_service._fixture_for_change(bundle.change_spec)
    payload = bundle.minimal_rebase_certificate.model_dump(mode="json")
    effects = payload["effects"]
    quote = next(item for item in effects if item["target_id"] == "work:quote_acme")
    quote["disposition"] = "HOLD_FOR_REVIEW"
    payload["digest"] = ""
    with pytest.raises((IntegrityError, ValueError), match=r"MINIMALITY_MISSING_REBUILD|content digest"):
        MinimalRebaseCertificateVerifier(fixture).verify(payload, bundle.change_set)


def test_preview_revision_drift_is_zero_write(workspace_service) -> None:
    workspace_service.form_quote()
    preview = workspace_service.preview_change("launch_date")
    approved = workspace_service.approve_change(
        "launch_date", actor_id=preview.change_spec.owner_id, preview_digest=preview.preview.digest)
    before_quote = workspace_service.current_quote()
    before_pointer = workspace_service.current_graph_pointer()
    current = dict(workspace_service._fixture_for_change(workspace_service.change_spec("launch_date")).revisions)
    current["graph"] = "graph:workspace@drifted"
    with pytest.raises(FreshnessError, match="preview revision drift"):
        workspace_service._execute_apply(
            kind="launch_date",
            bundle=preview,
            approval=Approval.model_validate(approved["approval"]),
            current_revisions=current,
        )
    assert workspace_service.current_quote().digest == before_quote.digest
    assert workspace_service.current_graph_pointer().digest == before_pointer.digest


def test_preserved_finance_payload_and_version_remain_unchanged(
    workspace_service, apply_staged_change
) -> None:
    workspace_service.form_quote()
    before = workspace_service.store.get_object("work:finance_analysis_d")
    apply_staged_change(workspace_service, "launch_date")
    after = workspace_service.store.get_object("work:finance_analysis_d")
    assert after.version == before.version
    assert after.payload == before.payload
