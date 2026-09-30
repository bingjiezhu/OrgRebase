"""Authenticated maintenance command and PostgreSQL old-writer role fence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from orgrebase.auth import Principal
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace import pattern_head_maintenance as maintenance
from orgrebase.workspace.pattern_evolution import GovernedPatternService
from orgrebase.workspace.quote_recovery_learning import TARGET_SKILL, quote_recovery_content_bundle
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_pattern_evolution import decide, qualify
from tests.workspace.test_pattern_legacy_migration import _seed_one_legal_v1_head

TENANT = "org:head-maintenance"
CORPUS = "scripted:corpus-controller"
AUTHOR = "learner:bounded"
EVALUATOR = "scripted:replay-controller"
GOVERNOR = "scripted:skill-governance"


def _service(store):
    return GovernedPatternService(
        store,
        corpus_authority=CORPUS,
        evaluator_authority=EVALUATOR,
        governance_authority=GOVERNOR,
    )


def _config(service, *, old_role, action, expected_package_digest, **overrides):
    bundle = quote_recovery_content_bundle(service.registry)
    value = {
        "schema_version": "orgrebase.pattern-head-maintenance.v1",
        "enabled": True,
        "action": action,
        "workspace_id": service.store.workspace_id,
        "access_token_variable": "ORGREBASE_TEST_GOVERNOR_TOKEN",
        "maintenance_window_id": "maintenance:controlled-20260928",
        "legacy_writer_roles": [old_role],
        "corpus_actor_id": CORPUS,
        "author_actor_id": AUTHOR,
        "evaluator_actor_id": EVALUATOR,
        "governor_actor_id": GOVERNOR,
        "reviewed_bundle_digest": bundle.digest,
        "reviewed_target_skill": TARGET_SKILL,
        "reviewed_predecessor_package_digest": service.registry.load(TARGET_SKILL).package_digest,
        "expected_source_ref": None,
        "expected_source_digest": None,
        "expected_head_ref": None,
        "expected_head_digest": None,
        "expected_generation": None,
        "expected_package_digest": expected_package_digest,
        "expected_previous_ref": None,
        "expected_previous_digest": None,
        "expected_transition_kind": None,
        "original_mutation_config_path": None,
        "expected_mutation_config_digest": None,
        "reason": None,
    }
    value.update(overrides)
    return value


def _write_config(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    path.chmod(0o600)


def _admin_setup(config, old_role):
    with psycopg.connect(config["migration_dsn"], autocommit=True) as admin:
        admin.execute(sql.SQL("ALTER ROLE {} INHERIT").format(sql.Identifier(config["role_name"])))
        admin.execute(sql.SQL("GRANT pg_read_all_stats TO {}").format(sql.Identifier(config["role_name"])))
        admin.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(old_role)))


def _admin_disable(config, old_role):
    with psycopg.connect(config["migration_dsn"], autocommit=True) as admin:
        admin.execute(sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(old_role)))


def _admin_drop(config, old_role):
    with psycopg.connect(config["migration_dsn"], autocommit=True) as admin:
        admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(old_role)))


def _role_dsn(dsn, role):
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(netloc=role + "@"))


@dataclass
class _FakeSettings:
    tenant_id: str
    workspace_id: str = "default"
    mode: str = "production"

    @property
    def identity(self):
        return SimpleNamespace(tenant_id=self.tenant_id)

    def for_workspace(self, workspace_id):
        if workspace_id != self.workspace_id:
            raise AssertionError("wrong workspace")
        return self

    def authorize_workspace(self, subject):
        if subject != "subject:governor":
            raise AssertionError("wrong subject")


class _FakeAuthenticator:
    def __init__(self, _identity):
        pass

    def authenticate(self, authorization):
        if authorization != "Bearer test":
            raise AssertionError("wrong token")
        return Principal(
            "https://issuer.example", "subject:governor", TENANT,
            GOVERNOR, frozenset({"governor"}), 4_000_000_000,
        )


def _patch_runtime(monkeypatch, store):
    settings = _FakeSettings(TENANT, store.workspace_id)
    monkeypatch.setattr(
        maintenance.DeploymentSettings, "from_environment",
        classmethod(lambda cls: settings),
    )
    monkeypatch.setattr(maintenance, "JWTAuthenticator", _FakeAuthenticator)
    monkeypatch.setattr(
        maintenance, "open_workspace",
        lambda _settings: SimpleNamespace(store=store, close=lambda: None),
    )
    monkeypatch.setenv("ORGREBASE_TEST_GOVERNOR_TOKEN", "test")


def test_maintenance_config_is_exact_and_file_is_not_symlinked(tmp_path, capsys):
    path = tmp_path / "maintenance.json"
    value = {
        "schema_version": "orgrebase.pattern-head-maintenance.v1",
        "enabled": True,
        "action": "MIGRATE",
        "workspace_id": "default",
        "access_token_variable": "TOKEN",
        "maintenance_window_id": "window:one",
        "legacy_writer_roles": ["old_writer"],
        "corpus_actor_id": CORPUS,
        "author_actor_id": AUTHOR,
        "evaluator_actor_id": EVALUATOR,
        "governor_actor_id": GOVERNOR,
        "reviewed_bundle_digest": "sha256:" + "1" * 64,
        "reviewed_target_skill": TARGET_SKILL,
        "reviewed_predecessor_package_digest": "sha256:" + "2" * 64,
        "expected_source_ref": "admitted-pattern-skill:" + "a" * 64 + "@1",
        "expected_source_digest": "sha256:" + "3" * 64,
        "expected_package_digest": "sha256:" + "4" * 64,
    }
    _write_config(path, value)
    parsed = maintenance.load_pattern_head_maintenance_config(path)
    assert parsed.action == "MIGRATE"
    assert maintenance.main([
        "--config", str(path), "--show-config-identity",
    ]) == 0
    identity = json.loads(capsys.readouterr().out)
    assert identity["config_digest"] == parsed.digest
    assert identity["database_fence_verified"] is False
    assert identity["mutation_executed"] is False
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="CONFIG_INVALID"):
        maintenance.load_pattern_head_maintenance_config(link)
    value["expected_generation"] = 1
    _write_config(path, value)
    with pytest.raises(ValueError, match="CONFIG_INVALID"):
        maintenance.load_pattern_head_maintenance_config(path)


def test_postgres_role_fence_rejects_login_and_live_old_session(postgres_runtime):
    config = postgres_runtime(tenant_id=TENANT)
    old_role = "old_" + config["role_name"].removeprefix("app_")
    _admin_setup(config, old_role)
    try:
        with StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as store:
            fence = maintenance.PostgresLegacyWriterFence(store, (old_role,))
            with pytest.raises(IntegrityError, match="LEGACY_ROLE_NOT_DISABLED"):
                fence.verify()
            old_session = psycopg.connect(_role_dsn(config["migration_dsn"], old_role))
            try:
                _admin_disable(config, old_role)
                with pytest.raises(IntegrityError, match="LEGACY_SESSION_ACTIVE"):
                    fence.verify()
            finally:
                old_session.close()
            fence.verify()
            member_role = "member_" + config["role_name"].removeprefix("app_")
            with psycopg.connect(config["migration_dsn"], autocommit=True) as admin:
                admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(member_role)))
                admin.execute(
                    sql.SQL("GRANT {} TO {}").format(
                        sql.Identifier(old_role), sql.Identifier(member_role)
                    )
                )
            try:
                with pytest.raises(IntegrityError, match="LEGACY_ROLE_INHERITABLE"):
                    fence.verify()
            finally:
                with psycopg.connect(config["migration_dsn"], autocommit=True) as admin:
                    admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(member_role)))
            fence.verify()
            with psycopg.connect(config["migration_dsn"], autocommit=True) as admin:
                admin.execute(sql.SQL("ALTER ROLE {} LOGIN").format(sql.Identifier(old_role)))
            with pytest.raises(IntegrityError, match="LEGACY_ROLE_NOT_DISABLED"):
                fence.verify()
    finally:
        _admin_drop(config, old_role)


def test_postgres_role_fence_rejects_incomplete_session_visibility(postgres_runtime):
    config = postgres_runtime(tenant_id=TENANT)
    old_role = "old_" + config["role_name"].removeprefix("app_")
    with psycopg.connect(config["migration_dsn"], autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(old_role)))
    try:
        with (
            StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as store,
            pytest.raises(IntegrityError, match="SESSION_VISIBILITY_REQUIRED"),
        ):
            maintenance.PostgresLegacyWriterFence(store, (old_role,)).verify()
    finally:
        _admin_drop(config, old_role)


def test_postgres_revoked_membership_does_not_hide_old_set_role_session(postgres_runtime):
    config = postgres_runtime(tenant_id=TENANT)
    suffix = config["role_name"].removeprefix("app_")
    old_role, member_role = "old_" + suffix, "member_" + suffix
    _admin_setup(config, old_role)
    with psycopg.connect(config["migration_dsn"], autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(member_role)))
        admin.execute(sql.SQL("GRANT {} TO {}").format(
            sql.Identifier(old_role), sql.Identifier(member_role),
        ))
    member = psycopg.connect(_role_dsn(config["migration_dsn"], member_role), autocommit=True)
    try:
        member.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(old_role)))
        with psycopg.connect(config["migration_dsn"], autocommit=True) as admin:
            admin.execute(sql.SQL("REVOKE {} FROM {}").format(
                sql.Identifier(old_role), sql.Identifier(member_role),
            ))
            admin.execute(sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(old_role)))
        identity = member.execute("SELECT current_user, session_user").fetchone()
        assert tuple(identity) == (old_role, member_role)
        with StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as store:
            fence = maintenance.PostgresLegacyWriterFence(store, (old_role,))
            with pytest.raises(IntegrityError, match="OTHER_CLIENT_BACKEND_ACTIVE"):
                fence.verify()
            member.close()
            fence.verify()
    finally:
        member.close()
        with psycopg.connect(config["migration_dsn"], autocommit=True) as admin:
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(member_role)))
        _admin_drop(config, old_role)


def test_authenticated_migration_and_exact_readback_on_real_postgres(
    postgres_runtime, monkeypatch, tmp_path,
):
    config = postgres_runtime(tenant_id=TENANT)
    old_role = "old_" + config["role_name"].removeprefix("app_")
    _admin_setup(config, old_role)
    _admin_disable(config, old_role)
    try:
        with StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as store:
            service = _service(store)
            source, package, _, _ = _seed_one_legal_v1_head(service)
            _patch_runtime(monkeypatch, store)
            path = tmp_path / "maintenance.json"
            _write_config(path, _config(
                service, old_role=old_role, action="MIGRATE",
                expected_source_ref=source.ref,
                expected_source_digest=source.digest,
                expected_package_digest=package.package_digest,
            ))
            result = maintenance.run_pattern_head_maintenance(path)
            assert result["action"] == "MIGRATE"
            assert result["transition_kind"] == "MIGRATE"
            assert result["generation"] == 1
            assert result["adoption_enabled"] is False
            assert result["receipt_ref"] == result["head_ref"]
            head = service._current_stable_head(TARGET_SKILL)
            assert head.ref == result["head_ref"]
            assert head.payload["maintenance_window_digest"] == result["maintenance_window_digest"]
            with pytest.raises(IntegrityError, match="HEAD_ALREADY_EXISTS"):
                maintenance.run_pattern_head_maintenance(path)

            original_digest = maintenance.load_pattern_head_maintenance_config(path).digest
            inspect_path = tmp_path / "inspect.json"
            _write_config(inspect_path, _config(
                service, old_role=old_role, action="INSPECT",
                expected_generation=1,
                expected_package_digest=package.package_digest,
                expected_previous_ref=source.ref,
                expected_previous_digest=source.digest,
                expected_transition_kind="MIGRATE",
                original_mutation_config_path=str(path),
                expected_mutation_config_digest=original_digest,
            ))
            readback = maintenance.run_pattern_head_maintenance(inspect_path)
            assert readback["head_ref"] == result["head_ref"]
            assert readback["receipt_ref"] == result["head_ref"]
            assert readback["maintenance_window_digest"] == result["maintenance_window_digest"]
            mismatched = _config(
                service, old_role=old_role, action="INSPECT",
                expected_generation=1,
                expected_package_digest=package.package_digest,
                expected_previous_ref=source.ref,
                expected_previous_digest="sha256:" + "0" * 64,
                expected_transition_kind="MIGRATE",
                original_mutation_config_path=str(path),
                expected_mutation_config_digest=original_digest,
            )
            _write_config(inspect_path, mismatched)
            with pytest.raises(IntegrityError, match="ORIGINAL_COMMAND_MISMATCH"):
                maintenance.run_pattern_head_maintenance(inspect_path)
            assert store.verify_event_chain()["status"] == "PASS"
    finally:
        _admin_drop(config, old_role)


def test_authenticated_direct_parent_rollback_keeps_adoption_off(
    postgres_runtime, monkeypatch, tmp_path,
):
    config = postgres_runtime(tenant_id=TENANT)
    old_role = "old_" + config["role_name"].removeprefix("app_")
    _admin_setup(config, old_role)
    _admin_disable(config, old_role)
    try:
        with StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as store:
            service = _service(store)
            candidate, evaluation, _, _, _ = qualify(service)
            decide(service, candidate, evaluation)
            head = service._current_stable_head(TARGET_SKILL)
            assert head is not None
            service.retract(
                candidate, actor_id=service.governance_authority,
                reason="regression in controlled candidate",
            )
            _patch_runtime(monkeypatch, store)
            path = tmp_path / "rollback.json"
            _write_config(path, _config(
                service, old_role=old_role, action="ROLLBACK",
                expected_head_ref=head.ref,
                expected_head_digest=head.digest,
                expected_generation=head.payload["generation"],
                expected_package_digest=head.payload["package_digest"],
                reason="rollback one direct parent",
            ))
            result = maintenance.run_pattern_head_maintenance(path)
            assert result["action"] == "ROLLBACK"
            assert result["transition_kind"] == "ROLLBACK"
            assert result["generation"] == 2
            assert result["adoption_enabled"] is False
            assert result["package_digest"] == service.registry.load(TARGET_SKILL).package_digest
            receipt = service._load(result["receipt_ref"], "restoration")
            assert receipt["maintenance_window_digest"] == result["maintenance_window_digest"]
            assert receipt["principal_binding"]["actor_id"] == GOVERNOR
            with pytest.raises(IntegrityError, match="STALE_HEAD"):
                maintenance.run_pattern_head_maintenance(path)
            original_digest = maintenance.load_pattern_head_maintenance_config(path).digest
            inspect_path = tmp_path / "inspect.json"
            _write_config(inspect_path, _config(
                service, old_role=old_role, action="INSPECT",
                expected_generation=2,
                expected_package_digest=result["package_digest"],
                expected_previous_ref=head.ref,
                expected_previous_digest=head.digest,
                expected_transition_kind="ROLLBACK",
                original_mutation_config_path=str(path),
                expected_mutation_config_digest=original_digest,
            ))
            readback = maintenance.run_pattern_head_maintenance(inspect_path)
            assert readback["head_ref"] == result["head_ref"]
            assert readback["receipt_ref"] == result["receipt_ref"]
            assert readback["maintenance_window_digest"] == result["maintenance_window_digest"]
            competing_path = tmp_path / "competing-rollback.json"
            _write_config(competing_path, _config(
                service, old_role=old_role, action="ROLLBACK",
                maintenance_window_id="maintenance:competing-window",
                expected_head_ref=head.ref,
                expected_head_digest=head.digest,
                expected_generation=head.payload["generation"],
                expected_package_digest=head.payload["package_digest"],
                reason="rollback one direct parent",
            ))
            competing_digest = maintenance.load_pattern_head_maintenance_config(
                competing_path
            ).digest
            _write_config(inspect_path, _config(
                service, old_role=old_role, action="INSPECT",
                maintenance_window_id="maintenance:competing-window",
                expected_generation=2,
                expected_package_digest=result["package_digest"],
                expected_previous_ref=head.ref,
                expected_previous_digest=head.digest,
                expected_transition_kind="ROLLBACK",
                original_mutation_config_path=str(competing_path),
                expected_mutation_config_digest=competing_digest,
            ))
            with pytest.raises(IntegrityError, match="INSPECT_MUTATION_MISMATCH"):
                maintenance.run_pattern_head_maintenance(inspect_path)
            assert store.verify_event_chain()["status"] == "PASS"
    finally:
        _admin_drop(config, old_role)


def test_inspect_cannot_claim_an_ordinary_s05_rollback_as_maintenance(
    postgres_runtime, monkeypatch, tmp_path,
):
    config = postgres_runtime(tenant_id=TENANT)
    old_role = "old_" + config["role_name"].removeprefix("app_")
    _admin_setup(config, old_role)
    _admin_disable(config, old_role)
    try:
        with StateStore(config["runtime_dsn"], tenant_id=TENANT, migrate=False) as store:
            service = _service(store)
            candidate, evaluation, _, _, _ = qualify(service)
            decide(service, candidate, evaluation)
            head = service._current_stable_head(TARGET_SKILL)
            assert head is not None
            service.retract(candidate, actor_id=GOVERNOR, reason="controlled regression")
            installed = service.registry.load(TARGET_SKILL).package_digest
            service.restore_predecessor(
                candidate, actor_id=GOVERNOR,
                expected_predecessor_digest=installed,
                reason="restore exact installed predecessor",
            )
            actual = service._current_stable_head(TARGET_SKILL)
            assert actual is not None and actual.payload["transition_kind"] == "ROLLBACK"
            _patch_runtime(monkeypatch, store)
            original_path = tmp_path / "not-issued-maintenance.json"
            _write_config(original_path, _config(
                service, old_role=old_role, action="ROLLBACK",
                expected_head_ref=head.ref,
                expected_head_digest=head.digest,
                expected_generation=head.payload["generation"],
                expected_package_digest=head.payload["package_digest"],
                reason="restore exact installed predecessor",
            ))
            inspect_path = tmp_path / "inspect.json"
            _write_config(inspect_path, _config(
                service, old_role=old_role, action="INSPECT",
                expected_generation=actual.payload["generation"],
                expected_package_digest=installed,
                expected_previous_ref=head.ref,
                expected_previous_digest=head.digest,
                expected_transition_kind="ROLLBACK",
                original_mutation_config_path=str(original_path),
                expected_mutation_config_digest=maintenance.load_pattern_head_maintenance_config(
                    original_path
                ).digest,
            ))
            with pytest.raises(IntegrityError, match="INSPECT_MUTATION_MISMATCH"):
                maintenance.run_pattern_head_maintenance(inspect_path)
    finally:
        _admin_drop(config, old_role)
