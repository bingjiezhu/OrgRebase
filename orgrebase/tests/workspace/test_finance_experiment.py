"""Durable Finance experiment controls; synthetic observations are not quality evidence."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

import pytest

from orgrebase.auth import AuthenticationError, Principal, request_authorization, request_principal
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.private_records import PrivateRecordStore
from orgrebase.store import StateStore
from orgrebase.workspace import finance_experiment as finance_experiment_module
from orgrebase.workspace.change_proposals import submit_change
from orgrebase.workspace.experience_contracts import LessonSnapshotEntry, MemorySnapshot
from orgrebase.workspace.finance_experiment import (
    ARMS,
    ASSESSMENT_MEDIA,
    FAMILY_MEDIA,
    GOLD_COMMITMENT_MEDIA,
    OBSERVATION_MEDIA,
    REVIEWED_FINANCE_RUBRIC_DIGEST,
    TRIAL_MEDIA,
    FinanceExperimentService,
    FinanceIndependentOracle,
    derive_finance_case_cluster,
    require_independent_dependency_clusters,
)
from orgrebase.workspace.finance_explanation_operations import (
    INTENT_MEDIA,
    RESULT_MEDIA,
    evaluate_registered_finance_arm,
    evaluate_static_finance,
    finance_evaluation_refs,
)
from orgrebase.workspace.skill_evolution_v2 import FinanceSkillHeadService
from orgrebase.workspace.vertex_candidate import FINANCE_V4_COMPILER_VERSION
from tests.postgres_support import postgres_cluster as postgres_cluster
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_change_proposals import command
from tests.workspace.test_change_proposals import workspace as workspace
from tests.workspace.test_finance_advice_v4 import _provider
from tests.workspace.test_finance_explanation_operations import (
    _controlled_family,
    _fake_send,
    _prepare,
)

TENANT = "tenant:finance-experiment"
EVALUATOR = "actor:experiment-evaluator"
OPERATOR = "actor:experiment-operator"


@contextmanager
def _as(tenant: str, actor: str, *roles: str):
    token = request_principal.set(Principal(
        issuer="https://issuer.example", subject=actor,
        tenant_id=tenant, actor_id=actor,
        roles=frozenset(roles), expires_at=int(time.time()) + 3600,
    ))
    authorization = request_authorization.set(lambda: None)
    try:
        yield
    finally:
        request_authorization.reset(authorization)
        request_principal.reset(token)


def _plan(service: FinanceExperimentService, *, family_id: str = "family:alpha",
          max_queries: int = 9, max_calls: int = 18, max_cost: int = 900,
          cases_override=None, orders_override=None, execution_scope="CONTROLLED_FIXTURE",
          sealed_suite_ref=None, sealed_suite_digest=None):
    clusters = ("cluster:one", "cluster:two", "cluster:three")
    cases = {
        f"case:{i}": {
            "case_revision_digest": sha256_digest({"case": i}),
            "independence_cluster_id": clusters[i-1],
            "split": ("DEVELOPMENT", "VALIDATION", "SEALED_HOLDOUT")[i-1],
            "source_change_key": f"source-change:{i}",
            "time_ordinal": i,
        } for i in (1, 2, 3)
    }
    return service.freeze_family(
        family_id=family_id,
        parent_head_ref="skill-head:parent@g00000000",
        parent_head_digest=sha256_digest("head"),
        cases=cases if cases_override is None else cases_override, seeds=(11, 23, 37),
        orders=orders_override if orders_override is not None else {
            "CHRONOLOGICAL": clusters,
            "ORDER_STRATIFIED": (clusters[0], clusters[2], clusters[1]),
            "CLUSTER_SHUFFLED": tuple(reversed(clusters)),
        },
        rubric_digest=sha256_digest("rubric"), max_queries=max_queries,
        max_reserved_calls=max_calls, max_reserved_microusd=max_cost,
        execution_scope=execution_scope,
        sealed_suite_ref=sealed_suite_ref, sealed_suite_digest=sealed_suite_digest,
    )


def _reserve(service, family_ref, *, arm="STATIC_CURRENT", seed=11, order="CHRONOLOGICAL",
             bundle="bundle:one", case_ref="case:1", calls=2, cost=100):
    return service.reserve_trial(
        family_ref=family_ref, case_ref=case_ref, seed=seed, order=order, arm=arm,
        bundle_digest=sha256_digest(bundle), snapshot_digest=sha256_digest("snapshot"),
        manifest_digest=sha256_digest("manifest"),
        budget_contract_digest=sha256_digest("budget"),
        reserved_calls=calls, reserved_microusd=cost,
    )


def _three_cases():
    return {
        f"case:{i}": {
            "case_revision_digest": sha256_digest({"case": i}),
            "independence_cluster_id": f"cluster:{i}",
            "split": ("DEVELOPMENT", "VALIDATION", "SEALED_HOLDOUT")[i - 1],
            "source_change_key": f"source-change:{i}",
            "time_ordinal": i,
        }
        for i in (1, 2, 3)
    }


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("cluster_across_splits", "CLUSTER_LEAKAGE"),
        ("same_source_different_cluster", "CLUSTER_LEAKAGE"),
        ("no_holdout", "SPLIT_INSUFFICIENT"),
        ("duplicate_order", "ORDER_INVALID"),
        ("false_chronology", "CHRONOLOGY_INVALID"),
    ],
)
def test_family_rejects_split_leakage_and_order_relabeling(tmp_path, mutation, reason):
    cases = _three_cases()
    clusters = ("cluster:1", "cluster:2", "cluster:3")
    orders = {
        "CHRONOLOGICAL": clusters,
        "ORDER_STRATIFIED": (clusters[0], clusters[2], clusters[1]),
        "CLUSTER_SHUFFLED": tuple(reversed(clusters)),
    }
    if mutation == "cluster_across_splits":
        cases["case:2"]["independence_cluster_id"] = "cluster:1"
    elif mutation == "same_source_different_cluster":
        cases["case:2"]["source_change_key"] = cases["case:1"]["source_change_key"]
    elif mutation == "no_holdout":
        cases["case:3"]["split"] = "VALIDATION"
    elif mutation == "duplicate_order":
        orders["CLUSTER_SHUFFLED"] = (clusters[0], clusters[0], clusters[2])
    else:
        orders["CHRONOLOGICAL"] = tuple(reversed(clusters))
    with StateStore(tmp_path / "split.sqlite3", tenant_id=TENANT) as store:
        service = FinanceExperimentService(
            store, tenant_id=TENANT, evaluator_actor_id=EVALUATOR,
            operator_actor_id=OPERATOR,
        )
        with _as(TENANT, EVALUATOR, "governor"), pytest.raises(IntegrityError, match=reason):
            _plan(service, cases_override=cases, orders_override=orders)
        with pytest.raises(KeyError):
            store.load_artifact(service.family_ref("family:alpha"), FAMILY_MEDIA)
        assert store.verify_event_chain()["status"] == "PASS"


def test_family_rejects_three_names_for_one_identical_order(tmp_path):
    clusters = ("cluster:1", "cluster:2", "cluster:3")
    identical_orders = {name: clusters for name in (
        "CHRONOLOGICAL", "ORDER_STRATIFIED", "CLUSTER_SHUFFLED",
    )}
    with StateStore(tmp_path / "same-orders.sqlite3", tenant_id=TENANT) as store:
        service = FinanceExperimentService(
            store, tenant_id=TENANT, evaluator_actor_id=EVALUATOR,
            operator_actor_id=OPERATOR,
        )
        with _as(TENANT, EVALUATOR, "governor"), pytest.raises(
            IntegrityError, match="ORDER_NOT_DISTINCT"
        ):
            _plan(service, cases_override=_three_cases(), orders_override=identical_orders)
        with pytest.raises(KeyError):
            store.load_artifact(service.family_ref("family:alpha"), FAMILY_MEDIA)


def test_family_rejects_sealed_holdout_earlier_than_development(tmp_path):
    cases = _three_cases()
    cases["case:1"]["time_ordinal"] = 3
    cases["case:3"]["time_ordinal"] = 1
    clusters = ("cluster:1", "cluster:2", "cluster:3")
    orders = {
        "CHRONOLOGICAL": tuple(reversed(clusters)),
        "ORDER_STRATIFIED": (clusters[2], clusters[0], clusters[1]),
        "CLUSTER_SHUFFLED": clusters,
    }
    with StateStore(tmp_path / "time-split.sqlite3", tenant_id=TENANT) as store:
        service = FinanceExperimentService(
            store, tenant_id=TENANT, evaluator_actor_id=EVALUATOR,
            operator_actor_id=OPERATOR,
        )
        with _as(TENANT, EVALUATOR, "governor"), pytest.raises(
            IntegrityError, match="TIME_SPLIT_ORDER_INVALID"
        ):
            _plan(service, cases_override=cases, orders_override=orders)
        with pytest.raises(KeyError):
            store.load_artifact(service.family_ref("family:alpha"), FAMILY_MEDIA)


def test_real_family_needs_real_suite_workspace_and_independent_gold(tmp_path):
    with StateStore(tmp_path / "real-denied.sqlite3", tenant_id=TENANT) as store:
        service = FinanceExperimentService(
            store, tenant_id=TENANT, evaluator_actor_id=EVALUATOR,
            operator_actor_id=OPERATOR,
        )
        with _as(TENANT, EVALUATOR, "governor"):
            with pytest.raises(IntegrityError, match="FAMILY_INVALID"):
                _plan(service, execution_scope="REAL_VERTEX")
            with pytest.raises(IntegrityError, match="REAL_WORKSPACE_REQUIRED"):
                _plan(
                    service, execution_scope="REAL_VERTEX",
                    sealed_suite_ref="suite:forged", sealed_suite_digest=sha256_digest("forged"),
                )
        with pytest.raises(KeyError):
            store.load_artifact(service.family_ref("family:alpha"), FAMILY_MEDIA)


@pytest.mark.parametrize(
    ("limits", "expected_error"),
    [
        ({"max_queries": 1, "max_calls": 2, "max_cost": 100}, "QUERY_BUDGET_EXHAUSTED"),
        ({"max_queries": 2, "max_calls": 1, "max_cost": 100}, "CALL_BUDGET_EXHAUSTED"),
        ({"max_queries": 2, "max_calls": 2, "max_cost": 50}, "COST_BUDGET_EXHAUSTED"),
    ],
)
def test_family_budget_counts_prior_reservations_even_after_fixture_terminal(
    tmp_path, limits, expected_error,
):
    with StateStore(tmp_path / "budget.sqlite3", tenant_id=TENANT) as store:
        service = FinanceExperimentService(
            store, tenant_id=TENANT, evaluator_actor_id=EVALUATOR,
            operator_actor_id=OPERATOR,
        )
        with _as(TENANT, EVALUATOR, "governor"):
            family_ref = _plan(service, **limits)
        with _as(TENANT, OPERATOR, "operator"):
            first = _reserve(service, family_ref, calls=1, cost=50)
        with _as(TENANT, EVALUATOR, "governor"):
            service.record_fixture_terminal(trial_ref=first["trial_ref"], outcome_code="NO_DELTA")
        with _as(TENANT, OPERATOR, "operator"):
            assert _reserve(service, family_ref, calls=1, cost=50)["trial_ref"] == first["trial_ref"]
            with pytest.raises(IntegrityError, match=expected_error):
                _reserve(service, family_ref, arm="NO_MEMORY", calls=1, cost=1)
        with _as(TENANT, EVALUATOR, "governor"):
            projection = service.family_projection(family_ref)
            assert projection["trial_count"] == 1
            assert projection["reserved_calls"] == 1
            assert projection["reserved_microusd"] == 50
            assert projection["quality_status"] == "NOT_EVALUATED"


def test_family_roles_and_registered_trial_identity_fail_closed(tmp_path):
    with StateStore(tmp_path / "actors.sqlite3", tenant_id=TENANT) as store:
        service = FinanceExperimentService(
            store, tenant_id=TENANT, evaluator_actor_id=EVALUATOR,
            operator_actor_id=OPERATOR,
        )
        with _as(TENANT, "actor:intruder", "governor"), pytest.raises(
            AuthorizationError, match="ACTOR_REQUIRED"
        ):
            _plan(service)
        with _as(TENANT, EVALUATOR, "governor"):
            family_ref = _plan(service)
        with _as(TENANT, EVALUATOR, "governor"), pytest.raises(
            AuthenticationError, match="AUTH_ACTION_DENIED"
        ):
            _reserve(service, family_ref)
        with _as("tenant:other", OPERATOR, "operator"), pytest.raises(
            AuthenticationError, match="AUTH_TENANT_DENIED"
        ):
            _reserve(service, family_ref)
        with _as(TENANT, OPERATOR, "operator"):
            for changed in ({"arm": "UNREGISTERED"}, {"seed": 99}, {"order": "FORGED"},
                            {"case_ref": "case:unknown"}):
                with pytest.raises(IntegrityError, match="TRIAL_NOT_REGISTERED"):
                    _reserve(service, family_ref, **changed)
        with _as(TENANT, EVALUATOR, "governor"):
            assert service.family_projection(family_ref)["trial_count"] == 0


def test_postgres_separate_python_process_reuses_unknown_identity_and_budget(postgres_runtime):
    database = postgres_runtime(tenant_id=TENANT)
    with StateStore(database["runtime_dsn"], tenant_id=TENANT, migrate=False) as store:
        service = FinanceExperimentService(
            store, tenant_id=TENANT, evaluator_actor_id=EVALUATOR,
            operator_actor_id=OPERATOR,
        )
        with _as(TENANT, EVALUATOR, "governor"):
            family_ref = _plan(service, max_queries=2, max_calls=2, max_cost=100)
        with _as(TENANT, OPERATOR, "operator"):
            first = _reserve(service, family_ref, calls=1, cost=50)
    script = """
import json
import sys
import time
from orgrebase.auth import Principal, request_principal
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.finance_experiment import FinanceExperimentService

dsn, tenant, evaluator, operator, family_ref, arm = sys.argv[1:]
token = request_principal.set(Principal(
    issuer="https://issuer.example", subject=operator, tenant_id=tenant,
    actor_id=operator, roles=frozenset({"operator"}), expires_at=int(time.time()) + 3600,
))
try:
    with StateStore(dsn, tenant_id=tenant, migrate=False) as store:
        service = FinanceExperimentService(
            store, tenant_id=tenant, evaluator_actor_id=evaluator,
            operator_actor_id=operator,
        )
        try:
            value = service.reserve_trial(
                family_ref=family_ref, case_ref="case:1", seed=11,
                order="CHRONOLOGICAL", arm=arm,
                bundle_digest=sha256_digest("bundle:one"),
                snapshot_digest=sha256_digest("snapshot"),
                manifest_digest=sha256_digest("manifest"),
                budget_contract_digest=sha256_digest("budget"),
                reserved_calls=1, reserved_microusd=50,
            )
            print(json.dumps({"trial_ref": value["trial_ref"],
                              "operation_id": value["operation_id"],
                              "status": value["status"]}))
        except IntegrityError as exc:
            print(json.dumps({"error": str(exc)}))
finally:
    request_principal.reset(token)
"""

    def resumed(arm):
        result = subprocess.run(
            [sys.executable, "-c", script, database["runtime_dsn"], TENANT,
             EVALUATOR, OPERATOR, family_ref, arm],
            check=True, capture_output=True, text=True, timeout=30,
        )
        return json.loads(result.stdout)

    replay = resumed("STATIC_CURRENT")
    assert replay == {
        "trial_ref": first["trial_ref"],
        "operation_id": first["operation_id"],
        "status": "RESULT_UNKNOWN",
    }
    assert "PREDECESSOR_UNKNOWN" in resumed("NO_MEMORY")["error"]
    with StateStore(database["runtime_dsn"], tenant_id=TENANT, migrate=False) as store:
        service = FinanceExperimentService(
            store, tenant_id=TENANT, evaluator_actor_id=EVALUATOR,
            operator_actor_id=OPERATOR,
        )
        with _as(TENANT, EVALUATOR, "governor"):
            projection = service.family_projection(family_ref)
            assert projection["trial_count"] == 1
            assert projection["reserved_calls"] == 1
            assert projection["reserved_microusd"] == 50
            assert projection["qualification_status"] == "NOT_QUALIFIED"
            assert store.verify_event_chain()["status"] == "PASS"


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_family_freeze_nine_lineages_budget_unknown_and_restart(tmp_path, postgres_runtime, backend):
    config = postgres_runtime(tenant_id=TENANT) if backend == "postgresql" else None
    path = config["runtime_dsn"] if config else tmp_path / "finance.sqlite3"
    with StateStore(path, tenant_id=TENANT, migrate=backend != "postgresql") as store:
        service = FinanceExperimentService(
            store, tenant_id=TENANT, evaluator_actor_id=EVALUATOR,
            operator_actor_id=OPERATOR,
        )
        with _as(TENANT, EVALUATOR, "governor"):
            family_ref = _plan(service)
            assert _plan(service) == family_ref
            assert store.load_artifact(family_ref, FAMILY_MEDIA).payload["execution_scope"] == "CONTROLLED_FIXTURE"
            with pytest.raises(IntegrityError, match="CASE_ALREADY_BOUND"):
                _plan(service, family_id="family:changed")
        with _as(TENANT, OPERATOR, "operator"):
            first = _reserve(service, family_ref)
            assert _reserve(service, family_ref) == first
            assert first["status"] == "RESULT_UNKNOWN"
            with pytest.raises(IntegrityError, match="TRIAL_IDENTITY_CONFLICT"):
                _reserve(service, family_ref, bundle="bundle:switched")
            with pytest.raises(IntegrityError, match="PREDECESSOR_UNKNOWN"):
                _reserve(service, family_ref, arm="BOUNDED_GEPA")
        with _as(TENANT, EVALUATOR, "governor"):
            projection = service.family_projection(family_ref)
            assert projection["lineage_count_registered"] == 9
            assert projection["statuses"]["RESULT_UNKNOWN"] == 1
    with StateStore(path, tenant_id=TENANT, migrate=False) as resumed:
        service = FinanceExperimentService(
            resumed, tenant_id=TENANT, evaluator_actor_id=EVALUATOR,
            operator_actor_id=OPERATOR,
        )
        with _as(TENANT, OPERATOR, "operator"):
            assert _reserve(service, family_ref) == first
            with pytest.raises(IntegrityError, match="PREDECESSOR_UNKNOWN"):
                _reserve(service, family_ref, arm="NO_MEMORY")
        with _as(TENANT, EVALUATOR, "governor"):
            service.record_fixture_terminal(trial_ref=first["trial_ref"], outcome_code="SIMULATED_SUCCESS")
        with _as(TENANT, OPERATOR, "operator"):
            second = _reserve(service, family_ref, arm="NO_MEMORY")
            assert second["status"] == "RESULT_UNKNOWN"
        with _as(TENANT, EVALUATOR, "governor"):
            projection = service.family_projection(family_ref)
            assert projection["reserved_calls"] == 4
            assert projection["reserved_microusd"] == 200
            assert projection["quality_status"] == "NOT_EVALUATED"
            assert resumed.verify_event_chain()["status"] == "PASS"


def test_fixture_matrix_retains_each_seed_order_lineage_and_negative_outcome(tmp_path):
    with StateStore(tmp_path / "matrix.sqlite3", tenant_id=TENANT) as store:
        service = FinanceExperimentService(
            store, tenant_id=TENANT, evaluator_actor_id=EVALUATOR,
            operator_actor_id=OPERATOR,
        )
        with _as(TENANT, EVALUATOR, "governor"):
            family_ref = _plan(service)
        for seed in (11, 23, 37):
            for order in ("CHRONOLOGICAL", "ORDER_STRATIFIED", "CLUSTER_SHUFFLED"):
                with _as(TENANT, OPERATOR, "operator"):
                    trial = _reserve(service, family_ref, seed=seed, order=order)
                with _as(TENANT, EVALUATOR, "governor"):
                    service.record_fixture_terminal(
                        trial_ref=trial["trial_ref"],
                        outcome_code="NEGATIVE_TRANSFER" if seed == 23 else "NO_DELTA",
                    )
        with _as(TENANT, EVALUATOR, "governor"):
            projection = service.family_projection(family_ref)
            assert projection["trial_count"] == 9
            assert projection["statuses"]["FIXTURE_TERMINAL"] == 9
            assert projection["qualification_status"] == "NOT_QUALIFIED"


def test_fixture_terminal_rejects_other_evaluator_and_forged_observation(tmp_path):
    with StateStore(tmp_path / "bound.sqlite3", tenant_id=TENANT) as store:
        service = FinanceExperimentService(
            store, tenant_id=TENANT, evaluator_actor_id=EVALUATOR,
            operator_actor_id=OPERATOR,
        )
        with _as(TENANT, EVALUATOR, "governor"):
            family_ref = _plan(service)
        with _as(TENANT, OPERATOR, "operator"):
            trial = _reserve(service, family_ref)
        other = FinanceExperimentService(
            store, tenant_id=TENANT, evaluator_actor_id="actor:other-evaluator",
            operator_actor_id=OPERATOR,
        )
        with _as(TENANT, "actor:other-evaluator", "governor"), pytest.raises(
            IntegrityError, match="FAMILY_STALE_OR_INVALID"
        ):
            other.record_fixture_terminal(
                trial_ref=trial["trial_ref"], outcome_code="PRETEND_TERMINAL",
            )
        forged_ref = "finance-experiment-observation:" + trial["trial_ref"].rsplit(":", 1)[1]
        with store.transaction() as connection:
            store.save_artifact(connection, forged_ref, OBSERVATION_MEDIA, {
                "schema_version": "orgrebase.finance-experiment-observation.v1",
                "trial_ref": trial["trial_ref"],
                "trial_digest": sha256_digest("wrong trial"),
                "status": "FIXTURE_TERMINAL",
                "evidence_scope": "DETERMINISTIC_MECHANISM_ONLY",
                "evaluator_actor_id": EVALUATOR,
                "quality_status": "NOT_EVALUATED", "target_writes": 0,
            })
        with pytest.raises(IntegrityError, match="OBSERVATION_BINDING_INVALID"):
            service.trial_status(trial["trial_ref"])
        with _as(TENANT, OPERATOR, "operator"), pytest.raises(
            IntegrityError, match="OBSERVATION_BINDING_INVALID"
        ):
            _reserve(service, family_ref, arm="NO_MEMORY")


def test_claimed_qualified_terminal_cannot_promote_a_fixture_trial(tmp_path):
    with StateStore(tmp_path / "forged-qualified.sqlite3", tenant_id=TENANT) as store:
        service = FinanceExperimentService(
            store, tenant_id=TENANT, evaluator_actor_id=EVALUATOR,
            operator_actor_id=OPERATOR,
        )
        with _as(TENANT, EVALUATOR, "governor"):
            family_ref = _plan(service)
        with _as(TENANT, OPERATOR, "operator"):
            trial = _reserve(service, family_ref)
        raw_trial = store.load_artifact(trial["trial_ref"], TRIAL_MEDIA).payload
        observation_ref = "finance-experiment-observation:" + trial["trial_ref"].rsplit(":", 1)[1]
        with store.transaction() as connection:
            store.save_artifact(connection, observation_ref, OBSERVATION_MEDIA, {
                "schema_version": "orgrebase.finance-experiment-observation.v1",
                "trial_ref": trial["trial_ref"],
                "trial_digest": sha256_digest(raw_trial),
                "status": "QUALIFIED", "evidence_scope": "REAL_VERTEX",
                "evaluator_actor_id": EVALUATOR, "quality_status": "PASS", "target_writes": 0,
            })
        with pytest.raises(IntegrityError, match="OBSERVATION_STATUS_INVALID"):
            service.trial_status(trial["trial_ref"])
        with _as(TENANT, OPERATOR, "operator"), pytest.raises(
            IntegrityError, match="OBSERVATION_STATUS_INVALID"
        ):
            _reserve(service, family_ref, arm="NO_MEMORY")


def test_partial_dependency_overlap_and_same_business_source_cannot_split_clusters(workspace):
    event_id, _ = _prepare(workspace)
    derived = derive_finance_case_cluster(workspace, event_id)
    assert derived["dependency_key_digests"]
    other_events = [
        submit_change(
            workspace,
            command(workspace, slot="currency", value=value).model_copy(
                update={"event_id": f"edit:{number}"}
            ),
        )["event"]["event_id"]
        for number, value in ((2, "GBP"), (3, "JPY"))
    ]
    for another_id in other_events:
        workspace.preview_change(another_id)
        assert derive_finance_case_cluster(workspace, another_id)["cluster_id"] == derived["cluster_id"]
    key_x = sha256_digest("shared upstream connector record")
    key_y = sha256_digest("additional read dependency")
    with pytest.raises(IntegrityError, match="OVERLAPPING_CLUSTERS"):
        require_independent_dependency_clusters({
            "case:A": ("cluster:A", (key_x,)),
            "case:B": ("cluster:B", (key_x, key_y)),
        })
    oracle = FinanceIndependentOracle(
        workspace, gold_owner_actor_id="actor:gold-owner",
        assessor_actor_id="actor:assessor",
        candidate_author_actor_id="actor:content-author",
        optimizer_actor_id="actor:optimizer",
        release_governor_actor_id="actor:finance-governor",
    )
    with _as(workspace.profile.organization_id, "actor:gold-owner", "governor"):
        for forged in ("cluster:fake-one", "cluster:fake-two", "cluster:fake-three"):
            with pytest.raises(IntegrityError, match="CLUSTER_SPOOFED"):
                oracle.freeze_gold(
                    event_id=event_id, suite_id=forged.replace(":", "-"),
                    independence_cluster_id=forged,
                    required_concepts=("source",), forbidden_claims=("approved",),
                    next_step_concepts=("review",), rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
                )


def test_postgres_two_processes_share_family_budget_and_unknown(postgres_runtime):
    config = postgres_runtime(tenant_id=TENANT)
    with (
        StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as first,
        StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as second,
    ):
        one = FinanceExperimentService(first, tenant_id=TENANT, evaluator_actor_id=EVALUATOR,
                                       operator_actor_id=OPERATOR)
        two = FinanceExperimentService(second, tenant_id=TENANT, evaluator_actor_id=EVALUATOR,
                                       operator_actor_id=OPERATOR)
        with _as(TENANT, EVALUATOR, "governor"):
            family_ref = _plan(one, max_queries=1, max_calls=2, max_cost=100)
        barrier = Barrier(2)

        def execute(item):
            service, arm = item
            with _as(TENANT, OPERATOR, "operator"):
                barrier.wait(timeout=10)
                try:
                    return _reserve(service, family_ref, arm=arm)
                except IntegrityError as exc:
                    return str(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute, ((one, "STATIC_CURRENT"), (two, "NO_MEMORY"))))
        assert sum(isinstance(item, dict) for item in results) == 1
        assert any("BUDGET_EXHAUSTED" in item for item in results if isinstance(item, str))
        with _as(TENANT, EVALUATOR, "governor"):
            assert one.family_projection(family_ref)["trial_count"] == 1


def test_independent_oracle_binds_predeclared_gold_real_v4_private_and_state(
    workspace, monkeypatch,
):
    event_id, _ = _prepare(workspace)
    oracle = FinanceIndependentOracle(
        workspace, gold_owner_actor_id="actor:gold-owner",
        assessor_actor_id="actor:assessor",
        candidate_author_actor_id="actor:content-author",
        optimizer_actor_id="actor:optimizer",
        release_governor_actor_id="actor:finance-governor",
    )
    derived_cluster = derive_finance_case_cluster(workspace, event_id)["cluster_id"]
    with _as(workspace.profile.organization_id, "actor:gold-owner", "governor"):
        commitment_ref = oracle.freeze_gold(
            event_id=event_id, suite_id="suite-one",
            independence_cluster_id=derived_cluster,
            required_concepts=("source",), forbidden_claims=("approved",),
            next_step_concepts=("review",), rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
        )
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
    with _as(workspace.profile.organization_id, "actor:finance-evaluator", "operator"):
        assert evaluate_static_finance(
            workspace, event_id=event_id, operation_id="oracle-static",
            provider_override=_provider(),
        )["status"] == "PROTOCOL_VALID"
    result_ref = finance_evaluation_refs(workspace, event_id)["result_ref"]
    with _as(workspace.profile.organization_id, "actor:assessor", "governor"):
        assessment_ref = oracle.assess_result(
            commitment_ref=commitment_ref, result_ref=result_ref,
            grounded_score=4, next_step_score=4, reason_code="INDEPENDENT_REVIEW",
        )
        receipt = workspace.store.load_artifact(assessment_ref, ASSESSMENT_MEDIA).payload
        assert receipt["status"] == "ASSESSOR_REVIEWED"
        assert all(receipt["hard_checks"].values())
        assert all(receipt["concept_checks"].values())
        assert receipt["evidence_scope"] == "CONTROLLED_LOCAL"
        assert receipt["first_exposure_status"] == "UNDETERMINED"
        assert receipt["sealed_eligibility"] == "HOLD_FIRST_EXPOSURE_UNVERIFIED"
    with _as(workspace.profile.organization_id, "actor:gold-owner", "governor"):
        with pytest.raises(IntegrityError, match="ASSESSMENT_INVALID"):
            oracle.verify_assessment_receipt(
                assessment_ref, expected_result_ref=result_ref,
                expected_gold_ref=receipt["gold_ref"],
                expected_rubric_digest=receipt["rubric_digest"],
                expected_case_ref=event_id, expected_cluster_id=receipt["independence_cluster_id"],
                expected_grounded_score=4, expected_next_step_score=4,
                require_sealed_pair=True,
            )
        assert oracle.verify_assessment_receipt(
            assessment_ref, expected_result_ref=result_ref,
            expected_gold_ref=receipt["gold_ref"],
            expected_rubric_digest=receipt["rubric_digest"],
            expected_case_ref=event_id, expected_cluster_id=receipt["independence_cluster_id"],
            expected_grounded_score=4, expected_next_step_score=4,
            require_sealed_pair=False,
        )["status"] == "ASSESSOR_REVIEWED"
    with _as(workspace.profile.organization_id, "actor:finance-governor", "governor"):
        assert oracle.verify_assessment_for_release(
            assessment_ref, expected_result_ref=result_ref,
            expected_gold_ref=receipt["gold_ref"],
            expected_rubric_digest=receipt["rubric_digest"],
            expected_case_ref=event_id, expected_cluster_id=receipt["independence_cluster_id"],
            expected_grounded_score=4, expected_next_step_score=4,
            require_sealed_pair=False,
            require_hard_pass=True, require_concept_pass=True,
        )["status"] == "ASSESSOR_REVIEWED"


def test_oracle_requires_current_rubric_and_independent_roles_before_gold(workspace):
    event_id, _ = _prepare(workspace)
    with pytest.raises(ValueError, match="INDEPENDENT_ROLES_REQUIRED"):
        FinanceIndependentOracle(
            workspace, gold_owner_actor_id="actor:gold-owner",
            assessor_actor_id="actor:gold-owner",
            candidate_author_actor_id="actor:content-author",
            optimizer_actor_id="actor:optimizer",
            release_governor_actor_id="actor:finance-governor",
        )
    oracle = FinanceIndependentOracle(
        workspace, gold_owner_actor_id="actor:gold-owner",
        assessor_actor_id="actor:assessor",
        candidate_author_actor_id="actor:content-author",
        optimizer_actor_id="actor:optimizer",
        release_governor_actor_id="actor:finance-governor",
    )
    cluster = derive_finance_case_cluster(workspace, event_id)["cluster_id"]
    kwargs = {
        "event_id": event_id, "suite_id": "protected-gold",
        "independence_cluster_id": cluster,
        "required_concepts": ("confidential-required-concept",),
        "forbidden_claims": ("confidential-forbidden-claim",),
        "next_step_concepts": ("confidential-next-step",),
    }
    with _as(workspace.profile.organization_id, "actor:content-author", "governor"), pytest.raises(
        AuthorizationError, match="ACTOR_REQUIRED"
    ):
        oracle.freeze_gold(**kwargs, rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST)
    with _as(workspace.profile.organization_id, "actor:gold-owner", "governor"):
        with pytest.raises(IntegrityError, match="RUBRIC_STALE"):
            oracle.freeze_gold(**kwargs, rubric_digest=sha256_digest("older rubric"))
        commitment_ref = oracle.freeze_gold(
            **kwargs, rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
        )
    commitment = workspace.store.load_artifact(commitment_ref, GOLD_COMMITMENT_MEDIA).payload
    assert commitment["sealed_eligibility"] == "HOLD_FIRST_EXPOSURE_UNVERIFIED"
    assert commitment["first_exposure_status"] == "UNDETERMINED"
    assert "confidential-" not in json.dumps(commitment)


def test_gold_cannot_be_frozen_after_same_dependency_model_exposure(workspace, monkeypatch):
    event_id, _ = _prepare(workspace)
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send([], workspace))
    with _as(workspace.profile.organization_id, "actor:finance-evaluator", "operator"):
        assert evaluate_static_finance(
            workspace, event_id=event_id, operation_id="prior-static-exposure",
            provider_override=_provider(),
        )["status"] == "PROTOCOL_VALID"
    oracle = FinanceIndependentOracle(
        workspace, gold_owner_actor_id="actor:gold-owner",
        assessor_actor_id="actor:assessor",
        candidate_author_actor_id="actor:content-author",
        optimizer_actor_id="actor:optimizer",
        release_governor_actor_id="actor:finance-governor",
    )
    cluster = derive_finance_case_cluster(workspace, event_id)["cluster_id"]
    with _as(workspace.profile.organization_id, "actor:gold-owner", "governor"), pytest.raises(
        IntegrityError, match="PRIOR_MODEL_EXPOSURE"
    ):
        oracle.freeze_gold(
            event_id=event_id, suite_id="too-late",
            independence_cluster_id=cluster,
            required_concepts=("source",), forbidden_claims=("approved",),
            next_step_concepts=("review",), rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
        )


def test_gold_cannot_be_frozen_for_another_case_in_exposed_dependency_cluster(
    workspace, monkeypatch,
):
    first_id, _ = _prepare(workspace)
    second_id = submit_change(
        workspace,
        command(workspace, slot="currency", value="GBP").model_copy(
            update={"event_id": "edit:second-finance-cluster-case"},
        ),
    )["event"]["event_id"]
    workspace.preview_change(second_id)
    cluster = derive_finance_case_cluster(workspace, first_id)["cluster_id"]
    assert derive_finance_case_cluster(workspace, second_id)["cluster_id"] == cluster
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send([], workspace))
    with _as(workspace.profile.organization_id, "actor:finance-evaluator", "operator"):
        assert evaluate_static_finance(
            workspace, event_id=first_id, operation_id="first-cluster-exposure",
            provider_override=_provider(),
        )["status"] == "PROTOCOL_VALID"
    oracle = FinanceIndependentOracle(
        workspace, gold_owner_actor_id="actor:gold-owner",
        assessor_actor_id="actor:assessor",
        candidate_author_actor_id="actor:content-author",
        optimizer_actor_id="actor:optimizer",
        release_governor_actor_id="actor:finance-governor",
    )
    with _as(workspace.profile.organization_id, "actor:gold-owner", "governor"), pytest.raises(
        IntegrityError, match="PRIOR_MODEL_EXPOSURE"
    ):
        oracle.freeze_gold(
            event_id=second_id, suite_id="too-late-other-case",
            independence_cluster_id=cluster,
            required_concepts=("source",), forbidden_claims=("approved",),
            next_step_concepts=("review",), rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
        )


def test_registered_arm_exposure_blocks_late_gold_without_business_writes(
    workspace, monkeypatch,
):
    event_id, head = _prepare(workspace)
    ledger, family_ref = _controlled_family(workspace, event_id, head)
    before = workspace.current_quote().digest
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
    with _as(workspace.profile.organization_id, "actor:experiment-operator", "operator"):
        result = evaluate_registered_finance_arm(
            workspace, ledger=ledger, family_ref=family_ref,
            event_id=event_id, seed=11, order="CHRONOLOGICAL",
            arm="STATIC_CURRENT", provider_override=_provider(),
        )
    assert result["status"] == "PROTOCOL_VALID"
    assert sent and workspace.current_quote().digest == before
    oracle = FinanceIndependentOracle(
        workspace, gold_owner_actor_id="actor:gold-owner",
        assessor_actor_id="actor:assessor",
        candidate_author_actor_id="actor:content-author",
        optimizer_actor_id="actor:optimizer",
        release_governor_actor_id="actor:finance-governor",
    )
    cluster = derive_finance_case_cluster(workspace, event_id)["cluster_id"]
    with _as(workspace.profile.organization_id, "actor:gold-owner", "governor"), pytest.raises(
        IntegrityError, match="PRIOR_MODEL_EXPOSURE"
    ):
        oracle.freeze_gold(
            event_id=event_id, suite_id="late-registered-arm",
            independence_cluster_id=cluster,
            required_concepts=("source",), forbidden_claims=("approved",),
            next_step_concepts=("review",), rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
        )


def test_unknown_historical_model_case_keeps_gold_on_first_exposure_hold(workspace):
    event_id, _ = _prepare(workspace)
    # A historical intent whose case cannot be reconstructed is not evidence of
    # first exposure. This hand-authored row is a rejection fixture, not a model run.
    with workspace.store.transaction() as connection:
        workspace.store.save_artifact(
            connection, "finance-evaluation-intent:unresolved-history", INTENT_MEDIA,
            {"event_id": "case:missing-historical-preview"},
        )
    oracle = FinanceIndependentOracle(
        workspace, gold_owner_actor_id="actor:gold-owner",
        assessor_actor_id="actor:assessor",
        candidate_author_actor_id="actor:content-author",
        optimizer_actor_id="actor:optimizer",
        release_governor_actor_id="actor:finance-governor",
    )
    with _as(workspace.profile.organization_id, "actor:gold-owner", "governor"):
        commitment_ref = oracle.freeze_gold(
            event_id=event_id, suite_id="unresolved-history",
            independence_cluster_id=derive_finance_case_cluster(workspace, event_id)["cluster_id"],
            required_concepts=("source",), forbidden_claims=("approved",),
            next_step_concepts=("review",), rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
        )
    commitment = workspace.store.load_artifact(commitment_ref, GOLD_COMMITMENT_MEDIA).payload
    assert commitment["first_exposure_status"] == "UNDETERMINED"
    assert commitment["sealed_eligibility"] == "HOLD_FIRST_EXPOSURE_UNVERIFIED"
    private = PrivateRecordStore(
        workspace.store, workspace.clock,
        retention_seconds=workspace.private_retention_seconds,
    ).read_owned(
        commitment["gold_ref"], owner_id="actor:gold-owner",
        scope_ref="finance-sealed-suite:unresolved-history",
    )
    assert private is not None
    audit = private["first_exposure_audit"]
    assert audit["unresolved_prior_case_count"] == 1
    assert audit["known_cluster_exposure_count"] == 0


def test_test_only_real_pair_artifacts_cannot_forge_verified_terminal(workspace):
    event_id, head = _prepare(workspace)
    store = workspace.store
    scope = workspace.profile.organization_id
    derived = derive_finance_case_cluster(workspace, event_id)
    family_id = "family:forged-real-terminal"
    ledger = FinanceExperimentService(
        store, tenant_id=scope, evaluator_actor_id=EVALUATOR,
        operator_actor_id=OPERATOR, workspace=workspace,
        release_governor_actor_id="actor:finance-governor",
    )
    family_ref = ledger.family_ref(family_id)
    family = {
        "schema_version": "orgrebase.finance-experiment-family.v1",
        "scope_digest": ledger.scope_digest, "family_id": family_id,
        "tenant_id": scope, "workspace_id": store.workspace_id,
        "execution_scope": "REAL_VERTEX", "sealed_suite_ref": "finance-sealed-suite:test-only",
        "evaluator_actor_id": EVALUATOR,
        "release_governor_actor_id": "actor:finance-governor",
        "arms": list(ARMS), "max_reserved_calls": 4, "max_reserved_microusd": 200,
        "cases": {
            event_id: {
                "case_revision_digest": derived["case_revision_digest"],
                "independence_cluster_id": derived["cluster_id"],
                "dependency_keys_digest": derived["dependency_keys_digest"],
                "dependency_key_digests": list(derived["dependency_key_digests"]),
                "split": "SEALED_HOLDOUT",
            },
            **{
                f"case:synthetic-{index}": {
                    "independence_cluster_id": f"cluster:synthetic-{index}",
                    "dependency_key_digests": [sha256_digest(f"synthetic-key-{index}")],
                    "split": split,
                }
                for index, split in ((1, "DEVELOPMENT"), (2, "VALIDATION"))
            },
        },
    }
    baseline_bundle = head.bundle.digest
    candidate_bundle = sha256_digest("unpublished-test-only-candidate")
    pair_refs = []
    with store.transaction() as connection:
        store.save_artifact(connection, family_ref, FAMILY_MEDIA, family)
        for arm, bundle in (("STATIC_CURRENT", baseline_bundle),
                            ("HUMAN_REVIEWED", candidate_bundle)):
            trial_ref = ledger._trial_ref(
                family_ref, case_ref=event_id, seed=11, order="CHRONOLOGICAL", arm=arm,
            )
            operation_id = "finance-experiment:" + trial_ref.rsplit(":", 1)[1]
            trial = {
                "family_ref": family_ref, "family_digest": sha256_digest(family),
                "case_ref": event_id,
                "case_revision_digest": derived["case_revision_digest"],
                "cluster_id": derived["cluster_id"], "split": "SEALED_HOLDOUT",
                "candidate_bundle_digest": bundle,
                "execution_scope": "REAL_VERTEX", "actor_id": OPERATOR,
                "operation_id": operation_id,
                "budget_contract_digest": sha256_digest("test-only-budget"),
                "reserved_calls": 2, "reserved_microusd": 100,
            }
            intent_ref = "finance-evaluation-intent:" + trial_ref.rsplit(":", 1)[1]
            result_ref = "finance-evaluation-result:" + trial_ref.rsplit(":", 1)[1]
            intent = {
                "trial_ref": trial_ref, "operation_id": operation_id,
                "candidate_bundle_digest": bundle,
                "budget_contract_digest": trial["budget_contract_digest"],
                "reserved_calls": 2, "reserved_microusd": 100,
            }
            result = {
                "intent_ref": intent_ref, "operation_id": operation_id,
                "candidate_bundle_digest": bundle,
                "intent_digest": sha256_digest(intent),
                "status": "PROTOCOL_VALID", "target_writes": 0,
            }
            store.save_artifact(connection, trial_ref, TRIAL_MEDIA, trial)
            store.save_artifact(connection, intent_ref, INTENT_MEDIA, intent)
            store.save_artifact(connection, result_ref, RESULT_MEDIA, result)
            pair_refs.append(result_ref)
    with _as(scope, EVALUATOR, "governor"), pytest.raises(
        IntegrityError, match="REAL_TRIAL_TERMINAL_UNVERIFIED"
    ):
        ledger.verify_sealed_pair_lineage(
            family_ref=family_ref, suite_ref="finance-sealed-suite:test-only",
            case_ref=event_id, case_revision_digest=derived["case_revision_digest"],
            cluster_id=derived["cluster_id"],
            baseline_result_ref=pair_refs[0], candidate_result_ref=pair_refs[1],
            parent_bundle_digest=baseline_bundle, candidate_bundle_digest=candidate_bundle,
        )


def test_real_family_freeze_rejects_gold_whose_first_exposure_is_unverified(
    workspace, monkeypatch,
):
    event_id, head = _prepare(workspace)
    scope = workspace.profile.organization_id
    actual = derive_finance_case_cluster(workspace, event_id)
    oracle = FinanceIndependentOracle(
        workspace, gold_owner_actor_id="actor:gold-owner",
        assessor_actor_id="actor:assessor",
        candidate_author_actor_id="actor:content-author",
        optimizer_actor_id="actor:optimizer",
        release_governor_actor_id="actor:finance-governor",
    )
    suite_id = "suite:test-only-unverified"
    with _as(scope, "actor:gold-owner", "governor"):
        commitment_ref = oracle.freeze_gold(
            event_id=event_id, suite_id=suite_id,
            independence_cluster_id=actual["cluster_id"],
            required_concepts=("source",), forbidden_claims=("approved",),
            next_step_concepts=("review",), rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
        )
    commitment = workspace.store.load_artifact(commitment_ref, GOLD_COMMITMENT_MEDIA).payload
    assert commitment["sealed_eligibility"] == "HOLD_FIRST_EXPOSURE_UNVERIFIED"
    synthetic = {
        f"case:unverified-{index}": {
            "cluster_id": f"finance-dependency-cluster:synthetic-{index}",
            "source_change_key": f"finance-dependency-source:synthetic-{index}",
            "case_revision_digest": sha256_digest(f"case:unverified-{index}"),
            "dependency_keys_digest": sha256_digest(f"keys:unverified-{index}"),
            "dependency_key_digests": (sha256_digest(f"key:unverified-{index}"),),
        }
        for index in (1, 2)
    }
    # The two extra cases only exercise pre-registered split structure. They
    # are test-only stubs; no model exposure or quality evidence is claimed.
    monkeypatch.setattr(
        finance_experiment_module, "derive_finance_case_cluster",
        lambda selected, case_ref: actual if case_ref == event_id else synthetic[case_ref],
    )
    cases = {
        **{
            case_ref: {
                "case_revision_digest": item["case_revision_digest"],
                "independence_cluster_id": item["cluster_id"],
                "split": split,
                "source_change_key": item["source_change_key"],
                "time_ordinal": index,
            }
            for index, (case_ref, item, split) in enumerate((
                ("case:unverified-1", synthetic["case:unverified-1"], "DEVELOPMENT"),
                ("case:unverified-2", synthetic["case:unverified-2"], "VALIDATION"),
            ), start=1)
        },
        event_id: {
            "case_revision_digest": actual["case_revision_digest"],
            "independence_cluster_id": actual["cluster_id"],
            "split": "SEALED_HOLDOUT",
            "source_change_key": actual["source_change_key"],
            "time_ordinal": 3,
        },
    }
    family_id = "family:test-only-unverified"
    suite_ref = "finance-sealed-suite:test-only-unverified"
    suite = {
        "schema_version": "orgrebase.finance-sealed-suite.v1",
        "suite_id": suite_id, "experiment_family_id": family_id,
        "evaluator_actor_id": EVALUATOR,
        "parent_head_ref": head.head_ref, "parent_head_digest": head.head_digest,
        "rubric_digest": REVIEWED_FINANCE_RUBRIC_DIGEST,
        "case_clusters": {event_id: actual["cluster_id"]},
        "case_gold": {
            event_id: {
                "case_revision_digest": actual["case_revision_digest"],
                "record_ref": commitment["gold_ref"],
                "record_digest": commitment["gold_digest"],
            },
        },
        "max_physical_attempts": 9, "max_reserved_microusd": 900,
    }
    with workspace.store.transaction() as connection:
        workspace.store.save_artifact(
            connection, suite_ref,
            "application/vnd.orgrebase.finance-sealed-suite.v1+json", suite,
        )
    ledger = FinanceExperimentService(
        workspace.store, tenant_id=scope,
        evaluator_actor_id=EVALUATOR, operator_actor_id=OPERATOR,
        workspace=workspace, release_governor_actor_id="actor:finance-governor",
    )
    clusters = tuple(cases[case_ref]["independence_cluster_id"] for case_ref in (
        "case:unverified-1", "case:unverified-2", event_id,
    ))
    with _as(scope, EVALUATOR, "governor"), pytest.raises(
        IntegrityError, match="SEALED_GOLD_UNVERIFIED"
    ):
        ledger.freeze_family(
            family_id=family_id,
            parent_head_ref=head.head_ref, parent_head_digest=head.head_digest,
            cases=cases, seeds=(11, 23, 37),
            orders={
                "CHRONOLOGICAL": clusters,
                "ORDER_STRATIFIED": (clusters[0], clusters[2], clusters[1]),
                "CLUSTER_SHUFFLED": tuple(reversed(clusters)),
            },
            rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
            max_queries=3, max_reserved_calls=9,
            max_reserved_microusd=900,
            execution_scope="REAL_VERTEX",
            sealed_suite_ref=suite_ref, sealed_suite_digest=sha256_digest(suite),
        )
    with pytest.raises(KeyError):
        workspace.store.load_artifact(ledger.family_ref(family_id), FAMILY_MEDIA)


def test_controlled_four_arm_v4_consumer_freezes_resources_and_budget(workspace, monkeypatch):
    event_id, head = _prepare(workspace)
    family_service = FinanceExperimentService(
        workspace.store, tenant_id=workspace.profile.organization_id,
        evaluator_actor_id="actor:experiment-evaluator",
        operator_actor_id="actor:experiment-operator", workspace=workspace,
    )
    cases = {
        event_id: {
            "case_revision_digest": sha256_digest({
                "change_set_digest": workspace._preview_record(event_id)["bundle"]["change_set"]["digest"],
                "preview_digest": workspace._preview_record(event_id)["bundle"]["preview"]["digest"],
            }),
            "independence_cluster_id": "cluster:live-one",
            "split": "DEVELOPMENT", "source_change_key": "source:live-one", "time_ordinal": 1,
        },
        "case:validation": {
            "case_revision_digest": sha256_digest("validation"),
            "independence_cluster_id": "cluster:validation", "split": "VALIDATION",
            "source_change_key": "source:validation", "time_ordinal": 2,
        },
        "case:heldout": {
            "case_revision_digest": sha256_digest("heldout"),
            "independence_cluster_id": "cluster:heldout", "split": "SEALED_HOLDOUT",
            "source_change_key": "source:heldout", "time_ordinal": 3,
        },
    }
    clusters = ("cluster:live-one", "cluster:validation", "cluster:heldout")
    with _as(workspace.profile.organization_id, "actor:experiment-evaluator", "governor"):
        family_ref = family_service.freeze_family(
            family_id="family:controlled-four", parent_head_ref=head.head_ref,
            parent_head_digest=head.head_digest, cases=cases,
            seeds=(11, 23, 37), orders={
                "CHRONOLOGICAL": clusters,
                "ORDER_STRATIFIED": (clusters[0], clusters[2], clusters[1]),
                "CLUSTER_SHUFFLED": tuple(reversed(clusters)),
            }, rubric_digest=sha256_digest("rubric:controlled"),
            max_queries=12, max_reserved_calls=100,
            max_reserved_microusd=50_000_000,
        )
    candidate = FinanceSkillHeadService(
        workspace.store, tenant_id=workspace.profile.organization_id,
    ).prepare_instruction_patch(
        head.bundle.instruction_text + "Check the specific source coverage gap.\n",
        expected_head_ref=head.head_ref, expected_head_digest=head.head_digest,
        expected_generation=head.generation,
        expected_package_digest=head.package_digest,
    )
    entry = LessonSnapshotEntry(
        lesson_ref="experience-lesson:controlled@r1", lesson_revision=1,
        content_digest=sha256_digest("controlled lesson content"),
        qualification_digest=sha256_digest("fixture qualification only"),
        cluster_digest=sha256_digest("cluster:other"),
        dependency_digest=sha256_digest("fixture dependency"),
    )
    snapshot = MemorySnapshot(
        tenant_id=workspace.profile.organization_id,
        workspace_id=workspace.store.workspace_id,
        profile_id="workspace-change-explanation-v1",
        lesson_entries=(entry,), coverage="COMPLETE",
        retrieval_version="controlled-fixture-recall.v1",
        compiler_version=FINANCE_V4_COMPILER_VERSION,
        budget_version="controlled-fixture-budget.v1",
    )
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
    views = {}
    with _as(workspace.profile.organization_id, "actor:experiment-operator", "operator"):
        for arm in ("STATIC_CURRENT", "NO_MEMORY", "HUMAN_REVIEWED", "SPARSE_RECALL"):
            view = evaluate_registered_finance_arm(
                workspace, ledger=family_service, family_ref=family_ref,
                event_id=event_id, seed=11, order="CHRONOLOGICAL", arm=arm,
                provider_override=_provider(),
                candidate_bundle=candidate if arm in {"HUMAN_REVIEWED", "SPARSE_RECALL"} else None,
                snapshot=snapshot if arm == "SPARSE_RECALL" else None,
                selected_lessons=(entry,) if arm == "SPARSE_RECALL" else (),
                advice_text="Only the current source can support this Finance explanation."
                if arm == "SPARSE_RECALL" else "",
            )
            assert view["status"] == "PROTOCOL_VALID", view
            assert view["experiment_trial_status"] == "CONTROLLED_PROTOCOL_VALID"
            views[arm] = view
            if arm == "STATIC_CURRENT":
                trial_ref = view["experiment_trial_ref"]
                guard = trial_ref.rsplit(":", 1)[1]
                binding = {
                    "ledger": family_service, "family_ref": family_ref,
                    "family_id": "family:controlled-four", "trial_ref": trial_ref,
                    "seed": 11, "order": "CHRONOLOGICAL", "arm": arm,
                    "refs": {
                        "guard": guard,
                        "intent_ref": "finance-evaluation-intent:forged-new-id",
                        "result_ref": "finance-evaluation-result:forged-new-id",
                        "private_ref": "finance-evaluation-private:forged-new-id",
                    },
                }
                before = len(sent)
                with pytest.raises(IntegrityError, match="EXPERIMENT_REFS_INVALID"):
                    evaluate_static_finance(
                        workspace, event_id=event_id,
                        operation_id="finance-experiment:" + guard,
                        provider_override=_provider(), _experiment=binding,
                    )
                assert len(sent) == before
                binding["refs"] = {
                    "guard": guard,
                    "intent_ref": f"finance-evaluation-intent:{guard}",
                    "result_ref": f"finance-evaluation-result:{guard}",
                    "private_ref": f"finance-evaluation-private:{guard}",
                }
                with pytest.raises(IntegrityError, match="EXPERIMENT_SCOPE_INVALID"):
                    evaluate_static_finance(
                        workspace, event_id=event_id,
                        operation_id="finance-experiment:changed-id",
                        provider_override=_provider(), _experiment=binding,
                    )
                assert len(sent) == before
        assert evaluate_registered_finance_arm(
            workspace, ledger=family_service, family_ref=family_ref,
            event_id=event_id, seed=11, order="CHRONOLOGICAL", arm="BOUNDED_GEPA",
            provider_override=_provider(),
        )["status"] == "NOT_RUN"
    with _as(workspace.profile.organization_id, "actor:experiment-evaluator", "governor"):
        projection = family_service.family_projection(family_ref)
        assert projection["statuses"]["CONTROLLED_PROTOCOL_VALID"] == 4
        assert projection["quality_status"] == "NOT_EVALUATED"
    assert len(sent) == 8  # Finance and GTM per real consumer invocation.
    assert views["STATIC_CURRENT"]["candidate_bundle_digest"] == head.bundle.digest
    assert views["NO_MEMORY"]["candidate_bundle_digest"] == head.bundle.digest
    assert views["HUMAN_REVIEWED"]["candidate_bundle_digest"] == candidate.digest
    assert views["SPARSE_RECALL"]["candidate_bundle_digest"] == candidate.digest
    assert workspace._approval_record(event_id) is None
    assert workspace._outcome_record(event_id) is None
    wires = {}
    for arm, view in views.items():
        private = PrivateRecordStore(
            workspace.store, workspace.clock, retention_seconds=workspace.private_retention_seconds,
        ).read(view["private_record_ref"])
        assert private is not None
        wires[arm] = private["finance_wire_body"]
    guidance = {
        arm: json.loads(wire["contents"][0]["parts"][0]["text"])["UNTRUSTED_ADVICE"]
        for arm, wire in wires.items()
    }
    assert guidance["STATIC_CURRENT"]["guidance"] == guidance["NO_MEMORY"]["guidance"]
    assert guidance["STATIC_CURRENT"]["memory"] == guidance["NO_MEMORY"]["memory"]
    assert guidance["HUMAN_REVIEWED"]["guidance"] != guidance["NO_MEMORY"]["guidance"]
    assert guidance["SPARSE_RECALL"]["memory"]["advice_text"]


def test_postgres_controlled_arm_crash_keeps_unknown_and_blocks_new_arm(
    workspace, postgres_runtime, monkeypatch,
):
    from orgrebase.workspace.advisory import WorkspaceChangeAdvisoryAdapter
    from orgrebase.workspace.service import WorkspaceService

    tenant = workspace.profile.organization_id
    database = postgres_runtime(tenant_id=tenant)
    target = WorkspaceService(
        store_path=database["runtime_dsn"], store_tenant_id=tenant,
        store_migrate=False, runtime_configuration=workspace.runtime_configuration,
        clock=workspace.clock, review_duration_seconds=0,
    )
    target.form_quote()
    try:
        event_id, head = _prepare(target)
        service = FinanceExperimentService(
            target.store, tenant_id=tenant,
            evaluator_actor_id="actor:experiment-evaluator",
            operator_actor_id="actor:experiment-operator", workspace=target,
        )
        revision = sha256_digest({
            "change_set_digest": target._preview_record(event_id)["bundle"]["change_set"]["digest"],
            "preview_digest": target._preview_record(event_id)["bundle"]["preview"]["digest"],
        })
        cases = {
            event_id: {
                "case_revision_digest": revision,
                "independence_cluster_id": "cluster:live",
                "split": "DEVELOPMENT", "source_change_key": "source:live", "time_ordinal": 1,
            },
            "case:validation": {
                "case_revision_digest": sha256_digest("validation"),
                "independence_cluster_id": "cluster:validation",
                "split": "VALIDATION", "source_change_key": "source:validation", "time_ordinal": 2,
            },
            "case:holdout": {
                "case_revision_digest": sha256_digest("holdout"),
                "independence_cluster_id": "cluster:holdout",
                "split": "SEALED_HOLDOUT", "source_change_key": "source:holdout", "time_ordinal": 3,
            },
        }
        clusters = ("cluster:live", "cluster:validation", "cluster:holdout")
        with _as(tenant, "actor:experiment-evaluator", "governor"):
            family_ref = service.freeze_family(
                family_id="family:postgres-unknown",
                parent_head_ref=head.head_ref, parent_head_digest=head.head_digest,
                cases=cases, seeds=(11, 23, 37), orders={
                    "CHRONOLOGICAL": clusters,
                    "ORDER_STRATIFIED": (clusters[0], clusters[2], clusters[1]),
                    "CLUSTER_SHUFFLED": tuple(reversed(clusters)),
                }, rubric_digest=sha256_digest("controlled-rubric"),
                max_queries=4, max_reserved_calls=100,
                max_reserved_microusd=50_000_000,
            )
        def interrupted(*_args, **_kwargs):
            raise KeyboardInterrupt("interrupted after durable Finance arm intent")

        monkeypatch.setattr(WorkspaceChangeAdvisoryAdapter, "run", interrupted)
        with _as(tenant, "actor:experiment-operator", "operator"):
            with pytest.raises(KeyboardInterrupt):
                evaluate_registered_finance_arm(
                    target, ledger=service, family_ref=family_ref,
                    event_id=event_id, seed=11, order="CHRONOLOGICAL",
                    arm="STATIC_CURRENT", provider_override=_provider(),
                )
            first_ref = service._trial_ref(
                family_ref, case_ref=event_id, seed=11,
                order="CHRONOLOGICAL", arm="STATIC_CURRENT",
            )
            assert service.trial_status(first_ref) == "RESULT_UNKNOWN"
    finally:
        target.close()
    restarted = WorkspaceService(
        store_path=database["runtime_dsn"], store_tenant_id=tenant,
        store_migrate=False, runtime_configuration=workspace.runtime_configuration,
        clock=workspace.clock, review_duration_seconds=0,
    )
    try:
        service = FinanceExperimentService(
            restarted.store, tenant_id=tenant,
            evaluator_actor_id="actor:experiment-evaluator",
            operator_actor_id="actor:experiment-operator", workspace=restarted,
        )
        with _as(tenant, "actor:experiment-operator", "operator"):
            previous = evaluate_registered_finance_arm(
                restarted, ledger=service, family_ref=family_ref,
                event_id=event_id, seed=11, order="CHRONOLOGICAL",
                arm="STATIC_CURRENT", provider_override=_provider(),
            )
            assert previous["status"] == "RESULT_UNKNOWN"
            assert previous["experiment_trial_status"] == "RESULT_UNKNOWN"
            with pytest.raises(IntegrityError, match="PREDECESSOR_UNKNOWN"):
                evaluate_registered_finance_arm(
                    restarted, ledger=service, family_ref=family_ref,
                    event_id=event_id, seed=11, order="CHRONOLOGICAL",
                    arm="NO_MEMORY", provider_override=_provider(),
                )
    finally:
        restarted.close()
