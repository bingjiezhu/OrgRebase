from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import (
    AuthenticationError,
    Principal,
    request_authorization,
    request_principal,
)
from orgrebase.change_events import ChangeEvent
from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    AuthorizationError,
    IntegrityError,
    ObjectState,
    VersionedObject,
)
from orgrebase.local_role_session import LocalRoleSessionSettings
from orgrebase.runtime_config import DeploymentSettings
from orgrebase.store import StateStore
from orgrebase.workspace import quote_pattern_bridge, quote_recovery_operations
from orgrebase.workspace.change_proposals import change_detail
from orgrebase.workspace.change_recovery import (
    REQUEST_MEDIA,
    RESUME_MEDIA,
    _prefix,
    _sealed,
    resume_change,
    return_for_evidence,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.quote_recovery_learning import (
    TARGET_SKILL,
    quote_recovery_content_bundle,
)
from orgrebase.workspace.quote_recovery_operations import (
    POLICY_ENVIRONMENT_VARIABLE,
    QuoteRecoveryNewRun,
    QuoteRecoveryOperationError,
    execute_quote_recovery_new_run,
    read_quote_recovery_operation,
)
from orgrebase.workspace.routes import change_router
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.skill_packages import SkillReleaseLedger
from tests.workspace.test_change_agentteams import configure
from tests.workspace.test_change_recovery import resume_command, return_command
from tests.workspace.test_continuous_changes import apply, proposal
from tests.workspace.test_enterprise_pilot_delivery import _make_distinct_enterprise_pack
from tests.workspace.test_onboarding_draft_recovery import ORIGIN, choose, csrf
from tests.workspace.test_quote_recovery_adoption_matrix import _verified_release
from tests.workspace.test_quote_recovery_governed_learning import (
    AUTHOR,
    CORPUS,
    EVALUATOR,
    GOVERNOR,
    RUNTIME,
    TENANT,
    WORKSPACE,
    _identity,
)


class _Changes:
    def __init__(self, event: ChangeEvent) -> None:
        self.event = event

    def get(self, event_id: str) -> ChangeEvent:
        if event_id != self.event.event_id:
            raise KeyError(event_id)
        return self.event

    def pending_ids(self) -> tuple[str, ...]:
        return ()


def _recovery_workspace(
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
    *,
    persist_receipts: bool = True,
):
    proposal = VersionedObject(
        id="claim:product.enterprise_plan",
        version="v2",
        kind="ClaimVersion",
        label="Product plan",
        domain="product",
        state=ObjectState.PROPOSED,
        payload={"canonical_value": "revised"},
        source_refs=("source:owner-reviewed@v2",),
        allowed_purposes=("change_rebase",),
    )
    event = ChangeEvent(
        event_id="recovery-operation-case",
        organization_id=TENANT,
        slot_id="product_plan",
        owner_id="owner:product",
        base_version="v1",
        base_digest=sha256_digest({"base": 1}),
        proposal=proposal,
        occurred_at="2026-09-25T00:00:00Z",
    )
    workspace = SimpleNamespace(
        store=store,
        profile=SimpleNamespace(organization_id=TENANT),
        approval_identity_mode="VERIFIED_PRINCIPAL_IDENTITY",
        changes=_Changes(event),
        change_order=(event.event_id,),
        effective_workflow_run_id="run:original-recovery",
    )
    request = _sealed(
        {
            "event_id": event.event_id,
            "event_digest": event.digest,
            "round": 1,
            "previous_request_digest": None,
            "workspace_id": store.workspace_id,
            "run_id": workspace.effective_workflow_run_id,
            "required_evidence_refs": ["claim:product.enterprise_plan@v1"],
        }
    )
    resume = _sealed(
        {
            "request_digest": request["digest"],
            "event_digest": event.digest,
            "executor": {"executor_id": "product-steward"},
            "evidence": [],
        }
    )
    if persist_receipts:
        with store.transaction() as connection:
            store.save_artifact(
                connection,
                _prefix(event.event_id) + "request:0001",
                REQUEST_MEDIA,
                request,
            )
            store.save_artifact(
                connection,
                _prefix(event.event_id) + "resume:0001",
                RESUME_MEDIA,
                resume,
            )
    detail = {
        "status": "APPLIED",
        "preview": {"preview_digest": sha256_digest({"preview": 1})},
        "approval": {"approval_digest": sha256_digest({"approval": 1})},
        "outcome": {"artifact_digest": sha256_digest({"outcome": 1})},
    }
    monkeypatch.setattr(
        quote_pattern_bridge,
        "change_detail",
        lambda _workspace, _event_id: detail,
    )
    return workspace, event, detail


def _policy(
    path: Path,
    *,
    candidate: str,
    service,
    enabled: bool = True,
    runtime_actor_id: str = RUNTIME,
) -> Path:
    bundle = quote_recovery_content_bundle(service.registry)
    value = {
        "schema_version": "orgrebase.quote-recovery-learning-policy.v1",
        "enabled": enabled,
        "policy_revision": "reviewed-v1",
        "organization_id": TENANT,
        "workspace_id": WORKSPACE,
        "candidate_ref": candidate,
        "corpus_actor_id": CORPUS,
        "author_actor_id": AUTHOR,
        "evaluator_actor_id": EVALUATOR,
        "governor_actor_id": GOVERNOR,
        "runtime_actor_ids": [runtime_actor_id],
        "required_knowledge_refs": ["knowledge:quote-recovery-v1"],
        "required_qualification_refs": ["qualification:quote-recovery-review"],
        "reviewed_bundle_digest": bundle.digest,
        "reviewed_target_skill": TARGET_SKILL,
        "reviewed_predecessor_package_digest": service.registry.load(
            TARGET_SKILL
        ).package_digest,
        "adoption_not_after_epoch": 4_000_000_000,
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_standard_new_run_entry_consumes_canonical_recovery_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with StateStore(
        tmp_path / "operations.sqlite",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    ) as store:
        service, _, candidate, _, checks, _ = _verified_release(store)
        workspace, event, _ = _recovery_workspace(store, monkeypatch)
        policy = _policy(tmp_path / "policy.json", candidate=candidate, service=service)
        monkeypatch.setenv(POLICY_ENVIRONMENT_VARIABLE, str(policy))
        command = QuoteRecoveryNewRun(
            operation_key="normal-product-entry-001",
            event_id=event.event_id,
            candidate_ref=candidate,
            new_run_id="run:normal-product-recovery-001",
        )
        with _identity(RUNTIME, frozenset({"reader"}), checks):
            first = execute_quote_recovery_new_run(workspace, command)
            replay = execute_quote_recovery_new_run(workspace, command)
        assert first == replay
        assert first["status"] == "CONSUMED"
        assert first["result_state"] == "SUCCEEDED"
        assert first["action"] == "HANDOFF"
        assert first["run_id"] == command.new_run_id
        assert first["model_invocations"] == first["canonical_target_writes"] == 0
        assert first["old_quote_modified"] is False
        assert len(service._family("invocation-result")) == 1
        conflict = command.model_copy(
            update={"new_run_id": "run:normal-product-recovery-conflict"}
        )
        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"),
        ):
            execute_quote_recovery_new_run(workspace, conflict)
        assert len(service._family("invocation-result")) == 1


def test_standard_entry_is_default_off_expiring_and_abstains_without_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with StateStore(
        tmp_path / "policy-boundary.sqlite",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    ) as store:
        service, _, candidate, _, checks, _ = _verified_release(store)
        workspace, event, detail = _recovery_workspace(store, monkeypatch)
        command = QuoteRecoveryNewRun(
            operation_key="policy-boundary-001",
            event_id=event.event_id,
            candidate_ref=candidate,
            new_run_id="run:policy-boundary-001",
        )
        disabled = _policy(
            tmp_path / "disabled.json",
            candidate=candidate,
            service=service,
            enabled=False,
        )
        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(QuoteRecoveryOperationError, match="LEARNING_DISABLED"),
        ):
            execute_quote_recovery_new_run(workspace, command, config_path=disabled)

        enabled = _policy(
            tmp_path / "enabled.json",
            candidate=candidate,
            service=service,
        )
        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(QuoteRecoveryOperationError, match="POLICY_EXPIRED"),
        ):
            execute_quote_recovery_new_run(
                workspace,
                command,
                config_path=enabled,
                now=lambda: 4_000_000_001,
            )

        detail["status"] = "REJECTED"
        detail["outcome"] = None
        with _identity(RUNTIME, frozenset({"reader"}), checks):
            abstained = execute_quote_recovery_new_run(
                workspace,
                command,
                config_path=enabled,
            )
        assert abstained["case_outcome"] == "UNKNOWN"
        assert abstained["action"] == "ABSTAIN"
        assert abstained["result_state"] == "SUCCEEDED"
        assert len(service._family("invocation-result")) == 1

        _policy(
            enabled,
            candidate=candidate,
            service=service,
            enabled=False,
        )
        with _identity(RUNTIME, frozenset({"reader"}), checks):
            assert read_quote_recovery_operation(
                workspace,
                command.operation_key,
            ) == abstained
            with pytest.raises(
                QuoteRecoveryOperationError,
                match="LEARNING_DISABLED",
            ):
                execute_quote_recovery_new_run(
                    workspace,
                    command,
                    config_path=enabled,
                )


def test_unknown_run_cannot_be_rebound_to_changed_case_under_another_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with StateStore(
        tmp_path / "unknown-binding.sqlite",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    ) as store:
        service, _, candidate, _, checks, _ = _verified_release(store)
        workspace, event, detail = _recovery_workspace(store, monkeypatch)
        detail["status"] = "REJECTED"
        detail["outcome"] = None
        policy = _policy(
            tmp_path / "unknown-policy.json",
            candidate=candidate,
            service=service,
        )
        command = QuoteRecoveryNewRun(
            operation_key="unknown-input-binding",
            event_id=event.event_id,
            candidate_ref=candidate,
            new_run_id="run:unknown-input-binding",
        )

        def fail_after_reservation(*_args, **_kwargs):
            raise RuntimeError("Bearer " + "secret-must-never-persist")

        with monkeypatch.context() as failure:
            failure.setattr(SkillReleaseLedger, "invoke", fail_after_reservation)
            with (
                _identity(RUNTIME, frozenset({"reader"}), checks),
                pytest.raises(
                    IntegrityError,
                    match="PATTERN_INVOCATION_RESULT_UNKNOWN",
                ),
            ):
                execute_quote_recovery_new_run(
                    workspace,
                    command,
                    config_path=policy,
                )

        with _identity(RUNTIME, frozenset({"reader"}), checks):
            unknown = execute_quote_recovery_new_run(
                workspace,
                command,
                config_path=policy,
            )
        assert unknown["case_outcome"] == "UNKNOWN"
        assert unknown["result_state"] == "RESULT_UNKNOWN"
        assert unknown["reason_code"] == "PATTERN_INVOCATION_RESULT_UNKNOWN"

        detail["status"] = "APPLIED"
        detail["outcome"] = {
            "artifact_digest": sha256_digest({"outcome": "later-apply"})
        }
        changed = command.model_copy(update={"operation_key": "changed-case-same-run"})
        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(
                IntegrityError,
                match="QUOTE_RECOVERY_RUN_INPUT_BINDING_MISMATCH",
            ),
        ):
            execute_quote_recovery_new_run(
                workspace,
                changed,
                config_path=policy,
            )
        with _identity(RUNTIME, frozenset({"reader"}), checks):
            assert execute_quote_recovery_new_run(
                workspace,
                command,
                config_path=policy,
            ) == unknown
        intents = store.list_artifacts(
            artifact_id_prefix="quote-recovery-operation-intent:",
        )
        assert len(intents) == 1
        assert "secret-must-never-persist" not in json.dumps(
            [
                artifact.payload
                for prefix in (
                    "pattern-evolution:",
                    "quote-recovery-operation-intent:",
                )
                for artifact in store.list_artifacts(artifact_id_prefix=prefix)
            ]
        )


def test_standard_entry_rejects_wrong_runtime_actor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with StateStore(
        tmp_path / "actor-boundary.sqlite",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    ) as store:
        service, _, candidate, _, checks, _ = _verified_release(store)
        workspace, event, _ = _recovery_workspace(store, monkeypatch)
        policy = _policy(tmp_path / "policy.json", candidate=candidate, service=service)
        command = QuoteRecoveryNewRun(
            operation_key="actor-boundary-001",
            event_id=event.event_id,
            candidate_ref=candidate,
            new_run_id="run:actor-boundary-001",
        )
        with (
            _identity("runtime:not-allowed", frozenset({"reader"}), checks),
            pytest.raises(AuthorizationError, match="RUNTIME_ACTOR_DENIED"),
        ):
            execute_quote_recovery_new_run(workspace, command, config_path=policy)
        assert service._family("invocation-reservation") == ()


def test_http_standard_entry_reaches_real_bridge_governance_and_consumer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with StateStore(
        tmp_path / "http-entry.sqlite",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    ) as store:
        service, _, candidate, _, _, _ = _verified_release(store)
        workspace, event, _ = _recovery_workspace(store, monkeypatch)
        policy = _policy(tmp_path / "policy.json", candidate=candidate, service=service)
        monkeypatch.setenv(POLICY_ENVIRONMENT_VARIABLE, str(policy))
        app = FastAPI()
        app.include_router(change_router(lambda: workspace))

        @app.middleware("http")
        async def current_identity(request: Request, call_next):
            principal = Principal(
                "https://issuer.example",
                f"subject:{RUNTIME}",
                TENANT,
                RUNTIME,
                frozenset({"operator"}),
                4_000_000_000,
            )
            principal_token = request_principal.set(principal)
            authorization_token = request_authorization.set(lambda: None)
            try:
                return await call_next(request)
            finally:
                request_authorization.reset(authorization_token)
                request_principal.reset(principal_token)

        with TestClient(app) as client:
            response = client.post(
                "/api/workspace/governed-learning/quote-recovery-runs",
                json={
                    "operation_key": "http-normal-entry",
                    "event_id": event.event_id,
                    "candidate_ref": candidate,
                    "new_run_id": "run:http-normal-entry",
                },
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "CONSUMED"
        assert body["run_id"] == "run:http-normal-entry"
        assert body["action"] == "HANDOFF"
        assert body["invocation_receipt_digest"]
        assert len(service._family("invocation-result")) == 1


def test_policy_change_after_dispatch_rejects_result_before_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with StateStore(
        tmp_path / "policy-change.sqlite",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    ) as store:
        service, _, candidate, _, checks, _ = _verified_release(store)
        workspace, event, _ = _recovery_workspace(store, monkeypatch)
        policy_path = _policy(
            tmp_path / "policy.json",
            candidate=candidate,
            service=service,
        )
        original_load = quote_recovery_operations.load_quote_recovery_learning_policy
        calls = 0
        consumer_completed = False
        original_invoke = SkillReleaseLedger.invoke

        def observe_consumer(*args, **kwargs):
            nonlocal consumer_completed
            result = original_invoke(*args, **kwargs)
            consumer_completed = True
            return result

        def changed_after_dispatch(path: Path):
            nonlocal calls
            calls += 1
            value = original_load(path)
            if consumer_completed:
                return value.model_copy(update={"enabled": False})
            return value

        monkeypatch.setattr(
            quote_recovery_operations,
            "load_quote_recovery_learning_policy",
            changed_after_dispatch,
        )
        monkeypatch.setattr(SkillReleaseLedger, "invoke", observe_consumer)
        command = QuoteRecoveryNewRun(
            operation_key="policy-changed-after-dispatch",
            event_id=event.event_id,
            candidate_ref=candidate,
            new_run_id="run:policy-changed-after-dispatch",
        )
        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(AuthenticationError, match="POLICY_CHANGED"),
        ):
            execute_quote_recovery_new_run(
                workspace,
                command,
                config_path=policy_path,
            )
        assert calls >= 4
        assert consumer_completed is True
        assert service._family("invocation-result") == ()
        assert len(service._family("invocation-rejection")) == 1


def test_real_workspace_recovery_apply_then_standard_new_run_consumes_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = _make_distinct_enterprise_pack(
        tmp_path / "pack",
        slug="quote-recovery",
        customer="recovery-customer",
        product_plan="Recovery Enterprise Plan",
        proposed_date="2027-02-15",
    )
    runtime = load_enterprise_quote_pilot_pack(pack)
    assert runtime.profile.organization_id == TENANT
    workspace = WorkspaceService(
        store_path=tmp_path / "real-workspace.sqlite",
        store_tenant_id=TENANT,
        runtime_configuration=runtime,
    )
    try:
        workspace.form_quote()
        for event_id in ("currency", "launch_date"):
            apply(workspace, event_id)
        configure(workspace, tmp_path / "native")
        event = proposal(
            workspace,
            "real-recovery-operation",
            "product_plan",
            "Recovery Enterprise Plan v2",
        )
        workspace.register_change(event)
        workspace.preview_change(event.event_id)
        returned = return_for_evidence(
            workspace,
            event.event_id,
            return_command(workspace, event.event_id),
        )
        resumed = resume_change(
            workspace,
            event.event_id,
            resume_command(workspace, event.event_id),
        )
        assert returned["state"] == "NEEDS_EVIDENCE"
        assert resumed["state"] == "READY_FOR_REVIEW"
        service, _, candidate, _, _, _ = _verified_release(workspace.store)
        settings = DeploymentSettings(
            mode="local",
            local_role_session=LocalRoleSessionSettings(ORIGIN),
        )
        original_identity_mode = workspace.approval_identity_mode
        app = create_app(
            workspace_service=workspace,
            deployment_settings=settings,
        )
        with TestClient(
            app,
            base_url=ORIGIN,
            client=("127.0.0.1", 42112),
        ) as client:
            anonymous = client.get("/api/session").json()
            runtime_actor = next(
                actor["actor_id"]
                for actor in anonymous["actors"]
                if "operator" in actor["roles"]
            )
            policy = _policy(
                tmp_path / "real-policy.json",
                candidate=candidate,
                service=service,
                runtime_actor_id=runtime_actor,
            )
            monkeypatch.setenv(POLICY_ENVIRONMENT_VARIABLE, str(policy))
            session = choose(client, runtime_actor)
            incomplete = client.post(
                "/api/workspace/governed-learning/quote-recovery-runs",
                json={
                    "operation_key": "real-workspace-incomplete",
                    "event_id": event.event_id,
                    "candidate_ref": candidate,
                    "new_run_id": "run:real-workspace-incomplete",
                },
                headers=csrf(session),
            )
            assert incomplete.status_code == 200, incomplete.text
            incomplete_body = incomplete.json()
            assert incomplete_body["case_outcome"] == "UNKNOWN"
            assert incomplete_body["action"] == "ABSTAIN"

            workspace.approval_identity_mode = original_identity_mode
            preview = workspace._preview_record(event.event_id)
            approval = workspace.approve_change(
                event.event_id,
                actor_id=event.owner_id,
                preview_digest=preview["preview_digest"],
                recovery_digest=resumed["recovery_digest"],
            )
            workspace.apply_approved_change(
                event.event_id,
                approval_digest=approval["approval_digest"],
            )
            assert change_detail(workspace, event.event_id)["status"] == "APPLIED"
            response = client.post(
                "/api/workspace/governed-learning/quote-recovery-runs",
                json={
                    "operation_key": "real-workspace-next-run",
                    "event_id": event.event_id,
                    "candidate_ref": candidate,
                    "new_run_id": "run:real-workspace-next-run",
                },
                headers=csrf(session),
            )
            replayed_incomplete = client.post(
                "/api/workspace/governed-learning/quote-recovery-runs",
                json={
                    "operation_key": "real-workspace-incomplete",
                    "event_id": event.event_id,
                    "candidate_ref": candidate,
                    "new_run_id": "run:real-workspace-incomplete",
                },
                headers=csrf(session),
            )
            assert replayed_incomplete.status_code == 200, replayed_incomplete.text
            assert replayed_incomplete.json() == incomplete_body

            _policy(
                policy,
                candidate=candidate,
                service=service,
                enabled=False,
                runtime_actor_id=runtime_actor,
            )
            historical = client.get(
                "/api/workspace/governed-learning/quote-recovery-operations/"
                "real-workspace-incomplete",
            )
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["case_outcome"] == "SUPPORT"
        assert receipt["action"] == "HANDOFF"
        assert receipt["status"] == "CONSUMED"
        assert receipt["invocation_receipt_digest"]
        assert historical.status_code == 200, historical.text
        assert historical.json() == incomplete_body
        assert workspace.current_quote().payload["product_plan"] == (
            "Recovery Enterprise Plan v2"
        )
        assert len(service._family("invocation-result")) == 2
    finally:
        workspace.close()
