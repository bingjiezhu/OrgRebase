from __future__ import annotations

import hashlib
import json
import stat
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy import delete, update

from orgrebase.audit_checkpoint import main, sign_checkpoint, verify_checkpoint, verify_database_checkpoint
from orgrebase.clock import FrozenClock
from orgrebase.database import domain_events, execute_core, workspace_registry
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore


@pytest.fixture
def legacy_checkpoint():
    # Frozen synthetic V1 wire fixture. Verification has only the public key;
    # it cannot regenerate a signature or silently migrate the signed payload.
    public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(
        "23f2ffe3d97c13ecd659aad0c7d57317ed81a2a574695dc1704757ab7c578d15"
    ))
    checkpoint = {
        "payload": {
            "schema_version": "orgrebase.audit-checkpoint.v1",
            "deployment_id": "deployment-1", "tenant_id": "org:test", "sequence_no": 0,
            "head_digest": "sha256:" + "0" * 64, "captured_at": "2026-09-09T00:00:00Z",
            "canonical_scheme": "orgrebase-python-json-v1",
        },
        "key_id": "sha256:e936def88481f9f34d55a23a9e1f79babf4370c007ebe908b7c6e143560bfd40",
        "signature": "xfErRMXOFq1D+RNs/rfP0K/uQQ8YZ+Gp0vYhGY47fhlETWcosS+igTJd9sY9i5Gwxxr/qaz1AUq6ZJRBj7FkAQ==",
    }
    return checkpoint, public_key


def _operator_keys(tmp_path):
    key = Ed25519PrivateKey.generate()
    private = tmp_path / "private.pem"
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    private.chmod(0o600)
    public = tmp_path / "public.pem"
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return private, public


def test_signed_checkpoint_detects_rewritten_and_truncated_database():
    key = Ed25519PrivateKey.generate()
    with StateStore(tenant_id="org:test") as store:
        store.record_event("OBSERVATION", {"value": "original"})
        checkpoint = sign_checkpoint(store, deployment_id="deployment-1", clock=FrozenClock("2026-09-09T00:00:00Z"), private_key=key)
        assert checkpoint["payload"]["schema_version"] == "orgrebase.audit-checkpoint.v2"
        assert checkpoint["payload"]["workspace_id"] == "default"
        args = {"public_key": key.public_key(), "deployment_id": "deployment-1", "expected_checkpoint_digest": sha256_digest(checkpoint)}
        assert verify_database_checkpoint(store, checkpoint, **args)["whole_current_chain_matches_checkpoint"]
        # A database administrator can rewrite a self-consistent hash chain; the external signature still detects it.
        envelope = {"sequence_no": 1, "event_type": "OBSERVATION", "payload": {"value": "rewritten"}, "previous_digest": "sha256:" + "0" * 64}
        with store.transaction() as connection:
            execute_core(connection, update(domain_events).values(payload_json=canonical_json(envelope["payload"]), event_digest=sha256_digest(envelope)))
        with pytest.raises(IntegrityError, match="STATE_STORE_AUDIT_HEAD_MISMATCH"):
            store.verify_event_chain()
        # The administrator also controls the local derived head. Only the
        # independently retained signed checkpoint survives this stronger attack.
        with store.transaction() as connection:
            execute_core(connection, update(workspace_registry)
                         .where(workspace_registry.c.workspace_id == store.workspace_id)
                         .values(audit_sequence=1, audit_head=sha256_digest(envelope)))
        assert store.verify_event_chain()["status"] == "PASS"
        with pytest.raises(IntegrityError, match="AUDIT_CHECKPOINT_PREFIX_MISMATCH"):
            verify_database_checkpoint(store, checkpoint, **args)
        with store.transaction() as connection:
            execute_core(connection, delete(domain_events))
            execute_core(connection, update(workspace_registry)
                         .where(workspace_registry.c.workspace_id == store.workspace_id)
                         .values(audit_sequence=0, audit_head="sha256:" + "0" * 64))
        with pytest.raises(IntegrityError, match="AUDIT_CHECKPOINT_TRUNCATED"):
            verify_database_checkpoint(store, checkpoint, **args)


def test_checkpoint_requires_external_key_scope_and_nonrollback_anchor():
    key = Ed25519PrivateKey.generate()
    with StateStore(tenant_id="org:test") as store:
        checkpoint = sign_checkpoint(store, deployment_id="deployment-1", clock=FrozenClock("2026-09-09T00:00:00Z"), private_key=key)
        args = {"public_key": key.public_key(), "deployment_id": "deployment-1", "tenant_id": "org:test"}
        for change in [{"public_key": Ed25519PrivateKey.generate().public_key()}, {"tenant_id": "org:other"},
                       {"deployment_id": "other"}, {"workspace_id": "other"}, {"minimum_sequence": 1},
                       {"expected_checkpoint_digest": "sha256:" + "f" * 64}]:
            with pytest.raises(IntegrityError, match="AUDIT_CHECKPOINT_INVALID"):
                verify_checkpoint(checkpoint, **{**args, **change})
        altered = deepcopy(checkpoint)
        altered["payload"]["sequence_no"] = 50
        with pytest.raises(IntegrityError, match="AUDIT_CHECKPOINT_INVALID"):
            verify_checkpoint(altered, **args)
        store.record_event("LATER", {})
        report = verify_database_checkpoint(store, checkpoint, public_key=key.public_key(), deployment_id="deployment-1")
        assert report["unanchored_events"] == 1
        assert report["whole_current_chain_matches_checkpoint"] is False


def test_operator_cli_signs_and_verifies_without_database_writes(tmp_path, monkeypatch, capsys):
    database = tmp_path / "audit.sqlite3"
    with StateStore(database, tenant_id="org:test") as store:
        store.record_event("OBSERVATION", {"ref": "source:1"})
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    monkeypatch.setenv("ORGREBASE_AUDIT_TEST_DATABASE", str(database))
    private, public = _operator_keys(tmp_path)
    checkpoint = tmp_path / "checkpoint.json"
    common = ["--database-env", "ORGREBASE_AUDIT_TEST_DATABASE", "--tenant", "org:test", "--deployment", "deployment:1"]
    assert main(["sign", *common, "--private-key", str(private), "--output", str(checkpoint)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["external_custody_verified"] is False
    assert stat.S_IMODE(checkpoint.stat().st_mode) == 0o600
    assert main(["verify", *common, "--public-key", str(public), "--checkpoint", str(checkpoint),
                 "--checkpoint-digest", report["checkpoint_digest"]]) == 0
    assert json.loads(capsys.readouterr().out)["whole_current_chain_matches_checkpoint"]
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert main(["sign", *common, "--private-key", str(private), "--output", str(checkpoint)]) == 1
    captured = capsys.readouterr()
    assert "PRIVATE KEY" not in captured.out + captured.err
    private.chmod(0o644)
    assert main(["sign", *common, "--private-key", str(private), "--output", str(tmp_path / "denied.json")]) == 1
    assert not (tmp_path / "denied.json").exists()


def test_operator_cli_requires_external_freshness_anchor(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGREBASE_WORKSPACE_DB", str(tmp_path / "must-not-create.sqlite3"))
    with pytest.raises(SystemExit) as error:
        main(["verify", "--tenant", "org:test", "--deployment", "deployment:1",
              "--public-key", "public.pem", "--checkpoint", "checkpoint.json"])
    assert error.value.code == 2
    assert not (tmp_path / "must-not-create.sqlite3").exists()


@pytest.mark.parametrize("minimum_sequence", [True, -1, 0.5, float("nan"), None])
def test_checkpoint_rejects_invalid_freshness_bound(legacy_checkpoint, minimum_sequence):
    checkpoint, key = legacy_checkpoint
    with pytest.raises(IntegrityError, match="AUDIT_CHECKPOINT_INVALID"):
        verify_checkpoint(checkpoint, public_key=key, deployment_id="deployment-1",
                          tenant_id="org:test", minimum_sequence=minimum_sequence)


def test_legacy_signature_is_preserved_and_limited_to_default_workspace(legacy_checkpoint):
    checkpoint, key = legacy_checkpoint
    original = canonical_json(checkpoint)
    arguments = {"public_key": key, "deployment_id": "deployment-1", "tenant_id": "org:test"}
    assert verify_checkpoint(checkpoint, **arguments) == checkpoint["payload"]
    with StateStore(tenant_id="org:test") as store:
        store.record_event("LATER", {})
        report = verify_database_checkpoint(store, checkpoint, public_key=key, deployment_id="deployment-1")
        assert report["workspace_binding"] == "legacy-default-only"
        assert report["workspace_id"] == "default"
        assert report["unanchored_events"] == 1
    assert canonical_json(checkpoint) == original
    with pytest.raises(IntegrityError, match="AUDIT_CHECKPOINT_INVALID"):
        verify_checkpoint(checkpoint, **arguments, workspace_id="quote-b")
    upgraded = deepcopy(checkpoint)
    upgraded["payload"].update(schema_version="orgrebase.audit-checkpoint.v2", workspace_id="default")
    with pytest.raises(IntegrityError, match="AUDIT_CHECKPOINT_INVALID"):
        verify_checkpoint(upgraded, **arguments)


@pytest.mark.parametrize("payload", [None, [], {"schema_version": []}, {"schema_version": "orgrebase.audit-checkpoint.v3"}])
def test_checkpoint_rejects_invalid_wire_shapes(legacy_checkpoint, payload):
    checkpoint, key = legacy_checkpoint
    checkpoint["payload"] = payload
    with pytest.raises(IntegrityError, match="AUDIT_CHECKPOINT_INVALID"):
        verify_checkpoint(checkpoint, public_key=key, deployment_id="deployment-1", tenant_id="org:test")


def test_real_postgres_checkpoint_binds_workspace_even_with_identical_event_chains(postgres_runtime, legacy_checkpoint):
    database = postgres_runtime(tenant_id="org:test")
    arguments = {"tenant_id": "org:test", "migrate": False}
    key = Ed25519PrivateKey.generate()
    with StateStore(database["runtime_dsn"], **arguments) as first:
        first.register_workspace("quote-b", profile_digest="sha256:" + "1" * 64,
                                 pack_digest=None, quote_object_id="quote:b", created_at="2026-09-09T00:00:00Z")
        with StateStore(database["runtime_dsn"], workspace_id="quote-b", **arguments) as second:
            for store in (first, second):
                store.record_event("OBSERVATION", {"ref": "same-source"})
            assert first.event_envelopes() == second.event_envelopes()
            checkpoint = sign_checkpoint(second, deployment_id="deployment-1", private_key=key,
                                         clock=FrozenClock("2026-09-09T00:00:00Z"))
            verify_args = {"public_key": key.public_key(), "deployment_id": "deployment-1"}
            report = verify_database_checkpoint(second, checkpoint, **verify_args)
            assert report["workspace_id"] == "quote-b"
            assert report["workspace_binding"] == "signed"
            assert report["whole_current_chain_matches_checkpoint"] is True
            assert verify_checkpoint(checkpoint, **verify_args, tenant_id="org:test", workspace_id="quote-b") == checkpoint["payload"]
            with pytest.raises(IntegrityError, match="AUDIT_CHECKPOINT_INVALID"):
                verify_database_checkpoint(first, checkpoint, **verify_args)
            legacy, legacy_key = legacy_checkpoint
            with pytest.raises(IntegrityError, match="AUDIT_CHECKPOINT_INVALID"):
                verify_database_checkpoint(second, legacy, public_key=legacy_key, deployment_id="deployment-1")
            downgraded = deepcopy(checkpoint)
            downgraded["payload"]["schema_version"] = "orgrebase.audit-checkpoint.v1"
            del downgraded["payload"]["workspace_id"]
            with pytest.raises(IntegrityError, match="AUDIT_CHECKPOINT_INVALID"):
                verify_database_checkpoint(first, downgraded, **verify_args)


def test_operator_cli_uses_selected_postgres_workspace(postgres_runtime, tmp_path, monkeypatch, capsys):
    database = postgres_runtime(tenant_id="org:test")
    with StateStore(database["runtime_dsn"], tenant_id="org:test", migrate=False) as store:
        store.register_workspace("quote-b", profile_digest="sha256:" + "1" * 64,
                                 pack_digest=None, quote_object_id="quote:b", created_at="2026-09-09T00:00:00Z")
    with StateStore(database["runtime_dsn"], tenant_id="org:test", workspace_id="quote-b", migrate=False) as store:
        store.record_event("OBSERVATION", {"ref": "source:1"})
        before = store.event_envelopes()
    monkeypatch.setenv("ORGREBASE_AUDIT_TEST_DATABASE", database["runtime_dsn"])
    private, public = _operator_keys(tmp_path)
    checkpoint = tmp_path / "checkpoint.json"
    common = ["--database-env", "ORGREBASE_AUDIT_TEST_DATABASE", "--tenant", "org:test", "--deployment", "deployment:1"]
    assert main(["sign", *common, "--workspace", "quote-b", "--private-key", str(private), "--output", str(checkpoint)]) == 0
    signed = json.loads(capsys.readouterr().out)
    assert signed["workspace_id"] == "quote-b"
    assert json.loads(checkpoint.read_text())["payload"]["workspace_id"] == "quote-b"
    verify = ["verify", *common, "--public-key", str(public), "--checkpoint", str(checkpoint),
              "--checkpoint-digest", signed["checkpoint_digest"]]
    assert main([*verify, "--workspace", "quote-b"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["whole_current_chain_matches_checkpoint"] is True
    assert report["workspace_id"] == "quote-b"
    assert main(verify) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "FAIL"
    with StateStore(database["runtime_dsn"], tenant_id="org:test", workspace_id="quote-b", read_only=True) as store:
        assert store.event_envelopes() == before
