from __future__ import annotations

import shutil

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.skill_compatibility import check_retained_compatibility, retained_pairs
from orgrebase.workspace.skill_packages import SkillPackageRegistry
from orgrebase.workspace.skill_rollback import RETAINED_PREDECESSORS, load_frozen_predecessor


@pytest.mark.parametrize("name", RETAINED_PREDECESSORS)
def test_retained_bytes_match_original_wheel_and_current_declared_predecessor(name):
    predecessor = load_frozen_predecessor(name=name, verify_retained_wheel=True)
    current = SkillPackageRegistry().load(name)
    assert predecessor.package_digest == current.manifest["release_artifact"]["predecessor_package_digest"]


@pytest.mark.parametrize("name", ["enterprise-launch-readiness", "structured-domain-handoff"])
def test_mutated_retained_program_cannot_be_used(name, tmp_path):
    original = load_frozen_predecessor(name=name)
    path = tmp_path / name
    shutil.copytree(original.root, path)
    (path / "program.json").write_text('{"adapter":"UNBOUNDED"}')
    with pytest.raises(IntegrityError, match="RESOURCE_DIGEST_MISMATCH"):
        load_frozen_predecessor(path, name=name)


def test_actual_current_and_predecessor_calls_enforce_business_boundaries():
    registry = SkillPackageRegistry()
    calls = []
    pairs = retained_pairs(registry)
    result = check_retained_compatibility(registry, key="test:compatibility",
        expected_catalog_digest=sha256_digest(pairs), before_invocation=lambda: calls.append(1))
    assert len(calls) == 26
    for pair, evidence in zip(pairs, result, strict=True):
        assert evidence["predecessor_replay_equal"] is True
        assert evidence["program_unchanged"] is True
        assert evidence["predecessor_replay"]["output_digest"] == evidence["cases"][0]["predecessor"]["output_digest"]
        for case in evidence["cases"]:
            assert case["predecessor"]["package_digest"] == pair["predecessor_digest"]
            assert case["current"]["package_digest"] == pair["package_digest"]
            assert case["predecessor"]["input"] == case["current"]["input"]
            assert case["predecessor"]["result"]["action"] == case["expected_action"]
            assert case["current"]["result"]["action"] == case["expected_action"]
            assert case["current"]["result"]["target_writes"] == 0


def test_catalog_drift_rejects_before_any_invocation():
    calls = []
    with pytest.raises(IntegrityError, match="CATALOG_CHANGED"):
        check_retained_compatibility(SkillPackageRegistry(), key="test:stale",
            expected_catalog_digest="sha256:" + "0" * 64, before_invocation=lambda: calls.append(1))
    assert calls == []


def test_wrong_security_action_is_not_reported_as_compatibility(monkeypatch):
    registry = SkillPackageRegistry()
    original = registry.interpret_candidate
    def widened(package, public_input):
        result = original(package, public_input)
        if public_input.get("target_write_requested"):
            result["action"] = "REBASE"
        return result
    monkeypatch.setattr(registry, "interpret_candidate", widened)
    with pytest.raises(IntegrityError, match="CASE_REJECTED"):
        check_retained_compatibility(registry, key="test:unsafe",
            expected_catalog_digest=sha256_digest(retained_pairs(registry)), before_invocation=lambda: None)
