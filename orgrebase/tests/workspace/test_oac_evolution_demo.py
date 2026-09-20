from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from orgrebase.cli import main
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.evolution_demo import (
    EXPECTED_ARTIFACT_PATHS,
    EXPECTED_JSON_PATHS,
    _export_pack,
    run_oac_evolution_demo,
)
from orgrebase.workspace.oac_wire import DEFAULT_POLICY_PATH, OACBlackBoxCLI


def _policy_for_current_public_source(path: Path) -> Path:
    capsule = OACBlackBoxCLI().build_capsule("BASE")
    policy = json.loads(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    policy["allowed_oac_source_fingerprints"] = {capsule.oac_cli_version: capsule.oac_source_fingerprint}
    selected = path / "runtime-admission-policy.json"
    selected.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return selected


def test_one_command_evolution_demo_exports_exact_closed_pack(tmp_path: Path) -> None:
    output = tmp_path / "evidence"
    policy = _policy_for_current_public_source(tmp_path)

    result = run_oac_evolution_demo(output, policy_path=policy)

    assert result["status"] == "PASS"
    assert result["maturity"] == "SYNTHETIC_CONTROLLED_REFERENCE_MVP"
    assert result["runs"] == {
        "base-accepted": {
            "plan_status": "ACCEPT",
            "execution_status": "COMPLETED",
            "outcome_verdict": "ACCEPT",
        },
        "split-accepted": {
            "plan_status": "ACCEPT",
            "execution_status": "COMPLETED",
            "outcome_verdict": "ACCEPT",
        },
        "split-rejected": {
            "plan_status": "ACCEPT",
            "execution_status": "COMPLETED",
            "outcome_verdict": "REJECT",
        },
    }
    assert result["boundaries"] == {
        "data": "SYNTHETIC_FIXTURE",
        "assurance": "SYNTHETIC_CONTROLLED_PROCESS_ASSURANCE_ONLY",
        "observation": "SYNTHETIC_CONTROLLED_OBSERVATION_NOT_EXTERNAL_GROUND_TRUTH",
        "runtime_evidence": "ZERO_EFFECT_HANDLER_COMPLETION_NOT_BUSINESS_TRUTH",
        "demand_admission": ("NO_INDEPENDENT_KIND_SCHEMA_VALIDATED_AND_EXECUTION_APPROVAL_BOUND"),
        "source_admission_authority": "human:veracier-shadow-owner",
        "authority_assurance": "DECLARED_NOT_AUTHENTICATED",
        "human_review": "NOT_RUN",
        "real_enterprise": "NOT_RUN",
        "external_effects": "NONE",
        "target_writes": 0,
        "production_ready": False,
    }
    assert result["evolution"]["governance_mode"] == "SCRIPTED_GOVERNANCE_IDENTITY"
    assert result["evolution"]["human_review"] == "NOT_RUN"
    assert result["evolution"]["promotion"] == "r1_TO_r2"
    assert result["evolution"]["rollback"] == "r2_TO_r1"
    assert result["evolution"]["active_pointer"] == "r1"
    assert result["event_chain"]["events"] == 13
    assert result["restart"]["idempotent_execution_replay"] == "PASS"
    assert result["restart"]["events_before_close"] == 5
    assert result["restart"]["events_after_replay"] == 5
    assert result["restart"]["replay_event_delta"] == 0
    assert result["restart"]["replay_artifact_delta"] == 0
    assert result["restart"]["artifacts_before_close"] == result["restart"]["artifacts_after_replay"]
    assert result["restart"]["event_head_unchanged"] is True
    assert len(result["restart"]["execution_receipt_digests_after_reopen"]) == 3
    regression = result["preliminary_regression"]
    assert regression["schema_version"] == "orgrebase.preliminary-quote-regression.v2"
    assert regression["status"] == "PASS"
    assert regression["actual_quote_digest"] == regression["final_quote"]["digest"]
    assert regression["actual_quote_semantics"] == regression["expected_quote_semantics"]
    assert regression["actual_quote_semantics"]["currency"] == "EUR"
    assert regression["actual_quote_semantics"]["launch_date"] == "2026-09-15"
    source = json.loads((output / "source" / "source-admission-oac.json").read_text(encoding="utf-8"))
    assert source["spec"]["decisionAuthorityRef"]["resourceId"] == ("human:veracier-shadow-owner")
    rejected_execution = json.loads(
        (output / "runs" / "split-rejected" / "execution-receipt.json").read_text(encoding="utf-8")
    )
    rejected_evidence = json.loads(
        (output / "runs" / "split-rejected" / "evidence.json").read_text(encoding="utf-8")
    )
    rejected_observation = json.loads(
        (output / "runs" / "split-rejected" / "outcome-observation.json").read_text(encoding="utf-8")
    )
    assert rejected_execution["source_admission_digest"] == source["digest"]
    assert {item["observed_value"] for item in rejected_evidence} == {"zero_effect_handler_completed"}
    assert "qualification_record_expired" not in json.dumps(rejected_evidence)
    assert {fact["name"]: fact["value"] for fact in rejected_observation["facts"]}[
        "qualification-evidence-check"
    ] == "qualification_record_expired"
    assert rejected_observation["producer_id"] != rejected_execution["executor_id"]
    assert rejected_observation["producer_authority_ref"] != rejected_execution["runtime_owner_id"]
    event_chain = json.loads((output / "event-chain.json").read_text(encoding="utf-8"))
    assert [record["event_type"] for record in event_chain["records"]][5:11] == [
        "OAC_CONTROLLED_OBSERVATION_RECORDED",
        "OAC_CONTROLLED_OUTCOME_ISSUED",
    ] * 3
    pointer = json.loads((output / "evolution" / "pointer-transition.json").read_text(encoding="utf-8"))
    predecessor = json.loads((output / "evolution" / "source-predecessor.json").read_text(encoding="utf-8"))
    successor = json.loads((output / "evolution" / "source-successor.json").read_text(encoding="utf-8"))
    assert pointer["after_promotion"]["version"] == "r2"
    assert pointer["active_object_after_promotion"]["state"] == "CURRENT"
    assert (
        pointer["active_object_after_promotion"]["payload"]["source_revision"]["digest"]
        == successor["digest"]
    )
    assert pointer["after_rollback"]["version"] == "r1"
    assert pointer["active_object_after_rollback"]["state"] == "CURRENT"
    assert (
        pointer["active_object_after_rollback"]["payload"]["source_revision"]["digest"]
        == predecessor["digest"]
    )
    assert pointer["history_retained"] == {"r1": True, "r2": True}

    actual_paths = {path.relative_to(output).as_posix() for path in output.rglob("*.json") if path.is_file()}
    assert actual_paths == EXPECTED_JSON_PATHS
    index = json.loads((output / "evidence-index.json").read_text(encoding="utf-8"))
    assert tuple(item["artifact_ref"] for item in index["entries"]) == EXPECTED_ARTIFACT_PATHS
    for entry in index["entries"]:
        actual = "sha256:" + hashlib.sha256((output / entry["artifact_ref"]).read_bytes()).hexdigest()
        assert actual == entry["sha256"]
    assert index["pack_digest"] == sha256_digest(index["entries"])

    first_pack_digest = index["pack_digest"]
    replayed = run_oac_evolution_demo(output, policy_path=policy)
    assert replayed["evidence_index"]["pack_digest"] == first_pack_digest
    from scripts.verify_oac_evolution_evidence import _quote_regression_ok
    assert _quote_regression_ok(result)
    for field, wrong_value in (("currency", "JPY"), ("notice_required", 1)):
        altered = deepcopy(result)
        changed = altered["preliminary_regression"]
        changed["expected_quote_semantics"][field] = wrong_value
        changed["actual_quote_semantics"][field] = wrong_value
        changed["final_quote"]["payload"][field] = wrong_value
        changed["final_quote"]["digest"] = sha256_digest({key: value for key, value in changed["final_quote"].items() if key not in {"digest", "state"}})
        changed["actual_quote_digest"] = changed["final_quote"]["digest"]
        changed["digest"] = sha256_digest({key: value for key, value in changed.items() if key != "digest"})
        assert not _quote_regression_ok(altered)


def test_cli_dispatches_evolution_demo_with_explicit_paths(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    output = tmp_path / "pack"
    policy = tmp_path / "policy.json"
    oac_root = tmp_path / "oac-spec"
    observed = {}

    def fake_run(
        selected_output: Path,
        *,
        oac_root: Path | None,
        policy_path: Path,
    ) -> dict[str, object]:
        observed.update(
            output=selected_output,
            oac_root=oac_root,
            policy_path=policy_path,
        )
        return {"status": "PASS", "maturity": "SYNTHETIC_CONTROLLED_REFERENCE_MVP"}

    monkeypatch.setattr("orgrebase.workspace.evolution_demo.run_oac_evolution_demo", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "orgrebase",
            "workspace-oac-evolution-demo",
            "--output-dir",
            str(output),
            "--oac-root",
            str(oac_root),
            "--policy",
            str(policy),
        ],
    )

    main()

    assert observed == {"output": output, "oac_root": oac_root, "policy_path": policy}
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"


def test_evidence_export_rejects_managed_path_symlink(tmp_path: Path) -> None:
    output = tmp_path / "pack"
    output.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"authority":"outside"}\n', encoding="utf-8")
    (output / "summary.json").symlink_to(outside)

    with pytest.raises(IntegrityError, match="OAC_EVOLUTION_MANAGED_PATH_SYMLINK_FORBIDDEN"):
        _export_pack(output, {path: {} for path in EXPECTED_ARTIFACT_PATHS})

    assert outside.read_text(encoding="utf-8") == '{"authority":"outside"}\n'
