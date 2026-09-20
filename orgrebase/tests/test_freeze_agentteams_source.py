from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import freeze_agentteams_run as freeze


def test_unqualified_source_cannot_read_cluster_or_publish_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for relative in (
        "agentteams/source-lock.json",
        "configs/goai-agentteams-demo.json",
        "schemas/agentteams-live-run.schema.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((freeze.ROOT / relative).read_bytes())
    source_path = tmp_path / "agentteams/source-lock.json"
    source = json.loads(source_path.read_text())
    source.update(tag="v99.0.0", commit="0" * 40)
    source_path.write_text(json.dumps(source))
    monkeypatch.setattr(freeze, "ROOT", tmp_path)
    output = tmp_path / "must-not-exist" / "run.json"
    monkeypatch.setattr(sys, "argv", ["freeze_agentteams_run.py", "--output", str(output)])
    monkeypatch.setattr(
        freeze, "_kubectl_json", lambda *args: pytest.fail("must reject before contacting Kubernetes")
    )
    with pytest.raises(SystemExit, match="NOT_FROZEN: DEPLOYMENT_SOURCE_NOT_QUALIFIED"):
        freeze.main()
    assert not output.parent.exists()
