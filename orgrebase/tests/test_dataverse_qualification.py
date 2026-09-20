from __future__ import annotations

import copy
import json
from urllib.parse import parse_qs

import httpx2 as httpx
import pytest

from orgrebase.dataverse_qualification import RECEIPT_FIELDS, main, probe_target, receipt_schema
from orgrebase.dataverse_target import DataverseDraftTarget, DataverseDraftTargetSettings

ORGANIZATION_ID = "00000000-0000-0000-0000-000000000987"
QUOTE_ID = "00000000-0000-0000-0000-000000000123"


def metadata_documents():
    def attribute(name, kind="String", *, primary=False):
        return {"LogicalName": name, "AttributeType": kind, "IsPrimaryId": primary,
                "IsValidForRead": True, "IsValidForCreate": True, "IsValidForUpdate": not primary}
    entities = {}
    for name, entity_set, key in [("quote", "quotes", "quoteid"),
                                  ("orgrebase_effectreceipt", "orgrebase_effectreceipts", "orgrebase_effectreceiptid")]:
        attributes = [attribute(key, "Uniqueidentifier", primary=True)]
        if name == "quote":
            attributes.extend([attribute("name"), attribute("description", "Memo")])
            strings, memos = [{"LogicalName": "name", "MaxLength": 300}], [{"LogicalName": "description", "MaxLength": 2000}]
        else:
            attributes.extend(attribute(field) for field in RECEIPT_FIELDS)
            strings = [{"LogicalName": field, "MaxLength": capacity} for field, capacity in RECEIPT_FIELDS.items()]
            memos = []
        entities[name] = {
            "entity": {"LogicalName": name, "MetadataId": ORGANIZATION_ID, "EntitySetName": entity_set,
                       "PrimaryIdAttribute": key, "TableType": "Standard", "DataProviderId": None,
                       "DataSourceId": None, "IsOptimisticConcurrencyEnabled": True},
            "attributes": attributes, "strings": strings, "memos": memos,
        }
    return entities


class MetadataProtocol:
    def __init__(self):
        self.entities = metadata_documents()
        self.organization_id = ORGANIZATION_ID
        self.requests = []
        self.duplicate = False
        self.next_link = False

    def respond(self, path: str, query: str):
        if path.endswith("/WhoAmI"):
            return {"OrganizationId": self.organization_id}
        if path.endswith("/EntityDefinitions"):
            entity_set = parse_qs(query)["$filter"][0].split("'")[1]
            rows = [item["entity"] for item in self.entities.values() if item["entity"]["EntitySetName"] == entity_set]
            return {"value": rows * (2 if self.duplicate else 1)}
        if "/EntityDefinitions(LogicalName='" in path:
            name = path.split("LogicalName='")[1].split("'")[0]
            kind = "strings" if path.endswith("StringAttributeMetadata") else "memos" if path.endswith("MemoAttributeMetadata") else "attributes"
            value = {"value": self.entities[name][kind]}
            if self.next_link:
                value["@odata.nextLink"] = "https://other.example/metadata"
            return value
        if "/quotes(" in path:
            return {"quoteid": QUOTE_ID, "statecode": 0, "@odata.etag": 'W/"41"', "name": "Private quote name",
                    "description": "Private quote description"}
        raise AssertionError(path)

    def __call__(self, request):
        assert request.method == "GET"
        self.requests.append(request)
        return httpx.Response(200, json=self.respond(request.url.path, request.url.query.decode()))


@pytest.fixture
def qualification():
    protocol = MetadataProtocol()
    settings = DataverseDraftTargetSettings(tenant_id="org:test", instance_url="https://sales.example", quote_id=QUOTE_ID)
    target = DataverseDraftTarget(settings, lambda: "opaque-test-credential", transport=httpx.MockTransport(protocol))
    return protocol, target


def test_valid_metadata_remains_incomplete_without_real_permissions_and_fault_evidence(qualification):
    protocol, target = qualification
    report = probe_target(target, organization_id=ORGANIZATION_ID)
    assert report["metadata_status"] == "PASS" and report["status"] == "INCOMPLETE"
    assert report["production_ready"] is False and report["write_authorized"] is False and report["target_writes"] == 0
    assert all(item["status"] == "NOT_RUN" for item in report["sandbox_requirements"])
    assert all(request.method == "GET" and request.url.host == "sales.example" for request in protocol.requests)
    assert "Private quote" not in json.dumps(report) and "opaque-test-credential" not in json.dumps(report)


@pytest.mark.parametrize("entity,field,value,code", [
    ("quote", "TableType", "Elastic", "QUOTE_STANDARD_NONVIRTUAL"),
    ("orgrebase_effectreceipt", "TableType", "Elastic", "RECEIPT_STANDARD_NONVIRTUAL"),
    ("orgrebase_effectreceipt", "TableType", None, "RECEIPT_STANDARD_NONVIRTUAL"),
    ("orgrebase_effectreceipt", "DataProviderId", ORGANIZATION_ID, "RECEIPT_STANDARD_NONVIRTUAL"),
    ("orgrebase_effectreceipt", "DataSourceId", ORGANIZATION_ID, "RECEIPT_STANDARD_NONVIRTUAL"),
    ("quote", "IsOptimisticConcurrencyEnabled", False, "QUOTE_OPTIMISTIC_CONCURRENCY"),
    ("quote", "IsOptimisticConcurrencyEnabled", 1, "QUOTE_OPTIMISTIC_CONCURRENCY"),
    ("orgrebase_effectreceipt", "PrimaryIdAttribute", "orgrebase_otherid", "RECEIPT_ENTITY_BINDING"),
])
def test_metadata_contradictions_cannot_qualify_target(qualification, entity, field, value, code):
    protocol, target = qualification
    protocol.entities[entity]["entity"][field] = value
    report = probe_target(target, organization_id=ORGANIZATION_ID)
    assert report["status"] == "FAIL" and report["production_ready"] is False
    assert next(item for item in report["checks"] if item["code"] == code)["status"] == "FAIL"


@pytest.mark.parametrize("field", list(RECEIPT_FIELDS))
def test_each_receipt_field_must_hold_the_full_request_contract(qualification, field):
    protocol, target = qualification
    attribute = next(item for item in protocol.entities["orgrebase_effectreceipt"]["strings"] if item["LogicalName"] == field)
    attribute["MaxLength"] = RECEIPT_FIELDS[field] - 1
    report = probe_target(target, organization_id=ORGANIZATION_ID)
    assert next(item for item in report["checks"] if item["code"] == "RECEIPT_FIELD_" + field)["status"] == "FAIL"


@pytest.mark.parametrize("mutation", ["read", "create", "type", "primary", "id-create", "organization"])
def test_receipt_primary_id_permissions_and_environment_must_match(qualification, mutation):
    protocol, target = qualification
    attributes = protocol.entities["orgrebase_effectreceipt"]["attributes"]
    if mutation == "organization":
        protocol.organization_id = "00000000-0000-0000-0000-000000000986"
    elif mutation == "primary":
        attributes[0]["IsPrimaryId"] = False
    elif mutation == "id-create":
        attributes[0]["IsValidForCreate"] = False
    else:
        attributes[1][{"read": "IsValidForRead", "create": "IsValidForCreate", "type": "AttributeType"}[mutation]] = "Memo" if mutation == "type" else False
    report = probe_target(target, organization_id=ORGANIZATION_ID)
    assert report["metadata_status"] == "FAIL"


def test_missing_virtual_provider_metadata_is_not_treated_as_nonvirtual(qualification):
    protocol, target = qualification
    del protocol.entities["orgrebase_effectreceipt"]["entity"]["DataProviderId"]
    assert probe_target(target, organization_id=ORGANIZATION_ID)["metadata_status"] == "FAIL"


@pytest.mark.parametrize("mutation", ["duplicate_entity", "duplicate_attribute", "pagination"])
def test_incomplete_or_ambiguous_metadata_cannot_produce_pass(qualification, mutation):
    from orgrebase.commit_gateway import EffectError

    protocol, target = qualification
    if mutation == "duplicate_entity":
        protocol.duplicate = True
        assert probe_target(target, organization_id=ORGANIZATION_ID)["metadata_status"] == "FAIL"
        return
    if mutation == "duplicate_attribute":
        protocol.entities["quote"]["attributes"].append(copy.deepcopy(protocol.entities["quote"]["attributes"][0]))
    else:
        protocol.next_link = True
    with pytest.raises(EffectError):
        probe_target(target, organization_id=ORGANIZATION_ID)


def test_schema_generation_has_no_transport_and_no_false_append_only_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(DataverseDraftTarget, "_request", lambda *args, **kwargs: pytest.fail("Schema generation sent a request"))
    output = tmp_path / "admin-schema.json"
    assert main(["schema", "--output", str(output)]) == 0
    schema = json.loads(output.read_text())
    assert schema == receipt_schema()
    assert schema["automatic_submission"] is False and schema["target_writes"] == 0
    assert schema["body"]["TableType"] == "Standard"
    assert schema["expected_primary_id"] == "orgrebase_effectreceiptid"
    assert {item["SchemaName"]: item["MaxLength"] for item in schema["body"]["Attributes"]} == RECEIPT_FIELDS
    assert not any(item.get("IsValidForUpdate") is False for item in schema["body"]["Attributes"])


def test_probe_cli_does_not_echo_bad_configuration_or_credentials(tmp_path, capsys):
    config = tmp_path / "config.json"
    config.write_text('{"secret":"not-a-real-credential"}')
    assert main(["probe", "--config", str(config), "--organization-id", ORGANIZATION_ID,
                 "--token-variable", "TARGET_TOKEN"]) == 2
    assert "not-a-real-credential" not in capsys.readouterr().out


def test_real_https_metadata_and_cli_verify_tls_without_database_or_write_transport(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    from browser_oidc_provider import LocalOIDCProvider, tls_files
    from test_effect_worker_https import HTTPSDataverse, signed_access

    certificate, private = tls_files(tmp_path)
    provider = LocalOIDCProvider(certificate, private)
    target = HTTPSDataverse(certificate, private, provider)
    try:
        token = signed_access(provider, "target-worker", audience=target.origin)
        settings = DataverseDraftTargetSettings(tenant_id="org:test", instance_url=target.origin,
                                                quote_id=QUOTE_ID, ca_bundle=str(certificate))
        config = tmp_path / "target.json"
        config.write_text(settings.model_dump_json())
        result = subprocess.run([sys.executable, "-W", "error", "-m", "orgrebase", "dataverse-target", "probe",
                                 "--config", str(config), "--organization-id", ORGANIZATION_ID,
                                 "--token-variable", "QUALIFICATION_TEST_TOKEN"],
                                cwd=Path(__file__).resolve().parents[1],
                                env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "QUALIFICATION_TEST_TOKEN": token},
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert token not in result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert report["metadata_status"] == "PASS" and report["production_ready"] is False
        assert all(method == "GET" for method, path in target.requests)
        assert target.batch_count == target.protocol.writes == 0
    finally:
        target.close()
        provider.close()
