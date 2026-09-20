"""Case versions are retained evidence, not independent qualification support."""

from __future__ import annotations

import pytest

from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.pattern_evolution import CaseObservation, GovernedPatternService, _record
from tests.workspace.test_pattern_evolution import decide, observed, prepare


def version(case_id, revision, outcome):
    base = observed(case_id, outcome)
    certificate = {
        key: value
        for key, value in base.certificate.items()
        if key not in {"digest", "kind", "schema_version"}
    }
    certificate["case_revision"] = revision
    return CaseObservation(
        case_id=case_id,
        revision=revision,
        public_input=base.public_input,
        outcome=outcome,
        certificate=_record("outcome-observation", **certificate),
    )


@pytest.mark.parametrize(
    ("versions", "qualified", "support_count"),
    [
        ((("a", "1", "SUPPORT"), ("a", "2", "SUPPORT"), ("b", "1", "COUNTEREXAMPLE")), False, 1),
        ((("a", "1", "SUPPORT"), ("a", "2", "COUNTEREXAMPLE"), ("b", "1", "SUPPORT")), False, 1),
        (
            (
                ("a", "1", "SUPPORT"),
                ("a", "2", "UNKNOWN"),
                ("b", "1", "SUPPORT"),
                ("c", "1", "COUNTEREXAMPLE"),
            ),
            False,
            1,
        ),
        (
            (("a", "1", "SUPPORT"), ("a", "2", "NULL"), ("b", "1", "SUPPORT"), ("c", "1", "COUNTEREXAMPLE")),
            False,
            1,
        ),
        ((("a", "1", "SUPPORT"), ("b", "1", "SUPPORT"), ("c", "1", "COUNTEREXAMPLE")), True, 2),
        (
            (
                ("a", "1", "SUPPORT"),
                ("a", "2", "SUPPORT"),
                ("b", "1", "SUPPORT"),
                ("c", "1", "COUNTEREXAMPLE"),
                ("c", "2", "COUNTEREXAMPLE"),
            ),
            True,
            2,
        ),
    ],
    ids=(
        "duplicate-support",
        "contradictory-versions",
        "unknown-version",
        "null-version",
        "independent-controls",
        "independent-controls-retain-history",
    ),
)
def test_qualification_counts_independent_cases(tmp_path, versions, qualified, support_count):
    cases = tuple(version(*entry) for entry in versions)
    with StateStore(tmp_path / "case-independence.sqlite3") as store:
        service = GovernedPatternService(
            store,
            corpus_authority="scripted:corpus-controller",
            evaluator_authority="scripted:replay-controller",
            governance_authority="scripted:skill-governance",
        )
        proposal, boundary, corpus, _ = prepare(service, cases=cases)
        retained = service._load(corpus)
        assert [row["case"] for row in retained["cases"]] == [
            case.model_dump(mode="json")
            for case in sorted(cases, key=lambda item: (item.case_id, item.revision))
        ]
        (candidate,) = service.propose(proposal, actor_id="learner:bounded", boundary=boundary)
        evaluation_ref = service.evaluate(candidate, actor_id="scripted:replay-controller")
        evaluation = service._load(evaluation_ref)
        assert all(item["passed"] for item in evaluation["current"]["case_results"])
        assert evaluation["verdict"] == ("QUALIFIED" if qualified else "REJECTED")
        assert len(evaluation["independent_case_membership"]["SUPPORT"]) == support_count
        if qualified:
            decide(service, candidate, evaluation_ref)
            assert len(service._family("admission")) == 1
        else:
            with pytest.raises(IntegrityError, match="PATTERN_CANDIDATE_NOT_QUALIFIED"):
                decide(service, candidate, evaluation_ref)
            assert service._family("admission") == ()


@pytest.mark.parametrize("change", ("missing", "duplicate", "overlap", "not-list"))
def test_admission_requires_frozen_independent_case_membership(tmp_path, change):
    with StateStore(tmp_path / "invalid-basis.sqlite3") as store:
        service = GovernedPatternService(
            store,
            corpus_authority="scripted:corpus-controller",
            evaluator_authority="scripted:replay-controller",
            governance_authority="scripted:skill-governance",
        )
        proposal, boundary, _, _ = prepare(service)
        (candidate,) = service.propose(proposal, actor_id="learner:bounded", boundary=boundary)
        evaluation = service._load(service.evaluate(candidate, actor_id="scripted:replay-controller"))
        if change == "missing":
            evaluation.pop("independent_case_membership")
        elif change == "duplicate":
            evaluation["independent_case_membership"]["SUPPORT"] = ["case:0", "case:0"]
        elif change == "overlap":
            evaluation["independent_case_membership"]["COUNTEREXAMPLE"] = ["case:0"]
        else:
            evaluation["independent_case_membership"]["SUPPORT"] = "case:0"
        body = {
            key: value for key, value in evaluation.items() if key not in {"digest", "kind", "schema_version"}
        }
        with service._transaction() as connection:
            ref = service._save(connection, _record("evaluation", **body))
        with pytest.raises(IntegrityError, match="PATTERN_CANDIDATE_NOT_QUALIFIED"):
            decide(service, candidate, ref)
        assert service._family("admission") == ()


def test_restart_denies_release_without_independent_qualification_basis(tmp_path, monkeypatch):
    from orgrebase.workspace import pattern_evolution
    from orgrebase.workspace.skill_packages import InvocationContext
    from tests.workspace.test_pattern_evolution import public

    database = tmp_path / "legacy-qualified.sqlite3"
    with StateStore(database) as store:
        service = GovernedPatternService(
            store,
            corpus_authority="scripted:corpus-controller",
            evaluator_authority="scripted:replay-controller",
            governance_authority="scripted:skill-governance",
        )
        proposal, boundary, _, _ = prepare(service)
        (candidate,) = service.propose(proposal, actor_id="learner:bounded", boundary=boundary)
        evaluation = service._load(service.evaluate(candidate, actor_id="scripted:replay-controller"))
        body = {
            key: value
            for key, value in evaluation.items()
            if key not in {"digest", "kind", "schema_version", "independent_case_membership"}
        }
        with service._transaction() as connection:
            ref = service._save(connection, _record("evaluation", **body))
        # Reproduce a pre-fix release that did not record independent case identities.
        with monkeypatch.context() as legacy:
            legacy.setattr(pattern_evolution, "_has_independent_support", lambda _: True)
            decide(service, candidate, ref)
    with StateStore(database) as reopened:
        service = GovernedPatternService(
            reopened,
            corpus_authority="scripted:corpus-controller",
            evaluator_authority="scripted:replay-controller",
            governance_authority="scripted:skill-governance",
            prerequisite_resolver=lambda actor, ref: True,
        )
        value = public()
        with pytest.raises(IntegrityError, match="PATTERN_PERSISTED_ADMISSION_BINDING_MISMATCH"):
            service.invoke(
                candidate,
                value,
                context=InvocationContext(
                    value["run_id"], value["task_id"], value["delegation_id"], "scripted:qualified-reviewer"
                ),
                knowledge_refs=("knowledge:handoff-v1",),
                qualification_refs=("qualification:handoff-review",),
            )
