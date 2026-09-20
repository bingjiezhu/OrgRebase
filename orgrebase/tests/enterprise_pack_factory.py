"""Synthetic initial-facts Pack used only for deployment integration tests."""

import json
import shutil
from pathlib import Path

from orgrebase.workspace.models import DomainPack, EnterpriseBinding, EnterpriseResourceBinding
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.pilot_authoring import seal_enterprise_quote_pilot_pack


def make_enterprise_pack(root: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "examples/enterprise-quote-pilot/evergreen"
    prior = load_enterprise_quote_pilot_pack(source)
    draft, sealed = root / "draft", root / "pack"
    shutil.copytree(source, draft)
    manifest = json.loads((draft / "pack.json").read_text())
    profile = json.loads((draft / "profile.json").read_text())
    domain = json.loads((draft / "components/domain.json").read_text())
    knowledge = json.loads((draft / "components/knowledge.json").read_text())
    product_owner = next(item.owner_id for item in prior.profile.change_family if item.kind == "launch_date")
    finance_owner = next(item.owner_id for item in prior.profile.change_family if item.kind == "currency")
    domain_pack = DomainPack.enterprise_quote(prior.profile.default_task.template_ref)
    binding = EnterpriseBinding(
        organization_id=prior.profile.organization_id, quote_object_id=prior.quote_object_id,
        domain_pack_digest=domain_pack.digest,
        resources=tuple(EnterpriseResourceBinding(slot_id=slot, object_id=object_id, domain_id=domain_id, owner_id=owner)
                        for slot, object_id, domain_id, owner in [
                            ("launch_date", "claim:product.launch_date", "product", product_owner),
                            ("currency", "policy:finance.currency", "finance", finance_owner),
                            ("product_plan", "claim:product.enterprise_plan", "product", product_owner),
                        ]),
    )
    manifest.update(schema_version="orgrebase.enterprise-quote-pilot-pack.v2", enterprise_binding=binding.model_dump(mode="json"))
    manifest["boundaries"].update(execution_profile="AUTHENTICATED_SINGLE_TENANT", deployment_maturity="AUTHENTICATED_SINGLE_TENANT")
    profile["change_family"] = []
    domain["projection"]["change_family"] = []
    knowledge["projection"]["proposed_values"] = []
    for name, value in [("pack.json", manifest), ("profile.json", profile), ("components/domain.json", domain), ("components/knowledge.json", knowledge)]:
        (draft / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    seal_enterprise_quote_pilot_pack(draft, sealed)
    return sealed
