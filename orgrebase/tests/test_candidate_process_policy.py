from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from orgrebase.process_policy import candidate_environment


def test_candidate_does_not_inherit_database_target_or_cloud_credentials(tmp_path):
    inherited = {
        "PATH": os.environ["PATH"], "HOME": "/operator/home", "LANG": "en_US.UTF-8",
        "ORGREBASE_WORKSPACE_DB": "postgresql://private", "PGPASSWORD": "private-password",
        "TARGET_ACCESS_TOKEN": "private-target-token", "AWS_SECRET_ACCESS_KEY": "private-cloud-key",
        "GOOGLE_APPLICATION_CREDENTIALS": "/operator/credentials.json", "CLOUDSDK_CONFIG": "/operator/gcloud",
        "PYTHONPATH": "/untrusted/python", "LD_PRELOAD": "/untrusted/library",
        "ORGREBASE_VERTEX_ACCESS_TOKEN": "model-only-token", "ORGREBASE_VERTEX_PROJECT_ID": "model-project",
    }
    environment = candidate_environment(inherited, home=tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", "import json,os; print(json.dumps(dict(os.environ)))"],
        env=environment, close_fds=True, check=True, capture_output=True, text=True,
    )
    observed = json.loads(result.stdout)
    assert observed["HOME"] == str(tmp_path)
    assert observed["PYTHONNOUSERSITE"] == "1"
    for name in inherited.keys() - {"PATH", "HOME", "LANG"}:
        assert name not in observed
    reviewer = candidate_environment(inherited, home=tmp_path, model_provider="vertex-ai")
    assert reviewer["ORGREBASE_VERTEX_ACCESS_TOKEN"] == "model-only-token"
    assert "TARGET_ACCESS_TOKEN" not in reviewer
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in reviewer


def test_candidate_cannot_use_inherited_descriptor(tmp_path):
    descriptor = os.open(tmp_path / "private", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.set_inheritable(descriptor, True)
        program = "import os,sys\ntry: os.fstat(int(sys.argv[1]))\nexcept OSError: print('closed')\nelse: print('inherited')"
        result = subprocess.run(
            [sys.executable, "-c", program, str(descriptor)],
            env=candidate_environment(os.environ, home=tmp_path), close_fds=True,
            check=True, capture_output=True, text=True,
        )
        assert result.stdout.strip() == "closed"
    finally:
        os.close(descriptor)


def test_unknown_model_provider_cannot_expand_environment(tmp_path):
    with pytest.raises(ValueError, match="CANDIDATE_MODEL_PROVIDER_INVALID"):
        candidate_environment({}, home=tmp_path, model_provider="target-admin")


def test_vertex_reviewer_resolves_parent_adc_without_exposing_cloud_home(tmp_path, monkeypatch):
    from orgrebase.workspace import competition_run, model_provider

    for key in ("ORGREBASE_VERTEX_ACCESS_TOKEN", "ORGREBASE_VERTEX_API_KEY", "ORGREBASE_VERTEX_PROJECT_ID",
                "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ORGREBASE_VERTEX_MODEL_ID", "gemini-3.8-flash")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unrelated-test-secret")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/private/test-adc")
    calls = []
    def resolve(*args):
        calls.append(args)
        return "ephemeral-test-token" if "print-access-token" in args else "test-cloud-project"
    monkeypatch.setattr(model_provider, "_gcloud_value", resolve)
    incoming, outgoing = tmp_path / "input.json", tmp_path / "output.json"
    incoming.write_text(json.dumps({"model_provider": "vertex-ai", "model_id": "gemini-3.8-flash"}))
    real_popen = subprocess.Popen
    program = (
        "import json,os,pathlib; from orgrebase.workspace.model_provider import VERTEX_MODEL_ID; "
        "value={'model':VERTEX_MODEL_ID,'token_bound':os.environ.get('ORGREBASE_VERTEX_ACCESS_TOKEN')=='ephemeral-test-token',"
        "'project_bound':os.environ.get('ORGREBASE_VERTEX_PROJECT_ID')=='test-cloud-project',"
        "'other_secret_absent':'DEEPSEEK_API_KEY' not in os.environ,"
        "'adc_file_absent':'GOOGLE_APPLICATION_CREDENTIALS' not in os.environ,"
        "'private_home':pathlib.Path(os.environ['HOME']).name.startswith('orgrebase-candidate-')}; "
        f"pathlib.Path({str(outgoing)!r}).write_text(json.dumps(value))"
    )
    def worker_probe(command, **kwargs):
        return real_popen([sys.executable, "-c", program], **kwargs)
    monkeypatch.setattr(competition_run.subprocess, "Popen", worker_probe)
    result, receipt = competition_run._run_child(repo_root=tmp_path, mode="reviewer", input_path=incoming,
                                                output_path=outgoing, ack_action_digest="sha256:" + "1" * 64)
    assert result.pop("model") == "gemini-3.8-flash" and all(result.values())
    assert len(calls) == 2 and receipt["independent_process"] is True
    assert "ephemeral-test-token" not in outgoing.read_text() + json.dumps(receipt)


def test_vertex_parent_missing_credentials_stops_before_worker(tmp_path, monkeypatch):
    from orgrebase.workspace import competition_run, model_provider
    for key in ("ORGREBASE_VERTEX_ACCESS_TOKEN", "ORGREBASE_VERTEX_API_KEY", "ORGREBASE_VERTEX_PROJECT_ID",
                "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(model_provider, "_gcloud_value", lambda *args: None)
    incoming = tmp_path / "input.json"
    incoming.write_text(json.dumps({"model_provider": "vertex-ai", "model_id": "gemini-3.8-flash"}))
    def forbidden(*args, **kwargs):
        raise AssertionError("worker must not be launched")
    monkeypatch.setattr(competition_run.subprocess, "Popen", forbidden)
    with pytest.raises(ValueError, match="VERTEX_PARENT_CREDENTIALS_UNAVAILABLE"):
        competition_run._run_child(repo_root=tmp_path, mode="reviewer", input_path=incoming,
                                   output_path=tmp_path / "output.json", ack_action_digest="sha256:" + "1" * 64)
