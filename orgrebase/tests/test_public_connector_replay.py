from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_public_connector_replay.py"
SPEC = importlib.util.spec_from_file_location("public_connector_replay", SCRIPT)
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


@pytest.fixture(scope="module")
def sample():
    return replay.select_events(replay.DATASET, 8)


def test_selection_is_pinned_deterministic_and_preserves_original_fields(sample):
    events, metadata = sample
    repeated, again = replay.select_events(replay.DATASET, 8)
    assert events == repeated
    assert metadata == again
    assert metadata["available_events"] == 45227
    assert "128" in metadata["selection_bias"]
    for event in events:
        assert {"event_ref", "case_ref", "purchase_document_ref", "item_ref", "occurred_at",
                "cumulative_net_worth_eur", "lineage"} <= event.keys()
        record = replay.protocol_record(event)
        assert json.loads(record["new_observed_event"]) == event
        assert replay.protocol_record(event) == record


def test_changed_manifest_is_refused_before_data_is_loaded(tmp_path):
    (tmp_path / "MANIFEST.sha256").write_text("changed manifest\n")
    with pytest.raises(ValueError, match="FROZEN_MANIFEST_CHANGED"):
        replay.select_events(tmp_path, 8)


def test_real_tls_shared_database_and_restart_faults(tmp_path, sample):
    events, metadata = sample
    results = [replay.run_workload(events, tmp_path / f"workers-{workers}", workers, 2, 1)
               for workers in (1, 4)]
    for result in results:
        assert result["sample_digest"] == metadata["sample_digest"]
        assert result["records_verified"] == 8
        assert result["losses"] == result["extra_or_duplicate_rows"] == result["content_mismatches"] == 0
        assert result["audit_chain"]["events"] == 8
        assert sum(result["http_requests"].values()) == 8
        assert len(result["page_timings"]) == 8
        assert result["wall_seconds"] > 0
    faults = replay.run_faults(events, tmp_path / "faults")
    assert faults["rate-limit"]["recovery_http_requests"] == 1
    assert faults["admission-failure"]["recovery_http_requests"] == 0
    assert all(result["status"] == "PASS" and result["records_verified"] == 3
               for result in faults.values())
