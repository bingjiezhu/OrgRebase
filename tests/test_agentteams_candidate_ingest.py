from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from orgrebase.agentteams_ingest import (
    AgentTeamsCandidateIngestor,
    assert_zero_write_ingestion,
    ingest_local_proposals,
)
from orgrebase.collaboration import CollaborationAdapter, OrchestrationCompiler
from orgrebase.digest import sha256_digest
from orgrebase.domain import EvidenceClass, IntegrityError, RunEnvelope
from orgrebase.fixture import EnterpriseFixture
from orgrebase.impact import ImpactEngine, build_change_set


def _bundle(
    root: Path,
    fixture: EnterpriseFixture,
    *,
    bound: bool,
) -> tuple[dict[str, object], dict[str, object], Path]:
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)
    plan = OrchestrationCompiler(fixture).compile(change_set, preview)
    task = next(item for item in plan.tasks if item.agent_name == "gtm-steward")
    run_id = "run:orgrebase:live:test:abc123"
    nonce = "a" * 64
    candidate = {
        "schema_version": "orgrebase.candidate-result.v1",
        "run_id": run_id,
        "nonce": nonce,
        "worker_name": "gtm-steward",
        "candidate_only": True,
        "orchestration_plan_digest": plan.digest if bound else "",
        "delegation_task_digest": task.digest if bound else "",
        "input_refs": list(task.input_refs) if bound else [],
        "output": {"ImpactCandidate": {"work:sales_quote_a": "AFFECTED_HARD"}},
        "uncertainty": [],
        "prohibited_actions_respected": [
            "no_canonical_write",
            "no_approval",
            "no_apply",
            "no_cross_domain_authority",
        ],
    }
    artifact_root = root / "artifacts"
    path = artifact_root / "gtm-steward" / "result.json"
    path.parent.mkdir(parents=True)
    data = (json.dumps(candidate, separators=(",", ":")) + "\n").encode()
    path.write_bytes(data)
    digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
    evidence = {
        "run_id": run_id,
        "nonce": nonce,
        "matrix": {
            "orchestration_plan_digest": plan.digest,
            "candidate_task_bindings": [
                {
                    "worker_name": "gtm-steward",
                    "delegation_task_digest": task.digest,
                    "input_refs": list(task.input_refs),
                }
            ],
        },
        "artifacts": {
            "candidate_artifacts": [
                {"ref": "result:gtm-steward", "digest": digest}
            ]
        },
    }
    live_receipt = {
        "status": "PASS",
        "evidence_class": "LIVE_AGENTTEAMS",
        "evidence": evidence,
        "receipt_digest": sha256_digest(evidence),
    }
    manifest = {
        "schema_version": "orgrebase.agentteams-artifact-manifest.v1",
        "run_id": run_id,
        "nonce": nonce,
        "artifacts": [
            {
                "ref": "result:gtm-steward",
                "path": "gtm-steward/result.json",
                "digest": digest,
                "producer_worker": "gtm-steward",
                "candidate_only": True,
            }
        ],
    }
    return live_receipt, manifest, artifact_root


def _replace_candidate(
    live_receipt: dict[str, object],
    manifest: dict[str, object],
    artifact_root: Path,
    data: bytes,
) -> None:
    path = artifact_root / "gtm-steward" / "result.json"
    path.write_bytes(data)
    digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
    evidence = live_receipt["evidence"]
    assert isinstance(evidence, dict)
    artifacts = evidence["artifacts"]
    assert isinstance(artifacts, dict)
    artifacts["candidate_artifacts"][0]["digest"] = digest
    live_receipt["receipt_digest"] = sha256_digest(evidence)
    manifest["artifacts"][0]["digest"] = digest


def test_exactly_bound_live_candidate_is_advisory_only(
    tmp_path: Path, fixture: EnterpriseFixture
) -> None:
    live_receipt, manifest, artifact_root = _bundle(tmp_path, fixture, bound=True)
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)

    receipt = AgentTeamsCandidateIngestor().ingest(
        live_receipt=live_receipt,
        artifact_manifest=manifest,
        artifact_root=artifact_root,
        change_set=change_set,
        preview=preview,
        orchestration_plan=OrchestrationCompiler(fixture).compile(change_set, preview),
    )

    assert receipt.decisions[0].decision == "ADVISORY_ACCEPTED"
    assert receipt.decisions[0].admitted_effects == ()
    assert receipt.target_writes == 0


def test_unbound_live_candidate_is_consumed_and_rejected(
    tmp_path: Path, fixture: EnterpriseFixture
) -> None:
    live_receipt, manifest, artifact_root = _bundle(tmp_path, fixture, bound=False)
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)

    receipt = AgentTeamsCandidateIngestor().ingest(
        live_receipt=live_receipt,
        artifact_manifest=manifest,
        artifact_root=artifact_root,
        change_set=change_set,
        preview=preview,
        orchestration_plan=OrchestrationCompiler(fixture).compile(change_set, preview),
    )

    assert receipt.decisions[0].decision == "REJECTED"
    assert receipt.decisions[0].reason_codes == (
        "EXACT_INPUT_BINDING_MISSING",
        "ORCHESTRATION_BINDING_MISSING",
    )
    assert receipt.target_writes == 0


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("plan", "not bound to the current orchestration plan"),
        ("missing", "no candidate task bindings"),
        ("ambiguous", "ambiguous candidate task bindings"),
    ),
)
def test_live_matrix_orchestration_binding_fails_closed(
    tmp_path: Path,
    fixture: EnterpriseFixture,
    mutation: str,
    message: str,
) -> None:
    live_receipt, manifest, artifact_root = _bundle(tmp_path, fixture, bound=True)
    evidence = live_receipt["evidence"]
    assert isinstance(evidence, dict)
    matrix = evidence["matrix"]
    assert isinstance(matrix, dict)
    if mutation == "plan":
        matrix["orchestration_plan_digest"] = "sha256:" + "0" * 64
    elif mutation == "missing":
        matrix.pop("candidate_task_bindings")
    else:
        bindings = matrix["candidate_task_bindings"]
        assert isinstance(bindings, list)
        matrix["candidate_task_bindings"] = [bindings[0], bindings[0]]
    live_receipt["receipt_digest"] = sha256_digest(evidence)
    change_set = build_change_set(fixture)

    with pytest.raises(IntegrityError, match=message):
        AgentTeamsCandidateIngestor().ingest(
            live_receipt=live_receipt,
            artifact_manifest=manifest,
            artifact_root=artifact_root,
            change_set=change_set,
            preview=ImpactEngine(fixture).preview(change_set),
            orchestration_plan=OrchestrationCompiler(fixture).compile(
                change_set, ImpactEngine(fixture).preview(change_set)
            ),
        )


def test_candidate_byte_tamper_fails_closed(
    tmp_path: Path, fixture: EnterpriseFixture
) -> None:
    live_receipt, manifest, artifact_root = _bundle(tmp_path, fixture, bound=True)
    path = artifact_root / "gtm-steward" / "result.json"
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(IntegrityError, match="candidate bytes mismatch"):
        AgentTeamsCandidateIngestor().ingest(
            live_receipt=live_receipt,
            artifact_manifest=manifest,
            artifact_root=artifact_root,
            change_set=build_change_set(fixture),
            preview=ImpactEngine(fixture).preview(build_change_set(fixture)),
            orchestration_plan=OrchestrationCompiler(fixture).compile(
                build_change_set(fixture),
                ImpactEngine(fixture).preview(build_change_set(fixture)),
            ),
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("live", "invalid LIVE_AGENTTEAMS receipt"),
        ("manifest", "artifact manifest is not bound"),
        ("set", "candidate set differs"),
        ("digest", "candidate digest mismatch"),
        ("path", "unsafe or missing"),
    ),
)
def test_candidate_ingestion_rejects_broken_provenance_layers(
    tmp_path: Path,
    fixture: EnterpriseFixture,
    mutation: str,
    message: str,
) -> None:
    live_receipt, manifest, artifact_root = _bundle(tmp_path, fixture, bound=True)
    if mutation == "live":
        live_receipt["status"] = "FAIL"
    elif mutation == "manifest":
        manifest["run_id"] = "run:other"
    elif mutation == "set":
        manifest["artifacts"] = []
    elif mutation == "digest":
        manifest["artifacts"][0]["digest"] = "sha256:" + "0" * 64
    elif mutation == "path":
        manifest["artifacts"][0]["path"] = "../outside.json"

    change_set = build_change_set(fixture)
    with pytest.raises(IntegrityError, match=message):
        AgentTeamsCandidateIngestor().ingest(
            live_receipt=live_receipt,
            artifact_manifest=manifest,
            artifact_root=artifact_root,
            change_set=change_set,
            preview=ImpactEngine(fixture).preview(change_set),
            orchestration_plan=OrchestrationCompiler(fixture).compile(
                change_set, ImpactEngine(fixture).preview(change_set)
            ),
        )


def test_candidate_extra_input_refs_are_rejected(
    tmp_path: Path, fixture: EnterpriseFixture
) -> None:
    live_receipt, manifest, artifact_root = _bundle(tmp_path, fixture, bound=True)
    path = artifact_root / "gtm-steward" / "result.json"
    candidate = json.loads(path.read_text(encoding="utf-8"))
    candidate["input_refs"] = [*candidate["input_refs"], "sha256:" + "a" * 64]
    _replace_candidate(
        live_receipt,
        manifest,
        artifact_root,
        (json.dumps(candidate, separators=(",", ":")) + "\n").encode(),
    )
    change_set = build_change_set(fixture)

    receipt = AgentTeamsCandidateIngestor().ingest(
        live_receipt=live_receipt,
        artifact_manifest=manifest,
        artifact_root=artifact_root,
        change_set=change_set,
        preview=ImpactEngine(fixture).preview(change_set),
        orchestration_plan=OrchestrationCompiler(fixture).compile(
            change_set, ImpactEngine(fixture).preview(change_set)
        ),
    )

    assert receipt.decisions[0].decision == "REJECTED"
    assert "EXACT_INPUT_BINDING_MISSING" in receipt.decisions[0].reason_codes
    assert receipt.target_writes == 0


def test_candidate_ingestion_rejects_invalid_json(
    tmp_path: Path, fixture: EnterpriseFixture
) -> None:
    live_receipt, manifest, artifact_root = _bundle(tmp_path, fixture, bound=True)
    _replace_candidate(live_receipt, manifest, artifact_root, b"not-json\n")
    change_set = build_change_set(fixture)

    with pytest.raises(IntegrityError, match="not valid JSON"):
        AgentTeamsCandidateIngestor().ingest(
            live_receipt=live_receipt,
            artifact_manifest=manifest,
            artifact_root=artifact_root,
            change_set=change_set,
            preview=ImpactEngine(fixture).preview(change_set),
            orchestration_plan=OrchestrationCompiler(fixture).compile(
                change_set, ImpactEngine(fixture).preview(change_set)
            ),
        )


def test_candidate_ingestion_reports_all_structural_rejection_reasons(
    tmp_path: Path, fixture: EnterpriseFixture
) -> None:
    live_receipt, manifest, artifact_root = _bundle(tmp_path, fixture, bound=True)
    path = artifact_root / "gtm-steward" / "result.json"
    candidate = json.loads(path.read_text(encoding="utf-8"))
    candidate["schema_version"] = "fabricated"
    candidate["prohibited_actions_respected"] = []
    candidate["output"] = {}
    _replace_candidate(
        live_receipt,
        manifest,
        artifact_root,
        (json.dumps(candidate, separators=(",", ":")) + "\n").encode(),
    )
    change_set = build_change_set(fixture)

    receipt = AgentTeamsCandidateIngestor().ingest(
        live_receipt=live_receipt,
        artifact_manifest=manifest,
        artifact_root=artifact_root,
        change_set=change_set,
        preview=ImpactEngine(fixture).preview(change_set),
        orchestration_plan=OrchestrationCompiler(fixture).compile(
            change_set, ImpactEngine(fixture).preview(change_set)
        ),
    )

    assert receipt.decisions[0].reason_codes == (
        "AUTHORITY_BOUNDARY_UNPROVEN",
        "CANDIDATE_ENVELOPE_INVALID",
        "STRUCTURED_OUTPUT_MISSING",
    )


def test_non_object_candidate_json_is_envelope_invalid(
    tmp_path: Path, fixture: EnterpriseFixture
) -> None:
    live_receipt, manifest, artifact_root = _bundle(tmp_path, fixture, bound=True)
    _replace_candidate(live_receipt, manifest, artifact_root, b'["not-an-object"]\n')
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)
    receipt = AgentTeamsCandidateIngestor().ingest(
        live_receipt=live_receipt,
        artifact_manifest=manifest,
        artifact_root=artifact_root,
        change_set=change_set,
        preview=preview,
        orchestration_plan=OrchestrationCompiler(fixture).compile(change_set, preview),
    )
    assert receipt.decisions[0].decision == "REJECTED"
    assert "CANDIDATE_ENVELOPE_INVALID" in receipt.decisions[0].reason_codes


def test_undeclared_output_kind_is_rejected(
    tmp_path: Path, fixture: EnterpriseFixture
) -> None:
    live_receipt, manifest, artifact_root = _bundle(tmp_path, fixture, bound=True)
    path = artifact_root / "gtm-steward" / "result.json"
    candidate = json.loads(path.read_text(encoding="utf-8"))
    candidate["output"] = {"ApplyReceipt": {"write": True}}
    _replace_candidate(
        live_receipt,
        manifest,
        artifact_root,
        (json.dumps(candidate, separators=(",", ":")) + "\n").encode(),
    )
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)
    receipt = AgentTeamsCandidateIngestor().ingest(
        live_receipt=live_receipt,
        artifact_manifest=manifest,
        artifact_root=artifact_root,
        change_set=change_set,
        preview=preview,
        orchestration_plan=OrchestrationCompiler(fixture).compile(change_set, preview),
    )
    assert receipt.decisions[0].reason_codes == ("UNDECLARED_OUTPUT_KIND",)


def test_local_ingestion_is_zero_write_and_exactly_bound(
    fixture: EnterpriseFixture,
) -> None:
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)
    envelope = RunEnvelope(
        run_id="run:test:local-ingest@1",
        nonce="c" * 64,
        issued_at="2026-08-14T00:00:00Z",
        expires_at="2026-08-14T01:00:00Z",
        mode="LOCAL_DETERMINISTIC",
        evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
    )
    collaboration = CollaborationAdapter(fixture).run(change_set, preview, envelope)
    receipt = collaboration["candidate_ingestion"]
    recomputed = ingest_local_proposals(
        change_set=change_set,
        preview=preview,
        plan=collaboration["orchestration_plan"],
        handoffs=collaboration["handoffs"],
        coordination_receipt=collaboration["coordination_receipt"],
        run_envelope=envelope,
    )
    assert receipt.digest == recomputed.digest
    assert receipt.target_writes == 0
    assert receipt.source_evidence_class == EvidenceClass.LOCAL_DETERMINISTIC
    assert len(receipt.decisions) == 5
    assert not receipt.rejected_candidate_digests
    assert all(not item.admitted_effects for item in receipt.decisions)


def test_local_ingestion_rejects_undeclared_handoff_kind(
    fixture: EnterpriseFixture,
) -> None:
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)
    envelope = RunEnvelope(
        run_id="run:test:local-ingest-kind@1",
        nonce="d" * 64,
        issued_at="2026-08-14T00:00:00Z",
        expires_at="2026-08-14T01:00:00Z",
        mode="LOCAL_DETERMINISTIC",
        evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
    )
    collaboration = CollaborationAdapter(fixture).run(change_set, preview, envelope)
    gtm = next(item for item in collaboration["handoffs"] if item.from_agent == "gtm-steward")
    forged = gtm.model_copy(
        update={"payload": {**gtm.payload, "kind": "ApplyReceipt"}, "digest": ""}
    )
    handoffs = tuple(
        forged if item.from_agent == "gtm-steward" else item
        for item in collaboration["handoffs"]
    )
    receipt = ingest_local_proposals(
        change_set=change_set,
        preview=preview,
        plan=collaboration["orchestration_plan"],
        handoffs=handoffs,
        coordination_receipt=collaboration["coordination_receipt"],
        run_envelope=envelope,
    )
    rejected = next(item for item in receipt.decisions if item.producer_worker == "gtm-steward")
    assert rejected.decision == "REJECTED"
    assert "UNDECLARED_OUTPUT_KIND" in rejected.reason_codes
    assert receipt.target_writes == 0


def _local_collaboration(fixture: EnterpriseFixture, nonce: str) -> tuple:
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)
    envelope = RunEnvelope(
        run_id=f"run:test:local-ingest:{nonce[:8]}@1",
        nonce=nonce,
        issued_at="2026-08-14T00:00:00Z",
        expires_at="2026-08-14T01:00:00Z",
        mode="LOCAL_DETERMINISTIC",
        evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
    )
    collaboration = CollaborationAdapter(fixture).run(change_set, preview, envelope)
    return change_set, preview, envelope, collaboration


def test_local_ingestion_fails_closed_on_unbound_inputs(
    fixture: EnterpriseFixture,
) -> None:
    change_set, preview, envelope, collaboration = _local_collaboration(fixture, "e" * 64)
    plan = collaboration["orchestration_plan"]
    handoffs = collaboration["handoffs"]
    coordination = collaboration["coordination_receipt"]
    kwargs = {
        "change_set": change_set,
        "preview": preview,
        "plan": plan,
        "handoffs": handoffs,
        "coordination_receipt": coordination,
        "run_envelope": envelope,
    }
    unbound_plan = plan.model_copy(
        update={"change_set_digest": "sha256:" + "0" * 64, "digest": ""}
    )
    with pytest.raises(IntegrityError, match="plan is not bound"):
        ingest_local_proposals(**{**kwargs, "plan": unbound_plan})
    unbound_coordination = coordination.model_copy(update={"status": "FAIL", "digest": ""})
    with pytest.raises(IntegrityError, match="coordination receipt is not bound"):
        ingest_local_proposals(**{**kwargs, "coordination_receipt": unbound_coordination})
    with pytest.raises(IntegrityError, match="duplicate candidate refs"):
        ingest_local_proposals(**{**kwargs, "handoffs": (*handoffs, handoffs[0])})

    escaped = handoffs[0].model_copy(update={"candidate_only": False, "digest": ""})
    receipt = ingest_local_proposals(
        **{**kwargs, "handoffs": (escaped, *handoffs[1:])}
    )
    rejected = next(item for item in receipt.decisions if item.artifact_ref == escaped.id)
    assert "CANDIDATE_ENVELOPE_INVALID" in rejected.reason_codes

    missing_inputs = handoffs[0].model_copy(update={"input_refs": ("missing",), "digest": ""})
    receipt = ingest_local_proposals(
        **{**kwargs, "handoffs": (missing_inputs, *handoffs[1:])}
    )
    rejected = next(item for item in receipt.decisions if item.artifact_ref == missing_inputs.id)
    assert "EXACT_INPUT_BINDING_MISSING" in rejected.reason_codes

    unbound_handoff = handoffs[0].model_copy(
        update={"orchestration_plan_digest": "sha256:" + "0" * 64, "digest": ""}
    )
    receipt = ingest_local_proposals(
        **{**kwargs, "handoffs": (unbound_handoff, *handoffs[1:])}
    )
    rejected = next(item for item in receipt.decisions if item.artifact_ref == unbound_handoff.id)
    assert "ORCHESTRATION_BINDING_MISSING" in rejected.reason_codes


def test_zero_write_ingestion_rejects_writes_or_effects(
    fixture: EnterpriseFixture,
) -> None:
    _, _, _, collaboration = _local_collaboration(fixture, "f" * 64)
    receipt = collaboration["candidate_ingestion"]
    with pytest.raises(IntegrityError, match="granted target writes"):
        assert_zero_write_ingestion(
            receipt.model_copy(update={"target_writes": 1, "digest": ""})
        )
    decision = receipt.decisions[0].model_copy(
        update={"admitted_effects": ({"object_id": "work:sales_quote_a"},)}
    )
    with pytest.raises(IntegrityError, match="admitted effects"):
        assert_zero_write_ingestion(
            receipt.model_copy(update={"decisions": (decision, *receipt.decisions[1:]), "digest": ""})
        )
