from __future__ import annotations

import copy
import json
import shutil
import tomllib
from pathlib import Path
from typing import Any

import pytest

from orgrebase.goai_agentteams import (
    EXPECTED_FRESH_CORE_EVIDENCE,
    EXPECTED_PACKAGE_PATHS,
    EXPECTED_PUBLIC_TRANSPORT_FIXTURE,
    EXPECTED_TEAM,
    FRESH_CORE_CLASSIFICATION,
    HISTORICAL_TRANSPORT_CLASSIFICATION,
    PRIVATE_RUNTIME_EVIDENCE_PATHS,
    REFERENCE_CLASSIFICATION,
    build_run_summary,
    load_agentteams_evidence,
    load_run_contract,
    verify_run_directory,
)
from scripts.generate_release_facts import build_facts

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "goai-agentteams-demo.json"
REQUEST = ROOT / "examples" / "agentteams" / "change-request.json"
SAMPLE_OUTPUT = ROOT / "examples" / "agentteams" / "run-summary.example.json"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _replace_path(document: dict[str, Any], path: str, value: object) -> None:
    target: dict[str, Any] = document
    parts = path.split(".")
    for part in parts[:-1]:
        nested = target[part]
        assert isinstance(nested, dict)
        target = nested
    target[parts[-1]] = value


def _copy_agentteams_evidence(tmp_path: Path) -> Path:
    project_root = tmp_path / "evidence-project"
    historical = project_root / EXPECTED_PUBLIC_TRANSPORT_FIXTURE
    historical.parent.mkdir(parents=True)
    shutil.copy2(ROOT / EXPECTED_PUBLIC_TRANSPORT_FIXTURE, historical)
    shutil.copytree(
        ROOT / EXPECTED_FRESH_CORE_EVIDENCE["bundle_dir"],
        project_root / EXPECTED_FRESH_CORE_EVIDENCE["bundle_dir"],
    )
    return project_root


def _fixture(
    tmp_path: Path,
) -> tuple[Path, Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    config, request = load_run_contract(CONFIG, REQUEST)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    placeholders = {
        "demo.json": {},
        "preview.json": {},
        "rebase-receipt.json": {},
        "conflict-receipt.json": {"status": "REJECTED_AUTHORITY_CONFLICT"},
        "failure-receipt.json": {
            "status": "REJECTED_BEFORE_WRITE",
            "error_code": "PREVIEW_EXPIRED",
            "target_writes": 0,
        },
        "rollback-receipt.json": {
            "status": "ROLLED_BACK_PENDING_REBASE",
            "metrics": {
                "authoritative_claims_changed": 0,
                "work_items_compensated": 3,
            },
        },
        "traces.otlp.json": {},
        "logs.otlp.json": {},
        "metrics.otlp.json": {},
        "manifest.json": {"schema_version": "orgrebase.evidence-manifest.v2"},
    }
    for name, value in placeholders.items():
        _write_json(output_dir / name, value)

    project_root = ROOT
    demo = {
        "collaboration": {
            "agent_runs": [{"agent_name": name} for name in EXPECTED_TEAM],
            "handoffs": [{"handoff": index} for index in range(5)],
            "tool_invocations": [
                {
                    "receipt": {
                        "status": "SUCCEEDED",
                        "tool_ref": "tool:dependency-evidence",
                        "digest": "sha256:" + "1" * 64,
                    }
                }
            ],
            "coordination_receipt": {
                "status": "PASS",
                "digest": "sha256:" + "2" * 64,
            },
            "task_graph": {
                "manager_workers": "Team Leader + four specialist Workers",
                "candidate_only": True,
                "plan_digest": "sha256:" + "3" * 64,
            },
        },
        "receipt": {
            "status": "COMPLETED",
            "approval_actor_id": "human:product-owner",
            "metrics": {
                "work_items_rebased": 3,
                "unauthorized_disclosures": 0,
                "false_invalidations": 0,
            },
            "qualification_report": {"candidate_state": "CANARY"},
        },
        "event_chain": ["preview", "approval", "apply"],
    }
    return output_dir, project_root, config, request, demo


def _built_summary(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    output_dir, project_root, config, request, demo = _fixture(tmp_path)
    summary = build_run_summary(
        demo=demo,
        output_dir=output_dir,
        config=config,
        request=request,
        project_root=project_root,
    )
    _write_json(output_dir / "run-summary.json", summary)
    return output_dir, summary


def test_contract_is_locked_to_truthful_reference_classification() -> None:
    config, request = load_run_contract(CONFIG, REQUEST)

    assert request["expected_team"] == list(EXPECTED_TEAM)
    assert config["execution_mode"] == "LOCAL_DETERMINISTIC_REFERENCE"
    assert config["run_classification"] == REFERENCE_CLASSIFICATION
    assert config["agentteams"]["historical_transport_fixture"] == EXPECTED_PUBLIC_TRANSPORT_FIXTURE
    assert config["agentteams"]["fresh_core_evidence"] == EXPECTED_FRESH_CORE_EVIDENCE
    configured_evidence = {
        config["agentteams"]["historical_transport_fixture"],
        *config["agentteams"]["fresh_core_evidence"].values(),
    }
    assert not configured_evidence & PRIVATE_RUNTIME_EVIDENCE_PATHS
    assert all((ROOT / relative).exists() for relative in configured_evidence)
    assert (ROOT / EXPECTED_FRESH_CORE_EVIDENCE["bundle_dir"]).is_dir()
    assert {key: config[key] for key in EXPECTED_PACKAGE_PATHS} == EXPECTED_PACKAGE_PATHS
    assert all((ROOT / config[key]).is_file() for key in ("sample_input", "sample_output"))
    for name in ("run-agentteams-demo.sh", "verify-agentteams-demo.sh"):
        entrypoint = ROOT / name
        assert entrypoint.is_file()
        assert entrypoint.stat().st_mode & 0o111
    assert (ROOT / "pyproject.toml").is_file()
    assert (ROOT / "uv.lock").is_file()


def test_sample_output_is_explicitly_illustrative_and_boundary_safe() -> None:
    sample = json.loads(SAMPLE_OUTPUT.read_text(encoding="utf-8"))

    assert sample["classification"] == "ILLUSTRATIVE_PROJECTION_NOT_RUNTIME_EVIDENCE"
    assert sample["run_classification"] == REFERENCE_CLASSIFICATION
    historical = sample["historical_agentteams_transport"]
    assert sample["frozen_live_agentteams_receipt"] == historical
    assert historical["status"] == "TRANSPORT_ONLY"
    assert historical["semantic_acceptance"] == "NOT_ESTABLISHED"
    assert historical["autonomous_collaboration"] is False
    assert historical["current_workspace_live"] == "NOT_RUN"


def test_sdist_includes_competition_entrypoints_and_excludes_partial_git_fixture() -> None:
    build = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["hatch"]["build"][
        "targets"
    ]["sdist"]

    assert "run-agentteams-demo.sh" in build["include"]
    assert "verify-agentteams-demo.sh" in build["include"]
    assert {
        "/evidence/goai-agentteams/latest/git-tool-repo",
        *(f"/{path}" for path in PRIVATE_RUNTIME_EVIDENCE_PATHS),
        "/evidence/agentteams/nonce-ledger.json",
        "/evidence/agentteams/.nonce-ledger.json.lock",
        "/evidence/agentteams/**/.nonce-ledger.json.lock",
        "/evidence/agentteams/live-sources",
        "/evidence/agentteams/**/live-sources",
        "/evidence/agentteams/private-sessions",
        "/evidence/agentteams/**/private-sessions",
        "/evidence/agentteams/debug",
        "/evidence/agentteams/**/debug",
        "/evidence/agentteams/debug-*",
        "/evidence/agentteams/**/debug-*",
        "/evidence/agentteams/**/*debug*",
        "/evidence/agentteams/dispatch-logs",
        "/evidence/agentteams/**/dispatch-logs",
        "/evidence/agentteams/dispatch-*",
        "/evidence/agentteams/**/dispatch-*",
    } <= set(build["exclude"])


@pytest.mark.parametrize(
    ("field", "promoted_value"),
    (
        ("semantic_acceptance", "PASS_LIVE"),
        ("autonomous_collaboration", True),
        ("current_workspace_live", "PASS"),
    ),
)
def test_contract_rejects_evidence_promotion(tmp_path: Path, field: str, promoted_value: object) -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config["run_classification"][field] = promoted_value
    config_path = tmp_path / "config.json"
    _write_json(config_path, config)

    with pytest.raises(ValueError, match="GOAI_RUN_CLASSIFICATION_MISMATCH"):
        load_run_contract(config_path, REQUEST)


@pytest.mark.parametrize(
    ("document", "path", "value", "expected_error"),
    (
        ("config", "schema_version", "unsupported", "GOAI_CONFIG_SCHEMA_UNSUPPORTED"),
        ("request", "schema_version", "unsupported", "GOAI_REQUEST_SCHEMA_UNSUPPORTED"),
        ("config", "profile", "other-scenario", "GOAI_SCENARIO_UNSUPPORTED"),
        ("request", "organization_id", "org:other", "GOAI_ORGANIZATION_UNSUPPORTED"),
        ("request", "requested_by", "agent:untrusted", "GOAI_AUTHORITY_OR_PURPOSE_MISMATCH"),
        ("request", "change", {}, "GOAI_CHANGE_OUTSIDE_FROZEN_FIXTURE"),
        ("request", "expected_team", [], "GOAI_TEAM_ROSTER_MISMATCH"),
        ("config", "agentteams", None, "GOAI_AGENTTEAMS_CONFIG_MISSING"),
        ("config", "agentteams.version", "v0.0.0", "GOAI_AGENTTEAMS_SOURCE_LOCK_MISMATCH"),
        (
            "config",
            "agentteams.historical_transport_fixture",
            "evidence/untrusted.json",
            "GOAI_PUBLIC_TRANSPORT_FIXTURE_PATH_MISMATCH",
        ),
        (
            "config",
            "agentteams.fresh_core_evidence",
            {},
            "GOAI_FRESH_CORE_EVIDENCE_PATHS_MISMATCH",
        ),
        ("config", "required_outputs", [], "GOAI_REQUIRED_OUTPUTS_INVALID"),
        ("config", "entrypoint", "python demo.py", "GOAI_PACKAGE_PATHS_MISMATCH"),
    ),
)
def test_contract_rejects_unsupported_identity_scope_and_package_mutations(
    tmp_path: Path,
    document: str,
    path: str,
    value: object,
    expected_error: str,
) -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    request = json.loads(REQUEST.read_text(encoding="utf-8"))
    selected = config if document == "config" else request
    _replace_path(selected, path, value)
    config_path = tmp_path / "config.json"
    request_path = tmp_path / "request.json"
    _write_json(config_path, config)
    _write_json(request_path, request)

    with pytest.raises(ValueError, match=expected_error):
        load_run_contract(config_path, request_path)


@pytest.mark.parametrize(
    ("content", "expected_error"),
    (("not-json", "INVALID_JSON"), ("[]", "JSON_OBJECT_REQUIRED")),
)
def test_contract_rejects_malformed_or_non_object_configuration(
    tmp_path: Path,
    content: str,
    expected_error: str,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=expected_error):
        load_run_contract(config_path, REQUEST)


def test_summary_keeps_historical_live_source_transport_only(tmp_path: Path) -> None:
    _, summary = _built_summary(tmp_path)

    assert summary["run_classification"] == REFERENCE_CLASSIFICATION
    historical = summary["historical_agentteams_transport"]
    assert summary["frozen_live_agentteams_receipt"] == historical
    assert {
        key: historical[key] for key in HISTORICAL_TRANSPORT_CLASSIFICATION
    } == HISTORICAL_TRANSPORT_CLASSIFICATION
    assert historical["status"] == "TRANSPORT_ONLY"
    assert "source_receipt_status" not in historical
    assert "source_evidence_class" not in historical
    assert historical["semantic_acceptance"] == "NOT_ESTABLISHED"
    assert historical["autonomous_collaboration"] is False
    assert historical["current_workspace_live"] == "NOT_RUN"
    assert historical["fixture_path"] == EXPECTED_PUBLIC_TRANSPORT_FIXTURE
    assert historical["receipt_path"] == EXPECTED_PUBLIC_TRANSPORT_FIXTURE


def test_public_historical_fixture_is_allowlisted_and_transport_only() -> None:
    config, _ = load_run_contract(CONFIG, REQUEST)
    historical, fresh = load_agentteams_evidence(config=config, project_root=ROOT)

    assert {
        key: historical[key] for key in HISTORICAL_TRANSPORT_CLASSIFICATION
    } == HISTORICAL_TRANSPORT_CLASSIFICATION
    assert historical["sanitization"] == {
        "contains_raw_runtime_sources": False,
        "contains_credentials": False,
        "contains_provider_request_ids": False,
        "contains_matrix_event_ids": False,
        "contains_prompts_or_outputs": False,
    }
    fixture = json.loads((ROOT / EXPECTED_PUBLIC_TRANSPORT_FIXTURE).read_text(encoding="utf-8"))
    assert "evidence" not in fixture
    assert "run_id" not in fixture
    assert "nonce" not in fixture
    assert "model_calls" not in fixture
    assert "matrix" not in fixture
    assert {key: fresh[key] for key in FRESH_CORE_CLASSIFICATION} == FRESH_CORE_CLASSIFICATION


def test_public_historical_fixture_rejects_unallowlisted_fields(tmp_path: Path) -> None:
    config, _ = load_run_contract(CONFIG, REQUEST)
    project_root = _copy_agentteams_evidence(tmp_path)
    fixture_path = project_root / EXPECTED_PUBLIC_TRANSPORT_FIXTURE
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture["raw_runtime_payload"] = {"must": "not ship"}
    _write_json(fixture_path, fixture)

    with pytest.raises(ValueError, match="GOAI_PUBLIC_TRANSPORT_FIXTURE_FIELDS_MISMATCH"):
        load_agentteams_evidence(config=config, project_root=project_root)


def test_fresh_core_bundle_rejects_checksum_tamper(tmp_path: Path) -> None:
    config, _ = load_run_contract(CONFIG, REQUEST)
    project_root = _copy_agentteams_evidence(tmp_path)
    summary_path = project_root / EXPECTED_FRESH_CORE_EVIDENCE["run_summary"]
    _write_json(summary_path, {"tampered": True})

    with pytest.raises(ValueError, match="GOAI_FRESH_CORE_CHECKSUM_MISMATCH"):
        load_agentteams_evidence(config=config, project_root=project_root)


@pytest.mark.parametrize(
    ("attack", "expected_error"),
    (
        ("invalid-line", "GOAI_FRESH_CORE_CHECKSUM_LINE_INVALID"),
        ("unsafe-path", "GOAI_FRESH_CORE_CHECKSUM_PATH_INVALID"),
        ("missing-artifact", "GOAI_FRESH_CORE_CHECKSUM_ARTIFACT_MISSING"),
        ("empty-manifest", "GOAI_FRESH_CORE_CHECKSUM_COVERAGE_MISSING"),
        ("unlisted-artifact", "GOAI_FRESH_CORE_CHECKSUM_COVERAGE_MISMATCH"),
        ("symlink", "GOAI_FRESH_CORE_SYMLINK_FORBIDDEN"),
    ),
)
def test_fresh_core_bundle_rejects_manifest_and_path_attacks(
    tmp_path: Path,
    attack: str,
    expected_error: str,
) -> None:
    config, _ = load_run_contract(CONFIG, REQUEST)
    project_root = _copy_agentteams_evidence(tmp_path)
    bundle = project_root / EXPECTED_FRESH_CORE_EVIDENCE["bundle_dir"]
    manifest = bundle / "SHA256SUMS"
    if attack == "invalid-line":
        manifest.write_text("invalid\n", encoding="utf-8")
    elif attack == "unsafe-path":
        manifest.write_text(f"{'0' * 64}  ./../escape\n", encoding="utf-8")
    elif attack == "missing-artifact":
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + f"{'0' * 64}  ./missing.json\n",
            encoding="utf-8",
        )
    elif attack == "empty-manifest":
        manifest.write_text("", encoding="utf-8")
    elif attack == "unlisted-artifact":
        (bundle / "unlisted.json").write_text("{}\n", encoding="utf-8")
    else:
        (bundle / "linked-readme.md").symlink_to("README.md")

    with pytest.raises(ValueError, match=expected_error):
        load_agentteams_evidence(config=config, project_root=project_root)


def test_summary_indexes_fresh_live_core_without_promoting_workspace(
    tmp_path: Path,
) -> None:
    _, summary = _built_summary(tmp_path)

    fresh = summary["fresh_core_agentteams_evidence"]
    assert fresh["evidence_class"] == "LIVE_AGENTTEAMS"
    assert fresh["scope_evidence_class"] == "LIVE_AGENTTEAMS_CORE_PROPOSAL_PLANE"
    assert fresh["successful_provider_executions"] == 4
    assert fresh["candidate_artifacts"] == 4
    assert fresh["candidate_events"] == 4
    assert fresh["execution_transport"] == "DIRECT_OPENCLAW_GATEWAY_NOT_MATRIX_INBOUND"
    assert fresh["matrix_role"] == "IDENTITY_PUBLICATION_COLLECTION_NOT_INBOUND_DELEGATION"
    assert fresh["semantic_ingestion"]["advisory_accepted"] == 4
    assert fresh["semantic_ingestion"]["rejected"] == 0
    assert fresh["semantic_ingestion"]["target_writes"] == 0
    assert fresh["current_workspace_live"] == "NOT_RUN"
    assert fresh["oac_runtime_bridge"] == "NOT_USED_IN_THIS_RUN"


def test_verifier_accepts_reference_without_promoting_current_live(tmp_path: Path) -> None:
    output_dir, _ = _built_summary(tmp_path)

    result = verify_run_directory(output_dir)

    assert result["status"] == "PASS"
    assert result["failures"] == []


@pytest.mark.parametrize(
    ("section", "field", "promoted_value", "expected_failure"),
    (
        ("run_classification", "autonomous_collaboration", True, "RUN_CLASSIFICATION"),
        ("run_classification", "current_workspace_live", "PASS", "RUN_CLASSIFICATION"),
        (
            "frozen_live_agentteams_receipt",
            "status",
            "PASS",
            "HISTORICAL_TRANSPORT_BOUNDARY",
        ),
        (
            "frozen_live_agentteams_receipt",
            "semantic_acceptance",
            "PASS",
            "HISTORICAL_TRANSPORT_BOUNDARY",
        ),
        (
            "frozen_live_agentteams_receipt",
            "current_workspace_live",
            "PASS",
            "HISTORICAL_TRANSPORT_BOUNDARY",
        ),
        (
            "fresh_core_agentteams_evidence",
            "current_workspace_live",
            "PASS",
            "FRESH_CORE_EVIDENCE_BOUNDARY",
        ),
        (
            "fresh_core_agentteams_evidence",
            "oac_runtime_bridge",
            "IMPLEMENTED",
            "FRESH_CORE_EVIDENCE_BOUNDARY",
        ),
    ),
)
def test_verifier_rejects_status_promotion(
    tmp_path: Path,
    section: str,
    field: str,
    promoted_value: object,
    expected_failure: str,
) -> None:
    output_dir, summary = _built_summary(tmp_path)
    promoted = copy.deepcopy(summary)
    promoted[section][field] = promoted_value
    _write_json(output_dir / "run-summary.json", promoted)

    result = verify_run_directory(output_dir)

    assert result["status"] == "FAIL"
    assert expected_failure in result["failures"]


def test_verifier_rejects_tampered_reference_artifact(tmp_path: Path) -> None:
    output_dir, _ = _built_summary(tmp_path)
    _write_json(output_dir / "preview.json", {"tampered": True})

    result = verify_run_directory(output_dir)

    assert result["status"] == "FAIL"
    assert "DIGEST:preview.json" in result["failures"]


@pytest.mark.parametrize(
    ("mutations", "expected_failure"),
    (
        ((("schema_version", "unsupported"),), "SUMMARY_SCHEMA_OR_STATUS"),
        ((("agent_collaboration", None),), "COLLABORATION_MISSING"),
        ((("agent_collaboration.agent_count", 4),), "TEAM_ROSTER"),
        ((("agent_collaboration.structured_handoffs", 4),), "HANDOFF_OR_COORDINATION"),
        ((("tool_call", None),), "TOOL_CALL"),
        ((("output", None),), "OUTPUT_STATUS"),
        ((("exception_and_recovery", None),), "EXCEPTION_EVIDENCE"),
        (
            (("exception_and_recovery.expired_preview.status", "COMPLETED"),),
            "FAIL_CLOSED_PREVIEW",
        ),
        ((("exception_and_recovery.rollback.status", "COMPLETED"),), "ROLLBACK"),
        ((("frozen_live_agentteams_receipt.status", "PASS"),), "HISTORICAL_TRANSPORT_ALIAS_MISMATCH"),
        (
            (("historical_agentteams_transport", None), ("frozen_live_agentteams_receipt", None)),
            "HISTORICAL_TRANSPORT_MISSING",
        ),
        ((("historical_agentteams_transport.worker_count", 4),), "HISTORICAL_TRANSPORT_METADATA"),
        (
            (("historical_agentteams_transport.fixture_path", "private/runtime.json"),),
            "HISTORICAL_TRANSPORT_PUBLIC_FIXTURE",
        ),
        ((("fresh_core_agentteams_evidence", None),), "FRESH_CORE_EVIDENCE_MISSING"),
        (
            (("fresh_core_agentteams_evidence.execution_transport", "MATRIX_INBOUND"),),
            "FRESH_CORE_TRANSPORT_BOUNDARY",
        ),
        ((("fresh_core_agentteams_evidence.worker_count", 4),), "FRESH_CORE_EXECUTION_METADATA"),
        ((("fresh_core_agentteams_evidence.semantic_ingestion.status", "FAIL"),), "FRESH_CORE_SEMANTIC_INGESTION"),
        (
            (("fresh_core_agentteams_evidence.bundle_path", "evidence/untrusted"),),
            "FRESH_CORE_EVIDENCE_PATHS",
        ),
        ((("artifact_raw_sha256", {}),), "ARTIFACT_DIGESTS_MISSING"),
    ),
)
def test_verifier_fails_closed_for_missing_or_promoted_evidence(
    tmp_path: Path,
    mutations: tuple[tuple[str, object], ...],
    expected_failure: str,
) -> None:
    output_dir, summary = _built_summary(tmp_path)
    mutated = copy.deepcopy(summary)
    mutated["frozen_live_agentteams_receipt"] = copy.deepcopy(
        mutated["frozen_live_agentteams_receipt"]
    )
    for path, value in mutations:
        _replace_path(mutated, path, value)
    _write_json(output_dir / "run-summary.json", mutated)

    result = verify_run_directory(output_dir)

    assert result["status"] == "FAIL"
    assert expected_failure in result["failures"]


def test_verifier_accepts_legacy_historical_alias_without_promoting_it(tmp_path: Path) -> None:
    output_dir, summary = _built_summary(tmp_path)
    summary.pop("historical_agentteams_transport")
    _write_json(output_dir / "run-summary.json", summary)

    result = verify_run_directory(output_dir)

    assert result["status"] == "PASS"
    assert result["failures"] == []


def test_verifier_rejects_missing_committed_artifact(tmp_path: Path) -> None:
    output_dir, _ = _built_summary(tmp_path)
    (output_dir / "preview.json").unlink()

    result = verify_run_directory(output_dir)

    assert result["status"] == "FAIL"
    assert "MISSING:preview.json" in result["failures"]


@pytest.mark.parametrize("private_path", sorted(PRIVATE_RUNTIME_EVIDENCE_PATHS))
def test_contract_rejects_private_runtime_evidence_paths(tmp_path: Path, private_path: str) -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config["agentteams"]["historical_transport_fixture"] = private_path
    config_path = tmp_path / "config.json"
    _write_json(config_path, config)

    with pytest.raises(ValueError, match="GOAI_PRIVATE_RUNTIME_EVIDENCE_FORBIDDEN"):
        load_run_contract(config_path, REQUEST)


def test_release_facts_use_fresh_public_evidence_only() -> None:
    facts = build_facts(root=ROOT)

    historical = facts["historical_agentteams_transport"]
    fresh = facts["core_change_advisory_live_agentteams"]
    ingestion = facts["candidate_control_plane"]
    assert historical["status"] == "TRANSPORT_ONLY"
    assert historical["fixture_path"] == EXPECTED_PUBLIC_TRANSPORT_FIXTURE
    assert fresh["evidence_class"] == "LIVE_AGENTTEAMS"
    assert fresh["bundle_path"] == EXPECTED_FRESH_CORE_EVIDENCE["bundle_dir"]
    assert fresh["current_workspace_live"] == "NOT_RUN"
    assert fresh["oac_runtime_bridge"] == "NOT_USED_IN_THIS_RUN"
    assert ingestion["advisory_accepted"] == 4
    assert ingestion["rejected"] == 0
    assert ingestion["target_writes"] == 0
    serialized = json.dumps(facts, ensure_ascii=False)
    assert not any(path in serialized for path in PRIVATE_RUNTIME_EVIDENCE_PATHS)
