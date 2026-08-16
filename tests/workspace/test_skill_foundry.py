from __future__ import annotations

import json

import pytest

from orgrebase.digest import canonical_json
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.formation import seed_workspace_store
from orgrebase.workspace.skill_foundry import (
    GovernedSkillEvaluator,
    SKILL_PROGRAM_MEDIA_TYPE,
    SkillCurator,
    SkillFoundryService,
)


def test_exact_candidate_skill_passes_all_partitions() -> None:
    store = StateStore(":memory:")
    try:
        seed_workspace_store(store)
        result = SkillFoundryService(store).run()
        receipt = result["evaluation"]
        assert result["status"] == "CANARY"
        assert len(receipt.case_results) == 16
        assert all(item.passed for item in receipt.case_results)
        assert all(item.passed for item in receipt.gate_results)
        assert result["program"].digest == result["candidate"].candidate_program_digest
    finally:
        store.close()


def test_mutated_candidate_program_bytes_are_rejected() -> None:
    store = StateStore(":memory:")
    try:
        seed_workspace_store(store)
        curator = SkillCurator()
        program, candidate = curator.build_candidate(
            source_task_refs=("task:a", "task:b"),
            source_trace_refs=("trace:a", "trace:b"),
        )
        curator.persist(store, program, candidate)
        row = store.connection.execute(
            "SELECT payload_json FROM artifacts WHERE artifact_id=?",
            (candidate.candidate_program_ref,),
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["operations"][1]["parameters"]["default"] = "ALLOW"
        store.connection.execute(
            "UPDATE artifacts SET payload_json=? WHERE artifact_id=?",
            (canonical_json(payload), candidate.candidate_program_ref),
        )
        store.connection.commit()
        with pytest.raises(IntegrityError, match="ARTIFACT_DIGEST_MISMATCH"):
            GovernedSkillEvaluator(store).evaluate(f"{candidate.id}@{candidate.version}")
    finally:
        store.close()


def test_skill_dependencies_trigger_requalification() -> None:
    store = StateStore(":memory:")
    try:
        seed_workspace_store(store)
        result = SkillFoundryService(store).run()
        manifest = result["dependency_manifest"]
        assert GovernedSkillEvaluator.requires_requalification(
            manifest, "policy:finance.currency"
        )
        assert not GovernedSkillEvaluator.requires_requalification(
            manifest, "claim:gtm.public_launch_message"
        )
    finally:
        store.close()
