from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from orgrebase.agentteams_source import load_teamharness_lock
from scripts import build_source_snapshot as snapshot

ROOT = Path(__file__).resolve().parents[1]


def _source_roots(tmp_path: Path) -> tuple[Path, Path]:
    orgrebase = tmp_path / "org-source"
    oac = tmp_path / "oac-source"
    (orgrebase / "src").mkdir(parents=True)
    (oac / "standard").mkdir(parents=True)
    (orgrebase / "src" / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    executable = orgrebase / "run.sh"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    (oac / "standard" / "contract.md").write_text("# OAC\n", encoding="utf-8")
    return orgrebase, oac


def test_source_snapshot_is_deterministic_and_verifies_exact_current_bytes(tmp_path: Path) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_metadata = snapshot.build_source_snapshot(orgrebase, oac, first)
    second_metadata = snapshot.build_source_snapshot(orgrebase, oac, second)

    assert (first / snapshot.ARCHIVE_NAME).read_bytes() == (second / snapshot.ARCHIVE_NAME).read_bytes()
    assert first_metadata == second_metadata
    assert first_metadata["schemaVersion"] == "orgrebase.source-snapshot.v3"
    assert first_metadata["releaseAllowlist"]["status"] == "PASS"
    assert first_metadata["releaseAllowlist"]["policy"] == "EXPLICIT_RELEASE_CLOSURE"
    assert "oacBoundShadowPublication" not in first_metadata
    assert first_metadata["formationTaskflowPublication"] == {"status": "NOT_PRESENT"}
    assert first_metadata["oacPublicRealProcessPublication"] == {"status": "NOT_PRESENT"}
    assert first_metadata["semifinalPublication"] == {"status": "NOT_PRESENT"}
    assert first_metadata["semifinalMvpPublication"] == {"status": "NOT_PRESENT"}
    assert first_metadata["semifinalRuntimeProjection"] == {"status": "NOT_PRESENT"}
    assert first_metadata["compensationPublication"] == {"status": "NOT_PRESENT"}
    metadata_text = (first / snapshot.METADATA_NAME).read_text(encoding="utf-8")
    assert str(orgrebase.resolve()) not in metadata_text
    assert str(oac.resolve()) not in metadata_text
    assert str(first.resolve()) not in metadata_text
    verified = snapshot.verify_source_snapshot(orgrebase, oac, first)
    assert verified["status"] == "PASS"
    assert verified["currentSourceComparison"]["exactByteMatch"] is True
    assert verified["deterministicArchiveReplay"] is True

    (orgrebase / "src" / "runtime.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(snapshot.SnapshotError, match="CURRENT_SOURCE_TREE_MISMATCH"):
        snapshot.verify_source_snapshot(orgrebase, oac, first)


def test_oac_governance_documents_survive_source_delivery(tmp_path: Path) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    documents = {
        "SECURITY.md": "# Security\nReport privately.\n",
        "CODE_OF_CONDUCT.md": "# Conduct\nRespect participants.\n",
        "CHANGELOG.md": "# Changelog\nUnreleased source changes.\n",
    }
    for name, content in documents.items():
        (oac / name).write_text(content)
    output = tmp_path / "delivery"
    snapshot.build_source_snapshot(orgrebase, oac, output)
    with zipfile.ZipFile(output / snapshot.ARCHIVE_NAME) as archive:
        for name, content in documents.items():
            assert archive.read(f"source-snapshot/oac-spec/{name}") == content.encode()


def test_source_snapshot_excludes_local_generated_private_and_environment_state(tmp_path: Path) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    excluded = {
        ".git/config": "private git config",
        ".venv/pyvenv.cfg": "local venv",
        ".cache/result.json": "cache",
        ".env": "API_TOKEN=do-not-package",
        "local.sqlite3": "database",
        "evidence/agentteams/private-sessions/session.json": "private session",
        "evidence/agentteams/live-sources/request.json": "private source",
        "evidence/agentteams/nonce-ledger.json": "private nonce",
        "evidence/agentteams/run-1/private-sessions/session.json": "nested private session",
        "evidence/agentteams/run-1/dispatch-logs/worker.log": "nested dispatch log",
        "submission/demo-capture-manifest.json": "media-specific external manifest",
    }
    for relative, content in excluded.items():
        path = orgrebase / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    output = tmp_path / "output"
    metadata = snapshot.build_source_snapshot(orgrebase, oac, output)
    inventory_paths = {item["path"] for item in metadata["inventory"]["files"]}
    assert "orgrebase/src/runtime.py" in inventory_paths
    assert not any(path.removeprefix("orgrebase/") in excluded for path in inventory_paths)
    exclusion_paths = {item["path"] for item in metadata["components"]["orgrebase"]["excluded"]}
    assert all(
        any(
            relative == excluded_path or relative.startswith(excluded_path + "/")
            for excluded_path in exclusion_paths
        )
        for relative in excluded
    )

    detector_source = b"Bearer must-" + b"not-enter-attempt-log\n" + b"Bearer super-" + b"secret\n"
    violations, sentinels = snapshot._credential_scan(
        "orgrebase/scripts/build_source_snapshot.py",
        detector_source,
    )
    assert violations == []
    assert sentinels == [
        {
            "path": "orgrebase/scripts/build_source_snapshot.py",
            "patternId": "bearer_value",
            "matches": 2,
        }
    ]

    violations, _ = snapshot._credential_scan(
        "orgrebase/scripts/build_source_snapshot.py",
        detector_source + b"Bearer actual-" + b"unregistered-secret\n",
    )
    assert violations == ["CREDENTIAL_PATTERN:bearer_value:orgrebase/scripts/build_source_snapshot.py:1"]


def test_source_snapshot_uses_a_release_allowlist_not_a_repository_mirror(
    tmp_path: Path,
) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    included = {
        ".github/workflows/ci.yml": "name: CI\n",
        ".github/dependabot.yml": "version: 2\n",
        ".github/pull_request_template.md": "## Verification\n",
        ".github/ISSUE_TEMPLATE/bug_report.yml": "name: Bug report\n",
        ".github/ISSUE_TEMPLATE/config.yml": "blank_issues_enabled: false\n",
        ".github/ISSUE_TEMPLATE/feature_request.yml": "name: Feature request\n",
        "docs/ARCHITECTURE.md": "# Architecture\n",
        "benchmark/public-retail-quote/v1/README.md": "Public data attribution\n",
        "benchmark/public-retail-quote/v1/LICENSE.md": "CC BY 4.0\n",
        "benchmark/public-retail-quote/v1/dataset-manifest.json": "{}\n",
        "benchmark/public-retail-quote/v1/sample.json": "{}\n",
        "benchmark/quote-value-v0.3-public-process/summary.json": "{}\n",
        "benchmark/product-path-v0.1/MANIFEST.sha256": "fixed predecessor\n",
        "benchmark/product-path-v0.2-source-bound/BENCHMARK-MIGRATION.json": "{}\n",
        "benchmark/product-path-v0.3-task-intake-bound/BENCHMARK-MIGRATION.json": "{}\n",
        "benchmark/quote-value-v0.4-bpi-real-process/dataset-manifest.json": "{}\n",
        "benchmark/quote-value-v0.4-bpi-real-process/projection/process.json": "{}\n",
        "evidence/golden-competition/latest/pilot/manifest.json": "{}\n",
        "evidence/oac-quote-adaptation/latest/manifest.json": "{}\n",
        "evidence/oac-public-real-process/latest/summary.json": "{}\n",
        "evidence/public-real-process/latest/summary.json": "{}\n",
        "evidence/oac-agentic-adaptation/latest/summary.json": "{}\n",
        "evidence/formation-taskflow/latest/probe-receipt.json": "{}\n",
        "evidence/semifinal-closure/latest/summary.json": "{}\n",
        "evidence/semifinal-governed/latest/summary.json": "{}\n",
        "evidence/semifinal-mvp/latest/summary.json": "{}\n",
        "evidence/skill-predecessor-rollback/latest/summary.json": "{}\n",
        "evidence/public-process/latest/public-process-bridge-receipt.json": "{}\n",
        "evidence/agentteams/public/historical-transport-v1.2.2.json": "{}\n",
        (
            "evidence/agentteams/fresh-live/2026-08-25-561171ed039b/"
            "RUN-SUMMARY.json"
        ): "{}\n",
        (
            "evidence/agentteams/fresh-live/2026-08-25-561171ed039b/"
            "live-receipt.json"
        ): "{}\n",
        (
            "evidence/agentteams/fresh-live/2026-08-25-561171ed039b/"
            "semantic-ingestion.json"
        ): "{}\n",
        (
            "evidence/agentteams/fresh-live/2026-08-25-561171ed039b/"
            "SHA256SUMS"
        ): "checksums\n",
        load_teamharness_lock()["offline_bundle"]["path"]: "bundle\n",
        (
            "vendor/predecessors/enterprise-quote-compose/1.3.0/"
            "orgrebase-0.4.0-py3-none-any.whl"
        ): "wheel\n",
        "evidence/release-facts.json": "{}\n",
    }
    excluded = {
        ".github/workflows/private-deployment.yml": "not part of the source delivery\n",
        ".github/ISSUE_TEMPLATE/internal.yml": "not a public contribution entrypoint\n",
        "docs/specs/066-clean-semifinal-release-seal/spec.md": "internal spec\n",
        "docs/待做/001-future.md": "future research\n",
        "benchmark/product-path-v0.4-experimental/summary.json": "future benchmark\n",
        "evidence/quote-value/latest/summary.json": "superseded evidence\n",
        "evidence/golden-competition/archive/old/manifest.json": "archive\n",
        "evidence/oac-agentic-adaptation/archive/old/summary.json": "archive\n",
        "evidence/oac-bound-shadow/latest/summary.json": "superseded shadow evidence\n",
        (
            "evidence/oac-bound-shadow/reference/"
            "fencing-lifecycle-receipt.json"
        ): "superseded shadow support\n",
        "evidence/oac-public-real-process/archive/old/summary.json": "archive\n",
        "submission/internal-workbench.md": "authoring workbench\n",
        "notes/private-design.md": "not release material\n",
        "benchmark/public-retail-quote/v1/raw.xlsx": "raw download not in release\n",
        "benchmark/public-retail-quote/v1/run.sqlite": "local execution not in release\n",
    }
    for relative, content in {**included, **excluded}.items():
        path = orgrebase / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    oac_included = {
        "src/oac/runtime.py": "VALUE = 1\n",
        "schemas/core.schema.json": "{}\n",
        "tck/test-core.json": "{}\n",
        "ctk/bundles/core.json": "{}\n",
        "experiments/plan-verification-portability/v0.1-seed-1/artifacts/SC-008/SC-008.change.json": "{}\n",
        "experiments/plan-verification-portability/v0.1-seed-1/artifacts/SC-008/veracier-proc01-contextual.snapshot.json": "{}\n",
        (
            "experiments/plan-verification-portability/v0.1-seed-1/artifacts/plans/"
            "PV-POS-SC008-SPLIT.plan.json"
        ): "{}\n",
    }
    oac_excluded = {
        "experiments/plan-verification-portability/v0.1-seed-1/artifacts/SC-008/private-note.json": "private\n",
        "experiments/portability/result.json": "{}\n",
        "specs/009-future/spec.md": "research\n",
        "dist/oac.whl": "generated\n",
    }
    for relative, content in {**oac_included, **oac_excluded}.items():
        path = oac / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    for relative in included:
        if relative.endswith(".whl"):
            with zipfile.ZipFile(orgrebase / relative, "w") as wheel:
                wheel.writestr("placeholder.py", "VALUE = 1\n")
    snapshot_root = tmp_path / "prepared" / snapshot.SNAPSHOT_ROOT_NAME
    snapshot._prepare_snapshot(orgrebase, oac, snapshot_root)
    paths = {item["path"] for item in snapshot._inventory(snapshot_root)}
    assert {f"orgrebase/{path}" for path in included} <= paths
    assert not ({f"orgrebase/{path}" for path in excluded} & paths)
    assert {f"oac-spec/{path}" for path in oac_included} <= paths
    assert not ({f"oac-spec/{path}" for path in oac_excluded} & paths)


def test_source_snapshot_closes_actual_console_dependencies_without_git_tracking(tmp_path: Path) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    console = orgrebase / "demo/console"
    console.mkdir(parents=True)
    (console / "index.html").write_text(
        '<script src="/assets/change-workbench.js?v=2#module"></script>'
        '<link rel="alternate stylesheet" href="./assets/change-workbench.css?v=3#style">'
        '<script src="https://cdn.example.invalid/external.js?v=4"></script>'
        '<link rel="stylesheet" href="//cdn.example.invalid/external.css">',
        encoding="utf-8",
    )
    (console / "change-workbench.js").write_text("export const ready = true;\n")
    (console / "change-workbench.css").write_text("body { color: black; }\n")
    metadata = snapshot.build_source_snapshot(orgrebase, oac, tmp_path / "delivery")
    assert metadata["runtimeClosure"]["status"] == "PASS"
    inventory = {item["path"] for item in metadata["inventory"]["files"]}
    assert {
        "orgrebase/demo/console/change-workbench.js",
        "orgrebase/demo/console/change-workbench.css",
    } <= inventory
    assert snapshot.verify_source_snapshot(orgrebase, oac, tmp_path / "delivery")["status"] == "PASS"


@pytest.mark.parametrize(
    "markup",
    (
        '<script src="/assets/missing.js?v=1"></script>',
        '<link rel="stylesheet" href="assets/missing.css#style">',
        '<script src="/assets/missing.js" src="https://cdn.example.invalid/external.js"></script>',
    ),
)
def test_source_snapshot_rejects_missing_console_script_or_stylesheet(tmp_path: Path, markup: str) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    console = orgrebase / "demo/console"
    console.mkdir(parents=True)
    (console / "index.html").write_text(markup, encoding="utf-8")
    with pytest.raises(snapshot.SnapshotError, match="RELEASE_RUNTIME_CLOSURE_INCOMPLETE"):
        snapshot.build_source_snapshot(orgrebase, oac, tmp_path / "delivery")
    assert not (tmp_path / "delivery").exists()


@pytest.mark.parametrize(
    "reference",
    (
        "/assets/../outside.js",
        "/assets/%2e%2e/outside.js",
        "/assets/%2e%2e%2foutside.js",
        "/assets/..%5coutside.js",
        "/assets/%00bad.js",
        "/other/app.js",
        "javascript:alert(1)",
        "?v=1",
    ),
)
def test_source_snapshot_rejects_unsafe_console_asset_paths(tmp_path: Path, reference: str) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    console = orgrebase / "demo/console"
    console.mkdir(parents=True)
    (console / "index.html").write_text(f'<script src="{reference}"></script>', encoding="utf-8")
    with pytest.raises(snapshot.SnapshotError, match=r"CONSOLE_ASSET_PATH_INVALID|UNSAFE_SOURCE_PATH"):
        snapshot.build_source_snapshot(orgrebase, oac, tmp_path / "delivery")
    assert not (tmp_path / "delivery").exists()


def test_source_snapshot_rejects_base_url_that_changes_asset_resolution(tmp_path: Path) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    console = orgrebase / "demo/console"
    console.mkdir(parents=True)
    (console / "index.html").write_text('<base href="https://cdn.example.invalid/">', encoding="utf-8")
    with pytest.raises(snapshot.SnapshotError, match="CONSOLE_ASSET_BASE_URL_UNSUPPORTED"):
        snapshot.build_source_snapshot(orgrebase, oac, tmp_path / "delivery")


@pytest.mark.parametrize("document", ("README.md", "README.zh-CN.md", "CONTRIBUTING.md"))
def test_source_snapshot_keeps_public_automation_without_private_workflows(
    tmp_path: Path, document: str
) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    (orgrebase / document).write_text("[PR template](.github/pull_request_template.md)\n")
    with pytest.raises(snapshot.SnapshotError, match="RELEASE_RUNTIME_CLOSURE_INCOMPLETE"):
        snapshot.build_source_snapshot(orgrebase, oac, tmp_path / "missing-template")
    (orgrebase / ".github/workflows").mkdir(parents=True)
    (orgrebase / ".github/pull_request_template.md").write_text("## Verification\n")
    (orgrebase / ".github/workflows/private-deployment.yml").write_text("private workflow\n")
    (orgrebase / ".github/workflows/ci.yml").write_text("name: Public checks\n")
    (orgrebase / ".github/dependabot.yml").write_text("version: 2\n")
    metadata = snapshot.build_source_snapshot(orgrebase, oac, tmp_path / "delivery")
    inventory = {item["path"] for item in metadata["inventory"]["files"]}
    assert "orgrebase/.github/pull_request_template.md" in inventory
    assert "orgrebase/.github/workflows/ci.yml" in inventory
    assert "orgrebase/.github/dependabot.yml" in inventory
    assert "orgrebase/.github/workflows/private-deployment.yml" not in inventory


def test_source_snapshot_accepts_contributing_guide_without_a_template_dependency(tmp_path: Path) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    (orgrebase / "CONTRIBUTING.md").write_text("Run the focused tests before proposing a change.\n")
    metadata = snapshot.build_source_snapshot(orgrebase, oac, tmp_path / "delivery")
    assert metadata["runtimeClosure"]["status"] == "PASS"


def test_source_snapshot_excludes_the_entire_submission_tree_even_when_nested(
    tmp_path: Path,
) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    submission_files = {
        "submission/README.md": "old evaluator notes\n",
        "submission/nested/technical-report.pdf": "not the executable source package\n",
        "submission/nested/demo/source/runtime.py": "must not leak through a source-like suffix\n",
    }
    for relative, content in submission_files.items():
        path = orgrebase / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    output = tmp_path / "output"
    metadata = snapshot.build_source_snapshot(orgrebase, oac, output)
    inventory_paths = {item["path"] for item in metadata["inventory"]["files"]}

    assert not any(path.startswith("orgrebase/submission/") for path in inventory_paths)
    assert metadata["releaseAllowlist"]["alwaysExcludedTreePrefixes"] == ["submission"]
    assert metadata["releaseAllowlist"]["submissionTreePackaged"] is False
    assert {(item["path"], item["reason"]) for item in metadata["components"]["orgrebase"]["excluded"]} >= {
        ("submission", "separate_submission_delivery_surface")
    }


def test_source_snapshot_documents_the_three_verification_authorities() -> None:
    release = (ROOT / "RELEASE-VERIFICATION.md").read_text(encoding="utf-8")
    readiness = (ROOT / "docs/REVIEW-READINESS.md").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    for published_text in (snapshot.README_FIRST, release, readiness):
        assert "make semifinal-mvp-check" in published_text
    for published_text in (release, readiness):
        assert "python3 -B verify-submission.py --deep" in published_text
    assert "verify-submission.py" not in snapshot.README_FIRST
    assert "outer delivery README" in snapshot.README_FIRST
    assert "外层实际交付 README" in snapshot.README_FIRST_ZH
    assert "不是 source snapshot 的复验入口" in release
    assert "not a source-snapshot reproduction command" in " ".join(readiness.split())
    assert "Developer-only aggregate gate" in makefile
    assert "GOAI_REPO_DEMO_MEDIA" in makefile


def test_source_snapshot_keeps_bpi_projection_but_never_raw_dataset(
    tmp_path: Path,
) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    benchmark = orgrebase / "benchmark/quote-value-v0.4-bpi-real-process"
    projection = benchmark / "projection/bpi2019-real-process-projection.json"
    receipt = benchmark / "retained/bpi2019-real-process-benchmark-receipt.json"
    manifest = benchmark / "dataset-manifest.json"
    raw = benchmark / "raw/BPI_Challenge_2019.xes"
    for path, content in (
        (projection, "{}\n"),
        (receipt, "{}\n"),
        (manifest, '{"source_sha256":"sha256:abc"}\n'),
        (raw, "raw-event-log"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    snapshot_root = tmp_path / "prepared" / snapshot.SNAPSHOT_ROOT_NAME
    snapshot._prepare_snapshot(orgrebase, oac, snapshot_root)
    paths = {item["path"] for item in snapshot._inventory(snapshot_root)}
    assert (
        "orgrebase/benchmark/quote-value-v0.4-bpi-real-process/"
        "projection/bpi2019-real-process-projection.json"
    ) in paths
    assert (
        "orgrebase/benchmark/quote-value-v0.4-bpi-real-process/"
        "retained/bpi2019-real-process-benchmark-receipt.json"
    ) in paths
    assert ("orgrebase/benchmark/quote-value-v0.4-bpi-real-process/dataset-manifest.json") in paths
    assert not any("BPI_Challenge_2019.xes" in path for path in paths)


def test_source_snapshot_entries_are_bilingual_and_use_explicit_cloud_mode() -> None:
    for english, chinese in ((snapshot.README_FIRST, snapshot.README_FIRST_ZH),
                             (snapshot.RUNTIME_README, snapshot.RUNTIME_README_ZH)):
        assert "README-FIRST.zh-CN.md" in english
        assert "README-FIRST.md" in chinese
        for text in (english, chinese):
            assert "uv sync --locked --all-extras" in text
            assert "run-semifinal-demo.sh live" in text
            assert "Ollama" not in text and "qwen2.5" not in text
    assert "verify_golden_pilot_evidence.py" in snapshot.README_FIRST
    assert "make check-core" in snapshot.RUNTIME_README


def test_source_snapshot_requires_public_golden_closure_without_runtime_database() -> None:
    required = set(snapshot.CURRENT_GOLDEN_RUNTIME_REQUIRED_FILES)

    assert {
        "manifest.json",
        "verification.json",
        "state.json",
        "evidence-export.json",
        "quote-export.json",
        "golden-run/summary.json",
        "golden-run/agentteams/execution-plan.json",
        "golden-run/inputs/task-formation-decision-receipt.json",
        "golden-run/inputs/task-agent-context-envelope.json",
        "golden-run/tool/invocation.json",
        "golden-run/skill/receipt.json",
        "restart/completed-state-restart-receipt.json",
        "restart/completed-state-restart-verification.json",
        "restart/state-before.json",
        "restart/state-after.json",
    } == required
    assert not any(path.endswith(snapshot.DATABASE_SUFFIXES) for path in required)
    assert all(
        (ROOT / snapshot.GOLDEN_PILOT_PORTABLE_EVIDENCE_PREFIX / relative).is_file()
        for relative in required
    )


def test_source_snapshot_requires_replayable_agentteams_public_closure() -> None:
    required = set(snapshot.AGENTTEAMS_RELEASE_RUNTIME_REQUIRED_FILES)

    assert {
        "run-agentteams-demo.sh",
        "verify-agentteams-demo.sh",
        "configs/goai-agentteams-demo.json",
        "examples/agentteams/change-request.json",
        "evidence/agentteams/public/historical-transport-v1.2.2.json",
        "evidence/agentteams/fresh-live/2026-08-25-561171ed039b/RUN-SUMMARY.json",
        "evidence/agentteams/fresh-live/2026-08-25-561171ed039b/live-receipt.json",
        "evidence/agentteams/fresh-live/2026-08-25-561171ed039b/semantic-ingestion.json",
        "evidence/agentteams/fresh-live/2026-08-25-561171ed039b/SHA256SUMS",
    } == required
    assert all((ROOT / relative).is_file() for relative in required)
    assert all(
        snapshot._exclusion_reason(
            PurePosixPath(relative),
            "orgrebase",
            is_dir=False,
        )
        is None
        for relative in required
    )


def test_source_snapshot_allowlists_only_current_release_evidence() -> None:
    for relative, is_dir in (
        ("evidence/oac-quote-adaptation/latest/manifest.json", False),
        ("evidence/oac-public-real-process/latest/summary.json", False),
        ("evidence/public-real-process/latest/summary.json", False),
        ("evidence/oac-agentic-adaptation/latest/summary.json", False),
        ("evidence/formation-taskflow/latest/probe-receipt.json", False),
        ("evidence/semifinal-closure/latest/agentteams/lifecycle-receipt.json", False),
        ("evidence/semifinal-closure/latest/summary.json", False),
        ("evidence/semifinal-closure/supporting/quote-value-inputs/manifest.json", False),
        ("evidence/semifinal-closure/supporting/quote-value-inputs/source-root/"
         "evidence/workspace/latest/evaluation-suite.json", False),
        ("evidence/semifinal-governed/latest/summary.json", False),
        ("evidence/semifinal-mvp/latest/summary.json", False),
        ("evidence/skill-predecessor-rollback/latest/summary.json", False),
        ("evidence/public-process/latest/public-process-bridge-receipt.json", False),
        ("evidence/release-facts.json", False),
        ("evidence/latest/failure-receipt.json", False),
        ("evidence/latest/rollback-evidence.json", False),
        ("evidence/latest/git-tool-evidence.json", False),
        ("benchmark/quote-value-v0.3-public-process/MANIFEST.sha256", False),
        ("benchmark/product-path-v0.1/MANIFEST.sha256", False),
        (
            "benchmark/product-path-v0.2-source-bound/BENCHMARK-MIGRATION.json",
            False,
        ),
        (
            "benchmark/product-path-v0.3-task-intake-bound/BENCHMARK-MIGRATION.json",
            False,
        ),
        ("evidence/workspace/latest/product-path-artifacts.json", False),
        ("evidence/workspace/latest/product-path-blackbox.json", False),
        ("evidence/workspace/latest/product-path-observations.json", False),
        ("evidence/workspace/latest/product-path-wheel.whl", False),
    ):
        assert snapshot._exclusion_reason(PurePosixPath(relative), "orgrebase", is_dir=is_dir) is None

    assert (
        snapshot._exclusion_reason(
            PurePosixPath("evidence/golden-competition/latest/pilot/workspace.sqlite3"),
            "orgrebase",
            is_dir=False,
        )
        == "private_golden_runtime_database"
    )
    assert (
        snapshot._exclusion_reason(
            PurePosixPath("evidence/golden-competition/latest/pilot/workspace.sqlite3-wal"),
            "orgrebase",
            is_dir=False,
        )
        == "private_golden_runtime_database"
    )
    for superseded in (
        "evidence/golden-competition/archive",
        (
            "evidence/golden-competition/archive/"
            "3cf35e5f-5d0f-40c0-99f8-a5847a5daead/manifest.json"
        ),
        (
            "evidence/golden-competition/archive/"
            "3cf35e5f-5d0f-40c0-99f8-a5847a5daead/workspace.sqlite3"
        ),
        "evidence/oac-bound-shadow/latest/summary.json",
        "evidence/oac-bound-shadow/reference/fencing-lifecycle-receipt.json",
    ):
        assert (
            snapshot._exclusion_reason(
                PurePosixPath(superseded),
                "orgrebase",
                is_dir=False,
            )
            == "not_in_release_allowlist"
        )


def test_source_snapshot_excludes_every_historical_golden_archive_file() -> None:
    historical_root = ROOT / "evidence/golden-competition/archive"
    historical_files = {
        "evidence/golden-competition/archive/old/manifest.json",
        "evidence/golden-competition/archive/old/workspace.sqlite3",
        "evidence/golden-competition/archive/old/workspace.sqlite3-wal",
        "evidence/golden-competition/archive/old/workspace.sqlite3-shm",
        *(
            path.relative_to(ROOT).as_posix()
            for path in historical_root.rglob("*")
            if path.is_file()
        ),
    }

    assert all(
        snapshot._exclusion_reason(
            PurePosixPath(relative),
            "orgrebase",
            is_dir=False,
        )
        == "not_in_release_allowlist"
        for relative in historical_files
    )

    assert (
        snapshot._exclusion_reason(
            PurePosixPath(
                "experiments/plan-verification-portability/v0.1-seed-1/artifacts/plans/"
                "PV-POS-SC008-SPLIT.plan.json"
            ),
            "oac-spec",
            is_dir=False,
        )
        is None
    )

    assert (
        snapshot._exclusion_reason(
            PurePosixPath("benchmark/product-path-v0.2-old/MANIFEST.sha256"),
            "orgrebase",
            is_dir=False,
        )
        == "not_in_release_allowlist"
    )
    assert (
        snapshot._exclusion_reason(
            PurePosixPath("benchmark/product-path-v0.4-experimental/MANIFEST.sha256"),
            "orgrebase",
            is_dir=False,
        )
        == "not_in_release_allowlist"
    )
    assert (
        snapshot._exclusion_reason(PurePosixPath("local.sqlite"), "orgrebase", is_dir=False)
        == "not_in_release_allowlist"
    )
    assert (
        snapshot._exclusion_reason(
            PurePosixPath("evidence/semifinal-closure/archive/old/summary.json"),
            "orgrebase",
            is_dir=False,
        )
        == "not_in_release_allowlist"
    )
    assert (
        snapshot._exclusion_reason(
            PurePosixPath("evidence/semifinal-governed/archive/old/summary.json"),
            "orgrebase",
            is_dir=False,
        )
        == "not_in_release_allowlist"
    )
    assert (
        snapshot._exclusion_reason(
            PurePosixPath("evidence/oac-public-real-process/archive/old/summary.json"),
            "orgrebase",
            is_dir=False,
        )
        == "not_in_release_allowlist"
    )
    assert (
        snapshot._exclusion_reason(
            PurePosixPath("evidence/oac-public-real-process/latest-copy/summary.json"),
            "orgrebase",
            is_dir=False,
        )
        == "not_in_release_allowlist"
    )
    assert (
        snapshot._exclusion_reason(
            PurePosixPath("evidence/oac-agentic-adaptation/archive/old/summary.json"),
            "orgrebase",
            is_dir=False,
        )
        == "not_in_release_allowlist"
    )
    assert (
        snapshot._exclusion_reason(
            PurePosixPath("evidence/semifinal-closure/latest/operations/observability/telemetry.sqlite-wal"),
            "orgrebase",
            is_dir=False,
        )
        == "generated_database_journal"
    )


def test_source_snapshot_rejects_incomplete_semifinal_publication(tmp_path: Path) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    del oac
    latest = orgrebase / "evidence/semifinal-closure/latest"
    latest.mkdir(parents=True)
    (latest / "summary.json").write_text("{}", encoding="utf-8")

    with pytest.raises(snapshot.SnapshotError, match="SEMIFINAL_PUBLICATION_INCOMPLETE"):
        snapshot._verify_semifinal_publication(orgrebase)


def test_source_snapshot_rejects_incomplete_formation_taskflow_publication(
    tmp_path: Path,
) -> None:
    orgrebase, _oac = _source_roots(tmp_path)
    latest = orgrebase / "evidence/formation-taskflow/latest"
    latest.mkdir(parents=True)
    (latest / "probe-receipt.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(
        snapshot.SnapshotError,
        match="FORMATION_TASKFLOW_PUBLICATION_INCOMPLETE",
    ):
        snapshot._verify_formation_taskflow_publication(orgrebase)


def test_retained_agentteams_source_selection_does_not_relabel_history(tmp_path: Path) -> None:
    active = load_teamharness_lock(ROOT)
    active_path, current, current_scope = snapshot._retained_agentteams_source_lock(
        ROOT, snapshot._agentteams_lock_digest(active),
    )
    historical_digest, relative = next(iter(snapshot.HISTORICAL_TEAMHARNESS_LOCKS.items()))
    path, historical, scope = snapshot._retained_agentteams_source_lock(ROOT, historical_digest)
    assert active_path == ROOT / "agentteams/teamharness-lock.json"
    assert current == active and current_scope == "ACTIVE_SOURCE"
    assert scope == "HISTORICAL_SOURCE" and historical["tag"] == "v1.2.2"
    assert historical["commit"] != current["commit"]
    assert path == ROOT / relative

    assets = tmp_path / "agentteams"
    assets.mkdir()
    for name in ("source-lock.json", "teamharness-lock.json"):
        (assets / name).write_bytes((ROOT / "agentteams" / name).read_bytes())
    retained = tmp_path / relative
    retained.parent.mkdir()
    retained.write_text(json.dumps({**historical, "tag": current["tag"]}), encoding="utf-8")
    with pytest.raises(snapshot.SnapshotError, match="RETAINED_AGENTTEAMS_SOURCE_LOCK_INVALID"):
        snapshot._retained_agentteams_source_lock(tmp_path, historical_digest)
    with pytest.raises(snapshot.SnapshotError, match="RETAINED_AGENTTEAMS_SOURCE_NOT_REGISTERED"):
        snapshot._retained_agentteams_source_lock(ROOT, "sha256:" + "0" * 64)


def test_retained_native_publications_replay_under_their_original_source() -> None:
    from scripts.verify_oac_agent_adaptation import VerificationError, verify

    formation = snapshot._verify_formation_taskflow_publication(ROOT)
    mapping = snapshot._verify_oac_agentic_adaptation_publication(ROOT)
    for result in (formation, mapping):
        assert result["status"] == "PASS"
        assert result["agentTeamsVersion"] == "v1.2.2"
        assert result["sourceVerificationScope"] == "HISTORICAL_SOURCE"
    with pytest.raises(VerificationError, match="AGENTTEAMS_LIFECYCLE_BINDING_INVALID"):
        verify(ROOT / "evidence/oac-agentic-adaptation/latest")


def test_matrix_fixture_token_exception_is_limited_to_its_exact_test_path() -> None:
    relative = "orgrebase/tests/workspace/test_matrix_observation.py"
    token = next(iter(snapshot.SYNTHETIC_CREDENTIAL_SENTINELS[(relative, "bearer_value")]))
    violations, sentinels = snapshot._credential_scan(relative, token)
    assert violations == []
    assert sentinels == [{"path": relative, "patternId": "bearer_value", "matches": 1}]
    other = "orgrebase/src/orgrebase/workspace/matrix_observation.py"
    violations, sentinels = snapshot._credential_scan(other, token)
    assert violations == [f"CREDENTIAL_PATTERN:bearer_value:{other}:1"]
    assert sentinels == []


def test_source_snapshot_rejects_incomplete_oac_public_real_process_publication(
    tmp_path: Path,
) -> None:
    orgrebase, _oac = _source_roots(tmp_path)
    latest = orgrebase / "evidence/oac-public-real-process/latest"
    latest.mkdir(parents=True)
    (latest / "adaptation-receipt.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(
        snapshot.SnapshotError,
        match="OAC_PUBLIC_REAL_PROCESS_PUBLICATION_INCOMPLETE",
    ):
        snapshot._verify_oac_public_real_process_publication(orgrebase)


def test_oac_quote_adaptation_publication_requires_owner_review_summary_manifest_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    evidence = orgrebase / "evidence/oac-quote-adaptation/latest"
    evidence.mkdir(parents=True)
    (orgrebase / "scripts").mkdir(exist_ok=True)
    (orgrebase / "scripts/verify_oac_quote_adaptation.py").write_text(
        "# verifier fixture\n",
        encoding="utf-8",
    )
    owner_review = evidence / "artifacts/evergreen/owner-review-summary.json"
    owner_review.parent.mkdir(parents=True)
    owner_review.write_text("{}\n", encoding="utf-8")
    manifest = {
        "status": "CLOSED_WORLD",
        "entry_count": 21,
        "entries": [
            {"path": f"artifacts/fixture-{index:02d}.json"}
            for index in range(21)
        ],
        "pack_digest": "sha256:fixture",
    }
    (evidence / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    summary = {
        "status": "PASS",
        "canonical_target_writes": 0,
        "evergreen": {
            "status": "READY_FOR_ORGREBASE",
            "mapping_count": 5,
            "gap_count": 0,
            "bound_execution_started": False,
        },
        "veracier": {
            "status": "HOLD",
            "gap_count": 7,
            "capsule_produced": False,
            "execution_started": False,
        },
        "oac_boundary": {
            "source_and_demand_validated": True,
            "source_admission_validated": True,
            "oac_plan_produced": False,
            "oac_plan_certificate_produced": False,
            "oac_runtime_invoked": False,
        },
        "real_enterprise_connectors": "NOT_RUN",
        "real_enterprise_data": "NOT_RUN",
        "enterprise_uat": "NOT_RUN",
        "production_sla_ha_dr": "NOT_RUN",
    }
    (evidence / "summary.json").write_text(
        json.dumps(summary),
        encoding="utf-8",
    )
    verifier_result = {
        "status": "PASS",
        "verification_scope": "RETAINED_ARTIFACT",
        "current_release_qualified": False,
        "evidence_class": "VALIDATED_CONTROLLED_LOCAL",
        "claim_ceiling": "CONTROLLED_LOCAL_ADAPTATION_MECHANISM_ONLY",
        "failure_codes": [],
        "oac_public_cli_checks": 5,
        "mutation_rejections": {f"case-{index}": "PASS" for index in range(6)},
        "real_review_wait_ms": 4000,
        "canonical_target_writes": 0,
        "manifest_pack_digest": "sha256:fixture",
    }
    monkeypatch.setattr(
        snapshot.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(verifier_result),
            stderr="",
        ),
    )

    with pytest.raises(
        snapshot.SnapshotError,
        match="OAC_QUOTE_ADAPTATION_PUBLICATION_VERIFY_FAILED",
    ):
        snapshot._verify_oac_quote_adaptation_publication(orgrebase, oac)

    manifest["entries"][0]["path"] = "artifacts/evergreen/owner-review-summary.json"
    (evidence / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    owner_review.unlink()
    with pytest.raises(
        snapshot.SnapshotError,
        match="OAC_QUOTE_ADAPTATION_PUBLICATION_VERIFY_FAILED",
    ):
        snapshot._verify_oac_quote_adaptation_publication(orgrebase, oac)

    owner_review.write_text("{}\n", encoding="utf-8")
    publication = snapshot._verify_oac_quote_adaptation_publication(orgrebase, oac)
    assert publication["status"] == "PASS"
    assert publication["entryCount"] == 21
    assert publication["verificationScope"] == "RETAINED_ARTIFACT"
    assert publication["currentReleaseQualified"] is False
    verifier_result["current_release_qualified"] = True
    with pytest.raises(snapshot.SnapshotError, match="OAC_QUOTE_ADAPTATION_PUBLICATION_VERIFY_FAILED"):
        snapshot._verify_oac_quote_adaptation_publication(orgrebase, oac)


def test_source_snapshot_rejects_unregistered_machine_local_paths(
    tmp_path: Path,
) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    leaked = b"command=" + b"/" + b"Users/reviewer/private/adapter.py\n"
    (orgrebase / "src" / "runtime.py").write_bytes(leaked)

    with pytest.raises(
        snapshot.SnapshotError,
        match=r"MACHINE_LOCAL_PATH:macos_user_home:.*runtime\.py:1",
    ):
        snapshot.build_source_snapshot(orgrebase, oac, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_source_snapshot_never_rewrites_retained_evidence_bytes(
    tmp_path: Path,
) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    evidence = orgrebase / "evidence/public-process/latest/retained-receipt.json"
    evidence.parent.mkdir(parents=True)
    original = b'{"receipt_digest":"sha256:abc","source":"orgrebase://evidence"}\n'
    evidence.write_bytes(original)

    output = tmp_path / "output"
    metadata = snapshot.build_source_snapshot(orgrebase, oac, output)
    with zipfile.ZipFile(output / snapshot.ARCHIVE_NAME) as archive:
        packaged = archive.read(
            "source-snapshot/orgrebase/evidence/public-process/latest/retained-receipt.json"
        )

    assert packaged == original
    assert metadata["releaseAllowlist"]["sourceContentPolicy"] == ("COPY_ORIGINAL_BYTES_ONLY")
    assert metadata["releaseAllowlist"]["evidenceContentRewrites"] == 0
    assert metadata["audit"]["machineLocalPathPolicy"] == {
        "status": "PASS_NO_REAL_HOST_PATH_EXCEPTIONS",
        "unregisteredReferences": 0,
        "realHostPathExceptions": 0,
        "immutableEvidencePathExceptions": 0,
        "toolInputReferences": 0,
        "allRealHostReferencesFailClosed": True,
        "syntheticSentinelsOnly": True,
        "contentRewriteMode": "NONE_ORIGINAL_BYTES_ONLY",
        "evidenceBytesRewritten": False,
    }
    assert metadata["audit"]["machineLocalPathExceptions"] == []
    assert metadata["audit"]["machineLocalPathsUsedAsToolInputs"] is False


def test_source_snapshot_fails_closed_for_machine_path_in_retained_evidence(
    tmp_path: Path,
) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    evidence = orgrebase / "evidence/public-process/latest/retained-receipt.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b'{"tool_input":"' + b"/" + b'tmp/private-runtime/input.json"}\n')

    output = tmp_path / "output"
    with pytest.raises(
        snapshot.SnapshotError,
        match=r"MACHINE_LOCAL_PATH:unix_tmp:.*receipt\.json:1",
    ):
        snapshot.build_source_snapshot(orgrebase, oac, output)
    assert not output.exists()


def test_source_snapshot_machine_path_exceptions_are_exact_and_classified() -> None:
    synthetic = b"/" + b"Users/alice/private-checkout"
    violations, references, used_as_tool_input = snapshot._machine_local_path_scan(
        "source-snapshot/orgrebase/tests/workspace/test_native_taskflow.py",
        synthetic,
    )
    assert violations == []
    assert references == [
        {
            "path": ("source-snapshot/orgrebase/tests/workspace/test_native_taskflow.py"),
            "patternId": "macos_user_home",
            "matches": 1,
            "classification": "SYNTHETIC_TEST_SENTINEL",
            "usedAsToolInput": False,
        }
    ]
    assert used_as_tool_input is False

    portable_tmp = b"--source " + b"/" + b"tmp/orgrebase-bpi2019-raw.xes"
    violations, references, used_as_tool_input = snapshot._machine_local_path_scan(
        ("source-snapshot/orgrebase/benchmark/quote-value-v0.4-bpi-real-process/README.md"),
        portable_tmp,
    )
    assert violations == []
    assert references == [
        {
            "path": ("source-snapshot/orgrebase/benchmark/quote-value-v0.4-bpi-real-process/README.md"),
            "patternId": "unix_tmp",
            "matches": 1,
            "classification": "SYNTHETIC_DOCUMENTATION_PLACEHOLDER",
            "usedAsToolInput": False,
        }
    ]
    assert used_as_tool_input is False

    violations, references, used_as_tool_input = snapshot._machine_local_path_scan(
        "source-snapshot/orgrebase/src/runtime.py",
        portable_tmp,
    )
    assert violations == ["MACHINE_LOCAL_PATH:unix_tmp:source-snapshot/orgrebase/src/runtime.py:1"]
    assert references == []
    assert used_as_tool_input is False

    historical = b"command=" + b"/" + b"Users/reviewer/private/adapter.py\n"
    violations, references, used_as_tool_input = snapshot._machine_local_path_scan(
        (
            "source-snapshot/oac-spec/experiments/plan-verification-portability/"
            "v0.1-seed-1/parity-summary.json"
        ),
        historical,
    )
    assert violations == [
        "MACHINE_LOCAL_PATH:macos_user_home:"
        "source-snapshot/oac-spec/experiments/plan-verification-portability/"
        "v0.1-seed-1/parity-summary.json:1"
    ]
    assert references == []
    assert used_as_tool_input is False

    violations, references, used_as_tool_input = snapshot._machine_local_path_scan(
        "source-snapshot/orgrebase/src/runtime.py",
        synthetic,
    )
    assert violations == ["MACHINE_LOCAL_PATH:macos_user_home:source-snapshot/orgrebase/src/runtime.py:1"]
    assert references == []
    assert used_as_tool_input is False


@pytest.mark.parametrize("attack", ["symlink", "special", "unsafe_name", "credential"])
def test_source_snapshot_rejects_unsafe_or_secret_source_entries(tmp_path: Path, attack: str) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    if attack == "symlink":
        (orgrebase / "link.py").symlink_to(orgrebase / "src" / "runtime.py")
        expected = "SYMLINK_REJECTED"
    elif attack == "special":
        os.mkfifo(orgrebase / "src" / "runtime.pipe")
        expected = "SPECIAL_FILE_REJECTED"
    elif attack == "unsafe_name":
        (orgrebase / "unsafe\\name.py").write_text("pass\n", encoding="utf-8")
        expected = "UNSAFE_SOURCE_PATH"
    else:
        (orgrebase / "src" / "credentials.txt").write_text(
            "token=" + "ghp_" + "abcdefghijklmnopqrstuvwxyz123456\n",
            encoding="utf-8",
        )
        expected = "CREDENTIAL_PATTERN:github_token"

    with pytest.raises(snapshot.SnapshotError, match=expected):
        snapshot.build_source_snapshot(orgrebase, oac, tmp_path / "output")


def test_source_snapshot_refuses_overwrite_and_included_output_path(tmp_path: Path) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    output = tmp_path / "output"
    snapshot.build_source_snapshot(orgrebase, oac, output)
    with pytest.raises(snapshot.SnapshotError, match="OUTPUT_DIRECTORY_ALREADY_EXISTS"):
        snapshot.build_source_snapshot(orgrebase, oac, output)

    with pytest.raises(snapshot.SnapshotError, match="OUTPUT_INSIDE_INCLUDED_SOURCE_TREE"):
        snapshot.build_source_snapshot(orgrebase, oac, orgrebase / "release" / "snapshot")


def test_source_snapshot_verify_rejects_archive_and_metadata_tampering(tmp_path: Path) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    output = tmp_path / "output"
    snapshot.build_source_snapshot(orgrebase, oac, output)
    archive = output / snapshot.ARCHIVE_NAME
    archive.write_bytes(archive.read_bytes() + b"tamper")
    with pytest.raises(snapshot.SnapshotError, match="ARCHIVE_SIZE_MISMATCH"):
        snapshot.verify_source_snapshot(orgrebase, oac, output)

    clean = tmp_path / "clean"
    snapshot.build_source_snapshot(orgrebase, oac, clean)
    metadata_path = clean / snapshot.METADATA_NAME
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["inventory"]["files"][0]["sha256"] = "0" * 64
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(snapshot.SnapshotError, match="METADATA_INVENTORY_DIGEST_INVALID"):
        snapshot.verify_source_snapshot(orgrebase, oac, clean)

    allowlist_tamper = tmp_path / "allowlist-tamper"
    snapshot.build_source_snapshot(orgrebase, oac, allowlist_tamper)
    metadata_path = allowlist_tamper / snapshot.METADATA_NAME
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["releaseAllowlist"]["rawBpiDatasetPackaged"] = True
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(snapshot.SnapshotError, match="METADATA_RELEASE_ALLOWLIST_INVALID"):
        snapshot.verify_source_snapshot(orgrebase, oac, allowlist_tamper)


@pytest.mark.parametrize(
    ("metadata_key", "expected_error"),
    [
        (
            "oacPublicRealProcessPublication",
            "OAC_PUBLIC_REAL_PROCESS_PUBLICATION_METADATA_MISMATCH",
        ),
    ],
)
def test_source_snapshot_rejects_replay_publication_metadata_tampering(
    tmp_path: Path,
    metadata_key: str,
    expected_error: str,
) -> None:
    orgrebase, oac = _source_roots(tmp_path)
    output = tmp_path / metadata_key
    snapshot.build_source_snapshot(orgrebase, oac, output)
    metadata_path = output / snapshot.METADATA_NAME
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata[metadata_key] = {"status": "PASS", "forged": True}
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(snapshot.SnapshotError, match=expected_error):
        snapshot.verify_source_snapshot(orgrebase, oac, output)


def test_safe_extract_rejects_parent_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        info = zipfile.ZipInfo("source-snapshot/../escape.txt")
        info.create_system = 3
        info.external_attr = (0o100644) << 16
        handle.writestr(info, b"escape")

    with pytest.raises(snapshot.SnapshotError, match="UNSAFE_SOURCE_PATH"):
        snapshot._safe_extract(archive, tmp_path / "extract")
    assert not (tmp_path / "escape.txt").exists()


def test_capacity_fixture_token_exception_requires_exact_path_and_bytes() -> None:
    relative = "orgrebase/tests/test_http_capacity.py"
    token = next(iter(snapshot.SYNTHETIC_CREDENTIAL_SENTINELS[(relative, "bearer_value")]))
    violations, sentinels = snapshot._credential_scan(relative, token)
    assert violations == []
    assert sentinels == [{"path": relative, "patternId": "bearer_value", "matches": 1}]
    unregistered = token + b"-unregistered"
    violations, sentinels = snapshot._credential_scan(relative, unregistered)
    assert violations == [f"CREDENTIAL_PATTERN:bearer_value:{relative}:1"]
    assert sentinels == []
    other = "orgrebase/src/orgrebase/http_capacity.py"
    violations, sentinels = snapshot._credential_scan(other, token)
    assert violations == [f"CREDENTIAL_PATTERN:bearer_value:{other}:1"]
    assert sentinels == []


def test_runtime_profile_excludes_history_but_keeps_install_resources(tmp_path):
    org, oac = _source_roots(tmp_path)
    for relative in ("evidence/release-facts.json", "benchmark/orgworkbench/v1/cases.json",
                     "benchmark/public-retail-quote/v1/sample.json", "docs/README.md",
                     "evidence/golden-competition/latest/pilot/private-history.txt",
                     "benchmark/quote-value-v0.4-bpi-real-process/projection/large.json"):
        path = org / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
    output = tmp_path / "runtime"
    metadata = snapshot.build_source_snapshot(org, oac, output, profile="runtime")
    paths = {item["path"] for item in metadata["inventory"]["files"]}
    assert {"README.md", "LICENSES.md", "README-FIRST.md", "README-FIRST.zh-CN.md"} <= paths
    assert "orgrebase/evidence/release-facts.json" in paths
    assert "orgrebase/benchmark/orgworkbench/v1/cases.json" in paths
    assert "orgrebase/benchmark/public-retail-quote/v1/sample.json" in paths
    assert not any("golden-competition" in item or "bpi-real-process" in item for item in paths)
    assert metadata["goldenPilotPublication"] == {"status": "NOT_INCLUDED_RUNTIME_PROFILE"}
    assert metadata["runtimeClosure"]["freshNativeExecution"] == "NOT_RUN_BY_PACKAGER"
    assert snapshot.verify_source_snapshot(org, oac, output, profile="runtime")["status"] == "PASS"
    with pytest.raises(snapshot.SnapshotError, match="METADATA_RELEASE_ALLOWLIST_INVALID"):
        snapshot.verify_source_snapshot(org, oac, output)
    second = tmp_path / "runtime-repeat"
    snapshot.build_source_snapshot(org, oac, second, profile="runtime")
    assert (output / snapshot.ARCHIVE_NAME).read_bytes() == (second / snapshot.ARCHIVE_NAME).read_bytes()


@pytest.mark.parametrize("name", (".env", ".env.local", "production.env"))
def test_evidence_allowlist_never_bypasses_environment_exclusion(name):
    assert snapshot._exclusion_reason(
        PurePosixPath("evidence/semifinal-closure/latest") / name, "orgrebase", is_dir=False,
    ) == "environment_file"


def test_runtime_closure_rejects_missing_hatch_resource(tmp_path):
    root = tmp_path / "snapshot"
    project = root / "orgrebase"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text('[tool.hatch.build.targets.wheel.force-include]\n"missing/input.json"="package/input.json"\n')
    with pytest.raises(snapshot.SnapshotError, match="RUNTIME_INSTALL_RESOURCE_MISSING"):
        snapshot._verify_release_runtime_closure(root, profile="runtime")


@pytest.mark.parametrize("member", ("secret.txt", "child.whl"))
def test_nested_archives_are_scanned_without_disclosing_matches(member):
    import io
    secret = b"ghp_" + b"z" * 30
    content = secret
    if member.endswith(".whl"):
        nested = io.BytesIO()
        with zipfile.ZipFile(nested, "w") as archive:
            archive.writestr("private.txt", secret)
        content = nested.getvalue()
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as archive:
        archive.writestr(member, content)
    with pytest.raises(snapshot.SnapshotError, match="CREDENTIAL_PATTERN:github_token") as caught:
        snapshot._scan_nested_archive("vendor/package.whl", outer.getvalue())
    assert secret.decode() not in str(caught.value)


def test_git_scan_covers_deleted_secrets(tmp_path):
    repo = tmp_path / "git"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    git("init")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")
    secret = "ghp_" + "z" * 30
    path = repo / "deleted.txt"
    path.write_text(secret)
    git("add", ".")
    git("commit", "-m", "fixture")
    path.unlink()
    git("add", "-u")
    git("commit", "-m", "remove fixture")
    with pytest.raises(snapshot.SnapshotError, match="CREDENTIAL_PATTERN:github_token") as caught:
        snapshot._scan_git_history(repo)
    assert secret not in str(caught.value)


def test_runtime_profile_retains_exact_oac_public_source_support(tmp_path):
    from orgrebase.workspace.oac_wire import _OAC_PUBLIC_SOURCE_PATHS, build_oac_public_source_manifest

    org, oac = _source_roots(tmp_path)
    for relative in _OAC_PUBLIC_SOURCE_PATHS:
        path = oac / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
    for relative in snapshot.RUNTIME_OAC_SUPPORT_FILES:
        path = oac / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
    spec_document = oac / "specs/009-proof-carrying-evolution-minimum-profile/spec.md"
    spec_document.parent.mkdir(parents=True, exist_ok=True)
    spec_document.write_text("# Spec 009\n")
    unrelated = oac / "experiments/plan-verification-portability/private.json"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("{}\n")
    prepared = tmp_path / "runtime"
    snapshot._prepare_snapshot(org, oac, prepared, profile="runtime")
    manifest, _ = build_oac_public_source_manifest(prepared / "oac-spec")
    assert len(manifest["files"]) == len(_OAC_PUBLIC_SOURCE_PATHS)
    assert all(
        (prepared / "oac-spec" / relative).is_file()
        for relative in snapshot.RUNTIME_OAC_SUPPORT_FILES
    )
    assert (prepared / "oac-spec" / spec_document.relative_to(oac)).is_file()
    assert not (prepared / "oac-spec/experiments/plan-verification-portability/private.json").exists()


def test_runtime_oac_allowlist_can_prepare_and_approve_real_adaptation(tmp_path, monkeypatch):
    import sys
    import time

    from orgrebase.store import StateStore
    from orgrebase.workspace.oac_quote_adaptation import (
        OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
        OACQuoteAdaptationService,
    )
    from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack

    # Exercise the filtered source tree through its real CLI, never a fake mapper.
    copied_org = tmp_path / "orgrebase"
    snapshot._copy_component(ROOT, copied_org, "orgrebase", profile="runtime")
    copied_oac = tmp_path / "oac-spec"
    snapshot._copy_component(ROOT.parent / "oac-spec", copied_oac, "oac-spec", profile="runtime")
    monkeypatch.setenv("UV_PYTHON", sys._base_executable)
    monkeypatch.delenv("ORGREBASE_OAC_PYTHON", raising=False)
    runtime = load_enterprise_quote_pilot_pack(copied_org / "examples/enterprise-quote-pilot/evergreen")
    with StateStore(tmp_path / "prepare.sqlite") as store:
        service = OACQuoteAdaptationService(store=store, profile=runtime.profile, runtime=runtime,
                                          oac_root=copied_oac, golden_root=copied_org / "evidence/golden-competition/latest/pilot",
                                          execution_run_id="run:runtime-package-regression")
        prepared = service.prepare(command_id="runtime-package:prepare")
        assert prepared["status"] == "OWNER_REVIEW_PENDING"
        assert prepared["candidate_digest"].startswith("sha256:")
        time.sleep(4.1)
        approved = service.approve(actor_id=prepared["human_authority_ref"],
                                   candidate_digest=prepared["candidate_digest"], command_id="runtime-package:approve",
                                   owner_review_summary_digest=prepared["owner_review_summary"]["digest"],
                                   acknowledgements=OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS)
        assert approved["formation_parity_status"] == "PASS"


def test_documentation_source_is_included_without_generated_site_or_environment(tmp_path):
    org, oac = _source_roots(tmp_path)
    for name in snapshot.DOCUMENTATION_SOURCE_FILES:
        path = org / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n")
    for name in ("documentation/site/index.html", "documentation/.venv/private.txt",
                 "documentation/work-notes.md"):
        path = org / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not source delivery\n")
    target = tmp_path / "snapshot"
    snapshot._prepare_snapshot(org, oac, target, profile="runtime")
    result = snapshot._verify_documentation_source_closure(target)
    assert result["status"] == "PASS"
    assert result["siteBuild"] == "NOT_RUN_BY_SOURCE_PACKAGER"
    assert not (target / "orgrebase/documentation/site").exists()
    assert not (target / "orgrebase/documentation/.venv").exists()
    assert not (target / "orgrebase/documentation/work-notes.md").exists()
    (target / "orgrebase/documentation/uv.lock").unlink()
    with pytest.raises(snapshot.SnapshotError, match="DOCUMENTATION_SOURCE_CLOSURE_INCOMPLETE"):
        snapshot._verify_documentation_source_closure(target)


@pytest.mark.parametrize("path,example", [
    ("orgrebase/docs/DOCUMENTATION-SITE.md", b"/" + b"tmp/orgrebase-docs-v1"),
    ("orgrebase/docs/guide/publishing.en.md", b"/" + b"tmp/orgrebase-docs-new"),
    ("orgrebase/docs/guide/publishing.zh.md", b"/" + b"tmp/orgrebase-docs-new"),
])
def test_documentation_site_examples_have_only_exact_path_sentinels(path, example):
    violations, references, _ = snapshot._machine_local_path_scan(
        path, example,
    )
    assert violations == []
    assert all(item["classification"] == "SYNTHETIC_DOCUMENTATION_PLACEHOLDER" for item in references)
    violations, _, _ = snapshot._machine_local_path_scan(path, b"/" + b"tmp/private-customer-records")
    assert violations


def _revalidation_fixture(tmp_path):
    import hashlib

    root = tmp_path / "source-snapshot"
    project = root / "orgrebase"
    bundle = project / snapshot.ARCHIVE_REVALIDATION_PREFIX
    implementation = project / "src/example.py"
    input_path = project / "benchmark/orgworkbench/dataset-manifest.json"
    for path, content in ((implementation, b"VALUE = 1\n"), (input_path, b"{}\n")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    lanes = {}
    for lane in ("bpi", "formation", "skill", "owb"):
        report = bundle / lane / "verification.json"
        report.parent.mkdir(parents=True)
        report.write_text('{"status":"PASS","failures":[]}')
        lanes[lane] = {"original_archive": "retained:" + lane,
                       "inputs": {input_path.relative_to(project).as_posix(): hashlib.sha256(input_path.read_bytes()).hexdigest()},
                       "files": {lane + "/verification.json": hashlib.sha256(report.read_bytes()).hexdigest()}}
    manifest = {"schema_version": "orgrebase.archive-revalidation.v1", "checked_at": "2026-09-18T00:00:00Z",
                "live_model_calls": 0, "implementation_files": {"src/example.py": hashlib.sha256(implementation.read_bytes()).hexdigest()},
                "lanes": lanes}
    manifest["digest"] = "sha256:" + hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                                               separators=(",", ":")).encode()).hexdigest()
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    return root, project, bundle, implementation, input_path


def test_revalidation_closure_binds_sources_inputs_and_results(tmp_path):
    root, _, _, _, _ = _revalidation_fixture(tmp_path)
    result = snapshot._verify_archive_revalidation_closure(root)
    assert result["status"] == "PASS"
    assert result["inputFiles"] == 1 and result["implementationFiles"] == 1
    assert result["outputFiles"] == 5 and result["liveModelCalls"] == 0
    assert result["currentBusinessRun"] is False


@pytest.mark.parametrize("mutation", ("source", "input", "output", "unlisted", "manifest"))
def test_revalidation_closure_rejects_drift_and_unlisted_output(tmp_path, mutation):
    root, _, bundle, source, input_path = _revalidation_fixture(tmp_path)
    target = {"source": source, "input": input_path, "output": bundle / "skill/verification.json",
              "unlisted": bundle / "private-note.txt", "manifest": bundle / "manifest.json"}[mutation]
    if mutation == "manifest":
        value = json.loads(target.read_text())
        value["live_model_calls"] = 1
        target.write_text(json.dumps(value))
    else:
        target.write_text("changed")
    with pytest.raises(snapshot.SnapshotError, match="ARCHIVE_REVALIDATION"):
        snapshot._verify_archive_revalidation_closure(root)


def test_runtime_revalidation_inputs_do_not_include_entire_old_archives(tmp_path):
    org, oac = _source_roots(tmp_path)
    for name in snapshot.RUNTIME_REVALIDATION_SUPPORT_FILES:
        path = org / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    unrelated = org / "evidence/semifinal-closure/latest/operations/private-note.json"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("private")
    target = tmp_path / "copied"
    snapshot._prepare_snapshot(org, oac, target, profile="runtime")
    assert all((target / "orgrebase" / name).is_file() for name in snapshot.RUNTIME_REVALIDATION_SUPPORT_FILES)
    assert not (target / "orgrebase" / unrelated.relative_to(org)).exists()
    assert snapshot._exclusion_reason(PurePosixPath(snapshot.ARCHIVE_REVALIDATION_PREFIX + "/runtime.sqlite"),
                                      "orgrebase", is_dir=False, profile="runtime") == "private_revalidation_runtime_database"
