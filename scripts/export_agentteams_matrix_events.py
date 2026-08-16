"""Export identity-bearing Matrix events for one frozen AgentTeams run.

Run this inside the bound Team Leader pod. The access token is read only from
the runtime environment and is never accepted as an argument or emitted.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

RUN_SCHEMA = "orgrebase.agentteams-live-run.v1"
TOKEN_ENV = "AGENTTEAMS_WORKER_MATRIX_TOKEN"
WORKER_ENV = "AGENTTEAMS_WORKER_NAME"
MATRIX_URL_ENV = "AGENTTEAMS_MATRIX_URL"


class ExportError(RuntimeError):
    """The Matrix source could not be exported without weakening provenance."""


def _request(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            value = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ExportError("Matrix request failed") from exc
    if not isinstance(value, dict):
        raise ExportError("Matrix returned a non-object response")
    return value


def _envelope(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError("invalid run envelope") from exc
    if not isinstance(value, dict) or value.get("schema_version") != RUN_SCHEMA:
        raise ExportError("run envelope schema mismatch")
    return value


def export_events(
    *, envelope: dict[str, Any], matrix_url: str, token: str
) -> list[dict[str, Any]]:
    workers = envelope.get("workers")
    if not isinstance(workers, list):
        raise ExportError("run envelope has no Worker bindings")
    leader = [item for item in workers if item.get("role") == "team_leader"]
    if len(leader) != 1 or os.environ.get(WORKER_ENV) != leader[0].get("worker_name"):
        raise ExportError("exporter is not running as the bound Team Leader")
    base = matrix_url.rstrip("/")
    whoami = _request(f"{base}/_matrix/client/v3/account/whoami", token)
    if whoami.get("user_id") != leader[0].get("matrix_user_id"):
        raise ExportError("Matrix token does not belong to the bound Team Leader")

    room_id = envelope.get("team", {}).get("room_id")
    if not isinstance(room_id, str) or not room_id.startswith("!"):
        raise ExportError("run envelope has no valid Team room")
    room = urllib.parse.quote(room_id, safe="")
    members = _request(f"{base}/_matrix/client/v3/rooms/{room}/members", token).get(
        "chunk", []
    )
    if not isinstance(members, list):
        raise ExportError("Matrix membership export is invalid")

    timeline: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(20):
        query: dict[str, str | int] = {"dir": "b", "limit": 500}
        if cursor:
            query["from"] = cursor
        page = _request(
            f"{base}/_matrix/client/v3/rooms/{room}/messages?"
            + urllib.parse.urlencode(query),
            token,
        )
        chunk = page.get("chunk")
        if not isinstance(chunk, list):
            raise ExportError("Matrix timeline export is invalid")
        timeline.extend(item for item in chunk if isinstance(item, dict))
        oldest = min(
            (
                item.get("origin_server_ts")
                for item in chunk
                if isinstance(item.get("origin_server_ts"), int)
            ),
            default=envelope["issued_at_ms"],
        )
        cursor = page.get("end") if isinstance(page.get("end"), str) else None
        if oldest < envelope["issued_at_ms"] or not cursor or not chunk:
            break

    run_id = envelope.get("run_id")
    nonce = envelope.get("nonce")
    selected: dict[str, dict[str, Any]] = {}
    for event in [*members, *timeline]:
        if not isinstance(event, dict):
            continue
        payload = event.get("content", {}).get("orgrebase.run")
        is_run_event = (
            isinstance(payload, dict)
            and payload.get("run_id") == run_id
            and payload.get("nonce") == nonce
        )
        is_membership = event.get("type") == "m.room.member"
        event_id = event.get("event_id")
        if (is_run_event or is_membership) and isinstance(event_id, str):
            selected[event_id] = event
    events = sorted(
        selected.values(),
        key=lambda item: (item.get("origin_server_ts", 0), item.get("event_id", "")),
    )
    if not any(
        isinstance(item.get("content", {}).get("orgrebase.run"), dict)
        for item in events
    ):
        raise ExportError("no structured OrgRebase events found")
    return events


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-envelope", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--matrix-url", default=os.environ.get(MATRIX_URL_ENV))
    args = parser.parse_args()
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"missing runtime credential environment: {TOKEN_ENV}")
    if not args.matrix_url:
        raise SystemExit(f"missing Matrix URL: pass --matrix-url or set {MATRIX_URL_ENV}")
    try:
        events = export_events(
            envelope=_envelope(args.run_envelope),
            matrix_url=args.matrix_url,
            token=token,
        )
        args.output.write_text(
            "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in events),
            encoding="utf-8",
        )
    except ExportError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"events": len(events), "output": str(args.output)}))


if __name__ == "__main__":
    main()
