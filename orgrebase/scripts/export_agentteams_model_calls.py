"""Export redacted provider-call bindings from AgentTeams session journals.

Only an assistant response whose ``write`` tool call contains the exact candidate
artifact bytes is accepted.  The resulting JSONL contains no prompts, tool
arguments, credentials or model output; it keeps only the provider response ID,
model, timestamp and byte-addressed artifact binding required by the live
evidence collector.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "orgrebase.agentteams-model-call.v1"


class ExportError(RuntimeError):
    """The source journals cannot prove a provider-to-artifact binding."""


def _json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ExportError(f"unsafe or missing JSON source: {path}")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError(f"invalid JSON source: {path.name}") from exc
    if not isinstance(value, dict):
        raise ExportError(f"expected one JSON object: {path.name}")
    return value


def _timestamp_ms(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.timestamp() * 1000)


def _iter_records(session_root: Path) -> list[dict[str, Any]]:
    if not session_root.is_dir() or session_root.is_symlink():
        raise ExportError(f"unsafe or missing session directory: {session_root}")
    records: list[dict[str, Any]] = []
    for path in sorted(session_root.glob("*.jsonl")):
        if path.is_symlink():
            raise ExportError(f"session journal is a symlink: {path.name}")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ExportError(f"cannot read session journal: {path.name}") from exc
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ExportError(f"invalid journal JSON: {path.name}:{line_number}") from exc
            if isinstance(record, dict):
                records.append(record)
    if not records:
        raise ExportError(f"no session records found for {session_root.name}")
    return records


def _matching_writes(
    *, records: list[dict[str, Any]], artifact_bytes: bytes, artifact_relative_path: str
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for record in records:
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        response_id = message.get("responseId")
        model = message.get("model")
        timestamp_ms = _timestamp_ms(record.get("timestamp"))
        if (
            message.get("provider") != "agentteams-gateway"
            or not isinstance(response_id, str)
            or not response_id
            or not isinstance(model, str)
            or not model
            or timestamp_ms is None
        ):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "toolCall":
                continue
            if item.get("name") != "write":
                continue
            arguments = item.get("arguments")
            if not isinstance(arguments, dict):
                continue
            written_path = arguments.get("path")
            written_content = arguments.get("content")
            if (
                isinstance(written_path, str)
                and written_path.endswith(artifact_relative_path)
                and isinstance(written_content, str)
                and written_content.encode() == artifact_bytes
            ):
                matches.append(
                    {
                        "provider_request_id": response_id,
                        "model": model,
                        "timestamp_ms": timestamp_ms,
                    }
                )
    return matches


def export_calls(
    *,
    run_envelope_path: Path,
    artifact_manifest_path: Path,
    artifact_root: Path,
    session_roots: dict[str, Path],
) -> list[dict[str, Any]]:
    envelope = _json(run_envelope_path)
    manifest = _json(artifact_manifest_path)
    if (
        manifest.get("run_id") != envelope.get("run_id")
        or manifest.get("nonce") != envelope.get("nonce")
    ):
        raise ExportError("artifact manifest does not match the run envelope")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ExportError("artifact manifest is empty")
    calls: list[dict[str, Any]] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ExportError("invalid artifact manifest entry")
        worker = artifact.get("producer_worker")
        relative = artifact.get("path")
        artifact_ref = artifact.get("ref")
        expected_digest = artifact.get("digest")
        if not all(
            isinstance(value, str) and value
            for value in (worker, relative, artifact_ref, expected_digest)
        ):
            raise ExportError("incomplete artifact manifest entry")
        session_root = session_roots.get(worker)
        if session_root is None:
            continue
        path = (artifact_root.resolve() / relative).resolve()
        if not path.is_relative_to(artifact_root.resolve()) or not path.is_file():
            raise ExportError(f"unsafe or missing artifact: {relative}")
        artifact_bytes = path.read_bytes()
        actual_digest = f"sha256:{hashlib.sha256(artifact_bytes).hexdigest()}"
        if actual_digest != expected_digest:
            raise ExportError(f"artifact digest mismatch: {artifact_ref}")
        matches = _matching_writes(
            records=_iter_records(session_root),
            artifact_bytes=artifact_bytes,
            artifact_relative_path=relative,
        )
        if not matches:
            raise ExportError(f"no exact provider write found: {artifact_ref}")
        match = max(matches, key=lambda item: item["timestamp_ms"])
        calls.append(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": envelope["run_id"],
                "nonce": envelope["nonce"],
                "worker_name": worker,
                "status": "SUCCEEDED",
                **match,
                "artifact_ref": artifact_ref,
                "artifact_digest": actual_digest,
            }
        )
    if len(calls) < 3:
        raise ExportError("fewer than three exact provider-to-artifact bindings")
    request_ids = [item["provider_request_id"] for item in calls]
    if len(request_ids) != len(set(request_ids)):
        raise ExportError("provider response IDs are not unique")
    return sorted(calls, key=lambda item: (item["timestamp_ms"], item["worker_name"]))


def _session_mapping(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        worker, separator, path = value.partition("=")
        if not separator or not worker or not path or worker in result:
            raise ExportError(f"invalid --session mapping: {value}")
        result[worker] = Path(path)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-envelope", type=Path, required=True)
    parser.add_argument("--artifact-manifest", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--session", action="append", default=[], metavar="WORKER=DIR")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        calls = export_calls(
            run_envelope_path=args.run_envelope,
            artifact_manifest_path=args.artifact_manifest,
            artifact_root=args.artifact_root,
            session_roots=_session_mapping(args.session),
        )
    except ExportError as exc:
        raise SystemExit(str(exc)) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in calls)
    args.output.write_text(encoded, encoding="utf-8")
    print(json.dumps({"records": len(calls), "output": str(args.output)}))


if __name__ == "__main__":
    main()
