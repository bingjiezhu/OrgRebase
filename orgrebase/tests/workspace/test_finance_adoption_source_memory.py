"""A qualified source lesson used by the *next* normal Finance change.

The head promotion and Vertex HTTP response are controlled mechanism fixtures.
SourceBinding, case assessment, Lesson publication, Finance preview, V4 wire,
Recall and Use all run through their production code paths on SQLite and a
real local PostgreSQL runtime role. No customer or model-quality claim follows.
"""

from __future__ import annotations

import json
import time
import urllib.request
from contextlib import closing, contextmanager
from functools import partial

import pytest
from enterprise_pack_factory import make_enterprise_pack

from orgrebase.auth import AuthenticationError, Principal, request_principal
from orgrebase.digest import sha256_digest
from orgrebase.domain import FreshnessError, IntegrityError
from orgrebase.workspace.change_proposals import submit_change
from orgrebase.workspace.change_recovery import resume_change, return_for_evidence
from orgrebase.workspace.dataverse import DataverseReader, SourcePage, SourceSynchronizer
from orgrebase.workspace.experience_assessment import ExperienceAssessmentService, require_quote_case_current
from orgrebase.workspace.experience_collection import COLLECTION_MEDIA, OBSERVATION_MEDIA, ExperienceCollector
from orgrebase.workspace.experience_contracts import (
    CaseObservationV2,
    MemorySnapshot,
    RecallSelectionManifest,
    exact_bytes_digest,
)
from orgrebase.workspace.experience_governance_operations import ExperiencePhaseActors, _recall
from orgrebase.workspace.experience_lessons import ExperienceLessonService, LessonBody, lesson_object_prefix
from orgrebase.workspace.experience_recall import SNAPSHOT_MEDIA
from orgrebase.workspace.finance_adoption import (
    USE_MEDIA,
    adopted_adapter_for_preview,
    finance_cluster_digest,
    selection_for_bundle,
    use_ref,
)
from orgrebase.workspace.finance_skill_qualification import CONTENT_RELEASE_MEDIA, QUALIFICATION_MEDIA
from orgrebase.workspace.model_provider import VERTEX_CANDIDATE_MODEL_ID
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.read_dependencies import (
    ReadDependencies,
    ReadDependencyError,
    ReadQuery,
    capture_read_witness,
    dependency_payload,
)
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.skill_evolution_v2 import CONTENT_MEDIA, FinanceSkillHeadService
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
from tests.workspace.test_change_agentteams import configure
from tests.workspace.test_change_proposals import command
from tests.workspace.test_change_recovery import resume_command, return_command
from tests.workspace.test_experience_source_qualification import _actor
from tests.workspace.test_finance_adoption import _policy, _save_policy
from tests.workspace.test_finance_advice_v4 import _provider
from tests.workspace.test_finance_explanation_operations import _as, _fake_send

PROFILE = "workspace-change-explanation-v1"
A_RECORD = "00000000-0000-0000-0000-000000000001"
B_RECORD = "00000000-0000-0000-0000-000000000002"
A_FIELD = "new_product_plan"
B_FIELD = "new_currency"


@contextmanager
def _source_workspace(tmp_path, runtime, backend, postgres_runtime_factory):
    tenant = runtime.profile.organization_id
    storage = (
        postgres_runtime_factory(tenant_id=tenant)["runtime_dsn"]
        if backend == "postgres" else tmp_path / "source-memory.sqlite"
    )
    with closing(WorkspaceService(
        store_path=storage, store_tenant_id=tenant,
        store_migrate=backend == "sqlite", runtime_configuration=runtime,
        review_duration_seconds=0, advisory_provider=_provider(),
        advisory_model_id=VERTEX_CANDIDATE_MODEL_ID,
    )) as workspace:
        workspace.form_quote()
        configure(workspace, tmp_path / "native")
        members = {"operator": "user:operator"}
        members.update({item.slot_id: item.owner_id for item in workspace.enterprise_binding.resources})
        workspace.identity_issuer = "https://controlled-local-issuer.example"

        def verify(subject, actor, _action):
            if members.get(subject) != actor:
                raise AuthenticationError("AUTH_MEMBERSHIP_DENIED", 403)

        workspace.verify_membership = verify
        def authorize_subject(subject):
            if subject not in members:
                raise AuthenticationError("AUTH_WORKSPACE_DENIED", 403)

        workspace.authorize_workspace_subject = authorize_subject
        workspace.members_for_action = lambda _action: tuple(
            {"subject": subject, "actor_id": actor} for subject, actor in members.items()
        )

        @contextmanager
        def principal(subject: str):
            token = request_principal.set(Principal(
                issuer=workspace.identity_issuer, subject=subject,
                tenant_id=tenant, actor_id=members[subject],
                roles=frozenset({"administrator"}), expires_at=int(time.time()) + 900,
            ))
            try:
                yield
            finally:
                request_principal.reset(token)

        config = SourceBindingConfig.model_validate({
            "source": {
                "connector_id": "source:experience-finance-next",
                "tenant_id": tenant,
                "instance_url": "https://company.crm.dynamics.com",
                "record_ids": [A_RECORD, B_RECORD],
            },
            "workspace_id": workspace.store.workspace_id,
            "organization_id": "00000000-0000-0000-0000-000000000987",
            "source_token_variable": "UNUSED_SOURCE_TOKEN",
            "access_token_variable": "UNUSED_ACCESS_TOKEN",
        })
        mappings = (
            SourceFieldMapping(record_id=A_RECORD, field=A_FIELD, slot_id="product_plan"),
            SourceFieldMapping(record_id=B_RECORD, field=B_FIELD, slot_id="currency"),
        )
        inventory = {
            "schema_version": "orgrebase.source-inventory.v1",
            "config_digest": config.digest, "organization_id": config.organization_id,
            "fields": [
                {"field": A_FIELD, "label": "Product plan", "transforms": ["identity"]},
                {"field": B_FIELD, "label": "Currency", "transforms": ["identity"]},
            ],
        }
        with principal("operator"):
            observed = observe_inventory(workspace, config, inventory)
            proposed = propose_binding(
                workspace, config, SourceBindingProposal(**observed, mappings=list(mappings)),
            )
        digest = proposed["proposal"]["proposal_digest"]
        for slot in ("product_plan", "currency"):
            with principal(slot):
                confirm_binding(
                    workspace, config, digest[7:], SourceBindingConfirmation(proposal_digest=digest),
                )
        config_path = tmp_path / "two-source-binding.json"
        config_path.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")
        config_path.chmod(0o600)
        workspace.source_config_path = config_path
        yield workspace, principal, config, mappings


def _source_sync(workspace, principal, config, mappings):
    row_a = {
        "quoteid": A_RECORD, "@odata.etag": 'W/"source-a1"',
        A_FIELD: "Renewed source service plan", B_FIELD: "USD",
    }
    row_b = {
        "quoteid": B_RECORD, "@odata.etag": 'W/"source-b1"',
        A_FIELD: "Baseline service plan", B_FIELD: "USD",
    }
    rows = {A_RECORD: row_a, B_RECORD: row_b}
    with principal("operator"):
        binding = active_binding(workspace, config)
        settings = config.source.reader_settings(
            (A_FIELD, B_FIELD), connector_id=binding["connector_id"],
            record_ids=(A_RECORD, B_RECORD),
        )
        receiver = WorkspaceSourceAdmission(
            workspace, settings, mappings, confirmed_binding_digest=binding["binding_digest"],
        )
        reader = DataverseReader(settings, lambda: pytest.fail("controlled transport requested a token"))
        page = {"records": (row_a, row_b), "cursor": "a1"}
        reader.fetch = lambda _cursor: reader.parse_page({
            "@odata.deltaLink": settings.endpoint + "?$deltatoken=" + page["cursor"],
            "value": list(page["records"]),
        })
        reader.read_record = lambda identity: reader._live_record(rows[identity])

        def verify_page(source_page: SourcePage) -> SourcePage:
            current = tuple({
                **reader.read_record(record_id), "observed_at": workspace.clock.now(),
            } for record_id in (A_RECORD, B_RECORD))
            return SourcePage(
                current, source_page.cursor, False,
                {"complete": True, "record_ids": [A_RECORD, B_RECORD],
                 "method": "CURRENT_POINT_READ", "cross_source_atomic": False},
            )

        synchronizer = SourceSynchronizer(
            workspace.store, reader, worker_id="controlled-two-source-reader",
            admit=receiver, admission_digest=receiver.admission_digest,
            clock=workspace.clock, verify_page=verify_page,
            page_committed=lambda connection, inbox, checkpoint, page_ref: record_coverage(
                workspace, config, binding, connection, inbox, checkpoint, page_ref,
            ),
        )
        assert synchronizer.sync_page()["records_admitted"] == 2
        coverage = source_coverage(workspace, config)
        assert coverage["status"] == "COMPLETE" and coverage["reasons"] == []
    workspace.changes.refresh()
    events = tuple(workspace.changes.all())
    a_events = [event for event in events if event.slot_id == "product_plan"]
    assert len(a_events) == 1
    return a_events[0], synchronizer, reader, rows, page


def _publish_source_lesson(workspace, principal, event_a):
    with principal("operator"):
        workspace.preview_change(event_a.event_id)
    with principal("product_plan"):
        return_for_evidence(workspace, event_a.event_id, return_command(workspace, event_a.event_id))
        ready = resume_change(workspace, event_a.event_id, resume_command(workspace, event_a.event_id))
        preview = workspace._preview_record(event_a.event_id)
        approval = workspace.approve_change(
            event_a.event_id, actor_id=event_a.owner_id,
            preview_digest=preview["preview_digest"], recovery_digest=ready["recovery_digest"],
        )
        workspace.apply_approved_change(event_a.event_id, approval_digest=approval["approval_digest"])
    with _actor(workspace, "collector", "reader"):
        collector = ExperienceCollector(
            workspace, collector_actor_id="actor:collector", enabled=True,
        )
        for _ in range(30):
            if collector.collect(worker_id="controlled-source-memory", limit=500).status == "IDLE":
                break
        else:
            pytest.fail("collector did not reach event head")
    cases = []
    for row in workspace.store.list_artifacts(
        artifact_id_prefix="experience-collection:", expected_media_type=COLLECTION_MEDIA,
    ):
        ref = row.payload.get("case_ref")
        if ref is None:
            continue
        case = CaseObservationV2.model_validate(
            workspace.store.load_artifact(ref, OBSERVATION_MEDIA).payload,
        )
        if (
            case.business_event_id == event_a.event_id
            and case.resume_digest is not None
            and case.execution_outcome == "APPLIED"
        ):
            cases.append((ref, case))
    assert len(cases) == 1
    case_ref, case = cases[0]
    assessment = ExperienceAssessmentService(
        workspace, evaluator_actor_id="actor:evaluator",
        excluded_actor_ids=("actor:collector", "actor:author", "actor:reviewer"),
        rubric_version="finance-source-next-v1",
        source_qualification_check=partial(require_quote_case_current, workspace),
    )
    with _actor(workspace, "evaluator", "governor"):
        support_ref = assessment.record_assessment(
            operation_id="assess:source-a", case_ref=case_ref, profile_id=PROFILE,
            verdict="SUPPORT", reason_code="INDEPENDENT_SOURCE_RUBRIC_PASS",
            evidence_refs=(case.business_event_digest,),
            independence_cluster_id=case.independence_cluster_id,
        )
    body = LessonBody(
        kind="PROCEDURAL_ADVICE", problem_code="FINANCE_SOURCE_REVIEW",
        applicability_tags=("finance:source-review",),
        applicability=("Finance 解释依赖当前已覆盖的来源事实。",),
        contraindications=("来源覆盖不完整或事实修订不一致时不得使用。",),
        steps=("核对解释中的事实引用与当前来源。",),
        stop_conditions=("来源修订、权限或覆盖发生变化时立即停止。",),
    )
    lesson = ExperienceLessonService(
        workspace, author_actor_id="actor:author", reviewer_actor_id="actor:reviewer",
        assessment_service=assessment,
    )
    with _actor(workspace, "author", "operator"):
        candidate_ref = lesson.propose(
            operation_id="candidate:source-a", profile_id=PROFILE,
            lesson_id="source-current-facts", body=body,
            support_assessment_refs=(support_ref,),
        )
    with _actor(workspace, "reviewer", "governor"):
        reviewed = lesson.review_candidate(candidate_ref)
        lesson.apply_delta(
            operation_id="release:source-a", action="ADD", candidate_ref=candidate_ref,
            expected_heads=(), reason_code="EXACT_CONTENT_REVIEWED",
            body_bytes_digest=reviewed["body_bytes_digest"],
            declassified_exact_content=True, purpose=PROFILE,
            recipients=("workspace-advisory",),
        )
    return workspace.store.get_object(lesson_object_prefix(PROFILE) + "source-current-facts"), case


def _qualified_head(workspace):
    """Hand-built qualification is only a controlled consumer wiring fixture."""

    tenant = workspace.profile.organization_id
    with _as(workspace, "actor:finance-governor", "governor"):
        service = FinanceSkillHeadService(workspace.store, tenant_id=tenant)
        genesis = service.bootstrap()
    candidate = service.prepare_instruction_patch(
        genesis.bundle.instruction_text + "\nReview the current Finance source.\n",
        expected_head_ref=genesis.head_ref, expected_head_digest=genesis.head_digest,
        expected_generation=0, expected_package_digest=genesis.package_digest,
    )
    qualification_ref = "finance-skill-qualification:controlled-source-memory"
    qualification = {
        "schema_version": "orgrebase.finance-skill-qualification.v1",
        "status": "QUALIFIED", "candidate_bundle_digest": candidate.digest,
        "candidate_package_digest": candidate.package_digest,
    }
    release_ref = "finance-content-release:controlled-source-memory"
    release = {
        "schema_version": "orgrebase.finance-content-release.v1",
        "qualification_ref": qualification_ref,
        "qualification_digest": sha256_digest(qualification),
        "candidate_bundle_digest": candidate.digest,
        "candidate_package_digest": candidate.package_digest,
        "adoption_enabled": False,
    }
    previous = service._current_source()
    promoted = service._head_object(
        generation=1, transition_kind="PROMOTE", bundle=candidate,
        previous=previous, actor_id="actor:finance-governor",
        qualification_status="QUALIFIED", qualification_ref=qualification_ref,
        content_release_ref=release_ref,
    )
    with workspace.store.transaction() as connection:
        workspace.store.save_artifact(connection, service._bundle_ref(candidate), CONTENT_MEDIA, candidate.payload)
        workspace.store.save_artifact(connection, qualification_ref, QUALIFICATION_MEDIA, qualification)
        workspace.store.save_artifact(connection, release_ref, CONTENT_RELEASE_MEDIA, release)
        workspace.store.insert_version(connection, promoted, make_current=False)
        workspace.store.promote_version(connection, service.head_id, previous.version, promoted.version)
    return service.resolve(), qualification_ref, sha256_digest(qualification), release_ref


@pytest.mark.parametrize("backend", ("sqlite", "postgres"))
@pytest.mark.parametrize(("b_kind", "failure_stage"), (
    ("SOURCE_B", None),
    ("HUMAN_B", None),
    ("HUMAN_B_WITH_WITNESS", "B_WITNESS_REPLACED_AFTER_PREVIEW"),
    ("SOURCE_B", "B_REVISION_REPLACED_BEFORE_PREVIEW"),
    ("SOURCE_B", "A_REVISION_REPLACED_AFTER_PREVIEW"),
))
def test_next_finance_business_change_consumes_only_current_source_relevant_lesson(
    tmp_path, monkeypatch, postgres_runtime, backend, b_kind, failure_stage,
):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    with _source_workspace(tmp_path, runtime, backend, postgres_runtime) as (
        target, principal, config, mappings,
    ):
        sent = []
        monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, target))
        for phase in ("collector", "author", "evaluator", "reviewer", "corpus"):
            monkeypatch.setenv(f"ORGREBASE_EXPERIENCE_{phase.upper()}_ACTOR_ID", f"actor:{phase}")
        monkeypatch.setenv("ORGREBASE_EXPERIENCE_RUBRIC_VERSION", "finance-source-next-v1")
        event_a, synchronizer, _reader, rows, page = _source_sync(
            target, principal, config, mappings,
        )
        lesson, case_a = _publish_source_lesson(target, principal, event_a)
        with _actor(target, "reader", "reader"):
            require_quote_case_current(target, case_a)
        if b_kind == "SOURCE_B":
            rows[B_RECORD] = {**rows[B_RECORD], "@odata.etag": 'W/"source-b2"', B_FIELD: "EUR"}
            page.update(records=(rows[B_RECORD],), cursor="b2")
            with principal("operator"):
                assert synchronizer.sync_page()["records_admitted"] == 2
                coverage = source_coverage(target, config)
                assert coverage["status"] == "COMPLETE"
                assert coverage["records"][A_RECORD]["revision"] == 'W/"source-a1"'
            target.changes.refresh()
            next_events = tuple(event for event in target.changes.all() if event.slot_id == "currency")
            assert len(next_events) == 1
            event_b = next_events[0]
            assert event_b.event_id.startswith("source:")
        else:
            with principal("operator"):
                dependencies = None
                if b_kind == "HUMAN_B_WITH_WITNESS":
                    binding = active_binding(target, config)
                    witness = capture_read_witness(target, ReadQuery(
                        connector_id=binding["connector_id"], record_ids=(B_RECORD,),
                        operator="eq", field=B_FIELD, value="USD",
                    ))
                    assert witness.result == "TRUE"
                    dependencies = ReadDependencies.model_validate(dependency_payload(witness))
                event_id = submit_change(target, command(
                    target, slot="currency", value="EUR", read_dependencies=dependencies,
                ))["event"]["event_id"]
            event_b = target.changes.get(event_id)
            assert not event_b.event_id.startswith("source:")
        with _actor(target, "reader", "reader"):
            require_quote_case_current(target, case_a)
            baseline_ref = _recall(target, ExperiencePhaseActors.from_deployment()).build_snapshot(
                purpose=PROFILE, recipient="workspace-advisory",
            )
            baseline = MemorySnapshot.model_validate(
                target.store.load_artifact(baseline_ref, SNAPSHOT_MEDIA).payload,
            ).revalidated()
            assert tuple(entry.lesson_ref for entry in baseline.lesson_entries) == (lesson.ref,)
        head, qualification_ref, qualification_digest, release_ref = _qualified_head(target)
        event_id = event_b.event_id
        spec = target.change_spec(event_id)
        fixture = target._fixture_for_change(spec)
        change_set = target.change_builder.build(fixture=fixture, spec=spec)
        policy = _policy(target, head, finance_cluster_digest(target, event_id, change_set)).model_copy(update={
            "qualification_ref": qualification_ref,
            "qualification_digest": qualification_digest,
            "content_release_ref": release_ref,
        })
        _save_policy(tmp_path, policy, monkeypatch)
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        before_b_send = len(sent)
        if failure_stage == "B_REVISION_REPLACED_BEFORE_PREVIEW":
            rows[B_RECORD] = {**rows[B_RECORD], "@odata.etag": 'W/"source-b3"'}
            page.update(records=(rows[B_RECORD],), cursor="b3")
            with principal("operator"):
                assert synchronizer.sync_page()["records_admitted"] == 2
                coverage = source_coverage(target, config)
                assert coverage["status"] == "COMPLETE"
                assert coverage["records"][B_RECORD]["revision"] == 'W/"source-b3"'
            with _as(target, "actor:finance-operator", "operator"), pytest.raises(
                (FreshnessError, IntegrityError, RuntimeError),
            ):
                target.preview_change(event_id)
            assert len(sent) == before_b_send
            assert not target.store.list_artifacts(artifact_id_prefix="finance-adoption-selection:")
            assert target.current_quote().payload["currency"] == "USD"
            return
        with _as(target, "actor:finance-operator", "operator"):
            bundle = target.preview_change(event_id)
            selection = selection_for_bundle(target, event_id, bundle)
        assert selection is not None
        manifest = RecallSelectionManifest.model_validate(
            target.store.load_artifact(selection.manifest_ref).payload,
        ).revalidated()
        selected = tuple(entry.lesson_ref for entry in manifest.selected_lessons)
        assert selected == ((lesson.ref,) if b_kind == "SOURCE_B" else ())
        if b_kind == "SOURCE_B":
            assert manifest.advice_byte_count > 0
        else:
            assert manifest.advice_byte_count == 0
        users = [json.loads(body["contents"][0]["parts"][0]["text"])
                 for body in sent[before_b_send:]]
        finance_wires = [user for user in users if "UNTRUSTED_ADVICE" in user]
        assert len(finance_wires) == 1
        user = finance_wires[0]
        memory = user["UNTRUSTED_ADVICE"]["memory"]
        assert tuple(item["ref"] for item in memory["lessons"]) == selected
        assert bool(memory["advice_text"]) is (b_kind == "SOURCE_B")
        assert exact_bytes_digest(memory["advice_text"].encode("utf-8")) == manifest.advice_bytes_digest
        assert len(memory["advice_text"].encode("utf-8")) == manifest.advice_byte_count
        assert selection.manifest_digest == manifest.digest
        assert tuple(item["content_digest"] for item in memory["lessons"]) == tuple(
            entry.content_digest for entry in manifest.selected_lessons
        )
        use = target.store.load_artifact(use_ref(selection), USE_MEDIA).payload
        assert use["execution_mode"] == "ADOPTED"
        assert use["target_writes"] == 0
        assert use["finance_request_digest"] == selection.finance_request_digest
        assert target.current_quote().payload["currency"] == "USD"
        if failure_stage == "B_WITNESS_REPLACED_AFTER_PREVIEW":
            rows[B_RECORD] = {**rows[B_RECORD], "@odata.etag": 'W/"source-b2"'}
            page.update(records=(rows[B_RECORD],), cursor="b2")
            with principal("operator"):
                assert synchronizer.sync_page()["records_admitted"] == 2
                coverage = source_coverage(target, config)
                assert coverage["status"] == "COMPLETE"
                assert coverage["records"][A_RECORD]["revision"] == 'W/"source-a1"'
            before_retry = len(sent)
            with principal("currency"), pytest.raises(ReadDependencyError):
                adopted_adapter_for_preview(
                    target, event_id=event_id, bundle=bundle,
                    base_adapter=target.advisory_factory, action="approve",
                )
            with _as(target, "actor:finance-operator", "operator"), pytest.raises(ReadDependencyError):
                target.preview_change(event_id)
            with principal("currency"), pytest.raises(ReadDependencyError):
                target.approve_change(
                    event_id, actor_id=event_b.owner_id,
                    preview_digest=bundle.preview.digest,
                )
            assert len(sent) == before_retry
            assert target._approval_record(event_id) is None
            assert target.current_quote().payload["currency"] == "USD"
        if failure_stage == "A_REVISION_REPLACED_AFTER_PREVIEW":
            rows[A_RECORD] = {**rows[A_RECORD], "@odata.etag": 'W/"source-a2"'}
            page.update(records=(rows[A_RECORD],), cursor="a2")
            with principal("operator"):
                assert synchronizer.sync_page()["records_admitted"] == 2
                coverage = source_coverage(target, config)
                assert coverage["status"] == "COMPLETE"
                assert coverage["records"][B_RECORD]["revision"] == 'W/"source-b2"'
            with principal("currency"), pytest.raises(IntegrityError, match="EXPERIENCE_MEMORY_HOLD"):
                adopted_adapter_for_preview(
                    target, event_id=event_id, bundle=bundle,
                    base_adapter=target.advisory_factory, action="approve",
                )
            assert target._approval_record(event_id) is None
            assert target.current_quote().payload["currency"] == "USD"
