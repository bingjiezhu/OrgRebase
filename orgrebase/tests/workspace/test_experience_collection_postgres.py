"""Real PostgreSQL fence and restart checks for the default-off collector."""

from __future__ import annotations

import time

import pytest

from orgrebase.auth import request_principal
from orgrebase.digest import sha256_digest
from orgrebase.store import StateStore
from orgrebase.workspace.experience_collection import COLLECTION_MEDIA, ExperienceCollector
from tests.postgres_support import postgres_dsn as postgres_dsn
from tests.workspace.test_experience_collection import _principal, _workspace


def test_postgres_collector_two_workers_and_restart_preserve_one_case(postgres_dsn):
    first, event, request, _ = _workspace(postgres_dsn)
    second, _, _, _ = _workspace(postgres_dsn)
    try:
        first.store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
            "event_id": event.event_id, "round": 1,
            "recovery_digest": request["digest"], "actor_id": "owner:product",
        })
        left = ExperienceCollector(first, collector_actor_id="actor:collector", enabled=True)
        right = ExperienceCollector(second, collector_actor_id="actor:collector", enabled=True)
        token = request_principal.set(_principal())
        try:
            claim = left._claim("worker:left", time.time())
            assert claim is not None
            assert right.collect(worker_id="worker:right").status == "BUSY"
            left._release("worker:left", claim["fence"])
            result = right.collect(worker_id="worker:right")
            assert result.observed == 1 and result.cursor_sequence == 1
            assert left.collect(worker_id="worker:left").status == "IDLE"
            rows = second.store.list_artifacts(
                artifact_id_prefix="experience-collection:", expected_media_type=COLLECTION_MEDIA,
            )
            assert len(rows) == 1 and rows[0].payload["status"] == "OBSERVED"
        finally:
            request_principal.reset(token)
    finally:
        first.store.close()
        second.store.close()
    reopened, _, _, _ = _workspace(postgres_dsn)
    try:
        token = request_principal.set(_principal())
        try:
            collector = ExperienceCollector(reopened, collector_actor_id="actor:collector", enabled=True)
            assert collector.collect(worker_id="worker:restart").status == "IDLE"
            assert collector.collect(worker_id="worker:restart").cursor_sequence == 1
        finally:
            request_principal.reset(token)
    finally:
        reopened.store.close()


def test_postgres_workspace_scope_does_not_expose_collected_case(postgres_dsn):
    workspace, event, request, _ = _workspace(postgres_dsn)
    try:
        workspace.store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
            "event_id": event.event_id, "round": 1,
            "recovery_digest": request["digest"], "actor_id": "owner:product",
        })
        token = request_principal.set(_principal())
        try:
            ExperienceCollector(
                workspace, collector_actor_id="actor:collector", enabled=True,
            ).collect(worker_id="worker:default")
        finally:
            request_principal.reset(token)
        case_ref = workspace.store.list_artifacts(
            artifact_id_prefix="experience-collection:", expected_media_type=COLLECTION_MEDIA,
        )[0].payload["case_ref"]
        workspace.store.register_workspace(
            "other", profile_digest=sha256_digest("other-profile"),
            pack_digest=None, quote_object_id="work:quote_other",
            created_at="2026-09-28T00:00:00Z",
        )
        with StateStore(postgres_dsn, tenant_id="org:test", workspace_id="other") as isolated:
            with pytest.raises(KeyError):
                isolated.load_artifact(case_ref)
            assert isolated.event_page()["items"] == ()
            assert isolated.current_object_page(object_id_prefix="experience-lesson:")["items"] == ()
    finally:
        workspace.store.close()
