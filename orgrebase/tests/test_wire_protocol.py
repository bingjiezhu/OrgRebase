from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.wire import (
    CONTENT_SCHEME,
    WIRE_SCHEME,
    WireJSONResponse,
    canonical_wire,
    parse_wire,
    protocol_digest,
)

ROOT = Path(__file__).resolve().parents[1]
VECTORS = json.loads((ROOT / "tests/fixtures/wire-v1-vectors.json").read_text())


@pytest.mark.parametrize("vector", VECTORS["accepted"], ids=lambda vector: vector["name"])
def test_python_wire_matches_frozen_cross_language_vectors(vector):
    value = parse_wire(vector["input"])
    assert canonical_wire(value).decode() == vector["canonical"]
    assert protocol_digest(value) == "sha256:" + hashlib.sha256(vector["canonical"].encode()).hexdigest()


@pytest.mark.parametrize("vector", VECTORS["rejected"], ids=lambda vector: vector["name"])
def test_python_wire_rejects_values_outside_the_declared_protocol(vector):
    with pytest.raises(ValueError):
        parse_wire(vector["input"])


def test_browser_uses_the_same_shared_vectors_and_rejects_unknown_scheme_and_tampering():
    node = shutil.which("node")
    assert node is not None, "Node is required for the cross-language release check"
    script = r"""
const fs = require('fs'); const vm = require('vm');
globalThis.crypto = require('crypto').webcrypto;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const vectors = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
(async () => {
  const accepted = [];
  for (const vector of vectors.accepted) {
    const value = JSON.parse(vector.input);
    if (OrgRebaseWire.canonical(value) !== vector.canonical) throw Error(vector.name);
    accepted.push({name: vector.name, digest: await OrgRebaseWire.digest(value)});
  }
  for (const vector of vectors.rejected) {
    let failed = false;
    try { await OrgRebaseWire.digest(JSON.parse(vector.input)); } catch (_) { failed = true; }
    if (!failed) throw Error('accepted:' + vector.name);
  }
  for (const [scheme, body] of [['future.v2', {a:1}], [vectors.scheme, {a:2}]]) {
    const headers = {'OrgRebase-Wire-Scheme':scheme, 'OrgRebase-Wire-Digest':await OrgRebaseWire.digest({a:1})};
    let failed = false;
    try { await OrgRebaseWire.verifyResponse(new Response(JSON.stringify(body), {headers})); } catch (_) { failed = true; }
    if (!failed) throw Error('accepted invalid response');
  }
  process.stdout.write(JSON.stringify(accepted));
})().catch(error => { console.error(error); process.exit(1); });
"""
    result = subprocess.run([node, "-e", script, str(ROOT / "demo/console/wire.js"),
                             str(ROOT / "tests/fixtures/wire-v1-vectors.json")],
                            capture_output=True, text=True, check=True, timeout=20)
    for vector, browser_result in zip(VECTORS["accepted"], json.loads(result.stdout), strict=True):
        assert browser_result == {"name": vector["name"], "digest": protocol_digest(parse_wire(vector["input"]))}


def test_stored_content_keeps_original_float_and_unicode_codec():
    value = {"דּ": 1.0, "😀": -0.0}
    assert canonical_json(value) == '{"דּ":1.0,"😀":-0.0}'
    assert protocol_digest(value, CONTENT_SCHEME) == sha256_digest(value)
    assert protocol_digest(value, WIRE_SCHEME) != sha256_digest(value)
    with pytest.raises(ValueError, match="WIRE_SCHEME_UNSUPPORTED"):
        protocol_digest(value, "future.codec.v2")


def test_wire_parser_refuses_duplicate_keys_non_string_keys_and_deep_values():
    with pytest.raises(ValueError, match="WIRE_DUPLICATE_KEY"):
        parse_wire('{"amount":"100.00","amount":"1.00"}')
    with pytest.raises(ValueError, match="WIRE_JSON_VALUE_REQUIRED"):
        canonical_wire({1: "ambiguous key"})
    value = None
    for _ in range(130):
        value = [value]
    with pytest.raises(ValueError, match="WIRE_DEPTH_EXCEEDED"):
        canonical_wire(value)


def test_wire_response_declares_both_algorithms_and_binds_actual_json_bytes():
    response = WireJSONResponse({"amount": "100.00", "ratio": -0.0})
    assert response.headers["OrgRebase-Content-Scheme"] == CONTENT_SCHEME
    assert response.headers["OrgRebase-Wire-Scheme"] == WIRE_SCHEME
    assert response.headers["OrgRebase-Wire-Digest"] == protocol_digest(json.loads(response.body))


def test_real_workspace_routes_supply_wire_contract_without_changing_payload_shape():
    with TestClient(create_app()) as client:
        for path in ("/api/workspace/state", "/api/workspace/change-options"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["OrgRebase-Wire-Scheme"] == WIRE_SCHEME
            assert response.headers["OrgRebase-Wire-Digest"] == protocol_digest(response.json())
            assert "payload" not in response.json()
