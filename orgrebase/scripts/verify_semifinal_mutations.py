#!/usr/bin/env python3
"""Prove that semantic substitutions cannot survive a rehashed evidence pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _rehash_record(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    body = {key: item for key, item in value.items() if key != "digest"}
    result = {**body, "digest": _digest(body)}
    _write(path, result)
    return result


def _rehash_index(root: Path) -> dict[str, Any]:
    index_path = root / "evidence-index.json"
    old = _load(index_path)
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _file_digest(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != index_path
    ]
    body = {
        "schema_version": old["schema_version"],
        "entry_count": len(entries),
        "entries": entries,
        "pack_digest": _digest(entries),
    }
    index = {**body, "digest": _digest(body)}
    _write(index_path, index)
    return index


def _bind_changed_operations_pack(root: Path) -> None:
    operations_index = _rehash_index(root / "operations")
    child_path = root / "verification/child-verifiers.json"
    child = _load(child_path)
    child["operations"]["pack_digest"] = operations_index["pack_digest"]
    _write(child_path, child)
    _rehash_index(root)


def _mutate_tool_result(root: Path) -> None:
    _write(
        root / "operations/tool/result.json",
        {
            "schema_version": "attacker.tool-result.v1",
            "dependencies": ["poisoned:policy"],
            "run_id": "run:foreign",
            "target_writes": 999,
        },
    )
    _bind_changed_operations_pack(root)


def _mutate_authority_roots(root: Path) -> None:
    operations_path = root / "operations/summary.json"
    operations = _load(operations_path)
    foreign = "sha256:" + "f" * 64
    operations["native_receipt_digest"] = foreign
    operations["skill_invocation_receipt_digest"] = foreign
    operations["correlation_root"] = foreign
    operations = _rehash_record(operations_path, operations)

    summary_path = root / "summary.json"
    summary = _load(summary_path)
    summary["operations_summary_digest"] = operations["digest"]
    _rehash_record(summary_path, summary)
    _bind_changed_operations_pack(root)


def _mutate_coalition_domain_result(root: Path) -> None:
    """Substitute one domain root, then recompute the coalition and parent index."""

    path = root / "agentteams/coalition-result-binding.json"
    coalition = _load(path)
    members = coalition.get("members")
    if not isinstance(members, list):
        raise RuntimeError("COALITION_MEMBERS_REQUIRED")
    legal = [
        item
        for item in members
        if isinstance(item, dict) and item.get("domain") == "legal"
    ]
    if len(legal) != 1:
        raise RuntimeError("EXACT_LEGAL_COALITION_MEMBER_REQUIRED")
    legal[0]["observed_result_digest"] = _digest(
        {
            "attack": "coalition-domain-result-substitution",
            "original": legal[0].get("observed_result_digest"),
        }
    )
    body = {
        key: value for key, value in coalition.items() if key != "coalition_digest"
    }
    coalition["coalition_digest"] = _digest(body)
    _write(path, coalition)
    _rehash_index(root)


def _trace_spans(root: Path) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    path = root / "operations/observability/traces.otlp.json"
    traces = _load(path)
    try:
        spans = traces["resourceSpans"][0]["scopeSpans"][0]["spans"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("CAUSAL_TRACE_SPANS_REQUIRED") from exc
    if not isinstance(spans, list) or len(spans) != 5 or not all(
        isinstance(item, dict) for item in spans
    ):
        raise RuntimeError("EXACT_FIVE_CAUSAL_SPANS_REQUIRED")
    return path, traces, spans


def _set_string_attribute(node: dict[str, Any], key: str, value: str) -> None:
    attributes = node.get("attributes")
    if not isinstance(attributes, list):
        raise RuntimeError("OTLP_ATTRIBUTES_REQUIRED")
    matches = [
        item
        for item in attributes
        if isinstance(item, dict) and item.get("key") == key
    ]
    if len(matches) != 1:
        raise RuntimeError(f"EXACT_OTLP_ATTRIBUTE_REQUIRED:{key}")
    matches[0]["value"] = {"stringValue": value}


def _mutate_otlp_causal_order(root: Path) -> None:
    path, traces, spans = _trace_spans(root)
    spans[1], spans[2] = spans[2], spans[1]
    _write(path, traces)
    _bind_changed_operations_pack(root)


def _mutate_otlp_parent(root: Path) -> None:
    path, traces, spans = _trace_spans(root)
    spans[3]["parentSpanId"] = spans[0].get("spanId")
    _write(path, traces)
    _bind_changed_operations_pack(root)


def _mutate_otlp_mixed_status(root: Path) -> None:
    """Make trace TOOL fail while the retained log still reports success."""

    path, traces, spans = _trace_spans(root)
    tool = spans[2]
    _set_string_attribute(tool, "orgrebase.chain.status", "FAILED")
    tool["status"] = {"code": 2}
    _write(path, traces)
    _bind_changed_operations_pack(root)


def _mutate_telemetry_privacy(root: Path) -> None:
    database = root / "operations/observability/telemetry.sqlite"
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT id,payload_json FROM otlp_ingestions ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("TELEMETRY_ROW_REQUIRED")
        payload = json.loads(row[1])
        payload["raw_prompt"] = "CONFIDENTIAL CUSTOMER TERMS"
        connection.execute(
            "UPDATE otlp_ingestions SET payload_json=? WHERE id=?",
            (json.dumps(payload, sort_keys=True), row[0]),
        )
        connection.commit()
    finally:
        connection.close()
    _bind_changed_operations_pack(root)


def _mutate_public_host_path(root: Path) -> None:
    _write(
        root / "agentteams/publication-note.json",
        {
            "schema_version": "attacker.publication-note.v1",
            "workspace_dir": "/" + "Users/attacker/private/semifinal-stage",
        },
    )
    _rehash_index(root)


def _mutate_unreviewed_publication_artifact(root: Path) -> None:
    artifact = root / "artifacts/unreviewed.bin"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"unreviewed-publication-artifact\n")
    _rehash_index(root)


ATTACKS: tuple[tuple[str, Callable[[Path], None], tuple[str, ...]], ...] = (
    (
        "coalition-domain-result-substitution",
        _mutate_coalition_domain_result,
        ("COALITION_NATIVE_RECONSTRUCTION",),
    ),
    (
        "otlp-causal-order-substitution",
        _mutate_otlp_causal_order,
        ("OTLP_CAUSAL_ORDER", "OTLP_CAUSAL_PARENT"),
    ),
    (
        "otlp-parent-substitution",
        _mutate_otlp_parent,
        ("OTLP_CAUSAL_PARENT",),
    ),
    (
        "otlp-mixed-status-substitution",
        _mutate_otlp_mixed_status,
        ("OTLP_CAUSAL_ORDER", "OTLP_LAYER_STATUS_BINDING"),
    ),
    (
        "tool-result-substitution",
        _mutate_tool_result,
        ("TOOL_RESULT_SKILL_BINDING", "TOOL_RECEIPT", "TOOL_RESULT"),
    ),
    (
        "operations-authority-root-substitution",
        _mutate_authority_roots,
        ("OPERATIONS_AUTHORITY_ROOT_BINDING", "CORRELATION_ROOT"),
    ),
    (
        "telemetry-privacy-substitution",
        _mutate_telemetry_privacy,
        ("TELEMETRY_DATABASE_DIGEST", "TELEMETRY_DATABASE_PRIVACY"),
    ),
    (
        "public-host-path-substitution",
        _mutate_public_host_path,
        ("PUBLIC_HOST_PATH",),
    ),
    (
        "unreviewed-publication-artifact",
        _mutate_unreviewed_publication_artifact,
        ("PUBLICATION_SURFACE_EXTRA:artifacts/unreviewed.bin",),
    ),
)


def verify_mutations(
    evidence: Path, *, checkout: Path | None = None, lock_path: Path | None = None,
    require_current_build: bool = True, retained_quote_value_inputs: Path | None = None,
) -> dict[str, Any]:
    if not (evidence / "evidence-index.json").is_file():
        raise SystemExit("SEMIFINAL_RETAINED_PACK_REQUIRED")
    if require_current_build and retained_quote_value_inputs is not None:
        raise SystemExit("SEMIFINAL_MUTATION_RETAINED_INPUTS_IN_CURRENT_MODE")
    if not require_current_build and retained_quote_value_inputs is None:
        raise SystemExit("SEMIFINAL_MUTATION_RETAINED_QUOTE_VALUE_INPUTS_REQUIRED")
    options: list[str] = []
    if checkout is not None:
        options.extend(("--checkout", str(checkout)))
    if lock_path is not None:
        options.extend(("--lock", str(lock_path)))
    if not require_current_build:
        options.extend(("--retained-build", "--retained-quote-value-inputs", str(retained_quote_value_inputs)))

    def verifier_command(pack: Path) -> list[str]:
        return [sys.executable, str(ROOT / "scripts/verify_semifinal_closure.py"),
                "--evidence", str(pack), *options]

    baseline = subprocess.run(
        verifier_command(evidence),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if baseline.returncode != 0:
        raise SystemExit("SEMIFINAL_MUTATION_BASELINE_INVALID")
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="orgrebase-semifinal-mutations-") as raw:
        temporary = Path(raw)
        for name, mutate, expected_markers in ATTACKS:
            attack_root = temporary / name
            shutil.copytree(evidence, attack_root)
            mutate(attack_root)
            completed = subprocess.run(
                verifier_command(attack_root),
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            output = completed.stderr + completed.stdout
            matched = sorted(marker for marker in expected_markers if marker in output)
            if completed.returncode == 0 or not matched:
                raise SystemExit(f"SEMIFINAL_MUTATION_SURVIVED:{name}")
            results.append(
                {
                    "attack": name,
                    "status": "REJECTED",
                    "matched_failure_markers": matched,
                }
            )
    return {"status": "PASS", "attacks_rejected": len(results), "results": results,
            "verification_scope": "CURRENT_BUILD_INPUTS" if require_current_build else "RETAINED_ARTIFACT",
            "current_release_qualified": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--checkout", type=Path, help="Optional exact checkout matching the selected lock")
    parser.add_argument("--lock", type=Path, help="Exact source lock for the native evidence")
    parser.add_argument("--retained-build", action="store_true")
    parser.add_argument("--retained-quote-value-inputs", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            verify_mutations(
                args.evidence.expanduser().resolve(),
                checkout=args.checkout.expanduser().resolve() if args.checkout is not None else None,
                lock_path=args.lock,
                require_current_build=not args.retained_build,
                retained_quote_value_inputs=args.retained_quote_value_inputs,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
