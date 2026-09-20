"""A real bounded Skill rehearsal, isolated from business release authority."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import PRINCIPAL_IDENTITY_MODES, AuthenticationError, Principal, request_principal
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace import skill_validation
from orgrebase.workspace.quote_skill_qualification import REVISION, SUITE_DIGEST, has_complete_case_identity
from orgrebase.workspace.service import CONTROLLED_LOCAL_HEADER_IDENTITY, WorkspaceService
from orgrebase.workspace.skill_packages import PARTITIONS, SkillPackageRegistry
from orgrebase.workspace.skill_rollback import load_frozen_predecessor, verify_rollback_execution_receipt
from tests.workspace.test_continuous_changes import make_service

PATH = "/api/workspace/skills/enterprise-quote-compose/validation"


def arguments(view):
    return {"actor_id": "human:skill-steward", "expected_package_digest": view["binding"]["package_digest"],
            "expected_predecessor_digest": view["binding"]["predecessor_digest"],
            "expected_catalog_digest": view["binding"]["compatibility_catalog_digest"]}


@pytest.mark.parametrize("identity_mode", sorted(PRINCIPAL_IDENTITY_MODES))
def test_authenticated_workspace_requires_principal_before_validation_reservation(tmp_path, monkeypatch, identity_mode):
    service = WorkspaceService(store_path=tmp_path / "authentication.sqlite", approval_identity_mode=identity_mode)
    token = request_principal.set(None)
    try:
        initial = skill_validation.validation_view(service)
        assert initial["can_execute"] is False
        def no_execution(*args, **kwargs):
            raise AssertionError("Unauthenticated validation must not reserve or execute")
        monkeypatch.setattr(skill_validation, "_execute", no_execution)
        with pytest.raises(AuthenticationError, match="AUTH_VERIFIED_PRINCIPAL_REQUIRED"):
            skill_validation.execute_validation(service, **arguments(initial))
        assert skill_validation.validation_view(service)["state"] == "NOT_RUN"
    finally:
        request_principal.reset(token)
        service.close()


def test_real_validation_is_bound_recoverable_and_does_not_release_business_capability(tmp_path, monkeypatch):
    path = tmp_path / "workspace.sqlite"
    service = make_service(path)
    runtime = service.runtime_configuration
    try:
        before = service.current_quote()
        package_before = SkillPackageRegistry().load("enterprise-quote-compose").package_digest
        initial = skill_validation.validation_view(service)
        assert initial["state"] == "NOT_RUN" and initial["result"] is None
        result = skill_validation.execute_validation(service, **arguments(initial))
        proof = result["result"]["evidence"]
        assert result["state"] == "PASS" and result["can_execute"] is False
        assert proof["skill_calls"] == 38 and proof["http_calls"] == 1
        assert result["limits"]["max_skill_calls"] == 38
        assert initial["binding"]["qualification_suite_revision"] == REVISION
        assert initial["binding"]["qualification_suite_digest"] == SUITE_DIGEST
        assert has_complete_case_identity(proof["evaluation"], proof["run_id"])
        assert result["synthetic_inputs"] is True and result["production_canary"] is False
        assert {item["partition"] for item in proof["evaluation"]["case_results"]} == set(PARTITIONS)
        assert [item["to_state"] for item in proof["release_history"]] == ["EVALUATED", "SHADOW", "CANARY"]
        assert proof["trial"]["receipt"]["package_digest"] == initial["binding"]["package_digest"]
        assert proof["dependency_tool"]["receipt"]["evidence_class"] == "CONTROLLED_LOCAL_REAL_HTTP"
        assert proof["rollback"]["receipt"]["effective_package_digest"] == initial["binding"]["predecessor_digest"]
        assert proof["rollback"]["receipt"]["restoration_status"] == "EXECUTED_AND_INVOKED"
        assert proof["rollback"]["result"] == proof["baseline"]["result"]
        verify_rollback_execution_receipt(proof["rollback"]["receipt"], load_frozen_predecessor())
        assert service.current_quote() == before
        assert SkillPackageRegistry().load("enterprise-quote-compose").package_digest == package_before
        def no_new_execution(*args, **kwargs):
            raise AssertionError("Completed validation must not execute again")
        monkeypatch.setattr(skill_validation, "_execute", no_new_execution)
        assert skill_validation.execute_validation(service, **arguments(initial)) == result
    finally:
        service.close()
    reopened = WorkspaceService.reopen(path, runtime_configuration=runtime)
    try:
        assert skill_validation.validation_view(reopened) == result
        assert skill_validation.execute_validation(reopened, **arguments(initial)) == result
    finally:
        reopened.close()


@pytest.mark.parametrize("field,value,error", [
    ("actor_id", "human:finance-owner", "ACTOR_DENIED"),
    ("expected_package_digest", "sha256:" + "0" * 64, "REVIEWED_VERSION_CHANGED"),
    ("expected_predecessor_digest", "sha256:" + "0" * 64, "REVIEWED_VERSION_CHANGED"),
    ("expected_catalog_digest", "sha256:" + "0" * 64, "REVIEWED_VERSION_CHANGED"),
])
def test_unreviewed_version_or_wrong_actor_cannot_start(tmp_path, field, value, error):
    service = WorkspaceService(store_path=tmp_path / "denied.sqlite")
    try:
        initial = skill_validation.validation_view(service)
        request = {**arguments(initial), field: value}
        with pytest.raises((IntegrityError, AuthorizationError), match=error):
            skill_validation.execute_validation(service, **request)
        assert skill_validation.validation_view(service)["state"] == "NOT_RUN"
    finally:
        service.close()


def test_retained_eight_case_pass_cannot_satisfy_current_qualification(tmp_path, monkeypatch):
    service = WorkspaceService(store_path=tmp_path / "legacy-suite.sqlite")
    original_scope = skill_validation._scope
    original_cases = skill_validation._quote_skill_evaluation_cases
    original_premise = skill_validation.qualification_premise

    def legacy_scope(workspace):
        registry, package, predecessor, binding, _ = original_scope(workspace)
        binding.pop("qualification_suite_revision")
        binding.pop("qualification_suite_digest")
        return registry, package, predecessor, binding, "skill-validation:" + skill_validation.sha256_digest(binding)[7:]

    def legacy_premise(package):
        premise = original_premise(package)
        premise.pop("qualification_suite_revision")
        premise.pop("qualification_suite_digest")
        return premise

    try:
        with monkeypatch.context() as legacy:
            legacy.setattr(skill_validation, "_scope", legacy_scope)
            legacy.setattr(skill_validation, "_quote_skill_evaluation_cases", lambda **kwargs: original_cases(**kwargs)[:-1])
            legacy.setattr(skill_validation, "qualification_premise", legacy_premise)
            previous = skill_validation.execute_validation(service, **arguments(skill_validation.validation_view(service)))
            old_key = legacy_scope(service)[-1]
            assert previous["state"] == "PASS"
            assert previous["result"]["evidence"]["skill_calls"] == 37
        current = skill_validation.validation_view(service)
        assert current["state"] == "NOT_RUN" and current["can_execute"] is True
        assert original_scope(service)[-1] != old_key
        completed = skill_validation.execute_validation(service, **arguments(current))
        assert completed["state"] == "PASS" and completed["result"]["evidence"]["skill_calls"] == 38
        assert service.store.load_artifact(old_key, skill_validation.MEDIA).payload == previous["result"]
    finally:
        service.close()


def test_concurrent_connections_reserve_once_and_reads_do_not_wait_for_execution(tmp_path, monkeypatch):
    path = tmp_path / "concurrent.sqlite"
    first = WorkspaceService(store_path=path)
    second = WorkspaceService.reopen(path)
    entered, release = Event(), Event()
    original = skill_validation._execute
    calls = []
    def block(*args, **kwargs):
        calls.append(kwargs["key"])
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(skill_validation, "_execute", block)
    request = arguments(skill_validation.validation_view(first))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(skill_validation.execute_validation, first, **request)
            assert entered.wait(3)
            try:
                assert pool.submit(skill_validation.validation_view, second).result(timeout=2)["state"] == "IN_PROGRESS"
                assert pool.submit(skill_validation.execute_validation, second, **request).result(timeout=2)["state"] == "IN_PROGRESS"
                assert len(calls) == 1
            finally:
                release.set()
            assert pending.result(timeout=5)["state"] == "PASS"
    finally:
        release.set()
        first.close()
        second.close()


def test_failed_and_unknown_attempts_remain_readable_without_automatic_retry(tmp_path, monkeypatch):
    service = WorkspaceService(store_path=tmp_path / "failure.sqlite")
    calls = []
    def fail(*args, **kwargs):
        calls.append(kwargs["key"])
        raise IntegrityError("SKILL_VALIDATION_QUALIFICATION_REJECTED")
    monkeypatch.setattr(skill_validation, "_execute", fail)
    try:
        request = arguments(skill_validation.validation_view(service))
        with pytest.raises(IntegrityError, match="QUALIFICATION_REJECTED"):
            skill_validation.execute_validation(service, **request)
        saved = skill_validation.validation_view(service)
        assert saved["state"] == "FAILED" and saved["result"]["error_code"] == "SKILL_VALIDATION_QUALIFICATION_REJECTED"
        assert skill_validation.execute_validation(service, **request) == saved and len(calls) == 1
    finally:
        service.close()
    unknown = WorkspaceService(store_path=tmp_path / "unknown.sqlite")
    try:
        _, _, _, binding, key = skill_validation._scope(unknown)
        with unknown.store.transaction() as connection:
            unknown.store.save_idempotent(connection, key, skill_validation.sha256_digest(binding), {
                "binding": binding, "actor_id": "human:skill-steward", "deadline_epoch_ms": 1})
        view = skill_validation.validation_view(unknown)
        assert view["state"] == "RESULT_UNKNOWN"
        assert skill_validation.execute_validation(unknown, **arguments(view))["state"] == "RESULT_UNKNOWN"
        assert len(calls) == 1
    finally:
        unknown.close()


def test_postgresql_workspace_scope_and_viewer_permissions_do_not_reuse_other_result(postgres_runtime):
    database = postgres_runtime(tenant_id="org:northstar")
    options = {"store_path": database["runtime_dsn"], "store_tenant_id": "org:northstar", "store_migrate": False}
    first = WorkspaceService(**options)
    first.store.register_workspace("beta", profile_digest=first.profile_digest, pack_digest=None,
        quote_object_id=first.quote_object_id, created_at=first.clock.now())
    second = WorkspaceService(**options, workspace_id="beta")
    try:
        initial = skill_validation.validation_view(first)
        skill_validation.execute_validation(first, **arguments(initial))
        assert skill_validation.validation_view(second)["state"] == "NOT_RUN"
        assert skill_validation._scope(first)[-1] != skill_validation._scope(second)[-1]
        principal = Principal(issuer="test", subject="reader", tenant_id=second.profile.organization_id,
            actor_id="human:skill-steward", roles=frozenset({"reader"}), expires_at=int(time.time()) + 60)
        token = request_principal.set(principal)
        try:
            assert skill_validation.validation_view(second)["can_execute"] is False
        finally:
            request_principal.reset(token)
    finally:
        first.close()
        second.close()


def test_corrupt_reservation_cannot_be_presented_as_a_valid_pending_attempt(tmp_path):
    service = WorkspaceService(store_path=tmp_path / "reservation.sqlite")
    try:
        _, _, _, binding, key = skill_validation._scope(service)
        with service.store.transaction() as connection:
            service.store.save_idempotent(connection, key, skill_validation.sha256_digest(binding), {
                "binding": {**binding, "workspace_id": "other"}, "actor_id": "human:skill-steward",
                "deadline_epoch_ms": service._wall_clock_epoch_ms() + 1000})
        with pytest.raises(IntegrityError, match="RESERVATION_BINDING_INVALID"):
            skill_validation.validation_view(service)
    finally:
        service.close()


def test_http_contract_requires_real_caller_and_rejects_arbitrary_versions_and_paths(tmp_path):
    service = WorkspaceService(store_path=tmp_path / "api.sqlite", approval_identity_mode=CONTROLLED_LOCAL_HEADER_IDENTITY)
    try:
        with TestClient(create_app(workspace_service=service)) as client:
            initial = client.get(PATH).json()
            payload = arguments(initial)
            assert client.post(PATH, json=payload).status_code == 403
            assert client.post(PATH, json=payload, headers={"X-OrgRebase-Actor": "human:finance-owner"}).status_code == 403
            headers = {"X-OrgRebase-Actor": "human:skill-steward"}
            assert client.post(PATH, json={**payload, "path": "unapproved/package"}, headers=headers).status_code == 409
            assert client.post(PATH, json={**payload, "version": "99.0"}, headers=headers).status_code == 409
            result = client.post(PATH, json=payload, headers=headers)
            assert result.status_code == 200 and result.json()["state"] == "PASS"
            assert client.get(PATH).json() == result.json()
    finally:
        service.close()
