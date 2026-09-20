"""Independent path enumeration protects the optimized engine's proof semantics."""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from itertools import pairwise
from types import SimpleNamespace
from typing import Any

import pytest

from orgrebase.domain import DependencyEdge, IntegrityError
from orgrebase.fixture import EnterpriseFixture
from orgrebase.impact import ImpactEngine, build_change_set


def _edge(index: int, source: str, target: str, **kwargs: Any) -> DependencyEdge:
    return DependencyEdge(
        id=f"edge:{index:05d}",
        source_id=source,
        target_id=target,
        relation=kwargs.get("relation", "DERIVED_FROM"),
        strength=kwargs.get("strength", "HARD"),
        coverage_basis=kwargs.get("coverage_basis", "CONTRACT_DECLARED"),
        status=kwargs.get("status", "ADMITTED"),
        provenance_refs=("test:dag-equivalence",),
    )


def _engine(edges: tuple[DependencyEdge, ...]) -> ImpactEngine:
    return ImpactEngine(SimpleNamespace(dependencies=edges))  # type: ignore[arg-type]


def _enumerate_paths(
    edges: tuple[DependencyEdge, ...], source: str, target: str, limit: int
) -> tuple[tuple[str, ...], bool]:
    """Small-graph oracle; deliberately imports no product transfer/ranking helper."""
    adjacency: dict[str, list[DependencyEdge]] = defaultdict(list)
    for edge in edges:
        if edge.status == "ADMITTED" and edge.coverage_basis in {
            "RUNTIME_OBSERVED",
            "OWNER_DECLARED_COMPLETE",
            "CONTRACT_DECLARED",
            "IMPORTED_VERIFIED",
        }:
            adjacency[edge.source_id].append(edge)
    paths: list[tuple[DependencyEdge, ...]] = []
    frontier = [(source, (), {source})]
    truncated = False
    while frontier:
        node, path, visited = frontier.pop()
        outgoing = [edge for edge in adjacency[node] if edge.target_id not in visited]
        if len(path) >= limit:
            truncated |= bool(outgoing)
            continue
        for edge in outgoing:
            following = (*path, edge)
            if edge.target_id == target:
                paths.append(following)
            else:
                frontier.append((edge.target_id, following, visited | {edge.target_id}))

    def rank(path: tuple[DependencyEdge, ...]) -> tuple[int, int, tuple[str, ...]]:
        strengths = {"HARD": 3, "REVIEW": 2, "INFORMATIONAL": 1}
        ceilings = {"DERIVED_FROM": 3, "ASSUMES": 3, "REQUIRES_CLAIM": 3, "REQUIRES_POLICY": 3, "MENTIONS": 1}
        if any(edge.relation not in ceilings for edge in path):
            severity = 3  # UNKNOWN outranks REVIEW, but cannot outrank all-HARD.
        else:
            transfer = min(min(strengths[edge.strength], ceilings[edge.relation]) for edge in path)
            severity = {1: 1, 2: 2, 3: 4}[transfer]
        return -severity, len(path), tuple(edge.id for edge in path)

    selected = min(paths, key=rank) if paths else ()
    return tuple(edge.id for edge in selected), truncated


def test_random_dags_match_independent_exhaustive_oracle() -> None:
    rng = random.Random(20260909)
    for _ in range(200):
        size = rng.randint(4, 13)
        edges = []
        for left in range(size):
            for right in range(left + 1, size):
                if rng.random() < 0.29:
                    edges.append(
                        _edge(
                            len(edges),
                            str(left),
                            str(right),
                            relation=rng.choice(("DERIVED_FROM", "ASSUMES", "MENTIONS", "NEW_RELATION")),
                            strength=rng.choice(("HARD", "REVIEW", "INFORMATIONAL")),
                            status=rng.choice(("ADMITTED", "ADMITTED", "PROPOSED_EDGE")),
                            coverage_basis=rng.choice(
                                ("CONTRACT_DECLARED", "RUNTIME_OBSERVED", "AGENT_INFERRED")
                            ),
                        )
                    )
        graph = tuple(edges)
        engine = _engine(graph)
        for limit in (0, 1, 2, 4, 8, 16):
            target = str(rng.randrange(size))
            actual, truncated = engine._search("0", target, max_depth=limit)
            assert (tuple(step.edge_id for step in actual), truncated) == _enumerate_paths(
                graph, "0", target, limit
            )


def test_unknown_prefix_survives_hard_prefix_then_weak_suffix() -> None:
    edges = (
        _edge(0, "s", "a"),
        _edge(1, "a", "m"),
        _edge(2, "s", "b", relation="NEW_RELATION"),
        _edge(3, "b", "m"),
        _edge(4, "m", "t", relation="MENTIONS"),
    )
    path, truncated = _engine(edges)._search("s", "t")
    assert tuple(step.edge_id for step in path) == ("edge:00002", "edge:00003", "edge:00004")
    assert truncated is False


def test_same_state_prefix_keeps_lexically_first_path() -> None:
    edges = (
        _edge(3, "s", "a"),
        _edge(4, "a", "m"),
        _edge(1, "s", "b"),
        _edge(2, "b", "m"),
        _edge(5, "m", "t"),
    )
    path, _ = _engine(edges)._search("s", "t")
    assert tuple(step.edge_id for step in path) == ("edge:00001", "edge:00002", "edge:00005")


def test_target_is_absorbing_and_cannot_create_false_path_truncation() -> None:
    edges = (_edge(0, "s", "t"), _edge(1, "t", "x"), _edge(2, "x", "y"))
    path, truncated = _engine(edges)._search("s", "t", max_depth=1)
    assert tuple(step.edge_id for step in path) == ("edge:00000",)
    assert truncated is False


@pytest.mark.parametrize(
    "cycle", [(("s", "s"),), (("s", "a"), ("a", "s")), (("s", "t"), ("x", "y"), ("y", "x"))]
)
def test_cycles_fail_explicitly_instead_of_merging_invalid_path_history(cycle: tuple) -> None:
    edges = tuple(_edge(i, left, right) for i, (left, right) in enumerate(cycle))
    with pytest.raises(IntegrityError, match="CYCLIC_DEPENDENCY_GRAPH_UNSUPPORTED"):
        _engine(edges)._search("s", "t")


def test_unadmitted_cycle_does_not_block_admitted_dag() -> None:
    edges = (_edge(0, "s", "t"), _edge(1, "t", "s", status="PROPOSED_EDGE"))
    assert _engine(edges)._search("s", "t")[0][0].edge_id == "edge:00000"


def test_negative_depth_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_depth must be non-negative"):
        _engine(())._search("s", "t", max_depth=-1)


def test_layered_graph_work_scales_with_states_not_number_of_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    # 20**7 paths would be infeasible to enumerate. Every edge here has one transfer state.
    layers = [["s"], *[[f"{level}:{i}" for i in range(20)] for level in range(7)], ["t"]]
    edges = []
    for left, right in pairwise(layers):
        for source in left:
            for target in right:
                edges.append(_edge(len(edges), source, target))
    engine = _engine(tuple(edges))
    calls = 0
    original = engine._transfer_classification

    def counted(steps: tuple) -> Any:
        nonlocal calls
        calls += 1
        return original(steps)

    monkeypatch.setattr(engine, "_transfer_classification", counted)
    path, truncated = engine._search("s", "t")
    assert len(path) == 8 and not truncated
    assert calls <= 2 * len(edges)


def test_preview_shares_graph_commitments_and_does_not_repeat_path_search(
    fixture: EnterpriseFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = ImpactEngine(fixture)
    counts: Counter = Counter()
    for name in ("_search", "_reachable_slice", "_traversal_snapshot"):
        original = getattr(engine, name)

        def counted(*args: Any, _name: str = name, _original: Any = original, **kwargs: Any) -> Any:
            counts[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(engine, name, counted)
    preview = engine.preview(build_change_set(fixture))
    assert counts == {"_search": len(fixture.impact_targets), "_reachable_slice": 1, "_traversal_snapshot": 1}
    # Captured from the pre-optimization implementation; includes every certificate digest.
    assert preview.digest == "sha256:3f63d00ac12f4fa000ca90e26ce63824877b41f96c4c97705e609c5d94d18741"


def test_fresh_preview_recomputes_instead_of_reusing_another_depths_commitments(
    fixture: EnterpriseFixture,
) -> None:
    engine = ImpactEngine(fixture)
    change = build_change_set(fixture)
    first = engine.preview(change)
    engine.max_depth = 0
    second = engine.preview(change)
    assert first.digest != second.digest
    assert all(item.classification == "UNKNOWN" for item in second.results)
    assert all(item.traversal_commitment["max_depth"] == 0 for item in second.certificates)
