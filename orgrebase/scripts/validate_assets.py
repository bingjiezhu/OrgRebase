"""Fail-closed static validation for deployment, identity, Skill, and fixture assets."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from orgrebase.agentteams_source import load_agentteams_source, load_teamharness_lock
from orgrebase.collaboration import _identity_payload
from orgrebase.digest import sha256_digest
from orgrebase.fixture import load_fixture

ROOT = Path(__file__).resolve().parents[1]
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
    source = load_agentteams_source(ROOT)
    load_teamharness_lock(ROOT)
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
        require(worker["apiVersion"] == source.crd_api_version, "stale Worker apiVersion")
        spec = worker["spec"]
        require(bool(spec.get("model")), "Worker model is required by the pinned CRD")
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
    require(team["apiVersion"] == source.crd_api_version, "stale Team apiVersion")
    members = team["spec"]["workerMembers"]
    require(len(members) == 5, "Team must reference all five Workers")
    require(
        sum(member["role"] == "team_leader" for member in members) == 1,
        "Team requires exactly one leader",
    )
    require({member["name"] for member in members} == worker_names, "Team/Worker membership mismatch")
    return {worker["spec"]["workerName"] for worker in workers}, source.tag


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
    legacy_path = (
        ROOT
        / "skills"
        / "enterprise-launch-readiness"
        / "legacy-contract-v1.3.json"
    )
    legacy = load_json(legacy_path)
    declared = legacy.pop("content_digest")
    actual = sha256_digest(legacy)
    require(declared == actual, f"Skill Contract digest mismatch: expected {actual}")
    require(legacy["version"] == "1.3", "unexpected Skill candidate version")
    require(legacy["permissions"]["side_effects"] == [], "Skill candidate gained side effects")
    require(legacy["permissions"]["sensitivity_ceiling"] == "INTERNAL", "Skill sensitivity widened")

    registry = load_json(ROOT / "configs" / "workspace" / "skill-registry.json")
    require(
        registry.get("schema_version") == "orgrebase.skill-registry.v1"
        and registry.get("authority") == "authority:skill-registry",
        "current Skill registry authority contract drifted",
    )
    expected_versions = {
        "enterprise-launch-readiness": ("1.4.2", "1.4"),
        "enterprise-quote-compose": ("1.3.1", "1.3"),
        "structured-domain-handoff": ("1.1.2", "1.1"),
    }
    entries = registry.get("packages", [])
    require(
        isinstance(entries, list)
        and {item.get("name") for item in entries if isinstance(item, dict)}
        == set(expected_versions),
        "current Skill registry package set drifted",
    )
    expected_resources = {
        "skill": "SKILL.md",
        "reference_zh_cn": "references/zh-CN.md",
        "reference_en": "references/en.md",
        "contract": "contract.json",
        "program": "program.json",
        "input_schema": "input.schema.json",
        "output_schema": "output.schema.json",
    }
    for entry in entries:
        name = str(entry["name"])
        package_version, contract_version = expected_versions[name]
        package_root = ROOT / "skills" / str(entry["path"])
        contract = load_json(package_root / "contract.json")
        contract_declared = contract.pop("content_digest")
        require(
            contract_declared == sha256_digest(contract),
            f"current Skill contract digest mismatch: {name}",
        )
        require(
            contract.get("version") == contract_version,
            f"unexpected current Skill contract version: {name}",
        )
        require(
            contract.get("permissions", {}).get("side_effects") == [],
            f"current Skill package gained side effects: {name}",
        )
        manifest = load_json(package_root / "package.json")
        manifest_declared = manifest.pop("manifest_digest")
        require(
            manifest_declared == sha256_digest(manifest),
            f"current Skill manifest digest mismatch: {name}",
        )
        require(
            manifest.get("schema_version") == "orgrebase.skill-package-manifest.v2"
            and manifest.get("name") == name
            and manifest.get("version") == package_version
            and entry.get("version") == package_version
            and entry.get("manifest_digest") == manifest_declared,
            f"current Skill registry/manifest identity drifted: {name}",
        )
        require(
            manifest.get("permissions")
            == {
                "allowed_tools": [],
                "side_effects": [],
                "effect_ceiling": "CANDIDATE_ONLY",
            },
            f"current Skill effect boundary widened: {name}",
        )
        resources = manifest.get("resources")
        require(
            isinstance(resources, dict) and set(resources) == set(expected_resources),
            f"current Skill v2 resource set drifted: {name}",
        )
        for resource_name, resource_path in expected_resources.items():
            declaration = resources.get(resource_name, {})
            path = package_root / resource_path
            require(
                declaration.get("path") == resource_path
                and declaration.get("sha256")
                == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                f"current Skill resource digest drifted: {name}/{resource_name}",
            )
        input_schema = load_json(package_root / "input.schema.json")
        output_schema = load_json(package_root / "output.schema.json")
        require(
            input_schema.get("$schema")
            == "https://json-schema.org/draft/2020-12/schema"
            and output_schema.get("$schema")
            == "https://json-schema.org/draft/2020-12/schema"
            and input_schema.get("$id") == manifest.get("input_schema_ref")
            and output_schema.get("$id") == manifest.get("output_schema_ref")
            and manifest.get("dependencies", {}).get(manifest["input_schema_ref"])
            == resources["input_schema"]["sha256"]
            and manifest.get("dependencies", {}).get(manifest["output_schema_ref"])
            == resources["output_schema"]["sha256"],
            f"current Skill bundled Schema contract drifted: {name}",
        )
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
