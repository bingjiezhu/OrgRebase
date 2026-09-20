"""Exclusion evidence records actual selection decisions, including after upgrades."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from orgrebase.domain import IntegrityError
from orgrebase.workspace.context import TaskContextCompiler
from orgrebase.workspace.formation import WorkspaceFormationService
from orgrebase.workspace.models import ActorContextProjection
from orgrebase.workspace.rebuild import WorkspaceRebuildContextProvider
from orgrebase.workspace.service import WorkspaceService

PROJECTION_MEDIA = "application/vnd.orgrebase.actor-context-projection+json"
OLD_EXCLUSIONS = (
    {"object_ref": "source:legal.customer-contract@v4", "reason": "MINIMAL_DISCLOSURE_DERIVATION_ONLY"},
    {"object_ref": "policy:finance.internal_cost_floor@v1", "reason": "FORBIDDEN_OUTPUT_FIELD"},
)


def historical_compile(original):
    """Model the recorded pre-upgrade format only to exercise migration boundaries."""
    def compile(self, **kwargs):
        manifest, projections = original(self, **kwargs)
        historical = []
        for projection in projections:
            if projection.actor_id == "workspace-renderer":
                body = projection.model_dump(mode="json", exclude={"digest"})
                body["excluded"] = OLD_EXCLUSIONS
                projection = ActorContextProjection.model_validate(body)
            historical.append(projection)
        return manifest, tuple(historical)
    return compile


@pytest.mark.parametrize("organization", ("org:example-one", "org:example-two"))
@pytest.mark.parametrize("example_objects_present", (False, True))
def test_compiler_does_not_invent_exclusions_or_scan_unselected_sources(
    workspace_service, organization, example_objects_present,
):
    original = TaskContextCompiler.compile
    observed = []

    def compile(self, **kwargs):
        task = kwargs["task"].model_dump(mode="json", exclude={"digest"})
        task["organization_id"] = organization
        kwargs["task"] = type(kwargs["task"]).model_validate(task)
        original_reader = self.object_reader
        unused = {entry["object_ref"]: object() for entry in OLD_EXCLUSIONS} if example_objects_present else {}

        def read(object_id, version=None):
            ref = f"{object_id}@{version}"
            if ref in {entry["object_ref"] for entry in OLD_EXCLUSIONS}:
                observed.append(ref)
                return unused[ref]
            return original_reader(object_id, version)

        compiler = TaskContextCompiler(read, read_dependency_validator=self.read_dependency_validator)
        result = original(compiler, **kwargs)
        manifest, projections = result
        renderer = next(item for item in projections if item.actor_id == "workspace-renderer")
        assert renderer.excluded == ()
        included = {binding.object_ref for binding in manifest.slot_bindings}
        for projection in projections:
            if projection.actor_id == "workspace-renderer":
                continue
            assert {item["object_ref"] for item in projection.excluded} == included - set(projection.included_refs)
            assert {item["reason"] for item in projection.excluded} == {"OTHER_AUTHORITY_DOMAIN"}
        return result

    # Prepare only: the altered organization is a compiler isolation test, not an admission bypass.
    with patch.object(TaskContextCompiler, "compile", compile):
        workspace_service.formation.prepare_quote(WorkspaceFormationService.default_request())
    assert observed == []


def test_rebase_context_and_successor_both_have_no_fabricated_exclusions(
    formed_service, apply_staged_change,
):
    seen = []
    original = WorkspaceRebuildContextProvider.prepare

    def prepare(self, **kwargs):
        contexts = original(self, **kwargs)
        assert contexts
        assert all(context.excluded == () for context in contexts)
        seen.extend(contexts)
        return contexts

    with patch.object(WorkspaceRebuildContextProvider, "prepare", prepare):
        result = apply_staged_change(formed_service, "currency")
    assert result["quote"].version == "v2"
    assert seen
    projections = formed_service.store.list_artifacts(
        artifact_id_prefix="actor-context:", expected_media_type=PROJECTION_MEDIA,
    )
    renderers = [item.payload for item in projections if item.payload["actor_id"] == "workspace-renderer"]
    assert {item["version"] for item in renderers} == {"v1", "v2"}
    assert all(item["excluded"] == [] for item in renderers)


def test_old_uncommitted_formation_requires_prepare_again(workspace_service):
    original = TaskContextCompiler.compile
    with patch.object(TaskContextCompiler, "compile", historical_compile(original)):
        prepared = workspace_service.formation.prepare_quote(WorkspaceFormationService.default_request())
    chain = workspace_service.store.verify_event_chain()
    with pytest.raises(IntegrityError, match="ACTOR_PROJECTION_SET_MISMATCH"):
        workspace_service.formation.commit_quote(prepared)
    assert workspace_service.state()["quote"] is None
    assert workspace_service.store.verify_event_chain() == chain
    workspace_service.form_quote()
    assert workspace_service.current_quote().version == "v1"


def test_committed_historical_context_remains_readable_without_recompilation(tmp_path):
    path = tmp_path / "historical.sqlite"
    service = WorkspaceService(store_path=path)
    try:
        with patch.object(TaskContextCompiler, "compile", historical_compile(TaskContextCompiler.compile)):
            service.form_quote()
        historical = service.store.load_artifact("actor-context:quote_acme:renderer@v1", PROJECTION_MEDIA)
        quote = service.export_quote()["quote"]
        chain = service.store.verify_event_chain()
    finally:
        service.close()
    reopened = WorkspaceService.reopen(path)
    try:
        with patch.object(TaskContextCompiler, "compile", side_effect=AssertionError("history must not recompile")):
            assert reopened.state()["quote"] == quote
            assert reopened.export_quote()["quote"] == quote
            assert reopened.export_evidence()["quote"] == quote
        assert reopened.store.load_artifact(historical.artifact_id, PROJECTION_MEDIA) == historical
        assert reopened.store.verify_event_chain() == chain
    finally:
        reopened.close()
