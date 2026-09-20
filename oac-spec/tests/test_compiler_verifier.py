from __future__ import annotations

import inspect
from dataclasses import replace
from pathlib import Path

import pytest
from test_core import make_change, make_snapshot

import oac.verifier as verifier_module
from oac.canonical import parse_resource, resource_ref, seal_resource
from oac.compiler import CompilationError, compile_supplier_change
from oac.models import (
    LEGACY_PREDICATE_VERSION,
    AdmissionStatus,
    ApplicabilityResult,
    BoundaryStatus,
    ContextualApplicabilityPredicate,
    DimensionVerdict,
    ImpactState,
    OrganizationPlan,
    OrganizationSnapshot,
    RelationType,
    ScopeSelector,
    SemanticChangeSet,
    TransferPredicate,
    Verdict,
)
from oac.supplier import RequiredOrder, derive_supplier_contract
from oac.verifier import verify_plan

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "profiles" / "supplier-change" / "inputs"


def _codes(certificate: object) -> set[str]:
    return set(certificate.spec.reason_codes)  # type: ignore[attr-defined]


def _contextual_roots(
    case_id: str, *, truncated: bool = False
) -> tuple[OrganizationSnapshot, SemanticChangeSet]:
    snapshot_name = (
        "veracier-proc01-truncated-contextual.snapshot.json"
        if truncated
        else "veracier-proc01-contextual.snapshot.json"
    )
    snapshot = parse_resource((INPUTS / snapshot_name).read_bytes(), verify_digest=True)
    change = parse_resource((INPUTS / f"{case_id}.change.json").read_bytes(), verify_digest=True)
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)
    return snapshot, change


def _split_operations_work(
    plan: OrganizationPlan,
    *,
    bind_prerequisite_unit: bool,
    cover_full_role_matrix: bool = False,
    split_other_reason_refs: tuple[str, ...] | None = None,
) -> OrganizationPlan:
    role_by_instance = {
        role.role_instance_id: role for role in plan.spec.role_instances
    }
    operations_work = next(
        work
        for work in plan.spec.work_units
        if any(
            role_by_instance[ref].role_definition_ref
            == "role:operations-continuity"
            for ref in work.role_instance_refs
        )
    )
    obligation_by_id = {
        obligation.obligation_id: obligation for obligation in plan.spec.obligations
    }
    prerequisite_ref = next(
        obligation.obligation_id
        for obligation in plan.spec.obligations
        if obligation.obligation_type == "continuity-option-selection"
    )
    other_refs = tuple(
        ref for ref in operations_work.obligation_refs if ref != prerequisite_ref
    )

    def evidence_for(refs: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    evidence
                    for ref in refs
                    for evidence in obligation_by_id[ref].required_evidence
                }
            )
        )

    prerequisite_work = operations_work.model_copy(
        update={
            "work_unit_id": f"{operations_work.work_unit_id}:split-prerequisite",
            "obligation_refs": (prerequisite_ref,),
            "evidence_outputs": evidence_for((prerequisite_ref,)),
        }
    )
    other_work = operations_work.model_copy(
        update={
            "work_unit_id": f"{operations_work.work_unit_id}:split-other",
            "obligation_refs": other_refs,
            "evidence_outputs": evidence_for(other_refs),
        }
    )
    work_units = tuple(
        (prerequisite_work, other_work)
        if work.work_unit_id == operations_work.work_unit_id
        else (work,)
        for work in plan.spec.work_units
    )
    flattened_work_units = tuple(item for group in work_units for item in group)
    order_constraints = []
    for edge in plan.spec.happens_before:
        if cover_full_role_matrix:
            predecessor_refs = (
                (prerequisite_work.work_unit_id,)
                if edge.predecessor_ref == operations_work.work_unit_id
                else (edge.predecessor_ref,)
            )
            successor_refs = (
                (prerequisite_work.work_unit_id, other_work.work_unit_id)
                if edge.successor_ref == operations_work.work_unit_id
                else (edge.successor_ref,)
            )
        else:
            predecessor_refs = (
                prerequisite_work.work_unit_id
                if edge.predecessor_ref == operations_work.work_unit_id
                else edge.predecessor_ref,
            )
            successor_refs = (
                (
                    prerequisite_work.work_unit_id
                    if bind_prerequisite_unit
                    else other_work.work_unit_id
                )
                if edge.successor_ref == operations_work.work_unit_id
                else edge.successor_ref,
            )
        order_constraints.extend(
            edge.model_copy(
                update={
                    "predecessor_ref": predecessor_ref,
                    "successor_ref": successor_ref,
                    **(
                        {"reason_refs": split_other_reason_refs}
                        if split_other_reason_refs is not None
                        and edge.successor_ref == operations_work.work_unit_id
                        and successor_ref == other_work.work_unit_id
                        else {}
                    ),
                }
            )
            for predecessor_ref in predecessor_refs
            for successor_ref in successor_refs
        )
    non_removable_refs = tuple(
        sorted(
            {
                *(role.role_instance_id for role in plan.spec.role_instances),
                *(work.work_unit_id for work in flattened_work_units),
            }
        )
    )
    spec = plan.spec.model_copy(
        update={
            "work_units": flattened_work_units,
            "happens_before": tuple(order_constraints),
            "minimality": plan.spec.minimality.model_copy(
                update={"non_removable_refs": non_removable_refs}
            ),
        }
    )
    return seal_resource(plan.model_copy(update={"spec": spec, "digest": None}))


def _split_legacy_dependency_work(
    plan: OrganizationPlan, *, cover_full_endpoint_matrix: bool
) -> OrganizationPlan:
    role_by_instance = {
        role.role_instance_id: role for role in plan.spec.role_instances
    }
    operations_work = next(
        work
        for work in plan.spec.work_units
        if any(
            role_by_instance[ref].role_definition_ref
            == "role:operations-continuity"
            for ref in work.role_instance_refs
        )
    )
    midpoint = len(operations_work.obligation_refs) // 2
    left_refs = operations_work.obligation_refs[:midpoint]
    right_refs = operations_work.obligation_refs[midpoint:]
    obligation_by_id = {
        obligation.obligation_id: obligation for obligation in plan.spec.obligations
    }

    def split_work(suffix: str, refs: tuple[str, ...]) -> object:
        evidence = tuple(
            sorted(
                {
                    item
                    for ref in refs
                    for item in obligation_by_id[ref].required_evidence
                }
            )
        )
        return operations_work.model_copy(
            update={
                "work_unit_id": f"{operations_work.work_unit_id}:{suffix}",
                "obligation_refs": refs,
                "evidence_outputs": evidence,
            }
        )

    left_work = split_work("split-left", left_refs)
    right_work = split_work("split-right", right_refs)
    work_units = tuple(
        item
        for work in plan.spec.work_units
        for item in (
            (left_work, right_work)
            if work.work_unit_id == operations_work.work_unit_id
            else (work,)
        )
    )
    constraints = []
    for edge in plan.spec.happens_before:
        if edge.successor_ref == operations_work.work_unit_id:
            constraints.append(
                edge.model_copy(update={"successor_ref": left_work.work_unit_id})
            )
            if cover_full_endpoint_matrix:
                constraints.append(
                    edge.model_copy(update={"successor_ref": right_work.work_unit_id})
                )
        else:
            constraints.append(edge)
    non_removable_refs = tuple(
        sorted(
            {
                *(role.role_instance_id for role in plan.spec.role_instances),
                *(work.work_unit_id for work in work_units),
            }
        )
    )
    spec = plan.spec.model_copy(
        update={
            "work_units": work_units,
            "happens_before": tuple(constraints),
            "minimality": plan.spec.minimality.model_copy(
                update={"non_removable_refs": non_removable_refs}
            ),
        }
    )
    return seal_resource(plan.model_copy(update={"spec": spec, "digest": None}))


def test_compiler_is_deterministic_and_preserves_candidate_unknown() -> None:
    snapshot = make_snapshot()
    change = make_change()
    first = compile_supplier_change(snapshot, change)
    second = compile_supplier_change(snapshot, change)
    assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)

    component_paths = [
        path for path in first.spec.impact_paths if path.target_ref == "urn:node:component-acme"
    ]
    assert component_paths
    assert {path.state for path in component_paths} == {ImpactState.UNKNOWN}
    assert "urn:role:operations" not in {
        obligation.required_role_ref for obligation in first.spec.obligations
    }
    assert "urn:role:dependency-steward" in {
        obligation.required_role_ref for obligation in first.spec.obligations
    }
    candidate_decision = next(
        item
        for item in first.spec.decisions
        if item.subject_ref == "urn:edge:supplier-component-candidate"
    )
    assert candidate_decision.disposition == "unresolved"


def test_complete_boundary_proves_bounded_non_impact_without_erasing_candidate() -> None:
    snapshot = make_snapshot(boundary=BoundaryStatus.COMPLETE)
    derived = derive_supplier_contract(snapshot, make_change())
    assert any(path.state is ImpactState.UNAFFECTED_PROVEN for path in derived.impact_paths)
    component = [
        path for path in derived.impact_paths if path.target_ref == "urn:node:component-acme"
    ]
    assert component and all(path.state is ImpactState.UNKNOWN for path in component)


def test_edge_transfer_predicate_makes_counterfactual_change_local() -> None:
    snapshot = make_snapshot(boundary=BoundaryStatus.COMPLETE)
    risky = compile_supplier_change(snapshot, make_change(after="bankruptcy_proceedings"))
    healthy = compile_supplier_change(snapshot, make_change(after="operating"))
    assert risky.spec.obligations
    assert not healthy.spec.obligations
    evaluation_result_by_id = {
        evaluation.evaluation_id: evaluation.result
        for evaluation in healthy.spec.applicability_evaluations
    }
    bounded_nonimpact = tuple(
        path
        for path in healthy.spec.impact_paths
        if path.origin == "bounded_non_impact" and path.evaluation_refs
    )
    assert bounded_nonimpact
    assert all(
        evaluation_result_by_id[evaluation_ref] is ApplicabilityResult.FALSE
        for path in bounded_nonimpact
        for evaluation_ref in path.evaluation_refs
    )
    assert healthy.spec.role_instances == ()
    assert healthy.spec.status == "planned"
    assert verify_plan(snapshot, make_change(after="operating"), healthy).spec.verdict is Verdict.ACCEPT


def test_retracted_edges_and_rules_are_excluded_not_promoted_to_unknown() -> None:
    snapshot = make_snapshot(boundary=BoundaryStatus.COMPLETE)
    edges = tuple(
        edge.model_copy(update={"admission_status": AdmissionStatus.RETRACTED})
        if edge.edge_id == "urn:edge:supplier-component-candidate"
        else edge
        for edge in snapshot.spec.dependency_edges
    )
    rules = tuple(
        rule.model_copy(update={"admission_status": AdmissionStatus.RETRACTED})
        for rule in snapshot.spec.impact_rules
    )
    snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(
                    update={"dependency_edges": edges, "impact_rules": rules}
                ),
                "digest": None,
            }
        )
    )
    derived = derive_supplier_contract(snapshot, make_change())
    component = [
        path for path in derived.impact_paths if path.target_ref == "urn:node:component-acme"
    ]
    assert component and all(path.state is ImpactState.UNAFFECTED_PROVEN for path in component)
    assert not any(path.rule_refs for path in derived.impact_paths)

    plan = compile_supplier_change(snapshot, make_change())
    decisions = {
        decision.subject_ref: decision
        for decision in plan.spec.decisions
        if decision.subject_ref
        in {"urn:edge:supplier-component-candidate", "urn:rule:supplier-bankruptcy"}
    }
    assert set(decisions) == {
        "urn:edge:supplier-component-candidate",
        "urn:rule:supplier-bankruptcy",
    }
    assert all(decision.disposition == "excluded" for decision in decisions.values())
    assert all(
        decision.reason_codes == ("RETRACTED_SOURCE_EXCLUDED",)
        for decision in decisions.values()
    )
    assert verify_plan(snapshot, make_change(), plan).spec.verdict is Verdict.ACCEPT


def test_admitted_rule_cannot_upgrade_candidate_target_or_role() -> None:
    snapshot = make_snapshot(boundary=BoundaryStatus.COMPLETE)
    candidate_node = snapshot.spec.nodes[-1].model_copy(
        update={"admission_status": AdmissionStatus.CANDIDATE}
    )
    nodes = (*snapshot.spec.nodes[:-1], candidate_node)
    rule = snapshot.spec.impact_rules[0].model_copy(
        update={
            "target_ref": candidate_node.node_id,
            "required_role_ref": "urn:role:dependency-steward",
        }
    )
    snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(
                    update={"nodes": nodes, "impact_rules": (rule,)}
                ),
                "digest": None,
            }
        )
    )
    derived = derive_supplier_contract(snapshot, make_change())
    rule_path = next(path for path in derived.impact_paths if path.rule_refs == (rule.rule_id,))
    assert rule_path.state is ImpactState.UNKNOWN
    assert rule_path.reason_codes == ("CANDIDATE_INPUT_NOT_AUTHORITY",)
    assert not any(
        obligation.origin == "organizational_rule"
        for obligation in derived.obligations
    )
    plan = compile_supplier_change(snapshot, make_change())
    assert plan.spec.status == "guarded_unresolved"
    assert verify_plan(snapshot, make_change(), plan).spec.verdict is Verdict.PROVISIONAL


def test_candidate_subject_and_complete_boundary_never_prove_authority() -> None:
    snapshot = make_snapshot(boundary=BoundaryStatus.COMPLETE)
    nodes = tuple(
        node.model_copy(update={"admission_status": AdmissionStatus.CANDIDATE})
        if node.node_id == "urn:node:supplier-acme"
        else node
        for node in snapshot.spec.nodes
    )
    candidate_snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(update={"nodes": nodes}),
                "digest": None,
            }
        )
    )
    derived = derive_supplier_contract(candidate_snapshot, make_change())
    seed = next(
        path
        for path in derived.impact_paths
        if path.target_ref == "urn:node:supplier-acme"
        and not path.edge_refs
        and not path.rule_refs
    )
    assert seed.state is ImpactState.UNKNOWN
    assert seed.reason_codes == ("CANDIDATE_INPUT_NOT_AUTHORITY",)
    assert not any(path.state is ImpactState.AFFECTED for path in derived.impact_paths)
    assert not any(
        path.state is ImpactState.UNAFFECTED_PROVEN
        and path.target_ref == "urn:node:supplier-acme"
        for path in derived.impact_paths
    )

    retracted_nodes = tuple(
        node.model_copy(update={"admission_status": AdmissionStatus.RETRACTED})
        if node.node_id == "urn:node:supplier-acme"
        else node
        for node in snapshot.spec.nodes
    )
    retracted_snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(update={"nodes": retracted_nodes}),
                "digest": None,
            }
        )
    )
    try:
        compile_supplier_change(retracted_snapshot, make_change())
    except CompilationError as exc:
        assert exc.reason_code == "RETRACTED_SOURCE_EXCLUDED"
    else:
        raise AssertionError("a retracted change subject must not compile")


def test_valid_guarded_plan_is_provisional_and_byte_repeatable() -> None:
    snapshot = make_snapshot()
    change = make_change()
    plan = compile_supplier_change(snapshot, change)
    first = verify_plan(snapshot, change, plan)
    second = verify_plan(snapshot, change, plan)
    assert first.spec.verdict is Verdict.PROVISIONAL
    assert first.spec.restrictions == ("zero_effect", "discovery_and_evidence_only")
    assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)


def test_verifier_does_not_import_compiler() -> None:
    source = inspect.getsource(verifier_module)
    assert "from .compiler" not in source
    assert "import oac.compiler" not in source


def test_plural_principal_witness_is_accepted() -> None:
    snapshot = make_snapshot()
    change = make_change()
    plan = compile_supplier_change(snapshot, change)
    procurement = next(
        role for role in plan.spec.role_instances if role.role_definition_ref == "urn:role:procurement"
    )
    alternate_id = (
        "urn:principal:buyer-b"
        if procurement.principal_ref == "urn:principal:buyer-a"
        else "urn:principal:buyer-a"
    )
    alternate_role = procurement.model_copy(update={"principal_ref": alternate_id})
    roles = tuple(
        alternate_role if role.role_instance_id == procurement.role_instance_id else role
        for role in plan.spec.role_instances
    )
    decisions = tuple(
        decision.model_copy(
            update={
                "disposition": "included",
                "reason_codes": ("PRINCIPAL_SELECTED_QUALIFIED",),
            }
        )
        if decision.subject_ref == alternate_id
        else decision.model_copy(
            update={
                "disposition": "excluded",
                "reason_codes": ("PRINCIPAL_NOT_SELECTED",),
            }
        )
        if decision.subject_ref == procurement.principal_ref
        else decision
        for decision in plan.spec.decisions
    )
    alternate = seal_resource(
        plan.model_copy(
            update={
                "spec": plan.spec.model_copy(
                    update={"role_instances": roles, "decisions": decisions}
                )
            }
        )
    )
    assert alternate.digest != plan.digest
    assert verify_plan(snapshot, change, alternate).spec.verdict is Verdict.PROVISIONAL


def test_omitted_obligation_is_rejected() -> None:
    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    mutated = seal_resource(
        plan.model_copy(
            update={"spec": plan.spec.model_copy(update={"obligations": plan.spec.obligations[1:]})}
        )
    )
    certificate = verify_plan(snapshot, change, mutated)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "OBLIGATION_SET_MISMATCH" in _codes(certificate)


def test_dangling_obligation_refs_in_role_and_work_are_rejected() -> None:
    snapshot, change = _contextual_roots("SC-008")
    plan = compile_supplier_change(snapshot, change)
    dangling_ref = "urn:oac:obligation:attacker-dangling"
    role = plan.spec.role_instances[0]
    work = next(
        item
        for item in plan.spec.work_units
        if item.accountable_role_instance_ref == role.role_instance_id
    )
    roles = tuple(
        item.model_copy(
            update={"obligation_refs": (*item.obligation_refs, dangling_ref)}
        )
        if item.role_instance_id == role.role_instance_id
        else item
        for item in plan.spec.role_instances
    )
    work_units = tuple(
        item.model_copy(
            update={"obligation_refs": (*item.obligation_refs, dangling_ref)}
        )
        if item.work_unit_id == work.work_unit_id
        else item
        for item in plan.spec.work_units
    )
    forged = seal_resource(
        plan.model_copy(
            update={
                "spec": plan.spec.model_copy(
                    update={"role_instances": roles, "work_units": work_units}
                ),
                "digest": None,
            }
        )
    )

    certificate = verify_plan(snapshot, change, forged)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "OBLIGATION_UNSATISFIED" in _codes(certificate)


def test_candidate_edge_cannot_be_promoted_by_plan() -> None:
    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    target = next(
        path for path in plan.spec.impact_paths if path.target_ref == "urn:node:component-acme"
    )
    promoted = target.model_copy(update={"state": ImpactState.AFFECTED})
    paths = tuple(promoted if path.path_id == target.path_id else path for path in plan.spec.impact_paths)
    mutated = seal_resource(
        plan.model_copy(update={"spec": plan.spec.model_copy(update={"impact_paths": paths})})
    )
    certificate = verify_plan(snapshot, change, mutated)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "IMPACT_PATH_OMITTED" in _codes(certificate)


def test_unknown_erasure_is_rejected() -> None:
    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    mutated = seal_resource(
        plan.model_copy(update={"spec": plan.spec.model_copy(update={"unresolved_refs": ()})})
    )
    certificate = verify_plan(snapshot, change, mutated)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "UNKNOWN_NOT_PRESERVED" in _codes(certificate)


def test_plan_status_must_match_derived_unknowns() -> None:
    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    assert plan.spec.status == "guarded_unresolved"
    mutated = seal_resource(
        plan.model_copy(update={"spec": plan.spec.model_copy(update={"status": "planned"})})
    )
    certificate = verify_plan(snapshot, change, mutated)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "PLAN_STATUS_MISMATCH" in _codes(certificate)


def test_unqualified_principal_is_rejected() -> None:
    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    legal = next(
        role for role in plan.spec.role_instances if role.role_definition_ref == "urn:role:legal"
    )
    broken = legal.model_copy(update={"principal_ref": "urn:principal:operations"})
    roles = tuple(broken if role.role_instance_id == legal.role_instance_id else role for role in plan.spec.role_instances)
    mutated = seal_resource(
        plan.model_copy(update={"spec": plan.spec.model_copy(update={"role_instances": roles})})
    )
    certificate = verify_plan(snapshot, change, mutated)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "QUALIFICATION_INVALID" in _codes(certificate)


def test_separation_of_duties_is_checked_on_principal_identity() -> None:
    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    finance = next(
        role for role in plan.spec.role_instances if role.role_definition_ref == "urn:role:finance"
    )
    legal = next(
        role for role in plan.spec.role_instances if role.role_definition_ref == "urn:role:legal"
    )
    broken = legal.model_copy(update={"principal_ref": finance.principal_ref})
    roles = tuple(broken if role.role_instance_id == legal.role_instance_id else role for role in plan.spec.role_instances)
    mutated = seal_resource(
        plan.model_copy(update={"spec": plan.spec.model_copy(update={"role_instances": roles})})
    )
    certificate = verify_plan(snapshot, change, mutated)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "SEPARATION_OF_DUTIES_VIOLATION" in _codes(certificate)


def test_reversed_required_order_is_rejected() -> None:
    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    edge = next(iter(plan.spec.happens_before))
    reversed_edge = edge.model_copy(
        update={
            "predecessor_ref": edge.successor_ref,
            "successor_ref": edge.predecessor_ref,
        }
    )
    edges = tuple(reversed_edge if item == edge else item for item in plan.spec.happens_before)
    mutated = seal_resource(
        plan.model_copy(update={"spec": plan.spec.model_copy(update={"happens_before": edges})})
    )
    certificate = verify_plan(snapshot, change, mutated)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "ORDER_CONSTRAINT_MISSING" in _codes(certificate)


def test_missing_evidence_is_rejected() -> None:
    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    work = plan.spec.work_units[0]
    broken = work.model_copy(update={"evidence_outputs": ()})
    work_units = (broken, *plan.spec.work_units[1:])
    mutated = seal_resource(
        plan.model_copy(update={"spec": plan.spec.model_copy(update={"work_units": work_units})})
    )
    certificate = verify_plan(snapshot, change, mutated)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "EVIDENCE_DUTY_MISSING" in _codes(certificate)


def test_redundant_role_and_work_unit_are_rejected_as_nonminimal() -> None:
    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    source_role = plan.spec.role_instances[0]
    source_work = plan.spec.work_units[0]
    extra_role = source_role.model_copy(
        update={"role_instance_id": source_role.role_instance_id + ":redundant"}
    )
    extra_work = source_work.model_copy(
        update={
            "work_unit_id": source_work.work_unit_id + ":redundant",
            "role_instance_refs": (extra_role.role_instance_id,),
            "accountable_role_instance_ref": extra_role.role_instance_id,
        }
    )
    mutated = seal_resource(
        plan.model_copy(
            update={
                "spec": plan.spec.model_copy(
                    update={
                        "role_instances": (*plan.spec.role_instances, extra_role),
                        "work_units": (*plan.spec.work_units, extra_work),
                    }
                )
            }
        )
    )
    certificate = verify_plan(snapshot, change, mutated)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "PLAN_NOT_MINIMAL" in _codes(certificate)


def test_frozen_root_and_plan_digest_mutations_are_rejected() -> None:
    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    wrong_ref = plan.spec.snapshot_ref.model_copy(update={"digest": "sha256:" + ("0" * 64)})
    wrong_root = seal_resource(
        plan.model_copy(update={"spec": plan.spec.model_copy(update={"snapshot_ref": wrong_ref})})
    )
    root_certificate = verify_plan(snapshot, change, wrong_root)
    assert "INPUT_ROOT_MISMATCH" in _codes(root_certificate)

    stale_digest = plan.model_copy(
        update={"spec": plan.spec.model_copy(update={"unresolved_refs": ()})}
    )
    digest_certificate = verify_plan(snapshot, change, stale_digest)
    assert digest_certificate.spec.verdict is Verdict.REJECT
    assert "PLAN_DIGEST_MISMATCH" in _codes(digest_certificate)


def test_applicability_evaluation_omission_and_witness_forgery_are_rejected() -> None:
    snapshot, change = _contextual_roots("SC-008")
    plan = compile_supplier_change(snapshot, change)
    referenced = {
        evaluation_ref
        for path in plan.spec.impact_paths
        for evaluation_ref in path.evaluation_refs
    }
    unreferenced = next(
        evaluation
        for evaluation in plan.spec.applicability_evaluations
        if evaluation.evaluation_id not in referenced
    )
    omitted = seal_resource(
        plan.model_copy(
            update={
                "spec": plan.spec.model_copy(
                    update={
                        "applicability_evaluations": tuple(
                            evaluation
                            for evaluation in plan.spec.applicability_evaluations
                            if evaluation.evaluation_id != unreferenced.evaluation_id
                        )
                    }
                ),
                "digest": None,
            }
        )
    )
    omitted_certificate = verify_plan(snapshot, change, omitted)
    assert omitted_certificate.spec.verdict is Verdict.REJECT
    assert "APPLICABILITY_EVALUATION_MISSING" in _codes(omitted_certificate)

    target = plan.spec.applicability_evaluations[0]
    forged_evaluation = target.model_copy(
        update={
            "witness_refs": tuple(
                sorted((*target.witness_refs, "urn:attacker:claim#/fabricated"))
            )
        }
    )
    forged = seal_resource(
        plan.model_copy(
            update={
                "spec": plan.spec.model_copy(
                    update={
                        "applicability_evaluations": tuple(
                            forged_evaluation
                            if evaluation.evaluation_id == target.evaluation_id
                            else evaluation
                            for evaluation in plan.spec.applicability_evaluations
                        )
                    }
                ),
                "digest": None,
            }
        )
    )
    forged_certificate = verify_plan(snapshot, change, forged)
    assert forged_certificate.spec.verdict is Verdict.REJECT
    assert "APPLICABILITY_WITNESS_MISMATCH" in _codes(forged_certificate)


def test_false_evaluation_cannot_be_widened_or_used_as_a_path() -> None:
    snapshot, change = _contextual_roots("SC-008")
    plan = compile_supplier_change(snapshot, change)
    edge_ids = {edge.edge_id for edge in snapshot.spec.dependency_edges}
    false_evaluation = next(
        evaluation
        for evaluation in plan.spec.applicability_evaluations
        if evaluation.result is ApplicabilityResult.FALSE
        and evaluation.source_ref in edge_ids
    )
    widened_evaluation = false_evaluation.model_copy(
        update={"result": ApplicabilityResult.TRUE, "reason_codes": ()}
    )
    widened = seal_resource(
        plan.model_copy(
            update={
                "spec": plan.spec.model_copy(
                    update={
                        "applicability_evaluations": tuple(
                            widened_evaluation
                            if evaluation.evaluation_id == false_evaluation.evaluation_id
                            else evaluation
                            for evaluation in plan.spec.applicability_evaluations
                        )
                    }
                ),
                "digest": None,
            }
        )
    )
    widened_certificate = verify_plan(snapshot, change, widened)
    assert widened_certificate.spec.verdict is Verdict.REJECT
    assert "APPLICABILITY_SCOPE_WIDENED" in _codes(widened_certificate)

    false_edge = next(
        edge
        for edge in snapshot.spec.dependency_edges
        if edge.edge_id == false_evaluation.source_ref
    )
    template = next(path for path in plan.spec.impact_paths if path.edge_refs)
    forged_path = template.model_copy(
        update={
            "path_id": "urn:oac:attacker:false-path",
            "target_ref": false_edge.target_ref,
            "state": ImpactState.AFFECTED,
            "edge_refs": (false_edge.edge_id,),
            "rule_refs": (),
            "evaluation_refs": (false_evaluation.evaluation_id,),
            "duty_refs": (),
            "reason_codes": (),
            "truncated": False,
        }
    )
    forged_plan = seal_resource(
        plan.model_copy(
            update={
                "spec": plan.spec.model_copy(
                    update={"impact_paths": (*plan.spec.impact_paths, forged_path)}
                ),
                "digest": None,
            }
        )
    )
    false_path_certificate = verify_plan(snapshot, change, forged_plan)
    assert false_path_certificate.spec.verdict is Verdict.REJECT
    assert "PREDICATE_FALSE_PATH_INCLUDED" in _codes(false_path_certificate)


def test_unknown_duty_and_prerequisite_order_omissions_have_stable_codes() -> None:
    snapshot, change = _contextual_roots("SC-009")
    plan = compile_supplier_change(snapshot, change)
    duty_path_ids = {
        path.path_id for path in plan.spec.impact_paths if path.duty_refs
    }
    duty_obligation = next(
        obligation
        for obligation in plan.spec.obligations
        if duty_path_ids.intersection(obligation.path_refs)
    )
    omitted = seal_resource(
        plan.model_copy(
            update={
                "spec": plan.spec.model_copy(
                    update={
                        "obligations": tuple(
                            obligation
                            for obligation in plan.spec.obligations
                            if obligation.obligation_id != duty_obligation.obligation_id
                        )
                    }
                ),
                "digest": None,
            }
        )
    )
    duty_certificate = verify_plan(snapshot, change, omitted)
    assert duty_certificate.spec.verdict is Verdict.REJECT
    assert "UNKNOWN_TRANSITION_DUTY_MISSING" in _codes(duty_certificate)

    snapshot, change = _contextual_roots("SC-010", truncated=True)
    plan = compile_supplier_change(snapshot, change)
    assert plan.spec.happens_before
    unordered = seal_resource(
        plan.model_copy(
            update={
                "spec": plan.spec.model_copy(update={"happens_before": ()}),
                "digest": None,
            }
        )
    )
    order_certificate = verify_plan(snapshot, change, unordered)
    assert order_certificate.spec.verdict is Verdict.REJECT
    assert "PREREQUISITE_OBLIGATION_ORDER_MISSING" in _codes(order_certificate)


def test_split_role_work_requires_every_role_wide_dependency_endpoint() -> None:
    snapshot, change = _contextual_roots("SC-008")
    plan = compile_supplier_change(snapshot, change)
    forged = _split_operations_work(plan, bind_prerequisite_unit=False)

    certificate = verify_plan(snapshot, change, forged)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "PREREQUISITE_OBLIGATION_ORDER_MISSING" in _codes(certificate)


def test_split_role_work_still_requires_role_wide_dependency_matrix() -> None:
    snapshot, change = _contextual_roots("SC-008")
    plan = compile_supplier_change(snapshot, change)
    bypass = _split_operations_work(plan, bind_prerequisite_unit=True)

    certificate = verify_plan(snapshot, change, bypass)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "ORDER_CONSTRAINT_MISSING" in _codes(certificate)


def test_split_role_work_accepts_complete_role_wide_dependency_matrix() -> None:
    snapshot, change = _contextual_roots("SC-008")
    plan = compile_supplier_change(snapshot, change)
    derived = derive_supplier_contract(snapshot, change)
    quality_operations = next(
        order
        for order in derived.required_orders
        if order.predecessor_role_ref == "role:quality-qualification"
        and order.successor_role_ref == "role:operations-continuity"
    )
    plural = _split_operations_work(
        plan,
        bind_prerequisite_unit=True,
        cover_full_role_matrix=True,
        split_other_reason_refs=quality_operations.dependency_reason_refs,
    )

    assert len(plural.spec.work_units) == len(plan.spec.work_units) + 1
    continuity_obligation_ref = next(
        obligation.obligation_id
        for obligation in plural.spec.obligations
        if obligation.obligation_type == "continuity-option-selection"
    )
    operations_successors = tuple(
        edge
        for edge in plural.spec.happens_before
        if edge.successor_ref.endswith(("split-prerequisite", "split-other"))
    )
    prerequisite_edge = next(
        edge
        for edge in operations_successors
        if edge.successor_ref.endswith("split-prerequisite")
    )
    split_other_edge = next(
        edge
        for edge in operations_successors
        if edge.successor_ref.endswith("split-other")
    )
    assert continuity_obligation_ref in prerequisite_edge.reason_refs
    assert continuity_obligation_ref not in split_other_edge.reason_refs
    assert split_other_edge.reason_refs == quality_operations.dependency_reason_refs
    assert verify_plan(snapshot, change, plural).spec.verdict is Verdict.ACCEPT


def test_dependency_order_requires_the_full_split_endpoint_matrix() -> None:
    snapshot = parse_resource(
        (INPUTS / "veracier-proc01.snapshot.json").read_bytes(), verify_digest=True
    )
    change = parse_resource(
        (INPUTS / "SC-001.change.json").read_bytes(), verify_digest=True
    )
    witness = parse_resource(
        (
            ROOT
            / "profiles/supplier-change/witnesses/SC-001-witness-a.plan.json"
        ).read_bytes(),
        verify_digest=True,
    )
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)
    assert isinstance(witness, OrganizationPlan)
    bypass = _split_legacy_dependency_work(
        witness, cover_full_endpoint_matrix=False
    )

    certificate = verify_plan(snapshot, change, bypass)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "ORDER_CONSTRAINT_MISSING" in _codes(certificate)


def test_dependency_order_accepts_a_complete_split_endpoint_matrix() -> None:
    snapshot = parse_resource(
        (INPUTS / "veracier-proc01.snapshot.json").read_bytes(), verify_digest=True
    )
    change = parse_resource(
        (INPUTS / "SC-001.change.json").read_bytes(), verify_digest=True
    )
    witness = parse_resource(
        (
            ROOT
            / "profiles/supplier-change/witnesses/SC-001-witness-a.plan.json"
        ).read_bytes(),
        verify_digest=True,
    )
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)
    assert isinstance(witness, OrganizationPlan)
    plural = _split_legacy_dependency_work(
        witness, cover_full_endpoint_matrix=True
    )

    assert len(plural.spec.work_units) == len(witness.spec.work_units) + 1
    assert verify_plan(snapshot, change, plural).spec.verdict is Verdict.ACCEPT


def test_required_role_order_cycle_rejects_even_when_plan_work_graph_is_acyclic() -> None:
    snapshot, change = _contextual_roots("SC-008")
    plan = compile_supplier_change(snapshot, change)
    derived = derive_supplier_contract(snapshot, change)
    forward = next(
        order
        for order in derived.required_orders
        if order.predecessor_role_ref == "role:operations-continuity"
        and order.successor_role_ref == "role:procurement-owner"
    )
    reverse = RequiredOrder(
        predecessor_role_ref=forward.successor_role_ref,
        successor_role_ref=forward.predecessor_role_ref,
        reason_refs=forward.reason_refs,
    )
    cyclic_contract = replace(
        derived, required_orders=(*derived.required_orders, reverse)
    )

    assert not verifier_module._cycle_nodes(
        (edge.predecessor_ref, edge.successor_ref)
        for edge in plan.spec.happens_before
    )
    checks = verifier_module._check_ordering(plan, cyclic_contract)
    cycle_check = next(check for check in checks if check.name == "order_acyclic")
    assert cycle_check.verdict is DimensionVerdict.FAIL
    assert cycle_check.reason_codes == ("ORDER_CYCLE",)


@pytest.mark.parametrize(
    "admission_status",
    [
        AdmissionStatus.ADMITTED,
        AdmissionStatus.CANDIDATE,
        AdmissionStatus.DISPUTED,
    ],
)
def test_false_continuation_does_not_create_depth_truncation_unknown(
    admission_status: AdmissionStatus,
) -> None:
    snapshot = make_snapshot(boundary=BoundaryStatus.COMPLETE)
    edges = tuple(
        edge.model_copy(
            update={
                "admission_status": admission_status,
                "transfer_predicate": TransferPredicate(
                    semanticType="supplier.status",
                    afterValues=("operating",),
                )
            }
        )
        if edge.edge_id == "urn:edge:contract-compliance"
        else edge
        for edge in snapshot.spec.dependency_edges
    )
    snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(
                    update={
                        "dependency_edges": edges,
                        "completeness": snapshot.spec.completeness.model_copy(
                            update={"max_depth": 1}
                        ),
                    }
                ),
                "digest": None,
            }
        )
    )
    derived = derive_supplier_contract(snapshot, make_change())
    contract_path = next(
        path
        for path in derived.impact_paths
        if path.edge_refs == ("urn:edge:supplier-contract",)
    )
    assert contract_path.state is ImpactState.AFFECTED
    assert contract_path.truncated is False
    assert "IMPACT_SEARCH_TRUNCATED" not in contract_path.reason_codes
    compliance_paths = tuple(
        path
        for path in derived.impact_paths
        if path.target_ref == "urn:node:compliance-case-acme"
    )
    assert compliance_paths
    assert {path.state for path in compliance_paths} == {
        ImpactState.UNAFFECTED_PROVEN
    }
    evaluation_by_id = {
        evaluation.evaluation_id: evaluation
        for evaluation in derived.applicability_evaluations
    }
    assert {
        evaluation_by_id[ref].source_ref
        for path in compliance_paths
        for ref in path.evaluation_refs
    } == {"urn:edge:contract-compliance"}


def test_complete_boundary_no_route_nonimpact_proof_needs_no_false_evidence() -> None:
    derived = derive_supplier_contract(
        make_snapshot(boundary=BoundaryStatus.COMPLETE), make_change()
    )
    no_route = next(
        path
        for path in derived.impact_paths
        if path.target_ref == "urn:node:dependency-catalog"
    )
    assert no_route.state is ImpactState.UNAFFECTED_PROVEN
    assert no_route.origin == "bounded_non_impact"
    assert no_route.evaluation_refs == ()


def test_complete_boundary_binds_false_evaluations_from_every_topology_route() -> None:
    snapshot = make_snapshot(boundary=BoundaryStatus.COMPLETE)
    edges = tuple(
        edge.model_copy(
            update={
                "relation_type": RelationType.BUSINESS_DEPENDENCY,
                "transfer_predicate": TransferPredicate(
                    semanticType="supplier.status", afterValues=("operating",)
                ),
            }
        )
        if edge.edge_id == "urn:edge:supplier-compliance-trace"
        else edge.model_copy(
            update={
                "transfer_predicate": TransferPredicate(
                    semanticType="supplier.status", afterValues=("operating",)
                )
            }
        )
        if edge.edge_id == "urn:edge:contract-compliance"
        else edge
        for edge in snapshot.spec.dependency_edges
    )
    snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(update={"dependency_edges": edges}),
                "digest": None,
            }
        )
    )
    derived = derive_supplier_contract(snapshot, make_change())
    bounded = next(
        path
        for path in derived.impact_paths
        if path.target_ref == "urn:node:compliance-case-acme"
        and path.state is ImpactState.UNAFFECTED_PROVEN
    )
    evaluation_by_id = {
        evaluation.evaluation_id: evaluation
        for evaluation in derived.applicability_evaluations
    }
    assert {
        evaluation_by_id[ref].source_ref for ref in bounded.evaluation_refs
    } == {
        "urn:edge:contract-compliance",
        "urn:edge:supplier-compliance-trace",
    }


@pytest.mark.parametrize(
    "admission_status",
    [AdmissionStatus.CANDIDATE, AdmissionStatus.DISPUTED],
)
def test_non_authoritative_continuation_at_depth_is_unknown_without_crossing_limit(
    admission_status: AdmissionStatus,
) -> None:
    snapshot = make_snapshot(boundary=BoundaryStatus.COMPLETE)
    edges = tuple(
        edge.model_copy(update={"admission_status": admission_status})
        if edge.edge_id == "urn:edge:contract-compliance"
        else edge
        for edge in snapshot.spec.dependency_edges
    )
    snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(
                    update={
                        "dependency_edges": edges,
                        "completeness": snapshot.spec.completeness.model_copy(
                            update={"max_depth": 1}
                        ),
                    }
                ),
                "digest": None,
            }
        )
    )

    derived = derive_supplier_contract(snapshot, make_change())
    assert all(len(path.edge_refs) <= 1 for path in derived.impact_paths)
    contract_path = next(
        path
        for path in derived.impact_paths
        if path.edge_refs == ("urn:edge:supplier-contract",)
    )
    assert contract_path.state is ImpactState.UNKNOWN
    assert contract_path.truncated is True
    assert "IMPACT_SEARCH_TRUNCATED" in contract_path.reason_codes
    compliance_paths = tuple(
        path
        for path in derived.impact_paths
        if path.target_ref == "urn:node:compliance-case-acme"
    )
    assert compliance_paths
    assert all(path.state is ImpactState.UNKNOWN for path in compliance_paths)
    assert any(
        path.origin == "gap" and "IMPACT_SEARCH_TRUNCATED" in path.reason_codes
        for path in compliance_paths
    )


def test_depth_truncation_taints_admitted_reachable_nodes_beyond_frontier() -> None:
    snapshot = parse_resource(
        (INPUTS / "veracier-proc01.snapshot.json").read_bytes(), verify_digest=True
    )
    change = parse_resource(
        (INPUTS / "SC-001.change.json").read_bytes(), verify_digest=True
    )
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)
    snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(
                    update={
                        "completeness": snapshot.spec.completeness.model_copy(
                            update={"max_depth": 1}
                        )
                    }
                ),
                "digest": None,
            }
        )
    )

    derived = derive_supplier_contract(snapshot, change)
    for target_ref in ("production:defense", "production:energy"):
        target_paths = tuple(
            path for path in derived.impact_paths if path.target_ref == target_ref
        )
        assert target_paths
        assert all(
            path.state is not ImpactState.UNAFFECTED_PROVEN for path in target_paths
        )
        assert any(
            path.state is ImpactState.UNKNOWN
            and path.origin == "gap"
            and "IMPACT_SEARCH_TRUNCATED" in path.reason_codes
            for path in target_paths
        )


def test_unknown_edge_state_cannot_be_upgraded_by_a_later_true_edge() -> None:
    snapshot = make_snapshot(boundary=BoundaryStatus.COMPLETE)
    first = snapshot.spec.dependency_edges[0]
    contextual_first = first.model_copy(
        update={
            "transfer_predicate": ContextualApplicabilityPredicate(
                predicateVersion="oac.supplier.applicability/v0.2",
                semanticType="supplier.status",
                afterState="known",
                afterValues=("bankruptcy_proceedings",),
                scopeSelector=ScopeSelector(
                    refs=("urn:node:not-observed",),
                    matchMode="all",
                    missingBehavior="unknown",
                ),
                relationTypes=(RelationType.CONTRACTUAL_DEPENDENCY,),
            )
        }
    )
    snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(
                    update={
                        "dependency_edges": (
                            contextual_first,
                            *snapshot.spec.dependency_edges[1:],
                        )
                    }
                ),
                "digest": None,
            }
        )
    )
    derived = derive_supplier_contract(snapshot, make_change())
    compliance_path = next(
        path
        for path in derived.impact_paths
        if path.edge_refs
        == (
            "urn:edge:supplier-contract",
            "urn:edge:contract-compliance",
        )
    )
    assert compliance_path.state is ImpactState.UNKNOWN
    assert len(compliance_path.evaluation_refs) == 2


def test_contextual_wire_cannot_smuggle_the_legacy_predicate_version() -> None:
    snapshot, change = _contextual_roots("SC-008")
    edge = snapshot.spec.dependency_edges[0]
    predicate = edge.transfer_predicate
    assert isinstance(predicate, ContextualApplicabilityPredicate)
    forged_edge = edge.model_copy(
        update={
            "transfer_predicate": predicate.model_copy(
                update={"predicate_version": LEGACY_PREDICATE_VERSION}
            )
        }
    )
    forged_snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(
                    update={
                        "dependency_edges": (
                            forged_edge,
                            *snapshot.spec.dependency_edges[1:],
                        )
                    }
                ),
                "digest": None,
            }
        )
    )
    with pytest.raises(CompilationError) as error:
        compile_supplier_change(forged_snapshot, change)
    assert error.value.reason_code == "PREDICATE_DEFINITION_INVALID"

    valid_plan = compile_supplier_change(snapshot, change)
    rebound = seal_resource(
        valid_plan.model_copy(
            update={
                "spec": valid_plan.spec.model_copy(
                    update={"snapshot_ref": resource_ref(forged_snapshot)}
                ),
                "digest": None,
            }
        )
    )
    certificate = verify_plan(forged_snapshot, change, rebound)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "PREDICATE_DEFINITION_INVALID" in _codes(certificate)


@pytest.mark.parametrize("source_kind", ["rule", "duty"])
def test_relation_types_on_rule_or_duty_are_rejected_as_invalid_definition(
    source_kind: str,
) -> None:
    snapshot, change = _contextual_roots("SC-009")
    if source_kind == "rule":
        source = snapshot.spec.impact_rules[0]
        forged_source = source.model_copy(
            update={
                "applicability": source.applicability.model_copy(  # type: ignore[union-attr]
                    update={"relation_types": (RelationType.BUSINESS_DEPENDENCY,)}
                )
            }
        )
        spec = snapshot.spec.model_copy(
            update={
                "impact_rules": (forged_source, *snapshot.spec.impact_rules[1:])
            }
        )
    else:
        source = snapshot.spec.unknown_transition_duties[0]
        forged_source = source.model_copy(
            update={
                "applicability": source.applicability.model_copy(
                    update={"relation_types": (RelationType.BUSINESS_DEPENDENCY,)}
                )
            }
        )
        spec = snapshot.spec.model_copy(
            update={"unknown_transition_duties": (forged_source,)}
        )
    forged_snapshot = seal_resource(
        snapshot.model_copy(update={"spec": spec, "digest": None})
    )

    with pytest.raises(CompilationError) as error:
        compile_supplier_change(forged_snapshot, change)
    assert error.value.reason_code == "PREDICATE_DEFINITION_INVALID"

    valid_plan = compile_supplier_change(snapshot, change)
    rebound = seal_resource(
        valid_plan.model_copy(
            update={
                "spec": valid_plan.spec.model_copy(
                    update={"snapshot_ref": resource_ref(forged_snapshot)}
                ),
                "digest": None,
            }
        )
    )
    certificate = verify_plan(forged_snapshot, change, rebound)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "PREDICATE_DEFINITION_INVALID" in _codes(certificate)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_predecessor",
        "ambiguous_predecessor",
        "same_role",
        "missing_successor",
    ],
)
def test_contextual_prerequisites_fail_closed_when_order_is_not_representable(
    mutation: str,
) -> None:
    snapshot, change = _contextual_roots("SC-008")
    rules = list(snapshot.spec.impact_rules)
    index = next(
        index
        for index, rule in enumerate(rules)
        if rule.obligation_type == "continuity-option-selection"
    )
    rule = rules[index]
    if mutation == "missing_predecessor":
        rule = rule.model_copy(
            update={"prerequisite_obligation_types": ("not-materialized",)}
        )
    elif mutation == "ambiguous_predecessor":
        rule = rule.model_copy(
            update={"prerequisite_obligation_types": ("assess_dependency_impact",)}
        )
    elif mutation == "same_role":
        rule = rule.model_copy(update={"required_role_ref": "role:quality-qualification"})
    else:
        rule = rule.model_copy(update={"admission_status": AdmissionStatus.CANDIDATE})
    rules[index] = rule
    mutated_snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(update={"impact_rules": tuple(rules)}),
                "digest": None,
            }
        )
    )
    with pytest.raises(CompilationError) as error:
        compile_supplier_change(mutated_snapshot, change)
    assert error.value.reason_code == "PREREQUISITE_OBLIGATION_ORDER_MISSING"

    valid_plan = compile_supplier_change(snapshot, change)
    rebound = seal_resource(
        valid_plan.model_copy(
            update={
                "spec": valid_plan.spec.model_copy(
                    update={"snapshot_ref": resource_ref(mutated_snapshot)}
                ),
                "digest": None,
            }
        )
    )
    certificate = verify_plan(mutated_snapshot, change, rebound)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "PREREQUISITE_OBLIGATION_ORDER_MISSING" in _codes(certificate)


def test_retracted_endpoint_is_excluded_without_a_discovery_obligation() -> None:
    snapshot = make_snapshot(boundary=BoundaryStatus.COMPLETE)
    nodes = tuple(
        node.model_copy(update={"admission_status": AdmissionStatus.RETRACTED})
        if node.node_id == "urn:node:contract-acme"
        else node
        for node in snapshot.spec.nodes
    )
    snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(update={"nodes": nodes}),
                "digest": None,
            }
        )
    )
    derived = derive_supplier_contract(snapshot, make_change())
    contract_paths = tuple(
        path
        for path in derived.impact_paths
        if path.target_ref == "urn:node:contract-acme"
    )
    assert contract_paths
    assert {path.state for path in contract_paths} == {ImpactState.OUT_OF_DECLARED_SCOPE}
    contract_path_ids = {path.path_id for path in contract_paths}
    assert not any(
        contract_path_ids.intersection(obligation.path_refs)
        for obligation in derived.obligations
    )


@pytest.mark.parametrize(
    "owner_status",
    [
        None,
        AdmissionStatus.CANDIDATE,
        AdmissionStatus.DISPUTED,
        AdmissionStatus.RETRACTED,
    ],
)
def test_affected_dependency_target_requires_an_admitted_owner_role(
    owner_status: AdmissionStatus | None,
) -> None:
    snapshot, change = make_snapshot(), make_change()
    if owner_status is None:
        nodes = tuple(
            node.model_copy(update={"owner_role_ref": None})
            if node.node_id == "urn:node:contract-acme"
            else node
            for node in snapshot.spec.nodes
        )
        spec = snapshot.spec.model_copy(update={"nodes": nodes})
    else:
        roles = tuple(
            role.model_copy(update={"admission_status": owner_status})
            if role.role_id == "urn:role:legal"
            else role
            for role in snapshot.spec.role_definitions
        )
        spec = snapshot.spec.model_copy(update={"role_definitions": roles})
    forged_snapshot = seal_resource(
        snapshot.model_copy(update={"spec": spec, "digest": None})
    )

    with pytest.raises(CompilationError) as error:
        compile_supplier_change(forged_snapshot, change)
    assert error.value.reason_code == "OBLIGATION_UNSATISFIED"

    valid_plan = compile_supplier_change(snapshot, change)
    rebound = seal_resource(
        valid_plan.model_copy(
            update={
                "spec": valid_plan.spec.model_copy(
                    update={"snapshot_ref": resource_ref(forged_snapshot)}
                ),
                "digest": None,
            }
        )
    )
    certificate = verify_plan(forged_snapshot, change, rebound)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "OBLIGATION_UNSATISFIED" in _codes(certificate)


def test_affected_seed_with_zero_closure_obligations_fails_closed() -> None:
    snapshot, change = make_snapshot(boundary=BoundaryStatus.COMPLETE), make_change()
    forged_snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(
                    update={"dependency_edges": (), "impact_rules": ()}
                ),
                "digest": None,
            }
        )
    )

    with pytest.raises(CompilationError) as error:
        compile_supplier_change(forged_snapshot, change)
    assert error.value.reason_code == "OBLIGATION_UNSATISFIED"

    valid_plan = compile_supplier_change(snapshot, change)
    rebound = seal_resource(
        valid_plan.model_copy(
            update={
                "spec": valid_plan.spec.model_copy(
                    update={"snapshot_ref": resource_ref(forged_snapshot)}
                ),
                "digest": None,
            }
        )
    )
    certificate = verify_plan(forged_snapshot, change, rebound)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "OBLIGATION_UNSATISFIED" in _codes(certificate)


def test_false_evaluation_ref_on_an_existing_path_is_rejected_with_specific_code() -> None:
    snapshot, change = _contextual_roots("SC-008")
    plan = compile_supplier_change(snapshot, change)
    false_evaluation = next(
        evaluation
        for evaluation in plan.spec.applicability_evaluations
        if evaluation.result is ApplicabilityResult.FALSE
    )
    target = next(path for path in plan.spec.impact_paths if path.evaluation_refs)
    altered = target.model_copy(
        update={
            "evaluation_refs": tuple(
                dict.fromkeys((*target.evaluation_refs, false_evaluation.evaluation_id))
            )
        }
    )
    paths = tuple(
        altered if path.path_id == target.path_id else path
        for path in plan.spec.impact_paths
    )
    mutated = seal_resource(
        plan.model_copy(
            update={
                "spec": plan.spec.model_copy(update={"impact_paths": paths}),
                "digest": None,
            }
        )
    )
    certificate = verify_plan(snapshot, change, mutated)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "PREDICATE_FALSE_PATH_INCLUDED" in _codes(certificate)


def test_legacy_compatibility_projection_preserves_exact_path_multiplicity() -> None:
    snapshot = parse_resource(
        (INPUTS / "veracier-proc01.snapshot.json").read_bytes(), verify_digest=True
    )
    change = parse_resource((INPUTS / "SC-001.change.json").read_bytes(), verify_digest=True)
    witness = parse_resource(
        (
            ROOT
            / "profiles/supplier-change/witnesses/SC-001-witness-a.plan.json"
        ).read_bytes(),
        verify_digest=True,
    )
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)
    path = next(item for item in witness.spec.impact_paths if item.state is ImpactState.AFFECTED)  # type: ignore[union-attr]
    duplicate = path.model_copy(update={"path_id": f"{path.path_id}:duplicate"})
    mutated = seal_resource(
        witness.model_copy(  # type: ignore[union-attr]
            update={
                "spec": witness.spec.model_copy(  # type: ignore[union-attr]
                    update={"impact_paths": (*witness.spec.impact_paths, duplicate)}  # type: ignore[union-attr]
                ),
                "digest": None,
            }
        )
    )
    certificate = verify_plan(snapshot, change, mutated)  # type: ignore[arg-type]
    assert certificate.spec.verdict is Verdict.REJECT
    assert "IMPACT_PATH_OMITTED" in _codes(certificate)


def test_legacy_compatibility_projection_rejects_semantic_obligation_duplicates() -> None:
    snapshot = parse_resource(
        (INPUTS / "veracier-proc01.snapshot.json").read_bytes(), verify_digest=True
    )
    change = parse_resource((INPUTS / "SC-001.change.json").read_bytes(), verify_digest=True)
    witness = parse_resource(
        (
            ROOT
            / "profiles/supplier-change/witnesses/SC-001-witness-a.plan.json"
        ).read_bytes(),
        verify_digest=True,
    )
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)
    obligation = witness.spec.obligations[0]  # type: ignore[union-attr]
    duplicate_obligation = obligation.model_copy(
        update={"obligation_id": f"{obligation.obligation_id}:duplicate"}
    )
    role = next(
        item
        for item in witness.spec.role_instances  # type: ignore[union-attr]
        if obligation.obligation_id in item.obligation_refs
    )
    duplicate_role = role.model_copy(
        update={
            "role_instance_id": f"{role.role_instance_id}:duplicate",
            "obligation_refs": (duplicate_obligation.obligation_id,),
        }
    )
    work = next(
        item
        for item in witness.spec.work_units  # type: ignore[union-attr]
        if obligation.obligation_id in item.obligation_refs
    )
    duplicate_work = work.model_copy(
        update={
            "work_unit_id": f"{work.work_unit_id}:duplicate",
            "role_instance_refs": (duplicate_role.role_instance_id,),
            "accountable_role_instance_ref": duplicate_role.role_instance_id,
            "obligation_refs": (duplicate_obligation.obligation_id,),
        }
    )
    spec = witness.spec.model_copy(  # type: ignore[union-attr]
        update={
            "obligations": (*witness.spec.obligations, duplicate_obligation),  # type: ignore[union-attr]
            "role_instances": (*witness.spec.role_instances, duplicate_role),  # type: ignore[union-attr]
            "work_units": (*witness.spec.work_units, duplicate_work),  # type: ignore[union-attr]
            "minimality": witness.spec.minimality.model_copy(  # type: ignore[union-attr]
                update={
                    "non_removable_refs": tuple(
                        sorted(
                            (
                                *witness.spec.minimality.non_removable_refs,  # type: ignore[union-attr]
                                duplicate_role.role_instance_id,
                                duplicate_work.work_unit_id,
                            )
                        )
                    )
                }
            ),
        }
    )
    mutated = seal_resource(
        witness.model_copy(update={"spec": spec, "digest": None})  # type: ignore[union-attr]
    )
    certificate = verify_plan(snapshot, change, mutated)  # type: ignore[arg-type]
    assert certificate.spec.verdict is Verdict.REJECT
    assert "OBLIGATION_SET_MISMATCH" in _codes(certificate)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("edge", "IMPACT_PATH_OMITTED"),
        ("rule", "IMPACT_PATH_OMITTED"),
        ("path", "OBLIGATION_SET_MISMATCH"),
        ("obligation", "OBLIGATION_UNSATISFIED"),
        ("work", "ORDER_CONSTRAINT_INVALID"),
        ("role", "OBLIGATION_UNSATISFIED"),
    ],
)
def test_legacy_dangling_plan_refs_fail_closed(
    mutation: str, expected_code: str
) -> None:
    snapshot = parse_resource(
        (INPUTS / "veracier-proc01.snapshot.json").read_bytes(), verify_digest=True
    )
    change = parse_resource(
        (INPUTS / "SC-001.change.json").read_bytes(), verify_digest=True
    )
    witness = parse_resource(
        (
            ROOT
            / "profiles/supplier-change/witnesses/SC-001-witness-a.plan.json"
        ).read_bytes(),
        verify_digest=True,
    )
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)
    assert isinstance(witness, OrganizationPlan)

    spec = witness.spec
    if mutation in {"edge", "rule"}:
        base_path = spec.impact_paths[0]
        attack_path = base_path.model_copy(
            update={
                "path_id": f"urn:attack:path:{mutation}",
                "edge_refs": ("urn:attack:edge:missing",)
                if mutation == "edge"
                else (),
                "rule_refs": ("urn:attack:rule:missing",)
                if mutation == "rule"
                else (),
                "origin": "rule" if mutation == "rule" else "dependency",
            }
        )
        spec = spec.model_copy(update={"impact_paths": (*spec.impact_paths, attack_path)})
    elif mutation == "path":
        obligation = spec.obligations[0]
        forged_obligation = obligation.model_copy(
            update={"path_refs": ("urn:attack:path:missing",)}
        )
        spec = spec.model_copy(
            update={
                "obligations": tuple(
                    forged_obligation
                    if item.obligation_id == obligation.obligation_id
                    else item
                    for item in spec.obligations
                )
            }
        )
    elif mutation == "obligation":
        dangling_ref = "urn:attack:obligation:missing"
        role = spec.role_instances[0]
        work = next(
            item
            for item in spec.work_units
            if item.accountable_role_instance_ref == role.role_instance_id
        )
        spec = spec.model_copy(
            update={
                "role_instances": tuple(
                    item.model_copy(
                        update={
                            "obligation_refs": (*item.obligation_refs, dangling_ref)
                        }
                    )
                    if item.role_instance_id == role.role_instance_id
                    else item
                    for item in spec.role_instances
                ),
                "work_units": tuple(
                    item.model_copy(
                        update={
                            "obligation_refs": (*item.obligation_refs, dangling_ref)
                        }
                    )
                    if item.work_unit_id == work.work_unit_id
                    else item
                    for item in spec.work_units
                ),
            }
        )
    elif mutation == "work":
        order = spec.happens_before[0]
        forged_order = order.model_copy(
            update={"predecessor_ref": "urn:attack:work:missing"}
        )
        spec = spec.model_copy(
            update={
                "happens_before": tuple(
                    forged_order if item == order else item
                    for item in spec.happens_before
                )
            }
        )
    else:
        work = spec.work_units[0]
        forged_work = work.model_copy(
            update={
                "role_instance_refs": (
                    *work.role_instance_refs,
                    "urn:attack:role-instance:missing",
                )
            }
        )
        spec = spec.model_copy(
            update={
                "work_units": tuple(
                    forged_work if item.work_unit_id == work.work_unit_id else item
                    for item in spec.work_units
                )
            }
        )

    forged = seal_resource(
        witness.model_copy(update={"spec": spec, "digest": None})
    )
    parsed = parse_resource(forged.model_dump_json(by_alias=True), verify_digest=True)
    assert isinstance(parsed, OrganizationPlan)
    certificate = verify_plan(snapshot, change, parsed)
    assert certificate.spec.verdict is Verdict.REJECT
    assert expected_code in _codes(certificate)


def test_legacy_compatibility_rejects_forged_hash_derived_ids() -> None:
    snapshot = parse_resource(
        (INPUTS / "veracier-proc01.snapshot.json").read_bytes(), verify_digest=True
    )
    change = parse_resource((INPUTS / "SC-001.change.json").read_bytes(), verify_digest=True)
    witness = parse_resource(
        (
            ROOT
            / "profiles/supplier-change/witnesses/SC-001-witness-a.plan.json"
        ).read_bytes(),
        verify_digest=True,
    )
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)

    referenced_path_ids = {
        path_ref
        for obligation in witness.spec.obligations  # type: ignore[union-attr]
        for path_ref in obligation.path_refs
    }
    referenced_path_ids.update(witness.spec.unresolved_refs)  # type: ignore[union-attr]
    referenced_path_ids.update(
        reason_ref
        for order in witness.spec.happens_before  # type: ignore[union-attr]
        for reason_ref in order.reason_refs
    )
    path = next(
        item
        for item in witness.spec.impact_paths  # type: ignore[union-attr]
        if item.path_id not in referenced_path_ids
    )
    forged_path = path.model_copy(update={"path_id": f"{path.path_id}:forged"})
    forged_paths = tuple(
        forged_path if item.path_id == path.path_id else item
        for item in witness.spec.impact_paths  # type: ignore[union-attr]
    )
    path_plan = seal_resource(
        witness.model_copy(  # type: ignore[union-attr]
            update={
                "spec": witness.spec.model_copy(update={"impact_paths": forged_paths}),  # type: ignore[union-attr]
                "digest": None,
            }
        )
    )
    path_certificate = verify_plan(snapshot, change, path_plan)  # type: ignore[arg-type]
    assert path_certificate.spec.verdict is Verdict.REJECT
    assert "IMPACT_PATH_OMITTED" in _codes(path_certificate)

    obligation = witness.spec.obligations[0]  # type: ignore[union-attr]
    forged_obligation = obligation.model_copy(
        update={"obligation_id": f"{obligation.obligation_id}:forged"}
    )
    obligations = tuple(
        forged_obligation if item.obligation_id == obligation.obligation_id else item
        for item in witness.spec.obligations  # type: ignore[union-attr]
    )
    roles = tuple(
        item.model_copy(
            update={
                "obligation_refs": tuple(
                    forged_obligation.obligation_id
                    if ref == obligation.obligation_id
                    else ref
                    for ref in item.obligation_refs
                )
            }
        )
        for item in witness.spec.role_instances  # type: ignore[union-attr]
    )
    work_units = tuple(
        item.model_copy(
            update={
                "obligation_refs": tuple(
                    forged_obligation.obligation_id
                    if ref == obligation.obligation_id
                    else ref
                    for ref in item.obligation_refs
                )
            }
        )
        for item in witness.spec.work_units  # type: ignore[union-attr]
    )
    obligation_plan = seal_resource(
        witness.model_copy(  # type: ignore[union-attr]
            update={
                "spec": witness.spec.model_copy(  # type: ignore[union-attr]
                    update={
                        "obligations": obligations,
                        "role_instances": roles,
                        "work_units": work_units,
                    }
                ),
                "digest": None,
            }
        )
    )
    obligation_certificate = verify_plan(snapshot, change, obligation_plan)  # type: ignore[arg-type]
    assert obligation_certificate.spec.verdict is Verdict.REJECT
    assert "OBLIGATION_SET_MISMATCH" in _codes(obligation_certificate)


def test_candidate_bounded_nonimpact_binds_all_false_source_evaluations() -> None:
    snapshot, change = _contextual_roots("SC-008")
    derived = derive_supplier_contract(snapshot, change)
    evaluation_by_id = {
        evaluation.evaluation_id: evaluation
        for evaluation in derived.applicability_evaluations
    }
    bounded = next(
        path
        for path in derived.impact_paths
        if path.target_ref == "control:nuclear-substitution"
        and path.state is ImpactState.UNAFFECTED_PROVEN
    )
    assert bounded.evaluation_refs
    assert all(
        evaluation_by_id[evaluation_ref].result is ApplicabilityResult.FALSE
        for evaluation_ref in bounded.evaluation_refs
    )


def test_candidate_root_cannot_establish_candidate_target_false_cut() -> None:
    snapshot = make_snapshot(boundary=BoundaryStatus.COMPLETE)
    nodes = tuple(
        node.model_copy(update={"admission_status": AdmissionStatus.CANDIDATE})
        if node.node_id in {"urn:node:supplier-acme", "urn:node:contract-acme"}
        else node
        for node in snapshot.spec.nodes
    )
    snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(update={"nodes": nodes}),
                "digest": None,
            }
        )
    )

    derived = derive_supplier_contract(snapshot, make_change(after="operating"))
    contract_paths = tuple(
        path
        for path in derived.impact_paths
        if path.target_ref == "urn:node:contract-acme"
    )
    assert contract_paths
    assert {path.state for path in contract_paths} == {ImpactState.UNKNOWN}


def test_admitted_root_false_edge_can_close_a_candidate_target_route() -> None:
    snapshot = make_snapshot(boundary=BoundaryStatus.COMPLETE)
    nodes = tuple(
        node.model_copy(update={"admission_status": AdmissionStatus.CANDIDATE})
        if node.node_id == "urn:node:contract-acme"
        else node
        for node in snapshot.spec.nodes
    )
    snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(update={"nodes": nodes}),
                "digest": None,
            }
        )
    )

    derived = derive_supplier_contract(snapshot, make_change(after="operating"))
    contract = next(
        path
        for path in derived.impact_paths
        if path.target_ref == "urn:node:contract-acme"
    )
    evaluation_by_id = {
        evaluation.evaluation_id: evaluation
        for evaluation in derived.applicability_evaluations
    }
    assert contract.state is ImpactState.UNAFFECTED_PROVEN
    assert contract.evaluation_refs
    assert {
        evaluation_by_id[evaluation_ref].source_ref
        for evaluation_ref in contract.evaluation_refs
    } == {"urn:edge:supplier-contract"}


def test_upstream_admitted_false_cut_excludes_downstream_candidate_source() -> None:
    snapshot = make_snapshot(boundary=BoundaryStatus.COMPLETE)
    nodes = tuple(
        node.model_copy(update={"admission_status": AdmissionStatus.CANDIDATE})
        if node.node_id
        in {"urn:node:contract-acme", "urn:node:compliance-case-acme"}
        else node
        for node in snapshot.spec.nodes
    )
    snapshot = seal_resource(
        snapshot.model_copy(
            update={
                "spec": snapshot.spec.model_copy(update={"nodes": nodes}),
                "digest": None,
            }
        )
    )

    derived = derive_supplier_contract(snapshot, make_change(after="operating"))
    compliance = next(
        path
        for path in derived.impact_paths
        if path.target_ref == "urn:node:compliance-case-acme"
    )
    evaluation_by_id = {
        evaluation.evaluation_id: evaluation
        for evaluation in derived.applicability_evaluations
    }
    assert compliance.state is ImpactState.UNAFFECTED_PROVEN
    assert {
        evaluation_by_id[evaluation_ref].source_ref
        for evaluation_ref in compliance.evaluation_refs
    } == {"urn:edge:supplier-contract"}
