from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from oac import cli as cli_module
from oac.canonical import (
    OACValidationError,
    calculate_digest,
    canonical_bytes,
    parse_resource,
    seal_resource,
    verify_resource_digest,
)
from oac.cli import main
from oac.compiler import CompilationError, compile_supplier_change
from oac.models import (
    AdmissionStatus,
    BoundaryStatus,
    ChangeDelta,
    CompletenessManifest,
    DependencyEdge,
    EffectCeiling,
    ImpactRule,
    ObservedValue,
    OrganizationNode,
    OrganizationSnapshot,
    OrganizationSnapshotSpec,
    Principal,
    RelationType,
    ResourceMetadata,
    RoleDefinition,
    SemanticChangeSet,
    SemanticChangeSetSpec,
    SeparationConstraint,
    TransferPredicate,
)
from oac.registry import KIND_MODELS, KIND_REGISTRY, REASON_CODE_REGISTRY

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def make_snapshot(*, boundary: BoundaryStatus = BoundaryStatus.PARTIAL) -> OrganizationSnapshot:
    nodes = (
        OrganizationNode(
            nodeId="urn:node:supplier-acme",
            nodeType="supplier",
            domainRef="urn:domain:procurement",
            ownerRoleRef="urn:role:procurement",
        ),
        OrganizationNode(
            nodeId="urn:node:contract-acme",
            nodeType="contract",
            domainRef="urn:domain:legal",
            ownerRoleRef="urn:role:legal",
        ),
        OrganizationNode(
            nodeId="urn:node:financial-exposure-acme",
            nodeType="financial_exposure",
            domainRef="urn:domain:finance",
            ownerRoleRef="urn:role:finance",
        ),
        OrganizationNode(
            nodeId="urn:node:component-acme",
            nodeType="component",
            domainRef="urn:domain:operations",
            ownerRoleRef="urn:role:operations",
        ),
        OrganizationNode(
            nodeId="urn:node:compliance-case-acme",
            nodeType="compliance_case",
            domainRef="urn:domain:compliance",
            ownerRoleRef="urn:role:compliance",
        ),
        OrganizationNode(
            nodeId="urn:node:dependency-catalog",
            nodeType="knowledge_asset",
            domainRef="urn:domain:governance",
            ownerRoleRef="urn:role:dependency-steward",
        ),
    )
    roles = (
        _role("compliance", "compliance", "review compliance exposure", "q-compliance"),
        _role("dependency-steward", "governance", "resolve dependency uncertainty", "q-graph"),
        _role("finance", "finance", "assess financial exposure", "q-finance"),
        _role("legal", "legal", "review supplier contract", "q-legal"),
        _role("operations", "operations", "assess continuity", "q-operations"),
        _role("procurement", "procurement", "assess supplier status", "q-procurement"),
    )
    principals = (
        _principal("buyer-a", ("procurement",), ("q-procurement",)),
        _principal("buyer-b", ("procurement",), ("q-procurement",)),
        _principal("compliance", ("compliance",), ("q-compliance",)),
        _principal(
            "cross",
            ("finance", "legal"),
            ("q-finance", "q-legal"),
        ),
        _principal("finance", ("finance",), ("q-finance",)),
        _principal("legal", ("legal",), ("q-legal",)),
        _principal("operations", ("operations",), ("q-operations",)),
        _principal("steward", ("dependency-steward",), ("q-graph",)),
    )
    edges = (
        DependencyEdge(
            edgeId="urn:edge:supplier-contract",
            sourceRef="urn:node:supplier-acme",
            targetRef="urn:node:contract-acme",
            relationType=RelationType.CONTRACTUAL_DEPENDENCY,
            transferPredicate=_at_risk_transfer(),
            admissionStatus=AdmissionStatus.ADMITTED,
        ),
        DependencyEdge(
            edgeId="urn:edge:supplier-finance",
            sourceRef="urn:node:supplier-acme",
            targetRef="urn:node:financial-exposure-acme",
            relationType=RelationType.FINANCIAL_EXPOSURE,
            transferPredicate=_at_risk_transfer(),
            admissionStatus=AdmissionStatus.ADMITTED,
        ),
        DependencyEdge(
            edgeId="urn:edge:contract-compliance",
            sourceRef="urn:node:contract-acme",
            targetRef="urn:node:compliance-case-acme",
            relationType=RelationType.COMPLIANCE_DEPENDENCY,
            transferPredicate=_at_risk_transfer(),
            admissionStatus=AdmissionStatus.ADMITTED,
        ),
        DependencyEdge(
            edgeId="urn:edge:supplier-component-candidate",
            sourceRef="urn:node:supplier-acme",
            targetRef="urn:node:component-acme",
            relationType=RelationType.OPERATIONAL_DEPENDENCY,
            transferPredicate=_at_risk_transfer(),
            admissionStatus=AdmissionStatus.CANDIDATE,
        ),
        # A weak shortcut is intentionally non-propagating; the longer admitted
        # contractual/compliance path must remain visible.
        DependencyEdge(
            edgeId="urn:edge:supplier-compliance-trace",
            sourceRef="urn:node:supplier-acme",
            targetRef="urn:node:compliance-case-acme",
            relationType=RelationType.TRACEABILITY,
            transferPredicate=_at_risk_transfer(),
            admissionStatus=AdmissionStatus.ADMITTED,
        ),
    )
    snapshot = OrganizationSnapshot(
        metadata=ResourceMetadata(
            id="urn:snapshot:supplier-case",
            namespace="urn:organization:veracier",
            revision=1,
            ownerRef="urn:role:dependency-steward",
            governanceRef="urn:governance:oac-shadow",
            createdAt=NOW,
            effectiveFrom=NOW,
            sourceRefs=("urn:dataset:edith",),
        ),
        spec=OrganizationSnapshotSpec(
            nodes=nodes,
            roleDefinitions=roles,
            principals=principals,
            dependencyEdges=edges,
            impactRules=(
                ImpactRule(
                    ruleId="urn:rule:supplier-bankruptcy",
                    semanticType="supplier.status",
                    afterValues=("bankruptcy_proceedings",),
                    targetRef="urn:node:supplier-acme",
                    requiredRoleRef="urn:role:procurement",
                    obligationType="assess_supplier_status",
                    requiredEvidence=("supplier_status_record",),
                    admissionStatus=AdmissionStatus.ADMITTED,
                ),
            ),
            separationConstraints=(
                SeparationConstraint(
                    constraintId="urn:sod:finance-legal",
                    leftRoleRef="urn:role:finance",
                    rightRoleRef="urn:role:legal",
                ),
            ),
            completeness=CompletenessManifest(
                status=boundary,
                coveredNodeRefs=tuple(node.node_id for node in nodes),
                coveredRelationTypes=tuple(RelationType),
                maxDepth=4,
                knownGaps=()
                if boundary is BoundaryStatus.COMPLETE
                else ("urn:gap:acquired-subsidiary",),
                discoveryRoleRef="urn:role:dependency-steward",
            ),
        ),
    )
    return seal_resource(snapshot)


def make_change(*, after: str = "bankruptcy_proceedings") -> SemanticChangeSet:
    change = SemanticChangeSet(
        metadata=ResourceMetadata(
            id="urn:change:supplier-bankruptcy",
            namespace="urn:organization:veracier",
            revision=1,
            ownerRef="urn:role:procurement",
            governanceRef="urn:governance:oac-shadow",
            createdAt=NOW,
            effectiveFrom=NOW,
            sourceRefs=(
                "urn:dataset:edith:proc-01",
                "urn:knowledge-view:supplier-master@813",
            ),
        ),
        spec=SemanticChangeSetSpec(
            demandRef="urn:demand:supplier-risk-assessment",
            subjectRef="urn:node:supplier-acme",
            semanticType="supplier.status",
            deltas=(
                ChangeDelta(
                    path="/status",
                    operation="replace",
                    before=ObservedValue(state="known", value="active"),
                    after=ObservedValue(state="known", value=after),
                    beforeVersion="supplier-master@812",
                    afterVersion="supplier-master@813",
                ),
            ),
            observedAt=NOW,
            effectiveAt=NOW,
            sourceRef="urn:knowledge-view:supplier-master@813",
            scopeRefs=("urn:organization:veracier",),
            reason="supplier status changed",
            admissionStatus=AdmissionStatus.ADMITTED,
        ),
    )
    return seal_resource(change)


def _role(name: str, domain: str, mission: str, qualification: str) -> RoleDefinition:
    return RoleDefinition(
        roleId=f"urn:role:{name}",
        domainRef=f"urn:domain:{domain}",
        mission=mission,
        responsibilityTypes=("assess",),
        requiredQualifications=(qualification,),
        effectCeiling=EffectCeiling.ZERO_EFFECT,
        admissionStatus=AdmissionStatus.ADMITTED,
    )


def _principal(
    name: str, role_names: tuple[str, ...], qualifications: tuple[str, ...]
) -> Principal:
    return Principal(
        principalId=f"urn:principal:{name}",
        principalType="human",
        eligibleRoleRefs=tuple(f"urn:role:{role}" for role in role_names),
        qualificationRefs=qualifications,
        status="active",
        admissionStatus=AdmissionStatus.ADMITTED,
    )


def _at_risk_transfer() -> TransferPredicate:
    return TransferPredicate(
        semanticType="supplier.status",
        afterValues=(
            "bankruptcy_proceedings",
            "judicial_restructuring",
            "liquidation",
            "qualification_suspended",
            "capacity_constrained",
            "payment_recovery_uncertain",
            "delivery_interrupted",
        ),
    )


def test_registered_resources_are_strict_and_round_trip() -> None:
    snapshot = make_snapshot()
    raw = snapshot.model_dump_json(by_alias=True)
    parsed = parse_resource(raw, verify_digest=True)
    assert parsed == snapshot

    value = json.loads(raw)
    value["surprise"] = True
    with pytest.raises(OACValidationError) as exc_info:
        parse_resource(value)
    assert exc_info.value.reason_code == "CORE_SCHEMA_INVALID"


def test_duplicate_keys_and_unknown_kinds_are_rejected() -> None:
    with pytest.raises(OACValidationError) as duplicate:
        parse_resource('{"kind":"OrganizationSnapshot","kind":"SemanticChangeSet"}')
    assert duplicate.value.reason_code == "CORE_SCHEMA_INVALID"

    with pytest.raises(OACValidationError) as unknown:
        parse_resource('{"apiVersion":"oac.dev/v0alpha1","kind":"Imaginary"}')
    assert unknown.value.reason_code == "CORE_KIND_UNKNOWN"


def test_digest_is_rfc8785_detached_and_sensitive_to_normative_content() -> None:
    snapshot = make_snapshot()
    assert snapshot.digest == calculate_digest(snapshot)
    assert seal_resource(snapshot).digest == snapshot.digest
    verify_resource_digest(snapshot)

    changed = snapshot.model_copy(
        update={
            "metadata": snapshot.metadata.model_copy(update={"revision": 2}),
            # An arbitrary old digest is detached from the projection.
            "digest": "sha256:" + ("f" * 64),
        }
    )
    assert calculate_digest(changed) != snapshot.digest
    with pytest.raises(OACValidationError) as exc_info:
        verify_resource_digest(changed)
    assert exc_info.value.reason_code == "ROOT_DIGEST_MISMATCH"


def test_canonical_bytes_are_stable_across_round_trip() -> None:
    snapshot = make_snapshot()
    parsed = parse_resource(snapshot.model_dump_json(by_alias=True))
    assert canonical_bytes(parsed) == canonical_bytes(snapshot)


def test_kind_and_reason_registries_are_closed_for_mvp() -> None:
    assert set(KIND_MODELS) == set(KIND_REGISTRY)
    assert {
        "OrganizationSnapshot",
        "SemanticChangeSet",
        "OrganizationPlan",
        "PlanCertificate",
        "RuntimeBinding",
        "RuntimeLoweringReceipt",
        "ZeroEffectRuntimeBundle",
        "OrgChangeCase",
        "OrganizationalDemand",
        "SourceAdmissionReceipt",
        "OutcomeCertificate",
    } == set(KIND_MODELS)
    assert "UNKNOWN_NOT_PRESERVED" in REASON_CODE_REGISTRY
    assert "CANDIDATE_EDGE_NOT_AUTHORITY" in REASON_CODE_REGISTRY


def test_snapshot_rejects_dangling_authority_like_references() -> None:
    snapshot = make_snapshot()
    bad_node = snapshot.spec.nodes[0].model_copy(update={"owner_role_ref": "urn:role:invented"})
    with pytest.raises(ValidationError):
        OrganizationSnapshotSpec(
            nodes=(bad_node, *snapshot.spec.nodes[1:]),
            roleDefinitions=snapshot.spec.role_definitions,
            principals=snapshot.spec.principals,
            dependencyEdges=snapshot.spec.dependency_edges,
            impactRules=snapshot.spec.impact_rules,
            separationConstraints=snapshot.spec.separation_constraints,
            completeness=snapshot.spec.completeness,
        )


def test_snapshot_owner_must_resolve_to_an_admitted_custodian_role() -> None:
    snapshot = make_snapshot()
    with pytest.raises(ValidationError):
        OrganizationSnapshot(
            metadata=snapshot.metadata.model_copy(update={"owner_ref": "urn:role:invented"}),
            spec=snapshot.spec,
        )

    owner = next(
        role
        for role in snapshot.spec.role_definitions
        if role.role_id == snapshot.metadata.owner_ref
    )
    candidate_owner = owner.model_copy(update={"admission_status": AdmissionStatus.CANDIDATE})
    roles = tuple(
        candidate_owner if role.role_id == owner.role_id else role
        for role in snapshot.spec.role_definitions
    )
    with pytest.raises(ValidationError):
        OrganizationSnapshot(
            metadata=snapshot.metadata,
            spec=snapshot.spec.model_copy(update={"role_definitions": roles}),
        )


def test_cli_validate_digest_compile_verify_and_registries(tmp_path, capfd) -> None:  # type: ignore[no-untyped-def]
    snapshot, change = make_snapshot(), make_change()
    snapshot_path = tmp_path / "snapshot.json"
    change_path = tmp_path / "change.json"
    plan_path = tmp_path / "plan.json"
    certificate_path = tmp_path / "certificate.json"
    snapshot_path.write_text(snapshot.model_dump_json(by_alias=True))
    change_path.write_text(change.model_dump_json(by_alias=True))

    assert main(["validate", str(snapshot_path), "--verify-digest"]) == 0
    assert json.loads(capfd.readouterr().out)["valid"] is True
    assert main(["digest", str(snapshot_path)]) == 0
    assert json.loads(capfd.readouterr().out)["digest"] == snapshot.digest
    assert main(["compile", str(snapshot_path), str(change_path), "-o", str(plan_path)]) == 0
    assert plan_path.exists()
    assert (
        main(
            [
                "verify",
                str(snapshot_path),
                str(change_path),
                str(plan_path),
                "-o",
                str(certificate_path),
            ]
        )
        == 0
    )
    assert json.loads(certificate_path.read_text())["spec"]["verdict"] == "PROVISIONAL"

    for registry in ("kinds", "reasons"):
        assert main(["registry", registry]) == 0
        assert json.loads(capfd.readouterr().out)["apiVersion"] == "oac.dev/v0alpha1"


def test_cli_reports_stable_errors_for_wrong_command_inputs(tmp_path, capfd) -> None:  # type: ignore[no-untyped-def]
    change_path = tmp_path / "change.json"
    change_path.write_text(make_change().model_dump_json(by_alias=True))
    assert main(["compile", str(change_path), str(change_path)]) == 2
    error = json.loads(capfd.readouterr().err)
    assert error["reasonCode"] == "CORE_SCHEMA_INVALID"


def test_manifest_driven_tck_runner(tmp_path, capfd) -> None:  # type: ignore[no-untyped-def]
    from oac.compiler import compile_supplier_change

    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    paths = {}
    for name, resource in (("snapshot", snapshot), ("change", change), ("plan", plan)):
        path = tmp_path / f"{name}.json"
        path.write_text(resource.model_dump_json(by_alias=True))
        paths[name] = str(path)
    broken = snapshot.model_copy(update={"digest": "sha256:" + ("0" * 64)})
    broken_path = tmp_path / "broken.json"
    broken_path.write_text(broken.model_dump_json(by_alias=True))
    manifest = {
        "apiVersion": "oac.dev/tck/v0alpha1",
        "suiteId": "test-suite",
        "claimLimit": "mechanics-only",
        "cases": [
            {
                "id": "valid",
                "operation": "validate",
                "resourceRef": paths["snapshot"],
                "verifyDigest": True,
                "expect": {"outcome": "PASS", "reasonCodes": [], "match": "exact"},
            },
            {
                "id": "bad-digest",
                "operation": "validate",
                "resourceRef": str(broken_path),
                "verifyDigest": True,
                "expect": {
                    "outcome": "ERROR",
                    "reasonCodes": ["ROOT_DIGEST_MISMATCH"],
                    "match": "exact",
                },
            },
            {
                "id": "verify",
                "operation": "verify",
                "snapshotRef": paths["snapshot"],
                "changeRef": paths["change"],
                "planRef": paths["plan"],
                "expect": {
                    "outcome": "PASS",
                    "verdict": "PROVISIONAL",
                    "reasonCodes": ["CANDIDATE_EDGE_NOT_AUTHORITY"],
                    "match": "contains",
                },
            },
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    assert main(["tck", "--manifest", str(manifest_path)]) == 0
    summary = json.loads(capfd.readouterr().out)
    assert summary["passed"] == 3
    assert summary["failed"] == 0


def test_tck_missing_fixture_is_a_structured_kit_error(tmp_path, capfd) -> None:  # type: ignore[no-untyped-def]
    manifest = {
        "apiVersion": "oac.dev/tck/v0alpha1",
        "suiteId": "broken-suite",
        "claimLimit": "mechanics-only",
        "cases": [
            {
                "id": "missing",
                "operation": "validate",
                "resourceRef": str(tmp_path / "does-not-exist.json"),
                "expect": {"outcome": "PASS", "reasonCodes": []},
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    assert main(["tck", "--manifest", str(manifest_path)]) == 2
    error = json.loads(capfd.readouterr().err)
    assert error["reasonCode"] == "TCK_MANIFEST_INVALID"


def test_tck_does_not_misclassify_execution_type_error_as_manifest_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    manifest = {
        "apiVersion": "oac.dev/tck/v0alpha1",
        "suiteId": "diagnostic-boundary",
        "claimLimit": "mechanics-only",
        "cases": [
            {
                "id": "valid-shape",
                "operation": "validate",
                "resourceRef": "unused.json",
                "expect": {"outcome": "PASS", "reasonCodes": []},
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    def implementation_defect(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise TypeError("execution defect")

    monkeypatch.setattr(cli_module, "_run_tck_case", implementation_defect)
    with pytest.raises(TypeError, match="execution defect"):
        cli_module._run_tck(str(manifest_path))


def test_demo_uses_frozen_public_data_fixture(capfd) -> None:  # type: ignore[no-untyped-def]
    assert main(["demo"]) == 0
    result = json.loads(capfd.readouterr().out)
    assert result["caseId"] == "SC-001"
    assert result["verdict"] == "ACCEPT"
    assert result["effectCeiling"] == "zero_effect"


def test_non_json_mappings_missing_digests_and_non_objects_fail() -> None:
    with pytest.raises(OACValidationError) as non_json:
        parse_resource({"kind": "OrganizationSnapshot", "bad": {1, 2}})
    assert non_json.value.reason_code == "NON_I_JSON"

    with pytest.raises(OACValidationError) as malformed:
        parse_resource("{")
    assert malformed.value.reason_code == "CORE_SCHEMA_INVALID"

    with pytest.raises(OACValidationError) as non_object:
        parse_resource("[]")
    assert non_object.value.reason_code == "CORE_SCHEMA_INVALID"

    unsealed = make_snapshot().model_copy(update={"digest": None})
    with pytest.raises(OACValidationError) as missing:
        verify_resource_digest(unsealed)
    assert missing.value.reason_code == "DIGEST_MISSING"


def test_horizontal_change_kind_parses_but_supplier_profile_refuses_it() -> None:
    source = make_change()
    delta = source.spec.deltas[0].model_copy(update={"path": "/renewalTerms"})
    horizontal = seal_resource(
        source.model_copy(
            update={
                "spec": source.spec.model_copy(
                    update={"semantic_type": "customer.contract", "deltas": (delta,)}
                ),
                "digest": None,
            }
        )
    )
    parsed = parse_resource(horizontal.model_dump_json(by_alias=True), verify_digest=True)
    assert parsed.spec.semantic_type == "customer.contract"  # type: ignore[union-attr]
    with pytest.raises(CompilationError) as exc_info:
        compile_supplier_change(make_snapshot(), horizontal)  # type: ignore[arg-type]
    assert exc_info.value.reason_code == "UNSUPPORTED_SEMANTICS"
