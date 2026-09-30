"""Controlled-local source-qualified experience, without an asserted customer connector."""

from __future__ import annotations

import json
import time
from contextlib import closing, contextmanager
from functools import partial
from pathlib import Path
from urllib.parse import quote

import pytest
from enterprise_pack_factory import make_enterprise_pack
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import AuthenticationError, Principal, request_principal
from orgrebase.domain import IntegrityError
from orgrebase.workspace.change_recovery import resume_change, return_for_evidence
from orgrebase.workspace.dataverse import DataverseReader, SourcePage, SourceSynchronizer
from orgrebase.workspace.experience_assessment import ExperienceAssessmentService, require_quote_case_current
from orgrebase.workspace.experience_collection import COLLECTION_MEDIA, OBSERVATION_MEDIA, ExperienceCollector
from orgrebase.workspace.experience_contracts import (
    CaseObservationV2,
    MemorySnapshot,
    RecallSelectionManifest,
)
from orgrebase.workspace.experience_invalidation import ExperienceInvalidationService
from orgrebase.workspace.experience_lessons import ExperienceLessonService, lesson_object_prefix
from orgrebase.workspace.experience_recall import SELECTION_MEDIA, SNAPSHOT_MEDIA, ExperienceRecallService
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.source_bindings import (
    SourceBindingConfig,
    SourceBindingConfirmation,
    SourceBindingProposal,
    SourceFieldMapping,
    active_binding,
    confirm_binding,
    observe_inventory,
    propose_binding,
    record_coverage,
    source_coverage,
)
from orgrebase.workspace.source_worker import WorkspaceSourceAdmission
from tests.postgres_support import postgres_runtime as postgres_runtime
from tests.workspace.test_change_agentteams import configure
from tests.workspace.test_change_recovery import resume_command, return_command
from tests.workspace.test_experience_lessons import PROFILE, _body

RECORD = "00000000-0000-0000-0000-000000000001"
FIELD = "new_product_plan"
SLOT = "product_plan"


@pytest.fixture(params=("sqlite", "postgres"))
def source_workspace(request, tmp_path):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    kwargs = {
        "runtime_configuration": runtime,
        "store_tenant_id": runtime.profile.organization_id,
        "review_duration_seconds": 0,
    }
    if request.param == "postgres":
        credentials = request.getfixturevalue("postgres_runtime")(
            tenant_id=runtime.profile.organization_id,
        )
        kwargs.update(store_path=credentials["runtime_dsn"], store_migrate=False)
    else:
        kwargs["store_path"] = tmp_path / "experience-source.sqlite"
    with closing(WorkspaceService(**kwargs)) as workspace:
        workspace.form_quote()
        configure(workspace, tmp_path / "native")
        members = {"operator": "user:operator"}
        members.update({item.slot_id: item.owner_id for item in workspace.enterprise_binding.resources})
        workspace.identity_issuer = "https://controlled-local-issuer.example"

        def verify(subject, actor, _action):
            if members.get(subject) != actor:
                raise AuthenticationError("AUTH_MEMBERSHIP_DENIED", 403)

        workspace.verify_membership = verify

        def require_member(subject):
            if subject not in members:
                raise AuthenticationError("AUTH_WORKSPACE_DENIED", 403)

        workspace.authorize_workspace_subject = require_member
        workspace.members_for_action = lambda _action: tuple(
            {"subject": subject, "actor_id": actor} for subject, actor in members.items()
        )

        @contextmanager
        def principal(subject: str):
            token = request_principal.set(Principal(
                issuer=workspace.identity_issuer, subject=subject,
                tenant_id=runtime.profile.organization_id, actor_id=members[subject],
                roles=frozenset({"administrator"}), expires_at=int(time.time()) + 900,
            ))
            try:
                yield
            finally:
                request_principal.reset(token)

        config = SourceBindingConfig.model_validate({
            "source": {
                "connector_id": "source:experience-qualified",
                "tenant_id": runtime.profile.organization_id,
                "instance_url": "https://company.crm.dynamics.com",
                "record_ids": [RECORD],
            },
            "workspace_id": workspace.store.workspace_id,
            "organization_id": "00000000-0000-0000-0000-000000000987",
            "source_token_variable": "UNUSED_SOURCE_TOKEN",
            "access_token_variable": "UNUSED_ACCESS_TOKEN",
        })
        mapping = SourceFieldMapping(record_id=RECORD, field=FIELD, slot_id=SLOT)
        inventory = {
            "schema_version": "orgrebase.source-inventory.v1", "config_digest": config.digest,
            "organization_id": config.organization_id,
            "fields": [{"field": FIELD, "label": "Product plan", "transforms": ["identity"]}],
        }
        with principal("operator"):
            observed = observe_inventory(workspace, config, inventory)
            proposed = propose_binding(
                workspace, config, SourceBindingProposal(**observed, mappings=[mapping]),
            )
        digest = proposed["proposal"]["proposal_digest"]
        with principal(SLOT):
            confirm_binding(
                workspace, config, digest[7:], SourceBindingConfirmation(proposal_digest=digest),
            )
        yield workspace, principal, config, mapping


@contextmanager
def _actor(workspace, name: str, role: str):
    token = request_principal.set(Principal(
        issuer="local:experience-source-test", subject=name,
        tenant_id=workspace.store.tenant_id, actor_id=f"actor:{name}",
        roles=frozenset({role}), expires_at=4_102_444_800,
    ))
    try:
        yield
    finally:
        request_principal.reset(token)


def _sync_qualified_source(workspace, principal, config, mapping, tmp_path: Path):
    config_path = tmp_path / "source-binding.json"
    config_path.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")
    config_path.chmod(0o600)
    workspace.source_config_path = config_path
    row = {"quoteid": RECORD, "@odata.etag": 'W/"source-a"',
           FIELD: "Renewed source service plan"}
    with principal("operator"):
        binding = active_binding(workspace, config)
        settings = config.source.reader_settings(
            (FIELD,), connector_id=binding["connector_id"], record_ids=(RECORD,),
        )
        receiver = WorkspaceSourceAdmission(
            workspace, settings, (mapping,), confirmed_binding_digest=binding["binding_digest"],
        )
        reader = DataverseReader(settings, lambda: pytest.fail("controlled transport never requests a token"))
        reader.fetch = lambda _cursor: reader.parse_page({
            "@odata.deltaLink": settings.endpoint + "?$deltatoken=source-a", "value": [row],
        })
        reader.read_record = lambda _identity: reader._live_record(row)

        def verify_page(page: SourcePage) -> SourcePage:
            readback = {RECORD: {**reader.read_record(RECORD), "observed_at": workspace.clock.now()}}
            return SourcePage(
                tuple(readback.values()), page.cursor, False,
                {"complete": True, "record_ids": [RECORD],
                 "method": "CURRENT_POINT_READ", "cross_source_atomic": False},
            )

        synchronizer = SourceSynchronizer(
            workspace.store, reader, worker_id="controlled-source-reader",
            admit=receiver, admission_digest=receiver.admission_digest,
            clock=workspace.clock, verify_page=verify_page,
            page_committed=lambda connection, inbox, checkpoint, page: record_coverage(
                workspace, config, binding, connection, inbox, checkpoint, page,
            ),
        )
        assert synchronizer.sync_page()["records_admitted"] == 1
        coverage = source_coverage(workspace, config)
        assert coverage["status"] == "COMPLETE" and coverage["reasons"] == []
    workspace.changes.refresh()
    (event,) = workspace.changes.all()
    assert event.event_id.startswith("source:")
    assert event.proposal.payload["source_observation_ref"].startswith("source-observation:")
    return event, synchronizer, reader, row


@pytest.mark.parametrize("invalidation_mode", ("SOURCE_REVISION", "PRIVATE_DELETE"))
def test_source_qualified_case_to_recall_then_revision_hold(
    source_workspace, tmp_path, invalidation_mode, monkeypatch,
):
    workspace, principal, config, mapping = source_workspace
    for phase in ("collector", "author", "evaluator", "reviewer", "corpus"):
        monkeypatch.setenv(f"ORGREBASE_EXPERIENCE_{phase.upper()}_ACTOR_ID", f"actor:{phase}")
    monkeypatch.setenv("ORGREBASE_EXPERIENCE_RUBRIC_VERSION", "finance-explanation-controlled-v1")
    event, synchronizer, reader, row = _sync_qualified_source(
        workspace, principal, config, mapping, tmp_path,
    )
    with principal("operator"):
        workspace.preview_change(event.event_id)
    with principal(SLOT):
        return_for_evidence(workspace, event.event_id, return_command(workspace, event.event_id))
        ready = resume_change(workspace, event.event_id, resume_command(workspace, event.event_id))
        preview = workspace._preview_record(event.event_id)
        approval = workspace.approve_change(
            event.event_id, actor_id=event.owner_id,
            preview_digest=preview["preview_digest"],
            recovery_digest=ready["recovery_digest"],
        )
        workspace.apply_approved_change(event.event_id, approval_digest=approval["approval_digest"])

    with _actor(workspace, "collector", "reader"):
        collector = ExperienceCollector(
            workspace, collector_actor_id="actor:collector", enabled=True,
        )
        for _ in range(20):
            result = collector.collect(worker_id="controlled-case-reader", limit=500)
            if result.status == "IDLE":
                break
        else:
            pytest.fail("bounded collector did not reach the current event head")
    cases = []
    for row_ref in workspace.store.list_artifacts(
        artifact_id_prefix="experience-collection:", expected_media_type=COLLECTION_MEDIA,
    ):
        case_ref = row_ref.payload.get("case_ref")
        if case_ref is None:
            continue
        case = CaseObservationV2.model_validate(
            workspace.store.load_artifact(case_ref, OBSERVATION_MEDIA).payload,
        )
        if (
            case.business_event_id == event.event_id
            and case.resume_digest is not None
            and case.execution_outcome == "APPLIED"
        ):
            cases.append((case_ref, case))
    assert cases, "the admitted source change must produce a recovery case"
    case_ref, case = cases[0]
    assert case.cluster_status == "PROVISIONAL"
    assert case.learning_assessment == "UNASSESSED"
    assert case.independence_cluster_id
    assert workspace.store.get_object(event.proposal.id).digest == event.proposal.digest

    assessment = ExperienceAssessmentService(
        workspace, evaluator_actor_id="actor:evaluator",
        excluded_actor_ids=("actor:collector", "actor:author", "actor:reviewer"),
        rubric_version="finance-explanation-controlled-v1",
        source_qualification_check=partial(require_quote_case_current, workspace),
    )
    with _actor(workspace, "evaluator", "governor"):
        require_quote_case_current(workspace, case)
        support_ref = assessment.record_assessment(
            operation_id="assess:source-qualified", case_ref=case_ref,
            profile_id=PROFILE, verdict="SUPPORT", reason_code="INDEPENDENT_SOURCE_RUBRIC_PASS",
            evidence_refs=(case.business_event_digest,),
            independence_cluster_id=case.independence_cluster_id,
        )
        _, receipt, proof = assessment.read_assessment(support_ref)
        assert receipt.verdict == "SUPPORT" and proof is not None
    lessons = ExperienceLessonService(
        workspace, author_actor_id="actor:author", reviewer_actor_id="actor:reviewer",
        assessment_service=assessment,
    )
    with _actor(workspace, "author", "operator"):
        candidate_ref = lessons.propose(
            operation_id="candidate:source-qualified", profile_id=PROFILE,
            lesson_id="qualified-source-check", body=_body(),
            support_assessment_refs=(support_ref,),
        )
    with _actor(workspace, "reviewer", "governor"):
        reviewed = lessons.review_candidate(candidate_ref)
        assert reviewed["status"] == "REVIEW_ONLY_NOT_PUBLISHED"
        lessons.apply_delta(
            operation_id="release:source-qualified", action="ADD",
            candidate_ref=candidate_ref, expected_heads=(),
            reason_code="EXACT_CONTENT_REVIEWED",
            body_bytes_digest=reviewed["body_bytes_digest"],
            declassified_exact_content=True, purpose=PROFILE,
            recipients=("workspace-advisory",),
        )
    assert workspace.store.get_object(
        lesson_object_prefix(PROFILE) + "qualified-source-check",
    ).payload["status"] == "ADMITTED"
    recall = ExperienceRecallService(
        workspace, profile_id=PROFILE, assessment_service=assessment,
    )
    with _actor(workspace, "reader", "reader"):
        snapshot_ref = recall.build_snapshot(
            purpose=PROFILE, recipient="workspace-advisory",
        )
        snapshot = MemorySnapshot.model_validate(
            workspace.store.load_artifact(snapshot_ref, SNAPSHOT_MEDIA).payload,
        )
        assert snapshot.coverage == "COMPLETE" and len(snapshot.lesson_entries) == 1
        manifest_ref, receipt_ref = recall.select_for_case(
            operation_id="recall:source-qualified", snapshot_ref=snapshot_ref,
            task_id="task:finance-explanation", attempt_id="advisory:qualified",
            context_digest="sha256:" + "1" * 64,
            case_id="finance:source-qualified", case_revision="r1",
            evaluation_arm="SPARSE_RECALL", query_text="Finance 来源证据核对",
            context_tags=("finance:source-review",), object_kinds=(),
            problem_codes=("FINANCE_SOURCE_REVIEW",),
        )
        manifest = RecallSelectionManifest.model_validate(
            workspace.store.load_artifact(manifest_ref, SELECTION_MEDIA).payload,
        )
        assert len(manifest.selected_lessons) == 1
        assert receipt_ref.startswith("experience-recall-receipt:")
        _, advice = recall.validate_manifest_for_consumption(
            manifest_ref, snapshot_ref=snapshot_ref,
            purpose=PROFILE, recipient="workspace-advisory",
        )
        assert "来源失效时停止使用" in advice
        with TestClient(create_app(workspace_service=workspace)) as client:
            case_detail = client.get(
                "/api/workspace/experience-cases/" + quote(case_ref, safe=""),
            )
            assert case_detail.status_code == 200, case_detail.text
            assert case_detail.json()["source_state"] == "CURRENT"
            assert case_detail.json()["private_content_disclosed"] is False
            lesson_detail = client.get(
                "/api/workspace/experience-lessons/heads/qualified-source-check",
            )
            assert lesson_detail.status_code == 200, lesson_detail.text
            assert lesson_detail.json()["status"] == "QUALIFIED_FOR_RECALL"
            assert lesson_detail.json()["related_case_refs"] == [case_ref]
            assert lesson_detail.json()["retrieval_status"] == "RECALLED"
            assert lesson_detail.json()["actual_use_status"] == "NOT_CHECKED"
            assert lesson_detail.json()["content_bytes_disclosed"] == 0

    prior_quote_digest = workspace.current_quote().digest
    if invalidation_mode == "SOURCE_REVISION":
        # Same value, different opaque source revision invalidates the exact
        # observation despite complete connector coverage.
        replacement = {**row, "@odata.etag": 'W/"source-b"'}
        reader.fetch = lambda _cursor: reader.parse_page({
            "@odata.deltaLink": reader.settings.endpoint + "?$deltatoken=source-b",
            "value": [replacement],
        })
        reader.read_record = lambda _identity: reader._live_record(replacement)
        with principal("operator"):
            assert synchronizer.sync_page()["records_admitted"] == 1
            assert source_coverage(workspace, config)["status"] == "COMPLETE"
    else:
        assert workspace.private_records.erase(
            case.private_episode_ref, actor_id="actor:collector",
        )
        invalidation = ExperienceInvalidationService(
            workspace, manager_actor_id="actor:privacy", profile_ids=(PROFILE,),
        )
        with _actor(workspace, "privacy", "administrator"):
            receipt_ref = invalidation.record_source_invalidation(
                operation_id="invalidate:source-qualified",
                source_record_id=case.private_episode_ref,
            )
            assert invalidation.verify_after_restore(receipt_ref).closure_complete is False
    assert workspace.current_quote().digest == prior_quote_digest
    with _actor(workspace, "evaluator", "governor"), pytest.raises(IntegrityError):
        require_quote_case_current(workspace, case)
    with _actor(workspace, "reader", "reader"):
        assert assessment.evidence_verdict_for_reader(support_ref) is None
        assert assessment.assessment_projection_for_reader(case_ref, PROFILE)["status"] == "HOLD"
        with pytest.raises(IntegrityError, match="MEMORY_HOLD"):
            recall.validate_manifest_for_consumption(
                manifest_ref, snapshot_ref=snapshot_ref,
                purpose=PROFILE, recipient="workspace-advisory",
            )
        current = recall.build_snapshot(purpose=PROFILE, recipient="workspace-advisory")
        assert MemorySnapshot.model_validate(
            workspace.store.load_artifact(current, SNAPSHOT_MEDIA).payload,
        ).coverage == "EMPTY"
        with TestClient(create_app(workspace_service=workspace)) as client:
            case_detail = client.get(
                "/api/workspace/experience-cases/" + quote(case_ref, safe=""),
            )
            assert case_detail.status_code == 200, case_detail.text
            assert case_detail.json()["source_state"] == "HOLD"
            assert case_detail.json()["source_reason_code"] == (
                "PRIVATE_EPISODE_DELETED" if invalidation_mode == "PRIVATE_DELETE"
                else "SOURCE_QUALIFICATION_UNAVAILABLE"
            )
            lesson_detail = client.get(
                "/api/workspace/experience-lessons/heads/qualified-source-check",
            )
            assert lesson_detail.status_code == 200, lesson_detail.text
            assert lesson_detail.json()["status"] == "HOLD"
            assert lesson_detail.json()["reason_code"] == case_detail.json()["source_reason_code"]
            assert lesson_detail.json()["retrieval_status"] == "RECALLED"
            assert lesson_detail.json()["actual_use_status"] == "NOT_CHECKED"
            assert "Renewed source service plan" not in lesson_detail.text
