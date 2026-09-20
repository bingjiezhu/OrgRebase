"""Read-only Dataverse prerequisites; metadata never grants write authority."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from orgrebase.commit_gateway import EffectError
from orgrebase.dataverse_target import DataverseDraftTarget, DataverseDraftTargetSettings
from orgrebase.digest import sha256_digest

RECEIPT_FIELDS = {
    "orgrebase_name": 36,
    "orgrebase_effectid": 512,
    "orgrebase_requestdigest": 71,
    "orgrebase_tenantid": 256,
    "orgrebase_targetkey": 1024,
    "orgrebase_predecessorversion": 512,
    "orgrebase_approvaldigest": 71,
    "orgrebase_payloadhash": 71,
    "orgrebase_outcome": 9,
}
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,100}$")
_ENTITY_FIELDS = "LogicalName,MetadataId,EntitySetName,PrimaryIdAttribute,TableType,DataProviderId,DataSourceId,IsOptimisticConcurrencyEnabled"
_ATTRIBUTE_FIELDS = "LogicalName,AttributeType,IsPrimaryId,IsValidForCreate,IsValidForRead,IsValidForUpdate"
_LIVE_REQUIREMENTS = (
    ("RECEIPT_APPEND_ONLY_AUTHORITY", "Under the deployed worker and every other principal, prove receipt creation/read are permitted and update/delete/upsert overwrite are denied; include administrators and break-glass controls."),
    ("TARGET_CUSTOMIZATION_ISOLATION", "Inventory and isolate synchronous plugins, flows, jobs and solutions that can mutate quote fields or receipts, or perform external actions outside the changeset transaction."),
    ("LIVE_ATOMIC_ROLLBACK", "In an authorized Dataverse sandbox, force the quote If-Match to fail and prove the receipt insert rolls back with no quote edit."),
    ("LIVE_RESPONSE_LOSS", "Drop the response after a committed changeset, restart the worker, and confirm only by the original receipt without dispatching another batch."),
    ("LIVE_LATE_COMMIT_CANCEL", "Hold an original batch, create the same-key cancellation tombstone, then release the batch; prove the original receipt conflicts and no quote edit commits."),
    ("LIVE_CONCURRENT_WORKERS", "Race worker leases and target response delays; verify one durable effect identity, preserved UNKNOWN barrier and positive reconciliation before release."),
    ("LIVE_PERMISSION_REVOCATION", "Revoke actual target/API/approver permissions before and during dispatch, then verify no unauthorized retry or inferred success."),
    ("RECEIPT_RETENTION_AND_RECOVERY", "Demonstrate receipt retention across the supported restore horizon, target receipt inventory, deletion protection and operator-controlled reconciliation before resumed writes."),
)


def _rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    rows = document.get("value")
    if "@odata.nextLink" in document or not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise EffectError("TARGET_METADATA_INCOMPLETE")
    return rows


def _indexed(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    names = [row.get("LogicalName") for row in rows]
    if any(not isinstance(name, str) or not _NAME.fullmatch(name) for name in names) or len(set(names)) != len(names):
        raise EffectError("TARGET_METADATA_ATTRIBUTES_INVALID")
    return dict(zip(names, rows, strict=True))


def _query(path: str, **parameters: str) -> str:
    return path + "?" + urlencode(parameters)


def probe_target(target: DataverseDraftTarget, *, organization_id: str) -> dict[str, Any]:
    """Observe immutable prerequisites; never claim sandbox or IAM proof."""
    if str(UUID(organization_id)) != organization_id:
        raise ValueError("DATAVERSE_ORGANIZATION_ID_INVALID")
    checks: list[dict[str, Any]] = []

    def check(code: str, passed: bool, observed: Any) -> None:
        checks.append({"code": code, "status": "PASS" if passed else "FAIL", "observed": observed})

    who = target.read_metadata("/WhoAmI")
    check("ENVIRONMENT_IDENTITY", who.get("OrganizationId") == organization_id,
          {"organization_id": who.get("OrganizationId"), "api_url": target.settings.api_url})
    observations = {}
    for kind, entity_set, primary_key in (("QUOTE", "quotes", "quoteid"),
                                          ("RECEIPT", target.settings.receipt_entity_set, target.settings.receipt_primary_key)):
        rows = _rows(target.read_metadata(_query("/EntityDefinitions", **{
            "$select": _ENTITY_FIELDS, "$filter": f"EntitySetName eq '{entity_set}'",
        })))
        if len(rows) != 1:
            check(kind + "_ENTITY_UNIQUE", False, {"matches": len(rows), "entity_set": entity_set})
            continue
        entity = rows[0]
        logical_name = entity.get("LogicalName")
        if not isinstance(logical_name, str) or not _NAME.fullmatch(logical_name):
            raise EffectError("TARGET_METADATA_ENTITY_INVALID")
        path = f"/EntityDefinitions(LogicalName='{logical_name}')/Attributes"
        attributes = _indexed(_rows(target.read_metadata(_query(path, **{"$select": _ATTRIBUTE_FIELDS}))))
        strings = _indexed(_rows(target.read_metadata(_query(path + "/Microsoft.Dynamics.CRM.StringAttributeMetadata",
                                                             **{"$select": "LogicalName,MaxLength"}))))
        observations[kind.lower()] = {"entity": entity, "attributes": attributes, "strings": strings}
        check(kind + "_ENTITY_BINDING", entity.get("EntitySetName") == entity_set and entity.get("PrimaryIdAttribute") == primary_key,
              {"logical_name": logical_name, "entity_set": entity.get("EntitySetName"), "primary_id": entity.get("PrimaryIdAttribute")})
        check(kind + "_STANDARD_NONVIRTUAL", entity.get("TableType") == "Standard"
              and "DataProviderId" in entity and entity["DataProviderId"] is None
              and "DataSourceId" in entity and entity["DataSourceId"] is None,
              {name: entity.get(name) for name in ("TableType", "DataProviderId", "DataSourceId")})
        primary = attributes.get(primary_key, {})
        check(kind + "_GUID_PRIMARY_KEY", primary.get("AttributeType") == "Uniqueidentifier"
              and primary.get("IsPrimaryId") is True and primary.get("IsValidForRead") is True,
              {"attribute": primary_key, "type": primary.get("AttributeType"), "primary": primary.get("IsPrimaryId")})
        if kind == "RECEIPT":
            check("RECEIPT_CALLER_ASSIGNED_ID", primary.get("IsValidForCreate") is True,
                  {"attribute": primary_key, "valid_for_create": primary.get("IsValidForCreate")})
            for name, capacity in RECEIPT_FIELDS.items():
                attribute, length = attributes.get(name, {}), strings.get(name, {}).get("MaxLength")
                check("RECEIPT_FIELD_" + name, attribute.get("AttributeType") == "String"
                      and attribute.get("IsValidForCreate") is True and attribute.get("IsValidForRead") is True
                      and type(length) is int and length >= capacity,
                      {"field": name, "type": attribute.get("AttributeType"), "capacity": length, "required_capacity": capacity,
                       "valid_for_create": attribute.get("IsValidForCreate"), "valid_for_read": attribute.get("IsValidForRead")})
        else:
            check("QUOTE_OPTIMISTIC_CONCURRENCY", entity.get("IsOptimisticConcurrencyEnabled") is True,
                  entity.get("IsOptimisticConcurrencyEnabled"))
            memos = _indexed(_rows(target.read_metadata(_query(path + "/Microsoft.Dynamics.CRM.MemoAttributeMetadata",
                                                               **{"$select": "LogicalName,MaxLength"}))))
            observations["quote"]["memos"] = memos
            for name, expected_type, lengths, minimum in (("name", "String", strings, 300), ("description", "Memo", memos, 2000)):
                attribute, length = attributes.get(name, {}), lengths.get(name, {}).get("MaxLength")
                check("QUOTE_FIELD_" + name, attribute.get("AttributeType") == expected_type
                      and attribute.get("IsValidForRead") is True and attribute.get("IsValidForUpdate") is True
                      and type(length) is int and length >= minimum,
                      {"field": name, "type": attribute.get("AttributeType"), "capacity": length, "required_capacity": minimum,
                       "valid_for_read": attribute.get("IsValidForRead"), "valid_for_update": attribute.get("IsValidForUpdate")})
    draft = target.draft()
    check("QUOTE_DRAFT_READABLE", True, {"target_key": draft["target_key"], "version": draft["version"]})
    metadata_pass = all(item["status"] == "PASS" for item in checks)
    report = {"schema_version": "orgrebase.dataverse-qualification.v1", "checked_at": datetime.now(UTC).isoformat(),
              "status": "INCOMPLETE" if metadata_pass else "FAIL", "metadata_status": "PASS" if metadata_pass else "FAIL",
              "production_ready": False, "write_authorized": False, "target_writes": 0,
              "target_settings_digest": sha256_digest(target.settings.model_dump(mode="json")),
              "organization_id": organization_id, "target_key": target.settings.target_key,
              "metadata_digest": sha256_digest(observations), "checks": checks,
              "sandbox_requirements": [{"code": code, "status": "NOT_RUN", "required_evidence": evidence}
                                       for code, evidence in _LIVE_REQUIREMENTS],
              "boundary": "Read-only metadata is a prerequisite, not append-only authority or atomicity qualification."}
    return {**report, "report_digest": sha256_digest(report)}


def receipt_schema(*, logical_name: str = "orgrebase_effectreceipt", entity_set: str = "orgrebase_effectreceipts") -> dict[str, Any]:
    if not _NAME.fullmatch(logical_name) or not logical_name.startswith("orgrebase_") or not _NAME.fullmatch(entity_set):
        raise ValueError("DATAVERSE_RECEIPT_SCHEMA_NAME_INVALID")
    def label(text: str) -> dict[str, Any]:
        return {"LocalizedLabels": [{"Label": text, "LanguageCode": 1033}]}
    attributes = [{"@odata.type": "Microsoft.Dynamics.CRM.StringAttributeMetadata", "SchemaName": name,
                   "DisplayName": label(name.removeprefix("orgrebase_")), "RequiredLevel": {"Value": "ApplicationRequired"},
                   "FormatName": {"Value": "Text"}, "MaxLength": length, "IsPrimaryName": name == "orgrebase_name"}
                  for name, length in RECEIPT_FIELDS.items()]
    body = {"SchemaName": logical_name, "EntitySetName": entity_set, "DisplayName": label("OrgRebase effect receipt"),
            "DisplayCollectionName": label("OrgRebase effect receipts"), "Description": label("Immutable effect outcome and cancellation fences"),
            "OwnershipType": "OrganizationOwned", "IsActivity": False, "HasActivities": False, "HasNotes": False,
            "TableType": "Standard", "Attributes": attributes}
    return {"schema_version": "orgrebase.dataverse-receipt-schema.v1", "target_writes": 0,
            "status": "ADMINISTRATOR_REVIEW_REQUIRED", "method": "POST", "path": "/api/data/v9.2/EntityDefinitions",
            "body": body, "expected_primary_id": logical_name + "id",
            "required_solution_publisher_prefix": "orgrebase", "automatic_submission": False,
            "boundary": "Schema alone does not enforce append-only access. Administrator must review solution, privileges, automation and sandbox evidence before deployment."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orgrebase dataverse-target")
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("probe", help="Read target metadata without sending writes")
    probe.add_argument("--config", type=Path, required=True)
    probe.add_argument("--organization-id", required=True)
    probe.add_argument("--token-variable", required=True)
    probe.add_argument("--output", type=Path)
    schema = commands.add_parser("schema", help="Generate a table definition for administrator review only")
    schema.add_argument("--logical-name", default="orgrebase_effectreceipt")
    schema.add_argument("--entity-set", default="orgrebase_effectreceipts")
    schema.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            result = receipt_schema(logical_name=args.logical_name, entity_set=args.entity_set)
            code = 0
        else:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", args.token_variable):
                raise ValueError("TARGET_TOKEN_VARIABLE_INVALID")
            if not args.config.is_file() or args.config.is_symlink() or args.config.stat().st_size > 65_536:
                raise ValueError("TARGET_CONFIGURATION_UNAVAILABLE")
            document = json.loads(args.config.read_bytes())
            settings = DataverseDraftTargetSettings.model_validate(document.get("target", document))
            with DataverseDraftTarget(settings, lambda: os.environ.get(args.token_variable, "")) as target:
                result = probe_target(target, organization_id=args.organization_id)
            code = 0 if result["metadata_status"] == "PASS" else 1
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered)
        else:
            print(rendered, end="")
        return code
    except (EffectError, ValueError, OSError, TypeError, AttributeError) as error:
        code = str(error).split(":", 1)[0] if isinstance(error, EffectError) else "TARGET_QUALIFICATION_INPUT_INVALID"
        print(json.dumps({"status": "ERROR", "code": code, "production_ready": False, "target_writes": 0}))
        return 2
