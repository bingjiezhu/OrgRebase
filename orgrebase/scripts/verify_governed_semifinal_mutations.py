#!/usr/bin/env python3
"""Prove governed-pack semantics survive fully rehashed substitutions.

Each attack copies the retained pack, changes one authority or recovery fact,
recomputes every affected content digest and the outer evidence index, then
requires the independent stdlib verifier to reject the result.
"""

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


def _reseal(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    body = {key: item for key, item in value.items() if key != "digest"}
    sealed = {**body, "digest": _digest(body)}
    _write(path, sealed)
    return sealed


def _reindex(root: Path) -> None:
    path = root / "evidence-index.json"
    old = _load(path)
    entries = [
        {
            "path": item.relative_to(root).as_posix(),
            "sha256": _file_digest(item),
            "bytes": item.stat().st_size,
        }
        for item in sorted(root.rglob("*"))
        if item.is_file() and item != path
    ]
    body = {
        "schema_version": old["schema_version"],
        "entry_count": len(entries),
        "entries": entries,
        "pack_digest": _digest(entries),
    }
    _write(path, {**body, "digest": _digest(body)})


def _owner_substitution(root: Path) -> None:
    path = root / "records/launch-approval.json"
    value = _load(path)
    value["actor_id"] = "human:gtm-owner"
    _reseal(path, value)
    _reindex(root)


def _run_binding_substitution(root: Path) -> None:
    binding_path = root / "records/run-bindings.json"
    binding = _load(binding_path)
    binding["stage_run_ids"]["currency_apply"] = "run:foreign:substitution"
    binding = _reseal(binding_path, binding)
    summary_path = root / "summary.json"
    summary = _load(summary_path)
    summary["same_run_binding_digest"] = binding["digest"]
    _reseal(summary_path, summary)
    _reindex(root)


def _sigkill_substitution(root: Path) -> None:
    path = root / "runtime/process-recovery.json"
    value = _load(path)
    kill = value["kill_receipts"][0]
    kill["signal"] = "SIGTERM"
    kill["returncode"] = -15
    body = {key: item for key, item in kill.items() if key != "digest"}
    value["kill_receipts"][0] = {**body, "digest": _digest(body)}
    _reseal(path, value)
    _reindex(root)


def _workspace_event_substitution(root: Path) -> None:
    database = root / "runtime/workspace.sqlite"
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT sequence_no,payload_json FROM domain_events ORDER BY sequence_no LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("WORKSPACE_EVENT_REQUIRED")
        payload = json.loads(row[1])
        payload["attacker_substitution"] = True
        connection.execute(
            "UPDATE domain_events SET payload_json=? WHERE sequence_no=?",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), row[0]),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    _reindex(root)


def _journal_state_substitution(root: Path) -> None:
    database = root / "runtime/runtime-journal.sqlite"
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT result_json FROM runtime_actions "
            "WHERE action_key='03-recover-and-apply-currency'"
        ).fetchone()
        if row is None:
            raise RuntimeError("JOURNAL_ACTION_REQUIRED")
        result = json.loads(row[0])
        result["after_quote_ref"] = "work:quote_acme@v999"
        connection.execute(
            "UPDATE runtime_actions SET result_json=? "
            "WHERE action_key='03-recover-and-apply-currency'",
            (json.dumps(result, ensure_ascii=False, sort_keys=True),),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    _reindex(root)


ATTACKS: tuple[
    tuple[str, Callable[[Path], None], tuple[str, ...]],
    ...,
] = (
    (
        "wrong-domain-owner",
        _owner_substitution,
        ("GOVERNANCE_RECORD_SEMANTICS", "WORKSPACE_ARTIFACT_BINDING:launch-approval"),
    ),
    (
        "mixed-run-binding",
        _run_binding_substitution,
        ("GOVERNANCE_RECORD_SEMANTICS",),
    ),
    (
        "sigkill-downgraded-to-sigterm",
        _sigkill_substitution,
        ("PROCESS_RECOVERY_SEMANTICS",),
    ),
    (
        "workspace-event-payload-rewrite",
        _workspace_event_substitution,
        ("WORKSPACE_EVENT_CHAIN",),
    ),
    (
        "journal-committed-result-rewrite",
        _journal_state_substitution,
        ("JOURNAL_SQLITE_RESULT_DIGEST",),
    ),
)


def _verify_attack(
    *,
    source: Path,
    parent: Path,
    name: str,
    mutate: Callable[[Path], None],
    expected: tuple[str, ...],
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"orgrebase-{name}-") as directory:
        attacked = Path(directory) / "pack"
        shutil.copytree(source, attacked)
        mutate(attacked)
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/verify_governed_semifinal_apply.py"),
                "--evidence",
                str(attacked),
                "--parent-pack",
                str(parent),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"ATTACK_VERIFIER_OUTPUT_INVALID:{name}:{completed.stderr}"
            ) from exc
        failures = result.get("failures", [])
        rejected = (
            completed.returncode != 0
            and result.get("status") == "FAIL"
            and any(
                any(str(failure).startswith(marker) for marker in expected)
                for failure in failures
            )
        )
        return {
            "attack": name,
            "rejected": rejected,
            "verifier_returncode": completed.returncode,
            "expected_failure_prefixes": list(expected),
            "observed_failures": failures,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        type=Path,
        default=ROOT / "evidence/semifinal-governed/latest",
    )
    parser.add_argument(
        "--parent-pack",
        type=Path,
        default=ROOT / "evidence/semifinal-closure/latest",
    )
    args = parser.parse_args()
    source = args.evidence.resolve()
    parent = args.parent_pack.resolve()
    results = [
        _verify_attack(
            source=source,
            parent=parent,
            name=name,
            mutate=mutate,
            expected=expected,
        )
        for name, mutate, expected in ATTACKS
    ]
    passed = all(item["rejected"] for item in results)
    output = {
        "schema_version": "orgrebase.semifinal-governed-mutation-verification.v1",
        "status": "PASS" if passed else "FAIL",
        "attacks_attempted": len(results),
        "attacks_rejected": sum(bool(item["rejected"]) for item in results),
        "results": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
