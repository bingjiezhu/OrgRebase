"""Build a deterministic real-process projection from BPI Challenge 2019.

The parser streams the 728 MB XES and writes only relevant rows to a temporary
SQLite index.  The retained JSON contains real ``Change Price`` observations
and real downstream events; it contains no hand-authored affected labels or
precomputed query Ground Truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

try:
    from scripts.data_adapters.fetch_bpi_challenge_2019 import (
        DOI,
        EXPECTED_SHA256,
        FILE_ID,
        FILE_NAME,
        OFFICIAL_RECORD,
        SOURCE_URL,
        verify_source,
    )
except ModuleNotFoundError:  # Direct ``python scripts/data_adapters/...`` execution.
    from fetch_bpi_challenge_2019 import (  # type: ignore[no-redef]
        DOI,
        EXPECTED_SHA256,
        FILE_ID,
        FILE_NAME,
        OFFICIAL_RECORD,
        SOURCE_URL,
        verify_source,
    )

PROJECTION_SCHEMA = "orgrebase.workspace-public-real-process-projection.v1"
DATASET_MANIFEST_SCHEMA = "orgrebase.workspace-public-real-process-dataset-manifest.v1"
LICENSE_MANIFEST_SCHEMA = "orgrebase.workspace-public-real-process-license-manifest.v1"
PROJECTION_FILE = "projection/bpi2019-real-process-projection.json"
TARGET_EVENT_TYPES = (
    "Record Goods Receipt",
    "Record Service Entry Sheet",
    "Record Invoice Receipt",
    "Remove Payment Block",
    "Clear Invoice",
)
CHANGE_EVENT_TYPE = "Change Price"
WINDOW_DAYS = 30
MAX_QUERIES = 128
OFFICIAL_TRACE_COUNT = 251_734
OFFICIAL_EVENT_COUNT = 1_595_923
OFFICIAL_ACTIVITY_COUNT = 42
OFFICIAL_IDENTITY_COUNT = 627

TRACE_KEYS = (
    "concept:name",
    "Purchasing Document",
    "Item",
    "Vendor",
    "Company",
    "Item Category",
    "Source",
    "Document Type",
    "Purch. Doc. Category name",
    "Item Type",
    "GR-Based Inv. Verif.",
    "Goods Receipt",
)
EVENT_KEYS = (
    "concept:name",
    "time:timestamp",
    "org:resource",
    "User",
    "Cumulative net worth (EUR)",
)


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


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attributes(element: ET.Element, allowed: Iterable[str]) -> dict[str, str]:
    allowed_set = set(allowed)
    values: dict[str, str] = {}
    for child in element:
        if _local_name(child.tag) == "event":
            continue
        key = child.attrib.get("key")
        if key in allowed_set and "value" in child.attrib:
            values[key] = child.attrib["value"]
    return values


def _event_attributes(event: ET.Element) -> dict[str, str]:
    allowed = set(EVENT_KEYS)
    return {
        child.attrib["key"]: child.attrib["value"]
        for child in event
        if child.attrib.get("key") in allowed and "value" in child.attrib
    }


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _float(value: str | None) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _boolean(value: str | None) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _selection_digest(case_ref: str, event_ref: str, occurred_at: str) -> str:
    return object_digest(
        {
            "source_sha256": f"sha256:{EXPECTED_SHA256}",
            "task_contract": "BPI2019_CHANGE_PRICE_30D_DOWNSTREAM_V1",
            "case_ref": case_ref,
            "change_event_ref": event_ref,
            "change_occurred_at": occurred_at,
        }
    )


def _event_ref(trace_index: int, event_index: int) -> str:
    return f"bpi2019:trace:{trace_index:06d}:event:{event_index:03d}"


def _prepare_index(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.executescript(
        """
        CREATE TABLE query_candidate (
          selection_digest TEXT NOT NULL,
          case_ref TEXT NOT NULL,
          trace_index INTEGER NOT NULL,
          company_ref TEXT NOT NULL,
          vendor_ref TEXT NOT NULL,
          purchase_document_ref TEXT NOT NULL,
          item_ref TEXT NOT NULL,
          item_category TEXT NOT NULL,
          source_system_ref TEXT NOT NULL,
          document_type TEXT NOT NULL,
          purchasing_category TEXT NOT NULL,
          item_type TEXT NOT NULL,
          gr_based_invoice_verification INTEGER,
          goods_receipt_expected INTEGER,
          change_event_ref TEXT NOT NULL,
          change_occurred_at TEXT NOT NULL,
          change_epoch REAL NOT NULL,
          change_actor_ref TEXT NOT NULL,
          change_cumulative_value REAL
        );
        CREATE TABLE target_event (
          event_ref TEXT PRIMARY KEY,
          case_ref TEXT NOT NULL,
          trace_index INTEGER NOT NULL,
          event_index INTEGER NOT NULL,
          company_ref TEXT NOT NULL,
          vendor_ref TEXT NOT NULL,
          purchase_document_ref TEXT NOT NULL,
          item_ref TEXT NOT NULL,
          event_type TEXT NOT NULL,
          occurred_at TEXT NOT NULL,
          occurred_epoch REAL NOT NULL,
          actor_ref TEXT NOT NULL,
          cumulative_value REAL
        );
        CREATE INDEX target_vendor_time ON target_event(vendor_ref, occurred_epoch, event_ref);
        """
    )
    return connection


def _stream_to_index(source: Path, connection: sqlite3.Connection, *, progress: bool) -> dict[str, Any]:
    trace_count = 0
    event_count = 0
    invalid_timestamp_count = 0
    missing_identity_sentinel_events = 0
    activities: set[str] = set()
    identities: set[str] = set()
    query_candidate_count = 0

    for _, trace in ET.iterparse(source, events=("end",)):
        if _local_name(trace.tag) != "trace":
            continue
        trace_count += 1
        trace_values = _attributes(trace, TRACE_KEYS)
        case_ref = trace_values.get("concept:name", "")
        purchase_document_ref = trace_values.get("Purchasing Document", "")
        item_ref = trace_values.get("Item", "")
        vendor_ref = trace_values.get("Vendor", "")
        company_ref = trace_values.get("Company", "")
        source_system_ref = trace_values.get("Source", "")
        complete_trace_identity = all(
            (case_ref, purchase_document_ref, item_ref, vendor_ref, company_ref, source_system_ref)
        )
        parsed_events: list[dict[str, Any]] = []
        for event_index, event in enumerate(
            (child for child in trace if _local_name(child.tag) == "event"), start=1
        ):
            event_count += 1
            values = _event_attributes(event)
            activity = values.get("concept:name", "")
            actor = values.get("org:resource") or values.get("User") or ""
            if activity:
                activities.add(activity)
            if actor and actor not in {"NONE", "UNKNOWN"}:
                identities.add(actor)
            elif actor in {"NONE", "UNKNOWN"}:
                missing_identity_sentinel_events += 1
            occurred = _parse_timestamp(values.get("time:timestamp"))
            if occurred is None:
                invalid_timestamp_count += 1
            parsed_events.append(
                {
                    "event_ref": _event_ref(trace_count, event_index),
                    "event_index": event_index,
                    "event_type": activity,
                    "occurred": occurred,
                    "actor_ref": actor,
                    "cumulative_value": _float(values.get("Cumulative net worth (EUR)")),
                }
            )

        if complete_trace_identity:
            targets = [
                event
                for event in parsed_events
                if event["event_type"] in TARGET_EVENT_TYPES and event["occurred"] is not None
            ]
            connection.executemany(
                """
                INSERT INTO target_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        event["event_ref"],
                        case_ref,
                        trace_count,
                        event["event_index"],
                        company_ref,
                        vendor_ref,
                        purchase_document_ref,
                        item_ref,
                        event["event_type"],
                        _timestamp(event["occurred"]),
                        event["occurred"].timestamp(),
                        event["actor_ref"],
                        event["cumulative_value"],
                    )
                    for event in targets
                ],
            )
            changes = [
                event
                for event in parsed_events
                if event["event_type"] == CHANGE_EVENT_TYPE and event["occurred"] is not None
            ]
            if changes:
                change = max(changes, key=lambda row: (row["occurred"], row["event_index"]))
                window_end = change["occurred"] + timedelta(days=WINDOW_DAYS)
                has_ground_truth_target = any(
                    change["occurred"] < target["occurred"] <= window_end for target in targets
                )
                if has_ground_truth_target:
                    change_at = _timestamp(change["occurred"])
                    connection.execute(
                        "INSERT INTO query_candidate VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            _selection_digest(case_ref, change["event_ref"], change_at),
                            case_ref,
                            trace_count,
                            company_ref,
                            vendor_ref,
                            purchase_document_ref,
                            item_ref,
                            trace_values.get("Item Category", ""),
                            source_system_ref,
                            trace_values.get("Document Type", ""),
                            trace_values.get("Purch. Doc. Category name", ""),
                            trace_values.get("Item Type", ""),
                            _boolean(trace_values.get("GR-Based Inv. Verif.")),
                            _boolean(trace_values.get("Goods Receipt")),
                            change["event_ref"],
                            change_at,
                            change["occurred"].timestamp(),
                            change["actor_ref"],
                            change["cumulative_value"],
                        ),
                    )
                    query_candidate_count += 1

        trace.clear()
        if trace_count % 5_000 == 0:
            connection.commit()
        if progress and trace_count % 25_000 == 0:
            print(
                f"BPI2019_STREAM traces={trace_count} events={event_count} candidates={query_candidate_count}",
                file=sys.stderr,
                flush=True,
            )

    connection.commit()
    if trace_count != OFFICIAL_TRACE_COUNT:
        raise ValueError(f"OFFICIAL_TRACE_COUNT_DRIFT:{trace_count}")
    if event_count != OFFICIAL_EVENT_COUNT:
        raise ValueError(f"OFFICIAL_EVENT_COUNT_DRIFT:{event_count}")
    if len(activities) != OFFICIAL_ACTIVITY_COUNT:
        raise ValueError(f"OFFICIAL_ACTIVITY_COUNT_DRIFT:{len(activities)}")
    if len(identities) != OFFICIAL_IDENTITY_COUNT:
        raise ValueError(f"OFFICIAL_IDENTITY_COUNT_DRIFT:{len(identities)}")
    return {
        "traces_scanned": trace_count,
        "events_scanned": event_count,
        "activity_types_observed": len(activities),
        "anonymous_identities_observed": len(identities),
        "missing_identity_sentinel_events": missing_identity_sentinel_events,
        "invalid_event_timestamps": invalid_timestamp_count,
        "change_queries_with_real_downstream_targets": query_candidate_count,
    }


def _target_rows(
    connection: sqlite3.Connection,
    *,
    vendor_ref: str,
    change_epoch: float,
    window_end_epoch: float,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT * FROM target_event
        WHERE vendor_ref = ? AND occurred_epoch > ? AND occurred_epoch <= ?
        ORDER BY occurred_epoch, event_ref
        """,
        (vendor_ref, change_epoch, window_end_epoch),
    ).fetchall()


def _materialize_projection(
    connection: sqlite3.Connection,
    source_receipt: dict[str, object],
    scan: dict[str, Any],
) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    selected: list[dict[str, Any]] = []
    observed_events: dict[str, dict[str, Any]] = {}
    eligible_seen = 0
    candidate_rows = connection.execute(
        "SELECT * FROM query_candidate ORDER BY selection_digest, case_ref, change_event_ref"
    ).fetchall()
    for query in candidate_rows:
        window_end_epoch = query["change_epoch"] + WINDOW_DAYS * 24 * 60 * 60
        targets = _target_rows(
            connection,
            vendor_ref=query["vendor_ref"],
            change_epoch=query["change_epoch"],
            window_end_epoch=window_end_epoch,
        )
        exact_targets = [
            target
            for target in targets
            if target["purchase_document_ref"] == query["purchase_document_ref"]
            and target["item_ref"] == query["item_ref"]
        ]
        if not exact_targets or len(targets) <= len(exact_targets):
            continue
        eligible_seen += 1
        if len(selected) >= MAX_QUERIES:
            continue
        candidate_refs: list[str] = []
        for target in targets:
            event_ref = target["event_ref"]
            candidate_refs.append(event_ref)
            observed_events[event_ref] = {
                "event_ref": event_ref,
                "case_ref": target["case_ref"],
                "company_ref": target["company_ref"],
                "vendor_ref": target["vendor_ref"],
                "purchase_document_ref": target["purchase_document_ref"],
                "item_ref": target["item_ref"],
                "event_type": target["event_type"],
                "occurred_at": target["occurred_at"],
                "actor_ref": target["actor_ref"],
                "cumulative_net_worth_eur": target["cumulative_value"],
                "evidence_class": "OBSERVED_DOWNSTREAM_EVENT_PROXY",
                "lineage": {
                    "source_sha256": f"sha256:{EXPECTED_SHA256}",
                    "trace_index": target["trace_index"],
                    "event_index": target["event_index"],
                },
            }
        selected.append(
            {
                "query_id": f"bpi2019-price-review-{len(selected) + 1:03d}",
                "selection_digest": query["selection_digest"],
                "case_ref": query["case_ref"],
                "company_ref": query["company_ref"],
                "vendor_ref": query["vendor_ref"],
                "purchase_document_ref": query["purchase_document_ref"],
                "item_ref": query["item_ref"],
                "item_category": query["item_category"],
                "source_system_ref": query["source_system_ref"],
                "document_type": query["document_type"],
                "purchasing_category": query["purchasing_category"],
                "item_type": query["item_type"],
                "gr_based_invoice_verification": (
                    None
                    if query["gr_based_invoice_verification"] is None
                    else bool(query["gr_based_invoice_verification"])
                ),
                "goods_receipt_expected": (
                    None if query["goods_receipt_expected"] is None else bool(query["goods_receipt_expected"])
                ),
                "change_event": {
                    "event_ref": query["change_event_ref"],
                    "event_type": CHANGE_EVENT_TYPE,
                    "occurred_at": query["change_occurred_at"],
                    "actor_ref": query["change_actor_ref"],
                    "cumulative_net_worth_eur": query["change_cumulative_value"],
                    "evidence_class": "PUBLIC_REAL_ANONYMIZED_ENTERPRISE_EVENT_LOG",
                    "lineage": {
                        "source_sha256": f"sha256:{EXPECTED_SHA256}",
                        "trace_index": query["trace_index"],
                        "event_index": int(query["change_event_ref"].rsplit(":", 1)[-1]),
                    },
                },
                "window_end": _timestamp(datetime.fromtimestamp(window_end_epoch, tz=UTC)),
                "candidate_event_refs": candidate_refs,
            }
        )

    if len(selected) != MAX_QUERIES:
        raise ValueError(f"SELECTED_QUERY_COUNT_DRIFT:{len(selected)}")
    return seal(
        {
            "schema_version": PROJECTION_SCHEMA,
            "projection_id": "bpi2019-change-price-30d-real-process-v1",
            "data_domain": "PURCHASE_TO_PAY",
            "evidence_class": "PUBLIC_REAL_ANONYMIZED_ENTERPRISE_EVENT_LOG",
            "claim_ceiling": "PUBLIC_REAL_PROCESS_RULE_DERIVED_MECHANISM_VALIDATION",
            "claim_boundary": "NOT_REAL_QUOTE_DATA_NOT_CAUSAL_GROUND_TRUTH_NOT_REALIZED_ROI_NOT_PRODUCTION",
            "upstream": source_receipt,
            "task_contract": {
                "task_id": "BPI2019_CHANGE_PRICE_30D_DOWNSTREAM_V1",
                "change_event_type": CHANGE_EVENT_TYPE,
                "window_days": WINDOW_DAYS,
                "downstream_event_types": list(TARGET_EVENT_TYPES),
                "candidate_universe": "SAME_VENDOR_EVENTS_AFTER_CHANGE_THROUGH_WINDOW_END",
                "query_ground_truth": "SAME_PURCHASE_DOCUMENT_AND_ITEM_EVENTS_IN_CANDIDATE_UNIVERSE",
                "query_ground_truth_available_to_strategy": False,
                "selection_rule": (
                    "HAS_EXACT_ITEM_DOWNSTREAM_EVENT_AND_VENDOR_SCOPE_HAS_EXTRA_EVENT_THEN_"
                    "ORDER_BY_SELECTION_DIGEST_TAKE_FIRST_128"
                ),
            },
            "provenance_legend": [
                "PUBLIC_REAL_ANONYMIZED_ENTERPRISE_EVENT_LOG",
                "TASK_DEFINED_QUERY_GROUND_TRUTH",
                "OBSERVED_DOWNSTREAM_EVENT_PROXY",
                "PROVIDER_DOCUMENTED_RULE_DERIVED",
            ],
            "selection": {
                **scan,
                "eligible_queries": eligible_seen,
                "selected_queries": len(selected),
                "maximum_queries": MAX_QUERIES,
                "ordering": "SHA256_PINNED_SOURCE_TASK_CASE_AND_LAST_CHANGE",
                "outcome_blind_ordering": True,
            },
            "queries": selected,
            "observed_events": sorted(observed_events.values(), key=lambda row: row["event_ref"]),
        }
    )


def build_projection(source: Path, *, work_dir: Path | None = None, progress: bool = False) -> dict[str, Any]:
    """Verify and stream the official source into a deterministic retained slice."""

    source_receipt = verify_source(source)
    owned_temp = work_dir is None
    resolved_work_dir = Path(tempfile.mkdtemp(prefix="orgrebase-bpi2019-")) if owned_temp else work_dir
    assert resolved_work_dir is not None
    resolved_work_dir.mkdir(parents=True, exist_ok=True)
    index_path = resolved_work_dir / "bpi2019-projection-index.sqlite"
    if index_path.exists():
        index_path.unlink()
    connection = _prepare_index(index_path)
    try:
        scan = _stream_to_index(source, connection, progress=progress)
        return _materialize_projection(connection, source_receipt, scan)
    finally:
        connection.close()
        if index_path.exists():
            index_path.unlink()
        if owned_temp:
            resolved_work_dir.rmdir()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_benchmark(projection: dict[str, Any], benchmark_root: Path) -> None:
    """Write the projection plus attribution and content-closure manifests."""

    projection_path = benchmark_root / PROJECTION_FILE
    _write_json(projection_path, projection)
    licenses = seal(
        {
            "schema_version": LICENSE_MANIFEST_SCHEMA,
            "upstream": {
                "dataset_id": "bpi-challenge-2019-4tu-12715853",
                "title": "BPI Challenge 2019",
                "publisher": "4TU.ResearchData",
                "doi": DOI,
                "record_url": OFFICIAL_RECORD,
                "spdx": "CC-BY-4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "included_material": (
                    "A deterministic transformed event projection with source attribution; "
                    "the raw XES bytes are not redistributed."
                ),
                "changes": (
                    "Extracted real Change Price and downstream event observations into a fixed "
                    "query slice; no affected labels or causal annotations were added."
                ),
            },
            "runtime_dependencies": [
                {
                    "name": "Python standard library",
                    "purpose": "streaming XES parsing, temporary SQLite indexing, hashing and JSON output",
                    "bundled": False,
                }
            ],
            "redistribution_boundary": (
                "Retain attribution and CC BY 4.0 terms with the projection; do not package the raw XES."
            ),
        }
    )
    _write_json(benchmark_root / "LICENSES.json", licenses)
    dataset_manifest = seal(
        {
            "schema_version": DATASET_MANIFEST_SCHEMA,
            "dataset_id": "bpi-challenge-2019-4tu-12715853",
            "dataset_kind": "REAL_ANONYMIZED_ENTERPRISE_PURCHASE_TO_PAY_EVENT_LOG",
            "title": "BPI Challenge 2019",
            "organization_profile": "LARGE_MULTINATIONAL_COATINGS_AND_PAINT_COMPANY",
            "source_description": (
                "Anonymized real SAP purchase-to-pay event log from a large multinational "
                "coatings and paints company."
            ),
            "official_record": OFFICIAL_RECORD,
            "doi": DOI,
            "artifact": {
                "file_name": FILE_NAME,
                "file_id": FILE_ID,
                "download_url": SOURCE_URL,
                "size_bytes": 728_558_522,
                "md5": "md5:4eb909242351193a61e1c15b9c3cc814",
                "sha256": f"sha256:{EXPECTED_SHA256}",
                "raw_bytes_in_repository": False,
            },
            "official_counts": {
                "purchase_order_items": OFFICIAL_TRACE_COUNT,
                "events": OFFICIAL_EVENT_COUNT,
                "activity_types": OFFICIAL_ACTIVITY_COUNT,
                "anonymous_identities": OFFICIAL_IDENTITY_COUNT,
            },
            "license": "CC-BY-4.0",
            "projection_path": PROJECTION_FILE,
            "projection_digest": projection["digest"],
            "license_manifest_path": "LICENSES.json",
            "license_manifest_digest": licenses["digest"],
            "reproduction": {
                "fetch_command": "uv run python scripts/data_adapters/fetch_bpi_challenge_2019.py",
                "projection_command": (
                    "uv run python scripts/data_adapters/build_bpi2019_real_process_projection.py "
                    "--source /tmp/orgrebase-bpi2019-raw.xes"
                ),
                "offline_replay_command": "uv run python scripts/run_bpi2019_real_process_benchmark.py",
                "offline_verification_command": (
                    "uv run python scripts/verify_bpi2019_real_process_benchmark.py"
                ),
            },
            "evidence_class": "PUBLIC_REAL_ANONYMIZED_ENTERPRISE_EVENT_LOG",
            "claim_boundary": "REAL_P2P_PROCESS_NOT_REAL_QUOTE_DATA_OR_CAUSAL_GROUND_TRUTH_OR_ROI",
        }
    )
    _write_json(benchmark_root / "dataset-manifest.json", dataset_manifest)
    manifest_relatives = ("LICENSES.json", "dataset-manifest.json", PROJECTION_FILE)
    (benchmark_root / "MANIFEST.sha256").write_text(
        "".join(
            f"{_file_sha256(benchmark_root / relative)}  {relative}\n"
            for relative in manifest_relatives
        ),
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument(
        "--benchmark-root",
        type=Path,
        default=root / "benchmark" / "quote-value-v0.4-bpi-real-process",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        projection = build_projection(
            args.source.resolve(),
            work_dir=args.work_dir.resolve() if args.work_dir else None,
            progress=not args.quiet,
        )
        write_benchmark(projection, args.benchmark_root.resolve())
    except (ET.ParseError, OSError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(
        json.dumps(
            {
                "status": "PASS",
                "projection_digest": projection["digest"],
                "selected_queries": projection["selection"]["selected_queries"],
                "observed_events": len(projection["observed_events"]),
                "benchmark_root": str(args.benchmark_root.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
