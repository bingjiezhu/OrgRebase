#!/usr/bin/env python3
"""Independently verify retained Skill lifecycle evidence without importing OrgRebase."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SKILLS = {
    "enterprise-launch-readiness",
    "enterprise-quote-compose",
    "structured-domain-handoff",
}
PARTITIONS = {
    "REPLAY",
    "HELD_OUT",
    "NEGATIVE_TRANSFER",
    "PERMISSION",
    "INJECTION",
    "MALFORMED",
    "RESOURCE_OR_DEADLINE",
    "CANARY",
}
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
ZERO_DIGEST = "sha256:" + "0" * 64


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _record_ok(value: dict[str, Any]) -> bool:
    return value.get("digest") == _digest({key: item for key, item in value.items() if key != "digest"})


def verify(root: Path) -> dict[str, Any]:
    failures: list[str] = []
    index = _load(root / "evidence-index.json")
    if not isinstance(index, dict) or not _record_ok(index):
        failures.append("INDEX_DIGEST")
        index = {"entries": []}
    entries = index.get("entries", [])
    indexed = {item.get("path") for item in entries if isinstance(item, dict)}
    observed = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.json")
        if path.name != "evidence-index.json"
    }
    if indexed != observed or index.get("entry_count") != len(observed):
        failures.append("INDEX_CLOSURE")
    if index.get("pack_digest") != _digest(entries):
        failures.append("PACK_DIGEST")
    for entry in entries:
        path = root / str(entry.get("path"))
        if (
            not path.is_file()
            or entry.get("sha256") != _file_digest(path)
            or entry.get("bytes") != path.stat().st_size
        ):
            failures.append(f"FILE_BINDING:{entry.get('path')}")

    discovery = _load(root / "discovery.json")
    if (
        not isinstance(discovery, list)
        or {item.get("name") for item in discovery} != SKILLS
        or any(
            item.get("resource_mode") != "INSTALLED_WHEEL"
            or not isinstance(item.get("description"), str)
            or not any("\u3400" <= character <= "\u9fff" for character in item["description"])
            or not any(
                character.isascii() and character.isalpha()
                for character in item["description"]
            )
            or not all(
                isinstance(item.get(field), str)
                and DIGEST.fullmatch(item[field]) is not None
                and item[field] != ZERO_DIGEST
                for field in ("package_digest", "input_schema_digest", "output_schema_digest")
            )
            or set(item.get("language_reference_digests", {})) != {"zh-CN", "en"}
            or not all(
                isinstance(value, str)
                and DIGEST.fullmatch(value) is not None
                and value != ZERO_DIGEST
                for value in item.get("language_reference_digests", {}).values()
            )
            for item in discovery
        )
    ):
        failures.append("DISCOVERY")
    package_digests = {item.get("name"): item.get("package_digest") for item in discovery}
    package_schema_digests = {
        item.get("name"): {
            "input": item.get("input_schema_digest"),
            "output": item.get("output_schema_digest"),
        }
        for item in discovery
    }
    package_language_reference_digests = {
        item.get("name"): item.get("language_reference_digests") for item in discovery
    }
    distribution = _load(root / "distribution" / "installed-wheel-receipt.json")
    if (
        not isinstance(distribution, dict)
        or not _record_ok(distribution)
        or distribution.get("resource_mode") != "INSTALLED_WHEEL"
        or distribution.get("module_origin_under_isolated_site") is not True
        or distribution.get("module_origin_suffix") != "orgrebase/workspace/skill_packages.py"
        or distribution.get("schema_dialect")
        != "https://json-schema.org/draft/2020-12/schema"
        or distribution.get("schema_resolution") != "LOCAL_BUNDLED_ONLY"
        or distribution.get("network_schema_resolution") is not False
        or distribution.get("canonical_skill_entrypoint") != "SKILL.md"
        or distribution.get("language_references_per_package") != 2
        or distribution.get("packages_discovered") != 3
        or not isinstance(distribution.get("wheel_bytes"), int)
        or distribution.get("wheel_bytes", 0) <= 0
        or not isinstance(distribution.get("wheel_sha256"), str)
        or DIGEST.fullmatch(distribution["wheel_sha256"]) is None
    ):
        failures.append("INSTALLED_WHEEL_RECEIPT")
    evaluation_digests: dict[str, str] = {}
    requalification_evaluation_digests: dict[str, str] = {}
    release_heads: dict[str, str] = {}
    rollback_decision_heads: dict[str, str] = {}
    for name in sorted(SKILLS):
        evaluation = _load(root / "evaluations" / f"{name}.json")
        if not isinstance(evaluation, dict) or not _record_ok(evaluation):
            failures.append(f"EVALUATION_DIGEST:{name}")
            continue
        evaluation_digests[name] = str(evaluation["digest"])
        fresh_evaluation = _load(
            root / "requalifications" / "evaluations" / f"{name}.json"
        )
        if not isinstance(fresh_evaluation, dict) or not _record_ok(fresh_evaluation):
            failures.append(f"REQUALIFICATION_EVALUATION_DIGEST:{name}")
            fresh_evaluation = {}
        else:
            requalification_evaluation_digests[name] = str(fresh_evaluation["digest"])
        partitions = {item.get("partition") for item in evaluation.get("case_results", [])}
        fresh_partitions = {
            item.get("partition") for item in fresh_evaluation.get("case_results", [])
        }
        if (
            evaluation.get("verdict") != "CANARY"
            or partitions != PARTITIONS
            or len(evaluation.get("case_results", [])) != 8
            or not all(item.get("passed") is True for item in evaluation.get("case_results", []))
            or not all(item.get("passed") is True for item in evaluation.get("gate_results", []))
            or fresh_evaluation.get("verdict") != "CANARY"
            or fresh_partitions != PARTITIONS
            or len(fresh_evaluation.get("case_results", [])) != 8
            or not all(
                item.get("passed") is True
                for item in fresh_evaluation.get("case_results", [])
            )
            or not all(
                item.get("passed") is True
                for item in fresh_evaluation.get("gate_results", [])
            )
            or fresh_evaluation.get("digest") == evaluation.get("digest")
            or str(fresh_evaluation.get("evaluated_at", ""))
            <= str(evaluation.get("evaluated_at", ""))
        ):
            failures.append(f"EVALUATION_GATES:{name}")
        for folder, expected_states, heads in (
            (
                "releases",
                [
                    "EVALUATED",
                    "SHADOW",
                    "CANARY",
                    "REQUALIFICATION_REQUIRED",
                    "EVALUATED",
                    "SHADOW",
                    "CANARY",
                ],
                release_heads,
            ),
            (
                "rollback-decision-probes",
                [
                    "EVALUATED",
                    "SHADOW",
                    "CANARY",
                    "REQUALIFICATION_REQUIRED",
                    "ROLLBACK_DECISION_RECORDED",
                ],
                rollback_decision_heads,
            ),
        ):
            history = _load(root / folder / f"{name}.json")
            previous = None
            valid = isinstance(history, list) and [item.get("to_state") for item in history] == expected_states
            for number, item in enumerate(history if isinstance(history, list) else (), start=1):
                expected_evaluation = (
                    fresh_evaluation if folder == "releases" and number >= 5 else evaluation
                )
                valid = (
                    valid
                    and isinstance(item, dict)
                    and _record_ok(item)
                    and item.get("event_index") == number
                    and item.get("previous_receipt_digest") == previous
                    and item.get("actor_id") == "authority:skill-registry"
                    and item.get("evaluation_receipt_digest")
                    == expected_evaluation.get("digest")
                    and item.get("evaluation_premise_lock_digest")
                    == _digest(expected_evaluation.get("premise_lock"))
                    and item.get("package_digest") == package_digests.get(name)
                    and item.get("effective_package_digest") == package_digests.get(name)
                )
                previous = item.get("digest")
            if folder == "releases" and isinstance(history, list):
                drift = history[3] if len(history) > 3 else {}
                valid = (
                    valid
                    and drift.get("event_type") == "REQUALIFICATION"
                    and bool(drift.get("changed_dependency_refs"))
                    and history[4].get("created_at", "") > drift.get("created_at", "")
                )
            if folder == "rollback-decision-probes" and isinstance(history, list):
                decision = history[-1] if history else {}
                valid = (
                    valid
                    and decision.get("event_type") == "ROLLBACK_DECISION"
                    and decision.get("predecessor_executable") is False
                    and decision.get("restoration_status") == "NOT_RUN"
                    and decision.get("effective_package_digest")
                    == decision.get("package_digest")
                )
            if not valid or previous is None:
                failures.append(f"LIFECYCLE:{folder}:{name}")
            else:
                heads[name] = str(previous)

    invocation = _load(root / "quote-compose" / "invocation-receipt.json")
    result = _load(root / "quote-compose" / "result.json")
    quote_input = _load(root / "quote-compose" / "input.json")
    if (
        not isinstance(invocation, dict)
        or not _record_ok(invocation)
        or not str(invocation.get("package_id", "")).startswith(
            "skill-package:enterprise-quote-compose@"
        )
        or invocation.get("package_digest") != package_digests.get("enterprise-quote-compose")
        or invocation.get("authorization_mode") != "RELEASE"
        or invocation.get("release_receipt_digest")
        != release_heads.get("enterprise-quote-compose")
        or invocation.get("target_writes") != 0
        or invocation.get("input_digest") != _digest(quote_input)
        or not all(
            isinstance(quote_input.get(field), str)
            and DIGEST.fullmatch(quote_input[field]) is not None
            and quote_input[field] != ZERO_DIGEST
            and result.get(field) == quote_input[field]
            for field in (
                "dependency_tool_receipt_digest",
                "dependency_result_digest",
                "coalition_result_binding_digest",
            )
        )
        or not isinstance(quote_input.get("domain_result_digests"), dict)
        or set(quote_input["domain_result_digests"])
        != {"product", "legal", "finance", "gtm"}
        or not all(
            isinstance(value, str)
            and DIGEST.fullmatch(value) is not None
            and value != ZERO_DIGEST
            for value in quote_input["domain_result_digests"].values()
        )
        or len(set(quote_input["domain_result_digests"].values())) != 4
        or result.get("domain_result_digests")
        != quote_input.get("domain_result_digests")
        or result.get("candidate_only") is not True
        or result.get("action") != "APPLY_QUOTE"
        or result.get("target_writes") != 0
        or invocation.get("output_digest") != _digest(result)
    ):
        failures.append("QUOTE_INVOCATION")
    summary = _load(root / "summary.json")
    if (
        not isinstance(summary, dict)
        or not _record_ok(summary)
        or summary.get("status") != "PASS"
        or summary.get("packages") != package_digests
        or summary.get("package_schema_digests") != package_schema_digests
        or summary.get("package_language_reference_digests")
        != package_language_reference_digests
        or summary.get("evaluation_receipts") != evaluation_digests
        or summary.get("requalification_evaluation_receipts")
        != requalification_evaluation_digests
        or summary.get("release_heads") != release_heads
        or summary.get("requalification_heads") != release_heads
        or summary.get("rollback_decision_receipts") != rollback_decision_heads
        or summary.get("wheel_runtime_receipt") != distribution.get("digest")
        or summary.get("runtime_resource_mode") != "INSTALLED_WHEEL"
        or summary.get("schema_resources_per_package") != 2
        or summary.get("language_references_per_package") != 2
        or summary.get("canonical_skill_entrypoints_per_package") != 1
        or summary.get("schema_resolution") != "LOCAL_BUNDLED_ONLY"
        or summary.get("network_schema_resolution") is not False
        or summary.get("evidence_class")
        != "CONTROLLED_LOCAL_INSTALLED_WHEEL_SCHEMA_AND_REQUALIFICATION"
        or summary.get("claim_ceiling")
        != "CONTROLLED_LOCAL_INSTALLED_WHEEL_SCHEMA_AND_REQUALIFICATION"
        or summary.get("executable_predecessor_restore") != "NOT_RUN"
        or summary.get("release_authority_model")
        != "PROCESS_LOCAL_EXACT_OBJECT_MEMBERSHIP"
        or summary.get("serialized_receipts_reauthorize") is not False
        or summary.get("quote_invocation_receipt") != invocation.get("digest")
        or summary.get("coalition_result_binding_digest")
        != quote_input.get("coalition_result_binding_digest")
        or summary.get("domain_result_digests_digest")
        != _digest(quote_input.get("domain_result_digests"))
        or invocation.get("run_id") != summary.get("run_id")
        or invocation.get("task_id") != summary.get("task_id")
        or invocation.get("delegation_id") != summary.get("delegation_id")
        or summary.get("trajectory_induction") != "NOT_RUN"
        or summary.get("cross_enterprise_generalization") != "NOT_RUN"
        or summary.get("statistically_meaningful_external_evaluation") != "NOT_RUN"
    ):
        failures.append("SUMMARY")
    if failures:
        raise SystemExit("SKILL_EVIDENCE_VERIFY_FAILED:" + ",".join(failures))
    return {
        "status": "PASS",
        "run_id": summary["run_id"],
        "packages": sorted(SKILLS),
        "quote_invocation_receipt": invocation["digest"],
        "claim_ceiling": summary["claim_ceiling"],
        "runtime_resource_mode": "INSTALLED_WHEEL",
        "schema_resolution": "LOCAL_BUNDLED_ONLY",
        "trajectory_induction": "NOT_RUN",
        "cross_enterprise_generalization": "NOT_RUN",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.evidence.resolve()), sort_keys=True))


if __name__ == "__main__":
    main()
