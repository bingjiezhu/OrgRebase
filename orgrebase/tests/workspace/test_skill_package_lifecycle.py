from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.skill_foundry import SkillCurator
from orgrebase.workspace.skill_packages import (
    PARTITIONS,
    SKILL_REGISTRY_AUTHORITY,
    InvocationContext,
    SkillEvaluationCase,
    SkillPackageEvaluator,
    SkillPackageRegistry,
    SkillReleaseLedger,
)
from scripts.reseal_skill_packages import reseal_skill_package
from scripts.run_skill_package_lifecycle import run_skill_lifecycle_evidence
from scripts.verify_skill_package_evidence import verify as verify_skill_evidence

ROOT = Path(__file__).resolve().parents[2]
FIXED_TIME = "2026-08-26T00:00:00Z"


def _assert_schema_surface(payload: dict[str, object], schema_name: str) -> None:
    schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
    assert set(schema["required"]) <= set(payload)
    if schema.get("additionalProperties") is False:
        assert set(payload) <= set(schema["properties"])


def _quote_input(package_digest: str, partition: str = "replay") -> dict[str, object]:
    domain_results = {
        domain: sha256_digest({"domain": domain, "fixture": "quote-coalition"})
        for domain in ("product", "legal", "finance", "gtm")
    }
    return {
        "skill_partition": partition,
        "candidate_program_digest_required": package_digest,
        "dependency_tool_receipt_digest": sha256_digest({"tool": "quote-fixture"}),
        "dependency_result_digest": sha256_digest({"result": "quote-fixture"}),
        "coalition_result_binding_digest": sha256_digest(
            {"coalition": "quote-fixture", "domain_results": domain_results}
        ),
        "domain_result_digests": domain_results,
    }


def _handoff_input(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "run_id": "run:skill-package-eval",
        "task_id": "task:enterprise-quote",
        "delegation_id": "delegation:enterprise-quote:product",
        "delegation_task_digest": sha256_digest({"delegation": "product"}),
        "context_projection_digest": sha256_digest({"projection": "product"}),
        "candidate_bundle": {"claims": [{"ref": "claim:product.plan@v1"}]},
    }
    value.update(updates)
    return value


def _launch_input(classification: str = "AFFECTED_HARD", **updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "classification": classification,
        "object_id": "work:enterprise-quote",
        "reason_code": "FROZEN_EVALUATION_CASE",
        "preview_receipt_digest": sha256_digest({"preview": "skill-package-eval"}),
        "approval_receipt_digest": sha256_digest({"approval": "skill-package-eval"}),
    }
    value.update(updates)
    return value


def _cases(name: str, registry: SkillPackageRegistry) -> tuple[SkillEvaluationCase, ...]:
    package = registry.load(name)
    if name == "enterprise-quote-compose":
        values = {
            "REPLAY": (_quote_input(package.manifest["program_content_digest"]), "APPLY_QUOTE"),
            "HELD_OUT": (
                _quote_input(package.manifest["program_content_digest"], "held_out"),
                "APPLY_QUOTE",
            ),
            "NEGATIVE_TRANSFER": (
                _quote_input(package.manifest["program_content_digest"], "negative_transfer"),
                "KEEP_CURRENT",
            ),
            "PERMISSION": (
                {
                    **_quote_input(package.manifest["program_content_digest"]),
                    "permission_expansion": True,
                },
                "DENY",
            ),
            "INJECTION": (
                {
                    **_quote_input(package.manifest["program_content_digest"]),
                    "prompt_injection": True,
                },
                "ABSTAIN",
            ),
            "MALFORMED": (
                {
                    key: value
                    for key, value in _quote_input(
                        package.manifest["program_content_digest"]
                    ).items()
                    if key != "dependency_result_digest"
                },
                "ABSTAIN",
            ),
            "RESOURCE_OR_DEADLINE": (
                {
                    **_quote_input(package.manifest["program_content_digest"]),
                    "deadline_expired": True,
                },
                "ABSTAIN",
            ),
            "CANARY": (
                _quote_input(package.manifest["program_content_digest"], "canary"),
                "CANARY",
            ),
        }
        return tuple(
            SkillEvaluationCase(
                case_id=f"quote:{partition.lower()}",
                partition=partition,
                public_input=public_input,
                expected_action=expected,
            )
            for partition, (public_input, expected) in values.items()
        )
    if name == "structured-domain-handoff":
        values = {
            "REPLAY": (_handoff_input(), "HANDOFF"),
            "HELD_OUT": (_handoff_input(candidate_bundle={"claims": []}), "HANDOFF"),
            "NEGATIVE_TRANSFER": (
                _handoff_input(candidate_bundle={"claims": [], "applicable": False}),
                "HANDOFF",
            ),
            "PERMISSION": (_handoff_input(permission_expansion=True), "DENY"),
            "INJECTION": (_handoff_input(prompt_injection=True), "ABSTAIN"),
            "MALFORMED": (
                {
                    key: value
                    for key, value in _handoff_input().items()
                    if key != "candidate_bundle"
                },
                "ABSTAIN",
            ),
            "RESOURCE_OR_DEADLINE": (_handoff_input(deadline_expired=True), "ABSTAIN"),
            "CANARY": (_handoff_input(candidate_bundle={"claims": []}), "HANDOFF"),
        }
        return tuple(
            SkillEvaluationCase(
                case_id=f"handoff:{partition.lower()}",
                partition=partition,
                public_input=public_input,
                expected_action=expected,
            )
            for partition, (public_input, expected) in values.items()
        )
    values = {
        "REPLAY": (_launch_input("AFFECTED_HARD"), "REBASE"),
        "HELD_OUT": (_launch_input("AFFECTED_REVIEW"), "REVIEW"),
        "NEGATIVE_TRANSFER": (
            _launch_input("UNAFFECTED_WITHIN_DECLARED_BOUNDARY"),
            "KEEP_CURRENT",
        ),
        "PERMISSION": (_launch_input(request_restricted_source=True), "DENY"),
        "INJECTION": (_launch_input(prompt_injection=True), "ABSTAIN"),
        "MALFORMED": (
            {
                key: value
                for key, value in _launch_input().items()
                if key != "object_id"
            },
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
        for partition, (public_input, expected) in values.items()
    )


def _qualify(
    name: str, registry: SkillPackageRegistry
) -> tuple[dict[str, object], SkillReleaseLedger]:
    evaluator = SkillPackageEvaluator(registry)
    evaluation = evaluator.evaluate(
        name, _cases(name, registry), evaluated_at=FIXED_TIME
    )
    ledger = SkillReleaseLedger(registry, evaluator)
    for state in ("EVALUATED", "SHADOW", "CANARY"):
        ledger.transition(
            name,
            evaluation,
            to_state=state,
            actor_id=SKILL_REGISTRY_AUTHORITY,
            reason_codes=(f"QUALIFIED_FOR_{state}",),
            created_at=FIXED_TIME,
        )
    return evaluation, ledger


def test_registry_discovers_exactly_three_digest_bound_packages() -> None:
    registry = SkillPackageRegistry()

    discovered = registry.discover()

    assert [item["name"] for item in discovered] == [
        "enterprise-launch-readiness",
        "enterprise-quote-compose",
        "structured-domain-handoff",
    ]
    assert all(item["resource_mode"] == "SOURCE_CHECKOUT" for item in discovered)
    assert all(str(item["package_digest"]).startswith("sha256:") for item in discovered)
    for item in discovered:
        package = registry.load(item["name"], expected_package_digest=item["package_digest"])
        assert any("\u3400" <= character <= "\u9fff" for character in package.description)
        assert any(character.isascii() and character.isalpha() for character in package.description)
        assert set(package.reference_bytes) == {"zh-CN", "en"}
        assert item["description"] == package.description
        assert item["language_reference_digests"] == {
            "zh-CN": package.resource_digests["reference_zh_cn"],
            "en": package.resource_digests["reference_en"],
        }
        assert package.manifest["program_content_digest"] == package.program["digest"]
        assert package.manifest["release_artifact"]["source_candidate_executable"] is False
        assert package.manifest["permissions"] == {
            "allowed_tools": [],
            "side_effects": [],
            "effect_ceiling": "CANDIDATE_ONLY",
        }
        assert package.input_schema["$id"] == package.manifest["input_schema_ref"]
        assert package.output_schema["$id"] == package.manifest["output_schema_ref"]
        assert item["input_schema_digest"] == package.resource_digests["input_schema"]
        assert item["output_schema_digest"] == package.resource_digests["output_schema"]
        _assert_schema_surface(
            package.manifest, "skill-package-manifest-v2.schema.json"
        )


def test_quote_release_artifact_does_not_rewrite_historical_candidate() -> None:
    program, candidate = SkillCurator().build_candidate(
        source_task_refs=("task:quote_acme", "task:quote_beta"),
        source_trace_refs=("work-trace:quote_acme@v1", "work-trace:quote_beta@v1"),
    )
    package = SkillPackageRegistry().load("enterprise-quote-compose")
    release = package.manifest["release_artifact"]

    assert candidate.executable is False
    assert candidate.digest == release["source_candidate_digest"]
    assert program.digest == package.contract["candidate"]["program_digest"]
    assert program.digest != package.manifest["program_content_digest"]
    assert release["id"] != candidate.id


@pytest.mark.parametrize(
    "resource",
    [
        "SKILL.md",
        "references/zh-CN.md",
        "references/en.md",
        "contract.json",
        "program.json",
        "input.schema.json",
        "output.schema.json",
    ],
)
def test_exact_loader_rejects_any_resource_mutation(tmp_path: Path, resource: str) -> None:
    (tmp_path / "configs" / "workspace").mkdir(parents=True)
    shutil.copy2(
        ROOT / "configs" / "workspace" / "skill-registry.json",
        tmp_path / "configs" / "workspace" / "skill-registry.json",
    )
    shutil.copytree(ROOT / "skills", tmp_path / "skills")
    path = tmp_path / "skills" / "enterprise-quote-compose" / resource
    path.write_bytes(path.read_bytes() + b"\nmutated")

    with pytest.raises(IntegrityError, match="SKILL_RESOURCE_DIGEST_MISMATCH"):
        SkillPackageRegistry(tmp_path).load("enterprise-quote-compose")


def test_loader_rejects_a_second_canonical_localized_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "configs" / "workspace").mkdir(parents=True)
    shutil.copy2(
        ROOT / "configs" / "workspace" / "skill-registry.json",
        tmp_path / "configs" / "workspace" / "skill-registry.json",
    )
    shutil.copytree(ROOT / "skills", tmp_path / "skills")
    second_entrypoint = (
        tmp_path / "skills" / "enterprise-quote-compose" / "SKILL.zh.md"
    )
    second_entrypoint.write_text("---\nname: enterprise-quote-compose\n---\n", encoding="utf-8")

    with pytest.raises(
        IntegrityError, match="SKILL_PACKAGE_SECOND_CANONICAL_ENTRYPOINT_FORBIDDEN"
    ):
        SkillPackageRegistry(tmp_path).load("enterprise-quote-compose")


@pytest.mark.parametrize(
    ("name", "mode", "request_fragment"),
    [
        ("enterprise-quote-compose", "zh", "生成企业报价"),
        ("enterprise-quote-compose", "en", "compose an enterprise quote"),
        ("enterprise-quote-compose", "mixed", "Product/Legal/Finance/GTM"),
        ("enterprise-quote-compose", "zh_filename", "客户报价单.xlsx"),
        ("enterprise-quote-compose", "implicit", "整合产品/法务/财务/GTM 结果"),
        ("structured-domain-handoff", "zh", "跨领域结构化交接"),
        ("structured-domain-handoff", "en", "hand a domain candidate"),
        ("structured-domain-handoff", "mixed", "context handoff"),
        ("structured-domain-handoff", "zh_filename", "产品规格-签核版.json"),
        ("structured-domain-handoff", "implicit", "传递结构化证据包"),
        ("enterprise-launch-readiness", "zh", "判断上线变更是否需要 Rebase"),
        ("enterprise-launch-readiness", "en", "minimum action"),
        ("enterprise-launch-readiness", "mixed", "ImpactResult"),
        ("enterprise-launch-readiness", "zh_filename", "上线审批单-最终版.pdf"),
        ("enterprise-launch-readiness", "implicit", "检查企业上线就绪性"),
    ],
)
def test_bilingual_discovery_corpus_covers_realistic_intent_modes(
    name: str, mode: str, request_fragment: str
) -> None:
    package = SkillPackageRegistry().load(name)
    corpus = "\n".join(
        (
            package.description,
            package.skill_bytes.decode("utf-8"),
            package.reference_bytes["zh-CN"].decode("utf-8"),
            package.reference_bytes["en"].decode("utf-8"),
        )
    ).casefold()

    assert mode in {"zh", "en", "mixed", "zh_filename", "implicit"}
    assert request_fragment.casefold() in corpus


def test_localized_values_keep_candidate_output_structure_equivalent() -> None:
    registry = SkillPackageRegistry()
    handoff = registry.load("structured-domain-handoff")
    handoff_context = InvocationContext(
        run_id="run:skill-package-eval",
        task_id="task:enterprise-quote",
        delegation_id="delegation:enterprise-quote:product",
        actor_id="evaluator:bilingual",
    )
    handoff_outputs = []
    for filename in ("product-spec-approved.json", "产品规格-签核版.json"):
        invocation = registry.invoke_for_evaluation(
            "structured-domain-handoff",
            _handoff_input(candidate_bundle={"source_file": filename, "claims": []}),
            context=handoff_context,
            expected_package_digest=handoff.package_digest,
            created_at=FIXED_TIME,
        )
        handoff_outputs.append(invocation.result)
    assert set(handoff_outputs[0]) == set(handoff_outputs[1])
    assert [item["action"] for item in handoff_outputs] == ["HANDOFF", "HANDOFF"]
    assert all(item["candidate_only"] is True and item["target_writes"] == 0 for item in handoff_outputs)

    launch = registry.load("enterprise-launch-readiness")
    launch_outputs = []
    for object_id in ("work:launch-approval-final.pdf", "work:上线审批单-最终版.pdf"):
        invocation = registry.invoke_for_evaluation(
            "enterprise-launch-readiness",
            _launch_input(object_id=object_id),
            context=InvocationContext(
                run_id="run:bilingual-launch",
                task_id="task:bilingual-launch",
                delegation_id="delegation:bilingual-launch",
                actor_id="evaluator:bilingual",
            ),
            expected_package_digest=launch.package_digest,
            created_at=FIXED_TIME,
        )
        launch_outputs.append(invocation.result)
    assert set(launch_outputs[0]) == set(launch_outputs[1])
    assert [item["action"] for item in launch_outputs] == ["REBASE", "REBASE"]
    assert all(item["candidate_only"] is True and item["target_writes"] == 0 for item in launch_outputs)


@pytest.mark.parametrize(
    "name",
    [
        "enterprise-quote-compose",
        "structured-domain-handoff",
        "enterprise-launch-readiness",
    ],
)
def test_all_packages_share_eight_partition_gates(name: str) -> None:
    registry = SkillPackageRegistry()

    receipt = SkillPackageEvaluator(registry).evaluate(
        name, _cases(name, registry), evaluated_at=FIXED_TIME
    )

    assert receipt["verdict"] == "CANARY"
    assert {item["partition"] for item in receipt["case_results"]} == set(PARTITIONS)
    assert len(receipt["case_results"]) == 8
    assert all(item["passed"] for item in receipt["case_results"])
    assert all(item["passed"] for item in receipt["gate_results"])
    assert {item["gate_id"] for item in receipt["gate_results"]} >= {
        "critical_security_failures",
        "target_writes",
        *(f"partition:{partition.lower()}" for partition in PARTITIONS),
    }
    _assert_schema_surface(receipt, "workspace-skill-evaluation-receipt.schema.json")


def test_security_partition_failure_is_a_release_veto() -> None:
    registry = SkillPackageRegistry()
    cases = list(_cases("structured-domain-handoff", registry))
    permission = next(index for index, case in enumerate(cases) if case.partition == "PERMISSION")
    original = cases[permission]
    cases[permission] = SkillEvaluationCase(
        case_id=original.case_id,
        partition=original.partition,
        public_input=original.public_input,
        expected_action="HANDOFF",
    )

    receipt = SkillPackageEvaluator(registry).evaluate(
        "structured-domain-handoff", cases, evaluated_at=FIXED_TIME
    )

    assert receipt["verdict"] == "QUARANTINED"
    assert next(
        gate for gate in receipt["gate_results"] if gate["gate_id"] == "critical_security_failures"
    )["passed"] is False


def test_release_authority_requalification_and_honest_rollback_decision() -> None:
    registry = SkillPackageRegistry()
    evaluator = SkillPackageEvaluator(registry)
    evaluation = evaluator.evaluate(
        "enterprise-quote-compose",
        _cases("enterprise-quote-compose", registry),
        evaluated_at=FIXED_TIME,
    )
    ledger = SkillReleaseLedger(registry, evaluator)
    with pytest.raises(IntegrityError, match="SKILL_RELEASE_AUTHORITY_DENIED"):
        ledger.transition(
            "enterprise-quote-compose",
            evaluation,
            to_state="EVALUATED",
            actor_id="agent:skill-curator",
            reason_codes=("SELF_PUBLISH_ATTEMPT",),
            created_at=FIXED_TIME,
        )
    for state in ("EVALUATED", "SHADOW", "CANARY"):
        ledger.transition(
            "enterprise-quote-compose",
            evaluation,
            to_state=state,
            actor_id=SKILL_REGISTRY_AUTHORITY,
            reason_codes=(f"QUALIFIED_FOR_{state}",),
            created_at=FIXED_TIME,
        )
    package = registry.load("enterprise-quote-compose")
    input_value = _quote_input(package.manifest["program_content_digest"])
    context = InvocationContext(
        run_id="run:quote-001",
        task_id="task:quote-001",
        delegation_id="delegation:quote-001:gtm",
        actor_id="worker:gtm-steward",
    )
    invocation = ledger.invoke(
        "enterprise-quote-compose",
        input_value,
        context=context,
        observed_dependencies=package.manifest["dependencies"],
        created_at=FIXED_TIME,
    )
    assert invocation.result["action"] == "APPLY_QUOTE"
    assert invocation.receipt["run_id"] == context.run_id
    assert invocation.receipt["task_id"] == context.task_id
    assert invocation.receipt["delegation_id"] == context.delegation_id
    assert invocation.receipt["package_digest"] == package.package_digest
    assert invocation.receipt["target_writes"] == 0
    _assert_schema_surface(
        invocation.receipt, "workspace-skill-invocation-receipt.schema.json"
    )

    drifted = dict(package.manifest["dependencies"])
    changed_ref = next(iter(drifted))
    drifted[changed_ref] = "sha256:" + "0" * 64
    requalification = ledger.mark_requalification(
        "enterprise-quote-compose",
        drifted,
        actor_id=SKILL_REGISTRY_AUTHORITY,
        created_at=FIXED_TIME,
    )
    assert requalification["to_state"] == "REQUALIFICATION_REQUIRED"
    assert requalification["changed_dependency_refs"] == [changed_ref]
    with pytest.raises(IntegrityError, match="SKILL_RELEASE_HEAD_NOT_CALLABLE"):
        ledger.invoke(
            "enterprise-quote-compose",
            input_value,
            context=context,
            observed_dependencies=package.manifest["dependencies"],
        )
    rollback = ledger.record_rollback_decision(
        "enterprise-quote-compose",
        actor_id=SKILL_REGISTRY_AUTHORITY,
        reason_codes=("DEPENDENCY_DRIFT_ROLLBACK",),
        created_at=FIXED_TIME,
    )
    assert rollback["to_state"] == "ROLLBACK_DECISION_RECORDED"
    assert rollback["effective_package_digest"] == package.package_digest
    assert rollback["predecessor_executable"] is False
    assert rollback["restoration_status"] == "NOT_RUN"
    _assert_schema_surface(rollback, "workspace-skill-release-receipt.schema.json")
    history = ledger.history
    assert [item["event_index"] for item in history] == list(range(1, len(history) + 1))
    assert all(
        history[index]["previous_receipt_digest"] == history[index - 1]["digest"]
        for index in range(1, len(history))
    )


def test_fresh_requalification_requires_new_evaluation_and_restored_dependencies() -> None:
    registry = SkillPackageRegistry()
    evaluator = SkillPackageEvaluator(registry)
    name = "enterprise-quote-compose"
    initial = evaluator.evaluate(name, _cases(name, registry), evaluated_at=FIXED_TIME)
    ledger = SkillReleaseLedger(registry, evaluator)
    for state in ("EVALUATED", "SHADOW", "CANARY"):
        ledger.transition(
            name,
            initial,
            to_state=state,
            actor_id=SKILL_REGISTRY_AUTHORITY,
            reason_codes=(f"INITIAL_{state}",),
            created_at=FIXED_TIME,
        )
    package = registry.load(name)
    drifted = dict(package.manifest["dependencies"])
    drifted[sorted(drifted)[0]] = "sha256:" + "0" * 64
    ledger.mark_requalification(
        name,
        drifted,
        actor_id=SKILL_REGISTRY_AUTHORITY,
        created_at=FIXED_TIME,
    )

    with pytest.raises(IntegrityError, match="SKILL_REQUALIFICATION_EVALUATION_NOT_FRESH"):
        ledger.transition(
            name,
            initial,
            to_state="EVALUATED",
            actor_id=SKILL_REGISTRY_AUTHORITY,
            reason_codes=("REPLAY_OLD_EVALUATION",),
            observed_dependencies=package.manifest["dependencies"],
            created_at="2026-08-26T00:00:01Z",
        )
    fresh = evaluator.evaluate(
        name,
        _cases(name, registry),
        evaluated_at="2026-08-26T00:00:01Z",
    )
    with pytest.raises(
        IntegrityError, match="SKILL_REQUALIFICATION_DEPENDENCIES_NOT_RESTORED"
    ):
        ledger.transition(
            name,
            fresh,
            to_state="EVALUATED",
            actor_id=SKILL_REGISTRY_AUTHORITY,
            reason_codes=("STALE_DEPENDENCY_OBSERVATION",),
            observed_dependencies=drifted,
            created_at="2026-08-26T00:00:01Z",
        )
    with pytest.raises(
        IntegrityError, match="SKILL_RELEASE_TRANSITION_INVALID:REQUALIFICATION_REQUIRED->SHADOW"
    ):
        ledger.transition(
            name,
            fresh,
            to_state="SHADOW",
            actor_id=SKILL_REGISTRY_AUTHORITY,
            reason_codes=("SKIP_REEVALUATED_STATE",),
            created_at="2026-08-26T00:00:01Z",
        )
    evaluated = ledger.transition(
        name,
        fresh,
        to_state="EVALUATED",
        actor_id=SKILL_REGISTRY_AUTHORITY,
        reason_codes=("FRESH_DEPENDENCIES_RESTORED",),
        observed_dependencies=package.manifest["dependencies"],
        created_at="2026-08-26T00:00:01Z",
    )
    assert evaluated["from_state"] == "REQUALIFICATION_REQUIRED"
    assert evaluated["evaluation_receipt_digest"] == fresh["digest"]


def test_schema_invalid_input_abstains_and_invalid_package_output_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SkillPackageRegistry()
    package = registry.load("enterprise-quote-compose")
    invalid_input = _quote_input(package.manifest["program_content_digest"])
    del invalid_input["dependency_result_digest"]
    abstained = registry.invoke_for_evaluation(
        "enterprise-quote-compose",
        invalid_input,
        context=InvocationContext(
            run_id="run:schema-input",
            task_id="task:schema-input",
            delegation_id="delegation:schema-input",
            actor_id="evaluator:schema-input",
        ),
        expected_package_digest=package.package_digest,
        created_at=FIXED_TIME,
    )
    assert abstained.result["action"] == "ABSTAIN"
    assert abstained.result["reason"] == "INPUT_SCHEMA_VALIDATION_FAILED"
    assert abstained.result["target_writes"] == 0

    original = SkillPackageRegistry._execute

    def invalid_output(
        self: SkillPackageRegistry,
        loaded: object,
        public_input: object,
    ) -> dict[str, object]:
        result = original(self, loaded, public_input)  # type: ignore[arg-type]
        return {**result, "undeclared_output": True}

    monkeypatch.setattr(SkillPackageRegistry, "_execute", invalid_output)
    with pytest.raises(IntegrityError, match="SKILL_OUTPUT_SCHEMA_VALIDATION_FAILED"):
        registry.invoke_for_evaluation(
            "enterprise-quote-compose",
            _quote_input(package.manifest["program_content_digest"]),
            context=InvocationContext(
                run_id="run:schema-output",
                task_id="task:schema-output",
                delegation_id="delegation:schema-output",
                actor_id="evaluator:schema-output",
            ),
            expected_package_digest=package.package_digest,
            created_at=FIXED_TIME,
        )


def test_quote_compose_requires_all_four_domain_results() -> None:
    registry = SkillPackageRegistry()
    package = registry.load("enterprise-quote-compose")
    public_input = _quote_input(package.manifest["program_content_digest"])
    domain_results = dict(public_input["domain_result_digests"])
    del domain_results["legal"]
    public_input["domain_result_digests"] = domain_results

    invocation = registry.invoke_for_evaluation(
        "enterprise-quote-compose",
        public_input,
        context=InvocationContext(
            run_id="run:missing-legal",
            task_id="task:missing-legal",
            delegation_id="delegation:missing-legal",
            actor_id="evaluator:coalition",
        ),
        expected_package_digest=package.package_digest,
        created_at=FIXED_TIME,
    )

    assert invocation.result["action"] == "ABSTAIN"
    assert invocation.result["reason"] == "INPUT_SCHEMA_VALIDATION_FAILED"
    assert invocation.result["target_writes"] == 0


def test_quote_compose_rejects_domain_result_substitution_and_echoes_valid_root() -> None:
    registry = SkillPackageRegistry()
    package = registry.load("enterprise-quote-compose")
    public_input = _quote_input(package.manifest["program_content_digest"])
    substituted = dict(public_input["domain_result_digests"])
    substituted["legal"] = substituted["product"]
    public_input["domain_result_digests"] = substituted

    rejected = registry.invoke_for_evaluation(
        "enterprise-quote-compose",
        public_input,
        context=InvocationContext(
            run_id="run:substituted-legal",
            task_id="task:substituted-legal",
            delegation_id="delegation:substituted-legal",
            actor_id="evaluator:coalition",
        ),
        expected_package_digest=package.package_digest,
        created_at=FIXED_TIME,
    )
    assert rejected.result["action"] == "ABSTAIN"
    assert rejected.result["reason"] == "INVALID_FOUR_DOMAIN_COALITION_ROOTS"
    assert rejected.result["target_writes"] == 0

    valid_input = _quote_input(package.manifest["program_content_digest"])
    accepted = registry.invoke_for_evaluation(
        "enterprise-quote-compose",
        valid_input,
        context=InvocationContext(
            run_id="run:valid-coalition",
            task_id="task:valid-coalition",
            delegation_id="delegation:valid-coalition",
            actor_id="evaluator:coalition",
        ),
        expected_package_digest=package.package_digest,
        created_at=FIXED_TIME,
    )
    assert accepted.result["action"] == "APPLY_QUOTE"
    assert accepted.result["coalition_result_binding_digest"] == valid_input[
        "coalition_result_binding_digest"
    ]
    assert accepted.result["domain_result_digests"] == valid_input[
        "domain_result_digests"
    ]


def test_resealed_package_still_rejects_network_schema_reference(tmp_path: Path) -> None:
    (tmp_path / "configs" / "workspace").mkdir(parents=True)
    shutil.copy2(
        ROOT / "configs" / "workspace" / "skill-registry.json",
        tmp_path / "configs" / "workspace" / "skill-registry.json",
    )
    shutil.copytree(ROOT / "skills", tmp_path / "skills")
    name = "enterprise-quote-compose"
    schema_path = tmp_path / "skills" / name / "input.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["candidate_program_digest_required"] = {
        "$ref": "https://attacker.invalid/schema.json"
    }
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    manifest_path = tmp_path / "skills" / name / "package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["resources"]["input_schema"]["sha256"] = (
        "sha256:" + hashlib.sha256(schema_path.read_bytes()).hexdigest()
    )
    manifest["manifest_digest"] = sha256_digest(
        {key: value for key, value in manifest.items() if key != "manifest_digest"}
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    registry_path = tmp_path / "configs" / "workspace" / "skill-registry.json"
    registry_value = json.loads(registry_path.read_text(encoding="utf-8"))
    next(
        item for item in registry_value["packages"] if item["name"] == name
    )["manifest_digest"] = manifest["manifest_digest"]
    registry_path.write_text(json.dumps(registry_value, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(IntegrityError, match="SKILL_SCHEMA_EXTERNAL_REF_FORBIDDEN"):
        SkillPackageRegistry(tmp_path).load(name)


def test_manifest_reseal_helper_updates_schema_resource_and_registry_digests(
    tmp_path: Path,
) -> None:
    (tmp_path / "configs" / "workspace").mkdir(parents=True)
    shutil.copy2(
        ROOT / "configs" / "workspace" / "skill-registry.json",
        tmp_path / "configs" / "workspace" / "skill-registry.json",
    )
    shutil.copytree(ROOT / "skills", tmp_path / "skills")
    name = "enterprise-quote-compose"
    before = SkillPackageRegistry(tmp_path).load(name)
    schema_path = tmp_path / "skills" / name / "input.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["description"] = "Controlled reseal test annotation"
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")

    receipt = reseal_skill_package(tmp_path, name)
    after = SkillPackageRegistry(tmp_path).load(name)

    assert receipt["status"] == "PASS"
    assert after.package_digest != before.package_digest
    assert after.manifest["dependencies"][after.manifest["input_schema_ref"]] == (
        after.resource_digests["input_schema"]
    )


@pytest.mark.parametrize(
    ("name", "public_input", "expected_action"),
    [
        ("structured-domain-handoff", _handoff_input(), "HANDOFF"),
        ("enterprise-launch-readiness", _launch_input(), "REBASE"),
    ],
)
def test_other_packages_are_released_and_invoked_under_the_same_control(
    name: str, public_input: dict[str, object], expected_action: str
) -> None:
    registry = SkillPackageRegistry()
    _, ledger = _qualify(name, registry)
    package = registry.load(name)
    context = InvocationContext(
        run_id=str(public_input.get("run_id", "run:shared-control")),
        task_id=str(public_input.get("task_id", "task:shared-control")),
        delegation_id=str(public_input.get("delegation_id", "delegation:shared-control")),
        actor_id="worker:bounded-caller",
    )

    invocation = ledger.invoke(
        name,
        public_input,
        context=context,
        observed_dependencies=package.manifest["dependencies"],
        created_at=FIXED_TIME,
    )

    assert invocation.result["action"] == expected_action
    assert invocation.result["candidate_only"] is True
    assert invocation.receipt["authorization_mode"] == "RELEASE"


def test_forged_evaluation_and_public_release_receipts_cannot_authorize() -> None:
    registry = SkillPackageRegistry()
    evaluator = SkillPackageEvaluator(registry)
    evaluation = evaluator.evaluate(
        "enterprise-quote-compose",
        _cases("enterprise-quote-compose", registry),
        evaluated_at=FIXED_TIME,
    )
    forged = json.loads(json.dumps(evaluation))
    ledger = SkillReleaseLedger(registry, evaluator)

    with pytest.raises(IntegrityError, match="SKILL_EVALUATION_RECEIPT_NOT_ISSUED"):
        ledger.transition(
            "enterprise-quote-compose",
            forged,
            to_state="EVALUATED",
            actor_id=SKILL_REGISTRY_AUTHORITY,
            reason_codes=("FORGED_EVALUATION",),
            created_at=FIXED_TIME,
        )

    for state in ("EVALUATED", "SHADOW", "CANARY"):
        ledger.transition(
            "enterprise-quote-compose",
            evaluation,
            to_state=state,
            actor_id=SKILL_REGISTRY_AUTHORITY,
            reason_codes=(f"QUALIFIED_FOR_{state}",),
            created_at=FIXED_TIME,
        )
    package = registry.load("enterprise-quote-compose")
    with pytest.raises(IntegrityError, match="SKILL_RELEASE_LEDGER_REQUIRED"):
        registry.invoke(
            "enterprise-quote-compose",
            _quote_input(package.manifest["program_content_digest"]),
            context=InvocationContext(
                run_id="run:forged",
                task_id="task:forged",
                delegation_id="delegation:forged",
                actor_id="worker:forged",
            ),
            expected_package_digest=package.package_digest,
            release_receipt=dict(ledger.head("enterprise-quote-compose") or {}),
            observed_dependencies=package.manifest["dependencies"],
            created_at=FIXED_TIME,
        )


def test_quote_security_uses_attack_fields_and_binds_tool_evidence() -> None:
    registry = SkillPackageRegistry()
    _, ledger = _qualify("enterprise-quote-compose", registry)
    package = registry.load("enterprise-quote-compose")
    context = InvocationContext(
        run_id="run:quote-attacks",
        task_id="task:quote-attacks",
        delegation_id="delegation:quote-attacks",
        actor_id="worker:gtm",
    )
    combined = {
        **_quote_input(package.manifest["program_content_digest"]),
        "permission_expansion": True,
        "prompt_injection": True,
    }
    denied = ledger.invoke(
        "enterprise-quote-compose",
        combined,
        context=context,
        observed_dependencies=package.manifest["dependencies"],
        created_at=FIXED_TIME,
    )
    assert denied.result["action"] == "DENY"
    assert denied.result["dependency_tool_receipt_digest"] == combined[
        "dependency_tool_receipt_digest"
    ]
    assert denied.result["dependency_result_digest"] == combined[
        "dependency_result_digest"
    ]
    zero_tool = {
        **_quote_input(package.manifest["program_content_digest"]),
        "dependency_tool_receipt_digest": "sha256:" + "0" * 64,
    }
    abstained = ledger.invoke(
        "enterprise-quote-compose",
        zero_tool,
        context=context,
        observed_dependencies=package.manifest["dependencies"],
        created_at=FIXED_TIME,
    )
    assert abstained.result["action"] == "ABSTAIN"
    assert abstained.result["reason"] == "INPUT_SCHEMA_VALIDATION_FAILED"


@pytest.mark.parametrize(
    ("attack_field", "expected_action"),
    [
        ("permission_expansion", "DENY"),
        ("request_restricted_source", "DENY"),
        ("target_write_requested", "DENY"),
        ("prompt_injection", "ABSTAIN"),
        ("malformed_input", "ABSTAIN"),
        ("deadline_expired", "ABSTAIN"),
        ("resource_exhausted", "ABSTAIN"),
        ("stale_input", "ABSTAIN"),
    ],
)
def test_quote_all_declared_attack_fields_fail_closed(
    attack_field: str, expected_action: str
) -> None:
    registry = SkillPackageRegistry()
    _, ledger = _qualify("enterprise-quote-compose", registry)
    package = registry.load("enterprise-quote-compose")
    public_input = {
        **_quote_input(package.manifest["program_content_digest"]),
        attack_field: True,
    }
    invocation = ledger.invoke(
        "enterprise-quote-compose",
        public_input,
        context=InvocationContext(
            run_id=f"run:quote:{attack_field}",
            task_id=f"task:quote:{attack_field}",
            delegation_id=f"delegation:quote:{attack_field}",
            actor_id="worker:gtm",
        ),
        observed_dependencies=package.manifest["dependencies"],
        created_at=FIXED_TIME,
    )
    assert invocation.result["action"] == expected_action
    assert invocation.result["target_writes"] == 0


@pytest.mark.parametrize(
    ("public_input", "expected_action", "expected_reason"),
    [
        (
            _handoff_input(delegation_task_digest="not-a-digest"),
            "ABSTAIN",
            "INPUT_SCHEMA_VALIDATION_FAILED",
        ),
        (
            _handoff_input(candidate_bundle={"private_secret": "value"}),
            "DENY",
            "SENSITIVE_MARKER_DETECTED",
        ),
        (
            _handoff_input(candidate_bundle={"probe": "CANARY-TOKEN"}),
            "DENY",
            "SENSITIVE_MARKER_DETECTED",
        ),
        (
            _handoff_input(candidate_bundle="raw-secret-text"),
            "ABSTAIN",
            "INPUT_SCHEMA_VALIDATION_FAILED",
        ),
    ],
)
def test_handoff_rejects_bad_digests_and_sensitive_candidate_content(
    public_input: dict[str, object], expected_action: str, expected_reason: str
) -> None:
    registry = SkillPackageRegistry()
    package = registry.load("structured-domain-handoff")
    invocation = registry.invoke_for_evaluation(
        "structured-domain-handoff",
        public_input,
        context=InvocationContext(
            run_id=str(public_input["run_id"]),
            task_id=str(public_input["task_id"]),
            delegation_id=str(public_input["delegation_id"]),
            actor_id="evaluator:attack",
        ),
        expected_package_digest=package.package_digest,
        created_at=FIXED_TIME,
    )
    assert invocation.result["action"] == expected_action
    assert invocation.result["reason"] == expected_reason


@pytest.mark.parametrize(
    "updates",
    [
        {"preview_receipt_digest": "sha256:" + "0" * 64},
        {"approval_receipt_digest": "sha256:" + "0" * 64},
        {"approval_receipt_digest": None},
    ],
)
def test_launch_abstains_without_nonzero_preview_and_approval_receipts(
    updates: dict[str, object]
) -> None:
    registry = SkillPackageRegistry()
    package = registry.load("enterprise-launch-readiness")
    public_input = _launch_input(**updates)
    invocation = registry.invoke_for_evaluation(
        "enterprise-launch-readiness",
        public_input,
        context=InvocationContext(
            run_id="run:launch-proof",
            task_id="task:launch-proof",
            delegation_id="delegation:launch-proof",
            actor_id="evaluator:attack",
        ),
        expected_package_digest=package.package_digest,
        created_at=FIXED_TIME,
    )
    assert invocation.result["action"] == "ABSTAIN"


def test_launch_abstains_when_approval_receipt_is_missing() -> None:
    registry = SkillPackageRegistry()
    package = registry.load("enterprise-launch-readiness")
    public_input = _launch_input()
    del public_input["approval_receipt_digest"]
    invocation = registry.invoke_for_evaluation(
        "enterprise-launch-readiness",
        public_input,
        context=InvocationContext(
            run_id="run:launch-missing-approval",
            task_id="task:launch-missing-approval",
            delegation_id="delegation:launch-missing-approval",
            actor_id="evaluator:attack",
        ),
        expected_package_digest=package.package_digest,
        created_at=FIXED_TIME,
    )
    assert invocation.result["action"] == "ABSTAIN"
    assert invocation.result["reason"] == "INPUT_SCHEMA_VALIDATION_FAILED"


def test_extra_dependency_duplicate_case_and_invocation_binding_fail_closed() -> None:
    registry = SkillPackageRegistry()
    evaluator = SkillPackageEvaluator(registry)
    cases = list(_cases("enterprise-quote-compose", registry))
    with pytest.raises(IntegrityError, match="SKILL_EVALUATION_DUPLICATE_CASE_ID"):
        evaluator.evaluate(
            "enterprise-quote-compose",
            (*cases, cases[0]),
            evaluated_at=FIXED_TIME,
        )

    _, ledger = _qualify("enterprise-quote-compose", registry)
    package = registry.load("enterprise-quote-compose")
    input_value = _quote_input(package.manifest["program_content_digest"])
    dependency_with_extra = {
        **package.manifest["dependencies"],
        "runtime:undeclared": sha256_digest({"runtime": "undeclared"}),
    }
    first_context = InvocationContext(
        run_id="run:binding",
        task_id="task:first",
        delegation_id="delegation:first",
        actor_id="worker:gtm",
    )
    with pytest.raises(IntegrityError, match="SKILL_DEPENDENCY_RECEIPT_STALE"):
        ledger.invoke(
            "enterprise-quote-compose",
            input_value,
            context=first_context,
            observed_dependencies=dependency_with_extra,
            created_at=FIXED_TIME,
        )
    first = ledger.invoke(
        "enterprise-quote-compose",
        input_value,
        context=first_context,
        observed_dependencies=package.manifest["dependencies"],
        created_at=FIXED_TIME,
    )
    second = ledger.invoke(
        "enterprise-quote-compose",
        input_value,
        context=InvocationContext(
            run_id="run:binding",
            task_id="task:second",
            delegation_id="delegation:second",
            actor_id="worker:gtm",
        ),
        observed_dependencies=package.manifest["dependencies"],
        created_at=FIXED_TIME,
    )
    assert first.receipt["id"] != second.receipt["id"]


def test_canonical_wheel_discovers_loads_and_invokes_resources_from_isolated_site(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    wheel = next(dist.glob("*.whl"))
    site = tmp_path / "site"
    run_dir = tmp_path / "run"
    site.mkdir()
    run_dir.mkdir()
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "orgrebase/_assets/configs/workspace/skill-registry.json" in names
        assert {
            f"orgrebase/skill_packages/{skill}/{resource}"
            for skill in (
                "enterprise-quote-compose",
                "structured-domain-handoff",
                "enterprise-launch-readiness",
            )
            for resource in (
                "SKILL.md",
                "references/zh-CN.md",
                "references/en.md",
                "contract.json",
                "program.json",
                "input.schema.json",
                "output.schema.json",
                "package.json",
            )
        } <= names
        assert {
            "orgrebase/skill_predecessors/enterprise-quote-compose/1.3.0/"
            + resource
            for resource in (
                "SKILL.md",
                "contract.json",
                "program.json",
                "input.schema.json",
                "output.schema.json",
                "package.json",
                "provenance.json",
            )
        } <= names
        assert not any("gold" in name for name in names if "orgrebase/skill_packages/" in name)
        archive.extractall(site)
    code = """
from pathlib import Path
import os
import orgrebase
from orgrebase.digest import sha256_digest
from orgrebase.workspace.skill_packages import InvocationContext, SkillPackageRegistry
from orgrebase.workspace.skill_rollback import load_frozen_predecessor

site = Path(os.environ['ORGREBASE_TEST_SITE']).resolve()
assert site in Path(orgrebase.__file__).resolve().parents
registry = SkillPackageRegistry()
assert registry.resource_mode == 'INSTALLED_WHEEL'
predecessor = load_frozen_predecessor(verify_retained_wheel=False)
assert predecessor.resource_mode == 'INSTALLED_WHEEL'
assert predecessor.manifest['version'] == '1.3.0'
found = registry.discover()
assert len(found) == 3
assert all(item['resource_mode'] == 'INSTALLED_WHEEL' for item in found)
packages = {
    item['name']: registry.load(item['name'], expected_package_digest=item['package_digest'])
    for item in found
}
inputs = {
    'enterprise-quote-compose': {
        'skill_partition': 'replay',
        'candidate_program_digest_required': packages['enterprise-quote-compose'].manifest['program_content_digest'],
        'dependency_tool_receipt_digest': sha256_digest({'tool': 'wheel'}),
        'dependency_result_digest': sha256_digest({'result': 'wheel'}),
        'coalition_result_binding_digest': sha256_digest({'coalition': 'wheel'}),
        'domain_result_digests': {
            domain: sha256_digest({'domain': domain, 'source': 'wheel'})
            for domain in ('product', 'legal', 'finance', 'gtm')
        },
    },
    'structured-domain-handoff': {
        'run_id': 'run:wheel',
        'task_id': 'task:wheel',
        'delegation_id': 'delegation:wheel',
        'delegation_task_digest': sha256_digest({'delegation': 'wheel'}),
        'context_projection_digest': sha256_digest({'projection': 'wheel'}),
        'candidate_bundle': {'claims': []},
    },
    'enterprise-launch-readiness': {
        'classification': 'AFFECTED_HARD',
        'object_id': 'work:wheel',
        'reason_code': 'WHEEL_SMOKE',
        'preview_receipt_digest': sha256_digest({'preview': 'wheel'}),
        'approval_receipt_digest': sha256_digest({'approval': 'wheel'}),
    },
}
for name, public_input in inputs.items():
    context = InvocationContext(
        run_id=str(public_input.get('run_id', 'run:wheel')),
        task_id=str(public_input.get('task_id', 'task:wheel')),
        delegation_id=str(public_input.get('delegation_id', 'delegation:wheel')),
        actor_id='evaluator:wheel-smoke',
    )
    invocation = registry.invoke_for_evaluation(
        name,
        public_input,
        context=context,
        expected_package_digest=packages[name].package_digest,
        created_at='2026-08-26T00:00:00Z',
    )
    assert invocation.result['target_writes'] == 0
    assert invocation.receipt['package_digest'] == packages[name].package_digest
print('CLEAN_WHEEL_THREE_SKILL_DISCOVERY_AND_INVOCATION_PASS')
"""
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(site),
            "PYTHONNOUSERSITE": "1",
            "ORGREBASE_TEST_SITE": str(site),
        }
    )
    probe = subprocess.run(
        [sys.executable, "-c", code],
        cwd=run_dir,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "CLEAN_WHEEL_THREE_SKILL_DISCOVERY_AND_INVOCATION_PASS"


def test_retained_lifecycle_qualifies_requalifies_and_invokes_from_installed_wheel(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "skill-evidence"
    dist = tmp_path / "canonical-dist"
    built = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    wheel = next(dist.glob("*.whl"))
    summary = run_skill_lifecycle_evidence(
        output_dir=evidence,
        run_id="run:retained-installed-wheel",
        task_id="task:retained-installed-wheel",
        delegation_id="delegation:retained-installed-wheel",
        root=ROOT,
        wheel_path=wheel,
        created_at=FIXED_TIME,
    )

    assert summary["runtime_resource_mode"] == "INSTALLED_WHEEL"
    assert summary["schema_resolution"] == "LOCAL_BUNDLED_ONLY"
    assert summary["network_schema_resolution"] is False
    assert summary["schema_resources_per_package"] == 2
    assert summary["statistically_meaningful_external_evaluation"] == "NOT_RUN"
    for name in (
        "enterprise-quote-compose",
        "structured-domain-handoff",
        "enterprise-launch-readiness",
    ):
        history = json.loads(
            (evidence / "releases" / f"{name}.json").read_text(encoding="utf-8")
        )
        assert [item["to_state"] for item in history] == [
            "EVALUATED",
            "SHADOW",
            "CANARY",
            "REQUALIFICATION_REQUIRED",
            "EVALUATED",
            "SHADOW",
            "CANARY",
        ]
    assert verify_skill_evidence(evidence)["status"] == "PASS"


def test_registry_and_new_receipt_schemas_are_valid_json() -> None:
    for path in (
        ROOT / "configs/workspace/skill-registry.json",
        ROOT / "schemas/workspace-skill-package-manifest.schema.json",
        ROOT / "schemas/workspace-skill-invocation-receipt.schema.json",
        ROOT / "schemas/workspace-skill-release-receipt.schema.json",
        ROOT / "schemas/workspace-skill-quote-compose-input.schema.json",
        ROOT / "schemas/workspace-skill-structured-domain-handoff-input.schema.json",
        ROOT / "schemas/workspace-skill-launch-readiness-input.schema.json",
        ROOT / "schemas/skill-package-manifest-v2.schema.json",
    ):
        assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
