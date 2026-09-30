"""Release identity follows the loaded checkout or installed wheel."""

from importlib import metadata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import orgrebase
from orgrebase.api import create_app
from orgrebase.observability import ObservabilityExporter
from orgrebase.service import OrgRebaseService
from orgrebase.workspace.controlled_local import build_joint_otlp


def test_release_facts_are_labeled_when_they_belong_to_an_older_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = tmp_path / "release-facts.json"
    facts.write_text('{"release":"0.1.0","frozen_receipt":"unchanged"}', encoding="utf-8")
    monkeypatch.setattr("orgrebase.api.RELEASE_FACTS_PATH", facts)
    service = OrgRebaseService()
    try:
        with TestClient(create_app(service)) as client:
            response = client.get("/api/release-facts").json()
        assert response["release"] == "0.1.0"
        assert response["frozen_receipt"] == "unchanged"
        assert response["release_context"] == {
            "runtime_version": orgrebase.__version__,
            "evidence_release": "0.1.0",
            "relationship": "HISTORICAL_RELEASE_FACTS",
        }
    finally:
        service.store.close()


def test_checkout_version_does_not_use_another_installed_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = tmp_path / "src/orgrebase/__init__.py"
    module.parent.mkdir(parents=True)
    module.touch()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "orgrebase"\nversion = "0.5.0b1"\n', encoding="utf-8",
    )
    monkeypatch.setattr(orgrebase, "__file__", str(module))
    monkeypatch.setattr(metadata, "version", lambda name: "0.4.0")
    assert orgrebase._resolve_version() == "0.5.0b1"


def test_installed_version_uses_distribution_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = tmp_path / "site-packages/orgrebase/__init__.py"
    module.parent.mkdir(parents=True)
    module.touch()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "another-project"\nversion = "9.9.9"\n', encoding="utf-8",
    )
    monkeypatch.setattr(orgrebase, "__file__", str(module))
    monkeypatch.setattr(metadata, "version", lambda name: "0.5.0b1")
    assert orgrebase._resolve_version() == "0.5.0b1"


def test_uninstalled_copy_does_not_claim_a_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = tmp_path / "copy/orgrebase/__init__.py"
    module.parent.mkdir(parents=True)
    module.touch()
    monkeypatch.setattr(orgrebase, "__file__", str(module))

    def missing(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", missing)
    assert orgrebase._resolve_version() == "0+unknown"


def test_api_and_telemetry_report_the_loaded_release() -> None:
    service = OrgRebaseService()
    try:
        with TestClient(create_app(service)) as client:
            assert client.get("/api/health").json()["version"] == orgrebase.__version__
            assert client.get("/readyz").json()["version"] == orgrebase.__version__
            assert client.get("/openapi.json").json()["info"]["version"] == orgrebase.__version__
        assert ObservabilityExporter.scope["version"] == orgrebase.__version__
        signals = build_joint_otlp(
            run_id="version-check", organization_id="enterprise", receipt_digest="sha256:receipt",
            task_id="task", skill_digest="sha256:skill", layers={},
        )
        for signal in signals.values():
            resource_key = next(iter(signal))
            attributes = signal[resource_key][0]["resource"]["attributes"]
            values = {item["key"]: item["value"]["stringValue"] for item in attributes}
            assert values["service.version"] == orgrebase.__version__
    finally:
        service.store.close()
