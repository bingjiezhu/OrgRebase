"""Executable rule erasures checked against fixed gold and a small independent oracle.

Only an in-memory copy of one product method is changed. A mutant crash is a
test failure, not a kill. Each case first passes with the original method,
observes the intended wrong result, and proves the same gold assertion rejects
that result. The original source and the existing oracle are never rewritten.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orgrebase.domain import DependencyRequirementSlot, IntegrityError
from orgrebase.fixture import EnterpriseFixture
from orgrebase.impact import ImpactEngine, build_change_set
from tests.test_impact_dag import _edge, _enumerate_paths


@dataclass(frozen=True)
class Mutation:
    id: str
    method: str
    before: str
    after: str
    rule: str
    owner: type = ImpactEngine


def _compile_mutant(mutation: Mutation) -> tuple[Any, dict[str, str]]:
    original = textwrap.dedent(inspect.getsource(getattr(mutation.owner, mutation.method)))
    assert original.count(mutation.before) == 1, "mutation site changed; review the rule and gold"
    changed = original.replace(mutation.before, mutation.after, 1)
    namespace = dict(sys.modules[mutation.owner.__module__].__dict__)
    exec(compile(changed, f"<invariant-mutant:{mutation.id}>", "exec"), namespace)
    return namespace[mutation.method], {
        "original_method_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "mutant_method_sha256": hashlib.sha256(changed.encode()).hexdigest(),
    }


def _assert_gold(actual: Any, expected: Any) -> None:
    assert actual == expected, f"INVARIANT_GOLD_MISMATCH: {actual!r} != {expected!r}"


def _kill(monkeypatch, request, mutation, observe, expected, *, oracle="PREDECLARED_MANUAL_GOLD"):
    source_path = Path(inspect.getfile(mutation.owner))
    disk_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    _assert_gold(observe(), expected)
    mutant, hashes = _compile_mutant(mutation)
    with monkeypatch.context() as isolated:
        isolated.setattr(mutation.owner, mutation.method, mutant)
        actual = observe()  # An unrelated exception must fail the harness.
        if isinstance(expected, dict) and "error" in expected:
            assert actual == {"accepted": True}, "another rejection still blocked this mutant"
        assert actual != expected, f"SURVIVING_MUTANT:{mutation.id}"
        with pytest.raises(AssertionError, match="INVARIANT_GOLD_MISMATCH"):
            _assert_gold(actual, expected)
    _assert_gold(observe(), expected)
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == disk_digest
    request.node.user_properties.append(("critical_mutation", json.dumps({
        "id": mutation.id,
        "rule": mutation.rule,
        "trigger": request.node.nodeid,
        "implementation": f"{mutation.owner.__module__}.{mutation.owner.__name__}.{mutation.method}",
        "source_path": str(source_path.relative_to(Path(__file__).resolve().parents[2])),
        "source_file_sha256": disk_digest,
        "source_file_unchanged": True,
        "erased_source": mutation.before,
        "replacement_source": mutation.after,
        "baseline": "PASS",
        "mutant_observed": actual,
        "gold": expected,
        "oracle": oracle,
        "killer": request.node.nodeid,
        "result": "KILLED_BY_GOLD_ASSERTION",
        "restored_baseline": "PASS",
        **hashes,
    }, ensure_ascii=False)))


def _path_gold(indices, classification, truncated=False):
    return {"path": [f"edge:{index:05d}" for index in indices],
            "classification": classification, "truncated": truncated}


# These small counterexamples and answers are fixed independently of the
# optimized transfer/state/ranking code. The existing exhaustive DFS supplies
# a second implementation for the selected path and the depth boundary.
PATH_CASES = (
    (Mutation("P01", "_transfer_classification", "return ImpactClassification.UNKNOWN",
              "return ImpactClassification.AFFECTED_INFORMATIONAL", "Unknown relation cannot become weak evidence"),
     (_edge(0, "s", "t", relation="UNSPECIFIED"),), 8, _path_gold((0,), "UNKNOWN")),
    (Mutation("P02", "_transfer_classification", "propagated_rank = min(", "propagated_rank = max(",
              "A HARD prefix cannot erase an INFORMATIONAL edge"),
     (_edge(0, "s", "a"), _edge(1, "a", "t", strength="INFORMATIONAL")), 8,
     _path_gold((0, 1), "AFFECTED_INFORMATIONAL")),
    (Mutation("P03", "_transfer_classification", "STRENGTH_RANK[ceiling],",
              "STRENGTH_RANK[DependencyStrength.HARD],", "MENTIONS imposes an informational ceiling"),
     (_edge(0, "s", "t", relation="MENTIONS"),), 8, _path_gold((0,), "AFFECTED_INFORMATIONAL")),
    (Mutation("P04", "_transfer_classification", "            propagated_rank,",
              "            STRENGTH_RANK[DependencyStrength.HARD],", "A later HARD edge cannot strengthen a REVIEW prefix"),
     (_edge(0, "s", "a", strength="REVIEW"), _edge(1, "a", "t")), 8,
     _path_gold((0, 1), "AFFECTED_REVIEW")),
    (Mutation("P05", "_path_rank", "-CLASSIFICATION_RANK[classification],", "0,",
              "A longer HARD witness outranks a short weak witness"),
     (_edge(0, "s", "t", strength="INFORMATIONAL"), _edge(1, "s", "a"), _edge(2, "a", "t")),
     8, _path_gold((1, 2), "AFFECTED_HARD")),
    (Mutation("P06", "_path_rank", "-CLASSIFICATION_RANK[classification],",
              "-(0 if classification == ImpactClassification.UNKNOWN else CLASSIFICATION_RANK[classification]),",
              "UNKNOWN outranks REVIEW without becoming proven HARD"),
     (_edge(0, "s", "t", strength="REVIEW"), _edge(1, "s", "a", relation="UNSPECIFIED"), _edge(2, "a", "t")),
     8, _path_gold((1, 2), "UNKNOWN")),
    (Mutation("P07", "_path_rank", "-CLASSIFICATION_RANK[classification],",
              "-(5 if classification == ImpactClassification.UNKNOWN else CLASSIFICATION_RANK[classification]),",
              "A complete HARD witness outranks an alternate UNKNOWN path"),
     (_edge(0, "s", "t", relation="UNSPECIFIED"), _edge(1, "s", "a"), _edge(2, "a", "t")),
     8, _path_gold((1, 2), "AFFECTED_HARD")),
    (Mutation("P08", "_path_rank", "len(edges),", "-len(edges),", "Equal severity chooses the shortest witness"),
     (_edge(0, "s", "a"), _edge(1, "a", "t"), _edge(2, "s", "t")),
     8, _path_gold((2,), "AFFECTED_HARD")),
    (Mutation("P09", "_search", "if prior is None or tuple(item.id for item in candidate) < tuple(",
              "if prior is None or tuple(item.id for item in candidate) > tuple(", "Equivalent prefixes retain the lexical witness"),
     (_edge(3, "s", "a"), _edge(4, "a", "m"), _edge(1, "s", "b"), _edge(2, "b", "m"), _edge(5, "m", "t")),
     8, _path_gold((1, 2, 5), "AFFECTED_HARD")),
    (Mutation("P10", "_search", "key = (edge.target_id, self._transfer_classification(candidate))",
              "key = (edge.target_id, ImpactClassification.AFFECTED_HARD)", "Do not merge distinct prefix transfer states"),
     (_edge(0, "s", "a"), _edge(1, "a", "m"), _edge(2, "s", "b", relation="UNSPECIFIED"),
      _edge(3, "b", "m"), _edge(4, "m", "t", relation="MENTIONS")),
     8, _path_gold((2, 3, 4), "UNKNOWN")),
    (Mutation("P11", "_search", "truncated = any(self._adjacency.get(node) for node, _ in states)",
              "truncated = False", "A depth frontier cannot be reported as a complete search"),
     (_edge(0, "s", "a"), _edge(1, "a", "t")), 1, _path_gold((), None, True)),
    (Mutation("P12", "_search", "                    continue\n                key =",
              "                    pass\n                key =", "The reached target is absorbing"),
     (_edge(0, "s", "t"), _edge(1, "t", "x")), 1, _path_gold((0,), "AFFECTED_HARD")),
    (Mutation("P13", "__init__", "edge.status == EdgeStatus.ADMITTED and edge.coverage_basis in TRUSTED_COVERAGE",
              "edge.coverage_basis in TRUSTED_COVERAGE", "Proposed edges do not carry admitted impact authority"),
     (_edge(0, "s", "t", status="PROPOSED_EDGE"),), 8, _path_gold((), None)),
    (Mutation("P14", "__init__", "edge.status == EdgeStatus.ADMITTED and edge.coverage_basis in TRUSTED_COVERAGE",
              "edge.status == EdgeStatus.ADMITTED", "An inferred edge cannot replace trusted coverage"),
     (_edge(0, "s", "t", coverage_basis="AGENT_INFERRED"),), 8, _path_gold((), None)),
)


@pytest.mark.parametrize("mutation,edges,limit,gold", PATH_CASES, ids=[case[0].id for case in PATH_CASES])
def test_path_rule_mutants_are_killed(monkeypatch, request, mutation, edges, limit, gold):
    oracle_path, oracle_truncated = _enumerate_paths(edges, "s", "t", limit)
    assert {"path": list(oracle_path), "truncated": oracle_truncated} == {
        "path": gold["path"], "truncated": gold["truncated"]}

    def observe():
        engine = ImpactEngine(SimpleNamespace(dependencies=edges))
        path, truncated = engine._search("s", "t", max_depth=limit)
        return {"path": [step.edge_id for step in path], "truncated": truncated,
                "classification": engine._path_classification(path).value if path else None}

    _kill(monkeypatch, request, mutation, observe, gold, oracle="EXISTING_EXHAUSTIVE_DFS_AND_PREDECLARED_GOLD")


@pytest.mark.parametrize("disconnected", [False, True], ids=["P15-cycle", "P16-disconnected-cycle"])
def test_cycle_prerequisite_mutant_is_killed(monkeypatch, request, disconnected):
    edges = ((_edge(0, "s", "t"), _edge(1, "x", "y"), _edge(2, "y", "x")) if disconnected else
             (_edge(0, "s", "a"), _edge(1, "a", "s")))
    mutation = Mutation("P16" if disconnected else "P15", "_is_acyclic", "return visited == len(indegrees)",
                        "return True", "Cycles, including disconnected cycles, invalidate DAG state merging")

    def observe():
        try:
            ImpactEngine(SimpleNamespace(dependencies=edges))._search("s", "t")
        except IntegrityError as exc:
            return {"error": str(exc)}
        return {"accepted": True}

    _kill(monkeypatch, request, mutation, observe, {"error": "CYCLIC_DEPENDENCY_GRAPH_UNSUPPORTED"})


def _coverage_fixture(fixture, case):
    target = "work:legal_review_c"
    template = fixture.dependency_manifest(target)
    edges = (_edge(0, "claim:unrelated", target), _edge(1, "claim:unrelated", target, relation="ASSUMES"))
    slots = tuple(DependencyRequirementSlot(
        slot_id=f"slot:use-{index}", edge_id=edge.id, source_id=edge.source_id,
        relation=edge.relation, strength=edge.strength, coverage_basis=edge.coverage_basis,
        provenance_refs=("test:owner-declaration",),
    ) for index, edge in enumerate(edges))
    changes = {"requirement_slots": slots}
    if case == "one-source-two-slots":
        changes["requirement_slots"] = slots[:1]
    elif case == "domain":
        changes["authority_domain"] = "finance"
    elif case == "source":
        changes["requirement_slots"] = (slots[0].model_copy(update={"source_id": "claim:wrong"}), slots[1])
    elif case == "duplicate":
        changes["requirement_slots"] = (*slots, slots[0].model_copy(update={"slot_id": "slot:duplicate"}))
    elif case == "partial":
        changes["completeness"] = "PARTIAL"
    elif case == "historical":
        changes["target_version"] = "v-historical"
    manifest = type(template).model_validate({**template.model_dump(mode="json", exclude={"digest"}), **changes})
    return fixture.model_copy(update={"dependencies": edges, "dependency_manifests": (manifest,),
                                      "impact_targets": (target,)})


COVERAGE_CASES = (
    ("one-source-two-slots", Mutation("C01", "_manifest_assessment", "if declared_ids != set(eligible_inbound):",
      "if {slot.source_id for slot in manifest.requirement_slots} != {edge.source_id for edge in eligible_inbound.values()}:",
      "One source used in two slots still requires both exact edge bindings")),
    ("domain", Mutation("C02", "_manifest_assessment", "if manifest.authority_domain != target.domain:",
      "if False:", "Coverage authority cannot be borrowed from another domain")),
    ("source", Mutation("C03", "_manifest_assessment", "slot.source_id != edge.source_id", "False",
      "Each requirement slot binds the exact source")),
    ("duplicate", Mutation("C04", "_manifest_assessment", "if len(declared_ids) != len(manifest.requirement_slots):",
      "if False:", "Repeated edge declarations cannot masquerade as distinct coverage obligations")),
    ("partial", Mutation("C05", "_manifest_assessment", "if manifest.completeness != ManifestCompleteness.COMPLETE:",
      "if False:", "Partial coverage cannot issue a bounded non-impact certificate")),
    ("historical", Mutation("C06", "dependency_manifest",
      "if item.target_id == target_id and item.target_version == target.version", "if item.target_id == target_id",
      "A current version cannot borrow a historical version's completeness", owner=EnterpriseFixture)),
)


@pytest.mark.parametrize("case,mutation", COVERAGE_CASES, ids=[case[1].id for case in COVERAGE_CASES])
def test_coverage_rule_mutants_are_killed(fixture, monkeypatch, request, case, mutation):
    graph = _coverage_fixture(fixture, case)

    def observe():
        result = ImpactEngine(graph).preview(build_change_set(graph)).results[0]
        return {"classification": result.classification.value}

    _kill(monkeypatch, request, mutation, observe, {"classification": "UNKNOWN"})


def test_budget_cannot_authorize_nonimpact_when_search_stops(monkeypatch, request, fixture):
    mutation = Mutation("B01", "_preview_source",
                        "budget_exhausted = traversal_budget_exhausted or path_truncated",
                        "budget_exhausted = False", "An exhausted traversal cannot authorize non-impact")
    graph = _coverage_fixture(fixture, "complete")
    source = fixture.change["object_id"]
    graph = graph.model_copy(update={"dependencies": (*graph.dependencies, _edge(2, source, "claim:frontier"))})

    def observe():
        engine = ImpactEngine(graph)
        engine.max_depth = 0
        preview = engine.preview(build_change_set(graph))
        return {"classification": preview.results[0].classification.value}

    _kill(monkeypatch, request, mutation, observe, {"classification": "UNKNOWN"})


def test_budget_cannot_hide_an_unseen_stronger_path(monkeypatch, request, fixture):
    mutation = Mutation("B02", "_preview_source", "budget_exhausted\n                and path_classification",
                        "False\n                and path_classification", "A weak shortcut does not close the frontier of a stronger path")
    target = "work:legal_review_c"
    source = fixture.change["object_id"]
    edges = (_edge(0, source, target, relation="MENTIONS"), _edge(1, source, "claim:frontier"),
             _edge(2, "claim:frontier", target))
    graph = fixture.model_copy(update={"dependencies": edges, "impact_targets": (target,)})

    def observe():
        engine = ImpactEngine(graph)
        engine.max_depth = 1
        return {"classification": engine.preview(build_change_set(graph)).results[0].classification.value}

    _kill(monkeypatch, request, mutation, observe, {"classification": "UNKNOWN"})


def test_certificate_cannot_claim_complete_traversal_after_budget_exhaustion(monkeypatch, request, fixture):
    mutation = Mutation("B03", "_traversal_commitment",
                        '"budget_exhausted": bool(truncated_frontier) or path_truncated,',
                        '"budget_exhausted": False,', "A certificate must disclose its incomplete traversal")

    def observe():
        engine = ImpactEngine(fixture)
        engine.max_depth = 0
        preview = engine.preview(build_change_set(fixture))
        return {"budget_exhausted": preview.certificates[0].traversal_commitment["budget_exhausted"],
                "classification": preview.results[0].classification.value}

    _kill(monkeypatch, request, mutation, observe, {"budget_exhausted": True, "classification": "UNKNOWN"})


@pytest.mark.parametrize("case", ["scope", "reachable"], ids=["S01", "S02"])
def test_scope_rule_mutants_are_killed(monkeypatch, request, fixture, case):
    source = fixture.change["object_id"]
    target = "work:legal_review_c"
    other = "work:finance_analysis_d"
    graph = fixture.model_copy(update={"dependencies": (_edge(0, source, target), _edge(1, source, other)),
                                      "impact_targets": (target,)})
    change = build_change_set(graph)
    if case == "scope":
        change = change.model_copy(update={"scope": (target, other), "digest": ""})
        mutation = Mutation("S01", "_preview_source", "if scope != tuple(self.fixture.impact_targets):",
                            "if False:", "The change scope must match the committed evaluation target set")
        expected = "CHANGE_SET_SCOPE_MUST_EQUAL_EVALUATION_TARGETS"
    else:
        mutation = Mutation("S02", "_preview_source", "if unevaluated:", "if False:",
                            "Reachable work outside scope is not silently omitted")
        expected = "REACHABLE_TARGET_OUTSIDE_EVALUATION_SCOPE:" + other

    def observe():
        try:
            ImpactEngine(graph).preview(change)
        except IntegrityError as exc:
            return {"error": str(exc)}
        return {"accepted": True}

    _kill(monkeypatch, request, mutation, observe, {"error": expected})


@pytest.mark.parametrize("relation,strength", [("UNSPECIFIED", "HARD"), ("ASSUMES", "REVIEW"),
                                                ("MENTIONS", "HARD")], ids=["M01", "M02", "M03"])
def test_multi_source_hard_cannot_override_uncertain_source(monkeypatch, request, fixture, relation, strength):
    target = "work:legal_review_c"
    source = fixture.change["object_id"]
    second = "claim:product.residency_capability"
    graph = fixture.model_copy(update={"dependencies": (_edge(0, source, target),
                                                        _edge(1, second, target, relation=relation, strength=strength)),
                                      "impact_targets": (target,)})
    change = build_change_set(graph)
    delta = change.deltas[0].model_copy(update={"object_id": second, "digest": ""})
    change = change.model_copy(update={"state": "READMISSION_GROUP_ADMITTED", "deltas": (*change.deltas, delta), "digest": ""})
    mutation = Mutation({"UNSPECIFIED": "M01", "ASSUMES": "M02", "MENTIONS": "M03"}[relation], "preview",
                        "if hard and uncertain:\n            classification = ImpactClassification.UNKNOWN",
                        "if False:\n            classification = ImpactClassification.UNKNOWN",
                        "A mixed HARD and uncertain source set must block whole-set application")

    def observe():
        result = ImpactEngine(graph).preview(change)
        return {"state": result.state, "classification": result.results[0].classification.value,
                "source_witness_count": len(result.certificates[0].traversal_commitment["source_witnesses"])}

    _kill(monkeypatch, request, mutation, observe,
          {"state": "BLOCKED", "classification": "UNKNOWN", "source_witness_count": 2})


def test_shared_certificate_recomputation_does_not_replace_independent_gold(monkeypatch, request, fixture):
    from orgrebase.certificates import ImpactCertificateVerifier

    mutation = Mutation("O01", "_transfer_classification", "propagated_rank = min(", "propagated_rank = max(",
                        "A shared transfer helper defect can survive a self-consistent certificate recomputation")
    source = fixture.change["object_id"]
    target = "work:legal_review_c"
    graph = fixture.model_copy(update={"dependencies": (_edge(0, source, target, strength="REVIEW"),),
                                      "impact_targets": (target,)})
    change = build_change_set(graph)

    def observe():
        preview = ImpactEngine(graph).preview(change)
        verification = ImpactCertificateVerifier(graph).verify(preview.certificates[0].model_dump(mode="json"), change)
        return {"classification": preview.results[0].classification.value,
                "shared_certificate_recomputation": verification["status"]}

    _kill(monkeypatch, request, mutation, observe,
          {"classification": "AFFECTED_REVIEW", "shared_certificate_recomputation": "PASS"})


def test_runtime_version_projection_mutant_is_killed(monkeypatch, request, workspace_service):
    from orgrebase.domain import VersionedObject

    workspace_service.form_quote()
    snapshot = workspace_service.current_snapshot()
    current = workspace_service.store.get_object("claim:product.enterprise_plan")
    payload = current.model_dump(mode="json", exclude={"digest"})
    payload.update(version="v-next", payload={**current.payload, "canonical_value": "Changed"})
    with workspace_service.store.transaction() as connection:
        workspace_service.store.insert_version(connection, VersionedObject.model_validate(payload), make_current=True)
    mutation = Mutation("V01", "to_enterprise_fixture", "if current_by_id.get(provider.id) != edge.provider_ref:",
                        "if False:", "An old runtime source version cannot authorize a current consumer",
                        owner=type(workspace_service.snapshot_builder))

    def observe():
        try:
            _project(workspace_service, snapshot)
        except ValueError as exc:
            return {"error": str(exc).split(":", 1)[0]}
        return {"accepted": True}

    _kill(monkeypatch, request, mutation, observe, {"error": "SNAPSHOT_RUNTIME_SOURCE_VERSION_STALE"})


def test_runtime_slots_cannot_be_deduplicated_by_source(monkeypatch, request, workspace_service):
    from orgrebase.digest import sha256_digest
    from orgrebase.workspace.models import (
        RuntimeDependencyManifest,
        WorkspaceGraphEdge,
        WorkspaceGraphSnapshot,
    )

    receipt = workspace_service.form_quote()
    snapshot = workspace_service.current_snapshot()
    manifest = RuntimeDependencyManifest.model_validate(
        workspace_service.store.load_artifact(receipt.dependency_manifest_ref).payload)
    entry = manifest.entries[0]
    second = entry.model_copy(update={"slot_id": "second-source-use"})
    payload = manifest.model_dump(mode="json", exclude={"digest"})
    payload["entries"].append(second.model_dump(mode="json"))
    revised = RuntimeDependencyManifest.model_validate(payload)
    original_edge = next(item for item in snapshot.edges
                         if item.source_evidence_ref == entry.source_event_ref and item.provider_ref == entry.provider_ref)
    edge_payload = original_edge.model_dump(mode="json", exclude={"digest"})
    edge_payload["id"] = f"edge:runtime:{sha256_digest((second.provider_ref, second.consumer_ref, second.slot_id))[7:23]}"
    snapshot_payload = snapshot.model_dump(mode="json", exclude={"digest"})
    snapshot_payload["edges"].append(WorkspaceGraphEdge.model_validate(edge_payload).model_dump(mode="json"))
    snapshot_payload["edges"].sort(key=lambda item: item["id"])
    snapshot_payload["edge_set_digest"] = sha256_digest(snapshot_payload["edges"])
    revised_snapshot = WorkspaceGraphSnapshot.model_validate(snapshot_payload)

    def reader(ref, media_type=None):
        if ref == manifest.ref:
            return SimpleNamespace(payload=revised.model_dump(mode="json"))
        return workspace_service.store.load_artifact(ref, media_type)

    def observe():
        projected = _project(workspace_service, revised_snapshot, reader)
        compiled = next(item for item in projected.dependency_manifests if item.target_id == workspace_service.quote_object_id)
        matching = [slot for slot in compiled.requirement_slots if slot.source_id == entry.provider_ref.rsplit("@", 1)[0]]
        return {"slot_count": len(matching), "unique_edge_count": len({slot.edge_id for slot in matching})}

    mutation = Mutation("V02", "to_enterprise_fixture", "for entry in runtime.entries:",
                        "for entry in {item.provider_ref: item for item in runtime.entries}.values():",
                        "The same exact source version can supply multiple distinct runtime slots",
                        owner=type(workspace_service.snapshot_builder))
    _kill(monkeypatch, request, mutation, observe, {"slot_count": 2, "unique_edge_count": 2})


def _project(workspace, snapshot, reader=None):
    return workspace.snapshot_builder.to_enterprise_fixture(
        snapshot=snapshot, artifact_reader=reader or workspace.store.load_artifact,
        object_reader=workspace.store.get_object, agents=workspace.legacy.agents,
        evaluation_cases=workspace.legacy.evaluation_cases, context_profiles=workspace.context_profiles, change={},
    )
