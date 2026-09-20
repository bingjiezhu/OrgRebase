#!/usr/bin/env python3
"""Independently recompute an executable Skill predecessor rollback pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_FILES = {
    "idempotency-proof.json",
    "ledger.json",
    "negative-probes.json",
    "predecessor-input.json",
    "predecessor-result.json",
    "rollback-execution-receipt.json",
    "summary.json",
}
RESOURCE_FILES = {
    "contract": "contract.json",
    "input_schema": "input.schema.json",
    "manifest": "package.json",
    "output_schema": "output.schema.json",
    "program": "program.json",
    "skill": "SKILL.md",
}


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


def _raw_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _file_digest(path: Path) -> str:
    return _raw_digest(path.read_bytes())


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _record_ok(value: Any) -> bool:
    return isinstance(value, dict) and value.get("digest") == _digest(
        {key: item for key, item in value.items() if key != "digest"}
    )


def verify(
    evidence_root: Path,
    *,
    checkout_root: Path = ROOT,
    semifinal_root: Path | None = None,
    current_manifest_path: Path | None = None,
    predecessor_root: Path | None = None,
) -> dict[str, Any]:
    failures: list[str] = []

    def require(condition: bool, code: str) -> None:
        if not condition:
            failures.append(code)

    try:
        checkout_root = checkout_root.resolve()
        semifinal = (
            semifinal_root.resolve()
            if semifinal_root is not None
            else checkout_root / "evidence" / "semifinal-closure" / "latest"
        )
        current_path = (
            current_manifest_path.resolve()
            if current_manifest_path is not None
            else checkout_root / "skills" / "enterprise-quote-compose" / "package.json"
        )
        frozen = (
            predecessor_root.resolve()
            if predecessor_root is not None
            else checkout_root / "skills" / "predecessors" / "enterprise-quote-compose" / "1.3.0"
        )

        index = _load(evidence_root / "evidence-index.json")
        require(_record_ok(index), "INDEX_DIGEST")
        entries = index.get("entries", []) if isinstance(index, dict) else []
        indexed = {item.get("path") for item in entries if isinstance(item, dict)}
        observed = {
            path.relative_to(evidence_root).as_posix()
            for path in evidence_root.rglob("*.json")
            if path.name != "evidence-index.json"
        }
        require(indexed == observed == EXPECTED_FILES, "INDEX_CLOSURE")
        require(index.get("entry_count") == len(EXPECTED_FILES), "INDEX_COUNT")
        require(index.get("pack_digest") == _digest(entries), "INDEX_PACK_DIGEST")
        for item in entries:
            path = evidence_root / str(item.get("path"))
            require(
                path.is_file()
                and item.get("bytes") == path.stat().st_size
                and item.get("sha256") == _file_digest(path),
                f"INDEX_FILE_BINDING:{item.get('path')}",
            )

        manifest = _load(frozen / "package.json")
        contract = _load(frozen / "contract.json")
        program = _load(frozen / "program.json")
        provenance = _load(frozen / "provenance.json")
        raw_digests = {name: _file_digest(frozen / filename) for name, filename in RESOURCE_FILES.items()}
        predecessor_digest = _digest(
            {key: value for key, value in manifest.items() if key != "manifest_digest"}
        )
        require(
            manifest.get("manifest_digest") == predecessor_digest
            and manifest.get("schema_version") == "orgrebase.skill-package-manifest.v2"
            and manifest.get("package_id") == "skill-package:enterprise-quote-compose@1.3.0"
            and manifest.get("entry_point") == "QUOTE_COMPOSE_V1",
            "PREDECESSOR_MANIFEST",
        )
        declarations = manifest.get("resources", {})
        require(
            isinstance(declarations, dict)
            and set(declarations)
            == {"skill", "contract", "program", "input_schema", "output_schema"}
            and all(
                declarations[name].get("path") == RESOURCE_FILES[name]
                and declarations[name].get("sha256") == raw_digests[name]
                for name in ("skill", "contract", "program", "input_schema", "output_schema")
            ),
            "PREDECESSOR_RESOURCE_DIGESTS",
        )
        require(
            contract.get("content_digest")
            == _digest({key: value for key, value in contract.items() if key != "content_digest"})
            and contract.get("name") == "enterprise-quote-compose"
            and contract.get("version") == "1.3",
            "PREDECESSOR_CONTRACT",
        )
        require(
            program.get("digest")
            == _digest({key: value for key, value in program.items() if key != "digest"})
            == manifest.get("program_content_digest")
            and program.get("allowed_tool_ids") == []
            and program.get("side_effects") == []
            and [item.get("operation") for item in program.get("operations", [])]
            == ["REQUIRE_FIELDS", "MAP_VALUE", "RETURN_FIELD"],
            "PREDECESSOR_PROGRAM",
        )
        require(_record_ok(provenance), "PREDECESSOR_PROVENANCE_DIGEST")
        extracted = provenance.get("extracted_resources", {})
        require(
            provenance.get("manifest_digest") == predecessor_digest
            and provenance.get("program_content_digest") == program.get("digest")
            and provenance.get("contract_content_digest") == contract.get("content_digest")
            and isinstance(extracted, dict)
            and set(extracted) == set(RESOURCE_FILES.values())
            and all(
                extracted[filename].get("sha256") == raw_digests[name]
                for name, filename in RESOURCE_FILES.items()
            ),
            "PREDECESSOR_PROVENANCE_BINDING",
        )
        source_wheel = provenance.get("source_wheel", {})
        wheel_path = checkout_root / str(source_wheel.get("relative_path", ""))
        require(
            wheel_path.is_file()
            and wheel_path.stat().st_size == source_wheel.get("bytes")
            and _file_digest(wheel_path) == source_wheel.get("sha256"),
            "SOURCE_WHEEL_BINDING",
        )
        if wheel_path.is_file():
            with zipfile.ZipFile(wheel_path) as archive:
                for _name, filename in RESOURCE_FILES.items():
                    member = extracted[filename]["wheel_member"]
                    require(
                        archive.read(member) == (frozen / filename).read_bytes(),
                        f"SOURCE_WHEEL_MEMBER:{filename}",
                    )

        current_manifest = _load(current_path)
        current_digest = _digest(
            {key: value for key, value in current_manifest.items() if key != "manifest_digest"}
        )
        require(
            current_manifest.get("manifest_digest") == current_digest
            and current_manifest.get("name") == "enterprise-quote-compose"
            and current_manifest.get("release_artifact", {}).get("predecessor_package_digest")
            == predecessor_digest,
            "DIRECT_LINEAGE",
        )

        tool_receipt = _load(semifinal / "operations" / "tool" / "receipt.json")
        tool_result = _load(semifinal / "operations" / "tool" / "result.json")
        current_invocation = _load(semifinal / "skills" / "quote-compose" / "invocation-receipt.json")
        current_input = _load(semifinal / "skills" / "quote-compose" / "input.json")
        current_result = _load(semifinal / "skills" / "quote-compose" / "result.json")
        require(_record_ok(tool_receipt), "TOOL_RECEIPT_DIGEST")
        require(_record_ok(current_invocation), "CURRENT_INVOCATION_DIGEST")
        run_id = current_invocation.get("run_id")
        task_id = current_invocation.get("task_id")
        result_digest = _digest(tool_result)
        require(
            tool_receipt.get("run_id") == tool_result.get("run_id") == run_id
            and tool_receipt.get("response_digest") == result_digest
            and tool_receipt.get("status") == "SUCCEEDED"
            and tool_receipt.get("target_writes") == tool_result.get("target_writes") == 0
            and tool_result.get("task_id") == task_id,
            "TOOL_RESULT_BINDING",
        )
        require(
            current_invocation.get("package_digest")
            == current_invocation.get("manifest_digest")
            == current_digest
            and current_invocation.get("program_digest") == current_manifest.get("program_content_digest")
            and current_invocation.get("input_digest") == _digest(current_input)
            and current_invocation.get("output_digest") == _digest(current_result)
            and current_invocation.get("candidate_only") is True
            and current_invocation.get("target_writes") == 0
            and current_result.get("package_digest") == current_digest
            and current_result.get("candidate_only") is True
            and current_result.get("target_writes") == 0
            and current_input.get("dependency_tool_receipt_digest") == tool_receipt.get("digest")
            and current_input.get("dependency_result_digest") == result_digest,
            "CURRENT_INVOCATION_BINDING",
        )

        predecessor_input = _load(evidence_root / "predecessor-input.json")
        predecessor_result = _load(evidence_root / "predecessor-result.json")
        expected_input = {
            "skill_partition": current_input.get("skill_partition"),
            "candidate_program_digest_required": program.get("digest"),
            "dependency_tool_receipt_digest": tool_receipt.get("digest"),
            "dependency_result_digest": result_digest,
            "coalition_result_binding_digest": current_input.get(
                "coalition_result_binding_digest"
            ),
            "domain_result_digests": current_input.get("domain_result_digests"),
        }
        require(predecessor_input == expected_input, "PREDECESSOR_INPUT")
        mapping = program["operations"][1]["parameters"]["mapping"]
        action = str(mapping.get(str(expected_input["skill_partition"]), "ABSTAIN"))
        expected_result = {
            "schema_version": "orgrebase.skill-result-candidate.v1",
            "action": action,
            "reason": "EXACT_DECLARATIVE_PROGRAM",
            "dependency_tool_receipt_digest": tool_receipt.get("digest"),
            "dependency_result_digest": result_digest,
            "coalition_result_binding_digest": current_input.get(
                "coalition_result_binding_digest"
            ),
            "domain_result_digests": current_input.get("domain_result_digests"),
            "package_digest": predecessor_digest,
            "candidate_only": True,
            "target_writes": 0,
        }
        require(predecessor_result == expected_result, "PREDECESSOR_RESTRICTED_EXECUTION")

        receipt = _load(evidence_root / "rollback-execution-receipt.json")
        schema = _load(checkout_root / "schemas" / "workspace-skill-rollback-execution-receipt.schema.json")
        require(_record_ok(receipt), "ROLLBACK_RECEIPT_DIGEST")
        require(
            set(receipt) == set(schema.get("properties", {}))
            and set(schema.get("required", [])) <= set(receipt),
            "ROLLBACK_RECEIPT_SCHEMA_SURFACE",
        )
        binding_body = {
            "run_id": run_id,
            "task_id": task_id,
            "delegation_id": current_invocation.get("delegation_id"),
            "idempotency_key": receipt.get("idempotency_key"),
            "actor_id": "authority:skill-registry",
            "from_package_digest": current_digest,
            "to_package_digest": predecessor_digest,
            "current_invocation_receipt_digest": current_invocation.get("digest"),
            "current_input_digest": _digest(current_input),
            "current_output_digest": _digest(current_result),
            "dependency_tool_receipt_digest": tool_receipt.get("digest"),
            "dependency_result_digest": result_digest,
            "predecessor_input_digest": _digest(predecessor_input),
            "predecessor_output_digest": _digest(predecessor_result),
            "reason_codes": receipt.get("reason_codes"),
        }
        rollback_binding = _digest(binding_body)
        require(
            receipt.get("schema_version") == "orgrebase.skill-predecessor-rollback-execution-receipt.v1"
            and receipt.get("id") == f"skill-predecessor-rollback:{rollback_binding.removeprefix('sha256:')}"
            and receipt.get("event_index") == 1
            and receipt.get("previous_receipt_digest") is None
            and receipt.get("run_id") == run_id
            and receipt.get("task_id") == task_id
            and receipt.get("actor_id") == "authority:skill-registry"
            and receipt.get("rollback_binding_digest") == rollback_binding
            and receipt.get("from_package_digest") == current_digest
            and receipt.get("declared_predecessor_package_digest")
            == receipt.get("effective_package_digest")
            == predecessor_digest
            and receipt.get("predecessor_resource_digests") == raw_digests
            and receipt.get("predecessor_source_provenance_digest") == provenance.get("digest")
            and receipt.get("source_wheel_digest") == source_wheel.get("sha256")
            and receipt.get("predecessor_input_digest") == _digest(predecessor_input)
            and receipt.get("predecessor_output_digest") == _digest(predecessor_result)
            and receipt.get("predecessor_action") == action
            and receipt.get("lineage_status") == "DIRECT_DECLARED_PREDECESSOR"
            and receipt.get("restoration_status") == "EXECUTED_AND_INVOKED"
            and receipt.get("candidate_only") is True
            and receipt.get("target_writes") == 0,
            "ROLLBACK_RECEIPT_BINDINGS",
        )

        ledger = _load(evidence_root / "ledger.json")
        require(
            ledger == [receipt] and len({item.get("idempotency_key") for item in ledger}) == len(ledger),
            "APPEND_ONLY_LEDGER",
        )
        idempotency = _load(evidence_root / "idempotency-proof.json")
        require(
            _record_ok(idempotency)
            and idempotency.get("run_id") == run_id
            and idempotency.get("first_receipt_digest")
            == idempotency.get("replay_receipt_digest")
            == idempotency.get("rehydrated_replay_receipt_digest")
            == receipt.get("digest")
            and idempotency.get("ledger_event_count_after_replay") == 1
            and idempotency.get("ledger_event_count_after_rehydration") == 1
            and idempotency.get("preexisting_ledger_event_count") in {0, 1}
            and idempotency.get("process_state_rehydration") == "PASS"
            and idempotency.get("status") == "PASS",
            "IDEMPOTENCY_PROOF",
        )
        probes = _load(evidence_root / "negative-probes.json")
        expected_errors = {
            "authority": "SKILL_PREDECESSOR_ROLLBACK_AUTHORITY_DENIED",
            "direct_lineage": "SKILL_PREDECESSOR_DIRECT_LINEAGE_MISMATCH",
            "idempotency_conflict": "SKILL_PREDECESSOR_ROLLBACK_IDEMPOTENCY_CONFLICT",
            "tool_result_tamper": "SKILL_ROLLBACK_TOOL_BINDING_INVALID",
        }
        require(
            _record_ok(probes)
            and probes.get("run_id") == run_id
            and probes.get("status") == "PASS"
            and set(probes.get("probes", {})) == set(expected_errors)
            and all(
                probes["probes"][name]
                == {
                    "expected_error": error,
                    "observed_error": error,
                    "status": "PASS",
                }
                for name, error in expected_errors.items()
            ),
            "NEGATIVE_PROBES",
        )
        summary = _load(evidence_root / "summary.json")
        require(
            _record_ok(summary)
            and summary.get("status") == "PASS"
            and summary.get("run_id") == run_id
            and summary.get("evidence_class") == "CONTROLLED_LOCAL_EXECUTABLE_DIRECT_PREDECESSOR_ROLLBACK"
            and summary.get("from_package_digest") == current_digest
            and summary.get("effective_package_digest") == predecessor_digest
            and summary.get("execution_receipt_digest") == receipt.get("digest")
            and summary.get("predecessor_action") == action
            and summary.get("append_only_events") == 1
            and summary.get("idempotent_replay") is True
            and summary.get("process_state_rehydration") == "PASS"
            and summary.get("negative_probe_count") == len(expected_errors)
            and summary.get("candidate_only") is True
            and summary.get("target_writes") == 0,
            "SUMMARY",
        )
    except Exception as exc:  # verifier must fail closed on malformed packs
        failures.append(f"VERIFIER_EXCEPTION:{type(exc).__name__}:{exc}")

    return {
        "schema_version": "orgrebase.skill-predecessor-rollback-verification.v1",
        "status": "PASS" if not failures else "FAIL",
        "failure_count": len(failures),
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--checkout-root", type=Path, default=ROOT)
    parser.add_argument("--semifinal-root", type=Path)
    parser.add_argument("--current-manifest", type=Path)
    parser.add_argument("--predecessor-root", type=Path)
    args = parser.parse_args()
    result = verify(
        args.root,
        checkout_root=args.checkout_root,
        semifinal_root=args.semifinal_root,
        current_manifest_path=args.current_manifest,
        predecessor_root=args.predecessor_root,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
