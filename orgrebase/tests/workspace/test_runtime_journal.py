from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.workspace.runtime_journal import (
    ZERO_DIGEST,
    RuntimeJournal,
    RuntimeJournalConflict,
    RuntimeJournalIntegrityError,
    StaleRuntimeAttempt,
)

ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "run:test:durable-runtime"
ACTION_KEY = "quote:apply:quote-001"


def _request(revision: int = 1) -> dict[str, object]:
    return {
        "proposal_digest": sha256_digest({"quote": "quote-001", "revision": revision}),
        "expected_predecessor_digest": sha256_digest({"quote": "quote-001", "revision": 0}),
        "target": "quote:quote-001",
    }


def test_runtime_journal_tightens_file_permissions(tmp_path: Path) -> None:
    database = tmp_path / "runtime-journal.sqlite"
    database.touch(mode=0o644)
    database.chmod(0o644)

    with RuntimeJournal(database):
        assert database.stat().st_mode & 0o777 == 0o600


def test_close_reopen_retries_intent_then_adopts_exact_commit(tmp_path: Path) -> None:
    database = tmp_path / "runtime" / "journal.sqlite3"
    request = _request()
    request_digest = sha256_digest(request)

    journal = RuntimeJournal(database)
    started = journal.begin_action(
        run_id=RUN_ID,
        action_key=ACTION_KEY,
        action_type="APPLY_QUOTE",
        request=request,
    )
    assert started["state"] == "INTENT"
    assert started["transition"] == "INTENT_STARTED"
    assert started["attempt"] == 1
    assert started["previous_action_digest"] == ZERO_DIGEST
    first_checkpoint = journal.write_checkpoint(
        run_id=RUN_ID,
        action_key=ACTION_KEY,
        attempt=started["attempt"],
        fencing_token=started["fencing_token"],
        checkpoint_key="proposal-validated",
        payload={"proposal_digest": request["proposal_digest"]},
    )
    journal.close()

    reopened = RuntimeJournal(database)
    recovered = reopened.recover_action(
        run_id=RUN_ID,
        action_key=ACTION_KEY,
        request_digest=request_digest,
    )
    assert recovered["disposition"] == "RETRY_INTENT"
    assert recovered["attempt"] == 2
    assert recovered["fencing_token"] != started["fencing_token"]
    assert recovered["checkpoint"] == first_checkpoint
    assert recovered["action_receipt"]["previous_action_digest"] == started["digest"]

    with pytest.raises(StaleRuntimeAttempt, match="STALE_RUNTIME_ATTEMPT"):
        reopened.write_checkpoint(
            run_id=RUN_ID,
            action_key=ACTION_KEY,
            attempt=started["attempt"],
            fencing_token=started["fencing_token"],
            checkpoint_key="late-worker",
            payload={"must": "not persist"},
        )
    with pytest.raises(StaleRuntimeAttempt, match="STALE_RUNTIME_ATTEMPT"):
        reopened.commit_action(
            run_id=RUN_ID,
            action_key=ACTION_KEY,
            attempt=started["attempt"],
            fencing_token=started["fencing_token"],
            result={"canonical_version": "v2"},
        )

    retry_checkpoint = reopened.write_checkpoint(
        run_id=RUN_ID,
        action_key=ACTION_KEY,
        attempt=recovered["attempt"],
        fencing_token=recovered["fencing_token"],
        checkpoint_key="approval-bound",
        payload={"approval_digest": sha256_digest({"approved": request_digest})},
    )
    result = {
        "canonical_version": "quote:quote-001@v2",
        "write_set_digest": sha256_digest(["quote:quote-001@v2"]),
    }
    committed = reopened.commit_action(
        run_id=RUN_ID,
        action_key=ACTION_KEY,
        attempt=recovered["attempt"],
        fencing_token=recovered["fencing_token"],
        result=result,
    )
    assert committed["state"] == "COMMITTED"
    assert committed["checkpoint_digest"] == retry_checkpoint["digest"]
    assert committed["result_digest"] == sha256_digest(result)
    assert [item["transition"] for item in reopened.list_action_receipts(RUN_ID)] == [
        "INTENT_STARTED",
        "INTENT_RECOVERED",
        "ACTION_COMMITTED",
    ]
    assert reopened.verify_chain(RUN_ID) == {
        "schema_version": "orgrebase.runtime-journal-verification.v1",
        "run_id": RUN_ID,
        "valid": True,
        "action_count": 1,
        "attempt_count": 2,
        "checkpoint_count": 2,
        "action_receipt_count": 3,
        "head_action_digest": committed["digest"],
    }
    reopened.close()

    final_process = RuntimeJournal(database)
    adopted = final_process.recover_action(
        run_id=RUN_ID,
        action_key=ACTION_KEY,
        request_digest=request_digest,
    )
    assert adopted["disposition"] == "ADOPT_COMMITTED"
    assert adopted["result"] == result
    assert adopted["action_receipt"] == committed
    assert final_process.get_action(run_id=RUN_ID, action_key=ACTION_KEY)["result"] == result
    final_process.close()


def test_action_checkpoint_and_commit_are_content_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "journal.sqlite3"
    request = _request()
    with RuntimeJournal(database) as journal:
        started = journal.begin_action(
            run_id=RUN_ID,
            action_key=ACTION_KEY,
            action_type="APPLY_QUOTE",
            request=request,
        )
        assert (
            journal.begin_action(
                run_id=RUN_ID,
                action_key=ACTION_KEY,
                action_type="APPLY_QUOTE",
                request=request,
                request_digest=sha256_digest(request),
            )
            == started
        )
        with pytest.raises(RuntimeJournalConflict, match="ACTION_IDEMPOTENCY_CONFLICT"):
            journal.begin_action(
                run_id=RUN_ID,
                action_key=ACTION_KEY,
                action_type="APPLY_QUOTE",
                request=_request(revision=2),
            )
        with pytest.raises(RuntimeJournalConflict, match="ACTION_TYPE_CONFLICT"):
            journal.begin_action(
                run_id=RUN_ID,
                action_key=ACTION_KEY,
                action_type="ROLLBACK_QUOTE",
                request=request,
            )
        with pytest.raises(RuntimeJournalConflict, match="REQUEST_DIGEST_MISMATCH"):
            journal.begin_action(
                run_id=RUN_ID,
                action_key="quote:apply:forged",
                action_type="APPLY_QUOTE",
                request=request,
                request_digest=sha256_digest({"different": True}),
            )

        checkpoint = journal.write_checkpoint(
            run_id=RUN_ID,
            action_key=ACTION_KEY,
            attempt=1,
            fencing_token=started["fencing_token"],
            checkpoint_key="validated",
            payload={"valid": True},
        )
        assert (
            journal.write_checkpoint(
                run_id=RUN_ID,
                action_key=ACTION_KEY,
                attempt=1,
                fencing_token=started["fencing_token"],
                checkpoint_key="validated",
                payload={"valid": True},
            )
            == checkpoint
        )
        with pytest.raises(RuntimeJournalConflict, match="CHECKPOINT_IDEMPOTENCY_CONFLICT"):
            journal.write_checkpoint(
                run_id=RUN_ID,
                action_key=ACTION_KEY,
                attempt=1,
                fencing_token=started["fencing_token"],
                checkpoint_key="validated",
                payload={"valid": False},
            )

        result = {"canonical_version": "v2"}
        committed = journal.commit_action(
            run_id=RUN_ID,
            action_key=ACTION_KEY,
            attempt=1,
            fencing_token=started["fencing_token"],
            result=result,
        )
        assert (
            journal.commit_action(
                run_id=RUN_ID,
                action_key=ACTION_KEY,
                attempt=1,
                fencing_token=started["fencing_token"],
                result=result,
            )
            == committed
        )
        with pytest.raises(RuntimeJournalConflict, match="COMMITTED_RESULT_CONFLICT"):
            journal.commit_action(
                run_id=RUN_ID,
                action_key=ACTION_KEY,
                attempt=1,
                fencing_token=started["fencing_token"],
                result={"canonical_version": "v3"},
            )
        assert (
            journal.begin_action(
                run_id=RUN_ID,
                action_key=ACTION_KEY,
                action_type="APPLY_QUOTE",
                request=request,
            )
            == committed
        )


def test_chain_spans_actions_and_detects_sqlite_tampering(tmp_path: Path) -> None:
    database = tmp_path / "journal.sqlite3"
    with RuntimeJournal(database) as journal:
        first = journal.begin_action(
            run_id=RUN_ID,
            action_key=ACTION_KEY,
            action_type="APPLY_QUOTE",
            request=_request(),
        )
        first_commit = journal.commit_action(
            run_id=RUN_ID,
            action_key=ACTION_KEY,
            attempt=first["attempt"],
            fencing_token=first["fencing_token"],
            result={"version": "v2"},
        )
        second = journal.begin_action(
            run_id=RUN_ID,
            action_key="quote:rollback:quote-001",
            action_type="ROLLBACK_QUOTE",
            request={"predecessor": "v1", "current": "v2"},
        )
        assert second["previous_action_digest"] == first_commit["digest"]
        report = journal.verify_chain(RUN_ID)
        assert report["action_count"] == 2
        assert report["action_receipt_count"] == 3

    connection = sqlite3.connect(database)
    row = connection.execute(
        "SELECT receipt_json FROM runtime_action_receipts WHERE run_id=? AND sequence=2",
        (RUN_ID,),
    ).fetchone()
    payload = json.loads(row[0])
    payload["created_at"] = "2099-01-01T00:00:00Z"
    connection.execute(
        "UPDATE runtime_action_receipts SET receipt_json=? WHERE run_id=? AND sequence=2",
        (json.dumps(payload, sort_keys=True), RUN_ID),
    )
    connection.commit()
    connection.close()

    with (
        RuntimeJournal(database) as journal,
        pytest.raises(RuntimeJournalIntegrityError, match="ACTION_RECEIPT_CHAIN_INVALID"),
    ):
        journal.verify_chain(RUN_ID)


def test_action_receipt_schema_covers_every_runtime_transition() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "workspace-runtime-action-receipt.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert schema["properties"]["schema_version"]["const"] == (
        "orgrebase.workspace-runtime-action-receipt.v1"
    )
    assert schema["properties"]["state"]["enum"] == ["INTENT", "COMMITTED"]
    assert set(schema["properties"]["transition"]["enum"]) == {
        "INTENT_STARTED",
        "INTENT_RECOVERED",
        "ACTION_COMMITTED",
    }
    assert "previous_action_digest" in schema["required"]
    assert "fencing_token" in schema["required"]
