from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from orgrebase.cli import _enterprise_pilot_check
from orgrebase.domain import AuthorizationError
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.pilot_authoring import (
    EnterpriseQuotePilotAuthoringError,
    initialize_enterprise_quote_pilot_draft,
    preflight_enterprise_quote_pilot_draft,
    seal_enterprise_quote_pilot_pack,
)
from orgrebase.workspace.service import WorkspaceService

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


def _replace_strings(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        for old, new in replacements.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_replace_strings(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_strings(item, replacements) for key, item in value.items()}
    return value


def _make_distinct_enterprise_pack(
    root: Path,
    *,
    slug: str,
    customer: str,
    product_plan: str,
    proposed_date: str,
) -> Path:
    draft = root / "draft"
    sealed = root / "sealed"
    initialize_enterprise_quote_pilot_draft(draft)
    replacements = {
        "evergreen-industries": slug,
        "evergreen": slug,
        "Evergreen": slug.replace("-", " ").title(),
        "blue-harbor": customer,
        "Blue Harbor": customer.replace("-", " ").title(),
        "enterprise-quote-operator": f"{slug}-quote-operator",
        "Evergreen Enterprise Plus": product_plan,
        "2026-10-15": proposed_date,
    }
    for path in sorted(draft.rglob("*.json")):
        _write_json(path, _replace_strings(_read_json(path), replacements))
    seal_enterprise_quote_pilot_pack(draft, sealed)
    return sealed


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
    preflight_receipt = preflight_enterprise_quote_pilot_draft(draft)
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
        "preflight": preflight_receipt,
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
    assert internal_pilot_delivery["preflight"]["status"] == "DRAFT_PREFLIGHT_PASSED"
    assert internal_pilot_delivery["preflight"]["candidate_pack_digest"] == runtime.pack_digest
    assert internal_pilot_delivery["preflight"]["sealed_output_created"] is False
    assert internal_pilot_delivery["preflight"]["profile_admitted_for_workspace"] is False
    assert internal_pilot_delivery["preflight"]["workspace_activated"] is False
    assert len(internal_pilot_delivery["preflight"]["required_inputs"]) == 5
    assert all(
        item["status"] == "READY"
        for item in internal_pilot_delivery["preflight"]["required_inputs"]
    )
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


def test_two_distinct_enterprises_reuse_one_template_without_authority_or_receipt_leakage(
    tmp_path: Path,
) -> None:
    packs = {
        "alpha": _make_distinct_enterprise_pack(
            tmp_path / "alpha",
            slug="alpha-industries",
            customer="customer-alpha",
            product_plan="Alpha Enterprise",
            proposed_date="2026-11-15",
        ),
        "beta": _make_distinct_enterprise_pack(
            tmp_path / "beta",
            slug="beta-industries",
            customer="customer-beta",
            product_plan="Beta Enterprise",
            proposed_date="2026-12-20",
        ),
    }
    runtimes = {name: load_enterprise_quote_pilot_pack(path) for name, path in packs.items()}
    assert runtimes["alpha"].profile.default_task.template_ref == runtimes["beta"].profile.default_task.template_ref
    assert runtimes["alpha"].profile.organization_id != runtimes["beta"].profile.organization_id
    assert runtimes["alpha"].pack_digest != runtimes["beta"].pack_digest
    assert set(runtimes["alpha"].profile.governance.owner_refs).isdisjoint(
        runtimes["beta"].profile.governance.owner_refs
    )

    services = {
        name: WorkspaceService(
            store_path=tmp_path / name / "workspace.sqlite",
            runtime_configuration=runtime,
            review_duration_seconds=0,
        )
        for name, runtime in runtimes.items()
    }
    try:
        for service in services.values():
            service.form_quote()
            service.preview_change("launch_date")

        alpha = services["alpha"]
        beta = services["beta"]
        alpha_approval = alpha.approve_change(
            "launch_date",
            actor_id=alpha.change_owner["launch_date"],
            preview_digest=alpha._preview_record("launch_date")["preview_digest"],
        )
        alpha.apply_approved_change(
            "launch_date",
            approval_digest=alpha_approval["approval_digest"],
        )

        with pytest.raises(AuthorizationError):
            beta.approve_change(
                "launch_date",
                actor_id=alpha.change_owner["launch_date"],
                preview_digest=beta._preview_record("launch_date")["preview_digest"],
            )
        with pytest.raises(RuntimeError, match="WORKSPACE_APPROVAL"):
            beta.apply_approved_change(
                "launch_date",
                approval_digest=alpha_approval["approval_digest"],
            )
        beta_approval = beta.approve_change(
            "launch_date",
            actor_id=beta.change_owner["launch_date"],
            preview_digest=beta._preview_record("launch_date")["preview_digest"],
        )
        beta.apply_approved_change(
            "launch_date",
            approval_digest=beta_approval["approval_digest"],
        )

        exports = {name: service.export_evidence() for name, service in services.items()}
        assert exports["alpha"]["scenario"]["organization_id"] == "org:alpha-industries"
        assert exports["beta"]["scenario"]["organization_id"] == "org:beta-industries"
        assert exports["alpha"]["quote"]["payload"]["launch_date"] == "2026-11-15"
        assert exports["beta"]["quote"]["payload"]["launch_date"] == "2026-12-20"
        assert alpha_approval["approval_digest"] != beta_approval["approval_digest"]
    finally:
        for service in services.values():
            service.close()

    for name, runtime in runtimes.items():
        reopened = WorkspaceService.reopen(
            tmp_path / name / "workspace.sqlite",
            runtime_configuration=runtime,
            review_duration_seconds=0,
        )
        try:
            assert reopened.current_quote().payload["launch_date"] == {
                "alpha": "2026-11-15",
                "beta": "2026-12-20",
            }[name]
            assert reopened.export_evidence()["enterprise_seed_profile"]["organization_id"] == (
                f"org:{name}-industries"
            )
        finally:
            reopened.close()


def test_draft_preflight_cli_returns_exact_read_only_status_without_output_directory(
    tmp_path: Path,
) -> None:
    draft = tmp_path / "draft"
    initialize_enterprise_quote_pilot_draft(draft)
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in sorted(draft.rglob("*.json"))
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "orgrebase.cli",
            "enterprise-pilot-draft-preflight",
            "--draft",
            str(draft),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "DRAFT_PREFLIGHT_PASSED"
    assert payload["sealed_output_created"] is False
    assert payload["profile_admitted_for_workspace"] is False
    assert payload["workspace_activated"] is False
    assert payload["authority_created_by_preflight"] is False
    assert payload["canonical_target_writes"] == 0
    assert payload["real_enterprise_validated"] == "NOT_RUN"
    assert not any(path.is_dir() for path in tmp_path.iterdir() if path != draft)
    assert before == {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in sorted(draft.rglob("*.json"))
    }


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


def test_default_init_preserves_every_historical_template_byte(tmp_path: Path) -> None:
    source = ROOT / "examples/enterprise-quote-pilot/evergreen"
    output = tmp_path / "default"
    initialize_enterprise_quote_pilot_draft(output)
    assert {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*.json")} == {
        p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob("*.json")
    }


def test_priced_init_is_initial_facts_v2_and_reseals_without_identity_drift(tmp_path: Path) -> None:
    draft = tmp_path / "priced-draft"
    initialized = initialize_enterprise_quote_pilot_draft(draft, template_name="priced-quote")
    preflight = preflight_enterprise_quote_pilot_draft(draft)
    sealed = tmp_path / "priced-pack"
    receipt = seal_enterprise_quote_pilot_pack(draft, sealed)
    runtime = load_enterprise_quote_pilot_pack(sealed)
    assert runtime.schema_version == "orgrebase.enterprise-quote-pilot-pack.v2"
    assert runtime.profile.synthetic is True
    assert runtime.profile.default_task.template_ref == "template:enterprise_quote@v2"
    assert runtime.profile.change_family == () and not runtime.proposed_values
    assert {item.slot_id for item in runtime.enterprise_binding.resources} == {
        "launch_date", "currency", "product_plan", "quote_basket", "pricing_policy",
    }
    assert initialized["copied_pack_digest"] == preflight["candidate_pack_digest"] == receipt["pack_digest"]
    assert preflight["profile_admitted_for_workspace"] is False
    assert preflight["workspace_activated"] is False
    assert initialized["canonical_target_writes"] == 0


@pytest.mark.parametrize("template_name", ["unsupported", "../evergreen", "/private/customer"])
def test_init_rejects_unregistered_templates_before_creating_output(tmp_path: Path, template_name: str) -> None:
    output = tmp_path / "parent-not-created" / "draft"
    with pytest.raises(EnterpriseQuotePilotAuthoringError, match="PILOT_AUTHOR_TEMPLATE_UNSUPPORTED"):
        initialize_enterprise_quote_pilot_draft(output, template_name=template_name)
    assert not output.parent.exists()


def test_priced_init_cli_uses_public_template_without_test_imports(tmp_path: Path) -> None:
    draft = tmp_path / "priced-cli"
    result = subprocess.run(
        [sys.executable, "-m", "orgrebase", "enterprise-pilot-init", "--template", "priced-quote", "--output", str(draft)],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["synthetic"] is True
    assert load_enterprise_quote_pilot_pack(draft).profile.default_task.template_ref == "template:enterprise_quote@v2"


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
