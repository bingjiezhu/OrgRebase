"""Finance guidance head mechanics; no model-quality or adoption claims."""

from __future__ import annotations

import time
from contextlib import contextmanager

import pytest

from orgrebase.auth import Principal, request_principal
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.skill_evolution_v2 import (
    INSTRUCTION_PATH,
    REFERENCE_PATH,
    FinanceSkillHeadService,
    ReviewedCapabilityPolicyV2,
    SkillContentBundleV2,
)
from tests.postgres_support import postgres_runtime as postgres_runtime


@contextmanager
def governor(tenant: str = "tenant:finance"):
    token = request_principal.set(
        Principal(
            issuer="https://issuer.example.test",
            subject="reviewer",
            tenant_id=tenant,
            actor_id="reviewer:finance",
            roles=frozenset({"governor"}),
            expires_at=int(time.time()) + 60,
        )
    )
    try:
        yield
    finally:
        request_principal.reset(token)


def test_static_bundle_is_exact_two_resource_unqualified_genesis(tmp_path):
    path = tmp_path / "finance.sqlite3"
    with StateStore(path, tenant_id="tenant:finance") as store:
        service = FinanceSkillHeadService(store)
        with governor():
            created = service.bootstrap()
        assert created.generation == 0
        assert created.qualification_status == "UNQUALIFIED"
        assert created.adoption_enabled is False
        assert created.previous_head_ref is None
        assert created.effective_version_parent_ref is None
        assert set(created.bundle.resource_digests) == {INSTRUCTION_PATH, REFERENCE_PATH}
        assert created.bundle.instruction_text.startswith("# Finance change explanation")
        assert created.bundle.reference_text.startswith("# Reviewed explanation boundary")
        assert store.get_pointer(service.head_id)["version"] == "g00000000"
    with StateStore(path, tenant_id="tenant:finance") as store:
        reopened = FinanceSkillHeadService(store).resolve()
        assert reopened.head_ref == created.head_ref
        assert reopened.head_digest == created.head_digest
        assert reopened.bundle.payload == created.bundle.payload


def test_bootstrap_requires_current_governor_and_cannot_replace_existing_head(tmp_path):
    with StateStore(tmp_path / "finance.sqlite3", tenant_id="tenant:finance") as store:
        service = FinanceSkillHeadService(store)
        with pytest.raises(Exception, match="FINANCE_SKILL_VERIFIED_PRINCIPAL_REQUIRED"):
            service.bootstrap()
        with governor("tenant:other"), pytest.raises(Exception, match="AUTH_TENANT_DENIED"):
            service.bootstrap()
        with governor():
            initial = service.bootstrap()
            with pytest.raises(IntegrityError, match="CURRENT_POINTER_ALREADY_EXISTS"):
                service.bootstrap()
        assert service.resolve().head_ref == initial.head_ref
        assert store.get_pointer(service.head_id)["revision"] == 1


def test_local_store_requires_explicit_verified_tenant_without_scope_spoofing(tmp_path):
    with StateStore(tmp_path / "local.sqlite3") as local:
        with pytest.raises(ValueError, match="TENANT_SCOPE_INVALID"):
            FinanceSkillHeadService(local)
        service = FinanceSkillHeadService(local, tenant_id="tenant:finance")
        with governor():
            created = service.bootstrap()
        assert service.resolve().head_ref == created.head_ref
        assert service.scope_digest
    with (
        StateStore(tmp_path / "bound.sqlite3", tenant_id="tenant:finance") as bound,
        pytest.raises(ValueError, match="TENANT_SCOPE_INVALID"),
    ):
        FinanceSkillHeadService(bound, tenant_id="tenant:other")


def test_server_compiles_one_instruction_leaf_against_exact_head(tmp_path):
    with StateStore(tmp_path / "finance.sqlite3", tenant_id="tenant:finance") as store:
        service = FinanceSkillHeadService(store)
        with governor():
            current = service.bootstrap()
        candidate = service.prepare_instruction_patch(
            current.bundle.instruction_text + "Check source coverage before explaining.\n",
            expected_head_ref=current.head_ref,
            expected_head_digest=current.head_digest,
            expected_generation=current.generation,
            expected_package_digest=current.package_digest,
        )
        assert candidate.package_digest != current.package_digest
        assert candidate.reference_bytes == current.bundle.reference_bytes
        assert candidate.resource_digests[REFERENCE_PATH] == current.bundle.resource_digests[REFERENCE_PATH]
        assert candidate.payload["parent_head_ref"] == current.head_ref
        assert candidate.payload["parent_package_digest"] == current.package_digest
        with pytest.raises(IntegrityError, match="FINANCE_SKILL_STALE_BASE"):
            service.prepare_instruction_patch(
                candidate.instruction_text + "Another line.\n",
                expected_head_ref=current.head_ref,
                expected_head_digest=current.head_digest,
                expected_generation=current.generation + 1,
                expected_package_digest=current.package_digest,
            )


def test_bundle_detects_changed_resource_and_policy_tampering():
    policy = ReviewedCapabilityPolicyV2()
    bundle = SkillContentBundleV2.static_baseline(policy)
    edited = {**bundle.payload, "resources": [dict(row) for row in bundle.payload["resources"]]}
    edited["resources"][0]["size_bytes"] += 1
    with pytest.raises(IntegrityError, match="FINANCE_SKILL_BUNDLE_DIGEST_MISMATCH"):
        SkillContentBundleV2.from_payload(edited, policy=policy)
    changed_policy = ReviewedCapabilityPolicyV2(revision="new-policy")
    with pytest.raises(IntegrityError, match="FINANCE_SKILL_POLICY_DRIFT"):
        SkillContentBundleV2.from_payload(bundle.payload, policy=changed_policy)


def test_postgres_static_head_is_unique_and_restart_resolves_exact_bytes(postgres_runtime):
    config = postgres_runtime(tenant_id="tenant:finance")
    with (
        StateStore(config["runtime_dsn"], tenant_id="tenant:finance", migrate=False) as first,
        StateStore(config["runtime_dsn"], tenant_id="tenant:finance", migrate=False) as second,
    ):
        first_service, second_service = FinanceSkillHeadService(first), FinanceSkillHeadService(second)
        with governor():
            initial = first_service.bootstrap()
            with pytest.raises(IntegrityError, match="CURRENT_POINTER_ALREADY_EXISTS"):
                second_service.bootstrap()
        observed = second_service.resolve()
        assert observed.head_ref == initial.head_ref
        assert observed.bundle.payload == initial.bundle.payload
        assert second.get_pointer(second_service.head_id)["revision"] == 1
