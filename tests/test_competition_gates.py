from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from orgrebase.digest import sha256_digest

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agentteams_preflight = _load_script("agentteams_preflight")
collect_agentteams_evidence = _load_script("collect_agentteams_evidence")
goai_gate = _load_script("goai_gate")
verify_evidence_manifest = _load_script("verify_evidence_manifest")


def test_agentteams_preflight_never_discloses_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agentteams_preflight.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(agentteams_preflight, "_command", lambda command: (True, "ok"))
    monkeypatch.setenv("AGENTTEAMS_LLM_API_KEY", "must-not-appear")
    report = agentteams_preflight.preflight()
    assert report["status"] == "READY"
    assert report["secrets_disclosed"] is False
    assert "must-not-appear" not in json.dumps(report)


def test_agentteams_preflight_accepts_vertex_adc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agentteams_preflight.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(agentteams_preflight, "_command", lambda command: (True, "ok"))
    monkeypatch.setattr(
        agentteams_preflight,
        "_vertex_adc",
        lambda: (True, "location=global; model=test; credentials=adc-present"),
    )
    monkeypatch.delenv("AGENTTEAMS_LLM_API_KEY", raising=False)
    report = agentteams_preflight.preflight()
    assert report["status"] == "READY"
    assert report["checks"]["llm.authentication"]["mode"] == "vertex-adc"
    assert report["deployment_model"] == "google/gemini-3.1-flash-lite"


def test_agentteams_preflight_redacts_vertex_project_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agentteams_preflight.shutil, "which", lambda name: f"/bin/{name}")

    def command_result(command: list[str]) -> tuple[bool, str]:
        if command[:2] == ["docker", "info"]:
            return True, "24.0.0"
        if command[:3] == ["gcloud", "config", "get-value"]:
            return True, "must-not-leak-gcp-project"
        if command[:4] == ["gcloud", "auth", "application-default", "print-access-token"]:
            return True, "ya29.must-not-leak-adc-token"
        return True, "ok"

    monkeypatch.setattr(agentteams_preflight, "_command", command_result)
    monkeypatch.delenv("AGENTTEAMS_LLM_API_KEY", raising=False)
    report = agentteams_preflight.preflight()
    blob = json.dumps(report)
    assert report["status"] == "READY"
    assert report["checks"]["llm.authentication"]["mode"] == "vertex-adc"
    assert "must-not-leak-gcp-project" not in blob
    assert "ya29" not in blob
    assert "must-not-leak-adc-token" not in blob


def test_published_agentteams_evidence_does_not_disclose_vertex_api() -> None:
    evidence_root = ROOT / "evidence" / "agentteams"
    forbidden = (
        "gen-lang-client-",
        "ya29.",
        "AIza",
        "GOCSPX-",
        "BEGIN PRIVATE KEY",
        "Bearer ",
    )
    for path in evidence_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl", ".txt", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{path} discloses Vertex/Gemini credential material"


def test_agentteams_preflight_rejects_empty_docker_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agentteams_preflight.shutil, "which", lambda name: f"/bin/{name}")

    def command_result(command: list[str]) -> tuple[bool, str]:
        if command[:2] == ["docker", "info"]:
            return True, "exit=0"
        return True, "ok"

    monkeypatch.setattr(agentteams_preflight, "_command", command_result)
    monkeypatch.setattr(agentteams_preflight, "_vertex_adc", lambda: (True, "ok"))
    monkeypatch.delenv("AGENTTEAMS_LLM_API_KEY", raising=False)
    report = agentteams_preflight.preflight()
    assert report["status"] == "BLOCKED"
    assert report["checks"]["docker.daemon"]["status"] == "BLOCKED"


def test_goai_stage_gate_will_not_upgrade_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(goai_gate, "ROOT", tmp_path)
    (tmp_path / "submission").mkdir()
    (tmp_path / "submission" / "INTRO.md").write_text("# title\nshort", encoding="utf-8")
    (tmp_path / "submission" / "deck.pdf").write_bytes(b"%PDF")
    identities = {
        "change-coordinator",
        "product-steward",
        "legal-steward",
        "gtm-steward",
        "skill-curator",
    }
    (tmp_path / "agentteams" / "identities").mkdir(parents=True)
    for name in identities:
        (tmp_path / "agentteams" / "identities" / f"{name}.json").write_text(
            "{}", encoding="utf-8"
        )
    (tmp_path / "submission" / "AGENT-IDENTITIES.md").write_text(
        "\n".join(f"`{name}`" for name in identities), encoding="utf-8"
    )
    (tmp_path / "LICENSE").write_text("Apache-2.0", encoding="utf-8")
    (tmp_path / "README.md").write_text("# source package\n", encoding="utf-8")
    (tmp_path / "evidence" / "latest").mkdir(parents=True)
    (tmp_path / "evidence" / "latest" / "demo.json").write_text(
        json.dumps({"receipt": {"status": "COMPLETED"}}), encoding="utf-8"
    )
    (tmp_path / "evidence" / "agentteams").mkdir(parents=True)
    (tmp_path / "evidence" / "agentteams" / "live-receipt.json").write_text(
        json.dumps({"evidence_class": "NOT_RUN"}), encoding="utf-8"
    )
    assert goai_gate.evaluate("preliminary")["decision"] == "GO"
    semifinal = goai_gate.evaluate("semifinal")
    assert semifinal["decision"] == "NO_GO"
    assert "semifinal.live_agentteams" in semifinal["blocking_failures"]
    assert all(
        not item["evidence"].startswith(str(tmp_path)) for item in semifinal["checks"]
    )


def test_evidence_manifest_recomputes_artifact_digest(tmp_path: Path) -> None:
    payload = {"result": "original"}
    artifact = tmp_path / "result.json"
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    manifest = {
        "schema_version": "orgrebase.evidence-manifest.v2",
        "workflow_run_id": "run:test@1",
        "run_nonce": "a" * 64,
        "digest_algorithm": "orgrebase-canonical-json-sha256-v1",
        "artifacts": {
            "result.json": {
                "path": "result.json",
                "digest": sha256_digest(payload),
                "status": "IMPLEMENTED",
                "evidence_class": "LOCAL_DETERMINISTIC",
            }
        },
        "capabilities": {
            "result": {
                "status": "IMPLEMENTED",
                "evidence_class": "LOCAL_DETERMINISTIC",
                "artifact": "result.json",
            }
        },
        "verifier": "test",
        "limitations": ["fixture"],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_evidence_manifest.verify(manifest_path)["status"] == "PASS"
    artifact.write_text(json.dumps({"result": "tampered"}), encoding="utf-8")
    with pytest.raises(verify_evidence_manifest.ManifestError, match="DIGEST_MISMATCH"):
        verify_evidence_manifest.verify(manifest_path)
