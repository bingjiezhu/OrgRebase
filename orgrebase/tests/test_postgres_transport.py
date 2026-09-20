from __future__ import annotations

from urllib.parse import quote

import pytest

from orgrebase.auth import IdentitySettings
from orgrebase.runtime_config import DeploymentSettings, validate_deployment_environment


@pytest.fixture(autouse=True)
def clean_transport_environment(monkeypatch):
    for name in ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSSLMODE", "PGGSSENCMODE"):
        monkeypatch.delenv(name, raising=False)


def production(database_url, tmp_path):
    return DeploymentSettings(
        mode="production", database_url=database_url, enterprise_pack="configured-pack",
        allowed_hosts=("service.example",),
        identity=IdentitySettings("https://issuer.example", "resource-api", "https://issuer.example/jwks",
                                  "org:test", tmp_path / "membership.json"),
    )


@pytest.mark.parametrize("uri_form", ["query", "authority"])
def test_production_accepts_explicit_unix_socket_path(uri_form, tmp_path):
    socket_path = quote(str(tmp_path / "postgres socket"), safe="")
    dsn = (f"postgresql:///app?host={socket_path}" if uri_form == "query"
           else f"postgresql://{socket_path}/app")
    assert production(dsn, tmp_path).database_url == dsn


@pytest.mark.parametrize("dsn", [
    "postgresql://127.0.0.1/app", "postgresql://[::1]/app?sslmode=disable",
    "postgresql://127.0.0.1,127.0.0.2/app", "postgresql://declared.example/app?hostaddr=127.0.0.1",
    "postgresql://db.example/app?sslmode=verify-full&gssencmode=disable",
    "postgresql://db.example/app?hostaddr=192.0.2.1&sslmode=verify-full&gssencmode=disable",
    "postgresql://127.0.0.1,db.example/app?sslmode=verify-full&gssencmode=disable",
])
def test_production_accepts_explicit_local_transport_or_verified_tls(dsn, tmp_path):
    assert production(dsn, tmp_path).database_url == dsn


@pytest.mark.parametrize("dsn", [
    "postgresql://db.example/app", "postgresql://db.example/app?sslmode=disable",
    "postgresql://db.example/app?sslmode=prefer", "postgresql://db.example/app?sslmode=require",
    "postgresql://db.example/app?sslmode=verify-ca", "postgresql://db.example/app?sslmode=verify-full",
    "postgresql://db.example/app?sslmode=verify-full&gssencmode=prefer",
    "postgresql://localhost/app", "postgresql://localhost./app?sslmode=disable",
    "postgresql://127.0.0.1/app?host=db.example&sslmode=disable",
    "postgresql://127.0.0.1/app?hostaddr=192.0.2.1&sslmode=disable",
    "postgresql://db.example/app?sslmode=verify-full&gssencmode=disable&sslmode=disable",
    "postgresql://127.0.0.1,db.example/app?sslmode=disable",
    "postgresql:///app", "postgresql://127.0.0.1/app?host=", "postgresql://127.0.0.1,/app",
    "postgresql:///app?hostaddr=127.0.0.1", "postgresql://127.0.0.1/app?service=deployment",
    "postgresql://127.0.0.1/app?hostaddr=127.0.0.1,192.0.2.1",
    "postgresql://127.0.0.1/app?hostaddr=not-an-address",
])
def test_production_rejects_implicit_or_unverified_transport(dsn, tmp_path):
    with pytest.raises(ValueError, match="PRODUCTION_POSTGRES_"):
        production(dsn, tmp_path)


def test_production_does_not_accept_environment_defaults_as_explicit_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("PGHOST", "127.0.0.1")
    monkeypatch.setenv("PGSSLMODE", "verify-full")
    monkeypatch.setenv("PGGSSENCMODE", "disable")
    with pytest.raises(ValueError, match="EXPLICIT_HOST_REQUIRED"):
        production("postgresql:///app", tmp_path)
    with pytest.raises(ValueError, match="VERIFIED_TLS_REQUIRED"):
        production("postgresql://db.example/app", tmp_path)
    assert production("postgresql://127.0.0.1/app", tmp_path)


@pytest.mark.parametrize("name", ["PGHOSTADDR", "PGSERVICE"])
def test_startup_rechecks_environment_indirection_after_settings_creation(name, tmp_path, monkeypatch):
    settings = production("postgresql://127.0.0.1/app", tmp_path)
    monkeypatch.setenv(name, "192.0.2.1" if name == "PGHOSTADDR" else "deployment")
    with pytest.raises(ValueError, match="PRODUCTION_POSTGRES_"):
        validate_deployment_environment(settings)


def test_local_mode_keeps_existing_transport_compatibility(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    monkeypatch.setenv("PGSERVICE", "local-profile")
    settings = DeploymentSettings(mode="local", database_url="postgresql:///app?sslmode=disable")
    validate_deployment_environment(settings)
