from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import rfc8785

from oac.benchmark import build_run_manifests
from oac.canonical import parse_resource, resource_ref
from oac.compiler import compile_supplier_change
from oac.models import (
    OrganizationPlan,
    OrganizationSnapshot,
    OrgChangeCase,
    SemanticChangeSet,
    Verdict,
)
from oac.verifier import verify_plan

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "profiles" / "supplier-change"


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resource(path: Path, expected_type: type[object]) -> object:
    value = parse_resource(path.read_bytes(), verify_digest=True)
    assert isinstance(value, expected_type)
    return value


def _case_paths() -> list[Path]:
    index = _json(PROFILE / "cases" / "index.json")
    return [PROFILE / "cases" / name for name in index["cases"]]  # type: ignore[index]


def _benchmark_sandbox(tmp_path: Path) -> Path:
    sandbox = tmp_path / "repo"
    for relative in ("benchmark", "profiles", "scripts", "src/oac"):
        source = ROOT / relative
        destination = sandbox / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
    shutil.copy2(ROOT / "pyproject.toml", sandbox / "pyproject.toml")
    shutil.copy2(ROOT / "uv.lock", sandbox / "uv.lock")
    return sandbox


def _observed_token(value: object) -> str:
    state = value.state  # type: ignore[attr-defined]
    return str(value.value) if state == "known" else str(state)  # type: ignore[attr-defined]


def _benchmark_behavioral_projection(
    manifest: dict[str, object],
) -> dict[str, object]:
    return {
        key: value
        for key, value in manifest.items()
        if key not in {"digest", "implementationClosure", "implementationDigest"}
    }


def test_pinned_public_sources_state_exact_ground_truth_boundary() -> None:
    edith = _json(PROFILE / "datasets" / "edith.dataset.json")
    ops = _json(PROFILE / "datasets" / "enterpriseops-gym.dataset.json")
    labels = _json(PROFILE / "source-labels" / "edith-proc-01.json")

    assert edith["revision"] == "844264a930674feacf6dee1844da77b0c4d66b2a"
    assert ops["revision"] == "c8e538eae8a6205294f0a86675fefdc1fac408f6"
    assert edith["license"] == ops["license"] == "Apache-2.0"
    assert edith["retrievalMode"] == "metadata-only"
    assert "by_entity.tar.gz" in edith["excludedAssets"]
    assert labels["answerKey"]["groundTruth"] == {}  # type: ignore[index]
    assert labels["oacGroundTruthStatus"] == "ABSENT"

    rows = labels["masterIndexLabels"]
    assert len(rows) == 19  # type: ignore[arg-type]
    assert Counter(row["classification"] for row in rows) == {  # type: ignore[union-attr]
        "AT_RISK": 8,
        "NOT_AT_RISK": 4,
        "REFERENCE": 6,
        "SUMMARY": 1,
    }


def test_ten_cases_are_strict_exploratory_resources_not_human_gold() -> None:
    paths = _case_paths()
    assert len(paths) == 10
    seen: set[str] = set()
    for path in paths:
        case = _resource(path, OrgChangeCase)
        assert isinstance(case, OrgChangeCase)
        assert case.spec.annotation_status == "exploratory"
        assert case.spec.outcome_observability == "none"
        assert case.metadata.id not in seen
        seen.add(case.metadata.id)
        assert case.spec.constraint_set is not None
        assert case.spec.constraint_set.required_obligation_types
        if case.metadata.id == "case:SC-001":
            assert case.spec.constraint_set.required_obligation_refs
        else:
            assert case.spec.constraint_set.required_obligation_refs == ()
        assert case.spec.annotations
        for annotation in case.spec.annotations:
            if annotation.field.startswith("oac."):
                assert annotation.status != "source_gt"
            if annotation.status == "source_gt":
                assert annotation.field.startswith("source.")

    index = _json(PROFILE / "cases" / "index.json")
    assert index["humanGroundTruthClaimed"] is False
    assert index["annotationStatus"] == "exploratory"


def test_case_roots_and_project_annotation_references_are_closed() -> None:
    allowed_external = ("hf:", "edith:", "authority:", "oac:", "spec:")
    for path in _case_paths():
        case = _resource(path, OrgChangeCase)
        assert isinstance(case, OrgChangeCase)
        case_id = case.metadata.id.removeprefix("case:")
        packet = PROFILE / "annotations" / path.name
        assert packet.is_file()

        packet_data = _json(packet)
        snapshot_path = (packet.parent / packet_data["frozenInputs"]["snapshotRef"]).resolve()  # type: ignore[index]
        assert snapshot_path.is_relative_to(PROFILE.resolve())
        change_path = PROFILE / "inputs" / f"{case_id}.change.json"
        snapshot = _resource(snapshot_path, OrganizationSnapshot)
        change = _resource(change_path, SemanticChangeSet)
        assert case.spec.snapshot_ref == resource_ref(snapshot)  # type: ignore[arg-type]
        assert case.spec.change_ref == resource_ref(change)  # type: ignore[arg-type]

        refs = [*case.metadata.source_refs]
        refs.extend(
            evidence
            for annotation in case.spec.annotations
            for evidence in annotation.evidence_refs
        )
        for ref in refs:
            if ref.startswith("profiles/"):
                assert (ROOT / ref).is_file(), ref
            else:
                assert ref.startswith(allowed_external), ref


def test_encoded_counterfactuals_match_their_frozen_change_delta() -> None:
    for packet_path in sorted((PROFILE / "annotations").glob("SC-*.json")):
        packet = _json(packet_path)
        case_id = packet["caseId"]
        change = _resource(
            PROFILE / "inputs" / f"{case_id}.change.json", SemanticChangeSet
        )
        assert isinstance(change, SemanticChangeSet)
        counterfactual = packet["counterfactual"]
        assert counterfactual["factor"] == "supplier.status"  # type: ignore[index]
        delta = next(item for item in change.spec.deltas if item.path == "/status")
        assert str(counterfactual["before"]) == _observed_token(delta.before)  # type: ignore[index]
        assert str(counterfactual["after"]) == _observed_token(delta.after)  # type: ignore[index]

        strict_case = _resource(PROFILE / "cases" / packet_path.name, OrgChangeCase)
        assert isinstance(strict_case, OrgChangeCase)
        encoded = next(
            annotation
            for annotation in strict_case.spec.annotations
            if annotation.field == "oac.counterfactual.supplier.status"
        )
        assert encoded.value == f"{_observed_token(delta.before)}->{_observed_token(delta.after)}"


def test_transfer_predicates_align_candidate_role_sets_for_first_seven_cases() -> None:
    snapshot = _resource(
        PROFILE / "inputs" / "veracier-proc01.snapshot.json", OrganizationSnapshot
    )
    assert isinstance(snapshot, OrganizationSnapshot)
    for number in range(1, 8):
        case_id = f"SC-{number:03}"
        packet_path = next((PROFILE / "annotations").glob(f"{case_id}-*.json"))
        packet = _json(packet_path)
        change = _resource(PROFILE / "inputs" / f"{case_id}.change.json", SemanticChangeSet)
        assert isinstance(change, SemanticChangeSet)
        plan = compile_supplier_change(snapshot, change)
        actual = {item.role_definition_ref for item in plan.spec.role_instances}
        expected = set(packet["candidateConstraints"]["admissibleRoleRefs"])  # type: ignore[index]
        assert actual == expected, case_id

    healthy = _resource(PROFILE / "inputs" / "SC-002.change.json", SemanticChangeSet)
    assert isinstance(healthy, SemanticChangeSet)
    healthy_plan = compile_supplier_change(snapshot, healthy)
    assert len(healthy_plan.spec.obligations) == 1
    assert healthy_plan.spec.obligations[0].obligation_type == "status-verification"


def test_unknown_state_is_not_encoded_as_a_business_value_rule() -> None:
    for path in sorted((PROFILE / "inputs").glob("*.snapshot.json")):
        snapshot = _resource(path, OrganizationSnapshot)
        assert isinstance(snapshot, OrganizationSnapshot)
        for rule in snapshot.spec.impact_rules:
            after_values = (
                rule.applicability.after_values
                if hasattr(rule, "applicability")
                else rule.after_values
            )
            assert "unknown" not in after_values, rule.rule_id

    # Unknown is a value-state, not the literal business value "unknown". The
    # compiler must route it through Unknown impact paths and completeness-led
    # discovery instead of a value-matching impact rule.
    change = _resource(PROFILE / "inputs" / "SC-010.change.json", SemanticChangeSet)
    assert isinstance(change, SemanticChangeSet)
    status_delta = next(item for item in change.spec.deltas if item.path == "/status")
    assert status_delta.after.state == "unknown"
    assert status_delta.after.value is None


def test_plural_case_accepts_two_distinct_topologies_against_exact_roots() -> None:
    snapshot = _resource(
        PROFILE / "inputs" / "veracier-proc01.snapshot.json", OrganizationSnapshot
    )
    change = _resource(PROFILE / "inputs" / "SC-001.change.json", SemanticChangeSet)
    witness_a = _resource(
        PROFILE / "witnesses" / "SC-001-witness-a.plan.json", OrganizationPlan
    )
    witness_b = _resource(
        PROFILE / "witnesses" / "SC-001-witness-b.plan.json", OrganizationPlan
    )
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)
    assert isinstance(witness_a, OrganizationPlan)
    assert isinstance(witness_b, OrganizationPlan)

    assert witness_a.digest != witness_b.digest
    assert witness_a.spec.snapshot_ref == witness_b.spec.snapshot_ref == resource_ref(snapshot)
    assert witness_a.spec.change_ref == witness_b.spec.change_ref == resource_ref(change)
    assert witness_a.spec.obligations == witness_b.spec.obligations
    assert witness_a.spec.role_instances == witness_b.spec.role_instances
    assert len(witness_a.spec.work_units) == 5
    assert len(witness_b.spec.work_units) == 4
    assert verify_plan(snapshot, change, witness_a).spec.verdict is Verdict.ACCEPT
    assert verify_plan(snapshot, change, witness_b).spec.verdict is Verdict.ACCEPT

    case = _resource(PROFILE / "cases" / "SC-001-restructuring.json", OrgChangeCase)
    assert isinstance(case, OrgChangeCase)
    assert case.spec.witness_refs == (resource_ref(witness_a), resource_ref(witness_b))
    assert set(case.spec.constraint_set.required_obligation_refs) == {  # type: ignore[union-attr]
        item.obligation_id for item in witness_a.spec.obligations
    }


def test_baselines_bind_one_exact_input_set_and_have_transparent_machine_policies() -> None:
    matched = _json(ROOT / "benchmark" / "matched-inputs.json")
    projection = dict(matched)
    claimed = projection.pop("digest")
    actual = f"sha256:{hashlib.sha256(rfc8785.dumps(projection)).hexdigest()}"
    assert claimed == actual
    assert matched["caseCount"] == 10
    for item in matched["cases"]:  # type: ignore[union-attr]
        case = _resource(ROOT / item["caseRef"], OrgChangeCase)
        assert case.digest == item["caseDigest"]  # type: ignore[attr-defined]

    baselines = [_json(path) for path in sorted((ROOT / "benchmark" / "baselines").glob("*.json"))]
    assert {item["baselineId"] for item in baselines} == {
        "initiator-only",
        "fixed-team",
        "graph-only",
    }
    assert {item["inputSetDigest"] for item in baselines} == {claimed}
    assert {item["executionStatus"] for item in baselines} == {"mechanics-run-available"}
    assert {item["policy"]["machinePolicy"] for item in baselines} == {
        "subject-owner-only",
        "fixed-role-set",
        "admitted-propagating-reachability",
    }


def test_frozen_run_manifests_are_closed_behavior_repeatable_and_candidate_only() -> None:
    first = build_run_manifests(ROOT)
    second = build_run_manifests(ROOT)
    assert first == second
    assert set(first) == {
        "oac-reference-compiler",
        "initiator-only",
        "fixed-team",
        "graph-only",
    }
    for system_id, expected in first.items():
        frozen = _json(ROOT / "benchmark" / "runs" / f"{system_id}.run.json")
        # The published manifest keeps the implementation bytes that produced
        # the historical run.  A successor source tree must reproduce its
        # complete behavioral projection; it must not re-sign the old byte
        # identity as though the implementation had never changed.
        frozen_projection = dict(frozen)
        frozen_digest = frozen_projection.pop("digest")
        assert frozen_digest == f"sha256:{hashlib.sha256(rfc8785.dumps(frozen_projection)).hexdigest()}"
        assert _benchmark_behavioral_projection(frozen) == (
            _benchmark_behavioral_projection(expected)
        )
        assert frozen["inputSetDigest"]
        assert frozen["inputClosureDigest"] == frozen["inputClosure"]["digest"]
        assert frozen["fixtureSetDigest"]
        assert frozen["implementationDigest"] == frozen["implementationClosure"]["digest"]
        assert frozen["inputClosure"]["digest"] == (
            f"sha256:{hashlib.sha256(rfc8785.dumps(frozen['inputClosure']['entries'])).hexdigest()}"
        )
        assert frozen["implementationClosure"]["digest"] == (
            f"sha256:{hashlib.sha256(rfc8785.dumps(frozen['implementationClosure']['entries'])).hexdigest()}"
        )
        assert frozen["digest"]
        assert len(frozen["results"]) == 10
        assert "project-authored-candidate-labels" in frozen["evaluationClass"]
        assert all(item["outputDigest"] for item in frozen["results"])

        input_paths = {item["path"] for item in frozen["inputClosure"]["entries"]}
        assert "benchmark/matched-inputs.json" in input_paths
        assert len(input_paths & {path.relative_to(ROOT).as_posix() for path in _case_paths()}) == 10
        assert len(
            input_paths
            & {
                path.relative_to(ROOT).as_posix()
                for path in (PROFILE / "annotations").glob("SC-*.json")
            }
        ) == 10
        snapshot_inputs = {
            path.relative_to(ROOT).as_posix()
            for path in (PROFILE / "inputs").glob("*.snapshot.json")
        }
        assert input_paths & snapshot_inputs == snapshot_inputs
        assert len(
            input_paths
            & {
                path.relative_to(ROOT).as_posix()
                for path in (PROFILE / "inputs").glob("SC-*.change.json")
            }
        ) == 10

        implementation_paths = {
            item["path"] for item in frozen["implementationClosure"]["entries"]
        }
        assert "src/oac/benchmark.py" in implementation_paths
        assert "scripts/run_benchmark.py" in implementation_paths
        assert "pyproject.toml" in implementation_paths
        assert "uv.lock" in implementation_paths
        if system_id == "oac-reference-compiler":
            assert "src/oac/applicability.py" in implementation_paths
            assert "src/oac/registry.py" in implementation_paths
            assert frozen["metrics"]["requiredObligationTypeCoverageMatches"] == 5
            assert frozen["metrics"]["requiredObligationTypeGapCaseIds"] == [
                "SC-003",
                "SC-004",
                "SC-005",
                "SC-006",
                "SC-007",
            ]
            assert frozen["metrics"]["candidateVerdictMatches"] == 10
            assert frozen["metrics"]["forbiddenObligationTypeViolationCases"] == 0
        else:
            assert frozen["configurationRef"] in implementation_paths


def test_annotation_byte_mutation_changes_published_input_closure_and_manifest(
    tmp_path: Path,
) -> None:
    sandbox = _benchmark_sandbox(tmp_path)
    before = build_run_manifests(sandbox)
    annotation_path = (
        sandbox / "profiles/supplier-change/annotations/SC-001-restructuring.json"
    )
    annotation_path.write_bytes(annotation_path.read_bytes() + b" ")
    after = build_run_manifests(sandbox)

    for system_id in before:
        assert before[system_id]["inputSetDigest"] == after[system_id]["inputSetDigest"]
        assert before[system_id]["inputClosureDigest"] != after[system_id]["inputClosureDigest"]
        assert before[system_id]["fixtureSetDigest"] != after[system_id]["fixtureSetDigest"]
        assert before[system_id]["metrics"] == after[system_id]["metrics"]
        assert before[system_id]["digest"] != after[system_id]["digest"]


def test_scorer_byte_mutation_changes_every_manifest_implementation_digest(
    tmp_path: Path,
) -> None:
    sandbox = _benchmark_sandbox(tmp_path)
    before = build_run_manifests(sandbox)
    scorer = sandbox / "src/oac/benchmark.py"
    scorer.write_bytes(scorer.read_bytes() + b"\n# scorer mutation\n")
    after = build_run_manifests(sandbox)

    for system_id in before:
        assert before[system_id]["inputClosureDigest"] == after[system_id]["inputClosureDigest"]
        assert before[system_id]["implementationDigest"] != after[system_id][
            "implementationDigest"
        ]
        assert before[system_id]["metrics"] == after[system_id]["metrics"]
        assert before[system_id]["digest"] != after[system_id]["digest"]


def test_readiness_report_keeps_human_and_profile_gates_open() -> None:
    report = _json(ROOT / "benchmark" / "data-readiness.json")
    assert report["overallStatus"] == "NOT_READY_FOR_HUMAN_GOLD_OR_ENTERPRISE_CLAIMS"
    assert report["annotationEvidence"]["independentQualifiedHumanAnnotators"] == 0  # type: ignore[index]
    gates = {item["gateId"]: item for item in report["gates"]}  # type: ignore[union-attr]
    assert gates["G3_STRUCTURAL_NEGATIVE_DETECTION"]["status"] == "PASS_MECHANICS_ONLY"
    assert (
        gates["G4_COUNTERFACTUAL_SENSITIVITY"]["status"]
        == "PARTIAL_PASS_ROLE_VERDICT_OPEN_OBLIGATION_TYPES"
    )
    assert gates["G4_COUNTERFACTUAL_SENSITIVITY"]["openObligationTypeCaseIds"] == [
        "SC-003",
        "SC-004",
        "SC-005",
        "SC-006",
        "SC-007",
    ]
    assert gates["G7_MATCHED_BASELINES"]["status"] == "PASS_MECHANICS_ONLY"
