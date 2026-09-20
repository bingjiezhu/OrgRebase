from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from orgrebase.store import StateStore
from tests.workspace.test_enterprise_pilot_delivery import VERIFIER, PilotEvidenceError


def database(tmp_path, fixture, schema_version):
    path = tmp_path / "pilot.sqlite3"
    with StateStore(path) as store:
        store.load_fixture(fixture)
        quote = store.get_object("work:sales_quote_a").model_dump(mode="json")
        chain = store.verify_event_chain()
    # Versions 3/4/5 share the evidence table shapes; this changes only the fixture's
    # declared version, with no historical production database being rewritten.
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE store_metadata SET schema_version=?", (schema_version,))
        connection.execute(f"PRAGMA user_version={schema_version}")
    return path, {"expected_quote": quote, "expected_event_chain": chain, "expected_changes": {}}


@pytest.mark.parametrize("version", [3, 4, 5])
def test_independent_verifier_reads_known_scoped_versions_without_modifying_bytes(tmp_path, fixture, version):
    path, expected = database(tmp_path, fixture, version)
    before = path.read_bytes()
    digest, objects, artifacts = VERIFIER._verify_sqlite(path, **expected)
    assert digest.startswith("sha256:") and objects == len(fixture.objects) and artifacts == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize("metadata_version,pragma_version", [(3, 4), (4, 3), (4, 5), (5, 4), (6, 6), (2, 2)])
def test_independent_verifier_rejects_unknown_or_inconsistent_version(tmp_path, fixture, metadata_version, pragma_version):
    path, expected = database(tmp_path, fixture, metadata_version)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version={pragma_version}")
    before = path.read_bytes()
    with pytest.raises(PilotEvidenceError, match="SQLITE_SCHEMA_VERSION_"):
        VERIFIER._verify_sqlite(path, **expected)
    assert path.read_bytes() == before


@pytest.mark.parametrize("version", [3, 4, 5])
def test_independent_verifier_keeps_event_digest_checks_in_every_supported_version(tmp_path, fixture, version):
    path, expected = database(tmp_path, fixture, version)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE domain_events SET payload_json='{}'")
    before = path.read_bytes()
    with pytest.raises(PilotEvidenceError, match="SQLITE_EVENT_DIGEST"):
        VERIFIER._verify_sqlite(path, **expected)
    assert path.read_bytes() == before
