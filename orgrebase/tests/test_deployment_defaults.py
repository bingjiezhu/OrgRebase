from __future__ import annotations

import sys

import pytest
import uvicorn

from orgrebase import cli
from orgrebase.api import create_app
from orgrebase.runtime_config import DeploymentSettings


def test_unconfigured_factory_cannot_create_an_unauthenticated_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("ORGREBASE_DEPLOYMENT_MODE")
    monkeypatch.setenv("ORGREBASE_AUTH_ISSUER", "")
    monkeypatch.setenv("ORGREBASE_AUTH_JWKS_URL", "")
    database = tmp_path / "must-not-be-created.sqlite"
    monkeypatch.setenv("ORGREBASE_WORKSPACE_DB", str(database))
    with pytest.raises(ValueError, match="AUTH_HTTPS_ENDPOINT_REQUIRED"):
        create_app()
    assert not database.exists()
    with pytest.raises(ValueError, match="PRODUCTION_IDENTITY_REQUIRED"):
        DeploymentSettings()


@pytest.mark.parametrize("mode", ["", "typo", "LOCAL"])
def test_invalid_mode_never_becomes_local(mode, monkeypatch):
    monkeypatch.setenv("ORGREBASE_DEPLOYMENT_MODE", mode)
    with pytest.raises(ValueError, match="DEPLOYMENT_MODE_INVALID"):
        DeploymentSettings.from_environment()


@pytest.mark.parametrize("mode_arguments", [["--local-demo"], []])
@pytest.mark.parametrize("mode", ["local", " local "])
def test_local_cli_refuses_non_loopback_before_listening(mode_arguments, mode, monkeypatch):
    monkeypatch.setenv("ORGREBASE_DEPLOYMENT_MODE", mode)
    monkeypatch.setattr(sys, "argv", ["orgrebase", "serve", *mode_arguments, "--host", "0.0.0.0"])
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: pytest.fail("must not listen"))
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2


def test_local_demo_is_explicit_and_keeps_the_existing_application_factory(monkeypatch):
    monkeypatch.delenv("ORGREBASE_DEPLOYMENT_MODE")
    monkeypatch.setattr(sys, "argv", ["orgrebase", "serve", "--local-demo"])
    observed = []
    def start(application, **kwargs):
        observed.append((application, kwargs, DeploymentSettings.from_environment().mode))
    monkeypatch.setattr(uvicorn, "run", start)
    cli.main()
    assert observed == [("orgrebase.api:create_app",
                         {"factory": True, "host": "127.0.0.1", "port": 8081}, "local")]


@pytest.mark.parametrize("option,value", [
    ("--limit-concurrency", "0"), ("--limit-concurrency", "1"),
    ("--limit-concurrency", "-1"), ("--backlog", "0"), ("--backlog", "-1"),
])
def test_invalid_server_limits_fail_before_listening(monkeypatch, option, value):
    monkeypatch.setattr(sys, "argv", ["orgrebase", "serve", option, value])
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: pytest.fail("must not listen"))
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2


def test_configured_limits_use_native_uvicorn_admission(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "orgrebase", "serve", "--limit-concurrency", "16", "--backlog", "128",
    ])
    observed = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: observed.append((args, kwargs)))
    cli.main()
    assert observed == [(("orgrebase.api:create_app",), {
        "factory": True, "host": "127.0.0.1", "port": 8081,
        "limit_concurrency": 16, "backlog": 128,
    })]
