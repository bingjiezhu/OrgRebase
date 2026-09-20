"""Replay preserved public P2P observations through a controlled HTTPS connector.

The OData envelope, UUIDs, network and faults are synthetic. Original observed
event objects are preserved. This is neither a Dataverse service qualification
nor a production capacity, quotation accuracy or customer value benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import platform
import sqlite3
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import NAMESPACE_URL, uuid5

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.store import StateStore
from orgrebase.workspace.dataverse import (
    DataverseReader,
    DataverseSettings,
    SourceRateLimited,
    SourceSynchronizer,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "benchmark/quote-value-v0.4-bpi-real-process"
MANIFEST_SHA256 = "6cb78fd9d7f618dd7e9d94febd03d2ec19cb4c1791e277cae90fef29d0ed2c45"
TENANT = "public-replay"
ADMISSION_DIGEST = sha256_digest({"mapping": "public-event-as-opaque-artifact-v1"})


def select_events(dataset: Path, count: int) -> tuple[list[dict], dict]:
    """Validate the pinned manifest and all its files before selecting anything."""
    manifest = (dataset / "MANIFEST.sha256").read_bytes()
    if hashlib.sha256(manifest).hexdigest() != MANIFEST_SHA256:
        raise ValueError("FROZEN_MANIFEST_CHANGED")
    for line in manifest.decode().splitlines():
        digest, relative = line.split("  ", 1)
        path = (dataset / relative).resolve()
        if not path.is_relative_to(dataset.resolve()):
            raise ValueError("MANIFEST_PATH_ESCAPE")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"FROZEN_INPUT_CHANGED:{relative}")
    projection = json.loads((dataset / "projection/bpi2019-real-process-projection.json").read_bytes())
    events = projection["observed_events"]
    if not 2 <= count <= len(events):
        raise ValueError("SAMPLE_COUNT_OUT_OF_RANGE")
    if len({event["event_ref"] for event in events}) != len(events):
        raise ValueError("DUPLICATE_SOURCE_EVENT")
    selected = sorted(events, key=lambda event: (sha256_digest(event), event["event_ref"]))[:count]
    return selected, {
        "manifest_sha256": MANIFEST_SHA256,
        "source": projection["upstream"], "available_events": len(events),
        "selection": "SHA256_OF_COMPLETE_EVENT_THEN_EVENT_REF_ASCENDING",
        "selection_bias": projection["task_contract"]["selection_rule"],
        "sample_digest": sha256_digest(selected), "sample_size": count,
        "representativeness": "A_SUBSAMPLE_OF_A_BIASED_128_QUERY_PROJECTION_NOT_THE_FULL_LOG",
    }


def protocol_record(event: dict) -> dict:
    return {
        "quoteid": str(uuid5(NAMESPACE_URL, "orgrebase-public-replay:" + event["event_ref"])),
        "@odata.etag": 'W/"' + sha256_digest(event).split(":", 1)[1] + '"',
        "modifiedon": event["occurred_at"], "new_observed_event": canonical_json(event),
    }


def create_certificate(directory: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Public replay loopback CA")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert, private = directory / "loopback-ca.pem", directory / "loopback-key.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    private.chmod(0o600)
    return cert, private


@contextmanager
def serve_records(groups: dict[str, list[dict]], directory: Path, page_size: int):
    cert, private = create_certificate(directory)
    counts: dict[str, int] = {}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            with lock:
                counts[token] = counts.get(token, 0) + 1
                request_number = counts[token]
            if token not in groups or urlsplit(self.path).path != "/api/data/v9.2/quotes":
                self.send_error(404)
                return
            if token == "rate-limit" and request_number == 1:
                self.send_response(429)
                self.send_header("Retry-After", "2")
                self.end_headers()
                return
            offset = int(parse_qs(urlsplit(self.path).query).get("offset", ["0"])[0])
            rows = groups[token][offset:offset + page_size]
            more = offset + page_size < len(groups[token])
            cursor = f"{origin}/api/data/v9.2/quotes?offset={offset + page_size}"
            document = {"value": rows, "@odata.nextLink" if more else "@odata.deltaLink": cursor}
            payload = json.dumps(document, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, private)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    origin = f"https://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield origin, cert, counts
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def synchronizer(store, origin, certificate, token, rows, page_size, *, fail_after=None):
    settings = DataverseSettings(
        connector_id=token, tenant_id=TENANT, instance_url=origin,
        record_ids=tuple(row["quoteid"] for row in rows), fields=("new_observed_event",),
        page_size=page_size, ca_bundle=str(certificate),
    )
    admitted = 0

    def admit(connection, record, _observed_at, _page_id):
        nonlocal admitted
        admitted += 1
        if fail_after is not None and admitted == fail_after:
            raise RuntimeError("INJECTED_ADMISSION_FAILURE")
        event = json.loads(record["fields"]["new_observed_event"])
        expected = protocol_record(event)
        if record["record_id"] != expected["quoteid"] or record["revision"] != expected["@odata.etag"]:
            raise ValueError("PROTOCOL_IDENTITY_MISMATCH")
        store.save_artifact(connection, "observed:" + event["event_ref"], "application/json", event)

    def committed(connection, inbox, checkpoint, page_id):
        store.append_event(connection, "public_connector_replay_page", {
            "connector_id": token, "page_ref": page_id, "revision": checkpoint["revision"],
            "records": len(inbox["records"]), "records_digest": sha256_digest(inbox["records"]),
        })

    return SourceSynchronizer(store, DataverseReader(settings, lambda: token), worker_id=token,
                              admit=admit, admission_digest=ADMISSION_DIGEST, page_committed=committed)


def verify_records(store: StateStore, events: list[dict]) -> dict:
    for event in events:
        loaded = store.load_artifact("observed:" + event["event_ref"], "application/json")
        if canonical_json(loaded.payload) != canonical_json(event) or loaded.payload_digest != sha256_digest(event):
            raise AssertionError("OBSERVED_CONTENT_CHANGED")
    count = store.connection.execute("SELECT COUNT(*) FROM artifacts WHERE artifact_id LIKE 'observed:%'").fetchone()[0]
    if count != len(events):
        raise AssertionError("MISSING_OR_EXTRA_OBSERVATIONS")
    return {"records_verified": count, "losses": 0, "extra_or_duplicate_rows": 0,
            "content_mismatches": 0, "audit_chain": store.verify_event_chain()}


def run_workload(events: list[dict], directory: Path, workers: int, partition_size: int, page_size: int) -> dict:
    directory.mkdir()
    groups = {f"partition-{index // partition_size:04d}": [protocol_record(e) for e in events[index:index + partition_size]]
              for index in range(0, len(events), partition_size)}
    database = directory / "replay.sqlite"
    StateStore(database, tenant_id=TENANT).close()
    with serve_records(groups, directory, page_size) as (origin, certificate, counts):
        def consume(item):
            token, rows = item
            timings = []
            with StateStore(database, tenant_id=TENANT, migrate=False) as store:
                sync = synchronizer(store, origin, certificate, token, rows, page_size)
                while True:
                    start = time.perf_counter()
                    result = sync.sync_page()
                    timings.append({"connector_id": token, "revision": result["revision"],
                                    "records": result["records_admitted"],
                                    "sync_page_seconds": time.perf_counter() - start})
                    if not result["more"]:
                        return timings

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            pages = [page for partition in executor.map(consume, groups.items()) for page in partition]
        elapsed = time.perf_counter() - started
        requests = dict(counts)
    with StateStore(database, tenant_id=TENANT, migrate=False) as store:
        verified = verify_records(store, events)
    return {"workers": workers, "sample_digest": sha256_digest(events), "partitions": len(groups),
            "partition_parallelism_upper_bound": min(workers, len(groups)),
            "wall_seconds": elapsed, "observed_records_per_second": len(events) / elapsed,
            "timing_scope": "EXECUTOR_QUEUE_CONNECTION_OPEN_FETCH_PARSE_INBOX_ADMIT_AUDIT_COMMIT_AND_JOIN",
            "excluded_from_timing": ["source_selection", "database_schema_creation", "TLS_server_startup", "final_readback"],
            "page_timings": pages, "http_requests": requests, **verified}


def run_faults(events: list[dict], directory: Path) -> dict:
    directory.mkdir()
    events = events[:3]
    rows = [protocol_record(e) for e in events]
    groups = {"rate-limit": rows, "admission-failure": rows}
    results = {}
    with serve_records(groups, directory, len(rows)) as (origin, certificate, counts):
        for token in groups:
            database = directory / f"{token}.sqlite"
            with StateStore(database, tenant_id=TENANT) as store:
                sync = synchronizer(store, origin, certificate, token, rows, len(rows),
                                    fail_after=2 if token == "admission-failure" else None)
                try:
                    sync.sync_page()
                except SourceRateLimited as error:
                    if token != "rate-limit" or error.retry_after != 2:
                        raise
                except RuntimeError as error:
                    if token != "admission-failure" or str(error) != "INJECTED_ADMISSION_FAILURE":
                        raise
                else:
                    raise AssertionError("INJECTED_FAULT_DID_NOT_FAIL")
                checkpoint = store.get_source_checkpoint(token)
                if checkpoint["cursor"] is not None or checkpoint["revision"] != 0:
                    raise AssertionError("FAILED_PAGE_ADVANCED_CURSOR")
                for event in events:
                    try:
                        store.load_artifact("observed:" + event["event_ref"], "application/json")
                    except KeyError:
                        continue
                    raise AssertionError("PARTIAL_ADMISSION_COMMITTED")
                if store.verify_event_chain()["events"] != 0:
                    raise AssertionError("FAILED_PAGE_COMMITTED_AUDIT")
            before = counts[token]
            if token == "rate-limit":
                time.sleep(2)  # Honor the injected Retry-After; fault work is outside throughput timing.
            with StateStore(database, tenant_id=TENANT, migrate=False) as reopened:
                sync = synchronizer(reopened, origin, certificate, token, rows, len(rows))
                sync.sync_page()
                verified = verify_records(reopened, events)
            requests_added = counts[token] - before
            if requests_added != (1 if token == "rate-limit" else 0):
                raise AssertionError("DURABLE_INBOX_REFETCHED_OR_RETRY_MISSING")
            results[token] = {"status": "PASS", "failed_page_cursor_unchanged": True,
                              "partial_artifacts_rolled_back": True, "store_reopened": True,
                              "recovery_http_requests": requests_added, **verified}
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 4, 8])
    parser.add_argument("--partition-size", type=int, default=250)
    parser.add_argument("--page-size", type=int, default=100)
    args = parser.parse_args()
    if not 2 <= args.partition_size <= 1000 or not 1 <= args.page_size <= 1000:
        parser.error("partition size must be 2..1000 and page size 1..1000")
    if not args.workers or len(set(args.workers)) != len(args.workers) or any(w < 1 or w > 16 for w in args.workers):
        parser.error("workers must be distinct integers in 1..16")
    output = args.output.resolve()
    if any(output.is_relative_to(ROOT / protected) for protected in ("benchmark", "evidence")):
        parser.error("output must be outside frozen benchmark and evidence directories")
    events, provenance = select_events(DATASET, args.sample_size)
    output.mkdir(parents=True, exist_ok=False)
    (output / "selected-events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2) + "\n")
    report = {"status": "PASS", "provenance": provenance,
              "environment": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                              "platform": platform.platform(), "logical_cpus": os.cpu_count()},
              "worker_configuration_order": args.workers,
              "arrival_model": "SATURATED_DIGEST_ORDER_REPLAY_NOT_OBSERVED_INTERARRIVAL_TIMES",
              "measurement_limits": "SINGLE_TRIAL_PER_CONFIGURATION_NO_MAXIMUM_CAPACITY_OR_SCALING_CLAIM",
              "claim_boundary": "CONTROLLED_LOOPBACK_TLS_SQLITE_CONNECTOR_REPLAY_NOT_PRODUCTION_CAPACITY_OR_CUSTOMER_VALUE",
              "synthetic_elements": ["UUID5 protocol IDs", "OData envelope with canonical event JSON in a text field", "loopback HTTPS source", "Bearer token as partition selector, no OAuth validation", "fault injection"],
              "preserved_elements": "Complete original observed event objects, including source lineage and event/case/document/item/time/value fields",
              "business_apply_calls": 0, "external_enterprise_writes": 0, "native_agentteams_runs": 0,
              "workloads": [], "faults": {}}
    for workers in args.workers:
        result = run_workload(events, output / f"workers-{workers}", workers, args.partition_size, args.page_size)
        report["workloads"].append(result)
        print(json.dumps({key: result[key] for key in ("workers", "wall_seconds", "records_verified")}), flush=True)
    report["faults"] = run_faults(events, output / "faults")
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "PASS", "report": str(output / "report.json")}), flush=True)


if __name__ == "__main__":
    main()
