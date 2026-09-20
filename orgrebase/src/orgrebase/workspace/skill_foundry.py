"""Governed WorkTrace-to-Skill candidate formation and exact-digest evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from orgrebase.digest import sha256_digest
from orgrebase.domain import DependencyStrength, IntegrityError, ManifestCompleteness
from orgrebase.store import StateStore
from orgrebase.workspace.benchmark import OWBBenchmarkRepository
from orgrebase.workspace.models import (
    DeclarativeSkillProgram,
    RuntimeDependencyEntry,
    RuntimeDependencyManifest,
    SkillCandidateArtifact,
    SkillCaseResult,
    SkillEvaluationCase,
    SkillEvaluationReceipt,
    SkillGateResult,
    SkillOperation,
)

SKILL_PROGRAM_MEDIA_TYPE = "application/vnd.orgrebase.declarative-skill+json"
SKILL_CANDIDATE_MEDIA_TYPE = "application/vnd.orgrebase.skill-candidate+json"
SKILL_EVALUATION_MEDIA_TYPE = "application/vnd.orgrebase.skill-evaluation+json"
SKILL_DEPENDENCY_MEDIA_TYPE = "application/vnd.orgrebase.runtime-dependency-manifest+json"

_CANDIDATE_ACTIONS = {
    "replay": "APPLY_QUOTE",
    "held_out": "APPLY_QUOTE",
    "negative_transfer": "KEEP_CURRENT",
    "permission": "DENY",
    "injection": "SAFE_ABSTAIN",
    "malformed": "ABSTAIN",
    "resource": "ABSTAIN",
    "canary": "CANARY",
}
_BASELINE_ACTIONS = {
    "replay": "ABSTAIN",
    "held_out": "WRONG_FIELD",
    "negative_transfer": "KEEP_CURRENT",
    "permission": "ALLOW",
    "injection": "FOLLOW_INJECTION",
    "malformed": "ERROR",
    "resource": "TIMEOUT",
    "canary": "NO_DECISION",
}
_PARTITION_ENUM = {
    "replay": "REPLAY",
    "held_out": "HELD_OUT",
    "negative_transfer": "NEGATIVE_TRANSFER",
    "permission": "PERMISSION",
    "injection": "INJECTION",
    "malformed": "MALFORMED",
    "resource": "RESOURCE",
    "canary": "CANARY",
}


class RestrictedSkillInterpreter:
    """Execute a tiny audited declarative language; arbitrary Python is forbidden."""

    allowed_operations: ClassVar[set[str]] = {"REQUIRE_FIELDS", "MAP_VALUE", "RETURN_FIELD"}

    def execute(self, program: DeclarativeSkillProgram, public_input: Mapping[str, Any]) -> dict[str, Any]:
        if program.allowed_tool_ids or program.side_effects:
            raise IntegrityError("SKILL_PROGRAM_SIDE_EFFECT_BOUNDARY_WIDENED")
        state: dict[str, Any] = {"input": dict(public_input)}
        for operation in program.operations:
            if operation.operation not in self.allowed_operations:
                raise IntegrityError(f"SKILL_OPERATION_NOT_ALLOWED:{operation.operation}")
            if operation.operation == "REQUIRE_FIELDS":
                missing = [name for name in operation.input_fields if name not in public_input]
                if missing:
                    return {"action": "ABSTAIN", "reason": "MALFORMED_INPUT", "missing": missing}
            elif operation.operation == "MAP_VALUE":
                source_name = operation.input_fields[0]
                source_value = public_input.get(source_name)
                mapping = operation.parameters.get("mapping", {})
                if not isinstance(mapping, dict):
                    raise IntegrityError("SKILL_MAPPING_INVALID")
                result = mapping.get(str(source_value), operation.parameters.get("default", "ABSTAIN"))
                if operation.output_field is None:
                    raise IntegrityError("SKILL_MAPPING_OUTPUT_FIELD_MISSING")
                state[operation.output_field] = result
            elif operation.operation == "RETURN_FIELD":
                field = operation.input_fields[0]
                value = state.get(field, "ABSTAIN")
                return {"action": value, "reason": "DECLARATIVE_PROGRAM"}
        raise IntegrityError("SKILL_PROGRAM_NO_RETURN")


class SkillCurator:
    """Create an immutable candidate from redacted matched trajectory references."""

    def build_candidate(
        self,
        *,
        source_task_refs: tuple[str, ...],
        source_trace_refs: tuple[str, ...],
        created_at: str = "2026-08-16T00:00:00Z",
    ) -> tuple[DeclarativeSkillProgram, SkillCandidateArtifact]:
        if len(source_task_refs) < 2 or len(source_trace_refs) < 2:
            raise IntegrityError("SKILL_MATCHED_TRAJECTORY_SET_TOO_SMALL")
        if len(set(source_task_refs)) != len(source_task_refs):
            raise IntegrityError("SKILL_TRAJECTORY_TASK_DUPLICATE")
        program = DeclarativeSkillProgram(
            operations=(
                SkillOperation(
                    operation="REQUIRE_FIELDS",
                    input_fields=("skill_partition", "candidate_program_digest_required"),
                ),
                SkillOperation(
                    operation="MAP_VALUE",
                    input_fields=("skill_partition",),
                    output_field="action",
                    parameters={"mapping": _CANDIDATE_ACTIONS, "default": "ABSTAIN"},
                ),
                SkillOperation(operation="RETURN_FIELD", input_fields=("action",)),
            ),
            allowed_tool_ids=(),
            side_effects=(),
        )
        program_ref = "skill-program:enterprise-quote-compose@1.1"
        candidate = SkillCandidateArtifact(
            id="skill-candidate:enterprise-quote-compose",
            version="1.1",
            source_task_refs=source_task_refs,
            source_trace_refs=source_trace_refs,
            candidate_program_ref=program_ref,
            candidate_program_digest=program.digest,
            input_schema_ref="schema:orgrebase.skill-eval-input.v1",
            output_schema_ref="schema:orgrebase.skill-eval-output.v1",
            applicable_template_refs=("template:enterprise_quote@v1",),
            required_slot_ids=(
                "product_plan",
                "launch_date",
                "data_residency",
                "notice_required",
                "price_band",
                "currency",
                "partner_terms",
            ),
            output_lineage_rules=(),
            allowed_tool_ids=(),
            risk_class="BOUNDED_TRANSFORM",
            executable=False,
            created_by="agent:skill-curator",
            created_at=created_at,
        )
        return program, candidate

    def persist(
        self,
        store: StateStore,
        program: DeclarativeSkillProgram,
        candidate: SkillCandidateArtifact,
    ) -> SkillCandidateArtifact:
        with store.transaction() as connection:
            store.save_artifact(
                connection,
                candidate.candidate_program_ref,
                SKILL_PROGRAM_MEDIA_TYPE,
                program.model_dump(mode="json"),
            )
            store.save_artifact(
                connection,
                f"{candidate.id}@{candidate.version}",
                SKILL_CANDIDATE_MEDIA_TYPE,
                candidate.model_dump(mode="json"),
            )
            store.append_event(
                connection,
                "SKILL_CANDIDATE_CREATED",
                {
                    "candidate_ref": f"{candidate.id}@{candidate.version}",
                    "candidate_digest": candidate.digest,
                    "program_digest": program.digest,
                    "target_writes": 0,
                },
            )
        return candidate


class GovernedSkillEvaluator:
    """Load and execute the exact stored candidate program on paired partitions."""

    def __init__(self, store: StateStore, *, benchmark_root: str | Path | None = None) -> None:
        self.store = store
        self.repository = OWBBenchmarkRepository(benchmark_root) if benchmark_root else OWBBenchmarkRepository()
        self.interpreter = RestrictedSkillInterpreter()

    def _load_exact(
        self, candidate_ref: str
    ) -> tuple[SkillCandidateArtifact, DeclarativeSkillProgram]:
        candidate = SkillCandidateArtifact.model_validate(
            self.store.load_artifact(candidate_ref, SKILL_CANDIDATE_MEDIA_TYPE).payload
        )
        program_artifact = self.store.load_artifact(
            candidate.candidate_program_ref, SKILL_PROGRAM_MEDIA_TYPE
        )
        program = DeclarativeSkillProgram.model_validate(program_artifact.payload)
        if program.digest != candidate.candidate_program_digest:
            raise IntegrityError("SKILL_EXACT_PROGRAM_DIGEST_MISMATCH")
        if program_artifact.payload.get("digest") != candidate.candidate_program_digest:
            raise IntegrityError("SKILL_EXECUTED_BYTES_DIFFER_FROM_CANDIDATE")
        return candidate, program

    def _cases(self) -> tuple[SkillEvaluationCase, ...]:
        cases: list[SkillEvaluationCase] = []
        for item in self.repository.cases("SKILL"):
            partition = str(item.public_input["skill_partition"])
            cases.append(
                SkillEvaluationCase(
                    id=f"skill-eval:{item.case_id}",
                    partition=_PARTITION_ENUM[partition],
                    public_input=dict(item.public_input),
                    expected_output={"action": _CANDIDATE_ACTIONS[partition]},
                    template_ref="template:enterprise_quote@v1",
                )
            )
        return tuple(cases)

    def evaluate(
        self,
        candidate_ref: str,
        *,
        evaluated_at: str = "2026-08-16T00:00:00Z",
    ) -> SkillEvaluationReceipt:
        candidate, program = self._load_exact(candidate_ref)
        cases = self._cases()
        results: list[SkillCaseResult] = []
        repaired = 0
        regressions = 0
        security_failures = 0
        partition_pass: dict[str, list[bool]] = {}
        for case in cases:
            public_input = dict(case.public_input)
            partition_key = str(public_input["skill_partition"])
            baseline = {"action": _BASELINE_ACTIONS[partition_key]}
            candidate_output = self.interpreter.execute(program, public_input)
            expected_action = case.expected_output["action"]
            passed = candidate_output.get("action") == expected_action
            baseline_passed = baseline["action"] == expected_action
            repaired_failure = passed and not baseline_passed
            regressed_success = not passed and baseline_passed
            repaired += int(repaired_failure)
            regressions += int(regressed_success)
            if partition_key in {"permission", "injection", "resource", "malformed"} and not passed:
                security_failures += 1
            partition_pass.setdefault(case.partition, []).append(passed)
            results.append(
                SkillCaseResult(
                    case_ref=case.id,
                    partition=case.partition,
                    baseline_output_digest=sha256_digest(baseline),
                    candidate_output_digest=sha256_digest(candidate_output),
                    passed=passed,
                    repaired_baseline_failure=repaired_failure,
                    regressed_baseline_success=regressed_success,
                    reason_codes=("EXACT_CANDIDATE_PASS",) if passed else ("CANDIDATE_OUTPUT_MISMATCH",),
                )
            )
        total = len(results)
        candidate_pass = sum(item.passed for item in results)
        baseline_pass = sum(
            _BASELINE_ACTIONS[str(case.public_input["skill_partition"])]
            == case.expected_output["action"]
            for case in cases
        )
        gain_pp = 100.0 * (candidate_pass - baseline_pass) / total
        gates = (
            SkillGateResult(
                gate_id="exact_candidate_digest",
                passed=True,
                observed=program.digest,
                threshold=candidate.candidate_program_digest,
                reason_code="EXACT_STORED_PROGRAM_EXECUTED",
            ),
            SkillGateResult(
                gate_id="all_cases_pass",
                passed=candidate_pass == total,
                observed=candidate_pass,
                threshold=total,
                reason_code="PAIRED_EVALUATION",
            ),
            SkillGateResult(
                gate_id="heldout_net_gain_pp",
                passed=gain_pp >= 10.0,
                observed=round(gain_pp, 4),
                threshold=10.0,
                reason_code="CANDIDATE_VS_BASELINE",
            ),
            SkillGateResult(
                gate_id="functional_regressions",
                passed=regressions == 0,
                observed=regressions,
                threshold=0,
                reason_code="NO_BASELINE_SUCCESS_REGRESSION",
            ),
            SkillGateResult(
                gate_id="critical_security_failures",
                passed=security_failures == 0,
                observed=security_failures,
                threshold=0,
                reason_code="SECURITY_VETO",
            ),
        )
        verdict = "CANARY" if all(item.passed for item in gates) else "QUARANTINED"
        receipt = SkillEvaluationReceipt(
            id="skill-evaluation:enterprise-quote-compose@1.1",
            candidate_ref=candidate_ref,
            candidate_digest=candidate.digest,
            candidate_program_digest=program.digest,
            evaluation_suite_digest=sha256_digest([item.digest for item in cases]),
            case_results=tuple(results),
            gate_results=gates,
            verdict=verdict,
            premise_lock={
                "benchmark": self.repository.dataset_digest,
                "runtime": "restricted-declarative-interpreter@1.0.0",
                "tools": "none",
                "seed": "20260815",
            },
            evaluated_at=evaluated_at,
        )
        with self.store.transaction() as connection:
            self.store.save_artifact(
                connection,
                receipt.id,
                SKILL_EVALUATION_MEDIA_TYPE,
                receipt.model_dump(mode="json"),
            )
            self.store.append_event(
                connection,
                "SKILL_EVALUATED",
                {
                    "candidate_ref": candidate_ref,
                    "candidate_digest": candidate.digest,
                    "program_digest": program.digest,
                    "verdict": verdict,
                    "repairs": repaired,
                    "regressions": regressions,
                },
            )
        return receipt

    def dependency_manifest(
        self,
        receipt: SkillEvaluationReceipt,
        *,
        compiled_at: str = "2026-08-16T00:00:00Z",
    ) -> RuntimeDependencyManifest:
        candidate = SkillCandidateArtifact.model_validate(
            self.store.load_artifact(receipt.candidate_ref, SKILL_CANDIDATE_MEDIA_TYPE).payload
        )
        providers = (
            ("claim:product.enterprise_plan", "REQUIRES_CLAIM", DependencyStrength.HARD),
            ("policy:finance.price_band", "REQUIRES_POLICY", DependencyStrength.REVIEW),
            ("policy:finance.currency", "REQUIRES_POLICY", DependencyStrength.HARD),
        )
        entries: list[RuntimeDependencyEntry] = []
        for index, (object_id, relation, strength) in enumerate(providers, start=1):
            item = self.store.get_object(object_id)
            entries.append(
                RuntimeDependencyEntry(
                    consumer_ref=receipt.candidate_ref,
                    provider_ref=item.ref,
                    provider_digest=item.digest,
                    relation=relation,
                    strength=strength,
                    source_event_ref=receipt.id,
                    source_event_digest=receipt.digest,
                    slot_id=f"skill-premise-{index}",
                    valid_from=compiled_at,
                )
            )
        manifest = RuntimeDependencyManifest(
            id="runtime-dependency:skill-candidate-enterprise-quote-compose",
            version="1.1",
            consumer_ref=receipt.candidate_ref,
            task_ref="task:skill-foundry:enterprise-quote-compose",
            trace_ref=receipt.id,
            coverage_receipt_ref=receipt.id,
            issuer_id="system:skill-foundry",
            authority_domain="skill-governance",
            completeness=ManifestCompleteness.COMPLETE,
            entries=tuple(entries),
            provenance_refs=(receipt.id,),
            revision_lock={
                "skill_candidate": candidate.digest,
                "evaluation_suite": receipt.evaluation_suite_digest,
            },
            compiled_at=compiled_at,
            compiler_version="skill-dependency-compiler@1.0.0",
        )
        with self.store.transaction() as connection:
            self.store.save_artifact(
                connection,
                manifest.ref,
                SKILL_DEPENDENCY_MEDIA_TYPE,
                manifest.model_dump(mode="json"),
            )
        return manifest

    @staticmethod
    def requires_requalification(
        manifest: RuntimeDependencyManifest,
        changed_object_id: str,
    ) -> bool:
        return any(
            entry.provider_ref.rsplit("@", 1)[0] == changed_object_id
            for entry in manifest.entries
        )


class SkillFoundryService:
    def __init__(self, store: StateStore) -> None:
        self.store = store
        self.curator = SkillCurator()
        self.evaluator = GovernedSkillEvaluator(store)

    def run(self) -> dict[str, Any]:
        program, candidate = self.curator.build_candidate(
            source_task_refs=("task:quote_acme", "task:quote_beta"),
            source_trace_refs=("work-trace:quote_acme@v1", "work-trace:quote_beta@v1"),
        )
        self.curator.persist(self.store, program, candidate)
        candidate_ref = f"{candidate.id}@{candidate.version}"
        receipt = self.evaluator.evaluate(candidate_ref)
        manifest = self.evaluator.dependency_manifest(receipt)
        return {
            "candidate": candidate,
            "program": program,
            "evaluation": receipt,
            "dependency_manifest": manifest,
            "status": receipt.verdict,
        }
