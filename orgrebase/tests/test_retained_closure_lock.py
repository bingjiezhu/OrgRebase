from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCK = Path("agentteams/historical/teamharness-v1.2.2.json")
EVIDENCE = Path("evidence/semifinal-closure/latest")


@pytest.fixture
def gate():
    spec = importlib.util.spec_from_file_location(
        "retained_closure_gate", ROOT / "scripts/goai_gate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def retained_root(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/verify_semifinal_closure.py").write_text("# not executed by boundary tests\n")
    (root / EVIDENCE / "agentteams").mkdir(parents=True)
    (root / EVIDENCE / "evidence-index.json").write_text("{}\n")
    native = json.loads((ROOT / EVIDENCE / "agentteams/lifecycle-receipt.json").read_bytes())
    (root / EVIDENCE / "agentteams/lifecycle-receipt.json").write_text(json.dumps(native))
    (root / LOCK).parent.mkdir(parents=True)
    (root / LOCK).write_bytes((ROOT / LOCK).read_bytes())
    return root


@pytest.mark.parametrize("fault", ("missing", "replaced", "symlink", "directory_escape"))
def test_invalid_historical_lock_never_dispatches_a_verifier(
    gate, retained_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    lock = retained_root / LOCK
    if fault == "missing":
        lock.unlink()
    elif fault == "replaced":
        lock.write_bytes((ROOT / "agentteams/teamharness-lock.json").read_bytes())
    elif fault == "symlink":
        copy = retained_root / "retained-lock-copy.json"
        lock.rename(copy)
        lock.symlink_to(copy)
    else:
        moved = tmp_path / "outside-history"
        lock.parent.rename(moved)
        lock.parent.symlink_to(moved, target_is_directory=True)

    def forbidden(*args, **kwargs):
        pytest.fail("invalid historical source must not dispatch or fall back to the active lock")

    monkeypatch.setattr(gate.subprocess, "run", forbidden)
    assert gate.verify_integrated_semifinal_closure(retained_root) is None


def test_unregistered_pack_source_never_dispatches_a_verifier(
    gate, retained_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = retained_root / EVIDENCE / "agentteams/lifecycle-receipt.json"
    native = json.loads(path.read_bytes())
    native["source_verification"]["source_lock_digest"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(native))

    def forbidden(*args, **kwargs):
        pytest.fail("an unregistered pack source must not choose a different verifier lock")

    monkeypatch.setattr(gate.subprocess, "run", forbidden)
    assert gate.verify_integrated_semifinal_closure(retained_root) is None


@pytest.mark.parametrize(
    "field,replacement",
    (
        ("agentteams_commit", "0" * 40),
        ("source_lock_digest", "sha256:" + "0" * 64),
        ("current_release_qualified", True),
    ),
)
def test_child_result_cannot_relabel_the_retained_source_or_release_scope(
    gate, retained_root: Path, monkeypatch: pytest.MonkeyPatch,
    field: str, replacement: object,
) -> None:
    source = json.loads(
        (retained_root / EVIDENCE / "agentteams/lifecycle-receipt.json").read_bytes()
    )["source_verification"]
    result = {
        "status": "PASS",
        "evidence_class": "CONTROLLED_LOCAL_INTEGRATED_VERTICAL_SLICE",
        "terminal_state": "CANDIDATE_ACCEPTED",
        "canonical_target_writes": 0,
        "production_readiness": False,
        "native_verification_strength": "RETAINED_LOCK_REPLAY",
        "verification_scope": "RETAINED_ARTIFACT",
        "current_release_qualified": False,
        "current_build_binding": {"status": "MISMATCH"},
        "agentteams_commit": source["commit"],
        "source_lock_digest": source["source_lock_digest"],
    }
    result[field] = replacement
    calls = []

    def substituted(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(result))

    monkeypatch.setattr(gate.subprocess, "run", substituted)
    assert gate.verify_integrated_semifinal_closure(retained_root) is None
    assert len(calls) == 1
    assert calls[0][-3:] == ["--retained-build", "--lock", str(retained_root / LOCK)]
