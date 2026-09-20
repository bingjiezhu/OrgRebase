from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str) -> ModuleType:
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREPARE = _script("prepare_fresh_core_agentteams_run")
DISPATCH = _script("dispatch_fresh_core_agentteams_run")


def _demo() -> dict[str, object]:
    return json.loads((ROOT / "evidence" / "latest" / "demo.json").read_bytes())


def _run_envelope() -> dict[str, object]:
    workers = []
    for name in PREPARE.ALL_WORKERS:
        workers.append(
            {
                "namespace": "orgrebase-agentteams",
                "resource_name": f"orgrebase-{name}",
                "uid": f"uid-{name}",
                "generation": 1,
                "worker_name": name,
                "matrix_user_id": f"@{name}:matrix.example",
                "role": "team_leader" if name == "change-coordinator" else "worker",
                "model": "google/gemini-3.1-flash-lite",
                "runtime": "openclaw",
                "image": None,
                "required_skills": ["enterprise-launch-readiness"]
                if name == "skill-curator"
                else [],
            }
        )
    return {
        "schema_version": PREPARE.RUN_SCHEMA,
        "run_id": "run:orgrebase:live:1787540000000:abc123def456",
        "nonce": "a" * 64,
        "issued_at_ms": 1_787_540_000_000,
        "expires_at_ms": 1_787_547_200_000,
        "agentteams": {
            "version": "v1.2.2",
            "source_commit": "849182af8e017168a5a200a87b1062142caf462d",
        },
        "team": {
            "namespace": "orgrebase-agentteams",
            "name": "orgrebase-change-team",
            "uid": "team-uid",
            "generation": 1,
            "room_id": "!room:matrix.example",
        },
        "workers": workers,
        "runtime_pods": [],
        "skill": {
            "name": "enterprise-launch-readiness",
            "assigned_worker": "skill-curator",
            "digest": f"sha256:{'b' * 64}",
        },
    }


def _schema_bytes() -> bytes:
    return (ROOT / "schemas" / "agentteams-candidate-result.schema.json").read_bytes()


def _bundle():
    return PREPARE.build_bundle(
        demo=_demo(), envelope=_run_envelope(), candidate_schema_bytes=_schema_bytes()
    )


def _projection(files: dict[str, bytes], worker_name: str) -> dict[str, object]:
    return json.loads(files[f"workers/{worker_name}/projection.json"])


def _keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            found.update(_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_keys(item))
    return found


def test_build_is_deterministic_and_binds_four_independent_workers() -> None:
    first_files, first_manifest = _bundle()
    second_files, second_manifest = _bundle()

    assert first_files == second_files
    assert first_manifest == second_manifest
    assert [item["worker_name"] for item in first_manifest["workers"]] == list(
        PREPARE.SPECIALISTS
    )
    assert len(first_files) == 11
    assert first_files["candidate-result.schema.json"] == _schema_bytes()
    assert first_manifest["answer_free_attestation"] == {
        "scanner": "orgrebase.answer-free-task-projection@1.0.0",
        "scope": "worker projection and single-read prompt bytes, including the ordinary-text structural result guide",
        "prior_candidates_included": False,
        "prewritten_candidate_structures_found": 0,
        "canonical_write_authority_granted": False,
        "prompt_contract_guide": "STRUCTURE_ONLY_NO_EXPECTED_BUSINESS_VALUES",
        "schema_delivery": "STAGED_OFFLINE_VALIDATION_ONLY_NOT_A_TOOL_RESPONSE",
        "planned_read_tool_calls_per_worker": 1,
    }
    for worker in first_manifest["workers"]:
        projection = _projection(first_files, worker["worker_name"])
        assert projection["digest"] == worker["projection_digest"]
        assert projection["bindings"]["delegation_task_digest"] == worker[
            "delegation_task_digest"
        ]
        assert projection["bindings"]["input_refs"] == worker["input_refs"]
        assert projection["authority_policy"]["permitted_write"] == "DECLARED_RESULT_FILE_ONLY"
        assert projection["authority_policy"]["prohibited_actions"] == list(
            PREPARE.PROHIBITIONS
        )


def test_role_projections_are_minimal_and_contain_no_candidate_answer() -> None:
    files, _ = _bundle()
    product = _projection(files, "product-steward")["source_projection"]
    legal = _projection(files, "legal-steward")["source_projection"]
    gtm = _projection(files, "gtm-steward")["source_projection"]
    skill = _projection(files, "skill-curator")["source_projection"]

    assert product["delta"]["object_id"] == "claim:product.launch_date"
    assert product["delta"]["base_value"] == "2026-09-01"
    assert product["delta"]["proposed_value"] == "2026-09-15"
    assert "base_value" not in _keys(legal)
    assert "proposed_value" not in _keys(legal)
    assert legal["disclosure"]["restricted_source_access"] == "DENIED"
    assert all("classification" not in target for target in gtm["targets"])
    assert {target["object_id"] for target in gtm["targets"]} == set(PREPARE.GTM_TARGETS)
    assert skill["current_contract"]["version"] == "1.2"
    assert "candidate_version" not in _keys(skill)

    for worker_name in PREPARE.SPECIALISTS:
        projection = _projection(files, worker_name)
        assert not (set(PREPARE.FORBIDDEN_PROJECTION_KEYS) & _keys(projection))
        prompt = files[f"workers/{worker_name}/prompt.txt"].decode()
        assert '"output"' not in prompt
        assert "prior candidates" in prompt
        assert "at most one tool call in each assistant turn" in prompt
        assert prompt.count("issue the only read tool call") == 1
        assert "read the frozen task projection" in prompt
        assert "offline validation contract only" in prompt
        assert "Never read it through a tool" in prompt
        assert "Second, read" not in prompt
        assert "Never issue parallel tool calls" in prompt
        assert "issue write as a separate single-tool turn" in prompt
        assert "Result contract guide" in prompt
        assert "schema_version: string literal" in prompt
        assert "candidate_schema_digest" in prompt
        assert "output: object that must contain exactly one key" in prompt
        assert "that key's exact name is the Primary output kind" in prompt
        assert "Nest every role-specific required field inside that object" in prompt
        assert "never place those fields directly under output" in prompt
        assert (
            f"Primary output kind: {PREPARE.PRIMARY_OUTPUT[worker_name]}. Its object value"
            in prompt
        )
        assert "2026-09-01" not in prompt
        assert "2026-09-15" not in prompt
        assert "claim:legal.customer_notice_required" not in prompt
        assert "work:sales_quote_a" not in prompt
        assert "work:support_doc_b" not in prompt
        assert "work:partner_brief_e" not in prompt
        assert "skill:enterprise-launch-readiness" not in prompt
        assert "value: true" not in prompt.lower()
        assert "value = true" not in prompt.lower()
        assert "candidate_version: 1.3" not in prompt
        assert "from_version: 1.2" not in prompt
        if worker_name == "gtm-steward":
            assert "completed projection read is the declared dependency-evidence-equivalent" in prompt
            assert "tool:read-projection@" in prompt
            assert "classification: one of" in prompt
        else:
            assert "tool_receipt_refs: exactly an empty array" in prompt


def test_preparation_rejects_a_precomputed_candidate_structure() -> None:
    projection = {"source_projection": {"ClaimDeltaCandidate": {"object_id": "x"}}}
    with pytest.raises(PREPARE.PreparationError, match="prewritten answer structure"):
        PREPARE._assert_answer_free(projection, "derive independently")


def test_result_contract_guide_rejects_direct_output_field_ambiguity() -> None:
    ambiguous = (
        "Primary output kind: ClaimDeltaCandidate. "
        "Put object_id and other required fields directly under output."
    )
    with pytest.raises(PREPARE.PreparationError, match="structurally ambiguous"):
        PREPARE._assert_result_contract_guide(ambiguous, "ClaimDeltaCandidate")


@pytest.mark.parametrize(
    "answer_fragment",
    (
        "Use 2026-09-01 -> 2026-09-15.",
        "claim:legal.customer_notice_required value=true",
        "work:sales_quote_a=AFFECTED_HARD",
        "candidate_version: 1.3",
    ),
)
def test_answer_free_scanner_rejects_concrete_business_answer_values(
    answer_fragment: str,
) -> None:
    with pytest.raises(PREPARE.PreparationError, match="business-answer value"):
        PREPARE._assert_answer_free(
            {"source_projection": {"fact": "task input"}},
            f"derive independently\n{answer_fragment}",
        )


def test_preparation_rejects_tampered_demo_digest() -> None:
    demo = _demo()
    demo["change_set"]["deltas"][0]["proposed_value"] = "2026-10-01"
    with pytest.raises(PREPARE.PreparationError, match="ChangeSet content digest mismatch"):
        PREPARE.build_bundle(
            demo=demo,
            envelope=_run_envelope(),
            candidate_schema_bytes=_schema_bytes(),
        )


def test_preparation_rejects_missing_runtime_worker() -> None:
    envelope = _run_envelope()
    envelope["workers"] = envelope["workers"][:-1]
    with pytest.raises(PREPARE.PreparationError, match="exact five-Worker set"):
        PREPARE.build_bundle(
            demo=_demo(), envelope=envelope, candidate_schema_bytes=_schema_bytes()
        )


def test_preparation_rejects_task_input_ref_drift() -> None:
    demo = _demo()
    plan = demo["collaboration"]["orchestration_plan"]
    product_task = next(
        item for item in plan["tasks"] if item["agent_name"] == "product-steward"
    )
    product_task["input_refs"] = [f"sha256:{'0' * 64}"]
    task_payload = {key: value for key, value in product_task.items() if key != "digest"}
    product_task["digest"] = PREPARE._value_digest(task_payload)
    plan_payload = {key: value for key, value in plan.items() if key != "digest"}
    plan["digest"] = PREPARE._value_digest(plan_payload)
    with pytest.raises(PREPARE.PreparationError, match="exactly bound"):
        PREPARE.build_bundle(
            demo=demo,
            envelope=_run_envelope(),
            candidate_schema_bytes=_schema_bytes(),
        )


def test_write_bundle_is_fail_closed_on_existing_output(tmp_path: Path) -> None:
    files, _ = _bundle()
    output = tmp_path / "fresh-bundle"
    PREPARE.write_bundle(files, output)
    before = copy.deepcopy({path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()})

    with pytest.raises(PREPARE.PreparationError, match="refusing to overwrite"):
        PREPARE.write_bundle(files, output)

    after = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    assert after == before


def test_candidate_schema_requires_fresh_projection_and_schema_bindings() -> None:
    schema = json.loads(_schema_bytes())
    required = set(schema["required"])
    assert {
        "run_envelope_digest",
        "task_projection_digest",
        "candidate_schema_digest",
        "orchestration_plan_digest",
        "delegation_task_digest",
        "input_refs",
    }.issubset(required)
    assert schema["additionalProperties"] is False
    gtm_rule = schema["allOf"][2]["then"]["properties"]["tool_receipt_refs"]
    assert gtm_rule["minItems"] == gtm_rule["maxItems"] == 1
    assert gtm_rule["items"]["pattern"].startswith("^tool:read-projection@sha256:")


def test_dispatch_collector_requires_exact_gtm_projection_read_receipt() -> None:
    files, _ = _bundle()
    projection = _projection(files, "gtm-steward")
    bindings = projection["bindings"]
    candidate = {
        "schema_version": DISPATCH.CANDIDATE_SCHEMA,
        "run_id": projection["run_id"],
        "nonce": projection["nonce"],
        "worker_name": "gtm-steward",
        "candidate_only": True,
        "run_envelope_digest": bindings["run_envelope_digest"],
        "task_projection_digest": projection["digest"],
        "candidate_schema_digest": bindings["candidate_schema_digest"],
        "orchestration_plan_digest": bindings["orchestration_plan_digest"],
        "delegation_task_digest": bindings["delegation_task_digest"],
        "input_refs": bindings["input_refs"],
        "output": {
            "ImpactCandidate": {
                "work:sales_quote_a": {
                    "classification": "AFFECTED_HARD",
                    "source_result_digest": f"sha256:{'c' * 64}",
                    "tool_receipt_ref": f"tool:read-projection@{projection['digest']}",
                }
            }
        },
        "uncertainty": [],
        "tool_receipt_refs": [f"tool:read-projection@{projection['digest']}"],
        "prohibited_actions_respected": list(PREPARE.PROHIBITIONS),
    }
    DISPATCH._validate_candidate(candidate, projection, "gtm-steward")

    candidate["tool_receipt_refs"] = [f"tool:read-projection@sha256:{'0' * 64}"]
    with pytest.raises(DISPATCH.DispatchError, match="tool receipt binding invalid"):
        DISPATCH._validate_candidate(candidate, projection, "gtm-steward")

    candidate["tool_receipt_refs"] = [f"tool:read-projection@{projection['digest']}"]
    candidate["output"]["ImpactCandidate"]["work:sales_quote_a"][
        "tool_receipt_ref"
    ] = f"tool:read-projection@sha256:{'0' * 64}"
    with pytest.raises(DISPATCH.DispatchError, match="exact read receipt"):
        DISPATCH._validate_candidate(candidate, projection, "gtm-steward")
