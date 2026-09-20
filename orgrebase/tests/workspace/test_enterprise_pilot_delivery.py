from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from orgrebase.cli import _enterprise_pilot_check
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.pilot_authoring import (
    EnterpriseQuotePilotAuthoringError,
    initialize_enterprise_quote_pilot_draft,
    seal_enterprise_quote_pilot_pack,
)

ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = ROOT / "scripts" / "verify_enterprise_quote_pilot.py"
WRAPPER_PATH = ROOT / "run-enterprise-pilot.sh"


def _load_verifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location("enterprise_pilot_verifier", VERIFIER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIER = _load_verifier()
PilotEvidenceError = VERIFIER.PilotEvidenceError


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _canonical_digest(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _resign_manifest(evidence: Path, *artifact_names: str) -> None:
    manifest_path = evidence / "manifest.json"
    manifest = _read_json(manifest_path)
    for name in artifact_names:
        manifest["artifacts"][name]["digest"] = _canonical_digest(_read_json(evidence / name))
    _write_json(manifest_path, manifest)


def _set_internal_enterprise_shape(draft: Path) -> None:
    pack_path = draft / "pack.json"
    pack = _read_json(pack_path)
    pack["pack_id"] = "pack:internal-enterprise-quote"
    pack["scenario"]["label"] = "Internal Enterprise Quote Pilot"
    _write_json(pack_path, pack)

    profile_path = draft / "profile.json"
    profile = _read_json(profile_path)
    profile["organization_id"] = "org:internal-pilot-enterprise"
    profile["synthetic"] = False
    profile["data_class"] = "INTERNAL"
    profile["declared_limitation_codes"] = [
        item
        for item in profile["declared_limitation_codes"]
        if item != "SYNTHETIC_DATA_ONLY"
    ]
    _write_json(profile_path, profile)

    for path in sorted((draft / "components").glob("*.json")):
        component = _read_json(path)
        component["organization_id"] = "org:internal-pilot-enterprise"
        if component["component_kind"] == "DOMAIN":
            component["projection"]["organization_id"] = "org:internal-pilot-enterprise"
        if component["component_kind"] == "KNOWLEDGE":
            for source in component["projection"]["source_values"]:
                if source.get("raw_private_value") is not None:
                    source["raw_private_value"] = "INTERNAL PILOT PLACEHOLDER"
            for proposed in component["projection"]["proposed_values"]:
                if proposed["change_kind"] == "launch_date":
                    proposed["value"] = "2026-11-01"
        _write_json(path, component)


@pytest.fixture(scope="module")
def internal_pilot_delivery(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    base = tmp_path_factory.mktemp("enterprise-pilot-delivery")
    draft = base / "draft"
    sealed = base / "sealed"
    init_receipt = initialize_enterprise_quote_pilot_draft(draft)
    _set_internal_enterprise_shape(draft)
    draft_bytes_before = {
        path.relative_to(draft).as_posix(): path.read_bytes()
        for path in sorted(draft.rglob("*.json"))
    }
    seal_receipt = seal_enterprise_quote_pilot_pack(draft, sealed)
    draft_bytes_after = {
        path.relative_to(draft).as_posix(): path.read_bytes()
        for path in sorted(draft.rglob("*.json"))
    }
    check = _enterprise_pilot_check(pack=sealed, work_dir=base / "check")
    return {
        "base": base,
        "draft": draft,
        "sealed": sealed,
        "init": init_receipt,
        "seal": seal_receipt,
        "check": check,
        "draft_bytes_before": draft_bytes_before,
        "draft_bytes_after": draft_bytes_after,
    }


def test_internal_enterprise_shape_seals_runs_and_verifies_without_claim_inflation(
    internal_pilot_delivery: dict[str, Any],
) -> None:
    runtime = load_enterprise_quote_pilot_pack(internal_pilot_delivery["sealed"])
    result = internal_pilot_delivery["check"]
    evidence = internal_pilot_delivery["base"] / "check" / "evidence"
    summary = _read_json(evidence / "summary.json")

    assert internal_pilot_delivery["init"]["status"] == "DRAFT_CREATED"
    assert internal_pilot_delivery["seal"]["status"] == "SEALED_AND_PREFLIGHT_PASSED"
    assert internal_pilot_delivery["draft_bytes_before"] == internal_pilot_delivery["draft_bytes_after"]
    assert runtime.profile.organization_id == "org:internal-pilot-enterprise"
    assert runtime.profile.synthetic is False
    assert runtime.profile.data_class.value == "INTERNAL"
    assert runtime.proposed_values["launch_date"].value == "2026-11-01"
    assert result["status"] == "PASS"
    assert result["synthetic"] is False
    assert result["data_class"] == "INTERNAL"
    assert result["final_quote"]["version"] == "v3"
    assert result["final_quote"]["payload"]["launch_date"] == "2026-11-01"
    assert result["independent_verification"]["status"] == "PASS"
    assert summary["real_enterprise_validated"] == "NOT_RUN"
    assert summary["production_ready"] is False
    assert summary["external_enterprise_target_writes"] == 0
    assert str(internal_pilot_delivery["base"]) not in json.dumps(result)


def test_independent_verifier_timeout_cannot_report_pilot_success(
    internal_pilot_delivery: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from orgrebase import cli, resource_paths

    verifier = tmp_path / "slow_verifier.py"
    verifier.write_text("import time\ntime.sleep(60)\n")
    monkeypatch.setattr(cli, "_PILOT_VERIFIER_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(cli, "_enterprise_pilot_loop", lambda **kwargs: internal_pilot_delivery["check"])
    monkeypatch.setattr(resource_paths, "runtime_asset_path", lambda path: verifier)
    work_dir = tmp_path / "timed-out-check"

    with pytest.raises(RuntimeError, match=r"^PILOT_INDEPENDENT_VERIFICATION_TIMEOUT$"):
        cli._enterprise_pilot_check(pack=internal_pilot_delivery["sealed"], work_dir=work_dir)

    assert not (work_dir / "verification.json").exists()


def test_seal_is_repeatable_and_never_overwrites(
    internal_pilot_delivery: dict[str, Any],
    tmp_path: Path,
) -> None:
    original = internal_pilot_delivery["sealed"]
    resealed = tmp_path / "resealed"
    first = seal_enterprise_quote_pilot_pack(original, resealed)
    assert first["pack_digest"] == internal_pilot_delivery["seal"]["pack_digest"]
    assert {
        path.relative_to(original).as_posix(): path.read_bytes()
        for path in sorted(original.rglob("*.json"))
    } == {
        path.relative_to(resealed).as_posix(): path.read_bytes()
        for path in sorted(resealed.rglob("*.json"))
    }

    sentinel = tmp_path / "existing"
    sentinel.mkdir()
    marker = sentinel / "owner-data.txt"
    marker.write_text("preserve", encoding="utf-8")
    with pytest.raises(EnterpriseQuotePilotAuthoringError, match="PILOT_AUTHOR_OUTPUT_EXISTS"):
        seal_enterprise_quote_pilot_pack(original, sentinel)
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_init_and_seal_reject_unowned_output_or_extra_input_files(tmp_path: Path) -> None:
    existing = tmp_path / "existing-draft"
    existing.mkdir()
    sentinel = existing / "owner-data.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(EnterpriseQuotePilotAuthoringError, match="PILOT_AUTHOR_OUTPUT_EXISTS"):
        initialize_enterprise_quote_pilot_draft(existing)
    assert sentinel.read_text(encoding="utf-8") == "preserve"

    draft = tmp_path / "draft"
    initialize_enterprise_quote_pilot_draft(draft)
    (draft / "unexpected.json").write_text("{}\n", encoding="utf-8")
    output = tmp_path / "must-not-exist"
    with pytest.raises(EnterpriseQuotePilotAuthoringError, match="PILOT_AUTHOR_FILE_SET_MISMATCH"):
        seal_enterprise_quote_pilot_pack(draft, output)
    assert not output.exists()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("unsigned-summary", "MANIFEST_ARTIFACT_DIGEST:summary.json"),
        ("wrong-owner-resigned", "WRONG_OWNER_ACTOR:launch_date"),
        ("stale-resigned", "STALE_COUNTS_CHANGED:currency"),
        ("claim-inflation-resigned", "SUMMARY_CLAIM_CEILING"),
        ("restored-database", "BACKUP_RESTORED_RAW_MISMATCH"),
    ),
)
def test_independent_verifier_rejects_tampering_even_when_json_is_resigned(
    internal_pilot_delivery: dict[str, Any],
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    source = internal_pilot_delivery["base"] / "check" / "evidence"
    evidence = tmp_path / "evidence"
    shutil.copytree(source, evidence)

    if mutation == "unsigned-summary":
        summary = _read_json(evidence / "summary.json")
        summary["status"] = "ALTERED"
        _write_json(evidence / "summary.json", summary)
    elif mutation == "wrong-owner-resigned":
        loop = _read_json(evidence / "pilot-loop.json")
        summary = _read_json(evidence / "summary.json")
        actor = loop["launch_change"]["approval"]["actor_id"]
        loop["approval_commands"]["launch_date"]["wrong_owner_probe"]["actor_id"] = actor
        summary["wrong_owner_probes"]["launch_date"]["actor_id"] = actor
        _write_json(evidence / "pilot-loop.json", loop)
        _write_json(evidence / "summary.json", summary)
        _resign_manifest(evidence, "pilot-loop.json", "summary.json")
    elif mutation == "stale-resigned":
        loop = _read_json(evidence / "pilot-loop.json")
        summary = _read_json(evidence / "summary.json")
        changed = dict(loop["approval_commands"]["currency"]["stale_approval_probe"]["after_counts"])
        changed["events"] += 1
        loop["approval_commands"]["currency"]["stale_approval_probe"]["after_counts"] = changed
        summary["stale_approval_probes"]["currency"]["after_counts"] = changed
        _write_json(evidence / "pilot-loop.json", loop)
        _write_json(evidence / "summary.json", summary)
        _resign_manifest(evidence, "pilot-loop.json", "summary.json")
    elif mutation == "claim-inflation-resigned":
        summary = _read_json(evidence / "summary.json")
        manifest = _read_json(evidence / "manifest.json")
        summary["deployment_maturity"] = "PRODUCTION_READY"
        manifest["deployment_maturity"] = "PRODUCTION_READY"
        _write_json(evidence / "summary.json", summary)
        _resign_manifest(evidence, "summary.json")
        manifest = _read_json(evidence / "manifest.json")
        manifest["deployment_maturity"] = "PRODUCTION_READY"
        _write_json(evidence / "manifest.json", manifest)
    elif mutation == "restored-database":
        with (evidence / "workspace.restored.sqlite3").open("ab") as handle:
            handle.write(b"tampered")
    else:  # pragma: no cover - parametrization invariant
        raise AssertionError(mutation)

    with pytest.raises(PilotEvidenceError, match=expected_code):
        VERIFIER.verify_evidence(evidence, pack_root=internal_pilot_delivery["sealed"])


def test_root_wrapper_is_executable_safe_and_non_resident() -> None:
    assert os.access(WRAPPER_PATH, os.X_OK)
    syntax = subprocess.run(
        ["bash", "-n", str(WRAPPER_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr
    help_result = subprocess.run(
        [str(WRAPPER_PATH), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0
    assert "Default command: start (launches the Golden UI; two owner clicks remain human)" in (
        help_result.stdout
    )
    assert "check` is the retained Spec 054 deterministic Pack health check" in help_result.stdout


@pytest.mark.parametrize(
    ("provider", "expected_mode"),
    (("ollama-local", "OFFLINE_LOCAL"), ("vertex-ai", "LIVE_VERTEX")),
)
def test_root_wrapper_derives_oac_execution_mode_from_provider(
    tmp_path: Path,
    provider: str,
    expected_mode: str,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'MODE=%s\\nARGS=%s\\n' \"$ORGREBASE_OAC_EXECUTION_MODE\" \"$*\"\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = dict(os.environ)
    env.pop("ORGREBASE_OAC_EXECUTION_MODE", None)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        [
            str(WRAPPER_PATH),
            "start",
            "--work-dir",
            str(tmp_path / "state"),
            "--competition-mode",
            "off",
            "--model-provider",
            provider,
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"OAC execution mode: {expected_mode}" in result.stdout
    assert f"MODE={expected_mode}" in result.stdout
    assert f"--competition-model-provider {provider}" in result.stdout


def test_root_wrapper_allows_only_offline_provider_for_frozen_review(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\nprintf 'MODE=%s\\n' \"$ORGREBASE_OAC_EXECUTION_MODE\"\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["ORGREBASE_OAC_EXECUTION_MODE"] = "FROZEN_REPLAY"

    frozen = subprocess.run(
        [
            str(WRAPPER_PATH),
            "start",
            "--work-dir",
            str(tmp_path / "frozen"),
            "--competition-mode",
            "off",
            "--model-provider",
            "ollama-local",
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    conflicting = subprocess.run(
        [
            str(WRAPPER_PATH),
            "start",
            "--work-dir",
            str(tmp_path / "conflicting"),
            "--competition-mode",
            "off",
            "--model-provider",
            "vertex-ai",
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert frozen.returncode == 0, frozen.stdout + frozen.stderr
    assert "OAC execution mode: FROZEN_REPLAY" in frozen.stdout
    assert "MODE=FROZEN_REPLAY" in frozen.stdout
    assert conflicting.returncode == 2
    assert "FROZEN_REPLAY cannot use a cloud reviewer provider" in conflicting.stderr


@pytest.mark.parametrize("execution_mode", (None, "", "OFFLINE_LOCAL", "FROZEN_REPLAY", "invalid", "LIVE_VERTEX"))
def test_root_wrapper_requires_explicit_live_vertex_for_deepseek(
    tmp_path: Path,
    execution_mode: str | None,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'LAUNCH_MODE=%s\\nARGS=%s\\n' \"$ORGREBASE_OAC_EXECUTION_MODE\" \"$*\"\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env.pop("ORGREBASE_OAC_EXECUTION_MODE", None)
    if execution_mode is not None:
        env["ORGREBASE_OAC_EXECUTION_MODE"] = execution_mode

    result = subprocess.run(
        [str(WRAPPER_PATH), "start", "--work-dir", str(tmp_path / "state"),
         "--model-provider", "deepseek"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    if execution_mode == "LIVE_VERTEX":
        assert result.returncode == 0, result.stdout + result.stderr
        assert "LAUNCH_MODE=LIVE_VERTEX" in result.stdout
        assert "--competition-model-provider deepseek" in result.stdout
    else:
        assert result.returncode == 2
        assert "DeepSeek requires explicit ORGREBASE_OAC_EXECUTION_MODE=LIVE_VERTEX" in result.stderr
        assert "Vertex mapping credentials/project and DEEPSEEK_API_KEY" in result.stderr
        assert "LAUNCH_MODE=" not in result.stdout, "reject before starting any provider process"
