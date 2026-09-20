"""Read-only comparison of a quarantined restore with external effect receipts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from orgrebase.clock import SystemClock, timestamp, utc_datetime
from orgrebase.commit_gateway import EffectError, EffectRequest, ResolutionState
from orgrebase.dataverse_target import DataverseDraftTarget, DataverseDraftTargetSettings
from orgrebase.digest import sha256_digest
from orgrebase.store import StateStore


def recovery_inventory(store: StateStore, target: DataverseDraftTarget, *, since: str, until: str,
                       max_pages: int = 1000) -> dict[str, Any]:
    """Discover missing intents without reconstructing approvals or releasing writes.

    A restored database cannot enumerate intents lost after its recovery point.
    Enumeration therefore starts at the target as well as at the local ledger.
    An absent receipt never establishes that an old request cannot arrive later.
    """
    if not 1 <= max_pages <= 10_000:
        raise EffectError("RECOVERY_INVENTORY_BUDGET_INVALID")
    start, end = utc_datetime(since), utc_datetime(until)
    if start >= end:
        raise EffectError("TARGET_INVENTORY_INTERVAL_INVALID")
    health = store.check_health()
    if not health["recovery_required"]:
        raise EffectError("RECOVERY_QUARANTINE_REQUIRED")
    if store.tenant_id != target.settings.tenant_id:
        raise EffectError("RECOVERY_TARGET_TENANT_MISMATCH")
    store.list_effects(limit=1, all_workspaces=True)

    findings: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursors: set[str] = set()
    cursor = None
    target_complete = False
    local_complete = False
    local_count = 0
    queries = 0
    errors: list[str] = []

    def local_request(record: dict[str, Any]) -> EffectRequest:
        try:
            effect = EffectRequest.model_validate(record["request"])
            target.validate_request(effect)
        except (ValidationError, KeyError, TypeError, EffectError):
            raise EffectError("RECOVERY_LOCAL_INTENT_INVALID") from None
        projected_key = (
            sha256_digest({"tenant": effect.tenant_id, "target": effect.target_key})
            if health["schema_version"] == 3 else effect.barrier_key
        )
        if (effect.effect_id != record["effect_id"] or effect.digest != record["request_digest"]
                or record["target_key"] != projected_key):
            raise EffectError("RECOVERY_LOCAL_INTENT_DIGEST_MISMATCH")
        return effect

    try:
        for _ in range(max_pages):
            page = target.effect_inventory_page(since=timestamp(start), until=timestamp(end), cursor=cursor)
            for receipt in page["items"]:
                effect_id = receipt["orgrebase_effectid"]
                if effect_id in seen:
                    raise EffectError("RECOVERY_TARGET_DUPLICATE_EFFECT")
                seen.add(effect_id)
                entry = {"effect_id": effect_id, "target_receipt": receipt,
                         "classification": "MISSING_FROM_RESTORED_DATABASE"}
                record = store.get_effect(effect_id)
                if record is not None:
                    effect = local_request(record)
                    expected = {"orgrebase_requestdigest": effect.digest,
                                "orgrebase_approvaldigest": effect.approval_digest,
                                "orgrebase_payloadhash": sha256_digest(effect.payload),
                                "orgrebase_predecessorversion": effect.expected_version}
                    matches = all(receipt[key] == value for key, value in expected.items())
                    outcome = receipt["orgrebase_outcome"]
                    classification = "REQUIRES_RECONCILIATION"
                    if not matches or (record["state"] in {"CONFIRMED", "REJECTED"} and record["state"] != outcome):
                        classification = "BINDING_OR_OUTCOME_CONFLICT"
                    elif record["state"] == outcome:
                        classification = "MATCHED_TERMINAL_INTENT"
                    entry.update(workspace_id=record["workspace_id"], local_state=record["state"],
                                 classification=classification)
                findings.append(entry)
            cursor = page["next_cursor"]
            if cursor is None:
                target_complete = True
                break
            if cursor in cursors:
                raise EffectError("RECOVERY_TARGET_CURSOR_CYCLE")
            cursors.add(cursor)
        if not target_complete:
            errors.append("RECOVERY_TARGET_PAGE_BUDGET_EXHAUSTED")

        cursor = None
        for _ in range(max_pages):
            # Operator privileges are mandatory: a workspace-scoped lookup could
            # incorrectly describe another workspace's intent as lost.
            page = store.list_effects(after=cursor, limit=100, all_workspaces=True)
            for record in page["items"]:
                if record["request"].get("target_key") != target.settings.target_key:
                    continue
                local_count += 1
                effect = local_request(record)
                if effect.effect_id in seen or record["state"] in {"CONFIRMED", "REJECTED"}:
                    continue
                resolution = target.query_effect(effect)
                queries += 1
                findings.append({"effect_id": effect.effect_id, "workspace_id": record["workspace_id"],
                                 "local_state": record["state"], "target_state": resolution.state.value,
                                 "classification": "UNRESOLVED_NO_RECEIPT" if resolution.state == ResolutionState.UNKNOWN
                                 else "REQUIRES_RECONCILIATION",
                                 "target_evidence": dict(resolution.evidence or {})})
            cursor = page["next_cursor"]
            if cursor is None:
                local_complete = True
                break
        if not local_complete:
            errors.append("RECOVERY_LOCAL_PAGE_BUDGET_EXHAUSTED")
    except (EffectError, PermissionError) as error:
        errors.append(str(error))

    requires_review = any(item["classification"] != "MATCHED_TERMINAL_INTENT" for item in findings)
    report = {
        "schema": "orgrebase.recovery-effect-inventory.v1",
        "status": "INCOMPLETE" if errors else "RECONCILIATION_REQUIRED" if requires_review else "INVENTORY_COMPLETE",
        "observed_at": SystemClock().now(), "tenant_id": store.tenant_id,
        "target_key": target.settings.target_key,
        "target_settings_digest": sha256_digest(target.settings.model_dump(mode="json")),
        "interval": {"since": timestamp(start), "until": timestamp(end)},
        "target_inventory_complete": target_complete, "local_inventory_complete": local_complete,
        "target_receipts": len(seen), "local_intents": local_count, "direct_receipt_queries": queries,
        "findings": findings, "errors": errors,
        "database_writes": 0, "target_writes": 0, "writes_released": False,
        "recovery_required": True, "production_ready": False,
        "release_prerequisites": [
            "Fence every pre-restore worker and its outbound requests before selecting the final inventory interval.",
            "Recover missing intent payloads and approvals from an independent ledger; target hashes cannot recreate them.",
            "Reconcile every unresolved effect and conflict against target evidence; missing receipts remain unknown.",
            "Reapply current deletion obligations and verify the complete restored database and target qualification.",
            "Obtain the deployment operator's recovery acceptance; this read-only command never releases quarantine.",
        ],
    }
    return {**report, "report_digest": sha256_digest(report)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orgrebase recovery-inventory")
    parser.add_argument("--target-config", type=Path, required=True)
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--token-variable", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--max-pages", type=int, default=1000)
    parser.add_argument("--read-schema-version", type=int, choices=(3,))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if (not args.target_config.is_file() or args.target_config.is_symlink()
                or args.target_config.stat().st_size > 65_536):
            raise EffectError("RECOVERY_TARGET_CONFIGURATION_INVALID")
        settings = DataverseDraftTargetSettings.model_validate_json(args.target_config.read_bytes())
        dsn = os.environ.get(args.database_url_env)
        token = os.environ.get(args.token_variable)
        if not dsn or not dsn.startswith(("postgresql://", "postgresql+psycopg://")) or not token:
            raise EffectError("RECOVERY_OPERATOR_CREDENTIALS_REQUIRED")
        with StateStore(
            dsn, tenant_id=args.tenant_id, maintenance=True, read_only=True, migrate=False,
            read_schema_version=args.read_schema_version,
        ) as store:
            # Cross-workspace access must be qualified before any target I/O.
            store.list_effects(limit=1, all_workspaces=True)
            with DataverseDraftTarget(settings, lambda: token) as target:
                report = recovery_inventory(store, target, since=args.since, until=args.until, max_pages=args.max_pages)
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print(json.dumps({key: report[key] for key in ("status", "report_digest", "writes_released")}))
        return 0 if report["status"] == "INVENTORY_COMPLETE" else 1
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({"status": "ERROR", "error": "RECOVERY_INVENTORY_FAILED", "writes_released": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
