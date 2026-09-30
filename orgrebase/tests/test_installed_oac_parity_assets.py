from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from orgrebase import api
from orgrebase.domain import IntegrityError
from orgrebase.workspace.oac_quote_parity import attempts_from_golden_summary, load_golden_summary


def test_installed_default_root_resolves_the_same_retained_parity_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Path(__file__).resolve().parents[1] / "evidence/golden-competition/latest/pilot/golden-run/summary.json"
    packaged = tmp_path / "installed/_assets/evidence/golden-competition/latest/pilot/golden-run/summary.json"
    packaged.parent.mkdir(parents=True)
    shutil.copy2(original, packaged)
    monkeypatch.delenv("ORGREBASE_GOLDEN_EVIDENCE_ROOT", raising=False)
    monkeypatch.setattr(api, "GOLDEN_EVIDENCE_ROOT", tmp_path / "absent-source-checkout")

    def resolve(relative: str) -> Path:
        assert relative == "evidence/golden-competition/latest/pilot/golden-run/summary.json"
        return packaged

    monkeypatch.setattr(api, "runtime_asset_path", resolve)
    assert api._active_golden_evidence_root() == packaged.parent.parent
    summary = load_golden_summary(api._active_golden_evidence_root())
    assert summary == load_golden_summary(original)
    attempts = attempts_from_golden_summary(summary)
    assert len(attempts) == 7
    assert [item.outcome for item in attempts if item.domain == "finance"] == ["ABSTAIN", "PASS"]


def test_explicit_missing_parity_root_never_falls_back_to_a_packaged_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "explicit-missing-reference"
    monkeypatch.setenv("ORGREBASE_GOLDEN_EVIDENCE_ROOT", str(missing))
    monkeypatch.setattr(api, "runtime_asset_path", lambda _: pytest.fail("explicit configuration must not fall back"))
    assert api._active_golden_evidence_root() == missing
    with pytest.raises(IntegrityError, match="QUOTE_PARITY_GOLDEN_SUMMARY_UNREADABLE"):
        load_golden_summary(api._active_golden_evidence_root())
