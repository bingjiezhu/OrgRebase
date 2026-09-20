from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from orgrebase.agentteams_source import load_agentteams_source

ROOT = Path(__file__).resolve().parents[1]


def test_packaged_agentteams_bundle_reconstructs_the_locked_checkout(
    tmp_path: Path,
) -> None:
    output = tmp_path / "agentteams-checkout"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/fetch_pinned_agentteams.py"),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    receipt = json.loads(completed.stdout)

    assert receipt["status"] == "PASS"
    assert receipt["mode"] == "PACKAGED_BUNDLE"
    assert receipt["commit"] == load_agentteams_source(ROOT).commit
    assert receipt["origin"] == "https://github.com/agentscope-ai/AgentTeams"
    assert set(receipt["files"]) == {
        "plugins/teamharness/mcp/message_tool.py",
        "plugins/teamharness/mcp/roomflow_tool.py",
        "plugins/teamharness/mcp/server.py",
    }
    assert (
        subprocess.run(
            ["git", "-C", str(output), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.strip()
        == receipt["commit"]
    )
