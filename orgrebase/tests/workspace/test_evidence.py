from __future__ import annotations

import json
from pathlib import Path

from orgrebase.workspace.evidence import WorkspaceEvidenceBuilder, verify_evidence_directory


def test_one_command_evidence_pack_is_self_verifying(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    index = WorkspaceEvidenceBuilder(root).build()
    assert index.status == "PASS"
    assert len(index.entries) >= 20
    assert verify_evidence_directory(root)["status"] == "PASS"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.json"))
    assert "ORGREBASE_CANARY_SECRET_" not in text
    live = json.loads((root / "agentteams/status.json").read_text(encoding="utf-8"))
    assert live["status"] == "NOT_RUN"
