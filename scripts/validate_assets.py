"""Fail-closed static validation for deployment, identity, Skill, and fixture assets."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from orgrebase.collaboration import _identity_payload
from orgrebase.digest import sha256_digest
from orgrebase.fixture import load_fixture

ROOT = Path(__file__).resolve().parents[1]
AGENTTEAMS_SOURCE_COMMIT = "849182af8e017168a5a200a87b1062142caf462d"
AGENTTEAMS_MODEL = "google/gemini-3.1-flash-lite"
IDENTITY_FIELDS = {
    "name",
    "role",
    "capabilities",
    "inputs",
    "outputs",
    "dependencies",
    "decision_boundary",
    "trace",
    "authority_domain",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"ASSET_VALIDATION_FAILED: {message}")


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def validate_agentteams() -> tuple[set[str], str]:
    source_lock = load_json(ROOT / "agentteams" / "source-lock.json")
    require(
        source_lock
        == {
            "schema_version": "orgrebase.agentteams-source-lock.v1",
            "repository": "https://github.com/agentscope-ai/AgentTeams",
            "tag": "v1.2.2",
            "commit": AGENTTEAMS_SOURCE_COMMIT,
            "crd_api_version": "agentteams.io/v1beta1",
        },
        "AgentTeams source lock drifted",
    )
    manifest_path = ROOT / "agentteams" / "team.yaml"
    text = manifest_path.read_text(encoding="utf-8")
    require(
        not re.search(r"(?i)(api[_-]?key|access[_-]?token|client[_-]?secret)\s*:", text),
        "secret-like field in AgentTeams manifest",
    )
    documents = tuple(yaml.safe_load_all(text))
    workers = [item for item in documents if item["kind"] == "Worker"]
    teams = [item for item in documents if item["kind"] == "Team"]
    require(len(workers) == 5, "AgentTeams manifest must declare exactly five Workers")
    require(len(teams) == 1, "AgentTeams manifest must declare exactly one Team")
    worker_names: set[str] = set()
    for worker in workers:
        require(worker["apiVersion"] == "agentteams.io/v1beta1", "stale Worker apiVersion")
        spec = worker["spec"]
        require(bool(spec.get("model")), "Worker model is required by v1.2.2 CRD")
        require(spec["model"] == AGENTTEAMS_MODEL, "Worker model drifted from tested Vertex model")
        require(spec.get("runtime") in {"openclaw", "copaw", "hermes", "qwenpaw"}, "invalid Worker runtime")
        require(
            all(spec.get(field) for field in ("workerName", "identity", "soul", "agents")),
            "incomplete Worker identity",
        )
        require(
            all(server.get("transport", "http") in {"http", "sse"} for server in spec.get("mcpServers", [])),
            "invalid MCP transport",
        )
        worker_names.add(worker["metadata"]["name"])
    team = teams[0]
    require(team["apiVersion"] == "agentteams.io/v1beta1", "stale Team apiVersion")
    members = team["spec"]["workerMembers"]
    require(len(members) == 5, "Team must reference all five Workers")
    require(
        sum(member["role"] == "team_leader" for member in members) == 1,
        "Team requires exactly one leader",
    )
    require({member["name"] for member in members} == worker_names, "Team/Worker membership mismatch")
    return {worker["spec"]["workerName"] for worker in workers}, "v1.2.2"


def validate_identities(expected_names: set[str]) -> None:
    identity_paths = sorted((ROOT / "agentteams" / "identities").glob("*.json"))
    require(len(identity_paths) == 5, "exactly five standalone identities are required")
    identities = [load_json(path) for path in identity_paths]
    for identity in identities:
        require(set(identity) == IDENTITY_FIELDS, f"identity fields drifted: {identity.get('name')}")
        require(
            all(identity[field] for field in IDENTITY_FIELDS),
            f"identity has empty field: {identity.get('name')}",
        )
    require({item["name"] for item in identities} == expected_names, "identity and Worker names differ")
    fixture = load_fixture()
    for agent in fixture.agents:
        path = ROOT / "agentteams" / "identities" / f"{agent.name}.json"
        require(path.is_file(), f"missing identity file: {agent.name}")
        require(
            load_json(path) == _identity_payload(agent),
            f"identity contract drifted from fixture: {agent.name}",
        )


def validate_skill() -> str:
    path = ROOT / "skills" / "enterprise-launch-readiness" / "contract.json"
    contract = load_json(path)
    declared = contract.pop("content_digest")
    actual = sha256_digest(contract)
    require(declared == actual, f"Skill Contract digest mismatch: expected {actual}")
    require(contract["version"] == "1.3", "unexpected Skill candidate version")
    require(contract["permissions"]["side_effects"] == [], "Skill candidate gained side effects")
    require(contract["permissions"]["sensitivity_ceiling"] == "INTERNAL", "Skill sensitivity widened")
    return actual


def validate_project_hygiene() -> None:
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for folder in ("src", "scripts", "agentteams", "skills", "contracts")
        for path in (ROOT / folder).rglob("*")
        if path.is_file() and path.suffix in {".py", ".json", ".yaml", ".md"}
    )
    private_prefix = "/" + "Users" + "/"
    require(private_prefix not in source_text, "private absolute path leaked into project")
    for path in sorted((ROOT / "contracts").glob("*.json")) + sorted((ROOT / "schemas").glob("*.json")):
        load_json(path)


def main() -> None:
    worker_names, agentteams_version = validate_agentteams()
    validate_identities(worker_names)
    fixture = load_fixture()
    require(
        {agent.name for agent in fixture.agents} == worker_names,
        "fixture and deployment identities differ",
    )
    contract_digest = validate_skill()
    validate_project_hygiene()
    print(
        json.dumps(
            {
                "status": "PASS",
                "agentteams_version": agentteams_version,
                "workers": len(worker_names),
                "skill_contract_digest": contract_digest,
                "fixture_digest": fixture.digest,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
