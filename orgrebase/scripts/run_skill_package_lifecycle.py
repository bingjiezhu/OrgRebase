#!/usr/bin/env python3
"""Retain three-Skill qualification, release, rollback-decision, and invocation evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.workspace.skill_packages import (
    SKILL_REGISTRY_AUTHORITY,
    InvocationContext,
    SkillEvaluationCase,
    SkillPackageEvaluator,
    SkillPackageRegistry,
    SkillReleaseLedger,
)

SKILL_NAMES = (
    "enterprise-launch-readiness",
    "enterprise-quote-compose",
    "structured-domain-handoff",
)
DOMAIN_NAMES = ("product", "legal", "finance", "gtm")


def _controlled_coalition_bindings(seed: str) -> tuple[str, dict[str, str]]:
    domain_results = {
        domain: sha256_digest({"controlled_coalition": seed, "domain": domain})
        for domain in DOMAIN_NAMES
    }
    return sha256_digest(
        {"controlled_coalition": seed, "domain_result_digests": domain_results}
    ), domain_results


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _record(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "digest": sha256_digest(value)}


def _quote_cases(registry: SkillPackageRegistry) -> tuple[SkillEvaluationCase, ...]:
    package = registry.load("enterprise-quote-compose")
    program = package.manifest["program_content_digest"]
    coalition_digest, domain_results = _controlled_coalition_bindings(
        "skill-evaluation"
    )

    def quote_input(partition: str = "replay", **updates: object) -> dict[str, object]:
        value: dict[str, object] = {
            "skill_partition": partition,
            "candidate_program_digest_required": program,
            "dependency_tool_receipt_digest": sha256_digest({"tool": "evaluation"}),
            "dependency_result_digest": sha256_digest({"result": "evaluation"}),
            "coalition_result_binding_digest": coalition_digest,
            "domain_result_digests": domain_results,
        }
        value.update(updates)
        return value

    mapping = {
        "REPLAY": (quote_input(), "APPLY_QUOTE"),
        "HELD_OUT": (quote_input("held_out"), "APPLY_QUOTE"),
        "NEGATIVE_TRANSFER": (quote_input("negative_transfer"), "KEEP_CURRENT"),
        "PERMISSION": (quote_input(permission_expansion=True), "DENY"),
        "INJECTION": (quote_input(prompt_injection=True), "ABSTAIN"),
        "MALFORMED": (
            {
                key: value
                for key, value in quote_input().items()
                if key != "dependency_result_digest"
            },
            "ABSTAIN",
        ),
        "RESOURCE_OR_DEADLINE": (quote_input(deadline_expired=True), "ABSTAIN"),
        "CANARY": (quote_input("canary"), "CANARY"),
    }
    return tuple(
        SkillEvaluationCase(
            case_id=f"quote:{partition.lower()}",
            partition=partition,
            public_input=public_input,
            expected_action=expected,
        )
        for partition, (public_input, expected) in mapping.items()
    )


def _handoff_input(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "run_id": "evaluation:structured-domain-handoff",
        "task_id": "evaluation-task:handoff",
        "delegation_id": "evaluation-delegation:handoff",
        "delegation_task_digest": sha256_digest({"delegation": "product"}),
        "context_projection_digest": sha256_digest({"projection": "product"}),
        "candidate_bundle": {"claims": [{"ref": "claim:product.plan@v1"}]},
    }
    value.update(updates)
    return value


def _handoff_cases() -> tuple[SkillEvaluationCase, ...]:
    mapping: dict[str, tuple[dict[str, object], str]] = {
        "REPLAY": (_handoff_input(), "HANDOFF"),
        "HELD_OUT": (_handoff_input(candidate_bundle={"claims": []}), "HANDOFF"),
        "NEGATIVE_TRANSFER": (
            _handoff_input(candidate_bundle={"claims": [], "applicable": False}),
            "HANDOFF",
        ),
        "PERMISSION": (_handoff_input(permission_expansion=True), "DENY"),
        "INJECTION": (_handoff_input(prompt_injection=True), "ABSTAIN"),
        "MALFORMED": (
            {key: value for key, value in _handoff_input().items() if key != "candidate_bundle"},
            "ABSTAIN",
        ),
        "RESOURCE_OR_DEADLINE": (_handoff_input(deadline_expired=True), "ABSTAIN"),
        "CANARY": (
            _handoff_input(candidate_bundle={"claims": []}),
            "HANDOFF",
        ),
    }
    return tuple(
        SkillEvaluationCase(
            case_id=f"handoff:{partition.lower()}",
            partition=partition,
            public_input=public_input,
            expected_action=expected,
        )
        for partition, (public_input, expected) in mapping.items()
    )


def _launch_input(classification: str = "AFFECTED_HARD", **updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "classification": classification,
        "object_id": "work:enterprise-quote",
        "reason_code": "EVALUATOR_ONLY_CASE",
        "preview_receipt_digest": sha256_digest({"preview": "skill-package-eval"}),
        "approval_receipt_digest": sha256_digest({"approval": "skill-package-eval"}),
    }
    value.update(updates)
    return value


def _launch_cases() -> tuple[SkillEvaluationCase, ...]:
    mapping: dict[str, tuple[dict[str, object], str]] = {
        "REPLAY": (_launch_input("AFFECTED_HARD"), "REBASE"),
        "HELD_OUT": (_launch_input("AFFECTED_REVIEW"), "REVIEW"),
        "NEGATIVE_TRANSFER": (
            _launch_input("UNAFFECTED_WITHIN_DECLARED_BOUNDARY"),
            "KEEP_CURRENT",
        ),
        "PERMISSION": (_launch_input(request_restricted_source=True), "DENY"),
        "INJECTION": (_launch_input(prompt_injection=True), "ABSTAIN"),
        "MALFORMED": (
            {key: value for key, value in _launch_input().items() if key != "object_id"},
            "ABSTAIN",
        ),
        "RESOURCE_OR_DEADLINE": (_launch_input(deadline_expired=True), "ABSTAIN"),
        "CANARY": (_launch_input("AFFECTED_INFORMATIONAL"), "NOTIFY"),
    }
    return tuple(
        SkillEvaluationCase(
            case_id=f"launch:{partition.lower()}",
            partition=partition,
            public_input=public_input,
            expected_action=expected,
        )
        for partition, (public_input, expected) in mapping.items()
    )


def _cases(name: str, registry: SkillPackageRegistry) -> tuple[SkillEvaluationCase, ...]:
    if name == "enterprise-quote-compose":
        return _quote_cases(registry)
    if name == "structured-domain-handoff":
        return _handoff_cases()
    return _launch_cases()


def _qualify(
    name: str,
    evaluation: dict[str, Any],
    registry: SkillPackageRegistry,
    evaluator: SkillPackageEvaluator,
    *,
    created_at: str,
) -> SkillReleaseLedger:
    ledger = SkillReleaseLedger(registry, evaluator)
    for state in ("EVALUATED", "SHADOW", "CANARY"):
        ledger.transition(
            name,
            evaluation,
            to_state=state,
            actor_id=SKILL_REGISTRY_AUTHORITY,
            reason_codes=(f"CONTROLLED_QUALIFICATION_{state}",),
            created_at=created_at,
        )
    return ledger


def _after(value: str, *, seconds: int = 1) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (parsed + timedelta(seconds=seconds)).astimezone(UTC).isoformat().replace(
        "+00:00", "Z"
    )


def _run_skill_lifecycle_installed(
    *,
    output_dir: str | Path,
    run_id: str,
    task_id: str,
    delegation_id: str,
    created_at: str,
    dependency_tool_receipt_digest: str | None,
    dependency_result_digest: str | None,
    coalition_result_binding_digest: str | None,
    domain_result_digests: dict[str, str] | None,
    wheel_filename: str,
    wheel_digest: str,
    wheel_bytes: int,
    expected_site: str | Path,
) -> dict[str, Any]:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    registry = SkillPackageRegistry()
    site = Path(expected_site).resolve()
    module_origin = Path(sys.modules[SkillPackageRegistry.__module__].__file__).resolve()
    if registry.resource_mode != "INSTALLED_WHEEL" or site not in module_origin.parents:
        raise RuntimeError("SKILL_LIFECYCLE_NOT_RUNNING_FROM_EXPECTED_INSTALLED_WHEEL")
    discovery = list(registry.discover())
    if not all(item.get("resource_mode") == "INSTALLED_WHEEL" for item in discovery):
        raise RuntimeError("SKILL_DISCOVERY_NOT_INSTALLED_WHEEL")
    _write(output / "discovery.json", discovery)
    distribution_receipt = _record(
        {
            "schema_version": "orgrebase.skill-wheel-runtime-receipt.v1",
            "wheel_filename": wheel_filename,
            "wheel_sha256": wheel_digest,
            "wheel_bytes": wheel_bytes,
            "resource_mode": "INSTALLED_WHEEL",
            "module_origin_suffix": "orgrebase/workspace/skill_packages.py",
            "module_origin_under_isolated_site": True,
            "schema_dialect": "https://json-schema.org/draft/2020-12/schema",
            "schema_resolution": "LOCAL_BUNDLED_ONLY",
            "network_schema_resolution": False,
            "canonical_skill_entrypoint": "SKILL.md",
            "language_references_per_package": 2,
            "packages_discovered": len(discovery),
            "created_at": created_at,
        }
    )
    _write(output / "distribution" / "installed-wheel-receipt.json", distribution_receipt)
    evaluator = SkillPackageEvaluator(registry)
    evaluations: dict[str, dict[str, Any]] = {}
    requalification_evaluations: dict[str, dict[str, Any]] = {}
    release_ledgers: dict[str, SkillReleaseLedger] = {}
    rollback_decision_digests: dict[str, str] = {}
    for name in SKILL_NAMES:
        evaluation = evaluator.evaluate(
            name,
            _cases(name, registry),
            evaluated_at=created_at,
        )
        evaluations[name] = evaluation
        _write(output / "evaluations" / f"{name}.json", evaluation)
        package = registry.load(name)

        release = _qualify(name, evaluation, registry, evaluator, created_at=created_at)
        drifted = dict(package.manifest["dependencies"])
        first_dependency = sorted(drifted)[0]
        drifted[first_dependency] = "sha256:" + "0" * 64
        release.mark_requalification(
            name,
            drifted,
            actor_id=SKILL_REGISTRY_AUTHORITY,
            reason_codes=("CONTROLLED_DEPENDENCY_DRIFT_PROBE",),
            created_at=created_at,
        )
        fresh_at = _after(created_at)
        fresh_evaluation = evaluator.evaluate(
            name,
            _cases(name, registry),
            evaluated_at=fresh_at,
        )
        requalification_evaluations[name] = fresh_evaluation
        _write(
            output / "requalifications" / "evaluations" / f"{name}.json",
            fresh_evaluation,
        )
        release.transition(
            name,
            fresh_evaluation,
            to_state="EVALUATED",
            actor_id=SKILL_REGISTRY_AUTHORITY,
            reason_codes=("FRESH_REQUALIFICATION_EVALUATED",),
            observed_dependencies=package.manifest["dependencies"],
            created_at=fresh_at,
        )
        for state in ("SHADOW", "CANARY"):
            release.transition(
                name,
                fresh_evaluation,
                to_state=state,
                actor_id=SKILL_REGISTRY_AUTHORITY,
                reason_codes=(f"FRESH_REQUALIFICATION_{state}",),
                created_at=fresh_at,
            )
        release_ledgers[name] = release
        _write(output / "releases" / f"{name}.json", list(release.history))

        rollback_probe = _qualify(name, evaluation, registry, evaluator, created_at=created_at)
        rollback_probe.mark_requalification(
            name,
            drifted,
            actor_id=SKILL_REGISTRY_AUTHORITY,
            reason_codes=("CONTROLLED_DEPENDENCY_DRIFT_PROBE",),
            created_at=created_at,
        )
        rollback = rollback_probe.record_rollback_decision(
            name,
            actor_id=SKILL_REGISTRY_AUTHORITY,
            reason_codes=("CONTROLLED_PREDECESSOR_ROLLBACK_DECISION",),
            created_at=created_at,
        )
        rollback_decision_digests[name] = rollback["digest"]
        _write(
            output / "rollback-decision-probes" / f"{name}.json",
            list(rollback_probe.history),
        )

    quote = registry.load("enterprise-quote-compose")
    default_coalition, default_domain_results = _controlled_coalition_bindings(run_id)
    coalition_digest = coalition_result_binding_digest or default_coalition
    domain_results = domain_result_digests or default_domain_results
    if set(domain_results) != set(DOMAIN_NAMES):
        raise ValueError("EXACT_FOUR_DOMAIN_RESULTS_REQUIRED")
    quote_input = {
        "skill_partition": "replay",
        "candidate_program_digest_required": quote.manifest["program_content_digest"],
        "dependency_tool_receipt_digest": dependency_tool_receipt_digest
        or sha256_digest({"controlled_local_tool": run_id}),
        "dependency_result_digest": dependency_result_digest
        or sha256_digest({"controlled_local_result": run_id}),
        "coalition_result_binding_digest": coalition_digest,
        "domain_result_digests": domain_results,
    }
    invocation = release_ledgers["enterprise-quote-compose"].invoke(
        "enterprise-quote-compose",
        quote_input,
        context=InvocationContext(
            run_id=run_id,
            task_id=task_id,
            delegation_id=delegation_id,
            actor_id="worker:gtm-steward",
        ),
        observed_dependencies=quote.manifest["dependencies"],
        created_at=_after(created_at, seconds=2),
    )
    _write(output / "quote-compose" / "input.json", quote_input)
    _write(output / "quote-compose" / "invocation-receipt.json", invocation.receipt)
    _write(output / "quote-compose" / "result.json", invocation.result)
    summary_body = {
        "schema_version": "orgrebase.skill-lifecycle-evidence-summary.v2",
        "status": "PASS",
        "evidence_class": "CONTROLLED_LOCAL_INSTALLED_WHEEL_SCHEMA_AND_REQUALIFICATION",
        "run_id": run_id,
        "task_id": task_id,
        "delegation_id": delegation_id,
        "packages": {item["name"]: item["package_digest"] for item in discovery},
        "package_schema_digests": {
            item["name"]: {
                "input": item["input_schema_digest"],
                "output": item["output_schema_digest"],
            }
            for item in discovery
        },
        "package_language_reference_digests": {
            item["name"]: item["language_reference_digests"] for item in discovery
        },
        "evaluation_receipts": {name: value["digest"] for name, value in evaluations.items()},
        "requalification_evaluation_receipts": {
            name: value["digest"] for name, value in requalification_evaluations.items()
        },
        "release_heads": {
            name: release_ledgers[name].head(name)["digest"] for name in SKILL_NAMES
        },
        "requalification_heads": {
            name: release_ledgers[name].head(name)["digest"] for name in SKILL_NAMES
        },
        "rollback_decision_receipts": rollback_decision_digests,
        "wheel_runtime_receipt": distribution_receipt["digest"],
        "runtime_resource_mode": "INSTALLED_WHEEL",
        "schema_resources_per_package": 2,
        "language_references_per_package": 2,
        "canonical_skill_entrypoints_per_package": 1,
        "schema_resolution": "LOCAL_BUNDLED_ONLY",
        "network_schema_resolution": False,
        "executable_predecessor_restore": "NOT_RUN",
        "quote_invocation_receipt": invocation.receipt["digest"],
        "quote_result_digest": invocation.receipt["output_digest"],
        "coalition_result_binding_digest": coalition_digest,
        "domain_result_digests_digest": sha256_digest(domain_results),
        "target_writes": invocation.receipt["target_writes"],
        "release_authority_model": "PROCESS_LOCAL_EXACT_OBJECT_MEMBERSHIP",
        "serialized_receipts_reauthorize": False,
        "claim_ceiling": "CONTROLLED_LOCAL_INSTALLED_WHEEL_SCHEMA_AND_REQUALIFICATION",
        "trajectory_induction": "NOT_RUN",
        "cross_enterprise_generalization": "NOT_RUN",
        "statistically_meaningful_external_evaluation": "NOT_RUN",
    }
    summary = _record(summary_body)
    _write(output / "summary.json", summary)
    entries = [
        {
            "path": path.relative_to(output).as_posix(),
            "sha256": _file_digest(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(output.rglob("*.json"))
        if path.name != "evidence-index.json"
    ]
    index = _record(
        {
            "schema_version": "orgrebase.skill-lifecycle-evidence-index.v2",
            "entries": entries,
            "entry_count": len(entries),
            "pack_digest": sha256_digest(entries),
        }
    )
    _write(output / "evidence-index.json", index)
    return summary


def run_skill_lifecycle_evidence(
    *,
    output_dir: str | Path,
    run_id: str,
    task_id: str,
    delegation_id: str,
    root: str | Path | None = None,
    wheel_path: str | Path | None = None,
    created_at: str = "2026-08-26T00:00:00Z",
    dependency_tool_receipt_digest: str | None = None,
    dependency_result_digest: str | None = None,
    coalition_result_binding_digest: str | None = None,
    domain_result_digests: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build/use one wheel, then run retained lifecycle from its isolated layout."""

    output = Path(output_dir).resolve()
    build_root = Path(root).resolve() if root is not None else Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="orgrebase-skill-wheel-") as temporary:
        temp = Path(temporary)
        if wheel_path is None:
            dist = temp / "dist"
            result = subprocess.run(
                ["uv", "build", "--wheel", "--out-dir", str(dist)],
                cwd=build_root,
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"SKILL_WHEEL_BUILD_FAILED:{result.stderr.strip()}")
            wheels = sorted(dist.glob("*.whl"))
            if len(wheels) != 1:
                raise RuntimeError("SKILL_CANONICAL_WHEEL_COUNT_INVALID")
            wheel = wheels[0]
        else:
            wheel = Path(wheel_path).resolve()
            if not wheel.is_file() or wheel.suffix != ".whl":
                raise ValueError("wheel_path must identify one built .whl file")
        site = temp / "installed-site"
        site.mkdir()
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(site)
        env = dict(os.environ)
        env.update({"PYTHONPATH": str(site), "PYTHONNOUSERSITE": "1"})
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--installed-worker",
            "--output-dir",
            str(output),
            "--run-id",
            run_id,
            "--task-id",
            task_id,
            "--delegation-id",
            delegation_id,
            "--created-at",
            created_at,
            "--wheel-filename",
            wheel.name,
            "--wheel-digest",
            _file_digest(wheel),
            "--wheel-bytes",
            str(wheel.stat().st_size),
            "--expected-site",
            str(site),
        ]
        if dependency_tool_receipt_digest is not None:
            command.extend(
                ["--dependency-tool-receipt-digest", dependency_tool_receipt_digest]
            )
        if dependency_result_digest is not None:
            command.extend(["--dependency-result-digest", dependency_result_digest])
        if coalition_result_binding_digest is not None:
            command.extend(
                [
                    "--coalition-result-binding-digest",
                    coalition_result_binding_digest,
                ]
            )
        if domain_result_digests is not None:
            command.extend(
                [
                    "--domain-result-digests-json",
                    json.dumps(domain_result_digests, sort_keys=True),
                ]
            )
        result = subprocess.run(
            command,
            cwd=temp,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"SKILL_INSTALLED_WHEEL_LIFECYCLE_FAILED:{result.stderr.strip()}")
    return json.loads((output / "summary.json").read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--delegation-id", required=True)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--wheel-path", type=Path)
    parser.add_argument("--installed-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--wheel-filename", help=argparse.SUPPRESS)
    parser.add_argument("--wheel-digest", help=argparse.SUPPRESS)
    parser.add_argument("--wheel-bytes", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--expected-site", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--created-at", default="2026-08-26T00:00:00Z")
    parser.add_argument("--dependency-tool-receipt-digest")
    parser.add_argument("--dependency-result-digest")
    parser.add_argument("--coalition-result-binding-digest")
    parser.add_argument("--domain-result-digests-json")
    args = parser.parse_args()
    if args.installed_worker:
        if not all(
            (args.wheel_filename, args.wheel_digest, args.wheel_bytes, args.expected_site)
        ):
            parser.error("installed worker requires wheel metadata and expected site")
        domain_result_digests = (
            json.loads(args.domain_result_digests_json)
            if args.domain_result_digests_json
            else None
        )
        if domain_result_digests is not None and not isinstance(
            domain_result_digests, dict
        ):
            parser.error("domain result digests must decode to an object")
        summary = _run_skill_lifecycle_installed(
            output_dir=args.output_dir,
            run_id=args.run_id,
            task_id=args.task_id,
            delegation_id=args.delegation_id,
            created_at=args.created_at,
            dependency_tool_receipt_digest=args.dependency_tool_receipt_digest,
            dependency_result_digest=args.dependency_result_digest,
            coalition_result_binding_digest=args.coalition_result_binding_digest,
            domain_result_digests=domain_result_digests,
            wheel_filename=args.wheel_filename,
            wheel_digest=args.wheel_digest,
            wheel_bytes=args.wheel_bytes,
            expected_site=args.expected_site,
        )
    else:
        summary = run_skill_lifecycle_evidence(
            output_dir=args.output_dir,
            run_id=args.run_id,
            task_id=args.task_id,
            delegation_id=args.delegation_id,
            root=args.root,
            wheel_path=args.wheel_path,
            created_at=args.created_at,
            dependency_tool_receipt_digest=args.dependency_tool_receipt_digest,
            dependency_result_digest=args.dependency_result_digest,
            coalition_result_binding_digest=args.coalition_result_binding_digest,
            domain_result_digests=(
                json.loads(args.domain_result_digests_json)
                if args.domain_result_digests_json
                else None
            ),
        )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
