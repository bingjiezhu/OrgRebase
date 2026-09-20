from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache

import pytest

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.outcome_lab import SYSTEMS, OutcomeLab, OutcomeOracle, OutcomeTaskMapping
from orgrebase.workspace.outcome_pattern_bridge import (
    PORTABLE_MEDIA,
    PROFILE_ID,
    PUBLIC_PATHS,
    TASK_MEDIA,
    TRUST_MEDIA,
    OutcomeRunEvidence,
    build_retail_case_candidate,
    make_retail_case_resolver,
)
from orgrebase.workspace.outcome_portability import (
    LabOutcomeTrust,
    build_lab_lifecycle,
    build_portable_lab_outcome,
)
from orgrebase.workspace.pattern_evolution import CaseObservation, GovernedPatternService
from tests.workspace.test_outcome_portability import FIXTURE, LocalEnvironment, RecordingGate
from tests.workspace.test_pattern_evolution import prepare

CORPUS = "scripted:corpus-controller"
CONTROLLER = "test:portable-controller"
ACTOR = "test:portable-actor"
PUBLIC_TASK = {
    "id": "unit-test",
    "user_scenario": {
        "persona": None,
        "instructions": {
            "domain": "retail",
            "reason_for_call": "Cancel pending orders.",
            "known_info": "Two pending orders.",
            "unknown_info": "No email.",
            "task_instructions": "Provide the reason when asked.",
        },
    },
    "evaluation_criteria": {"secret": "must-not-be-a-feature"},
}


@pytest.fixture(scope="module")
def experiments():
    """Real public CLI and existing controller, small controlled state; no tau benchmark claim."""

    @lru_cache(maxsize=16)
    def group(mode="accept", repetition=0, revision="a" * 40, extra_budget=0):
        del repetition  # Cache key forces distinct actual controller approvals per repetition.
        values = {
            key: json.loads((FIXTURE / (key + ".json")).read_text())
            for key in ("snapshot", "change", "plan", "mapping")
        }
        results = []
        for system in SYSTEMS:
            environment = LocalEnvironment(mode if system == "oac" else "accept")
            oracle = OutcomeOracle(
                authority="test:portable-oracle",
                policy_source_digest=sha256_digest(PUBLIC_TASK["evaluation_criteria"]),
                expected={"/orders/#W5056519/status": "cancelled", "/orders/#W5995614/status": "cancelled"},
            )
            raw = deepcopy(values["mapping"])
            raw.pop("digest")
            raw.update(
                task_id=PUBLIC_TASK["id"],
                task_digest=sha256_digest(PUBLIC_TASK),
                upstream_repository="https://example.org/controlled-local-retail",
                upstream_revision=revision,
                environment_build_digest=environment.build_digest,
                seed_root=sha256_digest(environment.snapshot()),
                oracle_digest=oracle.digest,
                budget={"tool_calls": 12 + extra_budget, "write_calls": 2, "elapsed_seconds": 30},
            )
            plan = values["plan"]["spec"]
            raw["work_unit_predecessors"] = {unit["workUnitId"]: [] for unit in plan["workUnits"]}
            for edge in plan["happensBefore"]:
                raw["work_unit_predecessors"][edge["successorRef"]].append(edge["predecessorRef"])
            for grant in raw["grants"]:
                grant.pop("digest", None)
                grant["work_unit_ref"] = next(
                    unit["workUnitId"]
                    for unit in plan["workUnits"]
                    if grant["obligation_ref"] in unit["obligationRefs"]
                )
            mapping = OutcomeTaskMapping.model_validate(raw)
            gate = RecordingGate()
            lifecycle = build_lab_lifecycle(
                gate, mapping, oracle, values["snapshot"], values["change"], values["plan"], CONTROLLER, ACTOR
            )
            assert environment.calls == 0
            lab = OutcomeLab(
                mapping,
                oracle,
                environment,
                gate,
                snapshot=values["snapshot"],
                change=values["change"],
                plan=values["plan"],
                controller_authority=CONTROLLER,
                acting_authority=ACTOR,
            )
            approval = lab.approve(system, authority=CONTROLLER)
            receipt = lab.run(
                approval["token"],
                [grant.request for grant in mapping.grants],
                runtime_bundle=approval["runtime_bundle"],
            )
            assert receipt["runtime_bundle"] == approval["runtime_bundle"]
            trust = LabOutcomeTrust(
                mapping=mapping,
                oracle=oracle,
                controller_authority=CONTROLLER,
                acting_authority=ACTOR,
                lifecycle_digest=lifecycle["digest"],
                receipt_digest=receipt["digest"],
                approval_digest=approval["receipt"]["digest"],
                oracle_build_digest=sha256_digest("controlled-test-observer-build"),
            )
            results.append(
                OutcomeRunEvidence(build_portable_lab_outcome(gate, lifecycle, receipt, trust), trust)
            )
            lab.close()
        return tuple(results)

    return group


@pytest.fixture
def store(tmp_path):
    with StateStore(tmp_path / "case.sqlite3") as value:
        yield value


def candidate(store, runs, *, task=PUBLIC_TASK, issuer=CORPUS):
    return build_retail_case_candidate(
        task, iter(runs), store=store, gate=RecordingGate(), corpus_authority=issuer
    )


def resolver(store, runs):
    trusted = {run.trust.receipt_digest: run.trust for run in runs}
    return make_retail_case_resolver(store, RecordingGate(), trusted.__getitem__)


def reseal_case(case, mutate):
    raw = case.model_dump(mode="json", exclude={"digest"})
    mutate(raw["certificate"])
    raw["certificate"]["digest"] = sha256_digest(
        {k: v for k, v in raw["certificate"].items() if k != "digest"}
    )
    return CaseObservation.model_validate(raw)


def test_complete_evidence_is_stored_once_case_stays_small_and_reopens(store, experiments):
    runs = experiments() + experiments(repetition=1)
    case = candidate(store, runs)
    assert case.certificate["profile_id"] == PROFILE_ID
    assert case.certificate["source_case_count"] == 1
    assert case.certificate["corpus_admitted"] is False
    assert case.certificate["public_projection_paths"] == list(PUBLIC_PATHS)
    assert len(canonical_json(case.model_dump(mode="json")).encode()) < 20000
    assert "state_observations" not in canonical_json(case.model_dump(mode="json"))
    assert case.public_input == {"user_scenario": PUBLIC_TASK["user_scenario"]}
    assert list(case.certificate["experiment_counts"].values()) == [dict.fromkeys(SYSTEMS, 2)]
    for run in runs:
        row = next(
            row for row in case.certificate["runs"] if row["receipt_digest"] == run.trust.receipt_digest
        )
        artifact = store.load_artifact(row["portable_ref"]["artifact_id"], PORTABLE_MEDIA)
        assert artifact.payload == run.bundle
        assert row["outcome_certificate_ref"]["digest"] == run.bundle["outcome_certificate"]["digest"]
    assert (
        len(
            store.list_artifacts(
                artifact_id_prefix="outcome-case:portable:", expected_media_type=PORTABLE_MEDIA
            )
        )
        == 8
    )
    assert (
        len(store.list_artifacts(artifact_id_prefix="outcome-case:trust:", expected_media_type=TRUST_MEDIA))
        == 8
    )
    assert (
        len(store.list_artifacts(artifact_id_prefix="outcome-case:task:", expected_media_type=TASK_MEDIA))
        == 1
    )
    assert candidate(store, reversed(runs)).digest == case.digest
    assert (
        len(
            store.list_artifacts(
                artifact_id_prefix="outcome-case:portable:", expected_media_type=PORTABLE_MEDIA
            )
        )
        == 8
    )
    assert resolver(store, runs)(case) == tuple(
        sorted(run.bundle["outcome_certificate"]["digest"] for run in runs)
    )
    path = store.path
    with StateStore(path) as reopened:
        assert resolver(reopened, runs)(case) == resolver(store, runs)(case)


@pytest.mark.parametrize(
    "mode,outcome", [("accept", "SUPPORT"), ("reject", "COUNTEREXAMPLE"), ("unknown-reset", "UNKNOWN")]
)
def test_actual_portable_verdict_drives_case(store, experiments, mode, outcome):
    runs = experiments(mode=mode)
    case = candidate(store, runs)
    assert case.outcome == outcome
    assert len(resolver(store, runs)(case)) == 4


@pytest.mark.parametrize("attack", ["partial", "duplicate", "unbalanced-context", "empty"])
def test_incomplete_or_reused_experiments_roll_back_every_artifact(store, experiments, attack):
    base = experiments()
    groups = {
        "partial": base[:-1],
        "duplicate": base + base,
        "unbalanced-context": base[:2] + experiments(extra_budget=1)[2:],
        "empty": (),
    }
    with pytest.raises(IntegrityError, match=r"OUTCOME_CASE_(BALANCED|REPEATED|EXPERIMENT)"):
        candidate(store, groups[attack])
    assert store.list_artifacts(artifact_id_prefix="outcome-case:") == ()


@pytest.mark.parametrize(
    "attack",
    ["receipt", "outcome-certificate", "payload", "trust-approval", "trust-oracle", "trust-lifecycle"],
)
def test_resealed_payload_or_wrong_host_pin_cannot_be_persisted(store, experiments, attack):
    runs = list(experiments())
    run = runs[0]
    if attack.startswith("trust-"):
        key = {
            "trust-approval": "approval_digest",
            "trust-oracle": "oracle_build_digest",
            "trust-lifecycle": "lifecycle_digest",
        }[attack]
        # oracle build is a caller-supplied pin; altering it while retaining the old bundle
        # must fail exact portable reconstruction rather than be silently trusted.
        runs[0] = replace(run, trust=replace(run.trust, **{key: sha256_digest("wrong-pin")}))
    else:
        bundle = deepcopy(run.bundle)
        if attack == "receipt":
            bundle["receipt"]["dimensions"]["task_goal"] = "FAIL"
        elif attack == "outcome-certificate":
            bundle["outcome_certificate"]["spec"]["verdict"] = "REJECT"
        else:
            bundle["payloads"][0]["spec"]["unexpected"] = "changed"
        bundle["digest"] = sha256_digest({k: v for k, v in bundle.items() if k != "digest"})
        runs[0] = replace(run, bundle=bundle)
    with pytest.raises(IntegrityError):
        candidate(store, runs)
    assert store.list_artifacts(artifact_id_prefix="outcome-case:") == ()


@pytest.mark.parametrize(
    "attack",
    [
        "outcome-ref",
        "artifact-media",
        "artifact-digest",
        "artifact-path",
        "extra-field",
        "context",
        "system",
        "profile",
    ],
)
def test_small_case_resealing_cannot_substitute_verified_references(store, experiments, attack):
    runs = experiments()
    case = candidate(store, runs)

    def mutate(cert):
        row = cert["runs"][0]
        if attack == "outcome-ref":
            row["outcome_certificate_ref"]["digest"] = sha256_digest("wrong-outcome")
        elif attack == "artifact-media":
            row["portable_ref"]["media_type"] = TRUST_MEDIA
        elif attack == "artifact-digest":
            row["portable_ref"]["payload_digest"] = sha256_digest("wrong-artifact")
        elif attack == "artifact-path":
            row["portable_ref"]["artifact_id"] = cert["runs"][1]["portable_ref"]["artifact_id"]
        elif attack == "context":
            row["trust_ref"] = cert["runs"][1]["trust_ref"]
        elif attack == "system":
            row["system"] = "oac" if row["system"] != "oac" else "graph-only"
        elif attack == "profile":
            cert["profile_id"] = "orgrebase.tau2-retail-outcome-case/v1"
        else:
            row["unverified"] = True

    forged = reseal_case(case, mutate)
    with pytest.raises(IntegrityError):
        resolver(store, runs)(forged)


def test_case_cannot_supply_its_own_trust_and_cross_store_cannot_resolve(store, experiments, tmp_path):
    runs = experiments()
    case = candidate(store, runs)
    missing = make_retail_case_resolver(store, RecordingGate(), {}.__getitem__)
    with pytest.raises(IntegrityError, match="TRUST_NOT_FOUND"):
        missing(case)
    with (
        StateStore(tmp_path / "other.sqlite3") as other,
        pytest.raises(KeyError, match="ARTIFACT_NOT_FOUND"),
    ):
        resolver(other, runs)(case)


def test_caller_buffer_reuse_cannot_change_verified_persisted_bytes(store, experiments):
    runs = list(experiments())
    original = deepcopy(runs[0].bundle)
    expected = deepcopy(original)
    runs[0] = replace(runs[0], bundle=original)

    class ReusedBufferGate(RecordingGate):
        def validate_lifecycle(self, resource, *, snapshot=None):
            original["receipt"]["verdict"] = "REJECT"
            super().validate_lifecycle(resource, snapshot=snapshot)

    case = build_retail_case_candidate(
        PUBLIC_TASK, iter(runs), store=store, gate=ReusedBufferGate(), corpus_authority=CORPUS
    )
    row = next(
        row for row in case.certificate["runs"] if row["receipt_digest"] == runs[0].trust.receipt_digest
    )
    assert original != expected
    assert store.load_artifact(row["portable_ref"]["artifact_id"], PORTABLE_MEDIA).payload == expected
    assert len(resolver(store, runs)(case)) == 4


def test_source_task_actor_and_revision_boundaries_remain_exact(store, experiments):
    runs = experiments()
    with pytest.raises(IntegrityError, match="PUBLIC_TASK_BINDING_MISMATCH"):
        candidate(store, runs, task={**PUBLIC_TASK, "id": "other"})
    with pytest.raises(IntegrityError, match="ACTOR_CANNOT_ISSUE_CORPUS"):
        candidate(store, runs, issuer=ACTOR)
    first = candidate(store, runs)
    second = candidate(store, experiments(extra_budget=1))
    assert first.case_id == second.case_id and first.revision == second.revision
    version = candidate(store, experiments(revision="b" * 40))
    assert version.case_id == first.case_id and version.revision != first.revision
    with pytest.raises(IntegrityError, match="SOURCE_COORDINATE_MISMATCH"):
        candidate(store, runs + experiments(revision="b" * 40))


@pytest.mark.parametrize("task_id", [None, True, "", []])
def test_invalid_source_id_fails_before_evidence_is_consumed(store, task_id):
    def must_not_read():
        raise AssertionError("invalid source consumed evidence")
        yield

    with pytest.raises(IntegrityError, match="OUTCOME_CASE_TASK_ID_REQUIRED"):
        candidate(store, must_not_read(), task={**PUBLIC_TASK, "id": task_id})
    assert store.list_artifacts(artifact_id_prefix="outcome-case:") == ()


def pattern_service(store, runs, *, resolve=True):
    return GovernedPatternService(
        store,
        corpus_authority=CORPUS,
        evaluator_authority="scripted:replay-controller",
        governance_authority="scripted:skill-governance",
        clock=lambda: 1800000000.0,
        case_evidence_resolver=resolver(store, runs) if resolve else None,
    )


def test_corpus_requires_trusted_resolver_and_rejects_same_case_repetitions(store, experiments):
    runs = experiments()
    case = candidate(store, runs)
    with pytest.raises(AuthorizationError, match="CASE_EVIDENCE_RESOLVER_REQUIRED"):
        prepare(pattern_service(store, runs, resolve=False), cases=(case,))
    other = candidate(store, experiments(extra_budget=1))
    with pytest.raises(IntegrityError, match="CORPUS_CASE_SET_INVALID"):
        prepare(pattern_service(store, runs), cases=(case, other))


def test_real_oac_certificate_withdrawal_invalidates_pattern_and_blocks_evaluation(store, experiments):
    runs = experiments()
    case = candidate(store, runs)
    service = pattern_service(store, runs)
    proposal, boundary, corpus, _ = prepare(
        service, paths=("/user_scenario/instructions/domain",), cases=(case,)
    )
    (candidate_ref,) = service.propose(proposal, actor_id="learner:bounded", boundary=boundary)
    outcome_digest = runs[-1].bundle["outcome_certificate"]["digest"]
    record = service._load(candidate_ref)
    assert outcome_digest in service._load(record["pattern_ref"])["certificate_refs"]
    assert outcome_digest in service._load(corpus)["cases"][0]["certificate_refs"]
    service.retract(
        outcome_digest, actor_id="scripted:skill-governance", reason="actual OAC certificate withdrawn"
    )
    assert service.evidence_status(record["pattern_ref"]) == "REQUALIFICATION_REQUIRED"
    assert service.evidence_status(candidate_ref) == "REQUALIFICATION_REQUIRED"
    with pytest.raises(IntegrityError):
        service.evaluate(candidate_ref, actor_id="scripted:replay-controller")


def test_one_real_case_still_cannot_qualify_or_admit_skill(store, experiments):
    runs = experiments() + experiments(repetition=1)
    case = candidate(store, runs)
    service = pattern_service(store, runs)
    proposal, boundary, _, _ = prepare(service, paths=("/user_scenario/instructions/domain",), cases=(case,))
    (ref,) = service.propose(proposal, actor_id="learner:bounded", boundary=boundary)
    record = service._load(ref)
    assert record["executable"] is False
    assert len(service._load(record["pattern_ref"])["membership"]["SUPPORT"]) == 1
    evaluation = service.evaluate(ref, actor_id="scripted:replay-controller")
    assert service._load(evaluation)["verdict"] == "REJECTED"
    with pytest.raises(IntegrityError, match="CANDIDATE_NOT_QUALIFIED"):
        service.decide(
            ref,
            evaluation,
            actor_id="scripted:skill-governance",
            verdict="ADMIT",
            expected_candidate_digest=record["digest"],
        )
