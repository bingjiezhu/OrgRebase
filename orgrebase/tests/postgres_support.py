"""Isolated PostgreSQL fixtures shared by storage and production runtime tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


@pytest.fixture(scope="module")
def postgres_cluster():
    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    if not initdb or not pg_ctl:
        if os.environ.get("ORGREBASE_REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("PostgreSQL initdb and pg_ctl are required by ORGREBASE_REQUIRE_POSTGRES_TESTS=1")
        pytest.skip("PostgreSQL initdb and pg_ctl are required for real database validation")
    # A short, private socket path avoids macOS Unix-socket length limits.
    with tempfile.TemporaryDirectory(prefix="obr-pg-", dir=Path(os.path.sep) / "tmp") as directory:
        root = Path(directory)
        data = root / "data"
        socket = root / "socket"
        socket.mkdir(mode=0o700)
        subprocess.run(
            [
                initdb,
                "-D",
                str(data),
                "-A",
                "trust",
                "-U",
                "orgrebase_test",
                "--no-locale",
                "--encoding=UTF8",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        subprocess.run(
            [
                pg_ctl,
                "-D",
                str(data),
                "-l",
                str(root / "server.log"),
                "-o",
                f"-k {socket} -c listen_addresses=''",
                "-w",
                "start",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        try:
            yield f"postgresql://orgrebase_test@/postgres?host={socket}"
        finally:
            subprocess.run(
                [pg_ctl, "-D", str(data), "-w", "stop", "-m", "fast"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )


@pytest.fixture
def postgres_dsn(postgres_cluster: str):
    database = f"test_{uuid4().hex}"
    with psycopg.connect(postgres_cluster, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    yield postgres_cluster.replace("/postgres?", f"/{database}?")
    with psycopg.connect(postgres_cluster, autocommit=True) as connection:
        connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))


@pytest.fixture
def postgres_runtime(postgres_cluster):
    """Create an operator-migrated database and a normal RLS runtime role."""
    created = []

    def create(*, tenant_id="org:test"):
        from orgrebase.store_operations import database_dsn, migrate_postgres

        suffix = uuid4().hex
        database = "runtime_test_" + suffix
        role = "app_" + suffix
        with psycopg.connect(postgres_cluster, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT").format(sql.Identifier(role))
            )
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        created.append((database, role))
        migration_dsn = database_dsn(postgres_cluster, database)
        migrate_postgres(migration_dsn, tenant_id=tenant_id, runtime_role=role)
        parsed = urlsplit(migration_dsn)
        runtime_dsn = urlunsplit(parsed._replace(netloc=role + "@"))
        return {"migration_dsn": migration_dsn, "runtime_dsn": runtime_dsn, "role_name": role}

    try:
        yield create
    finally:
        with psycopg.connect(postgres_cluster, autocommit=True) as connection:
            for database, role in reversed(created):
                connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
                connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
