"""Optional GEPA API and budget bounds; scripted scores are not quality proof."""

from __future__ import annotations

import importlib.metadata
import time
import urllib.request
from dataclasses import replace
from types import SimpleNamespace

import pytest

from orgrebase.auth import Principal, request_principal
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.model_budget import ModelBudget
from orgrebase.workspace.skill_evolution_v2 import FinanceSkillHeadService
from orgrebase.workspace.skill_optimizer_gepa import (
    FinanceDevCase,
    FinanceDevObservation,
    FinanceOptimizerHold,
    FinanceOptimizerLimits,
    FinanceProposalObservation,
    make_finance_optimizer,
)


def _budget():
    return ModelBudget(
        schema_version="orgrebase.model-budget.v1",
        model_id="gemini-3.8-flash",
        context_window_tokens=1_000_000,
        input_usd_per_million_ceiling="0.00000001",
        output_usd_per_million_ceiling="0.00000001",
        max_preview_usd="100.00000000",
        price_source_ref="test://gepa-scripted-price-contract",
        valid_until="2030-01-01T00:00:00Z",
    )


def _head(store):
    token = request_principal.set(Principal(
        issuer="https://issuer.example.test", subject="reviewer",
        tenant_id="tenant:gepa-test", actor_id="reviewer:finance",
        roles=frozenset({"governor"}), expires_at=int(time.time()) + 60,
    ))
    try:
        service = FinanceSkillHeadService(store)
        service.bootstrap()
        return service
    finally:
        request_principal.reset(token)


def _cases():
    def case(number, partition):
        return FinanceDevCase(
            case_ref=f"case:{number}", case_revision=sha256_digest(number),
            independence_cluster_id=f"cluster:{number}", partition=partition,
            memory_snapshot_ref="snapshot:dev", memory_snapshot_digest=sha256_digest("snapshot"),
            recall_manifest_ref=f"manifest:{number}", recall_manifest_digest=sha256_digest(number),
        )
    return (case(1, "TRAIN"), case(2, "TRAIN")), (case(3, "DEV"), case(4, "DEV"))


def _scripted_evaluate(case, bundle, capture_trace):
    del case, capture_trace
    return FinanceDevObservation(
        status="VALID", score=float("Check source coverage" in bundle.instruction_text),
        safety_passed=True, reason_code="SCRIPTED_MECHANISM_ONLY",
        result_ref=None, result_digest=None,
        physical_attempts=0, reserved_microusd=0,
        evidence_scope="CONTROLLED_TEST",
    )


def _scripted_propose(current, reflective_dataset):
    del reflective_dataset
    return FinanceProposalObservation(
        status="VALID", instruction_text=current + "Check source coverage.\n",
        physical_attempts=0, reserved_microusd=0, reason_code="SCRIPTED_PROPOSER",
    )


def _adapter(store):
    train, dev = _cases()
    return make_finance_optimizer(
        head=_head(store), train_cases=train, dev_cases=dev,
        evaluate_dev=_scripted_evaluate, propose_instruction=_scripted_propose,
        validate_frozen_inputs=lambda _case: None,
        model_budget=_budget(),
        limits=FinanceOptimizerLimits(
            max_candidates=3, max_reflections=2, max_metric_calls=14,
            max_physical_attempts=50, max_cost_microusd=100_000,
        ),
    )


def test_optional_optimizer_absence_keeps_current_head_and_normal_runtime(tmp_path, monkeypatch):
    from orgrebase.workspace import skill_optimizer_gepa as module

    monkeypatch.setattr(module, "_load_pinned_gepa", lambda: None)
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *_args, **_kwargs: pytest.fail("disabled optimizer must not send a request"),
    )
    with StateStore(tmp_path / "optional.sqlite3", tenant_id="tenant:gepa-test") as store:
        adapter = _adapter(store)
        adapter.evaluate_dev = lambda *_args: pytest.fail("disabled optimizer evaluated a case")
        adapter.propose_instruction = lambda *_args: pytest.fail("disabled optimizer proposed text")
        before = adapter.resolution.head_ref
        result = adapter.optimize()
        assert result.status == "DISABLED" and result.reason_code == "GEPA_EXTRA_NOT_INSTALLED"
        assert result.metric_calls == result.physical_attempts == 0
        assert FinanceSkillHeadService(store).resolve().head_ref == before


def test_optimizer_checks_frozen_inputs_before_loading_optional_package(tmp_path, monkeypatch):
    from orgrebase.workspace import skill_optimizer_gepa as module

    monkeypatch.setattr(
        module, "_load_pinned_gepa",
        lambda: pytest.fail("stale inputs must stop before loading GEPA"),
    )
    with StateStore(tmp_path / "stale-input.sqlite3", tenant_id="tenant:gepa-test") as store:
        adapter = _adapter(store)
        adapter.validate_frozen_inputs = lambda _case: (_ for _ in ()).throw(
            IntegrityError("FROZEN_SOURCE_REVOKED")
        )
        with pytest.raises(IntegrityError, match="FROZEN_SOURCE_REVOKED"):
            adapter.optimize()
        assert adapter.budget.physical_attempts == 0
        assert FinanceSkillHeadService(store).resolve().generation == 0


def test_optimizer_never_evaluates_a_case_outside_the_frozen_dev_split(tmp_path):
    with StateStore(tmp_path / "sealed.sqlite3", tenant_id="tenant:gepa-test") as store:
        adapter = _adapter(store)
        adapter._evaluation_batch_type = SimpleNamespace
        adapter.evaluate_dev = lambda *_args: pytest.fail("sealed case reached evaluator")
        foreign = replace(adapter.dev_cases[0], case_ref="case:sealed-holdout")
        candidate = {"change_explanation_instruction": adapter.resolution.bundle.instruction_text}
        with pytest.raises(FinanceOptimizerHold, match="SEALED_CASE_FORBIDDEN"):
            adapter.evaluate([foreign], candidate)
        with pytest.raises(FinanceOptimizerHold, match="MUTATION_SURFACE_INVALID"):
            adapter.evaluate([adapter.dev_cases[0]], {"reference": "alter safety rules"})
        assert adapter.budget.metric_calls == adapter.budget.physical_attempts == 0


def test_optimizer_cost_cap_stops_before_a_second_case_dispatch(tmp_path):
    calls = []

    def evaluate(case, _bundle, _capture_trace):
        calls.append(case.case_ref)
        return FinanceDevObservation(
            status="VALID", score=0.5, safety_passed=True,
            reason_code="CONTROLLED_METRIC", result_ref=None, result_digest=None,
            physical_attempts=1, reserved_microusd=1, evidence_scope="CONTROLLED_TEST",
        )

    with StateStore(tmp_path / "cost-cap.sqlite3", tenant_id="tenant:gepa-test") as store:
        adapter = _adapter(store)
        adapter._evaluation_batch_type = SimpleNamespace
        adapter.evaluate_dev = evaluate
        adapter.budget.limits = replace(adapter.budget.limits, max_cost_microusd=1)
        candidate = {"change_explanation_instruction": adapter.resolution.bundle.instruction_text}
        with pytest.raises(FinanceOptimizerHold, match="COST_BUDGET_EXHAUSTED"):
            adapter.evaluate(list(adapter.dev_cases), candidate)
        assert calls == [adapter.dev_cases[0].case_ref]
        assert adapter.budget.physical_attempts == 1
        assert adapter.budget.reserved_microusd == 1
        assert FinanceSkillHeadService(store).resolve().generation == 0


def test_optimizer_rejects_unaccounted_provider_attempts(tmp_path):
    calls = []

    def overreported(case, _bundle, _capture_trace):
        calls.append(case.case_ref)
        return FinanceDevObservation(
            status="VALID", score=1.0, safety_passed=True,
            reason_code="CONTROLLED_OVERREPORT", result_ref=None, result_digest=None,
            physical_attempts=4, reserved_microusd=1, evidence_scope="CONTROLLED_TEST",
        )

    with StateStore(tmp_path / "provider-usage.sqlite3", tenant_id="tenant:gepa-test") as store:
        adapter = _adapter(store)
        adapter._evaluation_batch_type = SimpleNamespace
        adapter.evaluate_dev = overreported
        candidate = {"change_explanation_instruction": adapter.resolution.bundle.instruction_text}
        with pytest.raises(FinanceOptimizerHold, match="PROVIDER_USAGE_INVALID"):
            adapter.evaluate([adapter.dev_cases[0]], candidate)
        assert calls == [adapter.dev_cases[0].case_ref]
        assert adapter.budget.physical_attempts == adapter.budget.limits.max_attempts_per_case
        assert FinanceSkillHeadService(store).resolve().generation == 0


def test_unknown_result_in_controlled_optimizer_keeps_conservative_reservation(
    tmp_path, monkeypatch,
):
    from orgrebase.workspace import skill_optimizer_gepa as module

    sends = []

    def simulated_gepa(**kwargs):
        adapter = kwargs["adapter"]
        adapter.evaluate(
            [kwargs["trainset"][0]], kwargs["seed_candidate"], capture_traces=True,
        )
        pytest.fail("GEPA search continued after an unknown provider result")

    monkeypatch.setattr(
        module, "_load_pinned_gepa",
        lambda: (SimpleNamespace(optimize=simulated_gepa), SimpleNamespace),
    )
    with StateStore(tmp_path / "unknown-without-extra.sqlite3", tenant_id="tenant:gepa-test") as store:
        adapter = _adapter(store)

        def ambiguous(case, _bundle, _capture_trace):
            sends.append(case.case_ref)
            return FinanceDevObservation(
                status="RESULT_UNKNOWN", score=None, safety_passed=True,
                reason_code="CONTROLLED_AMBIGUOUS_SEND", result_ref=None,
                result_digest=None, physical_attempts=1, reserved_microusd=0,
                evidence_scope="CONTROLLED_TEST",
            )

        adapter.evaluate_dev = ambiguous
        result = adapter.optimize()
        assert result.status == "HOLD"
        assert result.reason_code == "FINANCE_OPTIMIZER_RESULT_UNKNOWN"
        assert result.metric_calls == 1
        assert result.physical_attempts == adapter.budget.limits.max_attempts_per_case
        assert result.reserved_microusd > 0
        assert sends == [adapter.train_cases[0].case_ref]
        assert FinanceSkillHeadService(store).resolve().generation == 0


def test_controlled_gepa_host_can_return_only_an_unpublished_instruction_candidate(
    tmp_path, monkeypatch,
):
    from orgrebase.workspace import skill_optimizer_gepa as module

    def simulated_gepa(**kwargs):
        adapter = kwargs["adapter"]
        assert kwargs["use_merge"] is False
        assert kwargs["run_dir"] is None
        assert kwargs["use_wandb"] is kwargs["use_mlflow"] is False
        seed = kwargs["seed_candidate"]
        train = adapter.evaluate(kwargs["trainset"], seed, capture_traces=True)
        evidence = adapter.make_reflective_dataset(
            seed, train, ["change_explanation_instruction"],
        )
        proposed = kwargs["custom_candidate_proposer"](
            seed, evidence, ["change_explanation_instruction"],
        )
        dev = adapter.evaluate(kwargs["valset"], proposed)
        return SimpleNamespace(
            best_candidate=proposed,
            val_aggregate_scores=[sum(dev.scores) / len(dev.scores)],
            best_idx=0,
        )

    monkeypatch.setattr(
        module, "_load_pinned_gepa",
        lambda: (SimpleNamespace(optimize=simulated_gepa), SimpleNamespace),
    )
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *_args, **_kwargs: pytest.fail("scripted optimizer used a network transport"),
    )
    with StateStore(tmp_path / "candidate-only.sqlite3", tenant_id="tenant:gepa-test") as store:
        adapter = _adapter(store)
        current = FinanceSkillHeadService(store).resolve()
        result = adapter.optimize()
        assert result.status == "CANDIDATE_ONLY"
        assert result.evidence_scope == "CONTROLLED_TEST"
        assert result.development_score == 1.0
        assert result.candidate_bundle is not None
        assert result.candidate_bundle.reference_bytes == current.bundle.reference_bytes
        assert result.candidate_bundle.payload["parent_head_ref"] == current.head_ref
        assert result.candidate_bundle.payload["parent_head_digest"] == current.head_digest
        assert FinanceSkillHeadService(store).resolve().head_digest == current.head_digest
        assert result.metric_calls == 4
        assert result.physical_attempts == 0


def test_unpinned_gepa_version_fails_without_importing_package(monkeypatch):
    from orgrebase.workspace import skill_optimizer_gepa as module

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "0.1.5")
    monkeypatch.setattr(
        module.importlib, "import_module",
        lambda _name: pytest.fail("unreviewed GEPA version was imported"),
    )
    with pytest.raises(FinanceOptimizerHold, match="FINANCE_GEPA_VERSION_DRIFT"):
        module._load_pinned_gepa()


def test_sealed_or_cluster_overlapping_examples_are_rejected_before_search(tmp_path):
    with StateStore(tmp_path / "split.sqlite3", tenant_id="tenant:gepa-test") as store:
        adapter = _adapter(store)
        original = adapter.train_cases[0]
        with pytest.raises(ValueError, match="DEVELOPMENT_SPLIT_INVALID"):
            make_finance_optimizer(
                head=FinanceSkillHeadService(store),
                train_cases=adapter.train_cases,
                dev_cases=(FinanceDevCase(
                    case_ref="case:sealed", case_revision=original.case_revision,
                    independence_cluster_id=original.independence_cluster_id,
                    partition="DEV", memory_snapshot_ref=original.memory_snapshot_ref,
                    memory_snapshot_digest=original.memory_snapshot_digest,
                    recall_manifest_ref=original.recall_manifest_ref,
                    recall_manifest_digest=original.recall_manifest_digest,
                ),),
                evaluate_dev=_scripted_evaluate, propose_instruction=_scripted_propose,
                validate_frozen_inputs=lambda _case: None,
                model_budget=_budget(), limits=adapter.budget.limits,
            )


def test_real_vertex_optimizer_cannot_start_without_durable_family_ledger(tmp_path):
    with StateStore(tmp_path / "real-disabled.sqlite3", tenant_id="tenant:gepa-test") as store:
        head = _head(store)
        train, dev = _cases()
        with pytest.raises(ValueError, match="DURABLE_LEDGER_REQUIRED"):
            make_finance_optimizer(
                head=head, train_cases=train, dev_cases=dev,
                evaluate_dev=_scripted_evaluate, propose_instruction=_scripted_propose,
                validate_frozen_inputs=lambda _case: None,
                model_budget=_budget(), limits=FinanceOptimizerLimits(),
                execution_scope="REAL_VERTEX", experiment_family_id="family:one",
            )


def test_pinned_gepa_runs_only_dev_scripted_adapter_when_extra_available(tmp_path):
    try:
        version = importlib.metadata.version("gepa")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("optional GEPA extra not installed")
    assert version == "0.1.4"
    with StateStore(tmp_path / "gepa.sqlite3", tenant_id="tenant:gepa-test") as store:
        adapter = _adapter(store)
        original = adapter.resolution
        result = adapter.optimize()
        assert result.status == "CANDIDATE_ONLY"
        assert result.evidence_scope == "CONTROLLED_TEST"
        assert result.candidate_bundle is not None
        assert result.candidate_bundle.reference_bytes == original.bundle.reference_bytes
        assert result.candidate_bundle.payload["parent_head_ref"] == original.head_ref
        assert result.candidate_bundle.instruction_text.endswith("Check source coverage.\n")
        assert result.development_score == 1.0
        assert result.physical_attempts == 0
        assert result.metric_calls <= adapter.budget.limits.max_metric_calls
        assert FinanceSkillHeadService(store).resolve().head_ref == original.head_ref


def test_unknown_provider_result_holds_reserved_budget_without_retry(tmp_path):
    try:
        importlib.metadata.version("gepa")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("optional GEPA extra not installed")

    def unknown(case, bundle, capture_trace):
        del case, bundle, capture_trace
        return FinanceDevObservation(
            status="RESULT_UNKNOWN", score=None, safety_passed=True,
            reason_code="PROVIDER_RESULT_UNKNOWN", result_ref=None,
            result_digest=None, physical_attempts=1,
            reserved_microusd=0, evidence_scope="CONTROLLED_TEST",
        )

    with StateStore(tmp_path / "unknown.sqlite3", tenant_id="tenant:gepa-test") as store:
        adapter = _adapter(store)
        adapter.evaluate_dev = unknown
        result = adapter.optimize()
        assert result.status == "HOLD"
        assert result.reason_code == "FINANCE_OPTIMIZER_RESULT_UNKNOWN"
        assert result.metric_calls == 1
        assert result.physical_attempts == adapter.budget.limits.max_attempts_per_case
        assert result.reserved_microusd > 0
        assert FinanceSkillHeadService(store).resolve().generation == 0
