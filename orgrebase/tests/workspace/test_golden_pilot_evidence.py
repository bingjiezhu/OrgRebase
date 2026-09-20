from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(
    os.environ.get(
        "ORGREBASE_GOLDEN_EVIDENCE_ROOT",
        ROOT / "evidence/golden-competition/latest/pilot",
    )
).resolve()


def _load_verifier():
    path = ROOT / "scripts/verify_golden_pilot_evidence.py"
    spec = importlib.util.spec_from_file_location("verify_golden_pilot_evidence", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_finalizer():
    path = ROOT / "scripts/finalize_golden_pilot_evidence.py"
    spec = importlib.util.spec_from_file_location("finalize_golden_pilot_evidence", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("loader", (_load_finalizer, _load_verifier), ids=("finalizer", "verifier"))
@pytest.mark.parametrize("legacy", [False, True])
def test_quote_qualification_suite_accepts_only_its_exact_case_identity(loader, legacy):
    from tests.workspace.test_golden_skill_release_projection import _released_skill_chain

    module = loader()
    check = (module.has_complete_case_identity if hasattr(module, "has_complete_case_identity")
             else module._quote_skill_case_identity)
    chain = _released_skill_chain(legacy=legacy)
    original, run_id = chain["evaluation"], chain["run_id"]
    assert check(original, run_id)
    assert not check(original, "other-run")
    for mutation in ("duplicate", "missing", "wrong_partition", "wrong_revision", "wrong_digest", "wrong_suite"):
        changed = deepcopy(original)
        if mutation == "duplicate":
            changed["case_results"][-1] = deepcopy(changed["case_results"][0])
        elif mutation == "missing":
            changed["case_results"].pop()
        elif mutation == "wrong_partition":
            changed["case_results"][-1]["partition"] = "REPLAY"
        elif mutation == "wrong_revision":
            changed["premise_lock"]["qualification_suite_revision"] = "unrecognized"
        elif mutation == "wrong_digest":
            changed["premise_lock"]["qualification_suite_digest"] = "sha256:" + "0" * 64
        elif legacy:
            from orgrebase.workspace.quote_skill_qualification import REVISION, SUITE_DIGEST
            changed["premise_lock"].update(qualification_suite_revision=REVISION, qualification_suite_digest=SUITE_DIGEST)
        else:
            changed["premise_lock"].pop("qualification_suite_revision")
            changed["premise_lock"].pop("qualification_suite_digest")
        assert not check(changed, run_id), mutation


@pytest.mark.parametrize(
    "loader", (_load_verifier, _load_finalizer), ids=("verifier", "finalizer")
)
@pytest.mark.parametrize("model_id", ("gemini-3.7-flash", "gemini-3.8-flash"))
def test_evidence_tool_accepts_only_supported_vertex_models(
    monkeypatch: pytest.MonkeyPatch,
    loader,
    model_id: str,
) -> None:
    monkeypatch.setenv("ORGREBASE_VERTEX_MODEL_ID", model_id)

    module = loader()

    assert model_id == module.VERTEX_MODEL_ID
    assert {
        "gemini-3.7-flash",
        "gemini-3.8-flash",
    } == module.SUPPORTED_VERTEX_MODEL_IDS


@pytest.mark.parametrize(
    "loader", (_load_verifier, _load_finalizer), ids=("verifier", "finalizer")
)
@pytest.mark.parametrize("model_id", ("", "gemini-3.8-flash-preview", "gemini-pro"))
def test_evidence_tool_rejects_unsupported_vertex_model_at_load(
    monkeypatch: pytest.MonkeyPatch,
    loader,
    model_id: str,
) -> None:
    monkeypatch.setenv("ORGREBASE_VERTEX_MODEL_ID", model_id)

    with pytest.raises(RuntimeError, match=r"^UNSUPPORTED_VERTEX_MODEL_ID:"):
        loader()


def _sealed_vertex_summary(module, model_pairs: tuple[tuple[str, str], ...]):
    seal = module._digest if hasattr(module, "_digest") else module.sha256_digest
    attempts = []
    for phase, (requested, observed) in enumerate(model_pairs, start=1):
        body = {
            "phase": phase,
            "provider": "vertex-ai",
            "requested_model_id": requested,
            "observed_model_version": observed,
            "finish_reason": "STOP",
            "provider_request_id": f"vertex-request-{phase}",
            "advisory_accepted": phase == 2,
            "advisory_disposition": (
                "MODEL_ADVISORY_MATCHED_DETERMINISTIC_VERIFIER"
                if phase == 2
                else "MODEL_ADVISORY_OVERRIDDEN_BY_DETERMINISTIC_VERIFIER"
            ),
        }
        attempts.append({**body, "digest": seal(body)})
    return {
        "model_provider": "vertex-ai",
        "reviewer_model_attempts": attempts,
    }


def test_standalone_verifier_derives_sealed_vertex_38_without_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORGREBASE_VERTEX_MODEL_ID", raising=False)
    module = _load_verifier()
    summary = _sealed_vertex_summary(
        module,
        (("gemini-3.8-flash", "gemini-3.8-flash"),) * 2,
    )

    expected = module._expected_model_manifest(summary)

    assert module.VERTEX_MODEL_ID is None
    assert module._vertex_model_id_from_summary(summary) == "gemini-3.8-flash"
    assert expected is not None
    assert expected["model_id"] == "gemini-3.8-flash"
    assert expected["model_version"] == "gemini-3.8-flash"


def test_finalizer_derives_sealed_vertex_38_without_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORGREBASE_VERTEX_MODEL_ID", raising=False)
    module = _load_finalizer()
    summary = _sealed_vertex_summary(
        module,
        (("gemini-3.8-flash", "gemini-3.8-flash"),) * 2,
    )

    assert module.VERTEX_MODEL_ID is None
    assert module._vertex_model_id_from_summary(summary) == "gemini-3.8-flash"


def test_finalizer_rejects_explicit_vertex_constraint_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORGREBASE_VERTEX_MODEL_ID", "gemini-3.7-flash")
    module = _load_finalizer()
    summary = _sealed_vertex_summary(
        module,
        (("gemini-3.8-flash", "gemini-3.8-flash"),) * 2,
    )

    assert module._vertex_model_id_from_summary(summary) is None


def test_standalone_verifier_rejects_explicit_vertex_constraint_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORGREBASE_VERTEX_MODEL_ID", "gemini-3.7-flash")
    module = _load_verifier()
    summary = _sealed_vertex_summary(
        module,
        (("gemini-3.8-flash", "gemini-3.8-flash"),) * 2,
    )

    assert module._vertex_model_id_from_summary(summary) is None
    assert module._expected_model_manifest(summary) is None


@pytest.mark.parametrize(
    "model_pairs",
    (
        (("gemini-3.8-flash-preview", "gemini-3.8-flash-preview"),) * 2,
        (
            ("gemini-3.8-flash", "gemini-3.8-flash"),
            ("gemini-3.8-flash", "gemini-3.7-flash"),
        ),
        (
            ("gemini-3.8-flash", "gemini-3.8-flash"),
            ("gemini-3.7-flash", "gemini-3.7-flash"),
        ),
    ),
    ids=("unsupported", "requested-observed-drift", "attempt-drift"),
)
def test_standalone_verifier_rejects_untrusted_vertex_model_evidence(
    monkeypatch: pytest.MonkeyPatch,
    model_pairs: tuple[tuple[str, str], ...],
) -> None:
    monkeypatch.delenv("ORGREBASE_VERTEX_MODEL_ID", raising=False)
    module = _load_verifier()
    summary = _sealed_vertex_summary(module, model_pairs)

    assert module._vertex_model_id_from_summary(summary) is None
    assert module._expected_model_manifest(summary) is None


def test_standalone_verifier_has_no_orgrebase_import() -> None:
    source = (ROOT / "scripts/verify_golden_pilot_evidence.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (
            node.names
            if isinstance(node, ast.Import)
            else [ast.alias(name=node.module or "")]
        )
    }

    assert not any(name == "orgrebase" or name.startswith("orgrebase.") for name in imports)


def _write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _copy_and_reseal(
    tmp_path: Path,
    module,
    source: Path = EVIDENCE,
    *,
    finalize_public: bool = True,
) -> Path:
    root = tmp_path / "pilot"
    shutil.copytree(source, root)
    if finalize_public:
        manifest = _load_finalizer().build_manifest(root)
        _write_json(root / "manifest.json", manifest)
    return root


def _reseal_manifest(root: Path, module) -> None:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = module._entries(root)
    manifest["files"]["entries"] = entries
    manifest["files"]["entry_count"] = len(entries)
    manifest["files"]["pack_digest"] = module._digest(entries)
    manifest["digest"] = module._digest({key: value for key, value in manifest.items() if key != "digest"})
    _write_json(manifest_path, manifest)


def _assert_causal_failure(module, root: Path, code: str) -> None:
    _reseal_manifest(root, module)
    result = module.verify(root)
    assert result["status"] == "FAIL"
    assert result["causal_verification"] == "FAIL"
    assert code in result["failures"]


def _oac_bound_public_projection(finalizer):
    state = json.loads((EVIDENCE / "state.json").read_text(encoding="utf-8"))
    evidence = json.loads(
        (EVIDENCE / "evidence-export.json").read_text(encoding="utf-8")
    )
    quote = json.loads(
        (EVIDENCE / "quote-export.json").read_text(encoding="utf-8")
    )
    run_id = state["execution"]["run_id"]
    binding_digest = finalizer.sha256_digest(
        {"run_id": run_id, "contract": "oac-activation"}
    )
    previous = "sha256:" + "0" * 64
    records = []
    for sequence_no, event_type in enumerate(
        finalizer.OAC_BOUND_PUBLIC_EVENT_TYPES, start=1
    ):
        event_digest = finalizer.sha256_digest(
            {
                "sequence_no": sequence_no,
                "event_type": event_type,
                "previous_digest": previous,
            }
        )
        records.append(
            {
                "sequence_no": sequence_no,
                "event_type": event_type,
                "previous_digest": previous,
                "event_digest": event_digest,
            }
        )
        previous = event_digest
    event_chain = {
        "status": "PASS",
        "events": len(records),
        "head_digest": previous,
        "records": records,
    }
    oac_activation = {
        "status": "CONSUMED_BY_QUOTE_FORMATION",
        "execution_run_id": run_id,
        "activation_binding_digest": binding_digest,
    }
    task_intake = {
        "status": "FORMATION_COMPLETED",
        "run_id": run_id,
        "oac_activation_binding_digest": binding_digest,
        "event_digest": records[6]["event_digest"],
        "intake_persisted": True,
        "intake_canonical_target_writes": 0,
    }
    event_scopes = {
        "schema_version": "orgrebase.workspace-event-scopes.v2",
        "layout": "OAC_PREFIX_QUOTE_SUFFIX",
        "quote_business": {
            "status": "PASS",
            "events": 12,
            "head_digest": previous,
            "first_sequence_no": 5,
            "last_sequence_no": 16,
            "start_anchor_digest": records[3]["event_digest"],
            "definition": "CONTIGUOUS_NON_OAC_SUFFIX",
        },
        "workspace_global": {
            "status": "PASS",
            "events": 16,
            "head_digest": previous,
            "definition": "FULL_APPEND_ONLY_WORKSPACE_CHAIN",
        },
        "workspace_prelude": {
            "status": "PASS",
            "events": 1,
            "first_sequence_no": 1,
            "last_sequence_no": 1,
            "head_digest": records[0]["event_digest"],
            "definition": "LEADING_WORKSPACE_INITIALIZATION_NOT_QUOTE_BUSINESS",
        },
        "oac_adaptation": {
            "status": "PASS",
            "events": 3,
            "first_sequence_no": 2,
            "last_sequence_no": 4,
            "start_anchor_digest": records[0]["event_digest"],
            "head_digest": records[3]["event_digest"],
            "definition": "OAC_GOVERNANCE_EVENTS_ZERO_QUOTE_MUTATION_AUTHORITY",
        },
        "relationship": {
            "event_layout": "OAC_PREFIX_QUOTE_SUFFIX",
            "honest_contiguous_windows_published": True,
            "quote_is_contiguous_prefix": False,
            "quote_is_contiguous_suffix": True,
            "oac_events_are_prefix": True,
            "oac_events_are_suffix": False,
        },
    }
    state["execution"]["oac_activation"] = oac_activation
    state["competition_evidence"]["run_id"] = run_id
    state["task_intake"] = task_intake
    state["event_chain"] = event_chain
    state["event_scopes"] = event_scopes
    evidence["oac_activation_consumption"] = deepcopy(oac_activation)
    evidence["task_intake"] = deepcopy(task_intake)
    evidence["event_chain"] = deepcopy(event_chain)
    evidence["event_scopes"] = deepcopy(event_scopes)
    for kind, sequence_no in (("launch_date", 10), ("currency", 14)):
        approval = state["changes"][kind]["approval"][
            "approval_review_evidence"
        ]
        approval["event_sequence_no"] = sequence_no
        approval["event_digest"] = records[sequence_no - 1]["event_digest"]
    evidence["changes"] = deepcopy(state["changes"])
    return state, evidence, quote


def test_oac_bound_public_profile_is_consistent_between_finalizer_and_verifier() -> None:
    finalizer = _load_finalizer()
    verifier = _load_verifier()
    state, evidence, quote = _oac_bound_public_projection(finalizer)

    finalizer_facts = finalizer._public_state_facts(state, evidence, quote)
    verifier_failures, verifier_facts = verifier._verify_public_state_closure(
        state=state,
        evidence=evidence,
        quote=quote,
    )

    assert verifier_failures == []
    assert verifier_facts == finalizer_facts
    assert finalizer_facts == {
        **finalizer_facts,
        "event_profile": "OAC_BOUND_TASK_INTAKE_V1",
        "oac_bound": True,
        "task_intake_event_bound": True,
        "event_count": 16,
        "approval_count": 2,
    }


def test_oac_bound_public_profile_rejects_foreign_task_intake_run() -> None:
    finalizer = _load_finalizer()
    verifier = _load_verifier()
    state, evidence, quote = _oac_bound_public_projection(finalizer)
    foreign_run = "run:golden-competition:foreign-task-intake"
    state["task_intake"]["run_id"] = foreign_run
    evidence["task_intake"]["run_id"] = foreign_run

    with pytest.raises(
        finalizer.GoldenPilotEvidenceError,
        match=r"^PUBLIC_OAC_TASK_BINDING_INVALID$",
    ):
        finalizer._public_state_facts(state, evidence, quote)
    verifier_failures, _ = verifier._verify_public_state_closure(
        state=state,
        evidence=evidence,
        quote=quote,
    )

    assert "PUBLIC_OAC_TASK_BINDING" in verifier_failures


def test_oac_bound_public_profile_rejects_wrong_event_scope_anchor() -> None:
    finalizer = _load_finalizer()
    verifier = _load_verifier()
    state, evidence, quote = _oac_bound_public_projection(finalizer)
    wrong_anchor = "sha256:" + "0" * 64
    state["event_scopes"]["quote_business"]["start_anchor_digest"] = wrong_anchor
    evidence["event_scopes"]["quote_business"]["start_anchor_digest"] = wrong_anchor

    with pytest.raises(
        finalizer.GoldenPilotEvidenceError,
        match=r"^PUBLIC_OAC_EVENT_SCOPES_INVALID$",
    ):
        finalizer._public_state_facts(state, evidence, quote)
    verifier_failures, _ = verifier._verify_public_state_closure(
        state=state,
        evidence=evidence,
        quote=quote,
    )

    assert "PUBLIC_OAC_EVENT_SCOPES" in verifier_failures


def test_oac_bound_public_profile_rejects_legacy_approval_sequence() -> None:
    finalizer = _load_finalizer()
    verifier = _load_verifier()
    state, evidence, quote = _oac_bound_public_projection(finalizer)
    approval = state["changes"]["launch_date"]["approval"][
        "approval_review_evidence"
    ]
    approval["event_sequence_no"] = 6
    approval["event_digest"] = state["event_chain"]["records"][5]["event_digest"]
    evidence["changes"] = deepcopy(state["changes"])

    with pytest.raises(
        finalizer.GoldenPilotEvidenceError,
        match=r"^PUBLIC_CHANGE_CLOSURE_INVALID:launch_date$",
    ):
        finalizer._public_state_facts(state, evidence, quote)
    verifier_failures, _ = verifier._verify_public_state_closure(
        state=state,
        evidence=evidence,
        quote=quote,
    )

    assert "PUBLIC_CHANGE_CLOSURE:launch_date" in verifier_failures


def test_frozen_golden_pilot_evidence_is_independently_replayable(
    tmp_path: Path,
) -> None:
    verifier = _load_verifier()
    root = _copy_and_reseal(tmp_path, verifier)
    result = verifier.verify(root)

    assert result["status"] == "PASS"
    assert result["verification_mode"] == "STDLIB_ONLY_NO_PRODUCT_IMPORTS"
    assert result["product_imports"] == 0
    assert result["entry_count"] >= 80
    assert result["causal_verification"] == "PASS"
    assert result["public_state_verification"] == "PASS"
    assert result["run_id"].startswith("run:golden-competition:")
    assert result["failures"] == []
    assert not (root / "workspace.sqlite3").exists()

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["workflow"]["orchestration_node_count"] == 8
    assert manifest["workflow"]["native_task_binding_count"] == 7
    assert "task_count" not in manifest["workflow"]
    if manifest["schema_version"].endswith(".v3"):
        assert result["experience_verification"] == "PASS"
        model = manifest["workflow"]["model"]
        assert model["provider"] in {"vertex-ai", "ollama-local"}
        if model["provider"] == "vertex-ai":
            assert model["model_id"] == "gemini-3.8-flash"
            assert model["evidence_class"] == "LIVE_MODEL"
            assert model["provider_evidence_class"] == "LIVE_VERTEX_MODEL"
            assert model["provider_request_ids_distinct"] is True
            assert model["thinking_level"] == "LOW"
        else:
            assert model["provider"] == "ollama-local"
            assert model["model_id"] == "qwen2.5:3b"
            assert model["model_version"].startswith("ollama-manifest:")
            assert model["evidence_class"] == "LOCAL_OLLAMA_MODEL"
            assert model["provider_evidence_class"] == "LOCAL_OLLAMA_MODEL"
            assert model["provider_request_ids_distinct"] is None
            assert model["thinking_level"] is None
        assert model == {
            **model,
            "schema_valid": True,
            "canonical_target_writes": 0,
        }
        assert manifest["workflow"]["experience_governance"]["status"] == (
            "APPROVED_CANARY"
        )
        assert manifest["workflow"]["experience_governance"][
            "evaluation_pass_count"
        ] == 8
    else:
        assert result["experience_verification"] == "LEGACY_NOT_REQUIRED"


def test_public_finalizer_removes_private_runtime_database_bytes(
    tmp_path: Path,
) -> None:
    verifier = _load_verifier()
    finalizer = _load_finalizer()
    root = tmp_path / "pilot"
    shutil.copytree(EVIDENCE, root)
    sentinel = b"PRIVATE_TASK_INTAKE_SENTINEL_079_DO_NOT_PUBLISH"
    with (root / "workspace.sqlite3").open("ab") as stream:
        stream.write(sentinel)
    (root / "workspace.sqlite3-wal").write_bytes(sentinel)
    (root / "workspace.sqlite3-shm").write_bytes(sentinel)

    manifest = finalizer.build_manifest(root)
    _write_json(root / "manifest.json", manifest)

    assert manifest["schema_version"].endswith(".v3")
    assert manifest["privacy"]["runtime_database_included"] is False
    assert not any(
        path.name in finalizer.FORBIDDEN_RUNTIME_DATABASE_NAMES
        for path in root.rglob("*")
    )
    assert all(
        sentinel not in path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    )
    assert verifier.verify(root)["status"] == "PASS"


def test_public_verifier_rejects_resealed_runtime_database(
    tmp_path: Path,
) -> None:
    verifier = _load_verifier()
    root = _copy_and_reseal(tmp_path, verifier)
    (root / "workspace.sqlite3").write_bytes(b"private-runtime-database")
    _reseal_manifest(root, verifier)

    result = verifier.verify(root)

    assert result["status"] == "FAIL"
    assert any(
        item.startswith("RUNTIME_DATABASE_EXPOSED:")
        for item in result["failures"]
    )


def test_public_verifier_rejects_private_work_description_projection(
    tmp_path: Path,
) -> None:
    verifier = _load_verifier()
    root = _copy_and_reseal(tmp_path, verifier)
    sentinel = "PRIVATE_TASK_INTAKE_SENTINEL_079_DO_NOT_PUBLISH"
    _write_json(root / "leaked-private-record.json", {"work_description": sentinel})
    _reseal_manifest(root, verifier)

    result = verifier.verify(root)

    assert result["status"] == "FAIL"
    assert any(
        item.startswith("PRIVATE_TASK_INTAKE_EXPOSED:")
        for item in result["failures"]
    )


def test_public_evidence_tools_reject_credential_shaped_content(
    tmp_path: Path,
) -> None:
    finalizer = _load_finalizer()
    verifier = _load_verifier()
    root = _copy_and_reseal(tmp_path, verifier)
    _write_json(
        root / "leaked-credential.json",
        {"note": "Bearer " + "live" + "-customer-secret"},
    )
    _reseal_manifest(root, verifier)

    with pytest.raises(
        finalizer.GoldenPilotEvidenceError,
        match=r"^PRIVATE_TASK_INTAKE_EXPOSED:leaked-credential\.json$",
    ):
        finalizer.build_manifest(root)
    result = verifier.verify(root)
    assert result["status"] == "FAIL"
    assert result["failures"] == [
        "PRIVATE_TASK_INTAKE_EXPOSED:leaked-credential.json"
    ]


def test_public_evidence_tools_reject_symlinked_external_content(
    tmp_path: Path,
) -> None:
    finalizer = _load_finalizer()
    verifier = _load_verifier()
    root = _copy_and_reseal(tmp_path, verifier)
    outside = tmp_path / "outside-secret.json"
    outside.write_text('{"password":"outside"}', encoding="utf-8")
    (root / "linked-external.json").symlink_to(outside)

    with pytest.raises(
        finalizer.GoldenPilotEvidenceError,
        match=r"^PACK_SYMLINK_FORBIDDEN:linked-external\.json$",
    ):
        finalizer.build_manifest(root)
    result = verifier.verify(root)
    assert result["status"] == "FAIL"
    assert result["failures"] == [
        "PACK_SYMLINK_FORBIDDEN:linked-external.json"
    ]


def test_public_evidence_tools_reject_oversized_sparse_file(
    tmp_path: Path,
) -> None:
    finalizer = _load_finalizer()
    verifier = _load_verifier()
    root = _copy_and_reseal(tmp_path, verifier)
    oversized = root / "oversized.bin"
    with oversized.open("wb") as stream:
        stream.truncate(finalizer.MAX_PUBLIC_EVIDENCE_FILE_BYTES + 1)

    with pytest.raises(
        finalizer.GoldenPilotEvidenceError,
        match=r"^PACK_FILE_TOO_LARGE:oversized\.bin$",
    ):
        finalizer.build_manifest(root)
    result = verifier.verify(root)
    assert result["status"] == "FAIL"
    assert result["failures"] == ["PACK_FILE_TOO_LARGE:oversized.bin"]


def test_finalizer_prefers_reviewer_role_and_attempt_over_actor_name() -> None:
    module = _load_finalizer()
    semantic = [
        {
            "task_id": "task:any-review-stage-one",
            "agent_name": "independent-reviewer",
            "role": "REVIEWER",
            "attempt": 1,
        },
        {
            "task_id": "task:any-review-stage-two",
            "agent_name": "policy-assurance-agent",
            "role": "REVIEWER",
            "attempt": 2,
        },
    ]

    assert module._reviewer_runs(semantic) == semantic
    assert module._reviewer_runs(
        [*semantic, {**semantic[0], "task_id": "task:ambiguous"}]
    ) == []


def test_finalizer_legacy_reviewer_suffix_requires_one_match_per_attempt() -> None:
    module = _load_finalizer()
    legacy = [
        {"task_id": "task:legacy-reviewer-a1", "agent_name": "old-reviewer"},
        {"task_id": "task:legacy-reviewer-a2", "agent_name": "old-reviewer"},
    ]

    assert module._reviewer_runs(legacy) == legacy
    assert module._reviewer_runs(
        [*legacy, {"task_id": "task:other-reviewer-a2"}]
    ) == []


def test_verifier_rejects_ambiguous_orchestration_task_count(tmp_path: Path) -> None:
    module = _load_verifier()
    root = _copy_and_reseal(tmp_path, module)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["workflow"]["task_count"] = 8
    manifest["workflow"].pop("orchestration_node_count")
    manifest["digest"] = module._digest(
        {key: value for key, value in manifest.items() if key != "digest"}
    )
    _write_json(manifest_path, manifest)

    result = module.verify(root)

    assert result["status"] == "FAIL"
    assert "MANIFEST_WORKFLOW_CARDINALITY" in result["failures"]


def test_finalizer_keeps_current_ollama_provider_contract(tmp_path: Path) -> None:
    finalizer = _load_finalizer()
    verifier = _load_verifier()
    root = _copy_and_reseal(
        tmp_path,
        verifier,
        finalize_public=False,
    )
    summary_path = root / "golden-run" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["model_provider"] = "ollama-local"
    attempts = []
    bindings = sorted(
        (item for item in summary["task_bindings"] if item["role"] == "REVIEWER"),
        key=lambda item: item["attempt"],
    )
    for phase, binding in enumerate(bindings, start=1):
        task_id = binding["task_id"]
        input_path = root / "golden-run" / "process-inputs" / f"{task_id}.json"
        output_path = root / "golden-run" / "process-outputs" / f"{task_id}.json"
        reviewer_input = json.loads(input_path.read_text(encoding="utf-8"))
        output = json.loads(output_path.read_text(encoding="utf-8"))
        output["model_provider"] = "ollama-local"
        output["model_authority"] = finalizer.MODEL_AUTHORITY
        receipt = output["model_receipt"]
        receipt.update(
            {
                "evidence_class": "LOCAL_OLLAMA_MODEL",
                "finish_reason": "stop",
                "model_id": "qwen2.5:3b",
                "model_version": finalizer.OLLAMA_EXPECTED_RECEIPT_VERSION,
                "provider": "ollama-local",
                "provider_request_id": None,
                "seed_supported": True,
            }
        )
        receipt["digest"] = finalizer.sha256_digest(
            {key: value for key, value in receipt.items() if key != "digest"}
        )
        runtime = {
            "claim_boundary": "LOCAL_LOOPBACK_INFERENCE_NOT_PRODUCTION_PROVIDER",
            "expected_model_digest": finalizer.OLLAMA_EXPECTED_RECEIPT_VERSION.removeprefix(
                "ollama-manifest:"
            ),
            "model_id": "qwen2.5:3b",
            "observed_model_digest": finalizer.OLLAMA_EXPECTED_RECEIPT_VERSION.removeprefix(
                "ollama-manifest:"
            ),
            "response_observation_digest": output["model_runtime_binding"][
                "response_observation_digest"
            ],
            "status": "BOUND",
        }
        output["model_runtime_binding"] = runtime
        attempt_body = {
            "schema_version": "orgrebase.golden-model-attempt-evidence.v1",
            "run_id": summary["run_id"],
            "task_id": task_id,
            "phase": phase,
            "reviewer_input_digest": finalizer.sha256_digest(reviewer_input),
            "reviewer_prompt_payload_digest": output["prompt_payload_digest"],
            "model_request_digest": receipt["request_digest"],
            "model_response_receipt_digest": receipt["digest"],
            "provider": "ollama-local",
            "provider_request_id": None,
            "requested_model_id": receipt["model_id"],
            "observed_model_version": receipt["model_version"],
            "request_payload_digest": None,
            "response_observation_digest": runtime["response_observation_digest"],
            "output_digest": receipt["output_digest"],
            "input_tokens": receipt["input_tokens"],
            "output_tokens": receipt["output_tokens"],
            "latency_ms": receipt["latency_ms"],
            "finish_reason": receipt["finish_reason"],
            "thinking_level": None,
            "thinking_tokens": 0,
            "total_tokens": 0,
            "max_output_tokens": None,
            "advisory_accepted": output["model_advisory_accepted"],
            "advisory_disposition": output[
                "model_advisory_disposition_reason"
            ],
            "model_authority": finalizer.MODEL_AUTHORITY,
            "candidate_only": True,
            "target_writes": 0,
        }
        attempts.append(
            {
                **attempt_body,
                "digest": finalizer.sha256_digest(attempt_body),
            }
        )
        _write_json(output_path, output)
    summary["reviewer_model_attempts"] = attempts
    _write_json(summary_path, summary)

    facts = finalizer._model_facts(root, summary)

    assert facts["provider"] == "ollama-local"
    assert facts["model_id"] == "qwen2.5:3b"
    assert facts["model_version"].startswith("ollama-manifest:")
    assert facts["provider_request_ids_distinct"] is None
    assert facts["model_authority"] == finalizer.MODEL_AUTHORITY


def test_reviewer_advisory_authority_allows_override_then_acceptance() -> None:
    modules = (_load_finalizer(), _load_verifier())
    first_decision = deepcopy(modules[0].EXPECTED_REVIEWER_DECISIONS[1])
    second_decision = deepcopy(modules[0].EXPECTED_REVIEWER_DECISIONS[2])
    first_advisory = {
        "verdict": "PASS",
        "missing_domains": [],
        "reason_codes": ["RUNTIME_PROJECTION_RESOLVED"],
    }
    second_advisory = deepcopy(first_advisory)
    outputs = (
        {
            "phase": 1,
            "decision": first_decision,
            "model_advisory": first_advisory,
            "model_receipt": {"value": deepcopy(first_advisory)},
            "model_advisory_accepted": False,
            "model_advisory_disposition_reason": (
                "MODEL_ADVISORY_OVERRIDDEN_BY_DETERMINISTIC_VERIFIER"
            ),
        },
        {
            "phase": 2,
            "decision": second_decision,
            "model_advisory": second_advisory,
            "model_receipt": {"value": deepcopy(second_advisory)},
            "model_advisory_accepted": True,
            "model_advisory_disposition_reason": (
                "MODEL_ADVISORY_MATCHED_DETERMINISTIC_VERIFIER"
            ),
        },
    )

    for module in modules:
        assert module._model_advisory_authority_valid(
            outputs[0], expected_decision=first_decision, phase=1
        )
        assert module._model_advisory_authority_valid(
            outputs[1], expected_decision=second_decision, phase=2
        )


@pytest.mark.parametrize(
    "mutation",
    ("missing_override", "missing_advisory", "verdict_mismatch", "reason_mismatch"),
)
def test_reviewer_advisory_authority_rejects_unclosed_override(mutation: str) -> None:
    modules = (_load_finalizer(), _load_verifier())
    expected = deepcopy(modules[0].EXPECTED_REVIEWER_DECISIONS[1])
    advisory = {
        "verdict": "PASS",
        "missing_domains": [],
        "reason_codes": ["RUNTIME_PROJECTION_RESOLVED"],
    }
    output = {
        "phase": 1,
        "decision": deepcopy(expected),
        "model_advisory": deepcopy(advisory),
        "model_receipt": {"value": deepcopy(advisory)},
        "model_advisory_accepted": False,
        "model_advisory_disposition_reason": (
            "MODEL_ADVISORY_OVERRIDDEN_BY_DETERMINISTIC_VERIFIER"
        ),
    }
    if mutation == "missing_override":
        output["model_advisory_disposition_reason"] = (
            "MODEL_ADVISORY_MATCHED_DETERMINISTIC_VERIFIER"
        )
    elif mutation == "missing_advisory":
        output.pop("model_advisory")
    elif mutation == "verdict_mismatch":
        output["decision"]["verdict"] = "PASS"
    else:
        output["decision"]["reason_codes"] = ["UNSEALED_REASON"]

    for module in modules:
        assert not module._model_advisory_authority_valid(
            output, expected_decision=expected, phase=1
        )


def test_verifier_rejects_resealed_post_ack_process_forgery(tmp_path: Path) -> None:
    module = _load_verifier()
    root = _copy_and_reseal(tmp_path, module)
    golden = root / "golden-run"
    summary_path = golden / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    receipts_path = golden / "process-receipts.json"
    receipts = json.loads(receipts_path.read_text(encoding="utf-8"))
    target = receipts[0]
    binding = next(item for item in summary["task_bindings"] if item["task_id"] == target["task_id"])
    target["started_after_ack_action_digest"] = binding["submit_action_digest"]
    target["digest"] = module._digest({key: value for key, value in target.items() if key != "digest"})
    summary["process_receipt_digests"][0] = target["digest"]
    summary["digest"] = module._digest({key: value for key, value in summary.items() if key != "digest"})
    _write_json(receipts_path, receipts)
    _write_json(summary_path, summary)

    _assert_causal_failure(module, root, "PROCESS_ACK_BINDING")


def test_verifier_rejects_agentteams_submit_output_substitution(tmp_path: Path) -> None:
    module = _load_verifier()
    root = _copy_and_reseal(tmp_path, module)
    golden = root / "golden-run"
    summary = json.loads((golden / "summary.json").read_text(encoding="utf-8"))
    binding = summary["task_bindings"][0]
    journal = json.loads((golden / "agentteams" / "action-journal.json").read_text(encoding="utf-8"))
    submit = next(item for item in journal if item["digest"] == binding["submit_action_digest"])
    raw_path = golden / "agentteams" / submit["raw_ref"]
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["request"]["payload"]["summary"] = "{}"
    _write_json(raw_path, raw)

    _assert_causal_failure(module, root, "AGENTTEAMS_RESULT_ROUNDTRIP")


def test_verifier_rejects_finance_attempt_one_answer_leak(tmp_path: Path) -> None:
    module = _load_verifier()
    root = _copy_and_reseal(tmp_path, module)
    golden = root / "golden-run"
    summary = json.loads((golden / "summary.json").read_text(encoding="utf-8"))
    task_id = next(
        item["task_id"]
        for item in summary["task_bindings"]
        if item.get("domain") == "finance" and item.get("attempt") == 1
    )
    input_path = golden / "process-inputs" / f"{task_id}.json"
    worker_input = json.loads(input_path.read_text(encoding="utf-8"))
    tool = json.loads((golden / "tool" / "invocation.json").read_text(encoding="utf-8"))
    worker_input["source_values"]["price_band"] = tool["result"]["source_values"]["price_band"]
    _write_json(input_path, worker_input)

    _assert_causal_failure(module, root, "FINANCE_A1_SOURCE_SCOPE")


def test_verifier_rejects_tool_to_finance_a2_byte_substitution(tmp_path: Path) -> None:
    module = _load_verifier()
    root = _copy_and_reseal(tmp_path, module)
    golden = root / "golden-run"
    summary = json.loads((golden / "summary.json").read_text(encoding="utf-8"))
    task_id = next(
        item["task_id"]
        for item in summary["task_bindings"]
        if item.get("domain") == "finance" and item.get("attempt") == 2
    )
    input_path = golden / "process-inputs" / f"{task_id}.json"
    worker_input = json.loads(input_path.read_text(encoding="utf-8"))
    worker_input["source_values"]["price_band"]["value"] = "forged-enterprise"
    _write_json(input_path, worker_input)

    _assert_causal_failure(module, root, "TOOL_FINANCE_A2_BYTES")


def test_verifier_rejects_worker_owned_reviewer_expectation(tmp_path: Path) -> None:
    module = _load_verifier()
    root = _copy_and_reseal(tmp_path, module)
    golden = root / "golden-run"
    summary = json.loads((golden / "summary.json").read_text(encoding="utf-8"))
    task_id = next(
        item["task_id"]
        for item in summary["task_bindings"]
        if item.get("role") == "REVIEWER" and item.get("attempt") == 2
    )
    input_path = golden / "process-inputs" / f"{task_id}.json"
    reviewer_input = json.loads(input_path.read_text(encoding="utf-8"))
    reviewer_input["expected_bindings"]["product"]["source_bindings"]["launch_date"]["value_digest"] = (
        "sha256:" + "0" * 64
    )
    _write_json(input_path, reviewer_input)

    _assert_causal_failure(module, root, "REVIEWER_MANAGER_BINDING")


def test_reviewer_manager_binding_includes_oac_lineage_when_declared() -> None:
    module = _load_verifier()
    worker_input = {
        "projection": {"digest": "sha256:" + "1" * 64},
        "source_values": {
            "launch_date": {
                "object_ref": "claim:product.launch_date@v1",
                "value": "2026-11-15",
                "semantic_kind": "CLAIM",
                "authority_ref": "authority:product@v1",
                "source_id": "source:release-plan",
                "source_version": "v1",
                "source_digest": "sha256:" + "2" * 64,
                "sensitivity": "INTERNAL",
            }
        },
    }
    worker_binding = {
        "assignee": "@product-steward:controlled.local",
        "attempt": 1,
        "task_purpose": "enterprise_quote",
        "agentteams_execution_plan_digest": "sha256:" + "3" * 64,
        "formation_receipt_digest": "sha256:" + "4" * 64,
        "formation_receipt_id": "task-formation-decision:quote",
        "logical_plan_task_digest": "sha256:" + "5" * 64,
        "logical_plan_task_id": "at-domain-product",
    }

    expected = module._manager_expected_binding(
        worker_task_id="worker-product-a1",
        worker_binding=worker_binding,
        worker_input=worker_input,
        run_id="run:golden-competition:test",
        correlation_id="run:enterprise-pilot:test",
        oac_bound=True,
    )

    assert expected["worker_id"] == "product-steward"
    assert expected["agentteams_execution_plan_digest"] == (
        worker_binding["agentteams_execution_plan_digest"]
    )
    assert expected["formation_receipt_id"] == (
        worker_binding["formation_receipt_id"]
    )
    assert expected["logical_plan_task_id"] == (
        worker_binding["logical_plan_task_id"]
    )


def test_verifier_rejects_resealed_formation_quote_substitution(tmp_path: Path) -> None:
    module = _load_verifier()
    root = _copy_and_reseal(tmp_path, module)
    golden = root / "golden-run"
    formation_path = golden / "prepared-formation-bundle.json"
    formation = json.loads(formation_path.read_text(encoding="utf-8"))
    formation["deliverable"]["payload"]["price_band"] = "forged-enterprise"
    formation["digest"] = module._digest({key: value for key, value in formation.items() if key != "digest"})
    _write_json(formation_path, formation)

    summary_path = golden / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["prepared_quote_payload"] = formation["deliverable"]["payload"]
    summary["prepared_formation_digest"] = formation["digest"]
    summary["digest"] = module._digest({key: value for key, value in summary.items() if key != "digest"})
    _write_json(summary_path, summary)

    _assert_causal_failure(module, root, "FORMATION_QUOTE_CAUSALITY")


def test_verifier_rejects_evaluation_mode_as_released_skill(tmp_path: Path) -> None:
    module = _load_verifier()
    root = _copy_and_reseal(tmp_path, module)
    receipt_path = root / "golden-run" / "skill" / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["authorization_mode"] = "EVALUATION"
    receipt["release_receipt_digest"] = None
    receipt["digest"] = module._digest(
        {key: value for key, value in receipt.items() if key != "digest"}
    )
    _write_json(receipt_path, receipt)

    _assert_causal_failure(module, root, "SKILL_RELEASE_AUTHORIZATION")


@pytest.mark.parametrize("field", ["skill_evaluation_partition_count", "skill_evaluation_case_count"])
def test_evidence_tools_reject_counts_that_disagree_with_retained_suite(tmp_path, field):
    verifier, finalizer = _load_verifier(), _load_finalizer()
    root = _copy_and_reseal(tmp_path, verifier)
    summary_path = root / "golden-run/summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary[field] = 9
    summary["digest"] = verifier._digest({key: value for key, value in summary.items() if key != "digest"})
    _write_json(summary_path, summary)
    with pytest.raises(finalizer.GoldenPilotEvidenceError, match="SKILL_EVALUATION_INVALID"):
        finalizer.build_manifest(root)
    _assert_causal_failure(verifier, root, "SKILL_EVALUATION_AUTHORITY")


def test_verifier_rejects_foreign_resealed_release_head(tmp_path: Path) -> None:
    module = _load_verifier()
    root = _copy_and_reseal(tmp_path, module)
    ledger_path = root / "golden-run" / "skill" / "release-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger[-1]["reason_codes"] = ["FOREIGN_RESEALED_HEAD"]
    ledger[-1]["digest"] = module._digest(
        {key: value for key, value in ledger[-1].items() if key != "digest"}
    )
    _write_json(ledger_path, ledger)

    _assert_causal_failure(module, root, "SKILL_RELEASE_LEDGER")


def test_verifier_rejects_broken_release_previous_chain(tmp_path: Path) -> None:
    module = _load_verifier()
    root = _copy_and_reseal(tmp_path, module)
    ledger_path = root / "golden-run" / "skill" / "release-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger[1]["previous_receipt_digest"] = None
    ledger[1]["digest"] = module._digest(
        {key: value for key, value in ledger[1].items() if key != "digest"}
    )
    _write_json(ledger_path, ledger)

    _assert_causal_failure(module, root, "SKILL_RELEASE_LEDGER")


def test_verifier_rejects_skill_dependency_drift(tmp_path: Path) -> None:
    module = _load_verifier()
    root = _copy_and_reseal(tmp_path, module)
    package_path = root / "golden-run" / "skill" / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    dependency_ref = next(iter(package["dependencies"]))
    package["dependencies"][dependency_ref] = "sha256:" + "0" * 64
    package["manifest_digest"] = module._digest(
        {key: value for key, value in package.items() if key != "manifest_digest"}
    )
    _write_json(package_path, package)

    _assert_causal_failure(module, root, "SKILL_EVALUATION_AUTHORITY")


def test_v2_verifier_rejects_reused_vertex_response_id(tmp_path: Path) -> None:
    module = _load_verifier()
    manifest = json.loads((EVIDENCE / "manifest.json").read_text(encoding="utf-8"))
    if manifest["workflow"]["model"]["provider"] != "vertex-ai":
        pytest.skip("current Golden does not expose the Vertex response-id contract")
    root = _copy_and_reseal(tmp_path, module)
    summary_path = root / "golden-run" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    first_id = summary["reviewer_model_attempts"][0]["provider_request_id"]
    second = summary["reviewer_model_attempts"][1]
    second["provider_request_id"] = first_id
    second["digest"] = module._digest(
        {key: value for key, value in second.items() if key != "digest"}
    )
    task_id = second["task_id"]
    output_path = root / "golden-run" / "process-outputs" / f"{task_id}.json"
    output = json.loads(output_path.read_text(encoding="utf-8"))
    output["model_receipt"]["provider_request_id"] = first_id
    output["model_receipt"]["digest"] = module._digest(
        {
            key: value
            for key, value in output["model_receipt"].items()
            if key != "digest"
        }
    )
    second["model_response_receipt_digest"] = output["model_receipt"]["digest"]
    second["digest"] = module._digest(
        {key: value for key, value in second.items() if key != "digest"}
    )
    summary["digest"] = module._digest(
        {key: value for key, value in summary.items() if key != "digest"}
    )
    _write_json(output_path, output)
    _write_json(summary_path, summary)

    _assert_causal_failure(module, root, "REVIEWER_VERTEX_RESPONSE_ID_OR_SCHEMA")


def test_verifier_rejects_cross_run_experience_projection(tmp_path: Path) -> None:
    module = _load_verifier()
    root = _copy_and_reseal(tmp_path, module)
    state_path = root / "state.json"
    evidence_path = root / "evidence-export.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    exported = json.loads(evidence_path.read_text(encoding="utf-8"))
    foreign_run = "run:golden-competition:foreign"
    for value in (state, exported):
        experience = value["experience_governance"]
        experience["run_id"] = foreign_run
        experience["digest"] = module._digest(
            {key: item for key, item in experience.items() if key != "digest"}
        )
    exported["digest"] = module._digest(
        {key: value for key, value in exported.items() if key != "digest"}
    )
    _write_json(state_path, state)
    _write_json(evidence_path, exported)
    _reseal_manifest(root, module)

    result = module.verify(root)

    assert result["status"] == "FAIL"
    assert result["experience_verification"] == "FAIL"
    assert "EXPERIENCE_APPROVED_VIEW" in result["failures"]
