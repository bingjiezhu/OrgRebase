from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import rfc8785

from orgrebase.auth import AuthenticationError, request_authorization
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.oac_quote_adaptation import (
    OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
    OACAdaptationReviewGatePending,
    OACAdapterActivationBinding,
    OACQuoteAdaptationService,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.profile import supplier_shadow_intake_profile

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "examples" / "enterprise-quote-pilot" / "evergreen"
OAC_ROOT = ROOT.parent / "oac-spec"


class _Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture(scope="module")
def runtime():
    assert (OAC_ROOT / "pyproject.toml").is_file()
    return load_enterprise_quote_pilot_pack(PACK)


def _service(
    database: Path,
    *,
    runtime,
    clock: _Clock,
    execution_run_id: str | None = "run:test:oac-bound@v1",
) -> OACQuoteAdaptationService:
    return OACQuoteAdaptationService(
        store=StateStore(database),
        profile=runtime.profile,
        runtime=runtime,
        oac_root=OAC_ROOT,
        wall_clock=clock,
        execution_run_id=execution_run_id,
    )


def _review_subject(view: dict[str, object]) -> dict[str, object]:
    summary = view.get("owner_review_summary")
    digest = summary.get("digest") if isinstance(summary, dict) else None
    return {
        "owner_review_summary_digest": digest or "sha256:" + "0" * 64,
        "acknowledgements": OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
    }


@pytest.mark.parametrize("operation", ["prepare", "approve"])
def test_oac_session_revocation_at_commit_rolls_back_governance_records(tmp_path, runtime, operation):
    clock = _Clock()
    service = _service(tmp_path / "revoked.sqlite", runtime=runtime, clock=clock)
    try:
        prepared = service.prepare(command_id="prepare:initial") if operation == "approve" else None
        clock.advance(4)
        before = tuple(service.store.connection.iterdump())
        checks = []

        def authorization():
            checks.append(True)
            if len(checks) == 2:
                raise AuthenticationError("AUTH_LOCAL_SESSION_REQUIRED")

        token = request_authorization.set(authorization)
        try:
            with pytest.raises(AuthenticationError, match="AUTH_LOCAL_SESSION_REQUIRED"):
                if operation == "prepare":
                    service.prepare(command_id="prepare:revoked")
                else:
                    service.approve(actor_id=prepared["human_authority_ref"], candidate_digest=prepared["candidate_digest"],
                                    command_id="approve:revoked", **_review_subject(prepared))
        finally:
            request_authorization.reset(token)
        assert len(checks) == 2
        assert tuple(service.store.connection.iterdump()) == before
    finally:
        service.store.close()


def test_evergreen_requires_exact_owner_digest_and_four_second_review_before_ready(
    tmp_path: Path,
    runtime,
) -> None:
    clock = _Clock()
    service = _service(tmp_path / "adaptation.sqlite3", runtime=runtime, clock=clock)
    try:
        prepared = service.prepare(command_id="command:prepare@1")
        assert prepared["status"] == "OWNER_REVIEW_PENDING"
        assert prepared["adaptation_epoch"] == "DEPENDENCY_BOUND_R2"
        assert prepared["rule_set_digest"].startswith("sha256:")
        assert prepared["review_remaining_ms"] == 4_000
        assert len(prepared["candidate_mappings"]) == 5
        assert {item["component_kind"] for item in prepared["candidate_mappings"]} == {
            "DOMAIN",
            "KNOWLEDGE",
            "AUTHORITY",
            "CAPABILITY",
            "DEPENDENCY",
        }
        assert prepared["oac_public_validation"]["status"] == "PASS"
        assert [item["command"] for item in prepared["oac_public_validation"]["commands"]] == [
            "digest:OrganizationSnapshot",
            "validate:OrganizationSnapshot:verify-digest",
            "digest:OrganizationalDemand",
            "validate:OrganizationalDemand:verify-digest",
            "validate-evolution:OrganizationalDemand",
        ]
        assert prepared["canonical_target_writes"] == 0
        assert prepared["oac_plan_produced"] is False
        assert prepared["oac_plan_certificate_produced"] is False
        assert prepared["oac_runtime_invoked"] is False
        for resource in (prepared["organization_snapshot"], prepared["organizational_demand"]):
            raw_projection = {key: value for key, value in resource.items() if key != "digest"}
            assert resource["digest"] == "sha256:" + hashlib.sha256(rfc8785.dumps(raw_projection)).hexdigest()
        snapshot_spec = prepared["organization_snapshot"]["spec"]
        dependency_edges = snapshot_spec["dependencyEdges"]
        assert [item["relationType"] for item in dependency_edges] == [
            "business_dependency",
            "traceability",
            "operational_dependency",
        ]
        assert [item["admissionStatus"] for item in dependency_edges] == [
            "admitted",
            "admitted",
            "candidate",
        ]
        assert snapshot_spec["impactRules"] == []
        assert snapshot_spec["completeness"]["coveredRelationTypes"] == [
            "business_dependency",
            "operational_dependency",
            "traceability",
        ]
        endpoint_refs = {
            value for item in dependency_edges for value in (item["sourceRef"], item["targetRef"])
        }
        node_refs = {item["nodeId"] for item in snapshot_spec["nodes"]}
        assert len(endpoint_refs) == 6
        assert endpoint_refs.issubset(node_refs)
        assert snapshot_spec["completeness"]["coveredNodeRefs"] == sorted(node_refs)
        projection = prepared["dependency_projection_receipt"]
        assert projection["verdict"] == "PASS"
        assert projection["edge_count"] == 3
        assert projection["organization_snapshot_digest"] == prepared["organization_snapshot_digest"]
        assert (
            prepared["oac_public_validation"]["dependency_projection_receipt_digest"] == projection["digest"]
        )
        review_summary = prepared["owner_review_summary"]
        assert review_summary["synthetic"] is True
        assert review_summary["evidence_label"] == ("CONTROLLED_SYNTHETIC_ENTERPRISE_INPUT")
        assert [item["component_kind"] for item in review_summary["component_reviews"]] == [
            "DOMAIN",
            "KNOWLEDGE",
            "AUTHORITY",
            "CAPABILITY",
            "DEPENDENCY",
        ]
        assert review_summary["non_effects"] == [
            "NO_QUOTE_BUSINESS_APPROVAL",
            "NO_EXTERNAL_SYSTEM_WRITE",
            "NO_AGENT_AUTHORITY_EXPANSION",
        ]
        assert review_summary["dependency_target_count"] == 3
        assert review_summary["dependency_edge_count"] == 3
        assert review_summary["dependency_owner_refs"] == [
            "human:evergreen-finance-owner",
            "human:evergreen-gtm-owner",
        ]
        dependency_review = next(
            item for item in review_summary["component_reviews"] if item["component_kind"] == "DEPENDENCY"
        )
        assert dependency_review["responsible_refs"] == review_summary["dependency_owner_refs"]
        encoded_review = json.dumps(review_summary, ensure_ascii=False)
        assert str(PACK) not in encoded_review
        assert "raw_private_value" not in encoded_review
        assert "pack_root" not in encoded_review
        assert "Evergreen Enterprise Plus" not in encoded_review
        assert "USD" not in encoded_review
        assert "strategic" not in encoded_review
        knowledge_review = next(
            item for item in review_summary["component_reviews"] if item["component_kind"] == "KNOWLEDGE"
        )
        assert all(sample.startswith("field:") for sample in knowledge_review["sample_items"])
        assert all("=" not in sample for sample in knowledge_review["sample_items"])

        with pytest.raises(RuntimeError, match="OAC_ADAPTATION_OWNER_REVIEW_SUMMARY_MISMATCH"):
            service.approve(
                actor_id=prepared["human_authority_ref"],
                candidate_digest=prepared["candidate_digest"],
                command_id="command:approve:substituted-summary@1",
                owner_review_summary_digest="sha256:" + "9" * 64,
                acknowledgements=OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
            )
        with pytest.raises(RuntimeError, match="OAC_ADAPTATION_OWNER_ACKNOWLEDGEMENTS_REQUIRED"):
            service.approve(
                actor_id=prepared["human_authority_ref"],
                candidate_digest=prepared["candidate_digest"],
                command_id="command:approve:missing-acknowledgements@1",
                owner_review_summary_digest=review_summary["digest"],
                acknowledgements=(),
            )

        with pytest.raises(
            OACAdaptationReviewGatePending,
            match="OAC_ADAPTATION_REVIEW_GATE_NOT_READY",
        ) as early:
            service.approve(
                actor_id=prepared["human_authority_ref"],
                candidate_digest=prepared["candidate_digest"],
                command_id="command:approve:early@1",
                **_review_subject(prepared),
            )
        assert early.value.remaining_ms == 4_000

        for wrong_actor in (
            "human:not-the-owner",
            "principal:oac-adaptation-mapper",
        ):
            with pytest.raises(AuthorizationError, match="OAC_ADAPTATION_OWNER_MISMATCH"):
                service.approve(
                    actor_id=wrong_actor,
                    candidate_digest=prepared["candidate_digest"],
                    command_id=f"command:approve:{wrong_actor}@1",
                    **_review_subject(prepared),
                )
        with pytest.raises(RuntimeError, match="OAC_ADAPTATION_CANDIDATE_DIGEST_MISMATCH"):
            service.approve(
                actor_id=prepared["human_authority_ref"],
                candidate_digest="sha256:" + "0" * 64,
                command_id="command:approve:substituted-digest@1",
                **_review_subject(prepared),
            )

        assert service.view()["status"] == "OWNER_REVIEW_PENDING"
        clock.advance(4)
        ready = service.approve(
            actor_id=prepared["human_authority_ref"],
            candidate_digest=prepared["candidate_digest"],
            command_id="command:approve@1",
            **_review_subject(prepared),
        )
        assert ready["status"] == "READY_FOR_ORGREBASE"
        assert ready["formation_parity_status"] == "PASS"
        assert ready["source_admission_receipt_digest"].startswith("sha256:")
        assert ready["adapter_capsule"]["claim"] == (
            "OAC_SOURCE_DEMAND_ADMITTED_AND_ORGREBASE_FORMATION_PARITY"
        )
        assert ready["adapter_capsule"]["formation_authority"] == "ORGREBASE_CONTROL_PLANE"
        assert ready["approval"]["owner_review_summary_digest"] == review_summary["digest"]
        assert (
            ready["approval"]["source_admission_receipt"]["spec"]["ruleSetDigest"]
            == prepared["rule_set_digest"]
        )
        assert ready["adapter_capsule"]["owner_review_summary_digest"] == review_summary["digest"]
        assert ready["approval"]["acknowledgements"] == list(OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS)
        assert ready["adapter_capsule"]["oac_plan_produced"] is False
        assert ready["adapter_capsule"]["oac_plan_certificate_produced"] is False
        assert ready["adapter_capsule"]["dependency_projection_receipt_digest"] == projection["digest"]
        assert ready["activation_binding"]["binding_timing"] == "PRE_EXECUTION_EXACT_BINDING"
        assert ready["activation_binding"]["execution_run_id"] == "run:test:oac-bound@v1"
        assert (
            service.require_activation_binding(
                profile_digest=runtime.profile.digest,
                pack_digest=runtime.pack_digest,
                execution_run_id="run:test:oac-bound@v1",
            )
            == service.activation_binding()
        )

        for field, wrong_value in (
            ("profile_digest", "sha256:" + "1" * 64),
            ("pack_digest", "sha256:" + "2" * 64),
            ("execution_run_id", "run:test:stale@v1"),
        ):
            arguments = {
                "profile_digest": runtime.profile.digest,
                "pack_digest": runtime.pack_digest,
                "execution_run_id": "run:test:oac-bound@v1",
            }
            arguments[field] = wrong_value
            with pytest.raises(RuntimeError, match="OAC_ADAPTATION_ACTIVATION_BINDING_MISMATCH"):
                service.require_activation_binding(**arguments)

        substituted = dict(ready["activation_binding"])
        substituted["adapter_capsule_digest"] = "sha256:" + "3" * 64
        with pytest.raises(ValueError, match="content digest mismatch"):
            OACAdapterActivationBinding.model_validate(substituted)
    finally:
        service.store.close()


def test_veracier_intake_only_profile_retains_unknowns_and_cannot_be_approved(
    tmp_path: Path,
) -> None:
    profile = supplier_shadow_intake_profile()
    service = OACQuoteAdaptationService(
        store=StateStore(tmp_path / "veracier.sqlite3"),
        profile=profile,
        runtime=None,
        oac_root=OAC_ROOT,
        wall_clock=_Clock(),
    )
    try:
        held = service.prepare(command_id="command:prepare:veracier@1")
        assert held["status"] == "HOLD"
        assert held["pack_digest"] is None
        assert held["organization_snapshot"] is None
        assert held["organizational_demand"] is None
        assert held["review_gate"] is None
        assert held["canonical_target_writes"] == 0
        assert any(item["reason_code"] == "EXACT_ENTERPRISE_PACK_NOT_ADMITTED" for item in held["gaps"])
        assert any(
            "dependencies.historicalOutcomeBaseline" in item["declared_unknowns"]
            for item in held["candidate_mappings"]
            if item["component_kind"] == "DEPENDENCY"
        )
        with pytest.raises(RuntimeError, match="OAC_ADAPTATION_HOLD_NOT_APPROVABLE"):
            service.approve(
                actor_id=held["human_authority_ref"],
                candidate_digest=held["candidate_digest"],
                command_id="command:approve:veracier@1",
                **_review_subject(held),
            )
    finally:
        service.store.close()


def test_pending_gate_and_ready_capsule_survive_store_reopen(
    tmp_path: Path,
    runtime,
) -> None:
    database = tmp_path / "recover.sqlite3"
    clock = _Clock()
    first = _service(database, runtime=runtime, clock=clock)
    prepared = first.prepare(command_id="command:prepare:recover@1")
    first.store.close()

    clock.advance(4)
    reopened = _service(database, runtime=runtime, clock=clock)
    try:
        assert reopened.view()["candidate_digest"] == prepared["candidate_digest"]
        ready = reopened.approve(
            actor_id=prepared["human_authority_ref"],
            candidate_digest=prepared["candidate_digest"],
            command_id="command:approve:recover@1",
            **_review_subject(prepared),
        )
        expected_binding_digest = ready["activation_binding"]["digest"]
    finally:
        reopened.store.close()

    recovered = _service(database, runtime=runtime, clock=clock)
    try:
        assert recovered.view()["status"] == "READY_FOR_ORGREBASE"
        assert recovered.activation_binding().digest == expected_binding_digest
        assert recovered.verify_parity().verdict == "PASS"
    finally:
        recovered.store.close()


def test_activation_rejects_legacy_capsule_and_binding_without_full_review_lineage(
    tmp_path: Path,
    runtime,
) -> None:
    clock = _Clock()
    service = _service(tmp_path / "legacy-lineage.sqlite3", runtime=runtime, clock=clock)
    try:
        prepared = service.prepare(command_id="command:prepare:legacy-lineage@1")
        clock.advance(4)
        service.approve(
            actor_id=prepared["human_authority_ref"],
            candidate_digest=prepared["candidate_digest"],
            command_id="command:approve:legacy-lineage@1",
            **_review_subject(prepared),
        )
        with service.store.transaction() as connection:
            for kind in (
                "adaptation-draft",
                "adaptation-approval",
                "source-admission",
                "formation-parity",
            ):
                connection.execute(
                    "DELETE FROM artifacts WHERE artifact_id = ?",
                    (service._artifact_id(kind),),
                )
        with pytest.raises(
            RuntimeError,
            match="OAC_ADAPTATION_ACTIVATION_LINEAGE_REQUIRED",
        ):
            service.require_activation_binding(
                profile_digest=runtime.profile.digest,
                pack_digest=runtime.pack_digest,
                execution_run_id="run:test:oac-bound@v1",
            )
    finally:
        service.store.close()


def test_tampered_review_owner_is_rejected_before_any_successor_is_persisted(
    tmp_path: Path,
    runtime,
) -> None:
    clock = _Clock()
    service = _service(tmp_path / "review-owner.sqlite3", runtime=runtime, clock=clock)
    try:
        prepared = service.prepare(command_id="command:prepare:review-owner@1")
        stored = service.store.load_artifact(
            service._artifact_id("adaptation-draft"),
            "application/vnd.orgrebase.oac-quote-adaptation-draft+json",
        ).payload
        draft = json.loads(json.dumps(stored))
        summary = dict(draft["owner_review_summary"])
        summary["decision_owner_ref"] = "human:substituted-owner"
        summary.pop("digest", None)
        summary["digest"] = sha256_digest(summary)
        gate = dict(draft["review_gate"])
        gate["owner_review_summary_digest"] = summary["digest"]
        gate.pop("digest", None)
        gate["digest"] = sha256_digest(gate)
        draft["owner_review_summary"] = summary
        draft["review_gate"] = gate
        draft.pop("digest", None)
        draft["digest"] = sha256_digest(draft)
        with service.store.transaction() as connection:
            connection.execute(
                "UPDATE artifacts SET payload_json = ?, payload_digest = ? WHERE artifact_id = ?",
                (
                    canonical_json(draft),
                    sha256_digest(draft),
                    service._artifact_id("adaptation-draft"),
                ),
            )
        clock.advance(4)
        with pytest.raises(
            ValueError,
            match="OAC_ADAPTATION_OWNER_REVIEW_SUMMARY_BINDING_INVALID",
        ):
            service.approve(
                actor_id=prepared["human_authority_ref"],
                candidate_digest=prepared["candidate_digest"],
                command_id="command:approve:review-owner@1",
                owner_review_summary_digest=summary["digest"],
                acknowledgements=OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
            )
        successor_ids = tuple(
            service._artifact_id(kind)
            for kind in (
                "adaptation-approval",
                "adapter-capsule",
                "activation-binding",
            )
        )
        assert all(
            service.store.connection.execute(
                "SELECT COUNT(*) FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()[0]
            == 0
            for artifact_id in successor_ids
        )
    finally:
        service.store.close()


def test_stable_adaptation_identity_uses_one_persisted_unique_execution_attempt(
    tmp_path: Path,
    runtime,
) -> None:
    clock = _Clock()
    database = tmp_path / "attempt-identity.sqlite3"
    first = _service(
        database,
        runtime=runtime,
        clock=clock,
        execution_run_id=None,
    )
    prepared = first.prepare(command_id="command:prepare:attempt-identity@1")
    first_execution_run_id = prepared["execution_run_id"]
    assert first_execution_run_id.startswith("run:orgrebase:oac-bound:")
    first.store.close()

    reopened = _service(
        database,
        runtime=runtime,
        clock=clock,
        execution_run_id=None,
    )
    try:
        replay = reopened.prepare(command_id="command:prepare:attempt-identity@1")
        assert replay["adaptation_run_id"] == prepared["adaptation_run_id"]
        assert replay["execution_run_id"] == first_execution_run_id
        clock.advance(4)
        ready = reopened.approve(
            actor_id=replay["human_authority_ref"],
            candidate_digest=replay["candidate_digest"],
            command_id="command:approve:attempt-identity@1",
            **_review_subject(replay),
        )
        assert ready["activation_binding"]["execution_run_id"] == first_execution_run_id
        assert ready["approval"]["review_gate"]["digest"] == replay["review_gate_digest"]
    finally:
        reopened.store.close()

    independent = _service(
        tmp_path / "independent-attempt.sqlite3",
        runtime=runtime,
        clock=_Clock(),
        execution_run_id=None,
    )
    try:
        other = independent.prepare(command_id="command:prepare:independent-attempt@1")
        assert other["adaptation_run_id"] == prepared["adaptation_run_id"]
        assert other["execution_run_id"] != first_execution_run_id
    finally:
        independent.store.close()


def test_prepare_digests_are_stable_across_fresh_python_processes(
    tmp_path: Path,
) -> None:
    script = """
import json
import sys
from pathlib import Path
from orgrebase.store import StateStore
from orgrebase.workspace.oac_quote_adaptation import OACQuoteAdaptationService
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack

root = Path(sys.argv[1])
runtime = load_enterprise_quote_pilot_pack(root / 'examples/enterprise-quote-pilot/evergreen')
with StateStore(Path(sys.argv[2])) as store:
    service = OACQuoteAdaptationService(
        store=store,
        profile=runtime.profile,
        runtime=runtime,
        oac_root=root.parent / 'oac-spec',
        wall_clock=lambda: 1000.0,
        execution_run_id='run:test:oac-bound@v1',
    )
    value = service.prepare(command_id='command:prepare:repeatable@1')
    print(json.dumps({
        'adaptation_run_id': value['adaptation_run_id'],
        'draft_digest': value['digest'],
        'mapping_set_digest': value['mapping_set_digest'],
        'snapshot_digest': value['organization_snapshot_digest'],
        'demand_digest': value['organizational_demand_digest'],
        'gate_digest': value['review_gate_digest'],
        'dependency_projection_digest': value['dependency_projection_receipt']['digest'],
    }, sort_keys=True))
"""
    observed = []
    for index in range(2):
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(ROOT),
                str(tmp_path / f"process-{index}.sqlite3"),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        observed.append(json.loads(completed.stdout))
    assert observed[0] == observed[1]


def test_unknown_workspace_dependency_relation_fails_closed(
    tmp_path: Path,
    runtime,
) -> None:
    original = runtime.universe.edges[0]
    edge_payload = original.model_dump(mode="json")
    edge_payload.pop("digest", None)
    edge_payload["relation"] = "UNDECLARED_RELATION"
    mutated_edge = type(original).model_validate(edge_payload)
    universe_payload = runtime.universe.model_dump(mode="json")
    universe_payload.pop("digest", None)
    universe_payload["edges"][0] = mutated_edge.model_dump(mode="json")
    mutated_universe = type(runtime.universe).model_validate(universe_payload)
    mutated_runtime = replace(runtime, universe=mutated_universe)
    service = _service(
        tmp_path / "unknown-relation.sqlite3",
        runtime=mutated_runtime,
        clock=_Clock(),
    )
    try:
        with pytest.raises(
            IntegrityError,
            match="OAC_DEPENDENCY_PROJECTION_RELATION_UNSUPPORTED",
        ):
            service.prepare(command_id="command:prepare:unknown-relation@1")
        assert service.view()["status"] == "PACK_OBSERVED"
    finally:
        service.store.close()


def test_activation_recomputes_and_rejects_substituted_dependency_receipt(
    tmp_path: Path,
    runtime,
) -> None:
    clock = _Clock()
    service = _service(
        tmp_path / "dependency-receipt-tamper.sqlite3",
        runtime=runtime,
        clock=clock,
    )
    try:
        prepared = service.prepare(command_id="command:prepare:dependency-tamper@1")
        clock.advance(4)
        ready = service.approve(
            actor_id=prepared["human_authority_ref"],
            candidate_digest=prepared["candidate_digest"],
            command_id="command:approve:dependency-tamper@1",
            **_review_subject(prepared),
        )
        artifact_id = service._artifact_id("dependency-projection")
        retained = service.store.load_artifact(
            artifact_id,
            "application/vnd.orgrebase.oac-dependency-projection-receipt+json",
        ).payload
        substituted = json.loads(json.dumps(retained))
        substituted["organization_snapshot_digest"] = "sha256:" + "7" * 64
        substituted.pop("digest", None)
        substituted["digest"] = sha256_digest(substituted)
        with service.store.transaction() as connection:
            connection.execute(
                "UPDATE artifacts SET payload_json = ?, payload_digest = ? WHERE artifact_id = ?",
                (
                    canonical_json(substituted),
                    sha256_digest(substituted),
                    artifact_id,
                ),
            )
        with pytest.raises(
            RuntimeError,
            match="OAC_DEPENDENCY_PROJECTION_BINDING_MISMATCH",
        ):
            service.require_activation_binding(
                profile_digest=runtime.profile.digest,
                pack_digest=runtime.pack_digest,
                execution_run_id=ready["activation_binding"]["execution_run_id"],
            )
    finally:
        service.store.close()


def test_r2_projection_identity_preserves_legacy_zero_edge_draft(
    tmp_path: Path,
    runtime,
) -> None:
    database = tmp_path / "legacy-zero-edge.sqlite3"
    service = _service(database, runtime=runtime, clock=_Clock())
    legacy_artifact_id = f"oac-quote-adaptation-draft:{service._key}@r1"
    legacy_payload = {
        "schema_version": "orgrebase.oac-quote-adaptation-draft.v1",
        "status": "OWNER_REVIEW_PENDING",
        "organization_snapshot": {"spec": {"dependencyEdges": []}},
        "legacy_marker": "PRESERVE_DO_NOT_UPGRADE_IN_PLACE",
    }
    try:
        with service.store.transaction() as connection:
            service.store.save_artifact(
                connection,
                legacy_artifact_id,
                "application/vnd.orgrebase.oac-quote-adaptation-draft+json",
                legacy_payload,
            )
        prepared = service.prepare(command_id="command:prepare:v2-projection@1")
        assert prepared["adaptation_run_id"].endswith("@v1")
        assert service._artifact_id("adaptation-draft").endswith("@r2")
        assert prepared["dependency_projection_receipt"]["edge_count"] == 3
        assert (
            service.store.load_artifact(
                legacy_artifact_id,
                "application/vnd.orgrebase.oac-quote-adaptation-draft+json",
            ).payload
            == legacy_payload
        )
    finally:
        service.store.close()
