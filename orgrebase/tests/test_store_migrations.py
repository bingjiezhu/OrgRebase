from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.fixture import EnterpriseFixture
from orgrebase.store import StateStore
from orgrebase.store_migrations import STATE_STORE_SCHEMA_VERSION, migrate_state_store

# Frozen released unversioned DDL, deliberately independent of migration code.
_LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS object_versions (
    version_key TEXT PRIMARY KEY,
    object_id TEXT NOT NULL,
    version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    UNIQUE(object_id, version)
);
CREATE TABLE IF NOT EXISTS version_states (
    version_key TEXT PRIMARY KEY REFERENCES object_versions(version_key),
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS current_pointers (
    object_id TEXT PRIMARY KEY,
    version_key TEXT NOT NULL REFERENCES object_versions(version_key),
    revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS domain_events (
    sequence_no INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    previous_digest TEXT NOT NULL,
    event_digest TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS idempotency_records (
    key TEXT PRIMARY KEY,
    request_digest TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    media_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS external_operation_journal (
    operation_key TEXT PRIMARY KEY,
    request_digest TEXT NOT NULL,
    operation TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    status TEXT NOT NULL,
    expected_external_parent TEXT NOT NULL,
    external_operation_id TEXT,
    result_json TEXT,
    error_code TEXT
);
CREATE TABLE IF NOT EXISTS compensation_sagas (
    saga_key TEXT PRIMARY KEY,
    request_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    receipt_json TEXT NOT NULL
);
"""


def _legacy_database(path: Path, ddl: str = _LEGACY_SCHEMA) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(ddl)
        connection.commit()
    finally:
        connection.close()


def _rows(connection: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    tables = connection.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name").fetchall()
    return {
        table[0]: [tuple(row) for row in connection.execute(f'SELECT * FROM "{table[0]}" ORDER BY rowid')]
        for table in tables
    }


def test_new_store_schema_version_and_durability(tmp_path: Path) -> None:
    with StateStore(tmp_path / "new.sqlite") as store:
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == STATE_STORE_SCHEMA_VERSION
        assert store.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert store.connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert store.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert store.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
        assert not store.connection.in_transaction
        assert store.verify_event_chain()["events"] == 0


@pytest.mark.parametrize("legacy_version", [0, 1])
def test_legacy_upgrade_preserves_every_row_and_hash(
    tmp_path: Path, fixture: EnterpriseFixture, legacy_version: int
) -> None:
    path = tmp_path / "legacy.sqlite"
    _legacy_database(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"PRAGMA user_version = {legacy_version}")
        for item in fixture.objects:
            connection.execute(
                "INSERT INTO object_versions VALUES (?, ?, ?, ?, ?)",
                (item.ref, item.id, item.version, item.model_dump_json(), item.digest),
            )
            connection.execute("INSERT INTO version_states VALUES (?, ?)", (item.ref, item.state.value))
        item = fixture.objects[0]
        connection.execute("INSERT INTO current_pointers VALUES (?, ?, ?)", (item.id, item.ref, 7))
        previous = "sha256:" + "0" * 64
        for sequence in (1, 2):
            envelope = {
                "sequence_no": sequence,
                "event_type": "LEGACY",
                "payload": {"sequence": sequence},
                "previous_digest": previous,
            }
            digest = sha256_digest(envelope)
            connection.execute(
                "INSERT INTO domain_events VALUES (?, ?, ?, ?, ?)",
                (sequence, "LEGACY", canonical_json(envelope["payload"]), previous, digest),
            )
            previous = digest
        connection.execute(
            "INSERT INTO idempotency_records VALUES (?, ?, ?)",
            ("key", "request", canonical_json({"old": True})),
        )
        connection.execute(
            "INSERT INTO artifacts VALUES (?, ?, ?, ?)",
            ("artifact:old", "application/json", canonical_json({"old": True}), sha256_digest({"old": True})),
        )
        connection.execute(
            "INSERT INTO external_operation_journal VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("op:old", "request", "COMMIT", "repo", "EXTERNAL_COMMITTED", "parent", "commit", "{}", None),
        )
        connection.execute("INSERT INTO compensation_sagas VALUES ('saga', 'request', 'DONE', 2, '{}')")
        connection.commit()
        before = {name: rows for name, rows in _rows(connection).items() if not name.startswith("sqlite_")}
        columns = {
            name: [row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')] for name in before
        }
    finally:
        connection.close()

    def original_rows(connection):
        return {
            name: [
                tuple(row)
                for row in connection.execute(
                    "SELECT "
                    + ",".join('"' + column + '"' for column in names)
                    + f' FROM "{name}" ORDER BY rowid'
                )
            ]
            for name, names in columns.items()
        }

    with StateStore(path) as store:
        after = {
            name: rows for name, rows in _rows(store.connection).items() if not name.startswith("sqlite_")
        }
        assert original_rows(store.connection) == before
        for name in before:
            assert {row[0] for row in store.connection.execute(f'SELECT workspace_id FROM "{name}"')} <= {
                "default"
            }
        assert set(after) - set(before) == {
            "store_metadata",
            "effect_intents",
            "target_barriers",
            "source_checkpoints",
            "private_records",
            "workspace_registry",
            "workspace_changes",
            "oidc_login_transactions",
            "browser_sessions",
            "browser_session_revocations",
        }
        assert store.verify_event_chain() == {"status": "PASS", "events": 2, "head_digest": previous}
        assert store.load_artifact("artifact:old").payload == {"old": True}
        assert store.get_pointer(item.id)["revision"] == 7
    with StateStore(path) as store:
        assert original_rows(store.connection) == before
        assert store.audit_head() == {"sequence_no": 2, "head_digest": previous}
        store.record_event("AFTER_MIGRATION", {})
        assert store.verify_event_chain()["events"] == 3


def test_v2_literal_case_is_part_of_the_schema_contract(tmp_path: Path) -> None:
    path = tmp_path / "changed-literal.sqlite"
    with StateStore(path):
        pass
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute(
            "UPDATE sqlite_schema SET sql=replace(sql, '''READY''', '''ready''') WHERE name='effect_intents'"
        )
    with pytest.raises(IntegrityError, match="STATE_STORE_SCHEMA_INCOMPATIBLE"):
        StateStore(path)


@pytest.mark.parametrize("version", [-1, STATE_STORE_SCHEMA_VERSION + 1, 999])
def test_unknown_version_fails_without_changing_schema(tmp_path: Path, version: int) -> None:
    path = tmp_path / "future.sqlite"
    _legacy_database(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"PRAGMA user_version = {version}")
        before = connection.execute("SELECT * FROM sqlite_schema").fetchall()
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(IntegrityError, match=f"STATE_STORE_SCHEMA_VERSION_UNSUPPORTED:{version}"):
        StateStore(path)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT * FROM sqlite_schema").fetchall() == before
        assert connection.execute("PRAGMA user_version").fetchone()[0] == version
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        connection.close()


@pytest.mark.parametrize(
    "ddl",
    [
        _LEGACY_SCHEMA.replace("object_id TEXT NOT NULL", "object_id TEXTNOTNULL"),
        _LEGACY_SCHEMA.replace("UNIQUE(object_id, version)", "UNIQUE(object_id, payload_digest)"),
        _LEGACY_SCHEMA.replace(" REFERENCES object_versions(version_key)", ""),
        _LEGACY_SCHEMA.replace("revision INTEGER NOT NULL DEFAULT 1", "revision INTEGER NOT NULL DEFAULT 0"),
        _LEGACY_SCHEMA.replace(
            "CREATE TABLE IF NOT EXISTS artifacts", "CREATE TABLE IF NOT EXISTS absent_artifacts"
        ),
        _LEGACY_SCHEMA + "CREATE TABLE unrelated(value TEXT);",
        "CREATE TABLE artifacts(artifact_id TEXT PRIMARY KEY);",
    ],
)
def test_incompatible_schema_is_not_repaired_or_stamped(tmp_path: Path, ddl: str) -> None:
    path = tmp_path / "incompatible.sqlite"
    _legacy_database(path, ddl)
    connection = sqlite3.connect(path)
    try:
        before = connection.execute("SELECT * FROM sqlite_schema").fetchall()
    finally:
        connection.close()
    with pytest.raises(IntegrityError, match="STATE_STORE_SCHEMA_INCOMPATIBLE"):
        StateStore(path)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT * FROM sqlite_schema").fetchall() == before
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize("version", [0, 1])
@pytest.mark.parametrize(
    ("addition", "code"),
    [
        ("CREATE VIEW artifact_view AS SELECT * FROM artifacts;", "UNEXPECTED_BEHAVIOR"),
        (
            "CREATE TRIGGER suppress_event BEFORE INSERT ON domain_events BEGIN SELECT RAISE(IGNORE); END;",
            "UNEXPECTED_BEHAVIOR",
        ),
        ("CREATE UNIQUE INDEX exclusive_media ON artifacts(media_type);", "UNEXPECTED_UNIQUE_INDEX"),
    ],
)
def test_unknown_schema_behavior_is_rejected(tmp_path: Path, version: int, addition: str, code: str) -> None:
    path = tmp_path / "behavior.sqlite"
    _legacy_database(path, _LEGACY_SCHEMA + addition + f"PRAGMA user_version = {version};")
    with pytest.raises(IntegrityError, match=f"STATE_STORE_SCHEMA_{code}"):
        StateStore(path)


def test_non_unique_query_index_does_not_block_upgrade(tmp_path: Path) -> None:
    path = tmp_path / "indexed.sqlite"
    _legacy_database(path, _LEGACY_SCHEMA + "CREATE INDEX media_lookup ON artifacts(media_type);")
    with StateStore(path) as store:
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == STATE_STORE_SCHEMA_VERSION


def test_orphaned_legacy_foreign_key_blocks_upgrade(tmp_path: Path) -> None:
    path = tmp_path / "orphan.sqlite"
    _legacy_database(path, _LEGACY_SCHEMA + "INSERT INTO version_states VALUES ('missing', 'CURRENT');")
    with pytest.raises(IntegrityError, match="STATE_STORE_LEGACY_FOREIGN_KEY_VIOLATION"):
        StateStore(path)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("SELECT * FROM version_states").fetchall() == [("missing", "CURRENT")]
    finally:
        connection.close()


@pytest.mark.parametrize("phase", ["table", "version"])
def test_migration_failure_rolls_back_ddl_and_closes_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    path = tmp_path / "failed.sqlite"
    connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    def failing_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = connect(*args, **kwargs)
        opened.append(connection)

        def authorize(action: int, name: str | None, value: str | None, *unused: object) -> int:
            if phase == "table" and action == sqlite3.SQLITE_CREATE_TABLE and name == "artifacts":
                return sqlite3.SQLITE_DENY
            if phase == "version" and action == sqlite3.SQLITE_PRAGMA and name == "user_version" and value:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(sqlite3, "connect", failing_connect)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        StateStore(path)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")
    connection = connect(path)
    try:
        assert connection.execute("SELECT name FROM sqlite_schema").fetchall() == []
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        connection.close()


def test_migration_requires_transaction_ownership() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        with pytest.raises(RuntimeError, match="STATE_STORE_MIGRATION_REQUIRES_TRANSACTION"):
            migrate_state_store(connection)
        assert connection.execute("SELECT name FROM sqlite_schema").fetchall() == []
    finally:
        connection.close()


def test_legacy_formatting_and_keyword_case_are_compatible(tmp_path: Path) -> None:
    path = tmp_path / "formatted.sqlite"
    _legacy_database(path, _LEGACY_SCHEMA.upper().replace(",", " , ").replace("(", " ( "))
    with StateStore(path) as store:
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == STATE_STORE_SCHEMA_VERSION


@pytest.mark.parametrize("existing_schema", ["", _LEGACY_SCHEMA])
def test_sqlite_lookalike_table_is_not_ignored_or_adopted(tmp_path: Path, existing_schema: str) -> None:
    path = tmp_path / "foreign.sqlite"
    _legacy_database(
        path,
        existing_schema
        + "CREATE TABLE sqliteXcustomer_records(customer_id TEXT PRIMARY KEY, name TEXT NOT NULL);"
        + "INSERT INTO sqliteXcustomer_records VALUES ('customer:1', 'Preserve this customer');",
    )
    connection = sqlite3.connect(path)
    try:
        before_schema = connection.execute("SELECT * FROM sqlite_schema").fetchall()
        before_rows = _rows(connection)
    finally:
        connection.close()

    with pytest.raises(IntegrityError, match="STATE_STORE_SCHEMA_INCOMPATIBLE"):
        StateStore(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT * FROM sqlite_schema").fetchall() == before_schema
        assert _rows(connection) == before_rows
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    "ddl",
    [
        _LEGACY_SCHEMA.replace("artifacts (", "artifact\u017f ("),
        _LEGACY_SCHEMA.replace("state TEXT NOT NULL", "\u017ftate TEXT NOT NULL"),
    ],
)
def test_unicode_casefold_lookalikes_are_not_accepted_as_sqlite_identifiers(tmp_path: Path, ddl: str) -> None:
    path = tmp_path / "unicode.sqlite"
    _legacy_database(path, ddl)
    connection = sqlite3.connect(path)
    try:
        before_schema = connection.execute("SELECT * FROM sqlite_schema").fetchall()
    finally:
        connection.close()

    with pytest.raises(IntegrityError, match="STATE_STORE_SCHEMA_INCOMPATIBLE"):
        StateStore(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT * FROM sqlite_schema").fetchall() == before_schema
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        connection.close()
