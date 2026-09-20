"""Externally signed checkpoints for an otherwise rewritable database event chain."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from orgrebase.clock import Clock, SystemClock, utc_datetime
from orgrebase.database import DEFAULT_WORKSPACE_ID
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore

_SCHEMA = "orgrebase.audit-checkpoint.v2"
_LEGACY_SCHEMA = "orgrebase.audit-checkpoint.v1"
_FIELDS = {"schema_version", "deployment_id", "tenant_id", "sequence_no", "head_digest", "captured_at", "canonical_scheme"}
_SCHEMA_FIELDS = {_LEGACY_SCHEMA: _FIELDS, _SCHEMA: _FIELDS | {"workspace_id"}}


def _message(payload: dict[str, Any]) -> bytes:
    if not isinstance(payload, dict):
        raise ValueError("schema")
    schema = payload.get("schema_version")
    if not isinstance(schema, str) or schema not in _SCHEMA_FIELDS or set(payload) != _SCHEMA_FIELDS[schema]:
        raise ValueError("schema")
    return schema.encode("ascii") + b"\x00" + canonical_json(payload).encode("utf-8")


def _verified_events(store: StateStore) -> tuple[dict[str, Any], ...]:
    records = store.event_envelopes()
    previous = "sha256:" + "0" * 64
    for sequence, record in enumerate(records, start=1):
        envelope = {key: value for key, value in record.items() if key != "event_digest"}
        if (record["sequence_no"] != sequence or record["previous_digest"] != previous
                or record["event_digest"] != sha256_digest(envelope)):
            raise IntegrityError("AUDIT_EVENT_CHAIN_INVALID")
        previous = record["event_digest"]
    return records


def sign_checkpoint(store: StateStore, *, deployment_id: str, clock: Clock, private_key: Ed25519PrivateKey) -> dict[str, Any]:
    if not isinstance(deployment_id, str) or not deployment_id.strip():
        raise ValueError("AUDIT_DEPLOYMENT_ID_REQUIRED")
    records = _verified_events(store)
    captured = clock.now()
    utc_datetime(captured)
    scope = store.check_health()
    tenant = scope["tenant_id"]
    if not tenant:
        raise ValueError("AUDIT_TENANT_BINDING_REQUIRED")
    payload = {
        "schema_version": _SCHEMA, "deployment_id": deployment_id, "tenant_id": tenant,
        "workspace_id": scope["workspace_id"],
        "sequence_no": len(records), "head_digest": records[-1]["event_digest"] if records else "sha256:" + "0" * 64,
        "captured_at": captured, "canonical_scheme": "orgrebase-python-json-v1",
    }
    public = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {
        "payload": payload, "key_id": sha256_digest(public.hex()),
        "signature": base64.b64encode(private_key.sign(_message(payload))).decode("ascii"),
    }


def verify_checkpoint(
    checkpoint: dict[str, Any], *, public_key: Ed25519PublicKey, deployment_id: str,
    tenant_id: str, workspace_id: str = DEFAULT_WORKSPACE_ID, minimum_sequence: int = 0,
    expected_checkpoint_digest: str | None = None,
) -> dict[str, Any]:
    try:
        if not isinstance(checkpoint, dict) or set(checkpoint) != {"payload", "key_id", "signature"}:
            raise ValueError("shape")
        if type(minimum_sequence) is not int or minimum_sequence < 0:
            raise ValueError("minimum sequence")
        if not all(isinstance(value, str) and value.strip() for value in (deployment_id, tenant_id, workspace_id)):
            raise ValueError("scope")
        payload = checkpoint["payload"]
        message = _message(payload)
        if payload["canonical_scheme"] != "orgrebase-python-json-v1":
            raise ValueError("canonical scheme")
        if (payload["deployment_id"], payload["tenant_id"]) != (deployment_id, tenant_id):
            raise ValueError("scope")
        # V1 has no workspace field; its unchanged signature is accepted only
        # for the historical default scope, never a caller-selected workspace.
        if payload["schema_version"] == _LEGACY_SCHEMA:
            if workspace_id != DEFAULT_WORKSPACE_ID:
                raise ValueError("legacy workspace")
        elif payload["workspace_id"] != workspace_id:
            raise ValueError("workspace")
        sequence = payload["sequence_no"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0 or sequence < minimum_sequence:
            raise ValueError("sequence")
        if not isinstance(payload["head_digest"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", payload["head_digest"]):
            raise ValueError("head digest")
        utc_datetime(payload["captured_at"])
        public = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        if checkpoint["key_id"] != sha256_digest(public.hex()):
            raise ValueError("key")
        signature = base64.b64decode(checkpoint["signature"], validate=True)
        public_key.verify(signature, message)
        if expected_checkpoint_digest is not None and sha256_digest(checkpoint) != expected_checkpoint_digest:
            raise ValueError("anchor")
    except (ValueError, TypeError, KeyError, InvalidSignature) as exc:
        raise IntegrityError("AUDIT_CHECKPOINT_INVALID") from exc
    return dict(payload)


def verify_database_checkpoint(
    store: StateStore, checkpoint: dict[str, Any], *, public_key: Ed25519PublicKey,
    deployment_id: str, minimum_sequence: int = 0, expected_checkpoint_digest: str | None = None,
) -> dict[str, Any]:
    scope = store.check_health()
    payload = verify_checkpoint(
        checkpoint, public_key=public_key, deployment_id=deployment_id,
        tenant_id=scope["tenant_id"], workspace_id=scope["workspace_id"], minimum_sequence=minimum_sequence,
        expected_checkpoint_digest=expected_checkpoint_digest,
    )
    records = _verified_events(store)
    sequence = payload["sequence_no"]
    if len(records) < sequence:
        raise IntegrityError("AUDIT_CHECKPOINT_TRUNCATED")
    observed = records[sequence - 1]["event_digest"] if sequence else "sha256:" + "0" * 64
    if observed != payload["head_digest"]:
        raise IntegrityError("AUDIT_CHECKPOINT_PREFIX_MISMATCH")
    return {
        "status": "PASS", "anchored_through": sequence,
        "workspace_id": scope["workspace_id"], "checkpoint_schema_version": payload["schema_version"],
        "workspace_binding": "signed" if payload["schema_version"] == _SCHEMA else "legacy-default-only",
        "unanchored_events": len(records) - sequence,
        "whole_current_chain_matches_checkpoint": len(records) == sequence,
    }


def _read_operator_file(path: Path, *, private_key: bool = False) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 1_048_576:
            raise ValueError("AUDIT_OPERATOR_FILE_INVALID")
        if private_key and (metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077):
            raise ValueError("AUDIT_PRIVATE_KEY_PERMISSIONS_INVALID")
        return stream.read(1_048_577)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orgrebase audit")
    parser.add_argument("action", choices=("sign", "verify"))
    parser.add_argument("--database-env", default="ORGREBASE_WORKSPACE_DB")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE_ID)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--private-key", type=Path)
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--checkpoint-digest")
    parser.add_argument("--minimum-sequence", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    database = os.environ.get(args.database_env, "")
    if not database:
        parser.error("the configured database environment variable is empty")
    if args.minimum_sequence is not None and args.minimum_sequence < 0:
        parser.error("--minimum-sequence must be nonnegative")
    if args.action == "sign" and (args.private_key is None or args.output is None):
        parser.error("sign requires --private-key and a new --output file")
    if args.action == "verify" and (args.public_key is None or args.checkpoint is None):
        parser.error("verify requires --public-key and --checkpoint")
    if args.action == "verify" and args.checkpoint_digest is None and args.minimum_sequence is None:
        parser.error("verify requires an externally trusted --checkpoint-digest or --minimum-sequence")
    try:
        with StateStore(
            database, tenant_id=args.tenant, workspace_id=args.workspace,
            read_only=True, maintenance=args.action == "verify",
        ) as store:
            if args.action == "sign":
                key = serialization.load_pem_private_key(_read_operator_file(args.private_key, private_key=True), password=None)
                if not isinstance(key, Ed25519PrivateKey):
                    raise ValueError("AUDIT_ED25519_PRIVATE_KEY_REQUIRED")
                checkpoint = sign_checkpoint(store, deployment_id=args.deployment, clock=SystemClock(), private_key=key)
                descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(checkpoint, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                report = {"status": "PASS", "checkpoint_digest": sha256_digest(checkpoint),
                          "sequence_no": checkpoint["payload"]["sequence_no"],
                          "workspace_id": checkpoint["payload"]["workspace_id"], "external_custody_verified": False}
            else:
                key = serialization.load_pem_public_key(_read_operator_file(args.public_key))
                if not isinstance(key, Ed25519PublicKey):
                    raise ValueError("AUDIT_ED25519_PUBLIC_KEY_REQUIRED")
                checkpoint = json.loads(_read_operator_file(args.checkpoint))
                report = verify_database_checkpoint(
                    store, checkpoint, public_key=key, deployment_id=args.deployment,
                    minimum_sequence=args.minimum_sequence or 0, expected_checkpoint_digest=args.checkpoint_digest,
                )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        # Driver errors can contain a DSN or private SQL parameters.
        print(json.dumps({"status": "FAIL", "code": "AUDIT_OPERATION_FAILED", "error_type": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
