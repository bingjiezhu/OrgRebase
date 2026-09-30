"""Optional, bounded GEPA search over one Finance guidance leaf.

GEPA proposes text and compares development cases.  The host consumer owns
Vertex dispatch, current ModelBudget reservations, provenance, and private
records.  This adapter has no credential, publication, holdout, or head-write
capability.  Importing this module does not import the optional GEPA package.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import io
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.model_budget import ModelBudget
from orgrebase.workspace.skill_evolution_v2 import (
    FinanceSkillHeadService,
    FinanceSkillResolution,
    ReviewedCapabilityPolicyV2,
    SkillContentBundleV2,
)

GEPA_VERSION = "0.1.4"
GEPA_RELEASE_COMMIT = "8b0ce6cd99a234f6b74daf37558a2ac0ce18f975"
GEPA_WHEEL_SHA256 = "12b971039599625c156d2231f6d72a29c31a22e9c237689459b5f1a3c353f532"
GEPA_API_FINGERPRINT = "sha256:1d5c98de507858ed9a377bf6e0574dea202887c1ebb87666d6c83e1cf0548ecc"
_COMPONENT = "change_explanation_instruction"


class FinanceOptimizerHold(IntegrityError):
    """Unknown call, drift, or exhausted budget stops the entire family."""


@dataclass(frozen=True)
class FinanceDevCase:
    case_ref: str
    case_revision: str
    independence_cluster_id: str
    partition: Literal["TRAIN", "DEV"]
    memory_snapshot_ref: str
    memory_snapshot_digest: str
    recall_manifest_ref: str
    recall_manifest_digest: str

    def __post_init__(self) -> None:
        if not all(
            (
                self.case_ref, self.case_revision, self.independence_cluster_id,
                self.memory_snapshot_ref, self.memory_snapshot_digest,
                self.recall_manifest_ref, self.recall_manifest_digest,
            )
        ):
            raise ValueError("FINANCE_OPTIMIZER_CASE_BINDING_REQUIRED")


@dataclass(frozen=True)
class FinanceDevObservation:
    status: Literal["VALID", "RESULT_UNKNOWN", "NOT_SENT", "HOLD"]
    score: float | None
    safety_passed: bool
    reason_code: str
    result_ref: str | None
    result_digest: str | None
    physical_attempts: int
    reserved_microusd: int
    evidence_scope: Literal["REAL_VERTEX", "CONTROLLED_TEST"]

    def __post_init__(self) -> None:
        if (
            self.physical_attempts < 0
            or self.reserved_microusd < 0
            or not self.reason_code
            or (self.status == "VALID" and (
                self.score is None or not 0 <= self.score <= 1
            ))
            or (self.status != "VALID" and self.score is not None)
            or (self.evidence_scope == "REAL_VERTEX" and self.status == "VALID" and (
                not self.result_ref or not self.result_digest or self.physical_attempts < 1
            ))
        ):
            raise ValueError("FINANCE_OPTIMIZER_OBSERVATION_INVALID")


@dataclass(frozen=True)
class FinanceOptimizerLimits:
    max_candidates: int = 8
    max_reflections: int = 8
    max_metric_calls: int = 150
    max_physical_attempts: int = 150
    max_cost_microusd: int = 1_000_000
    max_seconds: float = 1800
    max_attempts_per_case: int = 3
    max_output_tokens_per_call: int = 2048

    def __post_init__(self) -> None:
        if (
            not 1 <= self.max_candidates <= 8
            or not 0 <= self.max_reflections <= 8
            or not 1 <= self.max_metric_calls <= 150
            or not 1 <= self.max_physical_attempts <= 150
            or self.max_cost_microusd <= 0
            or not 0 < self.max_seconds <= 1800
            or not 1 <= self.max_attempts_per_case <= 3
            or not 0 < self.max_output_tokens_per_call <= 2048
        ):
            raise ValueError("FINANCE_OPTIMIZER_LIMITS_INVALID")


@dataclass(frozen=True)
class FinanceProposalObservation:
    status: Literal["VALID", "RESULT_UNKNOWN", "NOT_SENT", "HOLD"]
    instruction_text: str | None
    physical_attempts: int
    reserved_microusd: int
    reason_code: str
    result_ref: str | None = None
    result_digest: str | None = None


class DurableOptimizerBoundary(Protocol):
    """Server-owned StateStore family ledger; ambiguous intents remain blocked."""

    def reserve(
        self, *, family_id: str, trial_key: str, kind: str,
        candidate_digest: str, case_ref: str | None,
        allowance_calls: int, allowance_microusd: int,
        model_budget_digest: str,
    ) -> None: ...

    def observe(
        self, *, family_id: str, trial_key: str, status: str,
        physical_attempts: int, reserved_microusd: int,
        result_ref: str | None, result_digest: str | None,
    ) -> None: ...


@dataclass(frozen=True)
class FinanceOptimizerResult:
    status: Literal["CANDIDATE_ONLY", "DISABLED", "HOLD"]
    reason_code: str
    candidate_bundle: SkillContentBundleV2 | None
    parent_head_ref: str
    parent_head_digest: str
    parent_package_digest: str
    development_score: float | None
    evidence_scope: Literal["CONTROLLED_TEST", "REAL_VERTEX", "NONE"]
    metric_calls: int
    physical_attempts: int
    reserved_microusd: int
    proposer_calls: int
    gepa_version: str | None


def _load_pinned_gepa() -> tuple[Any, Any] | None:
    try:
        version = importlib.metadata.version("gepa")
    except importlib.metadata.PackageNotFoundError:
        return None
    if version != GEPA_VERSION:
        raise FinanceOptimizerHold("FINANCE_GEPA_VERSION_DRIFT")
    gepa = importlib.import_module("gepa")
    adapter_module = importlib.import_module("gepa.core.adapter")
    fingerprint = sha256_digest({
        "gepa_version": version,
        "optimize_params": list(inspect.signature(gepa.optimize).parameters),
        "evaluation_batch_fields": list(adapter_module.EvaluationBatch.__dataclass_fields__),
    })
    if fingerprint != GEPA_API_FINGERPRINT:
        raise FinanceOptimizerHold("FINANCE_GEPA_API_DRIFT")
    return gepa, adapter_module.EvaluationBatch


@dataclass
class _BudgetState:
    limits: FinanceOptimizerLimits
    model_budget: ModelBudget
    start: float = field(default_factory=time.monotonic)
    metric_calls: int = 0
    physical_attempts: int = 0
    reserved_microusd: int = 0
    reflections: int = 0
    proposals: int = 0
    evidence_scopes: set[str] = field(default_factory=set)

    def _time(self) -> None:
        if time.monotonic() - self.start > self.limits.max_seconds:
            raise FinanceOptimizerHold("FINANCE_OPTIMIZER_TIME_EXHAUSTED")

    def reserve(self, *, calls: int) -> int:
        self._time()
        if self.physical_attempts + calls > self.limits.max_physical_attempts:
            raise FinanceOptimizerHold("FINANCE_OPTIMIZER_CALL_BUDGET_EXHAUSTED")
        try:
            quote = self.model_budget.reserve(
                model_id="gemini-3.8-flash",
                calls=calls,
                max_output_tokens=self.limits.max_output_tokens_per_call,
            )
        except ValueError as exc:
            raise FinanceOptimizerHold("FINANCE_OPTIMIZER_PRICE_CONTRACT_INVALID") from exc
        cost = int(quote["reserved_microusd"])
        if self.reserved_microusd + cost > self.limits.max_cost_microusd:
            raise FinanceOptimizerHold("FINANCE_OPTIMIZER_COST_BUDGET_EXHAUSTED")
        self.physical_attempts += calls
        self.reserved_microusd += cost
        return cost

    def account(self, *, allowance_calls: int, allowance_cost: int,
                observed_calls: int, observed_cost: int, unknown: bool) -> None:
        if (
            observed_calls < 0 or observed_cost < 0
            or observed_calls > allowance_calls or observed_cost > allowance_cost
        ):
            raise FinanceOptimizerHold("FINANCE_OPTIMIZER_PROVIDER_USAGE_INVALID")
        if not unknown:
            self.physical_attempts -= allowance_calls - observed_calls
            self.reserved_microusd -= allowance_cost - observed_cost
        # Unknown retains the entire conservative reservation across recovery.


class FinanceGEPAAdapter:
    """One-field GEPA adapter with a server-supplied bounded V4 dev consumer."""

    propose_new_texts = None

    def __init__(
        self, *, resolution: FinanceSkillResolution,
        policy: ReviewedCapabilityPolicyV2,
        train_cases: tuple[FinanceDevCase, ...],
        dev_cases: tuple[FinanceDevCase, ...],
        evaluate_dev: Callable[[FinanceDevCase, SkillContentBundleV2, bool], FinanceDevObservation],
        propose_instruction: Callable[[str, Mapping[str, Sequence[Mapping[str, Any]]]], FinanceProposalObservation],
        validate_frozen_inputs: Callable[[FinanceDevCase | None], None],
        model_budget: ModelBudget,
        limits: FinanceOptimizerLimits,
        execution_scope: Literal["CONTROLLED_TEST", "REAL_VERTEX"] = "CONTROLLED_TEST",
        experiment_family_id: str | None = None,
        durable_boundary: DurableOptimizerBoundary | None = None,
    ) -> None:
        if (
            not train_cases or not dev_cases
            or any(item.partition != "TRAIN" for item in train_cases)
            or any(item.partition != "DEV" for item in dev_cases)
            or {item.independence_cluster_id for item in train_cases}
            & {item.independence_cluster_id for item in dev_cases}
            or len({item.case_ref for item in (*train_cases, *dev_cases)})
            != len(train_cases) + len(dev_cases)
            or model_budget.model_id != "gemini-3.8-flash"
        ):
            raise ValueError("FINANCE_OPTIMIZER_DEVELOPMENT_SPLIT_INVALID")
        if execution_scope == "REAL_VERTEX" and (
            not experiment_family_id or durable_boundary is None
        ):
            raise ValueError("FINANCE_OPTIMIZER_DURABLE_LEDGER_REQUIRED")
        self.resolution = resolution
        self.policy = policy
        self.train_cases = train_cases
        self.dev_cases = dev_cases
        self.evaluate_dev = evaluate_dev
        self.propose_instruction = propose_instruction
        self.validate_frozen_inputs = validate_frozen_inputs
        self.budget = _BudgetState(limits=limits, model_budget=model_budget)
        self.execution_scope = execution_scope
        self.experiment_family_id = experiment_family_id
        self.durable_boundary = durable_boundary
        self.model_budget_digest = sha256_digest(model_budget.model_dump(mode="json"))
        self._evaluation_batch_type: Any = None
        self._case_cache: dict[str, FinanceDevObservation] = {}

    def _durable_reserve(
        self, *, trial_key: str, kind: str, candidate_digest: str,
        case_ref: str | None, allowance_calls: int, allowance_cost: int,
    ) -> None:
        if self.execution_scope == "REAL_VERTEX":
            assert self.durable_boundary is not None
            assert self.experiment_family_id is not None
            self.durable_boundary.reserve(
                family_id=self.experiment_family_id, trial_key=trial_key,
                kind=kind, candidate_digest=candidate_digest, case_ref=case_ref,
                allowance_calls=allowance_calls,
                allowance_microusd=allowance_cost,
                model_budget_digest=self.model_budget_digest,
            )

    def _durable_observe(
        self, *, trial_key: str, status: str, physical_attempts: int,
        reserved_microusd: int, result_ref: str | None, result_digest: str | None,
    ) -> None:
        if self.execution_scope == "REAL_VERTEX":
            assert self.durable_boundary is not None
            assert self.experiment_family_id is not None
            self.durable_boundary.observe(
                family_id=self.experiment_family_id, trial_key=trial_key,
                status=status, physical_attempts=physical_attempts,
                reserved_microusd=reserved_microusd,
                result_ref=result_ref, result_digest=result_digest,
            )

    def _bundle(self, candidate: Mapping[str, str]) -> SkillContentBundleV2:
        if set(candidate) != {_COMPONENT} or not isinstance(candidate[_COMPONENT], str):
            raise FinanceOptimizerHold("FINANCE_OPTIMIZER_MUTATION_SURFACE_INVALID")
        instruction = candidate[_COMPONENT]
        bundle = (
            self.resolution.bundle
            if instruction == self.resolution.bundle.instruction_text
            else self.resolution.bundle.patched_instruction(
                instruction.encode("utf-8"),
                policy=self.policy,
                parent_head_ref=self.resolution.head_ref,
                parent_head_digest=self.resolution.head_digest,
            )
        )
        guidance = {
            "instruction_ref": "instructions/change-explanation.md",
            "instruction_text": bundle.instruction_text,
            "reference_ref": "references/change-explanation.md",
            "reference_text": bundle.reference_text,
        }
        if len(canonical_json(guidance).encode("utf-8")) > 4096:
            raise FinanceOptimizerHold("FINANCE_OPTIMIZER_GUIDANCE_BUDGET_EXCEEDED")
        return bundle

    def evaluate(self, batch: list[FinanceDevCase], candidate: dict[str, str],
                 capture_traces: bool = False) -> Any:
        if self._evaluation_batch_type is None:
            raise FinanceOptimizerHold("FINANCE_GEPA_NOT_LOADED")
        bundle = self._bundle(candidate)
        outputs: list[dict[str, Any]] = []
        scores: list[float] = []
        trajectories: list[dict[str, Any]] | None = [] if capture_traces else None
        for case in batch:
            if case not in (*self.train_cases, *self.dev_cases):
                raise FinanceOptimizerHold("FINANCE_OPTIMIZER_SEALED_CASE_FORBIDDEN")
            self.validate_frozen_inputs(case)
            self.budget.metric_calls += 1
            if self.budget.metric_calls > self.budget.limits.max_metric_calls:
                raise FinanceOptimizerHold("FINANCE_OPTIMIZER_METRIC_BUDGET_EXHAUSTED")
            trial_key = sha256_digest({
                "family_id": self.experiment_family_id or "CONTROLLED_TEST",
                "kind": "DEV_EVALUATION",
                "case_ref": case.case_ref,
                "case_revision": case.case_revision,
                "manifest_digest": case.recall_manifest_digest,
                "candidate_bundle_digest": bundle.digest,
            })
            observation = self._case_cache.get(trial_key)
            if observation is None:
                allowance_calls = self.budget.limits.max_attempts_per_case
                allowance_cost = self.budget.reserve(calls=allowance_calls)
                self._durable_reserve(
                    trial_key=trial_key, kind="DEV_EVALUATION",
                    candidate_digest=bundle.digest, case_ref=case.case_ref,
                    allowance_calls=allowance_calls, allowance_cost=allowance_cost,
                )
                observation = self.evaluate_dev(case, bundle, capture_traces)
                self._durable_observe(
                    trial_key=trial_key, status=observation.status,
                    physical_attempts=observation.physical_attempts,
                    reserved_microusd=observation.reserved_microusd,
                    result_ref=observation.result_ref,
                    result_digest=observation.result_digest,
                )
                unknown = observation.status == "RESULT_UNKNOWN"
                self.budget.account(
                    allowance_calls=allowance_calls,
                    allowance_cost=allowance_cost,
                    observed_calls=observation.physical_attempts,
                    observed_cost=observation.reserved_microusd,
                    unknown=unknown,
                )
                if observation.status == "VALID":
                    self._case_cache[trial_key] = observation
            self.budget.evidence_scopes.add(observation.evidence_scope)
            if (
                self.execution_scope == "REAL_VERTEX"
                and observation.evidence_scope != "REAL_VERTEX"
            ):
                raise FinanceOptimizerHold("FINANCE_OPTIMIZER_REAL_EVIDENCE_REQUIRED")
            if observation.status != "VALID" or not observation.safety_passed:
                raise FinanceOptimizerHold(
                    "FINANCE_OPTIMIZER_SAFETY_VETO" if not observation.safety_passed
                    else "FINANCE_OPTIMIZER_" + observation.status
                )
            outputs.append({
                "case_ref": case.case_ref,
                "candidate_bundle_digest": bundle.digest,
                "result_ref": observation.result_ref,
                "result_digest": observation.result_digest,
                "reason_code": observation.reason_code,
            })
            scores.append(float(observation.score))
            if trajectories is not None:
                # No business text, gold, source IDs or full model output.
                trajectories.append({
                    "case_ref_digest": sha256_digest(case.case_ref),
                    "reason_code": observation.reason_code,
                    "score": observation.score,
                })
        return self._evaluation_batch_type(
            outputs=outputs, scores=scores, trajectories=trajectories,
            num_metric_calls=len(batch),
        )

    def make_reflective_dataset(
        self, candidate: dict[str, str], eval_batch: Any,
        components_to_update: list[str],
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        self._bundle(candidate)
        if components_to_update != [_COMPONENT]:
            raise FinanceOptimizerHold("FINANCE_OPTIMIZER_MUTATION_SURFACE_INVALID")
        return {_COMPONENT: list(eval_batch.trajectories or ())}

    def _propose(
        self, candidate: dict[str, str], reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: list[str], *, metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, str]:
        del metadata
        if components_to_update != [_COMPONENT]:
            raise FinanceOptimizerHold("FINANCE_OPTIMIZER_MUTATION_SURFACE_INVALID")
        self.validate_frozen_inputs(None)
        self.budget.reflections += 1
        self.budget.proposals += 1
        if (
            self.budget.reflections > self.budget.limits.max_reflections
            or self.budget.proposals >= self.budget.limits.max_candidates
        ):
            raise FinanceOptimizerHold("FINANCE_OPTIMIZER_CANDIDATE_BUDGET_EXHAUSTED")
        allowance_calls = self.budget.limits.max_attempts_per_case
        allowance_cost = self.budget.reserve(calls=allowance_calls)
        trial_key = sha256_digest({
            "family_id": self.experiment_family_id or "CONTROLLED_TEST",
            "kind": "REFLECTION",
            "ordinal": self.budget.reflections,
            "candidate_bundle_digest": self._bundle(candidate).digest,
        })
        self._durable_reserve(
            trial_key=trial_key, kind="REFLECTION",
            candidate_digest=self._bundle(candidate).digest, case_ref=None,
            allowance_calls=allowance_calls, allowance_cost=allowance_cost,
        )
        observed = self.propose_instruction(candidate[_COMPONENT], reflective_dataset)
        self._durable_observe(
            trial_key=trial_key, status=observed.status,
            physical_attempts=observed.physical_attempts,
            reserved_microusd=observed.reserved_microusd,
            result_ref=observed.result_ref,
            result_digest=observed.result_digest,
        )
        self.budget.account(
            allowance_calls=allowance_calls,
            allowance_cost=allowance_cost,
            observed_calls=observed.physical_attempts,
            observed_cost=observed.reserved_microusd,
            unknown=observed.status == "RESULT_UNKNOWN",
        )
        if observed.status != "VALID" or observed.instruction_text is None:
            raise FinanceOptimizerHold("FINANCE_OPTIMIZER_PROPOSAL_" + observed.status)
        proposed = {_COMPONENT: observed.instruction_text}
        self._bundle(proposed)
        return proposed

    def optimize(self) -> FinanceOptimizerResult:
        self.validate_frozen_inputs(None)
        if self.resolution.qualification_status not in {"UNQUALIFIED", "QUALIFIED"}:
            raise FinanceOptimizerHold("FINANCE_OPTIMIZER_PARENT_INVALID")
        loaded = _load_pinned_gepa()
        if loaded is None:
            return FinanceOptimizerResult(
                status="DISABLED", reason_code="GEPA_EXTRA_NOT_INSTALLED",
                candidate_bundle=None, parent_head_ref=self.resolution.head_ref,
                parent_head_digest=self.resolution.head_digest,
                parent_package_digest=self.resolution.package_digest,
                development_score=None, evidence_scope="NONE", metric_calls=0,
                physical_attempts=0, reserved_microusd=0, proposer_calls=0,
                gepa_version=None,
            )
        gepa, self._evaluation_batch_type = loaded
        # GEPA's valset participates in repeated search.  Both sets here are
        # adaptive development data; sealed holdout is absent from this API.
        hidden_log = io.StringIO()
        try:
            with redirect_stdout(hidden_log), redirect_stderr(hidden_log):
                result = gepa.optimize(
                    seed_candidate={_COMPONENT: self.resolution.bundle.instruction_text},
                    trainset=list(self.train_cases), valset=list(self.dev_cases),
                    adapter=self, reflection_lm=None,
                    custom_candidate_proposer=self._propose,
                    candidate_selection_strategy="current_best",
                    reflection_minibatch_size=min(2, len(self.train_cases)),
                    skip_perfect_score=True, perfect_score=1.0,
                    use_merge=False,
                    max_metric_calls=self.budget.limits.max_metric_calls,
                    display_progress_bar=False, use_wandb=False, use_mlflow=False,
                    cache_evaluation=False, track_best_outputs=False,
                    run_dir=None, seed=7,
                )
            best = self._bundle(result.best_candidate)
            scope = "REAL_VERTEX" if self.execution_scope == "REAL_VERTEX" else "CONTROLLED_TEST"
            return FinanceOptimizerResult(
                status="CANDIDATE_ONLY", reason_code="DEV_SEARCH_ONLY_NOT_QUALIFIED",
                candidate_bundle=best, parent_head_ref=self.resolution.head_ref,
                parent_head_digest=self.resolution.head_digest,
                parent_package_digest=self.resolution.package_digest,
                development_score=float(result.val_aggregate_scores[result.best_idx]),
                evidence_scope=scope,
                metric_calls=self.budget.metric_calls,
                physical_attempts=self.budget.physical_attempts,
                reserved_microusd=self.budget.reserved_microusd,
                proposer_calls=self.budget.proposals, gepa_version=GEPA_VERSION,
            )
        except FinanceOptimizerHold as exc:
            return FinanceOptimizerResult(
                status="HOLD", reason_code=str(exc), candidate_bundle=None,
                parent_head_ref=self.resolution.head_ref,
                parent_head_digest=self.resolution.head_digest,
                parent_package_digest=self.resolution.package_digest,
                development_score=None, evidence_scope="NONE",
                metric_calls=self.budget.metric_calls,
                physical_attempts=self.budget.physical_attempts,
                reserved_microusd=self.budget.reserved_microusd,
                proposer_calls=self.budget.proposals, gepa_version=GEPA_VERSION,
            )
        finally:
            hidden_log.close()


def make_finance_optimizer(
    *, head: FinanceSkillHeadService, train_cases: tuple[FinanceDevCase, ...],
    dev_cases: tuple[FinanceDevCase, ...],
    evaluate_dev: Callable[[FinanceDevCase, SkillContentBundleV2, bool], FinanceDevObservation],
    propose_instruction: Callable[[str, Mapping[str, Sequence[Mapping[str, Any]]]], FinanceProposalObservation],
    validate_frozen_inputs: Callable[[FinanceDevCase | None], None],
    model_budget: ModelBudget, limits: FinanceOptimizerLimits,
    execution_scope: Literal["CONTROLLED_TEST", "REAL_VERTEX"] = "CONTROLLED_TEST",
    experiment_family_id: str | None = None,
    durable_boundary: DurableOptimizerBoundary | None = None,
) -> FinanceGEPAAdapter:
    resolution = head.resolve()
    return FinanceGEPAAdapter(
        resolution=resolution, policy=head.policy,
        train_cases=train_cases, dev_cases=dev_cases,
        evaluate_dev=evaluate_dev, propose_instruction=propose_instruction,
        validate_frozen_inputs=validate_frozen_inputs,
        model_budget=model_budget, limits=limits,
        execution_scope=execution_scope,
        experiment_family_id=experiment_family_id,
        durable_boundary=durable_boundary,
    )
