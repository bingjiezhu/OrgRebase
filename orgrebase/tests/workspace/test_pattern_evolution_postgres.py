from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError, ObjectState
from orgrebase.store import StateStore
from orgrebase.workspace.pattern_evolution import GovernedPatternService, ProposalBudget
from tests.postgres_support import postgres_cluster as postgres_cluster
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_pattern_evolution import Clock, decide, prepare, qualify


def service(store):
    return GovernedPatternService(
        store,
        corpus_authority="scripted:corpus-controller",
        evaluator_authority="scripted:replay-controller",
        governance_authority="scripted:skill-governance",
        clock=Clock(),
    )


def test_ordinary_postgres_role_persists_replays_admission_and_retraction(postgres_runtime):
    config = postgres_runtime(tenant_id="org:pattern")
    with StateStore(config["runtime_dsn"], tenant_id="org:pattern", migrate=False) as store:
        current = service(store)
        candidate, evaluation, _, corpus, _ = qualify(current)
        decide(current, candidate, evaluation)
        source_ref = current._family("admission")[0]["source_ref"]
        case_ref = current._load(corpus)["cases"][0]["case"]["digest"]
    with StateStore(config["runtime_dsn"], tenant_id="org:pattern", migrate=False) as store:
        resumed = service(store)
        resumed.retract(case_ref, actor_id=resumed.governance_authority, reason="source invalid")
        assert store.get_object(source_ref.rsplit("@", 1)[0]).state is ObjectState.REQUALIFICATION_REQUIRED
        assert store.verify_event_chain()["status"] == "PASS"


def test_two_postgres_connections_cannot_overspend_one_call(postgres_runtime):
    config = postgres_runtime(tenant_id="org:pattern")
    with (
        StateStore(config["runtime_dsn"], tenant_id="org:pattern", migrate=False) as first,
        StateStore(config["runtime_dsn"], tenant_id="org:pattern", migrate=False) as second,
    ):
        services = (service(first), service(second))
        proposal, _, _, _ = prepare(
            services[0], budget=ProposalBudget(max_cases=10, max_skill_invocations=1, max_seconds=100)
        )
        ready = Barrier(2)

        def charge(current):
            ready.wait(timeout=5)
            try:
                current._charge(proposal, invocations=1)
                return "RESERVED"
            except IntegrityError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(charge, services))
        assert sorted(result) == ["PATTERN_PROPOSAL_BUDGET_EXHAUSTED", "RESERVED"]
        assert sum(item["invocations"] for item in services[0]._usage(proposal)) == 1
        assert services[1]._terminal(proposal)["status"] == "EXHAUSTED"


def test_two_postgres_governance_calls_create_only_one_source(postgres_runtime):
    config = postgres_runtime(tenant_id="org:pattern")
    with (
        StateStore(config["runtime_dsn"], tenant_id="org:pattern", migrate=False) as first,
        StateStore(config["runtime_dsn"], tenant_id="org:pattern", migrate=False) as second,
    ):
        services = (service(first), service(second))
        candidate, evaluation, _, _, _ = qualify(services[0])
        ready = Barrier(2)

        def admit(current):
            ready.wait(timeout=5)
            try:
                decide(current, candidate, evaluation)
                return "ADMITTED"
            except IntegrityError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(admit, services))
        assert result.count("ADMITTED") == 1
        assert all(
            value in {"ADMITTED", "PATTERN_GOVERNANCE_ALREADY_FINAL", "PATTERN_PROPOSAL_TERMINAL"}
            for value in result
        )
        assert len(services[0]._family("admission")) == len(services[0]._family("decision")) == 1


def test_postgres_workspace_cannot_see_another_corpus(postgres_runtime):
    config = postgres_runtime(tenant_id="org:pattern")
    with StateStore(config["runtime_dsn"], tenant_id="org:pattern", migrate=False) as default:
        for workspace_id in ("one", "two"):
            default.register_workspace(
                workspace_id,
                profile_digest=sha256_digest({"profile": workspace_id}),
                pack_digest=None,
                quote_object_id=f"quote:{workspace_id}",
                created_at="2026-09-10T00:00:00Z",
            )
    with StateStore(
        config["runtime_dsn"], tenant_id="org:pattern", workspace_id="one", migrate=False
    ) as first:
        _, _, corpus, _ = prepare(service(first))
    with (
        StateStore(
            config["runtime_dsn"], tenant_id="org:pattern", workspace_id="two", migrate=False
        ) as second,
        pytest.raises(KeyError),
    ):
        service(second)._load(corpus)


def test_retraction_and_concurrent_successor_have_one_serialized_graph(postgres_runtime, monkeypatch):
    from threading import Event

    from orgrebase.impact import ImpactEngine
    from orgrebase.workspace.pattern_evolution import _record

    config = postgres_runtime(tenant_id="org:pattern")
    with (
        StateStore(config["runtime_dsn"], tenant_id="org:pattern", migrate=False) as first,
        StateStore(config["runtime_dsn"], tenant_id="org:pattern", migrate=False) as second,
    ):
        primary, other = service(first), service(second)
        candidate, evaluation, _, _, _ = qualify(primary)
        decide(primary, candidate, evaluation)
        source = primary._family("admission")[0]["source_ref"]
        graph_read, attempted, finished = Event(), Event(), Event()
        original = ImpactEngine.dependency_closure

        def paused(engine, refs):
            graph_read.set()
            assert attempted.wait(timeout=5)
            finished.wait(timeout=0.3)
            return original(engine, refs)

        monkeypatch.setattr(ImpactEngine, "dependency_closure", paused)

        def register():
            assert graph_read.wait(timeout=5)
            attempted.set()
            try:
                return other.register_successor(
                    source,
                    kind="plan",
                    payload=_record("plan-proof", scope="local"),
                    actor_id=other.governance_authority,
                )
            except IntegrityError as error:
                return str(error)
            finally:
                finished.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(register)
            receipt = primary._load(
                primary.retract(source, actor_id=primary.governance_authority, reason="invalid")
            )
            successor = future.result(timeout=5)
        assert successor == "PATTERN_SUCCESSOR_PARENT_RETRACTED" or successor in receipt["affected_refs"]
