"""Fail-closed AgentTeams live-runtime readiness preflight."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from orgrebase.agentteams_source import load_agentteams_source

ROOT = Path(__file__).resolve().parents[1]
VERTEX_MODEL = "google/gemini-3.1-flash-lite"


def _command(command: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, type(exc).__name__
    detail = (result.stdout or result.stderr).strip().splitlines()
    return result.returncode == 0, (detail[0][:160] if detail else f"exit={result.returncode}")


def _vertex_adc() -> tuple[bool, str]:
    """Check deploy-time Vertex ADC without ever returning the access token."""
    if shutil.which("gcloud") is None:
        return False, "gcloud executable is unavailable"
    project_ready, project = _command(["gcloud", "config", "get-value", "project"])
    if not project_ready or not project or project == "(unset)":
        return False, "gcloud project is unset"
    token_ready, _ = _command(
        ["gcloud", "auth", "application-default", "print-access-token"]
    )
    if not token_ready:
        return False, "Vertex ADC access token is unavailable"
    # Never put the GCP project ID, ADC token, or Vertex URL into evidence.
    return True, f"location=global; model={VERTEX_MODEL}; credentials=adc-present"


def preflight() -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for binary in ("docker", "kubectl"):
        present = shutil.which(binary) is not None
        checks[f"binary.{binary}"] = {"status": "PASS" if present else "BLOCKED"}
        if not present:
            blockers.append(f"missing executable: {binary}")

    if shutil.which("docker"):
        ready, detail = _command(["docker", "info", "--format", "{{.ServerVersion}}"])
        ready = ready and detail not in {"", "exit=0"}
        checks["docker.daemon"] = {
            "status": "PASS" if ready else "BLOCKED",
            "detail": detail,
        }
        if not ready:
            blockers.append("Docker daemon is unavailable")

    api_key_present = bool(os.environ.get("AGENTTEAMS_LLM_API_KEY"))
    vertex_ready, vertex_detail = _vertex_adc() if not api_key_present else (False, "not needed")
    auth_ready = api_key_present or vertex_ready
    checks["llm.authentication"] = {
        "status": "PASS" if auth_ready else "BLOCKED",
        "mode": "api-key" if api_key_present else ("vertex-adc" if vertex_ready else "none"),
        "detail": "configured" if api_key_present else vertex_detail,
        "value_disclosed": False,
    }
    if not auth_ready:
        blockers.append("neither AGENTTEAMS_LLM_API_KEY nor usable Vertex ADC is available")

    asset_ready, detail = _command(
        ["uv", "run", "python", str(ROOT / "scripts" / "validate_assets.py")]
    )
    checks["orgrebase.assets"] = {
        "status": "PASS" if asset_ready else "BLOCKED",
        "detail": detail,
    }
    if not asset_ready:
        blockers.append("OrgRebase AgentTeams assets failed static validation")

    return {
        "schema_version": "orgrebase.agentteams-preflight.v1",
        "status": "READY" if not blockers else "BLOCKED",
        "agentteams_version": load_agentteams_source(ROOT).tag,
        "required_workers": 5,
        "deployment_model": VERTEX_MODEL,
        "deployment_provider": "vertex-ai-openai-compat",
        "checks": checks,
        "blockers": blockers,
        "secrets_disclosed": False,
        "evidence_class": "PASS_STATIC" if not blockers else "NOT_RUN",
        "claim_boundary": (
            "READY means the host can start a live run; it is not proof that a run occurred."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    report = preflight()
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    if args.require_ready and report["status"] != "READY":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
