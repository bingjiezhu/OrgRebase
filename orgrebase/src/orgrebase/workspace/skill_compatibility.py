"""Compatibility checks against exact retained core Skill packages.

Both versions use the product interpreter. The inputs are controlled fixtures;
these checks neither promote a release nor change a business capability pointer.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.skill_packages import SkillPackageRegistry
from orgrebase.workspace.skill_rollback import load_frozen_predecessor

COMPATIBILITY_SKILLS = ("enterprise-launch-readiness", "structured-domain-handoff")
CALLS_PER_PAIR = 13  # Six cases, each against two versions; one retained-version replay.


def retained_pairs(registry: SkillPackageRegistry) -> tuple[dict[str, Any], ...]:
    pairs = []
    for name in COMPATIBILITY_SKILLS:
        current = registry.load(name)
        predecessor = load_frozen_predecessor(name=name)
        if current.manifest["release_artifact"]["predecessor_package_digest"] != predecessor.package_digest:
            raise IntegrityError("SKILL_COMPATIBILITY_PREDECESSOR_MISMATCH")
        pairs.append({"name": name, "current_version": current.version,
            "predecessor_version": predecessor.manifest["version"],
            "package_digest": current.package_digest, "predecessor_digest": predecessor.package_digest,
            "provenance_digest": predecessor.provenance["digest"],
            "program_unchanged": current.program["digest"] == predecessor.program["digest"]})
    return tuple(pairs)


def _cases(name: str, key: str) -> tuple[tuple[str, dict[str, Any], str], ...]:
    digest = sha256_digest({"fixture": "CONTROLLED_SKILL_COMPATIBILITY", "run": key, "skill": name})
    if name == "enterprise-launch-readiness":
        healthy = {"classification": "AFFECTED_HARD", "object_id": "fixture:launch",
            "reason_code": "DEPENDENCY_CHANGED", "preview_receipt_digest": digest,
            "approval_receipt_digest": sha256_digest({"fixture": "approval", "binding": digest})}
        action, required, stale = "REBASE", "preview_receipt_digest", "stale_preview"
    elif name == "structured-domain-handoff":
        healthy = {"run_id": key, "task_id": key + ":handoff", "delegation_id": key + ":delegation",
            "delegation_task_digest": digest, "context_projection_digest": digest,
            "candidate_bundle": {"classification": "AFFECTED_REVIEW", "object_id": "fixture:quote"}}
        action, required, stale = "HANDOFF", "context_projection_digest", "stale_input"
    else:
        raise IntegrityError("SKILL_COMPATIBILITY_NAME_INVALID")
    missing = {field: value for field, value in healthy.items() if field != required}
    return (
        ("valid-handoff", healthy, action),
        ("missing-binding", missing, "ABSTAIN"),
        ("write-request", {**healthy, "target_write_requested": True}, "DENY"),
        ("stale-evidence", {**healthy, stale: True}, "ABSTAIN"),
        ("injected-instruction", {**healthy, "prompt_injection": True}, "ABSTAIN"),
        ("permission-expansion", {**healthy, "permission_expansion": True}, "DENY"),
    )


def check_retained_compatibility(
    registry: SkillPackageRegistry, *, key: str, expected_catalog_digest: str,
    before_invocation: Callable[[], None],
) -> list[dict[str, Any]]:
    pairs = retained_pairs(registry)
    if sha256_digest(pairs) != expected_catalog_digest:
        raise IntegrityError("SKILL_COMPATIBILITY_CATALOG_CHANGED")
    results = []
    for pair in pairs:
        current = registry.load(pair["name"], expected_package_digest=pair["package_digest"])
        predecessor = load_frozen_predecessor(name=pair["name"])
        cases = _cases(pair["name"], key)
        comparisons = []
        for case_id, public_input, expected_action in cases:
            invocations = []
            for package in (predecessor, current):
                before_invocation()
                result = registry.interpret_candidate(package, public_input)
                if (result.get("action") != expected_action or result.get("candidate_only") is not True
                        or type(result.get("target_writes")) is not int or result["target_writes"] != 0):
                    raise IntegrityError("SKILL_COMPATIBILITY_CASE_REJECTED")
                invocations.append({"package_digest": package.package_digest, "input": deepcopy(public_input),
                    "input_digest": sha256_digest(public_input), "result": result, "output_digest": sha256_digest(result)})
            # Version identities are expected to differ. All business output and
            # boundary decisions must remain equal for this compatibility claim.
            normalized = [{k: v for k, v in item["result"].items() if k not in {"package_digest", "contract_digest"}}
                          for item in invocations]
            if normalized[0] != normalized[1]:
                raise IntegrityError("SKILL_COMPATIBILITY_SEMANTIC_MISMATCH")
            comparisons.append({"case_id": case_id, "expected_action": expected_action,
                "passed": True, "predecessor": invocations[0], "current": invocations[1]})
        before_invocation()
        replay = registry.interpret_candidate(predecessor, cases[0][1])
        if replay != comparisons[0]["predecessor"]["result"]:
            raise IntegrityError("SKILL_COMPATIBILITY_PREDECESSOR_REPLAY_MISMATCH")
        body = {**pair, "scope": "ISOLATED_COMPATIBILITY_AND_PREDECESSOR_INVOCATION",
            "synthetic_inputs": True, "cases": comparisons, "skill_calls": CALLS_PER_PAIR,
            "predecessor_replay": {"result": replay, "output_digest": sha256_digest(replay)},
            "predecessor_replay_equal": True, "business_skill_pointer_writes": 0, "canonical_target_writes": 0}
        results.append({**body, "digest": sha256_digest(body)})
    return results
