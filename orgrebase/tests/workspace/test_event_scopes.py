from __future__ import annotations

from orgrebase.workspace.service import WorkspaceService

_EMPTY_CHAIN_DIGEST = "sha256:" + "0" * 64


def test_empty_workspace_does_not_claim_quote_business_chain_passed(
    workspace_service: WorkspaceService,
) -> None:
    state = workspace_service.state()
    scopes = state["event_scopes"]

    assert state["stage"] == "EMPTY"
    assert scopes["workspace_global"]["status"] == "PASS"
    assert scopes["quote_business"] == {
        "status": "NOT_OBSERVED",
        "events": 0,
        "head_digest": _EMPTY_CHAIN_DIGEST,
        "first_sequence_no": None,
        "last_sequence_no": None,
        "start_anchor_digest": _EMPTY_CHAIN_DIGEST,
        "definition": "NO_QUOTE_BUSINESS_EVENTS_OBSERVED",
    }


def test_quote_only_publishes_one_contiguous_business_window(
    workspace_service: WorkspaceService,
) -> None:
    workspace_service.form_quote()

    scopes = workspace_service.state()["event_scopes"]
    assert scopes["schema_version"] == "orgrebase.workspace-event-scopes.v2"
    assert scopes["layout"] == "QUOTE_ONLY"
    assert scopes["workspace_global"]["status"] == "PASS"
    assert scopes["workspace_prelude"]["events"] == 3
    assert scopes["quote_business"]["status"] == "PASS"
    assert scopes["quote_business"]["events"] == 1
    assert scopes["quote_business"]["definition"] == "COMPLETE_NON_OAC_CHAIN"
    assert scopes["relationship"]["honest_contiguous_windows_published"] is True


def test_reset_returns_quote_business_scope_to_unobserved(
    workspace_service: WorkspaceService,
) -> None:
    workspace_service.form_quote()
    assert workspace_service.state()["event_scopes"]["quote_business"]["status"] == "PASS"

    reset_state = workspace_service.reset()["state"]
    quote_scope = reset_state["event_scopes"]["quote_business"]

    assert reset_state["stage"] == "EMPTY"
    assert quote_scope["status"] == "NOT_OBSERVED"
    assert quote_scope["events"] == 0
    assert quote_scope["head_digest"] == _EMPTY_CHAIN_DIGEST
    assert quote_scope["definition"] == "NO_QUOTE_BUSINESS_EVENTS_OBSERVED"
    assert reset_state["event_scopes"]["workspace_global"]["status"] == "PASS"


def test_oac_only_events_do_not_promote_an_empty_quote_business_scope(
    workspace_service: WorkspaceService,
) -> None:
    workspace_service.store.record_event(
        "OAC_QUOTE_ADAPTATION_PREPARED",
        {"candidate_only": True, "canonical_target_writes": 0},
    )

    scopes = workspace_service.state()["event_scopes"]

    assert scopes["workspace_global"]["status"] == "PASS"
    assert scopes["oac_adaptation"]["status"] == "PASS"
    assert scopes["oac_adaptation"]["events"] == 1
    assert scopes["quote_business"]["status"] == "NOT_OBSERVED"
    assert scopes["quote_business"]["events"] == 0
    assert scopes["quote_business"]["head_digest"] == _EMPTY_CHAIN_DIGEST


def test_quote_events_remain_the_business_prefix_when_oac_governance_is_appended(
    workspace_service: WorkspaceService,
) -> None:
    workspace_service.form_quote()
    quote_state = workspace_service.state()
    quote_scope = quote_state["event_scopes"]["quote_business"]

    workspace_service.store.record_event(
        "OAC_QUOTE_ADAPTATION_PREPARED",
        {"candidate_only": True, "canonical_target_writes": 0},
    )
    workspace_service.store.record_event(
        "OAC_QUOTE_ADAPTER_ADMITTED",
        {"candidate_only": True, "canonical_target_writes": 0},
    )

    state = workspace_service.state()
    scopes = state["event_scopes"]
    assert scopes["layout"] == "QUOTE_PREFIX_OAC_SUFFIX"
    assert scopes["quote_business"]["status"] == "PASS"
    assert scopes["quote_business"]["events"] == quote_scope["events"]
    assert scopes["quote_business"]["head_digest"] == quote_scope["head_digest"]
    assert scopes["quote_business"]["definition"] == "CONTIGUOUS_NON_OAC_PREFIX"
    assert scopes["oac_adaptation"]["events"] == 2
    assert scopes["oac_adaptation"]["start_anchor_digest"] == quote_scope["head_digest"]
    assert scopes["relationship"]["quote_is_contiguous_prefix"] is True
    assert scopes["relationship"]["quote_is_contiguous_suffix"] is False
    assert scopes["relationship"]["oac_events_are_suffix"] is True


def test_oac_governance_can_be_an_honest_prefix_to_quote_business(
    workspace_service: WorkspaceService,
) -> None:
    workspace_service.store.record_event(
        "OAC_QUOTE_ADAPTATION_PREPARED",
        {"candidate_only": True, "canonical_target_writes": 0},
    )
    workspace_service.store.record_event(
        "OAC_QUOTE_ADAPTER_ADMITTED",
        {"candidate_only": True, "canonical_target_writes": 0},
    )
    workspace_service.form_quote()

    scopes = workspace_service.state()["event_scopes"]
    assert scopes["layout"] == "OAC_PREFIX_QUOTE_SUFFIX"
    assert scopes["oac_adaptation"]["events"] == 2
    assert scopes["quote_business"]["events"] == 1
    assert scopes["quote_business"]["definition"] == "CONTIGUOUS_NON_OAC_SUFFIX"
    assert scopes["quote_business"]["start_anchor_digest"] == scopes[
        "oac_adaptation"
    ]["head_digest"]
    assert scopes["relationship"]["oac_events_are_prefix"] is True
    assert scopes["relationship"]["quote_is_contiguous_suffix"] is True


def test_interleaved_oac_events_do_not_publish_a_false_quote_chain_head(
    workspace_service: WorkspaceService,
) -> None:
    workspace_service.store.record_event(
        "OAC_QUOTE_ADAPTATION_PREPARED",
        {"candidate_only": True, "canonical_target_writes": 0},
    )
    workspace_service.form_quote()
    workspace_service.store.record_event(
        "OAC_QUOTE_ADAPTER_ADMITTED",
        {"candidate_only": True, "canonical_target_writes": 0},
    )

    scopes = workspace_service.state()["event_scopes"]
    assert scopes["layout"] == "INTERLEAVED"
    assert scopes["quote_business"]["status"] == "NOT_CONTIGUOUS"
    assert scopes["quote_business"]["head_digest"] is None
    assert scopes["quote_business"]["first_sequence_no"] is None
    assert scopes["oac_adaptation"]["status"] == "INTERLEAVED"
    assert scopes["relationship"]["honest_contiguous_windows_published"] is False
    assert scopes["relationship"]["quote_is_contiguous_prefix"] is False
    assert scopes["relationship"]["quote_is_contiguous_suffix"] is False
    assert scopes["relationship"]["oac_events_are_prefix"] is False
    assert scopes["relationship"]["oac_events_are_suffix"] is False
