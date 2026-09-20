from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.skill_rollback import (
    ROLLBACK_AUTHORITY,
    SkillPredecessorRollbackExecutor,
    load_frozen_predecessor,
    verify_rollback_execution_receipt,
)
from scripts.run_skill_predecessor_rollback import run_skill_predecessor_rollback
from scripts.verify_skill_predecessor_rollback import verify

ROOT = Path(__file__).resolve().parents[2]
FIXED_TIME = "2026-08-27T00:00:00Z"
RUN_ID = "run:test:skill-predecessor-rollback"
TASK_ID = "task:test:quote"
DELEGATION_ID = "delegation:test:quote"


def _record(value: dict[str, object]) -> dict[str, object]:
    result = deepcopy(value)
    result["digest"] = sha256_digest(value)
    return result


def _current_evidence(predecessor_digest: str) -> dict[str, dict[str, object]]:
    manifest = json.loads(
        (ROOT / "skills" / "enterprise-quote-compose" / "package.json").read_text(encoding="utf-8")
    )
    manifest["release_artifact"]["predecessor_package_digest"] = predecessor_digest
    manifest["manifest_digest"] = sha256_digest(
        {key: value for key, value in manifest.items() if key != "manifest_digest"}
    )
    tool_result: dict[str, object] = {
        "schema_version": "orgrebase.controlled-dependency-result.v1",
        "run_id": RUN_ID,
        "task_id": TASK_ID,
        "target_id": "work:enterprise-quote",
        "target_writes": 0,
    }
    tool_receipt = _record(
        {
            "schema_version": "orgrebase.controlled-http-receipt.v1",
            "run_id": RUN_ID,
            "status": "SUCCEEDED",
            "response_digest": sha256_digest(tool_result),
            "target_writes": 0,
        }
    )
    domain_results = {
        domain: sha256_digest({"domain": domain, "fixture": "rollback"})
        for domain in ("product", "legal", "finance", "gtm")
    }
    current_input: dict[str, object] = {
        "skill_partition": "replay",
        "candidate_program_digest_required": manifest["program_content_digest"],
        "dependency_tool_receipt_digest": tool_receipt["digest"],
        "dependency_result_digest": sha256_digest(tool_result),
        "coalition_result_binding_digest": sha256_digest(
            {"coalition": "rollback", "domain_results": domain_results}
        ),
        "domain_result_digests": domain_results,
    }
    current_result: dict[str, object] = {
        "schema_version": "orgrebase.skill-result-candidate.v1",
        "action": "APPLY_QUOTE",
        "package_digest": manifest["manifest_digest"],
        "candidate_only": True,
        "target_writes": 0,
    }
    current_invocation = _record(
        {
            "schema_version": "orgrebase.skill-invocation-receipt.v1",
            "run_id": RUN_ID,
            "task_id": TASK_ID,
            "delegation_id": DELEGATION_ID,
            "package_digest": manifest["manifest_digest"],
            "manifest_digest": manifest["manifest_digest"],
            "program_digest": manifest["program_content_digest"],
            "input_digest": sha256_digest(current_input),
            "output_digest": sha256_digest(current_result),
            "candidate_only": True,
            "target_writes": 0,
        }
    )
    return {
        "current_manifest": manifest,
        "current_invocation_receipt": current_invocation,
        "current_input": current_input,
        "current_result": current_result,
        "tool_receipt": tool_receipt,
        "tool_result": tool_result,
    }


def _request(package_digest: str) -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "actor_id": ROLLBACK_AUTHORITY,
        "idempotency_key": f"rollback:{RUN_ID}",
        **_current_evidence(package_digest),
        "reason_codes": ("CANARY_REGRESSION",),
        "created_at": FIXED_TIME,
    }


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_frozen_predecessor_is_exact_retained_wheel_extraction() -> None:
    package = load_frozen_predecessor(checkout_root=ROOT, verify_retained_wheel=True)
    assert package.package_digest == (
        "sha256:91398536ad1308de5438a7bf3817ecbd0dcb474abbe929e49100a8452b78540a"
    )
    assert package.program["digest"] == (
        "sha256:dacf97f2b5d788ad2e963ace2e19466f0e7454f6f572d6cb3abbfa93712bab0d"
    )
    assert package.contract["content_digest"] == (
        "sha256:71f41593a3c35b42efdec3e1d1f1be8fa4f7c5cb144e019af5442448d74744d0"
    )
    assert package.raw_digests == {
        "contract": "sha256:5560a74bfc45d0c3ea2e8a2bd732da5890c789faf5a994a99a7de68ec889c404",
        "input_schema": "sha256:240dfb496be710a0a1b711e9874f5f70d9b41958b3f943824157f6e701c68b7f",
        "manifest": "sha256:4ccf4872a856c6b95daca3d809e55417b49fef4c7172d9656b7adbc13dc4d1a4",
        "output_schema": "sha256:d4e0d470c3f2d8c8fee33b27d5574889e0bc1f1a80ea721ccf1bf83829e02470",
        "program": "sha256:d39dc2e739b1f9de4fd2123f6d98021b93ee90579be16ced035f31d590b23f19",
        "skill": "sha256:4d32fbfd94a42025e0ed7b4284d2d46bd4a9a9e030be5d5be08db10736fbfa2b",
    }


def test_authority_executes_direct_predecessor_and_replay_is_idempotent() -> None:
    package = load_frozen_predecessor(checkout_root=ROOT)
    executor = SkillPredecessorRollbackExecutor(package)
    request = _request(package.package_digest)
    first = executor.execute(**request)
    replay = executor.execute(**{**request, "created_at": "2099-01-01T00:00:00Z"})

    assert first.idempotent_replay is False
    assert replay.idempotent_replay is True
    assert replay.receipt == first.receipt
    assert len(executor.history) == 1
    assert first.result["action"] == "APPLY_QUOTE"
    assert first.result["package_digest"] == package.package_digest
    assert first.result["candidate_only"] is True
    assert first.result["target_writes"] == 0
    assert first.receipt["lineage_status"] == "DIRECT_DECLARED_PREDECESSOR"
    assert first.receipt["restoration_status"] == "EXECUTED_AND_INVOKED"
    verify_rollback_execution_receipt(first.receipt, package)

    schema = json.loads(
        (ROOT / "schemas" / "workspace-skill-rollback-execution-receipt.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(first.receipt) == set(schema["properties"])
    assert set(schema["required"]) <= set(first.receipt)


@pytest.mark.parametrize(
    ("mutation", "error"),
    (
        ("authority", "SKILL_PREDECESSOR_ROLLBACK_AUTHORITY_DENIED"),
        ("lineage", "SKILL_PREDECESSOR_DIRECT_LINEAGE_MISMATCH"),
        ("tool_result", "SKILL_ROLLBACK_TOOL_BINDING_INVALID"),
        ("current_receipt", "SKILL_CURRENT_INVOCATION_RECEIPT_DIGEST_MISMATCH"),
    ),
)
def test_execution_fails_closed_on_authority_lineage_or_binding_tamper(mutation: str, error: str) -> None:
    package = load_frozen_predecessor(checkout_root=ROOT)
    request = _request(package.package_digest)
    if mutation == "authority":
        request["actor_id"] = "agent:skill-curator"
    elif mutation == "lineage":
        request["current_manifest"]["release_artifact"]["predecessor_package_digest"] = "sha256:" + "0" * 64
    elif mutation == "tool_result":
        request["tool_result"]["target_id"] = "work:tampered"
    else:
        request["current_invocation_receipt"]["target_writes"] = 1

    with pytest.raises(IntegrityError, match=error):
        SkillPredecessorRollbackExecutor(package).execute(**request)


def test_idempotency_key_rejects_a_different_rollback_binding() -> None:
    package = load_frozen_predecessor(checkout_root=ROOT)
    executor = SkillPredecessorRollbackExecutor(package)
    request = _request(package.package_digest)
    executor.execute(**request)
    with pytest.raises(IntegrityError, match="SKILL_PREDECESSOR_ROLLBACK_IDEMPOTENCY_CONFLICT"):
        executor.execute(**{**request, "reason_codes": ("DIFFERENT_REASON",)})
    assert len(executor.history) == 1


def test_loader_rejects_byte_tamper(tmp_path: Path) -> None:
    source = ROOT / "skills" / "predecessors" / "enterprise-quote-compose" / "1.3.0"
    target = tmp_path / "predecessor"
    shutil.copytree(source, target)
    (target / "program.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(IntegrityError, match="SKILL_PREDECESSOR_RESOURCE_DIGEST_MISMATCH"):
        load_frozen_predecessor(target, checkout_root=ROOT, verify_retained_wheel=False)


def test_receipt_tamper_is_rejected() -> None:
    package = load_frozen_predecessor(checkout_root=ROOT)
    execution = SkillPredecessorRollbackExecutor(package).execute(**_request(package.package_digest))
    tampered = deepcopy(execution.receipt)
    tampered["target_writes"] = 1
    with pytest.raises(IntegrityError, match="SKILL_PREDECESSOR_ROLLBACK_RECEIPT_DIGEST_MISMATCH"):
        verify_rollback_execution_receipt(tampered, package)


def test_runner_and_independent_verifier_recompute_pack(tmp_path: Path) -> None:
    package = load_frozen_predecessor(checkout_root=ROOT)
    evidence = _current_evidence(package.package_digest)
    semifinal = tmp_path / "semifinal"
    _write(semifinal / "operations" / "tool" / "receipt.json", evidence["tool_receipt"])
    _write(semifinal / "operations" / "tool" / "result.json", evidence["tool_result"])
    _write(
        semifinal / "skills" / "quote-compose" / "invocation-receipt.json",
        evidence["current_invocation_receipt"],
    )
    _write(
        semifinal / "skills" / "quote-compose" / "input.json",
        evidence["current_input"],
    )
    _write(
        semifinal / "skills" / "quote-compose" / "result.json",
        evidence["current_result"],
    )
    current_manifest = tmp_path / "current-package.json"
    _write(current_manifest, evidence["current_manifest"])
    output = tmp_path / "rollback-pack"
    summary = run_skill_predecessor_rollback(
        run_id=RUN_ID,
        semifinal_root=semifinal,
        current_manifest_path=current_manifest,
        predecessor_root=package.root,
        output=output,
        created_at=FIXED_TIME,
    )
    assert summary["status"] == "PASS"
    verification = verify(
        output,
        checkout_root=ROOT,
        semifinal_root=semifinal,
        current_manifest_path=current_manifest,
        predecessor_root=package.root,
    )
    assert verification == {
        "schema_version": "orgrebase.skill-predecessor-rollback-verification.v1",
        "status": "PASS",
        "failure_count": 0,
        "failures": [],
    }

    first_receipt = json.loads((output / "rollback-execution-receipt.json").read_text(encoding="utf-8"))
    resumed_summary = run_skill_predecessor_rollback(
        run_id=RUN_ID,
        semifinal_root=semifinal,
        current_manifest_path=current_manifest,
        predecessor_root=package.root,
        output=output,
        created_at="2099-01-01T00:00:00Z",
    )
    resumed_receipt = json.loads((output / "rollback-execution-receipt.json").read_text(encoding="utf-8"))
    assert resumed_summary["execution_receipt_digest"] == first_receipt["digest"]
    assert resumed_receipt == first_receipt
    assert (
        json.loads((output / "idempotency-proof.json").read_text(encoding="utf-8"))[
            "preexisting_ledger_event_count"
        ]
        == 1
    )
    assert (
        verify(
            output,
            checkout_root=ROOT,
            semifinal_root=semifinal,
            current_manifest_path=current_manifest,
            predecessor_root=package.root,
        )["status"]
        == "PASS"
    )

    result_path = output / "predecessor-result.json"
    tampered = json.loads(result_path.read_text(encoding="utf-8"))
    tampered["target_writes"] = 1
    _write(result_path, tampered)
    assert (
        verify(
            output,
            checkout_root=ROOT,
            semifinal_root=semifinal,
            current_manifest_path=current_manifest,
            predecessor_root=package.root,
        )["status"]
        == "FAIL"
    )


def test_interrupted_publication_retains_stage_and_requires_scoped_recovery(tmp_path, monkeypatch):
    from scripts import run_skill_predecessor_rollback as runner
    package = load_frozen_predecessor(checkout_root=ROOT)
    evidence = _current_evidence(package.package_digest)
    semifinal = tmp_path / "semifinal"
    for relative, key in {
        "operations/tool/receipt.json": "tool_receipt",
        "operations/tool/result.json": "tool_result",
        "skills/quote-compose/invocation-receipt.json": "current_invocation_receipt",
        "skills/quote-compose/input.json": "current_input",
        "skills/quote-compose/result.json": "current_result",
    }.items():
        _write(semifinal / relative, evidence[key])
    manifest = tmp_path / "current-package.json"
    _write(manifest, evidence["current_manifest"])
    output = tmp_path / "rollback-pack"
    args = dict(run_id=RUN_ID, semifinal_root=semifinal, current_manifest_path=manifest,
                predecessor_root=package.root, output=output, created_at=FIXED_TIME)
    assert run_skill_predecessor_rollback(**args)["status"] == "PASS"
    original = runner.os.replace

    def fail_second_rename(source, target):
        if Path(target) == output:
            raise OSError("INJECTED_PUBLICATION_RENAME_FAILURE")
        return original(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(runner.os, "replace", fail_second_rename)
        with pytest.raises(OSError, match="INJECTED_PUBLICATION_RENAME_FAILURE"):
            run_skill_predecessor_rollback(**args)
    assert not output.exists()
    stages = list(tmp_path.glob(".rollback-pack.stage-*"))
    assert len(stages) == 1 and (stages[0] / "ledger.json").is_file()
    archive = list((tmp_path / "archive").glob("pack-*"))
    assert len(archive) == 1 and (archive[0] / "ledger.json").is_file()
    retained = {str(path): path.read_bytes() for root in (*stages, *archive) for path in root.rglob("*") if path.is_file()}
    with pytest.raises(IntegrityError, match="PUBLICATION_RECOVERY_REQUIRED"):
        run_skill_predecessor_rollback(**args)
    assert not output.exists()
    assert retained == {path: Path(path).read_bytes() for path in retained}
    # Shared parent/archive paths do not make a different publication incomplete.
    assert run_skill_predecessor_rollback(**{**args, "output": tmp_path / "other-pack"})["status"] == "PASS"
