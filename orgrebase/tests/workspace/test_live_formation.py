from __future__ import annotations

import json
from pathlib import Path

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import EvidenceClass, IntegrityError
from orgrebase.workspace.execution import StaticClock
from orgrebase.workspace.live_formation import (
    LIVE_VERIFICATION_MEDIA_TYPE,
    WorkspaceLiveFormationService,
)
from orgrebase.workspace.models import (
    ClaimCandidate,
    DomainCandidateBundle,
    TaskContextManifest,
    WorkTrace,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE_LOCK = json.loads(
    (ROOT / "agentteams/workspace/source-lock.json").read_text(encoding="utf-8")
)


def _live_service(workspace_service, value: str = "2026-08-24T12:00:00Z"):
    return WorkspaceLiveFormationService(
        workspace_service.formation,
        clock=StaticClock(value),
    )


def _live_candidates(workspace_service, prepared):
    local_candidates, local_bundles = (
        workspace_service.formation.domain_registry.execute_selected(
            task=prepared.request,
            template=prepared.template,
            plan=prepared.coalition,
            projections=prepared.source_projections,
            now=prepared.prepared_at,
        )
    )
    candidates = tuple(
        ClaimCandidate.model_validate(
            {
                **item.model_dump(mode="json", exclude={"digest"}),
                "fresh_until": prepared.expires_at,
            }
        )
        for item in local_candidates
    )
    return candidates, _bundles_for_candidates(
        prepared, candidates, bundle_ids={item.domain_id: item.id for item in local_bundles}
    )


def _bundles_for_candidates(prepared, candidates, *, bundle_ids=None):
    delegation_by_domain = {item.domain_id: item for item in prepared.delegations}
    bundles: list[DomainCandidateBundle] = []
    for domain, delegation in sorted(delegation_by_domain.items()):
        refs = tuple(
            item.digest
            for item in candidates
            if item.issuer_domain_id == domain
        )
        bundles.append(
            DomainCandidateBundle(
                id=(bundle_ids or {}).get(
                    domain,
                    f"domain-bundle:{prepared.request.id.split(':')[-1]}:{domain}@live",
                ),
                task_ref=prepared.request.id,
                template_ref=prepared.template.ref,
                coalition_plan_ref=prepared.coalition.id,
                domain_id=domain,
                worker_id=delegation.worker_id,
                delegation_task_ref=delegation.id,
                candidate_refs=refs,
                candidate_set_digest=sha256_digest(sorted(refs)),
                transport_mode="LIVE_AGENTTEAMS",
                evidence_class=EvidenceClass.LIVE_AGENTTEAMS,
            )
        )
    return tuple(bundles)


def _evidence(prepared, bundles):
    bundle_by_domain = {item.domain_id: item for item in bundles}
    room_id = "!workspace-live-test:example.invalid"
    workers = []
    events = []
    candidate_artifacts = []
    provider_calls = []
    for index, delegation in enumerate(prepared.delegations, start=1):
        bundle = bundle_by_domain[delegation.domain_id]
        matrix_id = f"@{delegation.worker_id}:example.invalid"
        provider_id = f"provider-test-{index}"
        workers.append(
            {
                "worker_id": delegation.worker_id,
                "domain_id": delegation.domain_id,
                "uid": f"worker-uid-{index}",
                "generation": 1,
                "matrix_user_id": matrix_id,
                "image_id": SOURCE_LOCK["runtime_images"]["worker"],
                "skill_digests": [SOURCE_LOCK["skill_digest"]],
                "workspace_digest": SOURCE_LOCK["workspace_contract_digest"],
            }
        )
        candidate_artifacts.append(
            {
                "worker_id": delegation.worker_id,
                "ref": bundle.id,
                "digest": bundle.digest,
                "bytes_digest": bundle.digest,
                "delegation_task_digest": delegation.digest,
                "candidate_only": True,
                "provider_request_id": provider_id,
                "run_id": prepared.run_envelope.run_id,
                "nonce": prepared.run_envelope.nonce,
            }
        )
        provider_calls.append(
            {
                "worker_id": delegation.worker_id,
                "provider_request_id": provider_id,
                "model": "test-only-structured-provider",
                "candidate_digest": bundle.digest,
                "run_id": prepared.run_envelope.run_id,
                "nonce": prepared.run_envelope.nonce,
            }
        )
        events.append(
            {
                "worker_id": delegation.worker_id,
                "sender": matrix_id,
                "membership": "join",
                "event_id": f"$event-test-{index}",
                "room_id": room_id,
                "run_id": prepared.run_envelope.run_id,
                "nonce": prepared.run_envelope.nonce,
                "delegation_task_digest": delegation.digest,
                "candidate_artifact_digest": bundle.digest,
            }
        )
    return {
        "schema_version": "orgrebase.workspace-agentteams-live.v1",
        "status": "LIVE_AGENTTEAMS",
        "evidence_class": "LIVE_AGENTTEAMS",
        "source_lock_digest": sha256_digest(SOURCE_LOCK),
        "run_envelope": prepared.run_envelope.model_dump(mode="json"),
        "coalition_plan": prepared.coalition.model_dump(mode="json"),
        "coalition_plan_digest": prepared.coalition.digest,
        "delegation_tasks": [
            item.model_dump(mode="json") for item in prepared.delegations
        ],
        "team": {
            "name": "orgrebase-workspace-test-team",
            "uid": "team-test-uid",
            "generation": 1,
            "room_id": room_id,
        },
        "workers": workers,
        "matrix_events": events,
        "candidate_artifacts": candidate_artifacts,
        "provider_calls": provider_calls,
        "target_writes": 0,
    }


def _prepared_live_run(workspace_service):
    live = _live_service(workspace_service)
    prepared = live.prepare(
        workspace_service.formation.default_request(), ttl_seconds=900
    )
    candidates, bundles = _live_candidates(workspace_service, prepared)
    return live, prepared, candidates, bundles, _evidence(prepared, bundles)


def test_prepare_freezes_boundary_without_any_store_write(workspace_service) -> None:
    before_events = workspace_service.store.event_records()
    before_artifacts = workspace_service.store.connection.execute(
        "SELECT COUNT(*) FROM artifacts"
    ).fetchone()[0]
    before_objects = workspace_service.store.connection.execute(
        "SELECT COUNT(*) FROM object_versions"
    ).fetchone()[0]

    prepared = _live_service(workspace_service).prepare(
        workspace_service.formation.default_request(), ttl_seconds=900
    )

    assert prepared.prepared_at == "2026-08-24T12:00:00Z"
    assert prepared.expires_at == "2026-08-24T12:15:00Z"
    assert prepared.target_writes == 0
    assert prepared.run_envelope.mode == "LIVE_AGENTTEAMS"
    assert prepared.run_envelope.evidence_class == EvidenceClass.NOT_RUN
    assert len(prepared.source_projections) == 4
    assert len(prepared.delegations) == 4
    assert all(item.deadline_at == prepared.expires_at for item in prepared.delegations)
    assert workspace_service.store.event_records() == before_events
    assert (
        workspace_service.store.connection.execute(
            "SELECT COUNT(*) FROM artifacts"
        ).fetchone()[0]
        == before_artifacts
    )
    assert (
        workspace_service.store.connection.execute(
            "SELECT COUNT(*) FROM object_versions"
        ).fetchone()[0]
        == before_objects
    )
    with pytest.raises(KeyError):
        workspace_service.current_quote()


def test_live_prepare_rejects_request_outside_profile_before_write(
    workspace_service,
) -> None:
    request = workspace_service.formation.default_request()
    payload = request.model_dump(mode="json", exclude={"digest"})
    payload["organization_id"] = "org:other"
    payload["actor_id"] = "employee:other-owner"
    changed = type(request).model_validate(payload)
    before_events = workspace_service.store.event_records()

    with pytest.raises(ValueError, match="UNSUPPORTED_SYNTHETIC_SCENARIO"):
        _live_service(workspace_service).prepare(changed, ttl_seconds=900)

    assert workspace_service.store.event_records() == before_events
    assert workspace_service.state()["quote"] is None


def test_resume_verifies_admits_and_atomically_forms_quote(workspace_service) -> None:
    live, prepared, candidates, bundles, evidence = _prepared_live_run(
        workspace_service
    )

    result = live.resume(
        prepared,
        evidence_payload=evidence,
        source_lock=SOURCE_LOCK,
        claim_candidates=candidates,
        bundles=bundles,
    )

    assert result.evidence_class == "LIVE_AGENTTEAMS"
    assert result.verification_receipt.candidate_target_writes == 0
    assert result.task_receipt.deliverable_ref == "work:quote_acme@v1"
    assert workspace_service.current_quote().payload["currency"] == "USD"
    verification = workspace_service.store.load_artifact(
        result.verification_receipt.id, LIVE_VERIFICATION_MEDIA_TYPE
    )
    assert (
        result.verification_receipt.model_validate(verification.payload).digest
        == result.verification_receipt.digest
    )
    trace = WorkTrace.model_validate(
        workspace_service.store.load_artifact(result.task_receipt.trace_ref).payload
    )
    context = TaskContextManifest.model_validate(
        workspace_service.store.load_artifact(
            result.task_receipt.context_manifest_ref
        ).payload
    )
    assert trace.run_id == prepared.run_envelope.run_id
    assert context.expires_at == prepared.expires_at
    last_event = workspace_service.store.connection.execute(
        "SELECT payload_json FROM domain_events ORDER BY sequence_no DESC LIMIT 1"
    ).fetchone()
    event_payload = json.loads(last_event["payload_json"])
    assert event_payload["execution_mode"] == "LIVE_AGENTTEAMS"
    assert event_payload["candidate_target_writes"] == 0
    repeated = live.resume(
        prepared,
        evidence_payload=evidence,
        source_lock=SOURCE_LOCK,
        claim_candidates=candidates,
        bundles=bundles,
    )
    assert repeated.task_receipt.digest == result.task_receipt.digest
    assert workspace_service.store.get_pointer("work:quote_acme")["revision"] == 1


def test_resume_without_complete_live_evidence_fails_closed(workspace_service) -> None:
    live = _live_service(workspace_service)
    prepared = live.prepare(
        workspace_service.formation.default_request(), ttl_seconds=900
    )

    with pytest.raises(IntegrityError, match="LIVE_AGENTTEAMS_SCHEMA_VERSION_MISMATCH"):
        live.resume(
            prepared,
            evidence_payload={},
            source_lock=SOURCE_LOCK,
            claim_candidates=(),
            bundles=(),
        )

    with pytest.raises(KeyError):
        workspace_service.current_quote()


def test_resume_rejects_expired_prepare_before_reading_evidence(workspace_service) -> None:
    live = _live_service(workspace_service)
    prepared = live.prepare(
        workspace_service.formation.default_request(), ttl_seconds=60
    )
    live.clock = StaticClock("2026-08-24T12:01:00Z")

    with pytest.raises(RuntimeError, match="LIVE_FORMATION_EXPIRED"):
        live.resume(
            prepared,
            evidence_payload={},
            source_lock=SOURCE_LOCK,
            claim_candidates=(),
            bundles=(),
        )

    with pytest.raises(KeyError):
        workspace_service.current_quote()


def test_prepare_does_not_fork_an_already_committed_formation(workspace_service) -> None:
    workspace_service.form_quote()

    with pytest.raises(RuntimeError, match="LIVE_FORMATION_ALREADY_COMMITTED"):
        _live_service(workspace_service).prepare(
            workspace_service.formation.default_request(), ttl_seconds=900
        )


def test_resume_rejects_transport_valid_but_unadmittable_candidate(
    workspace_service,
) -> None:
    live, prepared, candidates, _bundles, _evidence_payload = _prepared_live_run(
        workspace_service
    )
    changed: list[ClaimCandidate] = []
    for item in candidates:
        payload = item.model_dump(mode="json", exclude={"digest"})
        if item.predicate == "currency":
            payload["value_schema_ref"] = "schema:unrelated@v1"
        changed.append(ClaimCandidate.model_validate(payload))
    changed_candidates = tuple(changed)
    bundles = _bundles_for_candidates(prepared, changed_candidates)
    evidence = _evidence(prepared, bundles)

    with pytest.raises(RuntimeError, match="REQUIRED_ADMISSION_MISSING:currency"):
        live.resume(
            prepared,
            evidence_payload=evidence,
            source_lock=SOURCE_LOCK,
            claim_candidates=changed_candidates,
            bundles=bundles,
        )

    with pytest.raises(KeyError):
        workspace_service.current_quote()


def test_resume_rejects_candidate_freshness_beyond_prepare_ttl(
    workspace_service,
) -> None:
    live, prepared, candidates, _bundles, _evidence_payload = _prepared_live_run(
        workspace_service
    )
    changed_candidates = tuple(
        ClaimCandidate.model_validate(
            {
                **item.model_dump(mode="json", exclude={"digest"}),
                "fresh_until": "2026-08-24T12:16:00Z",
            }
        )
        for item in candidates
    )
    bundles = _bundles_for_candidates(prepared, changed_candidates)

    with pytest.raises(IntegrityError, match="LIVE_FORMATION_CANDIDATE_TTL_INVALID"):
        live.resume(
            prepared,
            evidence_payload=_evidence(prepared, bundles),
            source_lock=SOURCE_LOCK,
            claim_candidates=changed_candidates,
            bundles=bundles,
        )

    with pytest.raises(KeyError):
        workspace_service.current_quote()


def test_resume_commit_failure_rolls_back_live_and_business_artifacts(
    workspace_service, monkeypatch
) -> None:
    before_chain = workspace_service.store.verify_event_chain()
    live, prepared, candidates, bundles, evidence = _prepared_live_run(
        workspace_service
    )
    original = workspace_service.store.save_artifact
    calls = {"count": 0}

    def fail_during_commit(connection, artifact_id, media_type, payload):
        calls["count"] += 1
        if calls["count"] == 5:
            raise RuntimeError("INJECTED_LIVE_RESUME_FAILURE")
        return original(connection, artifact_id, media_type, payload)

    monkeypatch.setattr(
        workspace_service.store, "save_artifact", fail_during_commit
    )
    with pytest.raises(RuntimeError, match="INJECTED_LIVE_RESUME_FAILURE"):
        live.resume(
            prepared,
            evidence_payload=evidence,
            source_lock=SOURCE_LOCK,
            claim_candidates=candidates,
            bundles=bundles,
        )

    with pytest.raises(KeyError):
        workspace_service.current_quote()
    assert not workspace_service.store.artifact_exists(
        "live-formation-verification:quote_acme@v1"
    )
    assert workspace_service.store.verify_event_chain() == before_chain
