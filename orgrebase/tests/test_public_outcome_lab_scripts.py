"""Portable boundary tests for the experiment scripts, without installing tau in the product."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from public_outcome_lab import artifacts  # noqa: E402
from public_outcome_lab.adapter import SANDBOX_POLICY, TauRetailEnvironment  # noqa: E402
from public_outcome_lab.artifacts import (  # noqa: E402
    decode,
    file_identity,
    safe_path,
    seal_directory,
    verify_directory,
    write,
)
from public_outcome_lab.experiment import map_plan  # noqa: E402
from public_outcome_lab.task import DiscoveryReplay, oracle_expectations, public_candidate  # noqa: E402


def scenario():
    return {
        "instructions": {
            "domain": "retail",
            "reason_for_call": "You want to cancel all pending orders.",
            "known_info": "You name is Yara Muller and your zip code is 85041.",
            "task_instructions": "You are mysterious and  don't want to reveal the reason for cancellation until the agent asks. If asked for reason, say you ordered the items by mistake.",
        }
    }


def discovery():
    from orgrebase.workspace.outcome_lab import LabToolRequest

    rows = [
        {
            "request": {
                "tool": "find_user_id_by_name_zip",
                "arguments": {"first_name": "Yara", "last_name": "Muller", "zip": "85041"},
            },
            "result": "u1",
        },
        {
            "request": {"tool": "get_user_details", "arguments": {"user_id": "u1"}},
            "result": {"orders": ["#W1", "#W2"]},
        },
        {
            "request": {"tool": "get_order_details", "arguments": {"order_id": "#W1"}},
            "result": {"status": "pending"},
        },
        {
            "request": {"tool": "get_order_details", "arguments": {"order_id": "#W2"}},
            "result": {"status": "delivered"},
        },
    ]

    for row in rows:
        row["request"] = LabToolRequest.model_validate(row["request"]).model_dump(mode="json")
    return rows


def test_candidate_uses_public_inputs_and_read_results_without_oracle():
    result = public_candidate(scenario(), DiscoveryReplay(discovery()))
    assert result["pending_orders"] == ["#W1"]
    assert result["requests"][-1] == {
        "tool": "cancel_pending_order",
        "arguments": {"order_id": "#W1", "reason": "ordered by mistake"},
    }
    assert len(result["discovery"]) == 4
    assert "evaluation_criteria" not in json.dumps(result)


@pytest.mark.parametrize("edit", ["goal", "user", "reason", "domain"])
def test_unmapped_public_instruction_is_explicitly_rejected(edit):
    data = scenario()
    fields = {
        "goal": "reason_for_call",
        "user": "known_info",
        "reason": "task_instructions",
        "domain": "domain",
    }
    data["instructions"][fields[edit]] = "different instruction"
    with pytest.raises(ValueError, match="TASK_MAPPING_UNSUPPORTED"):
        public_candidate(data, DiscoveryReplay(discovery()))


def test_discovery_replay_cannot_invent_request_or_consume_missing_result():
    rows = discovery()
    rows[0]["request"]["arguments"]["zip"] = "other"
    with pytest.raises(ValueError, match="DISCOVERY_REQUEST_MISMATCH"):
        public_candidate(scenario(), DiscoveryReplay(rows))
    with pytest.raises(ValueError, match="TRACE_INCOMPLETE"):
        public_candidate(scenario(), DiscoveryReplay([]))


def test_discovery_rejects_a_tool_declared_as_mutating():
    reader = DiscoveryReplay(discovery())
    reader.tool_mutates_state = lambda tool: True
    with pytest.raises(ValueError, match="MUST_BE_READ_ONLY"):
        public_candidate(scenario(), reader)
    assert reader.index == 0


def test_oracle_uses_frozen_task_and_seed_not_candidate_result():
    task = {
        "id": "113",
        "evaluation_criteria": {
            "actions": [
                {
                    "name": "cancel_pending_order",
                    "arguments": {"order_id": key, "reason": "ordered by mistake"},
                }
                for key in ("#W1", "#W2")
            ]
        },
    }
    order = {
        "status": "pending",
        "user_id": "u1",
        "payment_history": [{"payment_method_id": "card", "amount": 12, "transaction_type": "payment"}],
    }
    seed = {
        "orders": {"#W1": order, "#W2": order},
        "users": {"u1": {"payment_methods": {"card": {"source": "credit_card"}}}},
    }
    result = oracle_expectations(task, seed)
    assert len(result) == 6
    assert result["/orders/#W1/payment_history"][-1]["transaction_type"] == "refund"
    assert order["payment_history"][0]["transaction_type"] == "payment"
    seed["users"]["u1"]["payment_methods"]["card"]["source"] = "gift_card"
    with pytest.raises(ValueError, match="PAYMENT_MAPPING_UNSUPPORTED"):
        oracle_expectations(task, seed)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'"\xff"'])
def test_transport_json_does_not_normalize_invalid_input(raw):
    with pytest.raises((ValueError, UnicodeDecodeError)):
        decode(raw)


@pytest.mark.parametrize("name", ["../escape", "/root", "a//b", "a/../b", "a\\b", "./a"])
def test_bundle_paths_are_unambiguous_and_relative(tmp_path, name):
    with pytest.raises(ValueError):
        safe_path(tmp_path, name)


def test_artifacts_bind_exact_set_and_never_overwrite(tmp_path):
    write(tmp_path / "receipt.json", {"verdict": "UNKNOWN"})
    root = seal_directory(tmp_path, "test")
    verify_directory(tmp_path, root, "test")
    with pytest.raises(FileExistsError):
        write(tmp_path / "receipt.json", {})
    with pytest.raises(FileExistsError):
        seal_directory(tmp_path, "test")
    with pytest.raises(ValueError, match="ROOT_MISMATCH"):
        verify_directory(tmp_path, "sha256:" + "0" * 64, "test")
    write(tmp_path / "extra.json", {})
    with pytest.raises(ValueError, match="CONTENT_MISMATCH"):
        verify_directory(tmp_path, root, "test")


def test_symlink_materials_are_never_accepted(tmp_path):
    original = tmp_path / "original"
    original.write_bytes(b"a")
    link = tmp_path / "link"
    link.symlink_to(original)
    with pytest.raises(ValueError, match="SYMLINK"):
        safe_path(tmp_path, "link")
    with pytest.raises(ValueError):
        file_identity(link)


def test_source_pin_cannot_be_replaced_by_local_manifest(tmp_path, monkeypatch):
    pins, source = tmp_path / "pins", tmp_path / "source"
    pins.mkdir()
    source.mkdir()
    names = ["LICENSE", *[f"file-{i}" for i in range(269)]]
    for name in names:
        (source / name).write_bytes(b"MIT License" if name == "LICENSE" else b"pinned")
    pin = {
        "repo": "https://github.com/sierra-research/tau2-bench",
        "revision": "672227c6b6676edc20d57ea53b7000262aae77b9",
        "archive_sha256": "0" * 64,
        "files": {name: file_identity(source / name) for name in names},
    }
    write(pins / "tau-source.json", pin)
    monkeypatch.setattr(artifacts, "PINS", pins)
    assert artifacts.verify_source(source)["files_verified"] == 270
    (source / "file-1").write_bytes(b"changed")
    write(source / "source-manifest.json", {"files": {"file-1": file_identity(source / "file-1")}})
    with pytest.raises(ValueError, match="SOURCE_FILE_MISMATCH"):
        artifacts.verify_source(source)


def test_plan_mapping_derives_exact_work_units_and_predecessors(tmp_path):
    prepared, inputs = tmp_path / "prepared", tmp_path / "inputs"
    prepared.mkdir()
    inputs.mkdir()
    d = "sha256:" + "0" * 64
    request = {"tool": "get_user_details", "arguments": {"user_id": "u1"}}
    write(
        prepared / "candidate.json",
        {
            "requests": [
                request,
                {
                    "tool": "cancel_pending_order",
                    "arguments": {"order_id": "#W1", "reason": "ordered by mistake"},
                },
            ],
            "pending_orders": ["#W1"],
            "user_id": "u1",
        },
    )
    write(prepared / "public-task.json", {"evaluation_criteria": {}})
    write(prepared / "oracle-expectations.json", {"/status": "done"})
    write(prepared / "seed-state.json", {"status": "pending"})
    write(
        prepared / "source.json",
        {"repository": "https://github.com/sierra-research/tau2-bench", "revision": "6" * 40},
    )
    write(inputs / "change.json", {"digest": d})
    write(
        inputs / "snapshot.json",
        {
            "digest": d,
            "spec": {
                "dependencyEdges": [],
                "completeness": {"discoveryRoleRef": "role:review"},
                "roleDefinitions": [{"roleId": "role:cancel"}, {"roleId": "role:review"}],
            },
        },
    )
    write(
        inputs / "plan.json",
        {
            "digest": d,
            "spec": {
                "obligations": [
                    {
                        "targetRef": "request:113",
                        "obligationId": "obligation:review",
                        "requiredRoleRef": "role:review",
                        "requiredEvidence": ["evidence:review"],
                    },
                    {
                        "targetRef": "order:1",
                        "obligationId": "obligation:cancel",
                        "requiredRoleRef": "role:cancel",
                        "requiredEvidence": ["evidence:cancel"],
                    },
                ],
                "roleInstances": [{"roleDefinitionRef": "role:review"}],
                "workUnits": [
                    {"workUnitId": "work:review", "obligationRefs": ["obligation:review"]},
                    {"workUnitId": "work:cancel", "obligationRefs": ["obligation:cancel"]},
                ],
                "happensBefore": [],
            },
        },
    )
    write(
        inputs / "baselines.json",
        {
            "fixed-team": ["role:cancel", "role:review"],
            **{system: ["role:review"] for system in ("initiator-only", "graph-only", "oac")},
        },
    )
    mapping, _, requests = map_plan(prepared, inputs, d)
    assert mapping.grants[0].work_unit_ref == "work:review"
    assert mapping.work_unit_predecessors == {"work:review": (), "work:cancel": ()}
    assert requests[0].tool == "get_user_details"


@pytest.mark.parametrize("body,timeout", [({}, 0), ({}, float("nan")), ({"a": "x" * 70000}, 1)])
def test_transport_rejects_unbounded_requests_before_io(body, timeout):
    environment = TauRetailEnvironment.__new__(TauRetailEnvironment)
    with pytest.raises(ValueError, match="TRANSPORT_BOUND_REQUIRED"):
        environment._request(body, timeout)


def test_worker_policy_is_os_enforced_and_credentials_are_not_forwarded():
    import inspect

    from public_outcome_lab import adapter

    assert "(deny network*)" in SANDBOX_POLICY and "(deny file-write*)" in SANDBOX_POLICY
    text = inspect.getsource(adapter.TauRetailEnvironment.__init__)
    assert "os.environ" not in text
    assert '"-I"' in text and '"-B"' in text and '"-S"' in text
    assert "PUBLIC_LAB_OS_SANDBOX_UNAVAILABLE" in text


def test_cli_help_is_available_without_tau_import(tmp_path):
    completed = subprocess.run(
        [sys.executable, "-m", "public_outcome_lab", "--help"],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT / "scripts")},
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "check-source" in completed.stdout and "replay" in completed.stdout


@pytest.mark.parametrize(
    "attack",
    [
        "dependency-bytes",
        "dependency-missing",
        "python",
        "machine",
        "tau-source",
        "extra-distribution",
        "opened-seed",
    ],
)
def test_worker_identity_binds_real_dependency_bytes_and_source(attack):
    from copy import deepcopy

    from public_outcome_lab.adapter import verify_worker_identity
    from public_outcome_lab.artifacts import PINS, load

    runtime = deepcopy(load(PINS / "tau-environment-macos-arm64-py312.json"))
    runtime["tau_files"] = {
        key: value
        for key, value in load(PINS / "tau-source.json")["files"].items()
        if key.startswith("src/tau2/")
    }
    runtime["seed_bytes_digest"] = (
        "sha256:" + load(PINS / "tau-source.json")["files"]["data/tau2/domains/retail/db.json"]["sha256"]
    )
    runtime["distributions"] = {name: value["version"] for name, value in runtime["dependency_files"].items()}
    verify_worker_identity(runtime)
    if attack == "dependency-bytes":
        runtime["dependency_files"]["pydantic"]["digest"] = "sha256:" + "0" * 64
    elif attack == "dependency-missing":
        runtime["dependency_files"].pop("pydantic")
    elif attack == "python":
        runtime["python_binary_digest"] = "sha256:" + "0" * 64
    elif attack == "opened-seed":
        runtime["seed_bytes_digest"] = "sha256:" + "0" * 64
    elif attack == "machine":
        runtime["machine"] = "other"
    elif attack == "tau-source":
        runtime["tau_files"]["src/tau2/__init__.py"]["sha256"] = "0" * 64
    else:
        runtime["distributions"]["injected"] = "1"
    with pytest.raises(ValueError, match="MISMATCH"):
        verify_worker_identity(runtime)


@pytest.mark.parametrize("system", ["fixed-team", "initiator-only", "graph-only", "oac"])
def test_baseline_labels_cannot_be_rebound_to_different_roles(system):
    from public_outcome_lab.baselines import baseline_roles, verify_baseline_roles

    snapshot = {
        "spec": {
            "dependencyEdges": [],
            "completeness": {"discoveryRoleRef": "role:review"},
            "roleDefinitions": [{"roleId": "role:review"}, {"roleId": "role:cancel"}],
        }
    }
    plan = {
        "spec": {
            "roleInstances": [{"roleDefinitionRef": "role:cancel"}, {"roleDefinitionRef": "role:review"}]
        }
    }
    roles = baseline_roles(snapshot, plan)
    assert verify_baseline_roles(snapshot, plan, roles) == roles
    roles[system] = ["role:cancel"]
    with pytest.raises(ValueError, match="BASELINE_ROLES_MISMATCH"):
        verify_baseline_roles(snapshot, plan, roles)


def test_worker_skips_pth_and_sitecustomize_even_when_present(tmp_path):
    site = tmp_path / "toolkit"
    retail = site / "tau2/domains/retail"
    retail.mkdir(parents=True)
    (retail / "data_model.py").write_text("class RetailDB: pass\n")
    (retail / "tools.py").write_text("class RetailTools: pass\n")
    (site / "tau2/__init__.py").write_text("")
    (site / "sitecustomize.py").write_text("raise RuntimeError('UNEXPECTED_SITE_EXECUTION')\n")
    (site / "injected.pth").write_text("import sys; raise RuntimeError('UNEXPECTED_PTH_EXECUTION')\n")
    data = tmp_path / "data/tau2/domains/retail"
    data.mkdir(parents=True)
    (data / "db.json").write_text("{}")
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(ROOT / "scripts/public_outcome_lab/worker.py"), str(site)],
        input='{"operation":"identity"}\n',
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "TAU2_DATA_DIR": str(tmp_path / "data")},
        cwd=tmp_path,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["result"]["seed_bytes_digest"] == artifacts.sha(b"{}")
    assert "UNEXPECTED" not in completed.stdout + completed.stderr
