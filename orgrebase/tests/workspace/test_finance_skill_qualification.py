"""Controlled qualification mechanics; these synthetic receipts are not model proof."""

from __future__ import annotations

import time
import urllib.request
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from orgrebase.auth import Principal, request_principal
from orgrebase.clock import FrozenClock
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.private_records import PrivateRecordStore
from orgrebase.store import StateStore
from orgrebase.workspace import finance_skill_qualification as qualification_module
from orgrebase.workspace.advisory import (
    DomainAdvisoryCandidate,
    WorkspaceChangeAdvisoryAdapter,
    finance_case_revision,
    finance_evaluation_advice_context,
)
from orgrebase.workspace.experience_contracts import (
    empty_memory_snapshot,
    empty_recall_selection_manifest,
    exact_bytes_digest,
)
from orgrebase.workspace.finance_experiment import (
    REVIEWED_FINANCE_RUBRIC_DIGEST,
    FinanceExperimentService,
    FinanceIndependentOracle,
)
from orgrebase.workspace.finance_explanation_operations import (
    evaluate_static_finance,
    finance_evaluation_refs,
)
from orgrebase.workspace.finance_skill_candidates import FinanceSkillCandidateService
from orgrebase.workspace.finance_skill_qualification import (
    CONTENT_RELEASE_MEDIA,
    EVALUATION_INTENT_MEDIA,
    EVALUATION_RESULT_MEDIA,
    QUALIFICATION_MEDIA,
    SUITE_MEDIA,
    FinancePairJudgment,
    FinanceSkillQualificationService,
    verify_current_release_for_adoption,
)
from orgrebase.workspace.models import ModelRequestV4, ModelResponseReceiptV4
from orgrebase.workspace.skill_evolution_v2 import CONTENT_MEDIA, FinanceSkillHeadService
from orgrebase.workspace.vertex_candidate import build_vertex_advice_body
from tests.workspace.test_change_advisory import NOW, contracts
from tests.workspace.test_change_proposals import workspace as workspace
from tests.workspace.test_finance_advice_v4 import _current_validator, _provider
from tests.workspace.test_finance_explanation_operations import _as as _workspace_as
from tests.workspace.test_finance_explanation_operations import _fake_send, _prepare

TENANT = "tenant:finance-qualification"


@contextmanager
def _as(actor, role="governor"):
    token = request_principal.set(Principal(
        issuer="https://issuer.example.test", subject=actor,
        tenant_id=TENANT, actor_id=actor, roles=frozenset({role}),
        expires_at=int(time.time()) + 120,
    ))
    try:
        yield
    finally:
        request_principal.reset(token)


def _head(store):
    head = FinanceSkillHeadService(store)
    with _as("actor:governor"):
        head.bootstrap()
    return head


def _qualification(store, head):
    workspace = SimpleNamespace(
        store=store, profile=SimpleNamespace(organization_id=TENANT),
        clock=FrozenClock("2026-09-28T00:00:00Z"), private_retention_seconds=86_400,
    )
    return FinanceSkillQualificationService(
        head, workspace=workspace, evaluator_actor_id="actor:evaluator",
    )


def _gold(store, service, *, suite_id, expected_source_refs_digest=None):
    refs = {}
    with store.transaction() as connection:
        for number in (1, 2, 3):
            case_ref = f"case:{number}"
            ref = f"finance-sealed-gold:{suite_id}:{number}"
            PrivateRecordStore(
                store, service.workspace.clock,
                retention_seconds=service.workspace.private_retention_seconds,
            ).write(
                connection, record_id=ref, scope_ref="finance-sealed-suite:" + suite_id,
                owner_id="actor:evaluator",
                payload={
                    "schema_version": "orgrebase.finance-sealed-gold-case.v1",
                    "case_ref": case_ref,
                    "case_revision_digest": sha256_digest({"case": number}),
                    "independence_cluster_id": f"cluster:{number}",
                    "expected_source_refs_digest": expected_source_refs_digest or sha256_digest([]),
                    "rubric_digest": REVIEWED_FINANCE_RUBRIC_DIGEST,
                },
            )
            refs[case_ref] = ref
    return refs


def _freeze_suite(service, store, *, suite_id="suite:sealed", **overrides):
    parent = service.head.resolve()
    gold_refs = _gold(store, service, suite_id=suite_id)
    kwargs = {
        "suite_id": suite_id,
        "parent": parent,
        "case_clusters": {f"case:{i}": f"cluster:{i}" for i in (1, 2, 3)},
        "case_gold_refs": gold_refs,
        "rubric_digest": REVIEWED_FINANCE_RUBRIC_DIGEST,
        "experiment_family_id": "family:sealed",
        "max_physical_attempts": 10,
        "max_reserved_microusd": 100,
        "min_gain": 1,
    }
    kwargs.update(overrides)
    return service.freeze_suite(**kwargs)


def _save_synthetic_pairs(store, *, parent, candidate, suite_ref, case_numbers=(1, 2, 3)):
    suite_digest = sha256_digest(store.load_artifact(
        suite_ref, "application/vnd.orgrebase.finance-sealed-suite.v1+json"
    ).payload)
    pairs = []
    for number in case_numbers:
        event_id = f"case:{number}"
        case_revision = sha256_digest({"case": number})
        refs = []
        for arm, bundle in (("BASELINE", parent.bundle), ("CANDIDATE", candidate)):
            suffix = f"{number}:{arm}:{bundle.digest[7:10]}"
            intent_ref = "finance-evaluation-intent:" + suffix
            result_ref = "finance-evaluation-result:" + suffix
            intent = {
                "schema_version": "orgrebase.finance-evaluation-intent.v1",
                "scope": "SEALED_HOLDOUT_PAIR",
                "event_id": event_id,
                "operation_id": "operation:" + suffix,
                "experiment_family_id": "family:synthetic-mechanism",
                "evaluation_arm": arm,
                "execution_mode": "EVALUATION_ONLY",
                "sealed_suite_ref": suite_ref,
                "sealed_suite_digest": suite_digest,
                "head_ref": parent.head_ref,
                "head_digest": parent.head_digest,
                "head_generation": parent.generation,
                "case_revision_digest": case_revision,
                "budget_contract_digest": sha256_digest("budget"),
                "reserved_microusd": 1,
                "snapshot_digest": sha256_digest("fixed-snapshot"),
                "manifest_digest": sha256_digest({"manifest": number}),
                "candidate_bundle_digest": bundle.digest,
                "package_digest": bundle.package_digest,
                "target_writes": 0,
            }
            result = {
                "schema_version": "orgrebase.finance-evaluation-result.v1",
                "scope": "SEALED_HOLDOUT_PAIR",
                "intent_ref": intent_ref,
                "intent_digest": sha256_digest(intent),
                "event_id": event_id,
                "operation_id": intent["operation_id"],
                "experiment_family_id": intent["experiment_family_id"],
                "execution_mode": "EVALUATION_ONLY",
                "status": "PROTOCOL_VALID",
                "candidate_bundle_digest": bundle.digest,
                "package_digest": bundle.package_digest,
                "observed_model_id": "gemini-3.8-flash",
                "physical_attempts_observed": 1,
                "attempt_observation_coverage": "COMPLETE",
                "finance_request_digest": sha256_digest({"request": suffix}),
                "finance_receipt_digest": sha256_digest({"receipt": suffix}),
                "finance_wire_body_digest": sha256_digest({"wire": suffix}),
                "finance_provider_request_id": "synthetic:" + suffix,
                "private_record_ref": "private:" + suffix,
                "private_record_digest": sha256_digest({"private": suffix}),
                "quality_status": "NOT_EVALUATED",
                "target_writes": 0,
            }
            with store.transaction() as connection:
                store.save_artifact(connection, intent_ref, EVALUATION_INTENT_MEDIA, intent)
                store.save_artifact(connection, result_ref, EVALUATION_RESULT_MEDIA, result)
            refs.append(result_ref)
        pairs.append(FinancePairJudgment(
            case_ref=event_id, case_revision_digest=case_revision,
            independence_cluster_id=f"cluster:{number}",
            baseline_result_ref=refs[0], candidate_result_ref=refs[1],
            grounded_score_before=1, grounded_score_after=2,
            next_step_score_before=1, next_step_score_after=2,
            hard_safety_passed=True, reason_code="SYNTHETIC_FIXTURE_ONLY",
        ))
    return tuple(pairs)


def _save_mechanism_pairs(service, *, inputs, parent, candidate, suite_ref):
    """Fake transport exercises real V4/private binding; quality and real terminal stay stubbed."""
    store = service.store
    suite_digest = sha256_digest(store.load_artifact(suite_ref, SUITE_MEDIA).payload)
    snapshot = empty_memory_snapshot(
        tenant_id=TENANT, workspace_id=store.workspace_id,
        profile_id="workspace-change-explanation-v1",
        retrieval_version="sparse-v1", compiler_version="finance-guidance-compiler.v1",
        budget_version="finance-budget.v1",
    )
    manifest = empty_recall_selection_manifest(
        snapshot, case_id=inputs["change_set"].id,
        case_revision=finance_case_revision(inputs["change_set"], inputs["preview"]),
        evaluation_arm="NO_MEMORY", query_ref="query:qualification-mechanism",
        query_digest=exact_bytes_digest(b"admitted finance change"),
    )
    pairs = []
    for number in (1, 2, 3):
        case_ref = f"case:{number}"
        result_refs = []
        for arm, bundle in (("BASELINE", parent.bundle), ("CANDIDATE", candidate)):
            suffix = f"mechanism:{number}:{arm.lower()}"
            intent_ref = "finance-evaluation-intent:" + suffix
            result_ref = "finance-evaluation-result:" + suffix
            private_ref = "finance-evaluation-private:" + suffix
            advice = finance_evaluation_advice_context(
                resolution=parent, snapshot=snapshot, manifest=manifest,
                change_set=inputs["change_set"], preview=inputs["preview"],
                run_envelope=inputs["run_envelope"], operation_id="operation:" + suffix,
                snapshot_ref="memory-snapshot:" + snapshot.digest[7:],
                manifest_ref="recall-manifest:" + manifest.digest[7:],
                candidate_bundle=None if arm == "BASELINE" else candidate,
            )
            adapter = WorkspaceChangeAdvisoryAdapter(
                provider=_provider(), tenant_id=TENANT, workspace_id=store.workspace_id,
                model_id="gemini-3.8-flash", finance_advice=advice,
                validate_finance_advice=_current_validator(store, snapshot, manifest),
            )
            collaboration = adapter.run(**inputs, now=NOW, evaluation_only=True)
            finance = next(
                item.payload["model_advisory"] for item in collaboration["handoffs"]
                if item.payload.get("model_advisory", {}).get("request", {}).get("contract_version") == "4"
            )
            request = ModelRequestV4.model_validate(finance["request"]).revalidated()
            receipt = ModelResponseReceiptV4.model_validate(finance["receipt"]).revalidated()
            wire = build_vertex_advice_body(request, DomainAdvisoryCandidate)
            assert receipt.status == "VALID"
            assert sha256_digest(sorted(
                item.ref for item in request.business_input_projections
            )) == service._read_current_gold(
                store.load_artifact(suite_ref, SUITE_MEDIA).payload, case_ref,
            )["expected_source_refs_digest"]
            intent = {
                "schema_version": "orgrebase.finance-evaluation-intent.v1",
                "scope": "SEALED_HOLDOUT_PAIR", "event_id": case_ref,
                "operation_id": "operation:" + suffix,
                "experiment_family_id": "family:mechanism-only",
                "evaluation_arm": arm, "execution_mode": "EVALUATION_ONLY",
                "sealed_suite_ref": suite_ref, "sealed_suite_digest": suite_digest,
                "head_ref": parent.head_ref, "head_digest": parent.head_digest,
                "head_generation": parent.generation,
                "case_revision_digest": sha256_digest({"case": number}),
                "budget_contract_digest": sha256_digest("test-only-budget"),
                "reserved_microusd": 1,
                "snapshot_digest": snapshot.digest, "manifest_digest": manifest.digest,
                "candidate_bundle_digest": bundle.digest,
                "package_digest": bundle.package_digest,
                "actor_id": "actor:mechanism-operator", "target_writes": 0,
            }
            private = {
                "schema_version": "orgrebase.finance-evaluation-private.v1",
                "intent_ref": intent_ref, "intent_digest": sha256_digest(intent),
                "collaboration": {"handoffs": [{"payload": {"model_advisory": finance}}]},
                "finance_wire_body": wire,
            }
            result = {
                "schema_version": "orgrebase.finance-evaluation-result.v1",
                "scope": "SEALED_HOLDOUT_PAIR", "intent_ref": intent_ref,
                "intent_digest": sha256_digest(intent), "event_id": case_ref,
                "operation_id": intent["operation_id"],
                "experiment_family_id": intent["experiment_family_id"],
                "execution_mode": "EVALUATION_ONLY", "status": "PROTOCOL_VALID",
                "candidate_bundle_digest": bundle.digest,
                "package_digest": bundle.package_digest,
                "observed_model_id": "gemini-3.8-flash",
                "physical_attempts_observed": 1,
                "attempt_observation_coverage": "COMPLETE",
                "finance_request_digest": request.digest,
                "finance_receipt_digest": receipt.digest,
                "finance_wire_body_digest": sha256_digest(wire),
                "finance_provider_request_id": receipt.provider_request_id,
                "private_record_ref": private_ref,
                "private_record_digest": sha256_digest(private),
                "quality_status": "NOT_EVALUATED", "target_writes": 0,
            }
            with store.transaction() as connection:
                store.save_artifact(connection, intent_ref, EVALUATION_INTENT_MEDIA, intent)
                service._private_store().write(
                    connection, record_id=private_ref, scope_ref=intent_ref,
                    owner_id="actor:mechanism-operator", payload=private,
                )
                store.save_artifact(connection, result_ref, EVALUATION_RESULT_MEDIA, result)
                store.append_event(connection, "FINANCE_EVALUATION_INTENT_RESERVED", {
                    "intent_ref": intent_ref,
                })
            result_refs.append(result_ref)
        pairs.append(FinancePairJudgment(
            case_ref=case_ref, case_revision_digest=sha256_digest({"case": number}),
            independence_cluster_id=f"cluster:{number}",
            baseline_result_ref=result_refs[0], candidate_result_ref=result_refs[1],
            grounded_score_before=1, grounded_score_after=2,
            next_step_score_before=1, next_step_score_after=2,
            hard_safety_passed=True, reason_code="TEST_ONLY_MECHANISM",
            baseline_assessment_ref=f"test-only-assessment:{number}:baseline",
            candidate_assessment_ref=f"test-only-assessment:{number}:candidate",
        ))
    return tuple(pairs)


def test_static_single_arm_cannot_issue_finance_qualification(tmp_path):
    with StateStore(tmp_path / "static.sqlite3", tenant_id=TENANT) as store:
        head = _head(store)
        service = _qualification(store, head)
        parent = head.resolve()
        with _as("actor:evaluator"):
            gold_refs = _gold(store, service, suite_id="suite:one")
            suite_ref = service.freeze_suite(
                suite_id="suite:one", parent=parent,
                case_clusters={f"case:{i}": f"cluster:{i}" for i in (1, 2, 3)},
                case_gold_refs=gold_refs,
                rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
                experiment_family_id="family:static-only",
                max_physical_attempts=10, max_reserved_microusd=100,
                min_gain=1,
            )
        # The current 010 static operation has no sealed-suite binding, a
        # candidate arm, or the SEALED_HOLDOUT_PAIR scope.
        intent_ref = "finance-evaluation-intent:static-only"
        result_ref = "finance-evaluation-result:static-only"
        intent = {
            "schema_version": "orgrebase.finance-evaluation-intent.v1",
            "scope": "STATIC_BASELINE_ONLY", "event_id": "case:1",
            "operation_id": "operation:static", "evaluation_arm": "HUMAN_REVIEWED_STATIC_NO_MEMORY",
            "candidate_bundle_digest": parent.bundle.digest,
        }
        with store.transaction() as connection:
            store.save_artifact(connection, intent_ref, EVALUATION_INTENT_MEDIA, intent)
            store.save_artifact(connection, result_ref, EVALUATION_RESULT_MEDIA, {
                "schema_version": "orgrebase.finance-evaluation-result.v1",
                "intent_ref": intent_ref, "intent_digest": sha256_digest(intent),
                "scope": "STATIC_BASELINE_ONLY", "status": "PROTOCOL_VALID",
            })
        with pytest.raises(IntegrityError, match="RESULT_INVALID"):
            service._read_pair_result(
                result_ref, expected_bundle=parent.bundle, parent=parent,
                suite_ref=suite_ref,
                suite_digest=sha256_digest(store.load_artifact(
                    suite_ref, "application/vnd.orgrebase.finance-sealed-suite.v1+json"
                ).payload),
            )
        assert head.resolve().generation == 0


def test_synthetic_paired_metadata_without_current_private_v4_cannot_qualify(tmp_path):
    with StateStore(tmp_path / "mechanism.sqlite3", tenant_id=TENANT) as store:
        head = _head(store)
        service = _qualification(store, head)
        parent = head.resolve()
        candidate = head.prepare_instruction_patch(
            parent.bundle.instruction_text + "Check current source coverage.\n",
            expected_head_ref=parent.head_ref,
            expected_head_digest=parent.head_digest,
            expected_generation=parent.generation,
            expected_package_digest=parent.package_digest,
        )
        with _as("actor:author", "operator"):
            proposal_ref = FinanceSkillCandidateService(
                service.workspace, head,
            ).propose_instruction(
                operation_id="candidate:mechanism",
                instruction_text=candidate.instruction_text,
            )
        with _as("actor:evaluator"):
            gold_refs = _gold(store, service, suite_id="suite:mechanism")
            suite_ref = service.freeze_suite(
                suite_id="suite:mechanism", parent=parent,
                case_clusters={f"case:{i}": f"cluster:{i}" for i in (1, 2, 3)},
                case_gold_refs=gold_refs,
                rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
                experiment_family_id="family:synthetic-mechanism",
                max_physical_attempts=10, max_reserved_microusd=100,
                min_gain=1,
            )
        pairs = _save_synthetic_pairs(store, parent=parent, candidate=candidate, suite_ref=suite_ref)
        with _as("actor:evaluator"), pytest.raises(
            IntegrityError, match="PRIVATE_EVIDENCE_UNAVAILABLE"
        ):
            service.issue_qualification(
                suite_ref=suite_ref, candidate=candidate, pairs=pairs,
                author_actor_id="actor:author", candidate_proposal_ref=proposal_ref,
            )
        assert head.resolve().generation == 0
        assert head.resolve().qualification_status == "UNQUALIFIED"
        assert head.resolve().adoption_enabled is False
        assert store.list_artifacts(
            artifact_id_prefix="finance-skill-qualification:",
            expected_media_type="application/vnd.orgrebase.finance-skill-qualification.v1+json",
        ) == ()
        assert store.verify_event_chain()["status"] == "PASS"


def test_private_verifier_rebuilds_actual_v4_static_protocol_without_qualifying(
    workspace, monkeypatch,
):
    event_id, head = _prepare(workspace)
    sent = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, workspace))
    with _workspace_as(workspace, "actor:finance-evaluator", "operator"):
        result_view = evaluate_static_finance(
            workspace, event_id=event_id, operation_id="finance-static-protocol",
            provider_override=_provider(),
        )
    assert result_view["status"] == "PROTOCOL_VALID"
    refs = finance_evaluation_refs(workspace, event_id)
    intent = workspace.store.load_artifact(refs["intent_ref"], EVALUATION_INTENT_MEDIA).payload
    result = workspace.store.load_artifact(refs["result_ref"], EVALUATION_RESULT_MEDIA).payload
    private = PrivateRecordStore(
        workspace.store, workspace.clock,
        retention_seconds=workspace.private_retention_seconds,
    ).read_owned(refs["private_ref"], owner_id=intent["actor_id"], scope_ref=refs["intent_ref"])
    assert private is not None
    finance = next(
        row["payload"]["model_advisory"] for row in private["collaboration"]["handoffs"]
        if row.get("payload", {}).get("model_advisory", {}).get("request", {}).get("contract_version") == "4"
    )
    gold = {
        "expected_source_refs_digest": sha256_digest(sorted(
            item["ref"] for item in finance["request"]["business_input_projections"]
        )),
    }
    service = FinanceSkillQualificationService(
        FinanceSkillHeadService(
            workspace.store, tenant_id=workspace.profile.organization_id,
        ),
        workspace=workspace, evaluator_actor_id="actor:independent-evaluator",
    )
    assert service._verify_current_private_result(intent, result, gold) is None
    with pytest.raises(IntegrityError, match="FINANCE_QUALIFICATION_V4_BINDING_INVALID"):
        service._verify_current_private_result(
            intent, {**result, "finance_receipt_digest": sha256_digest("forged-receipt")}, gold,
        )
    with pytest.raises(IntegrityError, match="FINANCE_QUALIFICATION_V4_BINDING_INVALID"):
        service._verify_current_private_result(
            intent, result,
            {"expected_source_refs_digest": sha256_digest(["forged-enterprise-source"])},
        )
    with pytest.raises(IntegrityError, match="FINANCE_QUALIFICATION_PRIVATE_EVIDENCE_UNAVAILABLE"):
        service._verify_current_private_result(
            {**intent, "actor_id": "actor:foreign-private-owner"}, result, gold,
        )
    for suffix, changed_private, reason in (
        ("missing-collaboration", {**private, "collaboration": None},
         "FINANCE_QUALIFICATION_PRIVATE_EVIDENCE_INVALID"),
        ("invalid-handoffs", {**private, "collaboration": {"handoffs": "not-a-list"}},
         "FINANCE_QUALIFICATION_PRIVATE_EVIDENCE_INVALID"),
        ("no-finance-v4", {**private, "collaboration": {"handoffs": []}},
         "FINANCE_QUALIFICATION_V4_RESULT_REQUIRED"),
    ):
        private_ref = "finance-evaluation-private:forged-" + suffix
        with workspace.store.transaction() as connection:
            PrivateRecordStore(
                workspace.store, workspace.clock,
                retention_seconds=workspace.private_retention_seconds,
            ).write(
                connection, record_id=private_ref, scope_ref=refs["intent_ref"],
                owner_id=intent["actor_id"], payload=changed_private,
            )
        with pytest.raises(IntegrityError, match=reason):
            service._verify_current_private_result(
                intent,
                {
                    **result, "private_record_ref": private_ref,
                    "private_record_digest": sha256_digest(changed_private),
                },
                gold,
            )
    assert head.generation == 0
    assert head.adoption_enabled is False


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"rubric_digest": sha256_digest("unreviewed-rubric")}, "FINANCE_SEALED_SUITE_INVALID"),
        ({"case_clusters": {f"case:{i}": "cluster:shared" for i in (1, 2, 3)}},
         "FINANCE_SEALED_SUITE_INVALID"),
        ({"max_physical_attempts": 0}, "FINANCE_SEALED_SUITE_INVALID"),
        ({"max_reserved_microusd": 0}, "FINANCE_SEALED_SUITE_INVALID"),
        ({"min_gain": 0}, "FINANCE_SEALED_SUITE_INVALID"),
        ({"max_regressions": 1}, "FINANCE_SEALED_SUITE_INVALID"),
        ({"case_clusters": {
            "case:1": "cluster:wrong", "case:2": "cluster:2", "case:3": "cluster:3",
        }}, "FINANCE_SEALED_GOLD_INVALID"),
    ],
)
def test_sealed_suite_rejects_unreviewed_rubric_ambiguous_cases_and_unbounded_budget(
    tmp_path, overrides, reason,
):
    with StateStore(tmp_path / "sealed-validation.sqlite", tenant_id=TENANT) as store:
        head = _head(store)
        service = _qualification(store, head)
        with _as("actor:evaluator"), pytest.raises(IntegrityError, match=reason):
            _freeze_suite(service, store, **overrides)
        assert store.list_artifacts(
            artifact_id_prefix="finance-sealed-suite:", expected_media_type=SUITE_MEDIA,
        ) == ()
        assert head.resolve().qualification_status == "UNQUALIFIED"


def test_sealed_suite_is_idempotent_but_cannot_reuse_cases_or_erased_gold(tmp_path):
    with StateStore(tmp_path / "sealed-reuse.sqlite", tenant_id=TENANT) as store:
        head = _head(store)
        service = _qualification(store, head)
        with _as("actor:evaluator"):
            suite_ref = _freeze_suite(service, store)
            suite = store.load_artifact(suite_ref, SUITE_MEDIA).payload
            assert service.freeze_suite(
                suite_id=suite["suite_id"], parent=head.resolve(),
                case_clusters=suite["case_clusters"],
                case_gold_refs={
                    case: item["record_ref"] for case, item in suite["case_gold"].items()
                },
                rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
                experiment_family_id=suite["experiment_family_id"],
                max_physical_attempts=suite["max_physical_attempts"],
                max_reserved_microusd=suite["max_reserved_microusd"],
                min_gain=suite["min_gain"],
            ) == suite_ref
            with pytest.raises(IntegrityError, match="FINANCE_SEALED_CASE_ALREADY_USED"):
                _freeze_suite(service, store, suite_id="suite:second")
        assert len(store.list_artifacts(
            artifact_id_prefix="finance-sealed-suite:", expected_media_type=SUITE_MEDIA,
        )) == 1
        gold_ref = suite["case_gold"]["case:1"]["record_ref"]
        assert service._private_store().erase(gold_ref, actor_id="actor:evaluator")
        with pytest.raises(IntegrityError, match="FINANCE_SEALED_GOLD_UNAVAILABLE"):
            service._read_current_gold(suite, "case:1")
        assert head.resolve().generation == 0


@pytest.mark.parametrize("failure", ["rubric_drift", "gold_expired", "gold_wrong_scope"])
def test_frozen_gold_is_rechecked_on_recovery(tmp_path, monkeypatch, failure):
    with StateStore(tmp_path / "gold-recovery.sqlite", tenant_id=TENANT) as store:
        head = _head(store)
        service = _qualification(store, head)
        with _as("actor:evaluator"):
            suite_ref = _freeze_suite(service, store)
        suite = store.load_artifact(suite_ref, SUITE_MEDIA).payload
        if failure == "rubric_drift":
            monkeypatch.setattr(
                qualification_module, "REVIEWED_FINANCE_RUBRIC_DIGEST",
                sha256_digest("changed-reviewed-rubric"),
            )
        elif failure == "gold_expired":
            service.workspace.clock = FrozenClock("2026-09-30T00:00:00Z")
        else:
            suite = {**suite, "suite_id": "suite:other-private-scope"}
        with pytest.raises(IntegrityError, match="FINANCE_SEALED_GOLD_UNAVAILABLE"):
            service._read_current_gold(suite, "case:1")
        assert head.resolve().qualification_status == "UNQUALIFIED"


def test_suite_freeze_rejects_missing_private_gold(tmp_path):
    with StateStore(tmp_path / "gold-freeze.sqlite", tenant_id=TENANT) as store:
        head = _head(store)
        service = _qualification(store, head)
        with _as("actor:evaluator"):
            refs = _gold(store, service, suite_id="suite:missing-gold")
            refs["case:1"] = "finance-sealed-gold:missing"
            with pytest.raises(IntegrityError, match="FINANCE_SEALED_GOLD_INVALID"):
                service.freeze_suite(
                    suite_id="suite:missing-gold", parent=head.resolve(),
                    case_clusters={f"case:{i}": f"cluster:{i}" for i in (1, 2, 3)},
                    case_gold_refs=refs,
                    rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
                    experiment_family_id="family:missing-gold",
                    max_physical_attempts=6, max_reserved_microusd=6, min_gain=1,
                )
        assert store.list_artifacts(
            artifact_id_prefix="finance-sealed-suite:", expected_media_type=SUITE_MEDIA,
        ) == ()


def test_erased_candidate_private_source_cannot_issue_qualification(tmp_path):
    with StateStore(tmp_path / "candidate-erased.sqlite", tenant_id=TENANT) as store:
        head = _head(store)
        service = _qualification(store, head)
        parent = head.resolve()
        instruction = parent.bundle.instruction_text + "Check current source coverage.\n"
        candidate = head.prepare_instruction_patch(
            instruction,
            expected_head_ref=parent.head_ref,
            expected_head_digest=parent.head_digest,
            expected_generation=parent.generation,
            expected_package_digest=parent.package_digest,
        )
        with _as("actor:author", "operator"):
            proposal_ref = FinanceSkillCandidateService(
                service.workspace, head,
            ).propose_instruction(
                operation_id="candidate:erased-before-review",
                instruction_text=instruction,
            )
        private_ref = "finance-skill-candidate-private:" + proposal_ref.rsplit(":", 1)[1]
        assert service._private_store().erase(private_ref, actor_id="actor:author")
        with _as("actor:evaluator"), pytest.raises(
            IntegrityError, match="FINANCE_CANDIDATE_PRIVATE_CONTENT_UNAVAILABLE",
        ):
            service.issue_qualification(
                suite_ref="finance-sealed-suite:unreachable", candidate=candidate,
                pairs=(), author_actor_id="actor:author",
                candidate_proposal_ref=proposal_ref,
            )
        assert head.resolve() == parent
        assert store.list_artifacts(
            artifact_id_prefix="finance-skill-qualification:",
            expected_media_type=QUALIFICATION_MEDIA,
        ) == ()


def test_sealed_suite_must_precede_candidate_and_paired_intents(tmp_path):
    with StateStore(tmp_path / "sealed-order.sqlite", tenant_id=TENANT) as store:
        head = _head(store)
        service = _qualification(store, head)
        parent = head.resolve()
        instruction = parent.bundle.instruction_text + "Check current source coverage.\n"
        candidate = head.prepare_instruction_patch(
            instruction,
            expected_head_ref=parent.head_ref,
            expected_head_digest=parent.head_digest,
            expected_generation=parent.generation,
            expected_package_digest=parent.package_digest,
        )
        with _as("actor:author", "operator"):
            proposal_ref = FinanceSkillCandidateService(
                service.workspace, head,
            ).propose_instruction(
                operation_id="candidate:before-suite", instruction_text=instruction,
            )
        with _as("actor:evaluator"):
            proposal = FinanceSkillCandidateService(
                service.workspace, head,
            ).verify_for_qualification(proposal_ref, candidate=candidate)
            suite_ref = _freeze_suite(service, store)
        intent_refs = {"finance-evaluation-intent:baseline", "finance-evaluation-intent:candidate"}
        with store.transaction() as connection:
            for intent_ref in sorted(intent_refs):
                store.append_event(connection, "FINANCE_EVALUATION_INTENT_RESERVED", {
                    "intent_ref": intent_ref,
                })
        with pytest.raises(IntegrityError, match="FINANCE_QUALIFICATION_SUITE_NOT_PREDECLARED"):
            service._require_suite_precedes_intents(
                suite_ref, intent_refs,
                candidate_proposal_sequence=proposal["candidate_proposal_sequence"],
            )
        assert head.resolve().qualification_status == "UNQUALIFIED"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("observed_model_id", "other-model"),
        ("physical_attempts_observed", 0),
        ("attempt_observation_coverage", "UNKNOWN"),
        ("quality_status", "QUALIFIED"),
        ("target_writes", 1),
        ("private_record_ref", ""),
        ("intent_ref", None),
    ],
)
def test_pair_result_rejects_forged_model_budget_quality_or_business_write(
    tmp_path, field, value,
):
    with StateStore(tmp_path / "forged-result.sqlite", tenant_id=TENANT) as store:
        head = _head(store)
        service = _qualification(store, head)
        parent = head.resolve()
        with _as("actor:evaluator"):
            suite_ref = _freeze_suite(service, store)
        suite_digest = sha256_digest(store.load_artifact(suite_ref, SUITE_MEDIA).payload)
        candidate = head.prepare_instruction_patch(
            parent.bundle.instruction_text + "Check current source coverage.\n",
            expected_head_ref=parent.head_ref,
            expected_head_digest=parent.head_digest,
            expected_generation=parent.generation,
            expected_package_digest=parent.package_digest,
        )
        pair = _save_synthetic_pairs(
            store, parent=parent, candidate=candidate, suite_ref=suite_ref,
            case_numbers=(1,),
        )[0]
        result = store.load_artifact(pair.baseline_result_ref, EVALUATION_RESULT_MEDIA).payload
        forged = {**result, field: value}
        forged_ref = f"finance-evaluation-result:forged:{field}"
        with store.transaction() as connection:
            store.save_artifact(connection, forged_ref, EVALUATION_RESULT_MEDIA, forged)
        with pytest.raises(IntegrityError, match="FINANCE_QUALIFICATION_RESULT_INVALID"):
            service._read_pair_result(
                forged_ref, expected_bundle=parent.bundle, parent=parent,
                suite_ref=suite_ref, suite_digest=suite_digest,
            )
        assert head.resolve().generation == 0


@pytest.mark.parametrize(
    ("drift", "reason"),
    [
        ("none", "FINANCE_ADOPTION_SEALED_FAMILY_VERIFICATION_NOT_READY"),
        ("head", "FINANCE_ADOPTION_CURRENT_RELEASE_INVALID"),
        ("policy", "FINANCE_ADOPTION_CURRENT_RELEASE_INVALID"),
        ("qualification", "FINANCE_ADOPTION_RELEASE_EVIDENCE_INVALID"),
        ("release", "FINANCE_ADOPTION_RELEASE_EVIDENCE_INVALID"),
        ("missing_qualification", "FINANCE_ADOPTION_RELEASE_EVIDENCE_MISSING"),
        ("missing_release", "FINANCE_ADOPTION_RELEASE_EVIDENCE_MISSING"),
    ],
)
def test_apparent_qualified_head_still_cannot_adopt_without_real_family_terminal(
    tmp_path, drift, reason,
):
    """A mechanically self-consistent release fixture is not sealed-family proof."""
    with StateStore(tmp_path / "unverified-family.sqlite", tenant_id=TENANT) as store:
        head = _head(store)
        parent = head.resolve()
        candidate = head.prepare_instruction_patch(
            parent.bundle.instruction_text + "Review current Finance evidence.\n",
            expected_head_ref=parent.head_ref,
            expected_head_digest=parent.head_digest,
            expected_generation=parent.generation,
            expected_package_digest=parent.package_digest,
        )
        qualification_ref = "finance-skill-qualification:mechanical-fixture"
        qualification = {
            "schema_version": "orgrebase.finance-skill-qualification.v1",
            "status": "QUALIFIED",
            "rubric_digest": REVIEWED_FINANCE_RUBRIC_DIGEST,
            "candidate_bundle_digest": candidate.digest,
            "candidate_package_digest": candidate.package_digest,
        }
        if drift == "qualification":
            qualification["rubric_digest"] = sha256_digest("unreviewed-rubric")
        release_ref = "finance-content-release:mechanical-fixture"
        release = {
            "schema_version": "orgrebase.finance-content-release.v1",
            "qualification_ref": qualification_ref,
            "qualification_digest": sha256_digest(qualification),
            "candidate_bundle_digest": candidate.digest,
            "candidate_package_digest": candidate.package_digest,
            "adoption_enabled": False,
        }
        if drift == "release":
            release["adoption_enabled"] = True
        previous = head._current_source()
        promoted = head._head_object(
            generation=1, transition_kind="PROMOTE", bundle=candidate,
            previous=previous, actor_id="actor:governor",
            qualification_status="QUALIFIED", qualification_ref=qualification_ref,
            content_release_ref=release_ref,
        )
        with store.transaction() as connection:
            store.save_artifact(connection, head._bundle_ref(candidate), CONTENT_MEDIA, candidate.payload)
            if drift != "missing_qualification":
                store.save_artifact(connection, qualification_ref, QUALIFICATION_MEDIA, qualification)
            if drift != "missing_release":
                store.save_artifact(connection, release_ref, CONTENT_RELEASE_MEDIA, release)
            store.insert_version(connection, promoted, make_current=False)
            store.promote_version(connection, head.head_id, previous.version, promoted.version)
        current = head.resolve()
        workspace = SimpleNamespace(
            store=store, profile=SimpleNamespace(organization_id=TENANT),
        )
        with pytest.raises(IntegrityError, match=reason):
            verify_current_release_for_adoption(
                workspace,
                expected_head_ref=current.head_ref,
                expected_head_digest=(
                    sha256_digest("old-head") if drift == "head" else current.head_digest
                ),
                expected_package_digest=(
                    sha256_digest("old-policy") if drift == "policy" else current.package_digest
                ),
                qualification_ref=qualification_ref,
                qualification_digest=sha256_digest(qualification),
                content_release_ref=release_ref,
            )
        assert current.adoption_enabled is False


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_only_mechanism_issue_review_promote_preserves_exact_author_and_head_cas(
    fixture, tmp_path, monkeypatch, postgres_runtime, backend,
):
    """Exercise publication mechanics, never real quality or adoption eligibility.

    The fake transport returns V4 wire receipts; only the unavailable sealed
    REAL_VERTEX terminal and independent semantic assessor are test stubs.
    This fixture must never be used as a qualification or improvement claim.
    """
    database = (
        tmp_path / "mechanism-publication.sqlite" if backend == "sqlite"
        else postgres_runtime(tenant_id=TENANT)["runtime_dsn"]
    )
    with StateStore(database, tenant_id=TENANT, migrate=backend == "sqlite") as store:
        head = _head(store)
        parent = head.resolve()
        base_service = _qualification(store, head)
        base_service.workspace.clock = FrozenClock(NOW)
        service = FinanceSkillQualificationService(
            head, workspace=base_service.workspace,
            evaluator_actor_id="actor:evaluator",
            assessor_actor_id="actor:independent-assessor",
            optimizer_actor_id="actor:optimizer",
            experiment_operator_actor_id="actor:mechanism-operator",
        )
        inputs = contracts(fixture, (("rule:finance", "finance"),))
        source_digest = sha256_digest(sorted(
            item.ref for item in inputs["fixture"].objects if item.domain == "finance"
        ))
        with _as("actor:evaluator"):
            gold_refs = _gold(
                store, service, suite_id="suite:mechanism-publication",
                expected_source_refs_digest=source_digest,
            )
            suite_ref = service.freeze_suite(
                suite_id="suite:mechanism-publication", parent=parent,
                case_clusters={f"case:{i}": f"cluster:{i}" for i in (1, 2, 3)},
                case_gold_refs=gold_refs, rubric_digest=REVIEWED_FINANCE_RUBRIC_DIGEST,
                experiment_family_id="family:mechanism-only",
                max_physical_attempts=6, max_reserved_microusd=6, min_gain=1,
            )
        instruction = parent.bundle.instruction_text + "Check current Finance source coverage.\n"
        candidate = head.prepare_instruction_patch(
            instruction,
            expected_head_ref=parent.head_ref,
            expected_head_digest=parent.head_digest,
            expected_generation=parent.generation,
            expected_package_digest=parent.package_digest,
        )
        with _as("actor:author", "operator"):
            proposal_ref = FinanceSkillCandidateService(
                service.workspace, head,
            ).propose_instruction(
                operation_id="candidate:mechanism-publication",
                instruction_text=instruction,
            )
        sent = []
        monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, service.workspace))
        pairs = _save_mechanism_pairs(
            service, inputs=inputs, parent=parent, candidate=candidate, suite_ref=suite_ref,
        )
        assert len(sent) >= 6

        # These two external independent checks cannot currently issue a real
        # terminal/quality certificate. Replacing only them isolates CAS and
        # exact-content publication mechanics; the deployment hook stays closed.
        def test_only_sealed_terminal(_self, **kwargs):
            assert kwargs["suite_ref"] == suite_ref
            assert kwargs["case_ref"] in {pair.case_ref for pair in pairs}
            return {"test_only_mechanism": True}

        def test_only_semantic_assessment(_self, assessment_ref, **kwargs):
            assert assessment_ref.startswith("test-only-assessment:")
            assert kwargs["expected_rubric_digest"] == REVIEWED_FINANCE_RUBRIC_DIGEST
            return {"test_only_mechanism": True, "assessment_ref": assessment_ref}

        monkeypatch.setattr(
            FinanceExperimentService, "verify_sealed_pair_lineage", test_only_sealed_terminal,
        )
        monkeypatch.setattr(
            FinanceExperimentService, "verify_sealed_pair_lineage_for_release",
            test_only_sealed_terminal,
        )
        monkeypatch.setattr(
            FinanceIndependentOracle, "verify_assessment_receipt", test_only_semantic_assessment,
        )
        monkeypatch.setattr(
            FinanceIndependentOracle, "verify_assessment_for_release",
            test_only_semantic_assessment,
        )
        with monkeypatch.context() as patch:
            patch.setattr(
                qualification_module, "REVIEWED_FINANCE_RUBRIC_DIGEST",
                sha256_digest("rubric-replaced-after-suite-freeze"),
            )
            with _as("actor:evaluator"), pytest.raises(
                IntegrityError, match="FINANCE_QUALIFICATION_SUITE_INVALID",
            ):
                service.issue_qualification(
                    suite_ref=suite_ref, candidate=candidate, pairs=pairs,
                    author_actor_id="actor:author", candidate_proposal_ref=proposal_ref,
                )
        # These mutations must fail before the synthetic lineage/assessor
        # stubs can turn a malformed pair into a publication receipt.
        with _as("actor:evaluator"):
            with pytest.raises(
                IntegrityError, match="FINANCE_CANDIDATE_AUTHOR_PROOF_REQUIRED",
            ):
                service.issue_qualification(
                    suite_ref=suite_ref, candidate=candidate, pairs=pairs,
                    author_actor_id="actor:author", candidate_proposal_ref=None,
                )
            with pytest.raises(
                AuthorizationError, match="FINANCE_QUALIFICATION_ROLE_COLLISION",
            ):
                service.issue_qualification(
                    suite_ref=suite_ref, candidate=candidate, pairs=pairs,
                    author_actor_id="actor:evaluator", candidate_proposal_ref=proposal_ref,
                )
            for changed, reason in (
                (replace(pairs[0], hard_safety_passed=False),
                 "FINANCE_QUALIFICATION_PAIR_INVALID"),
                (replace(pairs[0], candidate_result_ref=pairs[1].candidate_result_ref),
                 "FINANCE_QUALIFICATION_PAIR_UNPAIRED"),
                (replace(pairs[0], baseline_assessment_ref=None),
                 "FINANCE_INDEPENDENT_QUALITY_RECEIPT_REQUIRED"),
            ):
                with pytest.raises(IntegrityError, match=reason):
                    service.issue_qualification(
                        suite_ref=suite_ref, candidate=candidate,
                        pairs=(changed, *pairs[1:]),
                        author_actor_id="actor:author",
                        candidate_proposal_ref=proposal_ref,
                    )
            service.experiment_operator_actor_id = None
            with pytest.raises(
                IntegrityError, match="FINANCE_EXPERIMENT_OPERATOR_UNCONFIGURED",
            ):
                service.issue_qualification(
                    suite_ref=suite_ref, candidate=candidate, pairs=pairs,
                    author_actor_id="actor:author", candidate_proposal_ref=proposal_ref,
                )
            service.experiment_operator_actor_id = "actor:mechanism-operator"
            service.assessor_actor_id = None
            with pytest.raises(
                IntegrityError, match="FINANCE_INDEPENDENT_QUALITY_ACTORS_UNCONFIGURED",
            ):
                service.issue_qualification(
                    suite_ref=suite_ref, candidate=candidate, pairs=pairs,
                    author_actor_id="actor:author", candidate_proposal_ref=proposal_ref,
                )
            service.assessor_actor_id = "actor:independent-assessor"
        with _as("actor:governor"), pytest.raises(
            AuthorizationError, match="FINANCE_QUALIFICATION_EVALUATOR_REQUIRED",
        ):
            service.issue_qualification(
                suite_ref=suite_ref, candidate=candidate, pairs=pairs,
                author_actor_id="actor:author", candidate_proposal_ref=proposal_ref,
            )
        with _as("actor:evaluator"), pytest.raises(
            IntegrityError, match="FINANCE_CANDIDATE_AUTHOR_MISMATCH",
        ):
            service.issue_qualification(
                suite_ref=suite_ref, candidate=candidate, pairs=pairs,
                author_actor_id="actor:forged-author", candidate_proposal_ref=proposal_ref,
            )
        with _as("actor:evaluator"):
            qualification_ref = service.issue_qualification(
                suite_ref=suite_ref, candidate=candidate, pairs=pairs,
                author_actor_id="actor:author", candidate_proposal_ref=proposal_ref,
            )
        assert head.resolve() == parent
        with _as("actor:evaluator"), pytest.raises(
            AuthorizationError, match="FINANCE_CONTENT_REVIEW_ROLE_COLLISION",
        ):
            service.review_content(
                candidate=candidate, qualification_ref=qualification_ref,
                author_actor_id="actor:author", reason_code="TEST_ONLY_MECHANISM",
            )
        with _as("actor:governor"):
            unrelated = head.prepare_instruction_patch(
                instruction + "Unapproved extra instruction.\n",
                expected_head_ref=parent.head_ref,
                expected_head_digest=parent.head_digest,
                expected_generation=parent.generation,
                expected_package_digest=parent.package_digest,
            )
            with pytest.raises(IntegrityError, match="FINANCE_CANDIDATE_PROPOSAL_INVALID"):
                service.review_content(
                    candidate=unrelated, qualification_ref=qualification_ref,
                    author_actor_id="actor:author", reason_code="TEST_ONLY_MECHANISM",
                )
            qualification = store.load_artifact(qualification_ref, QUALIFICATION_MEDIA).payload
            for suffix, changed, reason in (
                ("status", {**qualification, "status": "NOT_EVALUATED"},
                 "FINANCE_CONTENT_REVIEW_QUALIFICATION_INVALID"),
                ("gain", {**qualification, "total_gain": qualification["total_gain"] + 1},
                 "FINANCE_CONTENT_REVIEW_QUALIFICATION_DRIFT"),
            ):
                forged_ref = f"finance-skill-qualification:test-only-{suffix}"
                with store.transaction() as connection:
                    store.save_artifact(
                        connection, forged_ref, QUALIFICATION_MEDIA, changed,
                    )
                with pytest.raises(IntegrityError, match=reason):
                    service.review_content(
                        candidate=candidate, qualification_ref=forged_ref,
                        author_actor_id="actor:author", reason_code="TEST_ONLY_MECHANISM",
                    )
            release_ref = service.review_content(
                candidate=candidate, qualification_ref=qualification_ref,
                author_actor_id="actor:author", reason_code="TEST_ONLY_MECHANISM",
            )
            release = store.load_artifact(release_ref, CONTENT_RELEASE_MEDIA).payload
            forged_release_ref = "finance-content-release:test-only-wrong-digest"
            with store.transaction() as connection:
                store.save_artifact(connection, forged_release_ref, CONTENT_RELEASE_MEDIA, {
                    **release, "qualification_digest": sha256_digest("wrong-qualification"),
                })
            with pytest.raises(
                IntegrityError, match="FINANCE_SKILL_PROMOTION_EVIDENCE_INVALID",
            ):
                service.promote(
                    candidate=candidate, qualification_ref=qualification_ref,
                    content_release_ref=forged_release_ref,
                    expected_head_ref=parent.head_ref,
                    expected_head_digest=parent.head_digest,
                    expected_generation=parent.generation,
                    expected_package_digest=parent.package_digest,
                )
            drifted_qualification = {
                **qualification, "regression_count": 1,
            }
            drifted_qualification_ref = "finance-skill-qualification:test-only-regression"
            drifted_release_ref = "finance-content-release:test-only-regression"
            with store.transaction() as connection:
                store.save_artifact(
                    connection, drifted_qualification_ref, QUALIFICATION_MEDIA,
                    drifted_qualification,
                )
                store.save_artifact(
                    connection, drifted_release_ref, CONTENT_RELEASE_MEDIA,
                    {
                        **release,
                        "qualification_ref": drifted_qualification_ref,
                        "qualification_digest": sha256_digest(drifted_qualification),
                    },
                )
            with pytest.raises(IntegrityError, match="FINANCE_SKILL_QUALIFICATION_DRIFT"):
                service.promote(
                    candidate=candidate, qualification_ref=drifted_qualification_ref,
                    content_release_ref=drifted_release_ref,
                    expected_head_ref=parent.head_ref,
                    expected_head_digest=parent.head_digest,
                    expected_generation=parent.generation,
                    expected_package_digest=parent.package_digest,
                )
            assert head.resolve() == parent
            with pytest.raises(IntegrityError, match="FINANCE_SKILL_STALE_BASE"):
                service.promote(
                    candidate=candidate, qualification_ref=qualification_ref,
                    content_release_ref=release_ref,
                    expected_head_ref=parent.head_ref,
                    expected_head_digest=sha256_digest("wrong-head"),
                    expected_generation=parent.generation,
                    expected_package_digest=parent.package_digest,
                )
            promoted = service.promote(
                candidate=candidate, qualification_ref=qualification_ref,
                content_release_ref=release_ref,
                expected_head_ref=parent.head_ref,
                expected_head_digest=parent.head_digest,
                expected_generation=parent.generation,
                expected_package_digest=parent.package_digest,
            )
            with pytest.raises(IntegrityError, match="FINANCE_SKILL_STALE_BASE"):
                service.promote(
                    candidate=candidate, qualification_ref=qualification_ref,
                    content_release_ref=release_ref,
                    expected_head_ref=parent.head_ref,
                    expected_head_digest=parent.head_digest,
                    expected_generation=parent.generation,
                    expected_package_digest=parent.package_digest,
                )
        assert promoted.generation == 1
        assert promoted.bundle.payload == candidate.payload
        assert promoted.adoption_enabled is False
        assert store.verify_event_chain()["status"] == "PASS"
    with StateStore(database, tenant_id=TENANT, migrate=backend == "sqlite") as restarted:
        recovered = FinanceSkillHeadService(restarted).resolve()
        assert recovered.head_ref == promoted.head_ref
        assert recovered.bundle.payload == candidate.payload
        assert recovered.adoption_enabled is False
