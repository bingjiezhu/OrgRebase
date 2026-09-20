from __future__ import annotations

import ast
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import rfc8785

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError, ObjectState
from orgrebase.store import StateStore
from orgrebase.workspace.models import (
    OACRuntimeApproval,
    OACRuntimeCapsule,
    RuntimeAdmissionReceipt,
)
from orgrebase.workspace.oac_bridge import (
    DEFAULT_POLICY_PATH,
    RECEIPT_MEDIA_TYPE,
    OACBlackBoxCLI,
    OACRuntimeAdmissionBridge,
    OACRuntimeAdmissionVerifier,
    _jcs_digest,
    _oac_projection,
    _resource_ref,
    load_runtime_policy,
    locate_oac_root,
    run_oac_admission_demo,
    verify_oac_bridge_evidence,
)


@pytest.fixture(scope="module")
def capsules() -> dict[str, OACRuntimeCapsule]:
    cli = OACBlackBoxCLI()
    return {case: cli.build_capsule(case) for case in ("BASE", "SPLIT")}


def _reseal(resource: dict[str, Any]) -> dict[str, Any]:
    resource["digest"] = _jcs_digest(_oac_projection(resource))
    return resource


def _capsule_payload(capsule: OACRuntimeCapsule) -> dict[str, Any]:
    payload = capsule.model_dump(mode="json")
    payload.pop("digest")
    return payload


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _reseal_evidence_index(root: Path) -> None:
    index_path = root / "evidence-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for entry in index["entries"]:
        payload = (root / entry["artifact_ref"]).read_bytes()
        entry["sha256"] = "sha256:" + hashlib.sha256(payload).hexdigest()
    index["pack_digest"] = sha256_digest(index["entries"])
    _write_json(index_path, index)


def _mutate_resource(
    capsule: OACRuntimeCapsule,
    field: str,
    mutator: Any,
    *,
    cascade: bool = False,
) -> OACRuntimeCapsule:
    payload = _capsule_payload(capsule)
    resource = deepcopy(payload[field])
    mutator(resource)
    payload[field] = _reseal(resource)
    if cascade and field == "runtime_binding":
        bundle = deepcopy(payload["runtime_bundle"])
        bundle["spec"]["runtimeBindingRef"] = _resource_ref(resource)
        payload["runtime_bundle"] = _reseal(bundle)
        receipt = deepcopy(payload["runtime_lowering_receipt"])
        receipt["spec"]["runtimeBindingRef"] = _resource_ref(resource)
        receipt["spec"]["bundleRef"] = _resource_ref(bundle)
        payload["runtime_lowering_receipt"] = _reseal(receipt)
    elif cascade and field == "runtime_bundle":
        receipt = deepcopy(payload["runtime_lowering_receipt"])
        receipt["spec"]["bundleRef"] = _resource_ref(resource)
        payload["runtime_lowering_receipt"] = _reseal(receipt)
    return OACRuntimeCapsule.model_validate(payload)


def _bridge(path: Path) -> tuple[StateStore, OACRuntimeAdmissionBridge]:
    store = StateStore(path)
    return store, OACRuntimeAdmissionBridge(store)


def _approve(
    bridge: OACRuntimeAdmissionBridge,
    capsule: OACRuntimeCapsule,
    *,
    command_id: str = "test-command",
):
    preview = bridge.prepare(capsule)
    approval = bridge.approve(
        preview,
        actor_id=preview.runtime_owner_id,
        preview_digest=preview.digest,
        command_id=command_id,
        approved_at="2026-08-25T00:00:00Z",
    )
    return preview, approval


def test_black_box_base_and_split_prove_distinct_deterministic_topologies(
    capsules: dict[str, OACRuntimeCapsule],
) -> None:
    policy, digest = load_runtime_policy()
    verifier = OACRuntimeAdmissionVerifier(policy, digest)
    base = verifier.verify(capsules["BASE"])
    split = verifier.verify(capsules["SPLIT"])
    repeated = OACBlackBoxCLI().build_capsule("BASE")

    assert capsules["BASE"].plan_source == "OAC_REFERENCE_COMPILER"
    assert capsules["SPLIT"].plan_source == "OAC_PUBLIC_VERIFIER_ACCEPTED_FIXTURE"
    assert base["step_count"] == 3
    assert split["step_count"] == 4
    assert base["topology_digest"] != split["topology_digest"]
    assert capsules["BASE"].digest == repeated.digest
    assert capsules["BASE"].runtime_bundle["digest"] == repeated.runtime_bundle["digest"]
    assert capsules["BASE"].runtime_lowering_receipt["spec"] == {
        **capsules["BASE"].runtime_lowering_receipt["spec"],
        "runtimeInvoked": False,
        "runtimeAdmissionPerformed": False,
        "targetWrites": 0,
    }


@pytest.mark.parametrize("case_id", ("BASE", "SPLIT"))
def test_current_capsule_uses_exact_raw_artifacts_without_mutating_sources(case_id: str) -> None:
    cli = OACBlackBoxCLI()
    legacy_root = cli.root / "profiles" / "supplier-change" / "inputs"
    artifact_root = cli.root / "experiments" / "plan-verification-portability" / "v0.1-seed-1" / "artifacts"
    names = ("SC-008.change.json", "veracier-proc01-contextual.snapshot.json")
    paths = (
        tuple(legacy_root / name for name in names)
        + tuple(artifact_root / "SC-008" / name for name in names)
        + (artifact_root / "plans" / "PV-POS-SC008-SPLIT.plan.json",)
    )
    original_bytes = {path: path.read_bytes() for path in paths}

    capsule = cli.build_capsule(case_id)

    assert {path: path.read_bytes() for path in paths} == original_bytes
    legacy_change = json.loads(original_bytes[paths[0]])
    selected_change = json.loads(original_bytes[paths[2]])
    # These are the three declared optional members materialized by the original builder.
    legacy_change["metadata"]["effectiveTo"] = None
    legacy_change["spec"]["deltas"][0]["beforeVersion"] = None
    legacy_change["spec"]["deltas"][0]["afterVersion"] = None
    assert legacy_change == selected_change == capsule.change
    assert json.loads(original_bytes[paths[1]]) == capsule.snapshot
    assert json.loads(original_bytes[paths[3]]) == capsule.snapshot
    entries = {entry["path"]: entry for entry in capsule.oac_source_manifest["files"]}
    for path, resource in ((paths[2], capsule.change), (paths[3], capsule.snapshot)):
        projection = {key: value for key, value in resource.items() if key != "digest"}
        assert resource["digest"] == "sha256:" + hashlib.sha256(rfc8785.dumps(projection)).hexdigest()
        assert entries[str(path.relative_to(cli.root))] == {
            "path": str(path.relative_to(cli.root)),
            "sha256": "sha256:" + hashlib.sha256(original_bytes[path]).hexdigest(),
            "size": len(original_bytes[path]),
        }
    policy, policy_digest = load_runtime_policy()
    assert capsule.plan["digest"] == policy["plan_commitments_by_case"][case_id]["plan_digest"]
    assert OACRuntimeAdmissionVerifier(policy, policy_digest).verify(capsule)["step_count"] == (
        3 if case_id == "BASE" else 4
    )


def test_current_cli_rejects_original_incomplete_raw_change() -> None:
    cli = OACBlackBoxCLI()
    path = cli.root / "profiles" / "supplier-change" / "inputs" / "SC-008.change.json"
    original = path.read_bytes()

    with pytest.raises(RuntimeError, match="OAC_CLI_FAILED:validate"):
        cli.run("validate", str(path), "--verify-digest")

    assert path.read_bytes() == original


def test_public_source_manifest_covers_actual_cli_local_import_closure() -> None:
    cli = OACBlackBoxCLI()
    pending = ["cli", "__init__"]
    visited: set[str] = set()
    while pending:
        module = pending.pop()
        if module in visited:
            continue
        visited.add(module)
        path = cli.root / "src" / "oac" / f"{module}.py"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                dependency = node.module.split(".")[0]
                if (path.parent / f"{dependency}.py").is_file():
                    pending.append(dependency)
    entries = {entry["path"]: entry for entry in cli.source_manifest["files"]}
    assert {f"src/oac/{module}.py" for module in visited} <= entries.keys()
    for module in visited:
        relative = f"src/oac/{module}.py"
        assert (
            entries[relative]["sha256"]
            == "sha256:" + hashlib.sha256((cli.root / relative).read_bytes()).hexdigest()
        )


def test_distribution_python_preserves_virtual_environment_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interpreter = tmp_path / "venv-python"
    interpreter.symlink_to(Path(sys.executable))
    monkeypatch.setenv("ORGREBASE_OAC_PYTHON", str(interpreter))
    monkeypatch.setattr(
        OACBlackBoxCLI,
        "_execution_runtime",
        lambda self: {"mode": "INSTALLED_WHEEL_CLI", "version": self.version},
    )

    cli = OACBlackBoxCLI()

    assert cli.distribution_python == interpreter.absolute()
    assert cli.distribution_python != interpreter.resolve()


def test_lowering_blocked_or_receipt_only_cannot_form_an_admitted_capsule(
    capsules: dict[str, OACRuntimeCapsule],
) -> None:
    payload = _capsule_payload(capsules["BASE"])
    payload["runtime_bundle"] = {}
    with pytest.raises(ValueError, match="OAC_RUNTIME_CAPSULE_KIND_SET_INVALID"):
        OACRuntimeCapsule.model_validate(payload)

    blocked = _mutate_resource(
        capsules["BASE"],
        "runtime_lowering_receipt",
        lambda value: value["spec"].update(
            {"status": "BLOCKED", "reasonCodes": ["LOWERING_VERDICT_NOT_ACCEPT"]}
        ),
    )
    policy, digest = load_runtime_policy()
    with pytest.raises(IntegrityError, match="OAC_LOWERING_STATUS_INVALID"):
        OACRuntimeAdmissionVerifier(policy, digest).verify(blocked)


@pytest.mark.parametrize("target", ("plan", "plan_certificate"))
def test_mismatched_plan_or_certificate_refs_fail_closed(
    capsules: dict[str, OACRuntimeCapsule], target: str
) -> None:
    if target == "plan":
        mutated = _mutate_resource(
            capsules["BASE"],
            target,
            lambda value: value["spec"]["snapshotRef"].update({"digest": "sha256:" + "1" * 64}),
        )
        match = "OAC_REFERENCE_MISMATCH:plan.snapshot"
    else:
        mutated = _mutate_resource(
            capsules["BASE"],
            target,
            lambda value: value["spec"]["subjectPlanRef"].update({"digest": "sha256:" + "2" * 64}),
        )
        match = "OAC_REFERENCE_MISMATCH:certificate.plan"
    policy, digest = load_runtime_policy()
    with pytest.raises(IntegrityError, match=match):
        OACRuntimeAdmissionVerifier(policy, digest).verify(mutated)


def test_policy_namespace_not_admitted(capsules: dict[str, OACRuntimeCapsule]) -> None:
    policy, _ = load_runtime_policy()
    policy["allowed_namespaces"] = []
    with pytest.raises(IntegrityError, match="OAC_RUNTIME_POLICY_NAMESPACE_DENIED"):
        OACRuntimeAdmissionVerifier(policy, "sha256:" + "3" * 64).verify(capsules["BASE"])


def test_plan_source_and_public_source_snapshot_cannot_be_caller_spoofed(
    capsules: dict[str, OACRuntimeCapsule],
) -> None:
    policy, digest = load_runtime_policy()
    verifier = OACRuntimeAdmissionVerifier(policy, digest)

    source_spoof = _capsule_payload(capsules["BASE"])
    source_spoof["plan_source"] = "OAC_PUBLIC_VERIFIER_ACCEPTED_FIXTURE"
    with pytest.raises(IntegrityError, match="OAC_PLAN_SOURCE_COMMITMENT_MISMATCH"):
        verifier.verify(OACRuntimeCapsule.model_validate(source_spoof))

    snapshot_spoof = _capsule_payload(capsules["BASE"])
    manifest = deepcopy(snapshot_spoof["oac_source_manifest"])
    manifest["files"][0]["sha256"] = "sha256:" + "9" * 64
    snapshot_spoof["oac_source_manifest"] = manifest
    snapshot_spoof["oac_source_fingerprint"] = sha256_digest(manifest)
    with pytest.raises(IntegrityError, match="OAC_PUBLIC_SOURCE_FINGERPRINT_DENIED"):
        verifier.verify(OACRuntimeCapsule.model_validate(snapshot_spoof))


@pytest.mark.parametrize("mutation", ("principal", "subject", "capability", "evidence"))
def test_role_capability_and_handler_expansion_are_rejected(
    capsules: dict[str, OACRuntimeCapsule], mutation: str
) -> None:
    def mutate(binding: dict[str, Any]) -> None:
        if mutation == "principal":
            binding["spec"]["roleBindings"][0]["principalRef"] = "principal:attacker"
        elif mutation == "subject":
            roles = binding["spec"]["roleBindings"]
            first = roles[0]
            target = next(item for item in roles[1:] if item["principalRef"] != first["principalRef"])
            target["runtimeSubject"] = first["runtimeSubject"]
        elif mutation == "capability":
            binding["spec"]["roleBindings"][0]["capabilityRefs"].append("capability:admin")
        else:
            binding["spec"]["handlerBindings"][0]["evidenceOutputRefs"].append("evidence:extra")

    capsule = _mutate_resource(capsules["BASE"], "runtime_binding", mutate, cascade=True)
    policy, digest = load_runtime_policy()
    expected = {
        "principal": "OAC_ROLE_PRINCIPAL_SUBJECT_MISMATCH",
        "subject": "OAC_RUNTIME_SUBJECT_COLLISION",
        "capability": "OAC_RUNTIME_CAPABILITY_SET_MISMATCH",
        "evidence": "OAC_RUNTIME_HANDLER_CONTRACT_MISMATCH",
    }[mutation]
    with pytest.raises(IntegrityError, match=expected):
        OACRuntimeAdmissionVerifier(policy, digest).verify(capsule)


@pytest.mark.parametrize("mutation", ("reorder", "cycle"))
def test_bundle_topology_reorder_or_cycle_is_rejected(
    capsules: dict[str, OACRuntimeCapsule], mutation: str
) -> None:
    def mutate(bundle: dict[str, Any]) -> None:
        steps = bundle["spec"]["steps"]
        if mutation == "reorder":
            steps[0], steps[1] = steps[1], steps[0]
        else:
            steps[0]["predecessorRefs"] = [steps[-1]["workUnitRef"]]

    capsule = _mutate_resource(capsules["BASE"], "runtime_bundle", mutate, cascade=True)
    policy, digest = load_runtime_policy()
    with pytest.raises(IntegrityError, match="OAC_RUNTIME_STEP_PROJECTION_MISMATCH"):
        OACRuntimeAdmissionVerifier(policy, digest).verify(capsule)


def test_wrong_owner_wrong_preview_and_unapproved_apply_fail_closed(
    tmp_path: Path, capsules: dict[str, OACRuntimeCapsule]
) -> None:
    store, bridge = _bridge(tmp_path / "approval.sqlite")
    try:
        preview = bridge.prepare(capsules["BASE"])
        with pytest.raises(PermissionError, match="OAC_RUNTIME_APPROVER_MISMATCH"):
            bridge.approve(
                preview,
                actor_id="human:not-runtime-owner",
                preview_digest=preview.digest,
                command_id="wrong-owner",
                approved_at="2026-08-25T00:00:00Z",
            )
        with pytest.raises(IntegrityError, match="OAC_RUNTIME_APPROVAL_PREVIEW_DIGEST_MISMATCH"):
            bridge.approve(
                preview,
                actor_id=preview.runtime_owner_id,
                preview_digest="sha256:" + "4" * 64,
                command_id="wrong-preview",
                approved_at="2026-08-25T00:00:00Z",
            )
        with pytest.raises(PermissionError, match="OAC_RUNTIME_APPROVAL_REQUIRED"):
            bridge.apply(capsules["BASE"], preview, None)
        assert store.verify_event_chain()["events"] == 0
    finally:
        store.close()


def test_cached_preview_digest_cannot_reassign_runtime_owner(
    tmp_path: Path,
    capsules: dict[str, OACRuntimeCapsule],
) -> None:
    store, bridge = _bridge(tmp_path / "cached-preview-owner.sqlite")
    try:
        preview = bridge.prepare(capsules["BASE"])
        forged = preview.model_copy(update={"runtime_owner_id": "human:attacker"})
        assert forged.digest == preview.digest

        with pytest.raises(IntegrityError, match="OAC_RUNTIME_PREVIEW_MODEL_INVALID"):
            bridge.approve(
                forged,
                actor_id="human:attacker",
                preview_digest=forged.digest,
                command_id="forged-owner",
                approved_at="2026-08-25T00:00:00Z",
            )
        assert store.verify_event_chain()["events"] == 0
    finally:
        store.close()


def test_cached_capsule_digest_cannot_hide_nested_runtime_mutation(
    tmp_path: Path,
    capsules: dict[str, OACRuntimeCapsule],
) -> None:
    store, bridge = _bridge(tmp_path / "cached-capsule.sqlite")
    try:
        capsule = capsules["BASE"].model_copy(deep=True)
        capsule.runtime_bundle["spec"]["targetWrites"] = 1
        assert capsule.digest == capsules["BASE"].digest

        with pytest.raises(IntegrityError, match="OAC_RUNTIME_CAPSULE_MODEL_INVALID"):
            bridge.prepare(capsule)
        assert store.verify_event_chain()["events"] == 0
    finally:
        store.close()


def test_approval_is_deterministic_and_apply_revalidates_untrusted_model_bytes(
    tmp_path: Path, capsules: dict[str, OACRuntimeCapsule]
) -> None:
    store, bridge = _bridge(tmp_path / "approval-revalidation.sqlite")
    try:
        preview, approval = _approve(bridge, capsules["BASE"], command_id="deterministic-001")
        repeated = bridge.approve(
            preview,
            actor_id=preview.runtime_owner_id,
            preview_digest=preview.digest,
            command_id="deterministic-001",
            approved_at="2026-08-25T00:00:00Z",
        )
        assert repeated.id == "oac-runtime-approval:deterministic-001"
        assert repeated.digest == approval.digest

        malformed_payloads = []
        for changes in (
            {"command_id": "", "id": "oac-runtime-approval:"},
            {"id": "oac-runtime-approval:not-the-command"},
            {"approved_at": "2026-08-25T00:00:00+00:00"},
        ):
            payload = approval.model_dump(mode="json")
            payload.pop("digest")
            payload.update(changes)
            malformed_payloads.append(payload)
        for payload in malformed_payloads:
            malformed = OACRuntimeApproval.model_construct(**payload)
            with pytest.raises(IntegrityError, match="OAC_RUNTIME_APPROVAL_MODEL_INVALID"):
                bridge.apply(capsules["BASE"], preview, malformed)

        wrong_owner_payload = approval.model_dump(mode="json")
        wrong_owner_payload.pop("digest")
        wrong_owner_payload["runtime_owner_id"] = "human:attacker"
        wrong_owner = OACRuntimeApproval.model_construct(**wrong_owner_payload)
        with pytest.raises(IntegrityError, match="OAC_RUNTIME_APPROVAL_BINDING_INVALID"):
            bridge.apply(capsules["BASE"], preview, wrong_owner)
        assert store.verify_event_chain()["events"] == 0
    finally:
        store.close()


def test_apply_is_idempotent_and_conflicting_bytes_fail_closed(
    tmp_path: Path, capsules: dict[str, OACRuntimeCapsule]
) -> None:
    store, bridge = _bridge(tmp_path / "idempotency.sqlite")
    try:
        preview, approval = _approve(bridge, capsules["BASE"], command_id="same-key")
        first = bridge.apply(capsules["BASE"], preview, approval)
        repeated = bridge.apply(capsules["BASE"], preview, approval)
        assert repeated.digest == first.digest
        assert store.verify_event_chain()["events"] == 1

        changed = approval.model_dump(mode="json")
        changed.pop("digest")
        changed["approved_at"] = "2026-08-25T00:00:01Z"
        conflicting = OACRuntimeApproval.model_validate(changed)
        with pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"):
            bridge.apply(capsules["BASE"], preview, conflicting)
        assert store.verify_event_chain()["events"] == 1
    finally:
        store.close()


def test_sqlite_reopen_recovers_exact_active_formation_and_receipt(
    tmp_path: Path, capsules: dict[str, OACRuntimeCapsule]
) -> None:
    database = tmp_path / "reopen.sqlite"
    store, bridge = _bridge(database)
    preview, approval = _approve(bridge, capsules["BASE"], command_id="reopen")
    receipt = bridge.apply(capsules["BASE"], preview, approval)
    before = store.get_object(receipt.formation_ref.rsplit("@", 1)[0])
    store.close()

    reopened = StateStore(database)
    try:
        after = reopened.get_object(receipt.formation_ref.rsplit("@", 1)[0])
        stored_receipt = reopened.load_artifact(receipt.id, RECEIPT_MEDIA_TYPE)
        assert after.digest == before.digest
        assert after.state is ObjectState.ACTIVE
        assert RuntimeAdmissionReceipt.model_validate(stored_receipt.payload).digest == receipt.digest
        assert reopened.verify_event_chain()["status"] == "PASS"
    finally:
        reopened.close()


def test_evidence_pack_is_independent_and_tamper_evident(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    summary = run_oac_admission_demo(evidence)
    verified = verify_oac_bridge_evidence(evidence)
    assert summary["status"] == verified["maturity"] == "LOCAL_RUNTIME_ADMISSION_PASS"
    assert verified["cases"] == 2
    assert verified["target_writes"] == 0
    assert verified["handler_execution"] == "NOT_RUN"

    receipt = evidence / "cases" / "BASE" / "receipt.json"
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["target_writes"] = 1
    receipt.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(IntegrityError, match="OAC_BRIDGE_EVIDENCE_FILE_INVALID"):
        verify_oac_bridge_evidence(evidence)


@pytest.mark.parametrize(
    ("target", "expected"),
    (
        ("summary", "OAC_BRIDGE_CASE_BINDING_INVALID:BASE"),
        ("event", "OAC_BRIDGE_EVENT_SEMANTICS_INVALID:BASE"),
        ("restart", "OAC_BRIDGE_RESTART_INVALID"),
        ("formation", "OAC_BRIDGE_CASE_BINDING_INVALID:BASE"),
    ),
)
def test_resealed_semantic_evidence_tampering_still_fails_closed(
    tmp_path: Path, target: str, expected: str
) -> None:
    evidence = tmp_path / target
    run_oac_admission_demo(evidence)
    if target == "summary":
        path = evidence / "bridge-demo.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["cases"][0]["plan_digest"] = "sha256:" + "1" * 64
        _write_json(path, value)
    elif target == "event":
        path = evidence / "event-chain.json"
        events = json.loads(path.read_text(encoding="utf-8"))
        events[0]["payload"]["formation_digest"] = "sha256:" + "2" * 64
        previous = "sha256:" + "0" * 64
        for sequence, event in enumerate(events, start=1):
            event["sequence_no"] = sequence
            event["previous_digest"] = previous
            previous = sha256_digest(
                {
                    "sequence_no": sequence,
                    "event_type": event["event_type"],
                    "payload": event["payload"],
                    "previous_digest": event["previous_digest"],
                }
            )
            event["event_digest"] = previous
        _write_json(path, events)
        summary_path = evidence / "bridge-demo.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["event_chain"]["head_digest"] = previous
        _write_json(summary_path, summary)
    elif target == "restart":
        path = evidence / "bridge-demo.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["restart"]["formation_count"] = 3
        _write_json(path, value)
    else:
        path = evidence / "cases" / "BASE" / "formation.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value.pop("digest")
        value["plan_digest"] = "sha256:" + "3" * 64
        value["digest"] = sha256_digest(value)
        _write_json(path, value)
    _reseal_evidence_index(evidence)
    with pytest.raises(IntegrityError, match=expected):
        verify_oac_bridge_evidence(evidence)


def test_evidence_export_cleans_only_its_fixed_managed_json_paths(tmp_path: Path) -> None:
    evidence = tmp_path / "managed"
    stale = evidence / "cases" / "STALE"
    stale.mkdir(parents=True)
    (stale / "receipt.json").write_text("{}", encoding="utf-8")
    (stale / "notes.txt").write_text("keep", encoding="utf-8")
    run_oac_admission_demo(evidence)
    assert not (stale / "receipt.json").exists()
    assert (stale / "notes.txt").read_text(encoding="utf-8") == "keep"
    assert verify_oac_bridge_evidence(evidence)["entries"] == 13

    (stale / "rogue.json").write_text("{}", encoding="utf-8")
    with pytest.raises(IntegrityError, match="OAC_BRIDGE_EVIDENCE_PATH_SET_INVALID"):
        verify_oac_bridge_evidence(evidence)


def test_oac_root_precedence_is_explicit_then_environment_then_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit"
    environment = tmp_path / "environment"
    for root in (explicit, environment):
        (root / "src" / "oac").mkdir(parents=True)
        (root / "pyproject.toml").write_text("[project]\nname='oac'\n", encoding="utf-8")
    monkeypatch.setenv("ORGREBASE_OAC_ROOT", str(environment))
    assert locate_oac_root(explicit) == explicit.resolve()
    assert locate_oac_root() == environment.resolve()

    missing = tmp_path / "missing"
    monkeypatch.setenv("ORGREBASE_OAC_ROOT", str(missing))
    with pytest.raises(FileNotFoundError, match="selected_by=ORGREBASE_OAC_ROOT"):
        locate_oac_root()


def test_checked_in_policy_path_is_repository_local() -> None:
    assert DEFAULT_POLICY_PATH.is_file()
    assert DEFAULT_POLICY_PATH.parts[-3:] == (
        "configs",
        "oac",
        "runtime-admission-policy.json",
    )
