"""Spec011 controlled regression for historical learning text read boundaries."""

from __future__ import annotations

import urllib.request
from contextlib import contextmanager
from copy import deepcopy

import pytest
from enterprise_pack_factory import make_enterprise_pack
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.digest import sha256_digest
from orgrebase.local_role_session import LOCAL_SESSION_COOKIE, LocalRoleSessionSettings
from orgrebase.runtime_config import DeploymentSettings
from orgrebase.workspace.change_proposals import change_detail
from orgrebase.workspace.experience_governance_operations import experience_lesson_detail
from orgrebase.workspace.learning_content_projection import (
    CONTENT_CLASSES,
    project_learning_content,
    safe_response_view_digest,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from tests.test_local_role_sessions import ORIGIN, actor, choose
from tests.workspace import test_finance_adoption_source_memory as source_memory
from tests.workspace.test_finance_adoption import _policy, _save_policy
from tests.workspace.test_finance_explanation_operations import _as, _fake_send


def _contains_text(value, text):
    if isinstance(value, str):
        return text in value
    if isinstance(value, dict):
        return any(_contains_text(item, text) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_text(item, text) for item in value)
    return False


def _safe_v4_advisory(view, event_id):
    handoffs = view["changes"][event_id]["preview"]["bundle"]["advisory"]["handoffs"]
    return next(
        item["payload"]["model_advisory"] for item in handoffs
        if item.get("payload", {}).get("model_advisory", {}).get("schema_version")
        == "orgrebase.learning-model-advisory-view.v1"
    )


def _revoke_after_surface(original, sessions, cookie, late_surface, flag):
    def wrapped(payload, *, surface):
        view = original(payload, surface=surface)
        if surface == late_surface and not flag["done"]:
            flag["done"] = True
            sessions.logout(cookie)
        return view

    return wrapped


@contextmanager
def _controlled_leaky_history(tmp_path, monkeypatch):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    with source_memory._source_workspace(tmp_path, runtime, "sqlite", None) as (
        target, principal, config, mappings,
    ):
        sent = []
        monkeypatch.setattr(urllib.request, "urlopen", _fake_send(sent, target))
        for phase in ("collector", "author", "evaluator", "reviewer", "corpus"):
            monkeypatch.setenv(f"ORGREBASE_EXPERIENCE_{phase.upper()}_ACTOR_ID", f"actor:{phase}")
        monkeypatch.setenv("ORGREBASE_EXPERIENCE_RUBRIC_VERSION", "finance-source-next-v1")
        event_a, synchronizer, _reader, rows, page = source_memory._source_sync(
            target, principal, config, mappings,
        )
        source_memory._publish_source_lesson(target, principal, event_a)
        rows[source_memory.B_RECORD] = {
            **rows[source_memory.B_RECORD],
            "@odata.etag": 'W/"source-b2"', source_memory.B_FIELD: "EUR",
        }
        page.update(records=(rows[source_memory.B_RECORD],), cursor="b2")
        with principal("operator"):
            synchronizer.sync_page()
        target.changes.refresh()
        event_b = next(event for event in target.changes.all() if event.slot_id == "currency")
        head, qualification_ref, qualification_digest, release_ref = source_memory._qualified_head(target)
        spec = target.change_spec(event_b.event_id)
        fixture = target._fixture_for_change(spec)
        change_set = target.change_builder.build(fixture=fixture, spec=spec)
        policy = _policy(
            target, head, source_memory.finance_cluster_digest(target, event_b.event_id, change_set),
        ).model_copy(update={
            "qualification_ref": qualification_ref,
            "qualification_digest": qualification_digest,
            "content_release_ref": release_ref,
        })
        _save_policy(tmp_path, policy, monkeypatch)
        target.finance_adoption_qualification_verifier = lambda *_args, **_kwargs: None
        with _as(target, "actor:finance-operator", "operator"):
            target.preview_change(event_b.event_id)
        rows[source_memory.A_RECORD] = {
            **rows[source_memory.A_RECORD], "@odata.etag": 'W/"source-a2"',
        }
        page.update(records=(rows[source_memory.A_RECORD],), cursor="a2")
        with principal("operator"):
            synchronizer.sync_page()
            views = {
                "state": target.state(),
                "history": target.completion_history(),
                "export": target.export_evidence(),
            }
            detail_view = change_detail(target, event_b.event_id)
            detail = experience_lesson_detail(target, "source-current-facts")
        preview = target._preview_record(event_b.event_id)
        request = next(
            row["payload"]["model_advisory"]["request"]
            for row in preview["bundle"]["advisory"]["handoffs"]
            if row.get("payload", {}).get("model_advisory", {}).get("request", {}).get("contract_version") == "4"
        )
        canary = request["advice_context"]["advice_text"]
        assert canary and len(canary.encode("utf-8")) == 314
        yield target, views, canary, detail, sent, event_b.event_id, detail_view


def test_generic_reads_do_not_return_restricted_lesson_text(
    tmp_path, monkeypatch,
):
    with _controlled_leaky_history(tmp_path, monkeypatch) as (
        _target, views, canary, detail, _sent, _event_id, detail_view,
    ):
        assert detail["status"] == "HOLD" and detail["content_bytes_disclosed"] == 0
        leaked = {name: _contains_text(view, canary) for name, view in views.items()}
        assert leaked == {"state": False, "history": False, "export": False}
        assert not _contains_text(detail_view, canary)
        assert detail_view["learning_content_view"]["surface"] == "api"


def test_default_safe_read_model_removes_v4_text_from_all_three_surfaces(
    tmp_path, monkeypatch,
):
    with _controlled_leaky_history(tmp_path, monkeypatch) as (
        target, views, canary, detail, sent, event_id, _detail_view,
    ):
        assert detail["status"] == "HOLD" and detail["content_bytes_disclosed"] == 0
        assert {name: _contains_text(view, canary) for name, view in views.items()} == {
            "state": False, "history": False, "export": False,
        }
        source_digest = sha256_digest(views)
        before = target.store.audit_head()
        for surface, raw in views.items():
            safe = raw
            marker = safe["learning_content_view"]
            assert marker["redacted_count"] > 0
            assert "REVIEWED_LIMITED_LESSON" in marker["content_classes"]
            assert marker["content_status"] == "METADATA_ONLY_NO_HISTORICAL_CONTENT_GRANT"
            assert marker["storage_status"] == "LEGACY_CANONICAL_INLINE_MAY_REMAIN"
            assert not _contains_text(safe, canary)
            preview = safe["changes"][event_id]["preview"]
            projected_advisories = [
                item["payload"]["model_advisory"]
                for item in preview["bundle"]["advisory"]["handoffs"]
                if item.get("payload", {}).get("model_advisory", {}).get("schema_version")
                == "orgrebase.learning-model-advisory-view.v1"
            ]
            assert len(projected_advisories) == 1
            context = projected_advisories[0]["request"]["advice_context"]
            assert projected_advisories[0]["request"]["original_object_readable_as_request"] is False
            assert context["advice_byte_count"] == len(canary.encode("utf-8"))
            assert context["advice_bytes_digest"].startswith("sha256:")
            assert len(context["lessons"]) == 1
            assert "advice_text" not in context
            if surface == "export":
                assert safe["digest"] == sha256_digest({
                    key: value for key, value in safe.items() if key != "digest"
                })
        assert target.store.audit_head() == before
        assert sha256_digest(views) == source_digest
        assert sent  # The projection itself dispatches no additional model request.


def test_real_http_state_change_detail_and_export_do_not_disclose_or_write(
    tmp_path, monkeypatch,
):
    with _controlled_leaky_history(tmp_path, monkeypatch) as (
        target, views, canary, detail, sent, event_id, _detail_view,
    ):
        assert detail["status"] == "HOLD"
        settings = DeploymentSettings(
            mode="local", local_role_session=LocalRoleSessionSettings(ORIGIN),
        )
        app = create_app(workspace_service=target, deployment_settings=settings)
        paths = (
            ("/api/workspace/state", "api"),
            (f"/api/workspace/changes/{event_id}", "api"),
            ("/api/workspace/export/evidence", "export"),
        )
        source_advisory = _safe_v4_advisory(views["state"], event_id)
        expected_request_digest = source_advisory["request"]["logical_request_digest"]
        expected_receipt_digest = source_advisory["receipt"]["logical_receipt_digest"]
        assert expected_request_digest and expected_receipt_digest
        with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 42160)) as client:
            assert all(client.get(path).status_code == 401 for path, _ in paths)
            initial = client.get("/api/session").json()
            choose(client, actor(initial, "operator"))
            before = target.store.audit_head()
            sent_before = len(sent)
            for path, surface in paths:
                response = client.get(path)
                assert response.status_code == 200, (path, response.text)
                body = response.json()
                assert not _contains_text(body, canary)
                assert body["learning_content_view"]["surface"] == surface
                assert body["learning_content_view"]["redacted_count"] > 0
                assert body["learning_content_view"]["storage_status"] == "LEGACY_CANONICAL_INLINE_MAY_REMAIN"
                assert body["learning_content_view"]["view_digest"] == safe_response_view_digest(body)
                if path == "/api/workspace/state":
                    http_advisory = _safe_v4_advisory(body, event_id)
                    assert http_advisory["request"]["logical_request_digest"] == expected_request_digest
                    assert http_advisory["receipt"]["logical_receipt_digest"] == expected_receipt_digest
                    assert http_advisory["receipt"]["request_digest"] == expected_request_digest
                if surface == "export":
                    assert body["digest"] == sha256_digest({
                        key: value for key, value in body.items() if key != "digest"
                    })
            assert target.store.audit_head() == before
            assert len(sent) == sent_before


def test_real_http_api_addition_is_screened_after_state_projection(tmp_path, monkeypatch):
    with _controlled_leaky_history(tmp_path, monkeypatch) as (
        target, views, canary, _detail, sent, event_id, _detail_view,
    ):
        from orgrebase import api as api_module

        added_secret = "api-added-private-guidance"
        monkeypatch.setattr(
            api_module, "_same_run_candidate_output_view",
            lambda *_args: {"new_output": {"advice_text": added_secret}},
        )
        app = create_app(
            workspace_service=target,
            deployment_settings=DeploymentSettings(
                mode="local", local_role_session=LocalRoleSessionSettings(ORIGIN),
            ),
        )
        before = target.store.audit_head()
        sent_before = len(sent)
        with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 42161)) as client:
            initial = client.get("/api/session").json()
            choose(client, actor(initial, "operator"))
            response = client.get("/api/workspace/state")
            assert response.status_code == 200, response.text
            body = response.json()
        assert not _contains_text(body, added_secret)
        assert not _contains_text(body, canary)
        assert body["agent_candidate_outputs"]["new_output"]["advice_text"]["content_status"] == (
            "UNKNOWN_CLASS_FAIL_CLOSED"
        )
        assert _safe_v4_advisory(body, event_id) == _safe_v4_advisory(views["state"], event_id)
        assert body["learning_content_view"]["view_digest"] == safe_response_view_digest(body)
        assert target.store.audit_head() == before
        assert len(sent) == sent_before


def test_real_http_late_session_revocation_rejects_built_views(
    tmp_path, monkeypatch,
):
    with _controlled_leaky_history(tmp_path, monkeypatch) as (
        target, _views, canary, detail, sent, event_id, _detail_view,
    ):
        assert detail["status"] == "HOLD"
        settings = DeploymentSettings(
            mode="local", local_role_session=LocalRoleSessionSettings(ORIGIN),
        )
        app = create_app(workspace_service=target, deployment_settings=settings)
        original_projection = WorkspaceService._learning_history_view
        cases = (
            ("/api/workspace/state", "api"),
            (f"/api/workspace/changes/{event_id}", "api"),
            ("/api/workspace/export/evidence", "export"),
        )
        before = target.store.audit_head()
        sent_before = len(sent)
        for index, (path, late_surface) in enumerate(cases):
            with TestClient(
                app, base_url=ORIGIN, client=("127.0.0.1", 42170 + index),
            ) as client:
                initial = client.get("/api/session").json()
                choose(client, actor(initial, "operator"))
                cookie = client.cookies.get(LOCAL_SESSION_COOKIE)
                assert cookie
                revoked = {"done": False}
                with monkeypatch.context() as patch:
                    patch.setattr(
                        WorkspaceService, "_learning_history_view",
                        staticmethod(_revoke_after_surface(
                            original_projection, app.state.local_role_sessions,
                            cookie, late_surface, revoked,
                        )),
                    )
                    denied = client.get(path)
                assert revoked["done"], path
                assert denied.status_code in {401, 403}, (path, denied.status_code, denied.text)
                assert not _contains_text(denied.json(), canary)
        assert target.store.audit_head() == before
        assert len(sent) == sent_before


def test_unknown_nested_wire_error_and_content_are_metadata_only():
    canary = "controlled-private-advice-text"
    raw = {
        "nested": {
            "failure": {"error_message": canary},
            "request_json": '{"untrusted_advice":"' + canary + '"}',
            "finance_wire_body": {
                "systemInstruction": {"parts": [{"text": canary}]},
                "contents": [{"parts": [{"text": canary}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
            "raw_wire_body": {"contents": [{"parts": [{"text": canary}]}]},
            "model_advisory": {
                "request": {"contract_version": "future-v5", "advice_context": {"advice_text": canary}},
                "receipt": {"value": {"explanation": canary}},
            },
            "unreviewed": {
                "schema_version": "orgrebase.lesson-body.v1",
                "kind": "PROCEDURAL_ADVICE", "problem_code": "SOURCE_REVIEW",
                "steps": [canary], "digest": sha256_digest(canary),
            },
        },
    }
    projected = project_learning_content(raw, surface="other")
    safe = projected.as_response()
    assert not _contains_text(safe, canary)
    assert "UNKNOWN_LEARNING_TEXT" in projected.content_classes
    assert "MODEL_WIRE_COPY" in projected.content_classes
    assert "REVIEWED_LIMITED_LESSON" in projected.content_classes
    assert safe["payload"]["nested"]["model_advisory"]["content_status"] == "UNKNOWN_CLASS_FAIL_CLOSED"
    assert safe["payload"]["nested"]["finance_wire_body"]["content_status"] == "NOT_AUTHORIZED_FOR_GENERIC_HISTORY"
    assert safe["payload"]["nested"]["raw_wire_body"]["content_status"] == "NOT_AUTHORIZED_FOR_GENERIC_HISTORY"
    assert CONTENT_CLASSES["PUBLIC_REVIEWED_SKILL_RESOURCE"]["generic_history"] == (
        "METADATA_ONLY_UNTIL_RELEASE_SCOPE_VERIFIED"
    )


def test_unverified_skill_bundle_is_not_assumed_public_and_unrelated_reviewer_stays_intact():
    raw = {
        "bundle": {
            "schema_version": "orgrebase.skill-content-bundle.v2",
            "profile_id": "workspace-change-explanation-v1",
            "consumer_id": "workspace-change-advisory@4.0.0",
            "digest": sha256_digest("controlled-bundle"),
            "resources": [{
                "path": "instructions/change-explanation.md",
                "sha256": sha256_digest("controlled-instruction"),
                "content_base64": "Y29udHJvbGxlZC1pbnN0cnVjdGlvbg==",
                "size_bytes": 22,
            }],
        },
        "model_advisory": {
            "verdict": "REVIEW", "reason_code": "OWNER_REVIEW_REQUIRED",
        },
    }
    projected = project_learning_content(raw, surface="other")
    assert projected.payload["bundle"]["content_status"] == (
        "PUBLICATION_SCOPE_NOT_VERIFIED_FOR_GENERIC_HISTORY"
    )
    assert "content_base64" not in projected.payload["bundle"]
    assert projected.payload["model_advisory"] == raw["model_advisory"]


def test_projection_cannot_claim_old_view_digest_after_later_mutation():
    projected = project_learning_content({"advice_text": "controlled-only"}, surface="other")
    projected.payload["advice_text"]["content_status"] = "MISLABELED"
    with pytest.raises(ValueError, match="LEARNING_PROJECTION_MUTATED_AFTER_BUILD"):
        projected.as_response()


def test_safe_v4_views_keep_logical_identity_when_projected_twice():
    canary = "private-advice-body"
    request_digest = sha256_digest({"logical": "request"})
    receipt_digest = sha256_digest({"logical": "receipt"})
    raw = {
        "model_advisory": {
            "request": {
                "contract_version": "4",
                "request_id": "request-1",
                "digest": request_digest,
                "advice_context": {
                    "advice_text": canary,
                    "advice_bytes_digest": sha256_digest(canary),
                    "advice_byte_count": len(canary.encode()),
                    "lessons": [],
                },
            },
            "receipt": {
                "contract_version": "4",
                "request_digest": request_digest,
                "digest": receipt_digest,
                "status": "VALID",
                "output_digest": sha256_digest({"output": 1}),
            },
        },
    }
    first = project_learning_content(raw, surface="state").payload
    second = project_learning_content(first, surface="api").payload
    assert second == first
    assert not _contains_text(second, canary)
    view = second["model_advisory"]
    assert view["request"]["logical_request_digest"] == request_digest
    assert view["receipt"]["logical_receipt_digest"] == receipt_digest
    assert view["receipt"]["request_digest"] == request_digest


def test_safe_view_does_not_trust_new_unknown_nested_content():
    canary = "new-private-api-content"
    first = project_learning_content({
        "model_advisory": {
            "request": {"contract_version": "4", "digest": sha256_digest("request"),
                        "advice_context": {"advice_text": "initial-private", "lessons": []}},
            "receipt": {"contract_version": "4", "digest": sha256_digest("receipt"),
                        "request_digest": sha256_digest("request")},
        },
    }, surface="state").payload
    first["model_advisory"]["request"]["unexpected"] = {"advice_text": canary}
    second = project_learning_content(first, surface="api")
    assert second.redacted_paths
    assert not _contains_text(second.payload, canary)
    assert second.payload["model_advisory"]["request"]["logical_request_digest"] == sha256_digest("request")


def test_response_marker_digest_is_recomputable_and_export_digest_is_final():
    digest = sha256_digest("logical-export")
    raw = {
        "schema_version": "orgrebase.workspace-evidence-export.v1",
        "model_advisory": {
            "request": {"contract_version": "4", "digest": sha256_digest("request"),
                        "advice_context": {"advice_text": "private-advice", "lessons": []}},
            "receipt": {"contract_version": "4", "digest": sha256_digest("receipt"),
                        "request_digest": sha256_digest("request")},
        },
        "digest": digest,
    }
    safe = WorkspaceService._learning_history_view(raw, surface="export")
    marker = safe["learning_content_view"]
    assert marker["original_logical_digest"] == digest
    assert marker["view_digest_scope"] == "EXCLUDES_TOP_LEVEL_DIGEST_AND_MARKER_VIEW_DIGEST"
    view_without_digests = {key: value for key, value in safe.items() if key != "digest"}
    view_without_digests["learning_content_view"] = {
        key: value for key, value in marker.items() if key != "view_digest"
    }
    assert marker["view_digest"] == sha256_digest(view_without_digests)
    assert safe["digest"] == sha256_digest({key: value for key, value in safe.items() if key != "digest"})
    assert safe["digest"] != marker["view_digest"] != digest


def _safe_view_attack_samples():
    original = "original-private-lesson-body"
    request_digest = sha256_digest({"request": "logical"})
    receipt_digest = sha256_digest({"receipt": "logical"})
    context = {
        "head_ref": "skill-head:finance",
        "head_digest": sha256_digest("head"),
        "advice_text": original,
        "advice_bytes_digest": sha256_digest(original),
        "advice_byte_count": len(original.encode()),
        "lessons": [{
            "ref": "lesson:source-a", "revision": 2,
            "content_digest": sha256_digest(original),
        }],
    }
    request = {
        "contract_version": "4", "request_id": "request:one",
        "digest": request_digest, "advice_context": context,
    }
    receipt = {
        "contract_version": "4", "digest": receipt_digest,
        "request_digest": request_digest, "status": "VALID",
        "output_digest": sha256_digest("model-output"),
        "raw_response": original,
    }
    return (
        ("advice_context", context, (("head_ref",), ("head_digest",),
                                     ("lessons", 0, "content_digest"))),
        ("request", request, (("logical_request_digest",), ("request_ref",),
                               ("advice_context", "advice_bytes_digest"))),
        ("receipt", receipt, (("logical_receipt_digest",), ("request_digest",),
                               ("output_digest",))),
        ("model_advisory", {"request": request, "receipt": receipt},
         (("request", "logical_request_digest"), ("receipt", "logical_receipt_digest"),
          ("receipt", "request_digest"))),
        ("finance_wire_body", {"systemInstruction": {"parts": [{"text": original}]},
                               "contents": [{"parts": [{"text": original}]}]},
         (("body_digest",),)),
        ("bundle", {"schema_version": "orgrebase.skill-content-bundle.v2",
                    "profile_id": "finance", "digest": sha256_digest("bundle"),
                    "resources": [{"path": "instructions/finance.md",
                                   "sha256": sha256_digest(original),
                                   "content_base64": original, "size_bytes": len(original)}]},
         (("bundle_digest",), ("resources", 0, "content_digest"))),
        ("lesson_body", {"schema_version": "orgrebase.lesson-body.v1",
                         "digest": sha256_digest(original), "kind": "PROCEDURAL_ADVICE",
                         "problem_code": "SOURCE_REVIEW", "steps": [original]},
         (("body_digest",),)),
    )


def _at(value, path):
    for part in path:
        value = value[part]
    return value


@pytest.mark.parametrize("field,raw,identity_paths", _safe_view_attack_samples())
def test_second_projection_rejects_unknown_fields_inside_each_safe_learning_view(
    field, raw, identity_paths,
):
    injected = "second-read-private-extension-value"
    first = project_learning_content({field: raw}, surface="state").payload[field]
    assert not _contains_text(first, "original-private-lesson-body")
    tainted = deepcopy(first)
    tainted["future_extension"] = {"opaque": [injected]}
    if field in {"advice_context", "request", "model_advisory"}:
        context = tainted if field == "advice_context" else (
            tainted["advice_context"] if field == "request"
            else tainted["request"]["advice_context"]
        )
        context["lessons"][0]["future_note"] = injected
    if field == "model_advisory":
        tainted["request"]["future_prompt"] = injected
        tainted["receipt"]["future_output"] = injected
    if field == "bundle":
        tainted["resources"][0]["future_resource_bytes"] = injected
    projection = project_learning_content({field: tainted}, surface="api")
    response = projection.as_response()
    second = response["payload"][field]
    assert not _contains_text(response, injected)
    assert projection.redacted_paths
    assert "UNKNOWN_LEARNING_TEXT" in projection.content_classes
    assert second["future_extension"]["content_status"] == "UNKNOWN_CLASS_FAIL_CLOSED"
    for path in identity_paths:
        assert _at(second, path) == _at(first, path)
    if field in {"advice_context", "request", "model_advisory"}:
        context = second if field == "advice_context" else (
            second["advice_context"] if field == "request"
            else second["request"]["advice_context"]
        )
        assert "future_note" not in context["lessons"][0]
    if field == "bundle":
        assert "future_resource_bytes" not in second["resources"][0]


def test_second_export_projection_recomputes_both_digests_after_unknown_content():
    request_digest = sha256_digest("logical-request")
    receipt_digest = sha256_digest("logical-receipt")
    logical_export_digest = sha256_digest("original-logical-export")
    original = {
        "schema_version": "orgrebase.workspace-evidence-export.v1",
        "digest": logical_export_digest,
        "model_advisory": {
            "request": {"contract_version": "4", "request_id": "request:one",
                        "digest": request_digest,
                        "advice_context": {"advice_text": "private-original", "lessons": []}},
            "receipt": {"contract_version": "4", "digest": receipt_digest,
                        "request_digest": request_digest, "status": "VALID"},
        },
    }
    first = WorkspaceService._learning_history_view(original, surface="export")
    tainted = deepcopy(first)
    tainted["model_advisory"]["request"]["future_prompt"] = "private-added-after-export"
    tainted["model_advisory"]["receipt"]["future_output"] = "private-added-after-export"
    second = WorkspaceService._learning_history_view(tainted, surface="export")
    assert not _contains_text(second, "private-added-after-export")
    advisory = second["model_advisory"]
    assert advisory["request"]["logical_request_digest"] == request_digest
    assert advisory["receipt"]["logical_receipt_digest"] == receipt_digest
    assert advisory["receipt"]["request_digest"] == request_digest
    assert second["learning_content_view"]["original_logical_digest"] == logical_export_digest
    assert second["learning_content_view"]["view_digest"] == safe_response_view_digest(second)
    assert second["digest"] == sha256_digest({
        key: value for key, value in second.items() if key != "digest"
    })
    assert second["digest"] != first["digest"]
