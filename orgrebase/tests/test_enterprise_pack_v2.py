from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

import pytest
from enterprise_pack_factory import make_enterprise_pack

from orgrebase.workspace.pilot import EnterpriseQuotePilotPack, load_enterprise_quote_pilot_pack
from orgrebase.workspace.pilot_authoring import seal_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService


def test_initial_facts_pack_has_no_predetermined_change_events(tmp_path):
    pack = make_enterprise_pack(tmp_path)
    runtime = load_enterprise_quote_pilot_pack(pack)
    assert runtime.schema_version.endswith(".v2")
    assert runtime.enterprise_binding is not None
    assert runtime.profile.change_family == ()
    assert dict(runtime.proposed_values) == {}
    with closing(WorkspaceService(runtime_configuration=runtime)) as workspace:
        assert workspace.state()["change_events"] == []
        assert workspace.enterprise_binding == runtime.enterprise_binding


def test_v1_codec_preserves_historical_manifest_shape():
    path = Path(__file__).resolve().parents[1] / "examples/enterprise-quote-pilot/evergreen/pack.json"
    raw = json.loads(path.read_text())
    assert EnterpriseQuotePilotPack.model_validate(raw).model_dump(mode="json") == raw


def test_v2_requires_binding_and_rejects_seeded_future_values(tmp_path):
    pack = make_enterprise_pack(tmp_path)
    manifest = json.loads((pack / "pack.json").read_text())
    manifest.pop("enterprise_binding")
    with pytest.raises(ValueError, match="ENTERPRISE_BINDING_REQUIRED"):
        EnterpriseQuotePilotPack.model_validate(manifest)
    draft = tmp_path / "draft"
    knowledge = json.loads((draft / "components/knowledge.json").read_text())
    knowledge["projection"]["proposed_values"] = [{"change_kind": "launch_date", "object_ref": "claim:product.launch_date@v2",
        "value": "2030-01-01", "source_id": "future", "source_version": "v1"}]
    (draft / "components/knowledge.json").write_text(json.dumps(knowledge))
    with pytest.raises(ValueError, match="ENTERPRISE_PACK_FUTURE_CHANGES_FORBIDDEN"):
        seal_enterprise_quote_pilot_pack(draft, tmp_path / "rejected")
