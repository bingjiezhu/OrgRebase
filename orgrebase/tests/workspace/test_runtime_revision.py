from __future__ import annotations

from types import SimpleNamespace

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.store import StateStore
from orgrebase.workspace import runtime_revision


def workspace(store):
    return SimpleNamespace(store=store, effective_workflow_run_id="run:paused",
                           profile_digest="sha256:" + "a" * 64)


def test_paused_work_continues_after_reopen_with_exact_runtime_and_blocks_changed_implementation(tmp_path, monkeypatch):
    path = tmp_path / "paused.sqlite"
    preview = "sha256:" + "b" * 64
    with StateStore(path) as store:
        session = workspace(store)
        with store.transaction() as connection:
            runtime_revision.bind_preview_runtime(session, connection, "proposal:one", preview)
    with StateStore(path) as store:
        session = workspace(store)
        assert runtime_revision.preview_runtime_status(session, "proposal:one", preview)["decision"] == "CONTINUE"
        before = store.audit_head()
        current = runtime_revision.current_revision()
        changed = {**current, "handler_contract": "future-incompatible-handler.v3"}
        changed["revision_digest"] = sha256_digest({key: value for key, value in changed.items() if key != "revision_digest"})
        monkeypatch.setattr(runtime_revision, "current_revision", lambda: changed)
        decision = runtime_revision.preview_runtime_status(session, "proposal:one", preview)
        assert decision["decision"] == "REPLAN_REQUIRED"
        assert decision["reason"] == "RUNTIME_IMPLEMENTATION_CHANGED"
        with pytest.raises(RuntimeError, match="WORKSPACE_RUNTIME_REPLAN_REQUIRED"):
            runtime_revision.require_preview_runtime(session, "proposal:one", preview)
        assert store.audit_head() == before


def test_old_pending_preview_without_runtime_binding_cannot_silently_reuse_approval():
    with StateStore() as store:
        decision = runtime_revision.preview_runtime_status(workspace(store), "old:proposal", "sha256:" + "b" * 64)
        assert decision["decision"] == "REPLAN_REQUIRED" and decision["reason"] == "RUNTIME_BINDING_MISSING"
        assert store.audit_head()["sequence_no"] == 0


def test_runtime_binding_is_exact_to_preview_run_and_profile():
    preview = "sha256:" + "b" * 64
    with StateStore() as store:
        session = workspace(store)
        with store.transaction() as connection:
            runtime_revision.bind_preview_runtime(session, connection, "proposal:one", preview)
        session.effective_workflow_run_id = "run:other"
        assert runtime_revision.preview_runtime_status(session, "proposal:one", preview)["reason"] == "RUNTIME_BINDING_SCOPE_CHANGED"
        assert runtime_revision.preview_runtime_status(session, "proposal:one", "sha256:" + "c" * 64)["reason"] == "RUNTIME_BINDING_MISSING"


def test_returned_runtime_descriptor_cannot_mutate_cached_revision():
    current = runtime_revision.current_revision()
    expected = current["revision_digest"]
    current["implementation"].clear()
    assert runtime_revision.current_revision()["revision_digest"] == expected
    assert runtime_revision.current_revision()["implementation"]


def test_selected_model_configuration_invalidates_pending_review():
    with StateStore() as store:
        session = workspace(store)
        session.advisory_factory = SimpleNamespace(configuration_binding={"model_id": "model:one"})
        with store.transaction() as connection:
            runtime_revision.bind_preview_runtime(session, connection, "proposal:one", "preview:one")
        session.advisory_factory.configuration_binding = {"model_id": "model:two"}
        assert runtime_revision.preview_runtime_status(session, "proposal:one", "preview:one")["decision"] == "REPLAN_REQUIRED"


def test_native_transport_dependency_is_bound():
    current = runtime_revision.current_revision()
    assert "httpx2" in current["dependencies"]
    assert "workspace/openai_responses.py" in current["implementation"]
    assert "workspace/preview_execution.py" in current["implementation"]


@pytest.mark.parametrize("module", ("effect_identity.py", "workspace/enterprise_binding.py"))
def test_effect_authority_and_target_identity_are_bound_to_approved_implementation(monkeypatch, module):
    original = runtime_revision.Path.read_bytes
    before = runtime_revision.effect_runtime_revision()
    target = runtime_revision.Path(runtime_revision.__file__).resolve().parents[1] / module

    def changed_identity(path):
        contents = original(path)
        return contents + b"\n# changed dispatch semantics\n" if path == target else contents

    monkeypatch.setattr(runtime_revision.Path, "read_bytes", changed_identity)
    after = runtime_revision.effect_runtime_revision()
    assert before["revision_digest"] != after["revision_digest"]
    assert before["implementation"][module] != after["implementation"][module]


@pytest.mark.parametrize("module", (
    "workspace/advisory.py", "workspace/templates.py", "workspace/model_observations.py", "workspace/source_bindings.py",
    "workspace/dataverse.py", "clock.py", "auth.py", "fixture.py", "skills.py", "change_projection.py",
    "workspace/model_budget.py", "workspace/openai_http_worker.py", "workspace/enterprise_binding.py",
    "workspace/owner_change.py", "workspace/source_worker.py", "local_storage.py",
    "workspace/change_agentteams.py", "workspace/native_taskflow.py", "agentteams_source.py", "workspace/pricing.py",
    "workspace/context.py", "workspace/rebuild.py",
))
def test_paused_approval_cannot_resume_after_execution_semantics_change(module, tmp_path, monkeypatch):
    path = tmp_path / "paused.sqlite"
    preview = "sha256:" + "b" * 64
    original = runtime_revision.Path.read_bytes
    target = runtime_revision.Path(runtime_revision.__file__).resolve().parents[1] / module
    runtime_revision._revision_bytes.cache_clear()
    try:
        with StateStore(path) as store:
            session = workspace(store)
            with store.transaction() as connection:
                runtime_revision.bind_preview_runtime(session, connection, "proposal:one", preview)
            before = store.audit_head()

        def upgraded_bytes(path):
            contents = original(path)
            return contents + b"\n# upgraded execution semantics\n" if path == target else contents

        monkeypatch.setattr(runtime_revision.Path, "read_bytes", upgraded_bytes)
        runtime_revision._revision_bytes.cache_clear()
        with StateStore(path) as store:
            session = workspace(store)
            decision = runtime_revision.preview_runtime_status(session, "proposal:one", preview)
            assert decision["decision"] == "REPLAN_REQUIRED"
            assert decision["reason"] == "RUNTIME_IMPLEMENTATION_CHANGED"
            with pytest.raises(RuntimeError, match="WORKSPACE_RUNTIME_REPLAN_REQUIRED"):
                runtime_revision.require_preview_runtime(session, "proposal:one", preview)
            assert store.audit_head() == before
    finally:
        runtime_revision._revision_bytes.cache_clear()
