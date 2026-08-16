"""Probe Vertex AI's OpenAI-compatible endpoint and emit a redacted receipt."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_LOCATION = "global"
DEFAULT_MODEL = "google/gemini-3.1-flash-lite"


def _gcloud(*args: str) -> str:
    result = subprocess.run(
        ["gcloud", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gcloud {' '.join(args[:2])} failed")
    value = result.stdout.strip()
    if not value or value == "(unset)":
        raise RuntimeError(f"gcloud {' '.join(args[:2])} returned no value")
    return value


def probe(*, project: str | None, location: str, model: str, timeout: float) -> dict[str, Any]:
    started_ms = int(time.time() * 1000)
    try:
        resolved_project = project or os.environ.get("GOOGLE_CLOUD_PROJECT") or _gcloud(
            "config", "get-value", "project"
        )
        token = _gcloud("auth", "application-default", "print-access-token")
        host = (
            "aiplatform.googleapis.com"
            if location == "global"
            else f"{location}-aiplatform.googleapis.com"
        )
        endpoint = (
            f"https://{host}/v1beta1/projects/{resolved_project}/locations/{location}"
            "/endpoints/openapi/chat/completions"
        )
        payload = json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "user", "content": "Reply with exactly ORGREBASE_VERTEX_OK"}
                ],
                "temperature": 0,
                "max_tokens": 32,
            }
        ).encode()
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "OrgRebase/vertex-provider-probe",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
        choices = body.get("choices", [])
        content = choices[0].get("message", {}).get("content", "") if choices else ""
        if content.strip() != "ORGREBASE_VERTEX_OK":
            raise RuntimeError("provider returned an unexpected probe response")
        status = "PASS"
        request_id = body.get("id")
        error = None
    except (OSError, RuntimeError, ValueError, urllib.error.URLError) as exc:
        status = "BLOCKED"
        request_id = None
        error = type(exc).__name__

    return {
        "schema_version": "orgrebase.vertex-provider-probe.v1",
        "status": status,
        "provider": "vertex-ai-openai-compat",
        "project": "REDACTED",
        "location": location,
        "model": model,
        "provider_request_id": request_id,
        "timestamp_ms": started_ms,
        "credentials_disclosed": False,
        "prompt_or_completion_disclosed": False,
        "evidence_class": "LIVE_PROVIDER_PROBE" if status == "PASS" else "NOT_RUN",
        "claim_boundary": (
            "Proves one Vertex provider request only; it is not AgentTeams Worker, Matrix, "
            "candidate-artifact, or LIVE_AGENTTEAMS evidence."
        ),
        "error": error,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    receipt = probe(
        project=args.project,
        location=args.location,
        model=args.model,
        timeout=args.timeout,
    )
    encoded = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    if receipt["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
