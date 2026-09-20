from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from scripts import verify_governed_semifinal_apply as verifier

ROOT = Path(__file__).resolve().parents[2]
PARENT = ROOT / "evidence/semifinal-closure/latest"


@pytest.fixture
def archived_pack(tmp_path):
    source = ROOT / "evidence/semifinal-governed/latest"
    index = json.loads((source / "evidence-index.json").read_text())
    root = tmp_path / "sealed archive #1"
    root.mkdir()
    # Reconstruct only the sealed input bytes. Local SQLite sidecars are not
    # evidence inputs; the real verifier still rejects any unindexed extra file.
    for entry in index["entries"]:
        original = source / entry["path"]
        assert "sha256:" + hashlib.sha256(original.read_bytes()).hexdigest() == entry["sha256"]
        target = root / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    shutil.copyfile(source / "evidence-index.json", root / "evidence-index.json")
    return root


def _inventory(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def _seal_test_copy(root):
    index = json.loads((root / "evidence-index.json").read_text())
    entries = [{"path": path.relative_to(root).as_posix(), "sha256": verifier._file_digest(path),
                "bytes": path.stat().st_size}
               for path in sorted(root.rglob("*")) if path.is_file() and path.name != "evidence-index.json"]
    body = {**index, "entries": entries, "entry_count": len(entries), "pack_digest": verifier._digest(entries)}
    body.pop("digest")
    (root / "evidence-index.json").write_text(json.dumps({**body, "digest": verifier._digest(body)}))


def test_verified_archive_reads_are_read_only_without_sidecars(archived_pack, monkeypatch):
    before = _inventory(archived_pack)
    connect = sqlite3.connect
    opened = []

    def inspect(database, **kwargs):
        assert kwargs == {"uri": True}
        assert database.endswith("?mode=ro&immutable=1")
        connection = connect(database, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE forbidden_reader_write(value TEXT)")
        opened.append(database)
        return connection

    monkeypatch.setattr(verifier.sqlite3, "connect", inspect)
    for _ in range(2):
        result = verifier.verify(archived_pack, PARENT)
        assert result["status"] == "PASS", result
        assert result["verification_mode"] == "INDEPENDENT_STDLIB_JSON_SQLITE_REPLAY"
    assert len(opened) == 4
    assert _inventory(archived_pack) == before
    assert not tuple(archived_pack.rglob("*-wal")) and not tuple(archived_pack.rglob("*-shm"))


@pytest.mark.parametrize("mutation", ["main-database", "unindexed-file"])
def test_unsealed_archive_is_rejected_before_opening_sqlite(archived_pack, monkeypatch, mutation):
    if mutation == "main-database":
        path = archived_pack / "runtime/workspace.sqlite"
        path.write_bytes(path.read_bytes() + b"unsealed bytes")
    else:
        (archived_pack / "unindexed.txt").write_text("not in the seal")

    def forbidden(*args, **kwargs):
        pytest.fail("an invalid index must not authorize opening an archive database")

    monkeypatch.setattr(verifier.sqlite3, "connect", forbidden)
    before = _inventory(archived_pack)
    result = verifier.verify(archived_pack, PARENT)
    assert result["status"] == "FAIL" and "INDEX_ENTRIES" in result["failures"]
    assert _inventory(archived_pack) == before


def test_indexed_uncheckpointed_wal_cannot_be_ignored_by_immutable_reader(archived_pack, monkeypatch):
    connection = sqlite3.connect(archived_pack / "runtime/workspace.sqlite")
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE uncheckpointed_business_work(value TEXT)")
        connection.commit()
        assert (archived_pack / "runtime/workspace.sqlite-wal").stat().st_size > 0
        _seal_test_copy(archived_pack)

        def forbidden(*args, **kwargs):
            pytest.fail("pending WAL must be rejected before any immutable read")

        monkeypatch.setattr(verifier.sqlite3, "connect", forbidden)
        before = _inventory(archived_pack)
        result = verifier.verify(archived_pack, PARENT)
        assert result["status"] == "FAIL"
        assert "ARCHIVE_SQLITE_NOT_CHECKPOINTED:workspace.sqlite" in result["failures"]
        assert _inventory(archived_pack) == before
    finally:
        connection.close()
