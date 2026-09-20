from __future__ import annotations

from itertools import pairwise

import pytest

from orgrebase.certificates import (
    ImpactCertificateVerifier,
    MinimalRebaseCertificateVerifier,
    build_minimal_rebase_certificate,
)
from orgrebase.domain import (
    CoverageBasis,
    DependencyEdge,
    DependencyStrength,
    EdgeStatus,
    ImpactCertificate,
    ImpactClassification,
    IntegrityError,
    MinimalRebaseCertificate,
    SemanticClassification,
)
from orgrebase.fixture import EnterpriseFixture
from orgrebase.impact import ImpactEngine, build_change_set, classify_semantic_delta
from orgrebase.service import OrgRebaseService


def test_semantic_comparison_ignores_wording() -> None:
    assert classify_semantic_delta("2026-09-01", "2026-09-01") == SemanticClassification.NO_SEMANTIC_DELTA
    assert classify_semantic_delta("2026-09-01", "2026-09-15") == SemanticClassification.SEMANTIC_DELTA


def test_preview_proves_exact_expected_boundary(fixture: EnterpriseFixture) -> None:
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)
    by_id = {item.object_id: item for item in preview.results}
    assert preview.state == "READY"
    assert preview.counts == {
        "affected_hard": 2,
        "bounded_unaffected": 2,
        "unknown": 1,
        "skill_requalification": 1,
    }
    assert by_id["work:sales_quote_a"].classification == ImpactClassification.AFFECTED_HARD
    assert by_id["work:sales_quote_a"].proof_path[0].edge_id == "edge:launch-sales"
    assert by_id["work:support_doc_b"].proof_path[0].edge_id == "edge:launch-support"
    assert (
        by_id["work:legal_review_c"].classification
        == ImpactClassification.UNAFFECTED_WITHIN_DECLARED_BOUNDARY
    )
    assert by_id["work:finance_analysis_d"].boundary["graph_revision"] == "graph:canonical@r1"
    assert by_id["work:partner_brief_e"].classification == ImpactClassification.UNKNOWN
    assert by_id["work:partner_brief_e"].proof_path == ()
    assert (
        by_id["skill:enterprise-launch-readiness"].classification
        == ImpactClassification.REQUALIFICATION_REQUIRED
    )


def test_agent_inferred_proposed_edge_is_not_admitted(fixture: EnterpriseFixture) -> None:
    engine = ImpactEngine(fixture)
    assert engine._path("claim:product.launch_date", "work:partner_brief_e") == ()
    preview = engine.preview(build_change_set(fixture))
    partner = next(item for item in preview.results if item.object_id == "work:partner_brief_e")
    assert "complete authoritative DependencyManifest" in partner.missing_evidence
    assert "MANIFEST_PARTIAL" in partner.missing_evidence


def test_zero_depth_search_reports_truncation_not_unaffected(fixture: EnterpriseFixture) -> None:
    engine = ImpactEngine(fixture)
    path, truncated = engine._search(
        fixture.change["object_id"], "work:sales_quote_a", max_depth=0
    )
    assert path == ()
    assert truncated is True


def test_no_semantic_delta_has_no_results_or_target_writes(service: OrgRebaseService) -> None:
    before = service.current_view()
    result = service.no_semantic_delta()
    assert result["preview"].state == "NO_SEMANTIC_DELTA"
    assert result["preview"].results == ()
    assert result["target_writes"] == 0
    assert result["state"] == before


def test_revision_lock_binds_exact_evaluation_scope(fixture: EnterpriseFixture) -> None:
    change_set = build_change_set(fixture)
    lock_a = ImpactEngine(fixture).revision_lock(change_set)
    lock_b = ImpactEngine(fixture).revision_lock(change_set)
    assert lock_a == lock_b
    assert lock_a.change_set_digest == change_set.digest
    assert lock_a.evaluation_scope_digest.startswith("sha256:")


def _edge(
    edge_id: str,
    source_id: str,
    target_id: str,
    *,
    strength: DependencyStrength = DependencyStrength.HARD,
    relation: str = "ASSUMES",
) -> DependencyEdge:
    return DependencyEdge(
        id=edge_id,
        source_id=source_id,
        target_id=target_id,
        relation=relation,
        strength=strength,
        coverage_basis=CoverageBasis.CONTRACT_DECLARED,
        status=EdgeStatus.ADMITTED,
        provenance_refs=("test:counterexample",),
    )


def test_longer_hard_path_outranks_short_informational_path(
    fixture: EnterpriseFixture,
) -> None:
    source = fixture.change["object_id"]
    target = "work:finance_analysis_d"
    graph = fixture.model_copy(
        update={
            "dependencies": (
                _edge(
                    "edge:a-direct-info",
                    source,
                    target,
                    strength=DependencyStrength.INFORMATIONAL,
                ),
                _edge("edge:b-hard-1", source, "claim:intermediate"),
                _edge("edge:b-hard-2", "claim:intermediate", target),
            ),
            "impact_targets": (target,),
        }
    )

    result = ImpactEngine(graph).preview(build_change_set(graph)).results[0]

    assert result.classification == ImpactClassification.AFFECTED_HARD
    assert tuple(step.edge_id for step in result.proof_path) == (
        "edge:b-hard-1",
        "edge:b-hard-2",
    )


def test_traversal_budget_exhaustion_never_becomes_unaffected(
    fixture: EnterpriseFixture,
) -> None:
    source = fixture.change["object_id"]
    target = "work:legal_review_c"
    nodes = (source, *(f"claim:depth-{index}" for index in range(1, 9)), target)
    dependencies = tuple(
        _edge(f"edge:depth-{index + 1}", left, right)
        for index, (left, right) in enumerate(pairwise(nodes))
    )
    graph = fixture.model_copy(
        update={"dependencies": dependencies, "impact_targets": (target,)}
    )

    result = ImpactEngine(graph).preview(build_change_set(graph)).results[0]

    assert result.classification == ImpactClassification.UNKNOWN
    assert result.reason_code == "TRAVERSAL_BUDGET_EXHAUSTED"


def test_short_info_path_cannot_hide_hard_path_beyond_budget(
    fixture: EnterpriseFixture,
) -> None:
    source = fixture.change["object_id"]
    target = "work:finance_analysis_d"
    nodes = (source, *(f"claim:hidden-{index}" for index in range(1, 9)), target)
    dependencies = (
        _edge(
            "edge:visible-info",
            source,
            target,
            strength=DependencyStrength.INFORMATIONAL,
        ),
        *(
            _edge(f"edge:hidden-{index + 1}", left, right)
            for index, (left, right) in enumerate(pairwise(nodes))
        ),
    )
    graph = fixture.model_copy(
        update={"dependencies": dependencies, "impact_targets": (target,)}
    )

    result = ImpactEngine(graph).preview(build_change_set(graph)).results[0]

    assert result.classification == ImpactClassification.UNKNOWN
    assert result.reason_code == "TRAVERSAL_BUDGET_EXHAUSTED"


def test_informational_shortcut_cannot_hide_truncated_hard_path(
    fixture: EnterpriseFixture,
) -> None:
    source = fixture.change["object_id"]
    target = "work:finance_analysis_d"
    intermediates = tuple(f"claim:hidden-star-{index}" for index in range(1, 10))
    hard_nodes = (source, *intermediates, target)
    dependencies = (
        _edge(
            "edge:visible-info",
            source,
            target,
            strength=DependencyStrength.INFORMATIONAL,
            relation="MENTIONS",
        ),
        *(
            _edge(f"edge:hard-{index + 1}", left, right)
            for index, (left, right) in enumerate(pairwise(hard_nodes))
        ),
        *(
            _edge(
                f"edge:star-{index}",
                source,
                node,
                strength=DependencyStrength.INFORMATIONAL,
                relation="MENTIONS",
            )
            for index, node in enumerate(intermediates, start=1)
        ),
    )
    graph = fixture.model_copy(
        update={"dependencies": dependencies, "impact_targets": (target,)}
    )

    result = ImpactEngine(graph).preview(build_change_set(graph)).results[0]

    assert result.classification == ImpactClassification.UNKNOWN
    assert result.reason_code == "TRAVERSAL_BUDGET_EXHAUSTED"


def test_unknown_relation_has_no_implicit_transfer_rule(
    fixture: EnterpriseFixture,
) -> None:
    source = fixture.change["object_id"]
    target = "work:finance_analysis_d"
    graph = fixture.model_copy(
        update={
            "dependencies": (
                _edge("edge:unknown", source, target, relation="TRANSFIGURES"),
            ),
            "impact_targets": (target,),
        }
    )

    result = ImpactEngine(graph).preview(build_change_set(graph)).results[0]

    assert result.classification == ImpactClassification.UNKNOWN
    assert result.reason_code == "RELATION_RULE_UNKNOWN"


def test_skill_unknown_relation_is_not_requalified(fixture: EnterpriseFixture) -> None:
    source = fixture.change["object_id"]
    target = "skill:enterprise-launch-readiness"
    graph = fixture.model_copy(
        update={
            "dependencies": (
                _edge("edge:skill-unknown", source, target, relation="TRANSFIGURES"),
            ),
            "impact_targets": (target,),
        }
    )

    result = ImpactEngine(graph).preview(build_change_set(graph)).results[0]

    assert result.classification == ImpactClassification.UNKNOWN
    assert result.reason_code == "RELATION_RULE_UNKNOWN"


def test_empty_transfer_path_does_not_default_to_hard() -> None:
    with pytest.raises(ValueError, match="empty transfer path"):
        ImpactEngine._transfer_classification(())


def test_reachable_work_item_outside_scope_fails_closed(fixture: EnterpriseFixture) -> None:
    source = fixture.change["object_id"]
    graph = fixture.model_copy(
        update={
            "dependencies": (
                _edge("edge:in-scope", source, "work:sales_quote_a"),
                _edge("edge:hidden-work", source, "work:finance_analysis_d"),
            ),
            "impact_targets": ("work:sales_quote_a",),
        }
    )

    with pytest.raises(IntegrityError, match="REACHABLE_TARGET_OUTSIDE_EVALUATION_SCOPE"):
        ImpactEngine(graph).preview(build_change_set(graph))


def test_out_of_scope_cannot_compile_a_keep_certificate(
    fixture: EnterpriseFixture,
) -> None:
    from orgrebase.certificates import _disposition_for

    with pytest.raises(IntegrityError, match="OUT_OF_SCOPE_CANNOT_AUTHORIZE_KEEP"):
        _disposition_for(ImpactClassification.OUT_OF_SCOPE)


def test_relation_transfer_is_sequential_not_max_edge_strength(
    fixture: EnterpriseFixture,
) -> None:
    source = fixture.change["object_id"]
    target = "work:finance_analysis_d"
    graph = fixture.model_copy(
        update={
            "dependencies": (
                _edge("edge:hard", source, "claim:intermediate"),
                _edge("edge:mentions", "claim:intermediate", target, relation="MENTIONS"),
            ),
            "impact_targets": (target,),
        }
    )

    result = ImpactEngine(graph).preview(build_change_set(graph)).results[0]

    assert result.classification == ImpactClassification.AFFECTED_INFORMATIONAL


def test_duplicate_source_delta_is_explicitly_rejected(
    fixture: EnterpriseFixture,
) -> None:
    change_set = build_change_set(fixture)
    multiple = change_set.model_copy(
        update={"deltas": (change_set.deltas[0], change_set.deltas[0]), "digest": ""}
    )

    with pytest.raises(ValueError, match="ADMITTED_SOURCE_SET_INVALID"):
        ImpactEngine(fixture).preview(multiple)


def test_independent_certificate_verifier_rejects_tamper_and_false_rehash(
    fixture: EnterpriseFixture,
) -> None:
    change_set = build_change_set(fixture)
    certificate = ImpactEngine(fixture).preview(change_set).certificates[0]
    verifier = ImpactCertificateVerifier(fixture)

    assert verifier.verify(certificate.model_dump(mode="json"), change_set)["status"] == "PASS"

    tampered = certificate.model_dump(mode="json")
    tampered["reason_code"] = "FABRICATED"
    with pytest.raises(IntegrityError, match="digest or schema"):
        verifier.verify(tampered, change_set)

    self_consistent_false = ImpactCertificate.model_validate(
        {
            **certificate.model_dump(mode="json"),
            "reason_code": "FABRICATED_BUT_REHASHED",
            "digest": "",
        }
    )
    with pytest.raises(IntegrityError, match="canonical recomputation"):
        verifier.verify(self_consistent_false.model_dump(mode="json"), change_set)


def test_minimal_certificate_requires_exact_impact_certificate_set(
    fixture: EnterpriseFixture,
) -> None:
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)
    incomplete = preview.model_copy(update={"certificates": (), "digest": ""})

    with pytest.raises(IntegrityError, match="exact preview target set"):
        build_minimal_rebase_certificate(change_set, incomplete)


def test_minimal_verifier_fails_closed_for_every_counterfactual_shape(
    fixture: EnterpriseFixture,
) -> None:
    change_set = build_change_set(fixture)
    preview = ImpactEngine(fixture).preview(change_set)
    certificate = build_minimal_rebase_certificate(change_set, preview)
    verifier = MinimalRebaseCertificateVerifier(fixture)

    malformed = certificate.model_dump(mode="json")
    malformed["digest"] = "sha256:" + "0" * 64
    with pytest.raises(IntegrityError, match="digest or schema"):
        verifier.verify(malformed, change_set)

    different_change = build_change_set(fixture, proposed_value="2026-10-01")
    with pytest.raises(IntegrityError, match="another ChangeSet"):
        verifier.verify(certificate.model_dump(mode="json"), different_change)

    missing_target = certificate.model_dump(mode="json")
    missing_target["effects"] = missing_target["effects"][:-1]
    missing_target["digest"] = ""
    rehashed_missing = MinimalRebaseCertificate.model_validate(missing_target)
    with pytest.raises(IntegrityError, match="MINIMALITY_TARGET_SET_MISMATCH"):
        verifier.verify(rehashed_missing.model_dump(mode="json"), change_set)

    wrong_hold = certificate.model_dump(mode="json")
    partner = next(
        item for item in wrong_hold["effects"] if item["target_id"] == "work:partner_brief_e"
    )
    partner["disposition"] = "PRESERVE_WITHIN_BOUNDARY"
    wrong_hold["digest"] = ""
    rehashed_hold = MinimalRebaseCertificate.model_validate(wrong_hold)
    with pytest.raises(IntegrityError, match="MINIMALITY_DISPOSITION_MISMATCH"):
        verifier.verify(rehashed_hold.model_dump(mode="json"), change_set)

    wrong_binding = certificate.model_dump(mode="json")
    wrong_binding["effects"][0]["impact_result_digest"] = "sha256:" + "f" * 64
    wrong_binding["digest"] = ""
    rehashed_binding = MinimalRebaseCertificate.model_validate(wrong_binding)
    with pytest.raises(IntegrityError, match="canonical recomputation"):
        verifier.verify(rehashed_binding.model_dump(mode="json"), change_set)
