from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from orgrebase.agentteams_source import load_teamharness_lock
from orgrebase.goai_agentteams import (
    EXPECTED_FRESH_CORE_EVIDENCE,
    EXPECTED_PUBLIC_TRANSPORT_FIXTURE,
    PRIVATE_RUNTIME_EVIDENCE_PATHS,
)
from scripts import build_offline_release as release


def _minimal_release_config() -> release.BuildConfig:
    return release.BuildConfig(
        name="orgrebase",
        version="0.0.0",
        description="test release",
        requires_python=">=3.12",
        dependencies=(),
        optional_dependencies=(),
        license_text="test-only",
        license_files=(),
        authors=(),
    )


def _minimal_release_root(root: Path) -> None:
    (root / "src" / "orgrebase").mkdir(parents=True)
    (root / "README.md").write_text("# Test release\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[tool.hatch.build.targets.sdist]\nexclude = []\n",
        encoding="utf-8",
    )


def test_new_configured_assets_are_included_without_builder_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    _minimal_release_root(checkout)
    assets = checkout / "new-assets"
    assets.mkdir()
    (assets / "policy.json").write_bytes(b'{"version":2}')
    (assets / "nested").mkdir()
    (assets / "nested" / "data.txt").write_bytes(b"retained input")
    with (checkout / "pyproject.toml").open("a") as config:
        config.write(
            '[tool.hatch.build.targets.wheel.force-include]\n'
            '"new-assets" = "orgrebase/_assets/new-domain"\n'
            '"README.md" = "orgrebase/_assets/start.md"\n'
        )
    monkeypatch.setattr(release, "ROOT", checkout)

    wheel = release.build_wheel(_minimal_release_config(), tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("orgrebase/_assets/new-domain/policy.json") == b'{"version":2}'
        assert archive.read("orgrebase/_assets/new-domain/nested/data.txt") == b"retained input"
        assert archive.read("orgrebase/_assets/start.md") == b"# Test release\n"


@pytest.mark.parametrize(
    ("source", "target", "error"),
    (
        ("absent-assets", "orgrebase/_assets/new", "MISSING_WHEEL_ASSET"),
        ("../outside", "orgrebase/_assets/new", "UNSAFE_SOURCE_PATH"),
        ("README.md", "../outside", "UNSAFE_SOURCE_PATH"),
        ("README.md", "outside/data", "WHEEL_ASSET_OUTSIDE_PACKAGE"),
    ),
)
def test_invalid_asset_mapping_fails_before_writing_wheel(
    source: str,
    target: str,
    error: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    _minimal_release_root(checkout)
    with (checkout / "pyproject.toml").open("a") as config:
        config.write(
            '[tool.hatch.build.targets.wheel.force-include]\n'
            f'"{source}" = "{target}"\n'
        )
    monkeypatch.setattr(release, "ROOT", checkout)

    with pytest.raises(release.ReleaseInputError, match=error):
        release.build_wheel(_minimal_release_config(), tmp_path)
    assert not list(tmp_path.glob("*.whl"))


def _extract_sdist_without_top_level(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            parts = Path(member.name).parts
            if len(parts) < 2 or member.isdir():
                continue
            assert member.isfile(), f"unexpected source archive member: {member.name}"
            relative = Path(*parts[1:])
            target = (root / relative).resolve()
            assert root in target.parents
            source = archive.extractfile(member)
            assert source is not None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
            target.chmod(member.mode)


def _assert_public_sdist_boundary(sdist: Path, prefix: str) -> None:
    with tarfile.open(sdist, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}
        for relative in (".python-version", ".github/pull_request_template.md"):
            source = archive.extractfile(f"{prefix}/{relative}")
            assert source is not None
            assert source.read() == (release.ROOT / relative).read_bytes()
    required_paths = {
        ".python-version",
        ".github/pull_request_template.md",
        "run-agentteams-demo.sh",
        "run-enterprise-pilot.sh",
        "run-semifinal-demo.sh",
        "verify-agentteams-demo.sh",
        "configs/goai-agentteams-demo.json",
        "examples/agentteams/change-request.json",
        "examples/agentteams/run-summary.example.json",
        EXPECTED_PUBLIC_TRANSPORT_FIXTURE,
        EXPECTED_FRESH_CORE_EVIDENCE["run_summary"],
        EXPECTED_FRESH_CORE_EVIDENCE["live_receipt"],
        EXPECTED_FRESH_CORE_EVIDENCE["semantic_ingestion"],
        EXPECTED_FRESH_CORE_EVIDENCE["checksums"],
        load_teamharness_lock()["offline_bundle"]["path"],
        (
            "vendor/predecessors/enterprise-quote-compose/1.3.0/"
            "orgrebase-0.4.0-py3-none-any.whl"
        ),
        *release.PUBLISHED_SDIST_DATABASES,
    }
    assert {f"{prefix}/{relative}" for relative in required_paths} <= set(members)
    for entrypoint in (
        "run-agentteams-demo.sh",
        "run-enterprise-pilot.sh",
        "run-semifinal-demo.sh",
        "verify-agentteams-demo.sh",
    ):
        assert members[f"{prefix}/{entrypoint}"].mode & 0o111
    forbidden_fragments = {
        *PRIVATE_RUNTIME_EVIDENCE_PATHS,
        "evidence/agentteams/private-sessions",
        "evidence/agentteams/live-sources",
        "evidence/agentteams/nonce-ledger.json",
        "evidence/agentteams/.nonce-ledger.json.lock",
        "evidence/agentteams/debug",
        "evidence/agentteams/debug-",
        "evidence/agentteams/dispatch-logs",
        "evidence/agentteams/dispatch-",
    }
    assert not any(fragment in name for name in members for fragment in forbidden_fragments)
    for name in members:
        relative = PurePosixPath(name).relative_to(prefix)
        assert not ({"archive", "failed", ".git"} & set(relative.parts))
        assert not relative.name.lower().endswith(release.TRANSIENT_DATABASE_SUFFIXES)


def test_offline_builder_uses_canonical_sdist_denylist() -> None:
    patterns = release._sdist_exclude_patterns()
    private_examples = {
        *PRIVATE_RUNTIME_EVIDENCE_PATHS,
        "evidence/agentteams/nonce-ledger.json",
        "evidence/agentteams/.nonce-ledger.json.lock",
        "evidence/agentteams/live-sources/raw.json",
        "evidence/agentteams/private-sessions/session.json",
        "evidence/agentteams/fresh-live/run/debug/request.json",
        "evidence/agentteams/fresh-live/run/worker-debug.json",
        "evidence/agentteams/fresh-live/run/dispatch-logs/worker.log",
        "evidence/agentteams/fresh-live/run/dispatch-private.log",
        "evidence/semifinal-closure/archive/old/summary.json",
        "evidence/semifinal-governed/failed/run/summary.json",
        "evidence/goai-agentteams/latest/git-tool-repo/.git/HEAD",
    }
    assert all(release._is_sdist_excluded(release.ROOT / relative, patterns) for relative in private_examples)
    assert not release._is_sdist_excluded(release.ROOT / EXPECTED_PUBLIC_TRANSPORT_FIXTURE, patterns)
    assert not release._is_sdist_excluded(
        release.ROOT / EXPECTED_FRESH_CORE_EVIDENCE["run_summary"], patterns
    )


def test_offline_sdist_rejects_untracked_credential_canary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    _minimal_release_root(checkout)
    untracked_secret = checkout / "src" / "orgrebase" / "runtime-notes.txt"
    untracked_secret.write_text(
        "token=" + "github_pat_" + "A" * 32,
        encoding="utf-8",
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    monkeypatch.setattr(release, "ROOT", checkout)

    assert untracked_secret in release._sdist_files()
    with pytest.raises(release.ReleaseInputError, match="CREDENTIAL_PATTERN:github_fine_grained_token"):
        release.build_sdist(_minimal_release_config(), dist)

    assert list(dist.iterdir()) == []


@pytest.mark.parametrize(
    "filename",
    (".env.local", "client-certificate.pem", "runtime.log", "service-credentials.json"),
)
def test_offline_wheel_rejects_untracked_sensitive_filename(
    filename: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    _minimal_release_root(checkout)
    sensitive_file = checkout / "src" / "orgrebase" / filename
    sensitive_file.write_text("placeholder\n", encoding="utf-8")
    monkeypatch.setattr(release, "ROOT", checkout)

    with pytest.raises(
        release.ReleaseInputError,
        match=r"(?:CREDENTIAL_FILE|SENSITIVE_RELEASE_FILE)_REJECTED",
    ):
        release.wheel_payloads()


def test_offline_wheel_rejects_untracked_machine_local_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    _minimal_release_root(checkout)
    report = checkout / "src" / "orgrebase" / "generated-report.txt"
    report.write_text(
        "source=" + "/" + "Users/runtime-user/private/export.json\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(release, "ROOT", checkout)

    with pytest.raises(release.ReleaseInputError, match="MACHINE_LOCAL_PATH:macos_user_home"):
        release.wheel_payloads()


def test_fallback_sdist_replays_predecessor_and_governed_pack_from_clean_extraction(
    tmp_path: Path,
) -> None:
    sdist = release.build_sdist(release.load_config(), tmp_path)
    checkout = tmp_path / "checkout"
    _extract_sdist_without_top_level(sdist, checkout)
    code = """
from pathlib import Path
from orgrebase.workspace.skill_rollback import load_frozen_predecessor

predecessor = load_frozen_predecessor(
    checkout_root=Path.cwd(),
    verify_retained_wheel=True,
)
assert predecessor.manifest['version'] == '1.3.0'
assert predecessor.resource_mode == 'SOURCE_CHECKOUT'
print('FROZEN_PREDECESSOR_CLEAN_SDIST_PASS')
"""
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(checkout / "src"),
            "PYTHONNOUSERSITE": "1",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=checkout,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "FROZEN_PREDECESSOR_CLEAN_SDIST_PASS"

    governed = subprocess.run(
        [
            sys.executable,
            "scripts/verify_governed_semifinal_apply.py",
            "--evidence",
            "evidence/semifinal-governed/latest",
            "--parent-pack",
            "evidence/semifinal-closure/latest",
        ],
        cwd=checkout,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert governed.returncode == 0, governed.stdout + governed.stderr
    governed_result = json.loads(governed.stdout)
    assert governed_result["status"] == "PASS"
    assert governed_result["verification_mode"] == "INDEPENDENT_STDLIB_JSON_SQLITE_REPLAY"


def test_offline_release_preserves_license_and_package_boundary(tmp_path) -> None:
    config = release.load_config()
    wheel = release.build_wheel(config, tmp_path)
    sdist = release.build_sdist(config, tmp_path)

    dist_info = config.dist_info
    expected_license_files = {
        f"{dist_info}/licenses/LICENSE",
        f"{dist_info}/licenses/LICENSE.md",
        f"{dist_info}/licenses/NOTICE.md",
        f"{dist_info}/licenses/LICENSES/Apache-2.0.txt",
        f"{dist_info}/licenses/LICENSES/CC-BY-4.0.txt",
    }
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata = archive.read(f"{dist_info}/METADATA").decode("utf-8")
    assert expected_license_files <= names
    assert "License: Apache-2.0" in metadata
    assert "License-File: LICENSE" in metadata
    assert "License-File: LICENSE.md" in metadata
    assert "License-File: NOTICE.md" in metadata
    assert "License-File: LICENSES/Apache-2.0.txt" in metadata
    assert "License-File: LICENSES/CC-BY-4.0.txt" in metadata
    assert "Provides-Extra: dev" in metadata
    assert "orgrebase/_assets/evidence/release-facts.json" in names
    assert "orgrebase/_assets/configs/oac/runtime-admission-policy.json" in names
    assert "orgrebase/_assets/configs/workspace/skill-registry.json" in names
    assert (
        "orgrebase/_assets/benchmark/quote-value-v0.1/public/"
        "current-process-baseline.json"
    ) in names
    assert "orgrebase/skill_contracts/enterprise-launch-readiness.json" in names
    assert (
        "orgrebase/skill_contracts/enterprise-launch-readiness-legacy-v1.3.json"
        in names
    )
    assert {
        f"orgrebase/skill_packages/{skill}/{resource}"
        for skill in (
            "enterprise-quote-compose",
            "structured-domain-handoff",
            "enterprise-launch-readiness",
        )
        for resource in (
            "SKILL.md",
            "references/zh-CN.md",
            "references/en.md",
            "contract.json",
            "program.json",
            "input.schema.json",
            "output.schema.json",
            "package.json",
        )
    } <= names
    assert {
        "orgrebase/skill_predecessors/enterprise-quote-compose/1.3.0/" + resource
        for resource in (
            "SKILL.md",
            "contract.json",
            "program.json",
            "input.schema.json",
            "output.schema.json",
            "package.json",
            "provenance.json",
        )
    } <= names
    assert {
        f"orgrebase/_assets/fixtures/enterprise-seed/{organization}/{kind}.json"
        for organization in ("northstar", "veracier")
        for kind in ("domain", "knowledge", "authority", "capability", "dependency")
    } <= names

    prefix = f"{config.normalized_name}-{config.version}"
    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = set(archive.getnames())
    for relative in (
        "LICENSE",
        "COMMERCIAL-LICENSE.md",
        "NOTICE.md",
        "RELEASE-VERIFICATION.md",
        "requirements.txt",
        "requirements-dev.txt",
    ):
        assert f"{prefix}/{relative}" in sdist_names
    assert not any("evidence/agentteams/live-sources" in name for name in sdist_names)
    assert not any("nonce-ledger" in name for name in sdist_names)
    _assert_public_sdist_boundary(sdist, prefix)


def test_canonical_sdist_preserves_public_evidence_boundary(tmp_path: Path) -> None:
    subprocess.run(
        ["uv", "build", "--sdist", "--out-dir", str(tmp_path)],
        cwd=release.ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    config = release.load_config()
    sdist = next(tmp_path.glob("*.tar.gz"))
    _assert_public_sdist_boundary(sdist, f"{config.normalized_name}-{config.version}")


def test_fallback_wheel_loads_oac_policy_and_seed_sources_from_isolated_install(
    tmp_path: Path,
) -> None:
    wheel = release.build_wheel(release.load_config(), tmp_path)
    site = tmp_path / "site"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with zipfile.ZipFile(wheel) as archive:
        assert "orgrebase/_assets/configs/oac/runtime-admission-policy.json" in archive.namelist()
        assert "orgrebase/_assets/fixtures/enterprise-seed/veracier-r2/domain.json" in archive.namelist()
        archive.extractall(site)
    code = """
from pathlib import Path
import orgrebase
from orgrebase.agentteams_source import load_agentteams_source, load_teamharness_lock
from orgrebase.digest import sha256_digest
from orgrebase.workspace.oac_agent_adaptation import current_oac_admission_implementation
from orgrebase.workspace.oac_wire import load_runtime_policy
from orgrebase.workspace.profile import northstar_acme_quote_profile
from orgrebase.workspace.reference_profiles import (
    supplier_sc008_source_aligned_profile,
    supplier_shadow_intake_profile,
)
from orgrebase.workspace.source_admission import (
    admit_enterprise_seed_sources,
    admit_veracier_sc008_profile_migration,
)

site = Path(__import__('os').environ['ORGREBASE_TEST_SITE']).resolve()
assert site in Path(orgrebase.__file__).resolve().parents
implementation = current_oac_admission_implementation()
assert implementation['agentteams_source']['agentteams_commit'] == load_agentteams_source().commit
assert implementation['agentteams_source']['source_lock_digest'] == sha256_digest(load_teamharness_lock())
policy, digest = load_runtime_policy()
assert policy['schema_version'] == 'orgrebase.oac-runtime-admission-policy.v2'
assert policy['policy_id'] == 'policy:orgrebase-oac-local-runtime-admission'
assert digest.startswith('sha256:')
source_receipt = admit_enterprise_seed_sources(northstar_acme_quote_profile())
assert len(source_receipt.root_observations) == 5
assert source_receipt.verdict == 'ADMITTED'
successor = supplier_sc008_source_aligned_profile()
successor_receipt = admit_enterprise_seed_sources(successor)
assert len(successor_receipt.root_observations) == 5
assert all(item.locator.endswith('@r2') for item in successor_receipt.root_observations)
migration = admit_veracier_sc008_profile_migration(
    supplier_shadow_intake_profile(), successor
)
assert migration.canonical_target_writes == 0
print('FALLBACK_WHEEL_POLICY_AND_SEED_SOURCES_PASS')
"""
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(site),
            "PYTHONNOUSERSITE": "1",
            "ORGREBASE_TEST_SITE": str(site),
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=run_dir,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "FALLBACK_WHEEL_POLICY_AND_SEED_SOURCES_PASS"


def test_fallback_wheel_runs_one_command_enterprise_pilot_check_outside_checkout(
    tmp_path: Path,
) -> None:
    wheel = release.build_wheel(release.load_config(), tmp_path)
    site = tmp_path / "pilot-site"
    run_dir = tmp_path / "pilot-run"
    run_dir.mkdir()
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "orgrebase/_assets/examples/enterprise-quote-pilot/evergreen/pack.json" in names
        assert "orgrebase/_assets/scripts/verify_enterprise_quote_pilot.py" in names
        archive.extractall(site)
    env = dict(os.environ)
    env.update({"PYTHONPATH": str(site), "PYTHONNOUSERSITE": "1"})

    for command in (
        ("enterprise-pilot-init", "--output", "draft"),
        ("enterprise-pilot-seal", "--draft", "draft", "--output", "sealed"),
        ("enterprise-pilot-check", "--pack", "sealed", "--work-dir", "check"),
    ):
        result = subprocess.run(
            [sys.executable, "-m", "orgrebase.cli", *command],
            cwd=run_dir,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    check = json.loads((run_dir / "check" / "verification.json").read_text(encoding="utf-8"))
    assert check["status"] == "PASS"
    assert check["deployment_maturity"] == "PILOT_READY_CONTROLLED_LOCAL"
    assert check["wrong_owner_rejections"] == 2
    assert check["stale_approval_rejections"] == 2
    assert check["backup_restore"] == "PASS"
    assert check["external_enterprise_target_writes"] == 0
    assert check["real_enterprise_validated"] == "NOT_RUN"
    assert check["production_ready"] is False


def test_fallback_wheel_source_checkout_commands_fail_before_writing(
    tmp_path: Path,
) -> None:
    wheel = release.build_wheel(release.load_config(), tmp_path)
    site = tmp_path / "source-only-site"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site)
    env = dict(os.environ)
    env.update({"PYTHONPATH": str(site), "PYTHONNOUSERSITE": "1"})

    for command in ("demo", "agentteams-demo"):
        run_dir = tmp_path / f"source-only-{command}"
        run_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "orgrebase.cli", command],
            cwd=run_dir,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert f"ORGREBASE_SOURCE_CHECKOUT_REQUIRED:{command}" in result.stderr
        assert list(run_dir.iterdir()) == []


def test_fallback_wheel_discovers_and_invokes_all_skill_packages(tmp_path: Path) -> None:
    wheel = release.build_wheel(release.load_config(), tmp_path)
    site = tmp_path / "skill-site"
    run_dir = tmp_path / "skill-run"
    run_dir.mkdir()
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site)
    code = """
from orgrebase.skills import EnterpriseLaunchReadinessSkill
from orgrebase.workspace.skill_packages import InvocationContext, SkillPackageRegistry
from orgrebase.workspace.skill_rollback import load_frozen_predecessor

legacy = EnterpriseLaunchReadinessSkill()
assert legacy.contract['version'] == '1.3'
registry = SkillPackageRegistry()
predecessor = load_frozen_predecessor(verify_retained_wheel=False)
assert predecessor.resource_mode == 'INSTALLED_WHEEL'
assert predecessor.manifest['version'] == '1.3.0'
found = registry.discover()
assert {item['name'] for item in found} == {
    'enterprise-quote-compose',
    'structured-domain-handoff',
    'enterprise-launch-readiness',
}
quote = registry.load('enterprise-quote-compose')
launch = registry.load('enterprise-launch-readiness')
assert launch.contract['version'] == '1.4'
assert legacy.contract_digest != launch.contract['content_digest']
result = registry.invoke_for_evaluation(
    'enterprise-quote-compose',
    {
        'skill_partition': 'replay',
        'candidate_program_digest_required': quote.manifest['program_content_digest'],
        'dependency_tool_receipt_digest': 'sha256:' + '1' * 64,
        'dependency_result_digest': 'sha256:' + '2' * 64,
        'coalition_result_binding_digest': 'sha256:' + '3' * 64,
        'domain_result_digests': {
            'product': 'sha256:' + '4' * 64,
            'legal': 'sha256:' + '5' * 64,
            'finance': 'sha256:' + '6' * 64,
            'gtm': 'sha256:' + '7' * 64,
        },
    },
    context=InvocationContext(
        run_id='run:fallback-wheel',
        task_id='task:fallback-wheel',
        delegation_id='delegation:fallback-wheel',
        actor_id='evaluator:fallback-wheel',
    ),
    expected_package_digest=quote.package_digest,
)
assert result.result['action'] == 'APPLY_QUOTE'
assert result.receipt['target_writes'] == 0
print('FALLBACK_WHEEL_SKILL_PACKAGES_PASS')
"""
    env = dict(os.environ)
    env.update({"PYTHONPATH": str(site), "PYTHONNOUSERSITE": "1"})
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=run_dir,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "FALLBACK_WHEEL_SKILL_PACKAGES_PASS"


def test_combined_source_snapshot_runs_bridge_and_standalone_fails_closed(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    org_sdist = release.build_sdist(release.load_config(), dist)
    oac_dist = tmp_path / "oac-dist"
    subprocess.run(
        ["uv", "build", "--sdist", "--out-dir", str(oac_dist)],
        cwd=release.ROOT.parent / "oac-spec",
        check=True,
        capture_output=True,
        text=True,
    )
    oac_sdist = next(oac_dist.glob("*.tar.gz"))

    standalone = tmp_path / "standalone" / "orgrebase"
    _extract_sdist_without_top_level(org_sdist, standalone)
    standalone_output = tmp_path / "standalone-evidence"
    standalone_env = dict(os.environ)
    standalone_env.pop("ORGREBASE_OAC_ROOT", None)
    standalone_env["PYTHONPATH"] = str(standalone / "src")
    missing = subprocess.run(
        [
            sys.executable,
            "-m",
            "orgrebase.cli",
            "workspace-oac-admission-demo",
            "--output-dir",
            str(standalone_output),
        ],
        cwd=standalone,
        env=standalone_env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 2
    assert "OAC_SPEC_CHECKOUT_NOT_FOUND" in missing.stderr
    assert "OrgRebase does not bundle OAC" in missing.stderr
    assert "--oac-root" in missing.stderr

    combined = tmp_path / "combined"
    org_root = combined / "orgrebase"
    oac_root = combined / "oac-spec"
    _extract_sdist_without_top_level(org_sdist, org_root)
    _extract_sdist_without_top_level(oac_sdist, oac_root)
    combined_env = dict(os.environ)
    combined_env.pop("ORGREBASE_OAC_ROOT", None)
    combined_env.pop("PYTHONPATH", None)
    combined_env["UV_OFFLINE"] = "1"
    # Reuse the running interpreter, while uv still creates the extracted
    # project's isolated environment; unrelated managed installs may be broken.
    combined_env["UV_PYTHON"] = str(Path(sys.executable).resolve())
    result = subprocess.run(
        [
            "make",
            "workspace-oac-admission-demo",
            "workspace-oac-admission-verify",
        ],
        cwd=org_root,
        env=combined_env,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "LOCAL_RUNTIME_ADMISSION_PASS" in result.stdout
    assert '"status": "PASS"' in result.stdout
