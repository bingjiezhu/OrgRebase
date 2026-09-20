from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from orgrebase.clock import FrozenClock
from orgrebase.digest import sha256_digest
from orgrebase.domain import FreshnessError, IntegrityError, VersionedObject
from orgrebase.store import StateStore
from orgrebase.workspace import read_dependencies as dependencies
from orgrebase.workspace.formation import WorkspaceFormationService
from orgrebase.workspace.graph import workspace_seed_objects
from orgrebase.workspace.read_dependencies import (
    ReadQuery,
    capture_read_witness,
    dependency_payload,
    observe_query,
    validate_read_dependencies,
)

NOW = "2026-09-09T00:00:00Z"
RECORD = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"


def sealed(value):
    return {**value, "coverage_digest": sha256_digest(value)}


def coverage(value="allowed", *, deleted=False):
    return sealed(
        {
            "schema_version": "orgrebase.source-coverage.v1",
            "status": "COMPLETE",
            "reasons": [],
            **{
                key: sha256_digest(key)
                for key in (
                    "source_config_digest",
                    "source_binding_digest",
                    "inventory_digest",
                    "generation_digest",
                    "cursor_digest",
                    "page_digest",
                )
            },
            "connector_id": "source:qualified",
            "record_ids": [RECORD],
            "fields": ["new_condition"],
            "cursor_revision": 1,
            "page_ref": "source-page:1",
            "observed_at": NOW,
            "expires_at": "2026-09-09T00:05:00Z",
            "records": {
                RECORD: {
                    "revision": 'W/"1"',
                    "deleted": deleted,
                    "field_digests": {"new_condition": sha256_digest(value)},
                    "observed_at": NOW,
                }
            },
        }
    )


def query(**kwargs):
    return ReadQuery(
        connector_id="source:qualified",
        record_ids=(RECORD,),
        operator="eq",
        field="new_condition",
        value="forbidden",
        **kwargs,
    )


def changed(value, **updates):
    result = {key: deepcopy(item) for key, item in value.items() if key != "coverage_digest"}
    result.update(updates)
    return sealed(result)


@pytest.mark.parametrize("value,expected", [("allowed", "FALSE"), ("forbidden", "TRUE"), (None, "FALSE")])
def test_query_result_requires_complete_bounded_coverage(value, expected):
    result = observe_query(query(), coverage(value), now=NOW)
    assert result.result == expected
    assert result.matched_record_ids == ((RECORD,) if expected == "TRUE" else ())


def test_missing_means_observed_null_field_not_missing_record_or_field():
    missing = ReadQuery(
        connector_id="source:qualified", record_ids=(RECORD,), operator="is_missing", field="new_condition"
    )
    assert observe_query(missing, coverage(None), now=NOW).result == "TRUE"
    assert observe_query(missing, coverage(""), now=NOW).result == "FALSE"
    assert observe_query(missing, coverage(deleted=True), now=NOW).result == "UNKNOWN"
    empty = coverage()
    empty["records"][RECORD]["field_digests"] = {}
    assert observe_query(missing, changed(empty), now=NOW).result == "UNKNOWN"
    assert observe_query(missing, changed(coverage(), records={}), now=NOW).result == "UNKNOWN"


@pytest.mark.parametrize(
    "reason",
    [
        "SOURCE_PAGINATION_INCOMPLETE",
        "SOURCE_PERMISSION_DENIED",
        "SOURCE_FIELDS_UNAVAILABLE",
        "SOURCE_RECORDS_NOT_OBSERVED",
        "SOURCE_CURSOR_EXPIRED",
    ],
)
def test_incomplete_or_unavailable_read_never_becomes_false(reason):
    result = observe_query(query(), changed(coverage(), status="UNKNOWN", reasons=[reason]), now=NOW)
    assert result.result == "UNKNOWN" and result.reasons == (reason,)


@pytest.mark.parametrize("now", ["2026-09-08T23:59:59Z", "2026-09-09T00:05:00Z"])
def test_expired_or_future_coverage_is_unknown(now):
    assert observe_query(query(), coverage(), now=now).result == "UNKNOWN"


def test_predicate_scope_and_forged_coverage_are_rejected():
    with pytest.raises(IntegrityError, match="READ_COVERAGE_DIGEST_INVALID"):
        observe_query(query(), {**coverage(), "status": "COMPLETE", "records": {}}, now=NOW)
    with pytest.raises(IntegrityError, match="READ_QUERY_OUTSIDE_SOURCE_SCOPE"):
        observe_query(
            ReadQuery(
                connector_id="source:qualified",
                record_ids=(OTHER,),
                operator="eq",
                field="new_condition",
                value="forbidden",
            ),
            coverage(),
            now=NOW,
        )
    with pytest.raises(IntegrityError, match="READ_QUERY_FIELD_OUTSIDE_SOURCE_SCOPE"):
        observe_query(
            ReadQuery(
                connector_id="source:qualified",
                record_ids=(RECORD,),
                operator="eq",
                field="secret",
                value="forbidden",
            ),
            coverage(),
            now=NOW,
        )
    with pytest.raises(ValueError):
        ReadQuery(
            connector_id="source:qualified",
            record_ids=(RECORD,),
            operator="sql",
            field="new_condition",
            value="SELECT *",
        )
    with pytest.raises(ValueError, match="READ_QUERY_FIELD_REQUIRED"):
        ReadQuery(connector_id="source:qualified", record_ids=(RECORD,), operator="is_missing")


def _premise(payload):
    original = workspace_seed_objects()[0]
    value = original.model_dump(mode="json", exclude={"digest"})
    value["payload"]["read_dependencies"] = payload
    return VersionedObject.model_validate(value)


def _source_premise(store, *, revision='W/"1"'):
    reference = "source-observation:" + sha256_digest({
        "connector": "source:qualified", "record": RECORD,
        "revision": revision, "field": "new_condition",
    })[7:]
    with store.transaction() as connection:
        store.save_artifact(connection, reference, "application/json", {
            "source_ref": f"https://company.crm.dynamics.com/api/data/v9.2/quotes({RECORD})#new_condition",
            "revision": revision, "value_digest": sha256_digest("allowed"),
            "page_ref": "source-page:1", "observed_at": NOW,
        })
    value = workspace_seed_objects()[0].model_dump(mode="json", exclude={"digest"})
    value["payload"]["source_observation_ref"] = reference
    value["payload"]["canonical_value"] = "allowed"
    value.update(version="source-test-observation", state="PROPOSED")
    return VersionedObject.model_validate(value)


@pytest.mark.parametrize("value", ["allowed", "new-value", "original-value"])
def test_source_observation_is_replaced_even_when_values_match_or_return(monkeypatch, value):
    latest = coverage()
    monkeypatch.setattr(dependencies, "_coverage", lambda workspace, now: latest)
    with StateStore() as store:
        workspace = SimpleNamespace(store=store, clock=FrozenClock(NOW))
        premise = _source_premise(store)
        dependencies.validate_source_observations(workspace, (premise,), NOW)
        latest = coverage(value)
        latest["records"][RECORD]["revision"] = 'W/"opaque-earlier-lexically"'
        latest = changed(latest, cursor_revision=2)
        with pytest.raises(FreshnessError, match="SOURCE_OBSERVATION_REPLACED"):
            dependencies.validate_source_observations(workspace, (premise,), NOW)


@pytest.mark.parametrize("failure", ["unknown", "empty", "deleted", "binding", "expiry", "no-config"])
def test_source_observation_never_survives_unavailable_or_rebound_coverage(monkeypatch, failure):
    latest = coverage()
    monkeypatch.setattr(dependencies, "_coverage", lambda workspace, now: latest)
    with StateStore() as store:
        workspace = SimpleNamespace(store=store, clock=FrozenClock(NOW))
        premise = _source_premise(store)
        if failure == "unknown":
            latest = changed(latest, status="UNKNOWN", reasons=["SOURCE_PERMISSION_DENIED"])
        elif failure == "empty":
            latest = changed(latest, records={})
        elif failure == "deleted":
            latest = coverage(deleted=True)
        elif failure == "binding":
            latest = changed(latest, connector_id="source:replacement")
        elif failure == "expiry":
            workspace.clock = FrozenClock("2026-09-09T00:05:00Z")
        else:
            monkeypatch.undo()
        with pytest.raises(FreshnessError, match="READ_DEPENDENCY_"):
            dependencies.validate_source_observations(workspace, (premise,), NOW)


def test_source_observation_ignores_new_pages_and_does_not_requalify_applied_facts(monkeypatch):
    latest = coverage()
    monkeypatch.setattr(dependencies, "_coverage", lambda workspace, now: latest)
    with StateStore() as store:
        workspace = SimpleNamespace(store=store, clock=FrozenClock(NOW))
        premise = _source_premise(store)
        latest = changed(latest, cursor_revision=2, cursor_digest=sha256_digest("new page"))
        dependencies.validate_source_observations(workspace, (premise,), NOW)
        latest["records"][RECORD]["revision"] = 'W/"new-current-same-value"'
        latest = changed(latest)
        current = VersionedObject.model_validate({
            **premise.model_dump(mode="json", exclude={"digest"}), "state": "CURRENT",
        })
        dependencies.validate_source_observations(workspace, (current,), NOW)
        manual = VersionedObject.model_validate({
            **premise.model_dump(mode="json", exclude={"digest"}), "version": "proposal-manual",
        })
        dependencies.validate_source_observations(workspace, (manual,), NOW)


@pytest.mark.parametrize(
    "change",
    ["new_match", "generation", "inventory", "mapping", "watermark", "scope", "permission", "expiry"],
)
def test_persisted_false_witness_cannot_survive_relevant_source_change(monkeypatch, change):
    latest = coverage()
    monkeypatch.setattr(dependencies, "_coverage", lambda workspace, now: latest)
    with StateStore() as store:
        workspace = SimpleNamespace(store=store, clock=FrozenClock(NOW))
        witness = capture_read_witness(workspace, query())
        premise = _premise(dependency_payload(witness))
        validate_read_dependencies(workspace, (premise,), NOW)
        if change == "new_match":
            latest = coverage("forbidden")
        elif change in {"generation", "inventory"}:
            latest = changed(latest, **{change + "_digest": sha256_digest("new")})
        elif change == "mapping":
            latest = changed(latest, source_binding_digest=sha256_digest("new"))
        elif change == "watermark":
            latest = changed(latest, cursor_revision=2, cursor_digest=sha256_digest("new"))
        elif change == "scope":
            latest = changed(latest, record_ids=[RECORD, OTHER])
        elif change == "permission":
            latest = changed(latest, status="UNKNOWN", reasons=["SOURCE_PERMISSION_DENIED"])
        else:
            workspace.clock = FrozenClock("2026-09-09T00:05:00Z")
        with pytest.raises(FreshnessError, match=r"READ_DEPENDENCY_(CHANGED|UNKNOWN)"):
            # Historical compilation time cannot revive current expired coverage.
            validate_read_dependencies(workspace, (premise,), NOW)


def test_witness_reference_and_explicit_invalid_contract_fail_closed(monkeypatch):
    monkeypatch.setattr(dependencies, "_coverage", lambda workspace, now: coverage())
    with StateStore() as store:
        workspace = SimpleNamespace(store=store, clock=FrozenClock(NOW))
        witness = capture_read_witness(workspace, query())
        reference = dependency_payload(witness)
        reference["witnesses"][0]["expected_result"] = "TRUE"
        with pytest.raises(IntegrityError, match="READ_DEPENDENCY_WITNESS_BINDING_INVALID"):
            validate_read_dependencies(workspace, (_premise(reference),), NOW)
        for malformed in (None, {}, {"schema_version": "unsupported", "witnesses": []}):
            with pytest.raises(IntegrityError, match="READ_DEPENDENCIES_CONTRACT_INVALID"):
                validate_read_dependencies(workspace, (_premise(malformed),), NOW)


@pytest.mark.parametrize("missing_validator", [False, True])
def test_actual_task_context_and_formation_commit_revalidate_witness(monkeypatch, missing_validator):
    latest = coverage()
    monkeypatch.setattr(dependencies, "_coverage", lambda workspace, now: latest)
    with StateStore() as store:
        workspace = SimpleNamespace(store=store, clock=FrozenClock(NOW))
        witness = capture_read_witness(workspace, query())
        objects = []
        for original in workspace_seed_objects():
            value = original.model_dump(mode="json", exclude={"digest"})
            if original.id == "claim:product.enterprise_plan":
                value["payload"]["read_dependencies"] = dependency_payload(witness)
            objects.append(VersionedObject.model_validate(value))
        formation = WorkspaceFormationService(
            store,
            seed_objects=tuple(objects),
            read_dependency_validator=None
            if missing_validator
            else lambda premises, now: validate_read_dependencies(workspace, premises, now),
        )
        formation.clock = workspace.clock
        if missing_validator:
            with pytest.raises(IntegrityError, match="READ_DEPENDENCY_VALIDATOR_REQUIRED"):
                formation.prepare_quote(formation.default_request())
            return
        prepared = formation.prepare_quote(formation.default_request())
        baseline = store.audit_head()
        latest = coverage("forbidden")
        with pytest.raises(
            IntegrityError,
            match="PREPARED_FORMATION_PROOF_GRAPH_INVALID:ReadDependencyError:READ_DEPENDENCY_CHANGED",
        ):
            formation.commit_quote(prepared)
        assert store.audit_head() == baseline
        with pytest.raises(KeyError):
            store.get_object("work:quote_acme")
        latest = coverage()
        receipt = formation.commit_quote(prepared)
        assert store.get_object("work:quote_acme").ref == receipt.deliverable_ref


def test_formation_rechecks_dependency_inside_canonical_write_transaction(monkeypatch):
    attack = False
    checks = []

    def read(workspace, now):
        checks.append(workspace.store.connection.in_transaction)
        return coverage("forbidden") if attack and workspace.store.connection.in_transaction else coverage()

    monkeypatch.setattr(dependencies, "_coverage", read)
    with StateStore() as store:
        workspace = SimpleNamespace(store=store, clock=FrozenClock(NOW))
        witness = capture_read_witness(workspace, query())
        objects = []
        for original in workspace_seed_objects():
            value = original.model_dump(mode="json", exclude={"digest"})
            if original.id == "claim:product.enterprise_plan":
                value["payload"]["read_dependencies"] = dependency_payload(witness)
            objects.append(VersionedObject.model_validate(value))
        formation = WorkspaceFormationService(
            store,
            seed_objects=tuple(objects),
            read_dependency_validator=lambda premises, now: validate_read_dependencies(
                workspace, premises, now
            ),
        )
        formation.clock = workspace.clock
        prepared = formation.prepare_quote(formation.default_request())
        before = store.audit_head()
        checks.clear()
        attack = True
        with pytest.raises(IntegrityError, match="ReadDependencyError:READ_DEPENDENCY_CHANGED"):
            formation.commit_quote(prepared)
        assert checks == [True]
        assert store.audit_head() == before
        with pytest.raises(KeyError):
            store.get_object("work:quote_acme")
