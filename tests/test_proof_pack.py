from __future__ import annotations

import ast
import copy
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from orgrebase.digest import sha256_digest

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proof_pack = _load_script("verify_proof_pack")


def _demo() -> dict:
    return proof_pack._read_json(ROOT / "evidence" / "latest" / "demo.json")


def _seal(pack: dict) -> dict:
    pack = copy.deepcopy(pack)
    pack.pop("digest", None)
    pack["digest"] = sha256_digest(pack)
    return pack


def _seal_object(item: dict) -> dict:
    item = copy.deepcopy(item)
    item.pop("digest", None)
    item["digest"] = sha256_digest(item)
    return item


def test_proof_pack_source_does_not_import_engine_modules() -> None:
    source = (ROOT / "scripts" / "verify_proof_pack.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = proof_pack.FORBIDDEN_IMPORT_ROOTS
    assert not [
        name
        for name in imported
        if name in forbidden or any(name.startswith(f"{root}.") for root in forbidden)
    ]
    assert "orgrebase.digest" in imported


def test_proof_pack_happy_path_digest_is_stable() -> None:
    first = proof_pack.build_from_demo(_demo(), root=ROOT)
    second = proof_pack.build_from_demo(_demo(), root=ROOT)
    report = proof_pack.verify_pack(first, root=ROOT)
    assert first["digest"] == second["digest"]
    assert report["status"] == "PASS"
    assert report["orchestration_plan_digest"] == proof_pack.FROZEN_LIVE_PLAN_DIGEST
    assert first["world"]["orchestration_plan"]["digest"] == proof_pack.FROZEN_LIVE_PLAN_DIGEST


def test_proof_pack_rejects_deleted_rebuild_effect() -> None:
    pack = proof_pack.build_from_demo(_demo(), root=ROOT)
    vmrc = pack["world"]["minimal_rebase_certificate"]
    effects = list(vmrc["effects"])
    index = next(i for i, item in enumerate(effects) if item["disposition"] == "REBUILD")
    del effects[index]
    vmrc["effects"] = effects
    pack["world"]["minimal_rebase_certificate"] = _seal_object(vmrc)
    sealed = _seal(pack)
    with pytest.raises(proof_pack.ProofPackError, match="PROOFPACK_EFFECT_TARGET_SET_MISMATCH"):
        proof_pack.verify_pack(sealed, root=ROOT)


def test_proof_pack_rejects_identity_capability_drift() -> None:
    pack = proof_pack.build_from_demo(_demo(), root=ROOT)
    coordinator = next(
        item for item in pack["world"]["identities"] if item["name"] == "change-coordinator"
    )
    coordinator["capabilities"] = [
        *coordinator["capabilities"],
        "write canonical state",
    ]
    sealed = _seal(pack)
    with pytest.raises(proof_pack.ProofPackError, match="PROOFPACK_IDENTITY_LOCK:change-coordinator"):
        proof_pack.verify_pack(sealed, root=ROOT)


def test_proof_pack_rejects_handoff_kind_escape() -> None:
    pack = proof_pack.build_from_demo(_demo(), root=ROOT)
    handoff = pack["world"]["handoffs"][0]
    handoff["payload"]["kind"] = "CanonicalStateWrite"
    pack["world"]["handoffs"][0] = _seal_object(handoff)
    sealed = _seal(pack)
    with pytest.raises(proof_pack.ProofPackError, match="PROOFPACK_HANDOFF_KIND"):
        proof_pack.verify_pack(sealed, root=ROOT)


def test_proof_pack_rejects_forged_disposition_table() -> None:
    pack = proof_pack.build_from_demo(_demo(), root=ROOT)
    pack["disposition_table"]["UNKNOWN"] = "PRESERVE_WITHIN_BOUNDARY"
    sealed = _seal(pack)
    with pytest.raises(proof_pack.ProofPackError, match="PROOFPACK_DISPOSITION_TABLE"):
        proof_pack.verify_pack(sealed, root=ROOT)
