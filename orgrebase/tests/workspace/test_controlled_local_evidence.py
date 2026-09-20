from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest

from orgrebase.workspace.controlled_local_evidence import (
    ControlledLocalEvidenceError,
    run_controlled_local_evidence,
)

ROOT = Path(__file__).resolve().parents[2]
DIGESTS = {
    "native_receipt_digest": "sha256:" + "1" * 64,
    "skill_package_digest": "sha256:" + "2" * 64,
    "skill_invocation_receipt_digest": "sha256:" + "3" * 64,
    "graph_digest": "sha256:" + "4" * 64,
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _rehash_index(output: Path) -> None:
    index_path = output / "evidence-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entries = [
        {
            "path": path.relative_to(output).as_posix(),
            "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for path in sorted(output.rglob("*"))
        if path.is_file() and path != index_path
    ]
    body = {
        "schema_version": index["schema_version"],
        "entry_count": len(entries),
        "entries": entries,
        "pack_digest": "sha256:" + hashlib.sha256(_canonical(entries)).hexdigest(),
    }
    body["digest"] = "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
    index_path.write_text(json.dumps(body), encoding="utf-8")


def _verifier() -> ModuleType:
    path = ROOT / "scripts/verify_controlled_local_evidence.py"
    spec = importlib.util.spec_from_file_location("controlled_local_verifier", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(tmp_path: Path, **metadata: str | int | None) -> Path:
    artifact = tmp_path / "orgrebase-test.whl"
    artifact.write_bytes(b"controlled-local-test-artifact")
    output = tmp_path / "evidence"
    run_controlled_local_evidence(
        output_dir=output,
        run_id="run:controlled-local-evidence:test",
        task_id="task:gtm:test",
        delegation_id="delegation:gtm:test",
        artifact_paths=(artifact,),
        deployment_profile_path=ROOT / "configs/deployment/controlled-local.json",
        lock_path=ROOT / "uv.lock",
        pyproject_path=ROOT / "pyproject.toml",
        **metadata,
        **DIGESTS,
    )
    return output


def test_controlled_local_evidence_pack_is_complete_and_verifiable(tmp_path: Path) -> None:
    output = _run(tmp_path)
    result = _verifier().verify(output)
    assert result["status"] == "PASS"
    assert result["required_layers"] == [
        "AGENTTEAMS",
        "SKILL",
        "SOURCE",
        "TERMINAL",
        "TOOL",
    ]
    assert result["approval_apply_status"] == "NOT_RUN"
    assert result["production_readiness"] is False
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["causal_chain"] == [
        "SOURCE",
        "AGENTTEAMS",
        "TOOL",
        "SKILL",
        "TERMINAL",
    ]
    assert summary["telemetry_timing_class"] == (
        "SYNTHETIC_DETERMINISTIC_PROJECTION"
    )
    assert summary["native_nonce_binding"] == "NOT_BOUND"
    assert summary["coalition_result_binding_digest"] == "NOT_BOUND"


def test_bound_native_skill_and_coalition_facts_are_verified(tmp_path: Path) -> None:
    coalition_digest = "sha256:" + "5" * 64
    output = _run(
        tmp_path,
        coalition_result_binding_digest=coalition_digest,
        native_nonce="6" * 64,
        agentteams_project_id="orgrebase-native-quote-001",
        agentteams_attempt_id="attempt:gtm:1",
        agentteams_attempt_number=1,
        agentteams_retry_count=0,
        agentteams_reassign_count=1,
        skill_name="enterprise-quote-compose",
        skill_version="1.2.0",
    )

    result = _verifier().verify(output)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert result["status"] == "PASS"
    assert summary["coalition_result_binding_digest"] == coalition_digest
    assert summary["native_nonce_binding"] == "BOUND"
    assert summary["agentteams_project_binding"] == "BOUND"
    assert summary["agentteams_attempt_binding"] == "BOUND"
    assert summary["skill_version_binding"] == "BOUND"


def test_verifier_rejects_cross_run_tool_substitution(tmp_path: Path) -> None:
    output = _run(tmp_path)
    receipt_path = output / "tool/receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(receipt)
    mutated["run_id"] = "run:substituted"
    body = {key: value for key, value in mutated.items() if key != "digest"}
    import hashlib

    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    mutated["digest"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    receipt_path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(
        SystemExit,
        match=r"FILE_BINDING:tool/receipt\.json|TOOL_RECEIPT",
    ):
        _verifier().verify(output)


def test_generation_rejects_zero_authority_digest(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.whl"
    artifact.write_bytes(b"artifact")
    with pytest.raises(ControlledLocalEvidenceError, match="NONZERO_SHA256"):
        run_controlled_local_evidence(
            output_dir=tmp_path / "evidence",
            run_id="run:test",
            task_id="task:test",
            delegation_id="delegation:test",
            native_receipt_digest="sha256:" + "0" * 64,
            skill_package_digest=DIGESTS["skill_package_digest"],
            skill_invocation_receipt_digest=DIGESTS[
                "skill_invocation_receipt_digest"
            ],
            graph_digest=DIGESTS["graph_digest"],
            artifact_paths=(artifact,),
            deployment_profile_path=ROOT / "configs/deployment/controlled-local.json",
            lock_path=ROOT / "uv.lock",
            pyproject_path=ROOT / "pyproject.toml",
        )


def test_verifier_rejects_rehashed_tool_result_substitution(tmp_path: Path) -> None:
    output = _run(tmp_path)
    result_path = output / "tool/result.json"
    result_path.write_text(
        json.dumps(
            {
                "schema_version": "attacker.tool-result.v1",
                "dependencies": ["poisoned:policy"],
                "run_id": "run:foreign",
                "target_writes": 999,
            }
        ),
        encoding="utf-8",
    )
    _rehash_index(output)

    with pytest.raises(SystemExit, match=r"TOOL_RECEIPT|TOOL_RESULT"):
        _verifier().verify(output)


def test_verifier_rejects_rehashed_telemetry_database_privacy_drift(
    tmp_path: Path,
) -> None:
    output = _run(tmp_path)
    database = output / "observability/telemetry.sqlite"
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT id,payload_json FROM otlp_ingestions ORDER BY id LIMIT 1"
        ).fetchone()
        assert row is not None
        payload = json.loads(row[1])
        payload["raw_prompt"] = "CONFIDENTIAL CUSTOMER TERMS"
        connection.execute(
            "UPDATE otlp_ingestions SET payload_json=? WHERE id=?",
            (json.dumps(payload), row[0]),
        )
        connection.commit()
    finally:
        connection.close()
    _rehash_index(output)

    with pytest.raises(
        SystemExit,
        match=r"TELEMETRY_DATABASE_(DIGEST|PRIVACY)",
    ):
        _verifier().verify(output)


def test_verifier_rejects_rehashed_causal_order_mutation(tmp_path: Path) -> None:
    output = _run(tmp_path)
    traces_path = output / "observability/traces.otlp.json"
    traces = json.loads(traces_path.read_text(encoding="utf-8"))
    spans = traces["resourceSpans"][0]["scopeSpans"][0]["spans"]
    spans[1], spans[2] = spans[2], spans[1]
    traces_path.write_text(json.dumps(traces), encoding="utf-8")
    _rehash_index(output)

    with pytest.raises(SystemExit, match=r"OTLP_CAUSAL_ORDER"):
        _verifier().verify(output)


def test_verifier_rejects_rehashed_parent_substitution(tmp_path: Path) -> None:
    output = _run(tmp_path)
    traces_path = output / "observability/traces.otlp.json"
    traces = json.loads(traces_path.read_text(encoding="utf-8"))
    spans = traces["resourceSpans"][0]["scopeSpans"][0]["spans"]
    spans[3]["parentSpanId"] = spans[0]["spanId"]
    traces_path.write_text(json.dumps(traces), encoding="utf-8")
    _rehash_index(output)

    with pytest.raises(SystemExit, match=r"OTLP_CAUSAL_PARENT"):
        _verifier().verify(output)
