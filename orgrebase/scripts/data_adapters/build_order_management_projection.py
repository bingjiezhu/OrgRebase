"""Build the retained object-centric OCEL projection and causal overlay.

The projection is a deterministic subset of the exact Zenodo SQLite bytes. It
keeps objects, events, event-object relations, object-object relations and
attribute history as separate tables.  The overlay is deliberately separate:
the source log observes a product price change, but treating that change as a
quote policy and adjudicating dependency coverage are experimenter-injected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

try:
    from scripts.data_adapters.fetch_order_management_ocel import (
        EXPECTED_SHA256,
        verify_source,
    )
except ModuleNotFoundError:  # Direct ``python scripts/data_adapters/...`` execution.
    from fetch_order_management_ocel import EXPECTED_SHA256, verify_source

PROJECTION_SCHEMA = "orgrebase.workspace-public-process-projection.v1"
OVERLAY_SCHEMA = "orgrebase.workspace-public-causal-overlay.v1"
SELECTION_TIME_SQL = "2023-08-01 14:17:59"
SELECTION_TIME_ISO = "2023-08-01T14:17:59Z"
TARGET_PRODUCT = "MacBook Pro"
OBJECT_TABLES = {
    "customers": "object_Customers",
    "employees": "object_Employees",
    "items": "object_Items",
    "orders": "object_Orders",
    "packages": "object_Packages",
    "products": "object_Products",
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def object_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def seal(value: dict[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    payload.pop("digest", None)
    return {**payload, "digest": object_digest(payload)}


def _iso(value: str) -> str:
    return value.replace(" ", "T") + ("Z" if not value.endswith("Z") else "")


def _rows(connection: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor = connection.execute(query, params)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _history(connection: sqlite3.Connection, selected_ids: set[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for object_type, table in sorted(OBJECT_TABLES.items()):
        placeholders = ",".join("?" for _ in selected_ids)
        if not placeholders:
            continue
        query = f'SELECT * FROM "{table}" WHERE ocel_id IN ({placeholders}) ORDER BY ocel_id, ocel_time'
        for row in _rows(connection, query, tuple(sorted(selected_ids))):
            attributes = {
                key: value
                for key, value in row.items()
                if key not in {"ocel_id", "ocel_time", "ocel_changed_field"} and value is not None
            }
            changed = row.get("ocel_changed_field")
            records.append(
                {
                    "object_ref": row["ocel_id"],
                    "object_type": object_type,
                    "observed_at": _iso(row["ocel_time"]),
                    "changed_field": changed if changed not in {None, ""} else None,
                    "attributes": attributes,
                    "origin": "OBSERVED",
                }
            )
    return records


def build_projection(source: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source_receipt = verify_source(source)
    connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        open_order_rows = _rows(
            connection,
            """
            WITH order_times AS (
              SELECT o.ocel_id AS order_ref,
                     placed.ocel_time AS placed_at,
                     confirmed.ocel_time AS confirmed_at
              FROM object o
              JOIN event_object place_link ON place_link.ocel_object_id = o.ocel_id
              JOIN event_PlaceOrder placed ON placed.ocel_id = place_link.ocel_event_id
              JOIN event_object confirm_link ON confirm_link.ocel_object_id = o.ocel_id
              JOIN event_ConfirmOrder confirmed ON confirmed.ocel_id = confirm_link.ocel_event_id
              WHERE o.ocel_type = 'orders'
            )
            SELECT order_ref, placed_at, confirmed_at
            FROM order_times
            WHERE placed_at <= ? AND confirmed_at > ?
            ORDER BY order_ref
            """,
            (SELECTION_TIME_SQL, SELECTION_TIME_SQL),
        )
        open_orders = {row["order_ref"] for row in open_order_rows}
        if len(open_orders) != 11:
            raise ValueError(f"OPEN_ORDER_COUNT_DRIFT:{len(open_orders)}")

        all_relations = _rows(
            connection,
            """
            SELECT ocel_source_id AS source_ref,
                   ocel_target_id AS target_ref,
                   ocel_qualifier AS qualifier
            FROM object_object
            ORDER BY ocel_source_id, ocel_target_id, ocel_qualifier
            """,
        )
        items = {
            row["target_ref"]
            for row in all_relations
            if row["source_ref"] in open_orders and row["qualifier"] == "comprises"
        }
        products = {
            row["target_ref"]
            for row in all_relations
            if row["source_ref"] in items and row["qualifier"] == "is a"
        }
        customers = {
            row["source_ref"]
            for row in all_relations
            if row["target_ref"] in open_orders and row["qualifier"] == "places"
        }
        employees = {
            row["target_ref"]
            for row in all_relations
            if row["source_ref"] in customers and row["qualifier"] in {"primarySalesRep", "secondarySalesRep"}
        }
        selected_ids = open_orders | items | products | customers | employees
        objects = _rows(
            connection,
            """
            SELECT ocel_id AS object_ref, ocel_type AS object_type
            FROM object
            ORDER BY ocel_type, ocel_id
            """,
        )
        objects = [{**row, "origin": "OBSERVED"} for row in objects if row["object_ref"] in selected_ids]
        relations = [
            {**row, "origin": "OBSERVED"}
            for row in all_relations
            if row["source_ref"] in selected_ids and row["target_ref"] in selected_ids
        ]

        event_placeholders = ",".join("?" for _ in open_orders)
        event_rows = _rows(
            connection,
            f"""
            SELECT e.ocel_id AS event_ref, e.ocel_type AS event_type,
                   CASE e.ocel_type
                     WHEN 'place order' THEN p.ocel_time
                     WHEN 'confirm order' THEN c.ocel_time
                   END AS occurred_at
            FROM event e
            LEFT JOIN event_PlaceOrder p ON p.ocel_id = e.ocel_id
            LEFT JOIN event_ConfirmOrder c ON c.ocel_id = e.ocel_id
            WHERE e.ocel_type IN ('place order', 'confirm order')
              AND e.ocel_id IN (
                SELECT eo.ocel_event_id FROM event_object eo
                WHERE eo.ocel_object_id IN ({event_placeholders})
              )
            ORDER BY occurred_at, event_ref
            """,
            tuple(sorted(open_orders)),
        )
        events = [
            {**row, "occurred_at": _iso(row["occurred_at"]), "origin": "OBSERVED"} for row in event_rows
        ]
        event_ids = {row["event_ref"] for row in events}
        event_object_relations = [
            {**row, "origin": "OBSERVED"}
            for row in _rows(
                connection,
                """
                SELECT ocel_event_id AS event_ref,
                       ocel_object_id AS object_ref,
                       ocel_qualifier AS qualifier
                FROM event_object
                ORDER BY ocel_event_id, ocel_object_id, ocel_qualifier
                """,
            )
            if row["event_ref"] in event_ids and row["object_ref"] in selected_ids
        ]
        history = _history(connection, selected_ids)
    finally:
        connection.close()

    product_by_item = {
        row["source_ref"]: row["target_ref"] for row in relations if row["qualifier"] == "is a"
    }
    item_by_order: dict[str, list[str]] = {order: [] for order in open_orders}
    for row in relations:
        if row["qualifier"] == "comprises" and row["source_ref"] in open_orders:
            item_by_order[row["source_ref"]].append(row["target_ref"])
    exact_product_paths: dict[str, list[str]] = {}
    for order_ref, order_items in sorted(item_by_order.items()):
        for item_ref in sorted(order_items):
            if product_by_item.get(item_ref) == TARGET_PRODUCT:
                exact_product_paths[order_ref] = [order_ref, item_ref, TARGET_PRODUCT]
                break
    if len(exact_product_paths) != 5:
        raise ValueError(f"EXPECTED_AFFECTED_COUNT_DRIFT:{len(exact_product_paths)}")

    projection = seal(
        {
            "schema_version": PROJECTION_SCHEMA,
            "projection_id": "ocel-order-management-open-orders-2023-08-01",
            "evidence_class": "PUBLIC_PROCESS_OBSERVED_DERIVED",
            "claim_boundary": "PUBLIC_SIMULATION_MECHANISM_FIXTURE_NOT_ENTERPRISE_SHADOW_OR_ROI",
            "upstream": source_receipt,
            "selection": {
                "as_of": SELECTION_TIME_ISO,
                "changed_object_ref": TARGET_PRODUCT,
                "rule": "place_order_time <= as_of < confirm_order_time",
                "origin": "DERIVED",
            },
            "provenance_legend": [
                "OBSERVED",
                "DERIVED",
                "EXPERIMENTER_INJECTED",
                "MISSING",
            ],
            "objects": objects,
            "events": events,
            "event_object_relations": event_object_relations,
            "object_object_relations": relations,
            "object_attribute_history": history,
            "derived_views": {
                "open_order_refs": sorted(open_orders),
                "exact_product_paths": exact_product_paths,
                "origin": "DERIVED",
            },
            "table_counts": {
                "objects": len(objects),
                "events": len(events),
                "event_object_relations": len(event_object_relations),
                "object_object_relations": len(relations),
                "object_attribute_history": len(history),
            },
        }
    )

    affected = sorted(exact_product_paths)
    nonaffected = sorted(open_orders - set(affected))
    declared_non_dependencies = nonaffected[:4]
    missing_dependencies = nonaffected[4:]
    dependency_claims = []
    for order_ref in affected:
        dependency_claims.append(
            {
                "source_ref": TARGET_PRODUCT,
                "target_ref": order_ref,
                "dependency_kind": "ACTUAL_READ",
                "origin": "EXPERIMENTER_INJECTED",
                "observed_support_path": exact_product_paths[order_ref],
            }
        )
    for order_ref in declared_non_dependencies:
        dependency_claims.append(
            {
                "source_ref": TARGET_PRODUCT,
                "target_ref": order_ref,
                "dependency_kind": "EXPLICIT_NON_DEPENDENCY",
                "origin": "EXPERIMENTER_INJECTED",
                "observed_support_path": [],
            }
        )
    for order_ref in missing_dependencies:
        dependency_claims.append(
            {
                "source_ref": TARGET_PRODUCT,
                "target_ref": order_ref,
                "dependency_kind": "UNKNOWN",
                "origin": "MISSING",
                "observed_support_path": [],
            }
        )
    overlay = seal(
        {
            "schema_version": OVERLAY_SCHEMA,
            "scenario_id": "ocel-om-macbook-price-change-open-orders-v1",
            "evidence_class": "PUBLIC_SOURCE_DERIVED_SYNTHETIC",
            "claim_boundary": "INJECTED_CAUSAL_GROUND_TRUTH_NOT_OBSERVED_ENTERPRISE_CAUSALITY_OR_ROI",
            "projection_digest": projection["digest"],
            "policy_change": {
                "changed_object_ref": TARGET_PRODUCT,
                "changed_field": "price",
                "effective_at": SELECTION_TIME_ISO,
                "old_value": 2642.75,
                "new_value": 2705.0,
                "source_attribute_origin": "OBSERVED",
                "policy_interpretation_origin": "EXPERIMENTER_INJECTED",
            },
            "target_universe": sorted(open_orders),
            "ground_truth": {
                "expected_affected": affected,
                "expected_unaffected": nonaffected,
                "basis": "INJECTED_EXACT_PRODUCT_READ_RULE_OVER_OBSERVED_OBJECT_RELATIONS",
                "origin": "EXPERIMENTER_INJECTED",
            },
            "dependency_claims": sorted(dependency_claims, key=lambda row: row["target_ref"]),
            "controlled_missingness": {
                "missing_target_refs": missing_dependencies,
                "reason": "EXERCISE_UNKNOWN_INSTEAD_OF_FALSE_UNAFFECTED",
                "origin": "MISSING",
            },
        }
    )
    return projection, overlay


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--projection-output",
        type=Path,
        default=root
        / "benchmark"
        / "quote-value-v0.3-public-process"
        / "projection"
        / "object-centric-projection.json",
    )
    parser.add_argument(
        "--overlay-output",
        type=Path,
        default=root
        / "benchmark"
        / "quote-value-v0.3-public-process"
        / "overlay"
        / "policy-change-ground-truth.json",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        projection, overlay = build_projection(args.source.resolve())
        _write(args.projection_output, projection)
        _write(args.overlay_output, overlay)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2))
        return 1
    print(
        json.dumps(
            {
                "status": "PASS",
                "source_sha256": f"sha256:{EXPECTED_SHA256}",
                "projection": str(args.projection_output),
                "projection_digest": projection["digest"],
                "overlay": str(args.overlay_output),
                "overlay_digest": overlay["digest"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
