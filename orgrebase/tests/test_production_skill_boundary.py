"""Production Pack admission and application cannot promote fixture skills."""

from __future__ import annotations

import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from enterprise_pack_factory import make_enterprise_pack
from fastapi.testclient import TestClient
from test_identity_boundary import configure_keys, signed_token

from orgrebase.api import create_app
from orgrebase.auth import IdentitySettings
from orgrebase.clock import SystemClock
from orgrebase.domain import EffectDisposition, IntegrityError, VersionedObject
from orgrebase.runtime_config import DeploymentSettings, open_workspace
from orgrebase.workspace.models import ChangeEvent, WorkspacePreviewBundle
from orgrebase.workspace.pilot import EnterpriseQuotePilotPackError, load_enterprise_quote_pilot_pack
from orgrebase.workspace.pilot_authoring import seal_enterprise_quote_pilot_pack

pytest_plugins = ("test_postgres_store",)


def test_production_pack_cannot_admit_a_promotable_skill_target(tmp_path):
    make_enterprise_pack(tmp_path)
    dependency = tmp_path / "draft/components/dependency.json"
    document = json.loads(dependency.read_text())
    document["projection"]["targets"][0]["kind"] = "SkillContractVersion"
    dependency.write_text(json.dumps(document))
    with pytest.raises(EnterpriseQuotePilotPackError, match="PILOT_DEPENDENCY_PROJECTION_INVALID"):
        seal_enterprise_quote_pilot_pack(tmp_path / "draft", tmp_path / "invalid-pack")


@pytest.mark.parametrize("slot,value", [
    ("launch_date", "2030-07-01"),
    ("currency", "JPY"),
    ("product_plan", "Enterprise Premium"),
])
def test_production_changes_do_not_turn_local_qualification_into_skill_release(
    postgres_runtime, tmp_path, monkeypatch, slot, value,
):
    pack = make_enterprise_pack(tmp_path)
    runtime = load_enterprise_quote_pilot_pack(pack)
    postgres_dsn = postgres_runtime(tenant_id=runtime.profile.organization_id)["runtime_dsn"]
    membership = tmp_path / "membership.json"
    membership.write_text(json.dumps({
        "tenant_id": runtime.profile.organization_id,
        "members": [{"subject": "operator", "actor_id": runtime.profile.default_task.actor_id,
                     "roles": ["administrator"]}],
    }))
    identity = IdentitySettings("https://issuer.example", "orgrebase-api", "https://issuer.example/jwks",
                                runtime.profile.organization_id, membership)
    settings = DeploymentSettings(mode="production", identity=identity, database_url=postgres_dsn,
                                  enterprise_pack=str(pack), allowed_hosts=("localhost",))
    monkeypatch.setenv("ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED", "0")
    # Preview, approval, and application cross the real JWT HTTP boundary
    # with the current mapped owner.
    workspace = open_workspace(settings)
    try:
        assert isinstance(workspace.clock, SystemClock)
        assert workspace.store.check_health()["backend"] == "postgresql"
        assert not workspace.change_history()["items"]
        assert all(item.kind != "SkillContractVersion" for item in runtime.seed_objects)
        skill_sources = tuple(item for item in runtime.seed_objects if item.kind == "SkillReferenceVersion")
        assert skill_sources
        workspace.form_quote()
        skill_before = {item.id: workspace.store.get_object(item.id) for item in skill_sources}
        binding = next(item for item in workspace.enterprise_binding.resources if item.slot_id == slot)
        base = workspace.store.get_object(binding.object_id)
        proposal = VersionedObject.model_validate({
            **base.model_dump(mode="json"), "version": "v99", "state": "PROPOSED", "digest": "",
            "payload": {**base.payload, "canonical_value": value},
            "source_refs": ["test:observed-production-change"], "valid_from": workspace.clock.now(),
        })
        event = ChangeEvent(event_id="production-change", organization_id=runtime.profile.organization_id,
                            slot_id=slot, owner_id=binding.owner_id, base_version=base.version,
                            base_digest=base.digest, proposal=proposal, occurred_at=workspace.clock.now())
        forged = event.model_dump(mode="json")
        forged["digest"] = ""
        forged["proposal"].update(kind="SkillContractVersion", digest="")
        with pytest.raises(IntegrityError, match="CHANGE_EVENT_OBJECT_KIND_MISMATCH"):
            workspace.register_change(ChangeEvent.model_validate(forged))
        forged["slot_id"] = "quote_compose_skill"
        with pytest.raises(IntegrityError, match="CHANGE_EVENT_SLOT_UNSUPPORTED"):
            workspace.register_change(ChangeEvent.model_validate(forged))
        assert not workspace.change_history()["items"]
        with pytest.raises(KeyError):
            workspace.store.get_object(base.id, proposal.version)

        workspace.register_change(event)
        def reject_skill_activation(*args, **kwargs):
            pytest.fail("Production application attempted to activate an unqualified skill")

        monkeypatch.setattr(workspace.store, "activate_version", reject_skill_activation)
        membership.write_text(json.dumps({"tenant_id": runtime.profile.organization_id,
                                          "members": [{"subject": "person-1", "actor_id": binding.owner_id,
                                                       "roles": ["administrator"]}]}))
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        configure_keys(monkeypatch, key)
        headers = {"Authorization": "Bearer " + signed_token(key)}
        app = create_app(workspace_service=workspace, deployment_settings=settings)
        with TestClient(app, base_url="http://localhost") as client:
            response = client.post(f"/api/workspace/preview/{event.event_id}", headers=headers)
            assert response.status_code == 200, response.text
            preview = WorkspacePreviewBundle.model_validate(response.json()["bundle"])
            assert all(effect.disposition != EffectDisposition.REQUALIFY
                       for effect in preview.minimal_rebase_certificate.effects)
            fixture, _ = workspace._workflow(kind=event.event_id, bundle=preview)
            assert all(item.kind != "SkillContractVersion" for item in fixture.objects)
            time.sleep(4.05)
            approval = client.post(f"/api/workspace/approve/{event.event_id}", headers=headers,
                                   json={"preview_digest": preview.preview.digest})
            assert approval.status_code == 200, approval.text
            response = client.post(f"/api/workspace/apply/{event.event_id}", headers=headers,
                                   json={"approval_digest": approval.json()["approval_digest"]})
            assert response.status_code == 200, response.text
            result = response.json()
        report = result["outcome"]["rebase_receipt"]["qualification_report"]
        assert report["candidate_state"] == "CANARY"
        assert report["evidence_class"] == "LOCAL_DETERMINISTIC"
        assert workspace.current_quote().payload[slot] == value
        assert {item.id: workspace.store.get_object(item.id) for item in skill_sources} == skill_before
        assert workspace.store.verify_event_chain()["status"] == "PASS"
    finally:
        workspace.close()
