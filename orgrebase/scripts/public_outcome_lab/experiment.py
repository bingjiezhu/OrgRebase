"""Preparation, matched execution and offline verification through one OutcomeLab."""

from __future__ import annotations

import importlib.metadata
import platform
import shutil
from pathlib import Path

from orgrebase.digest import sha256_digest
from orgrebase.workspace.outcome_lab import (
    SYSTEMS,
    LabBudget,
    LabToolGrant,
    LabToolRequest,
    OACPlanGate,
    OutcomeLab,
    OutcomeOracle,
    OutcomeTaskMapping,
    verify_outcome_receipt,
)

from .adapter import TauRetailEnvironment
from .artifacts import (
    PROFILE,
    encode,
    file_identity,
    load,
    seal_directory,
    verify_directory,
    verify_source,
    write,
)
from .baselines import verify_baseline_roles
from .task import SCOPE, TASK_ID, UNSUPPORTED, DiscoveryReplay, oracle_expectations, public_candidate

CONTROLLER = "scripted:controller:disposable-lab"
ACTOR = "scripted:actor:pending-orders"
EVALUATOR = "scripted:evaluator:public-task-db"


def implementation_identity() -> dict:
    from orgrebase import digest, domain
    from orgrebase.workspace import (
        oac_wire,
        outcome_contracts,
        outcome_lab,
        outcome_runtime,
        outcome_verification,
    )

    files = {
        "scripts/public_outcome_lab/" + path.name: file_identity(path)
        for path in sorted(Path(__file__).parent.glob("*.py"))
    }
    for module in (
        digest,
        domain,
        oac_wire,
        outcome_lab,
        outcome_contracts,
        outcome_runtime,
        outcome_verification,
    ):
        files[module.__name__] = file_identity(Path(module.__file__))
    return {
        "schema_version": "orgrebase.public-outcome-implementation.v1",
        "bound_components": files,
        "python": platform.python_version(),
        "libraries": {
            name: importlib.metadata.version(name) for name in ("pydantic", "pydantic_core", "rfc8785")
        },
    }


def source_task(source: Path) -> dict:
    tasks = load(source / "data/tau2/domains/retail/tasks.json")
    matches = [item for item in tasks if item["id"] == TASK_ID]
    if len(matches) != 1 or matches[0]["initial_state"] is not None:
        raise ValueError("PUBLIC_LAB_TASK_INITIAL_STATE_UNSUPPORTED")
    return matches[0]


def prepare(source: Path, python: Path, output: Path) -> str:
    provenance = verify_source(source)
    task = source_task(source)
    output.mkdir(exist_ok=False)
    environment = TauRetailEnvironment(source=source, python=python, stderr=output / "worker.stderr.log")
    try:
        environment.reset()
        seed = environment.snapshot()
        candidate = public_candidate(task["user_scenario"], environment)
        if environment.snapshot() != seed:
            raise ValueError("PUBLIC_LAB_DISCOVERY_CHANGED_STATE")
        environment.reset()
        if environment.snapshot() != seed:
            raise ValueError("PUBLIC_LAB_DISCOVERY_RESET_FAILED")
        # Oracle derivation occurs after candidate construction and is not an actor argument.
        oracle = oracle_expectations(task, seed)
        for name, value in (
            ("source", provenance),
            ("runtime", environment.runtime),
            ("public-task", task),
            ("candidate", candidate),
            ("seed-state", seed),
            ("oracle-expectations", oracle),
        ):
            write(output / (name + ".json"), value)
    finally:
        environment.close()
    return seal_directory(output, "prepared-task")


def verify_prepared(prepared: Path, expected_root: str, source: Path, environment) -> dict:
    verify_directory(prepared, expected_root, "prepared-task")
    if load(prepared / "source.json") != verify_source(source) or load(
        prepared / "public-task.json"
    ) != source_task(source):
        raise ValueError("PUBLIC_LAB_PREPARED_UPSTREAM_MISMATCH")
    if load(prepared / "runtime.json") != environment.runtime:
        raise ValueError("PUBLIC_LAB_RUNTIME_IDENTITY_MISMATCH")
    environment.reset()
    seed = environment.snapshot()
    if seed != load(prepared / "seed-state.json"):
        raise ValueError("PUBLIC_LAB_NORMALIZED_SEED_MISMATCH")
    task = load(prepared / "public-task.json")
    if oracle_expectations(task, seed) != load(prepared / "oracle-expectations.json"):
        raise ValueError("PUBLIC_LAB_ORACLE_SOURCE_MISMATCH")
    candidate = load(prepared / "candidate.json")
    reader = DiscoveryReplay(candidate["discovery"])
    if public_candidate(task["user_scenario"], reader) != candidate or reader.index != len(reader.rows):
        raise ValueError("PUBLIC_LAB_CANDIDATE_DISCOVERY_MISMATCH")
    return candidate


def check_input_sources(inputs: Path, expected_root: str, prepared_root: str, oac_source: Path) -> None:
    verify_directory(inputs, expected_root, "oac-inputs")
    producer = load(inputs / "producer.json")
    if producer["profile"] != PROFILE or producer["prepared_root"] != prepared_root:
        raise ValueError("PUBLIC_LAB_INPUT_PROVENANCE_MISMATCH")
    expected_names = {p.relative_to(oac_source).as_posix() for p in (oac_source / "src/oac").rglob("*.py")}
    expected_names.add("profiles/change-profiles/retail-cancellation-review-v0.1/profile.json")
    if set(producer["oac_sources"]) != expected_names:
        raise ValueError("PUBLIC_LAB_OAC_SOURCE_CLOSURE_MISMATCH")
    for name, expected in producer["oac_sources"].items():
        if file_identity(oac_source / name) != expected:
            raise ValueError("PUBLIC_LAB_OAC_SOURCE_MISMATCH:" + name)


def public_gate(python: Path, source: Path) -> OACPlanGate:
    return OACPlanGate(
        (str(python.absolute()), "-B", "-m", "oac.cli"),
        environment={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(source.resolve() / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )


def map_plan(
    prepared: Path, inputs: Path, build_digest: str
) -> tuple[OutcomeTaskMapping, OutcomeOracle, tuple[LabToolRequest, ...]]:
    task = load(prepared / "public-task.json")
    candidate = load(prepared / "candidate.json")
    snapshot = load(inputs / "snapshot.json")
    change = load(inputs / "change.json")
    plan = load(inputs / "plan.json")
    requests = tuple(LabToolRequest.model_validate(item) for item in candidate["requests"])
    oracle = OutcomeOracle(
        authority=EVALUATOR,
        policy_source_digest=sha256_digest(task["evaluation_criteria"]),
        expected=load(prepared / "oracle-expectations.json"),
    )
    obligations = {item["targetRef"]: item for item in plan["spec"]["obligations"]}
    work_units = plan["spec"]["workUnits"]
    predecessors = {unit["workUnitId"]: [] for unit in work_units}
    for edge in plan["spec"]["happensBefore"]:
        predecessors[edge["successorRef"]].append(edge["predecessorRef"])
    grants = []
    for request in requests:
        write_call = request.tool == "cancel_pending_order"
        target = "order:" + request.arguments["order_id"].removeprefix("#W") if write_call else "request:113"
        obligation = obligations[target]
        units = [unit for unit in work_units if obligation["obligationId"] in unit["obligationRefs"]]
        if len(units) != 1 or not obligation["requiredEvidence"]:
            raise ValueError("PUBLIC_LAB_EXACT_WORK_UNIT_REQUIRED")
        grants.append(
            LabToolGrant(
                request=request,
                role=obligation["requiredRoleRef"],
                obligation_ref=obligation["obligationId"],
                work_unit_ref=units[0]["workUnitId"],
                evidence_ref=obligation["requiredEvidence"][0],
                mutates_state=write_call,
            )
        )
    provenance = load(prepared / "source.json")
    subjects = {"request:113": "/users/" + candidate["user_id"]}
    subjects.update(
        {"order:" + value.removeprefix("#W"): "/orders/" + value for value in candidate["pending_orders"]}
    )
    mapping = OutcomeTaskMapping(
        upstream_repository=provenance["repository"],
        upstream_revision=provenance["revision"],
        task_id=TASK_ID,
        oac_profile=PROFILE,
        task_digest=sha256_digest(task),
        environment_build_digest=build_digest,
        seed_root=sha256_digest(load(prepared / "seed-state.json")),
        snapshot_digest=snapshot["digest"],
        change_digest=change["digest"],
        plan_digest=plan["digest"],
        subjects=subjects,
        grants=tuple(grants),
        work_unit_predecessors={key: tuple(sorted(value)) for key, value in predecessors.items()},
        system_roles=verify_baseline_roles(snapshot, plan, load(inputs / "baselines.json")),
        writable_paths=tuple(sorted(oracle.expected)),
        required_evidence=tuple(sorted({grant.evidence_ref for grant in grants})),
        oracle_digest=oracle.digest,
        unsupported=UNSUPPORTED,
        budget=LabBudget(tool_calls=12, write_calls=2, elapsed_seconds=120),
    )
    return mapping, oracle, requests


def _compare_repetitions(results: list[dict]) -> None:
    for system in SYSTEMS:
        first, second = [value for value in results if value["system"] == system]
        for field in ("initial_root", "final_root", "trace_digest", "dimensions", "verdict"):
            if first[field] != second[field]:
                raise ValueError("PUBLIC_LAB_REPETITION_MISMATCH:" + system + ":" + field)


def experiment_summary(results: list[dict], candidate: dict, prepared_root: str, inputs_root: str) -> dict:
    return {
        "schema_version": "orgrebase.public-outcome-summary.v1",
        "scope": SCOPE,
        "task_id": TASK_ID,
        "prepared_root": prepared_root,
        "inputs_root": inputs_root,
        "matched_runs": len(results),
        "replays_identical": True,
        "all_failures_retained": True,
        "actual_llm_calls": 0,
        "real_external_effects": 0,
        "prepared_candidate_discovery_calls": len(candidate["discovery"]),
        "unsupported": list(UNSUPPORTED),
        "results": [
            {
                key: receipt[key]
                for key in ("system", "verdict", "final_root", "tool_calls", "write_calls", "dimensions")
            }
            for receipt in results
        ],
    }


def run(
    *,
    source: Path,
    python: Path,
    prepared: Path,
    prepared_root: str,
    inputs: Path,
    inputs_root: str,
    oac_python: Path,
    oac_source: Path,
    output: Path,
) -> str:
    check_input_sources(inputs, inputs_root, prepared_root, oac_source)
    output.mkdir(exist_ok=False)
    environment = TauRetailEnvironment(source=source, python=python, stderr=output / "worker.stderr.log")
    try:
        candidate = verify_prepared(prepared, prepared_root, source, environment)
        write(output / "implementation.json", implementation_identity())
        mapping, oracle, requests = map_plan(prepared, inputs, environment.build_digest)
        lab = OutcomeLab(
            mapping,
            oracle,
            environment,
            public_gate(oac_python, oac_source),
            snapshot=load(inputs / "snapshot.json"),
            change=load(inputs / "change.json"),
            plan=load(inputs / "plan.json"),
            controller_authority=CONTROLLER,
            acting_authority=ACTOR,
        )
        shutil.copytree(prepared, output / "prepared")
        shutil.copytree(inputs, output / "inputs")
        for name, value in (
            ("mapping", mapping.model_dump(mode="json")),
            ("oracle", oracle.model_dump(mode="json")),
            ("plan-certificate", lab.plan_certificate),
        ):
            write(output / (name + ".json"), value)
        results = []
        for repetition in range(2):
            for system in SYSTEMS:
                approval = lab.approve(system, authority=CONTROLLER)
                receipt = lab.run(approval["token"], requests, runtime_bundle=approval["runtime_bundle"])
                lab.verify_receipt(receipt)
                write(output / f"{system}-{repetition}.json", receipt)
                results.append(receipt)
        _compare_repetitions(results)
        approval = lab.approve("oac", authority=CONTROLLER)
        counterexample = lab.run(approval["token"], requests[:-1], runtime_bundle=approval["runtime_bundle"])
        lab.verify_receipt(counterexample)
        if counterexample["verdict"] != "REJECT" or counterexample["dimensions"]["task_goal"] != "FAIL":
            raise ValueError("PUBLIC_LAB_ACCEPT_PLAN_FAILURE_COUNTEREXAMPLE_MISSING")
        write(output / "accepted-plan-failed-outcome.json", counterexample)
        summary = experiment_summary(results, candidate, prepared_root, inputs_root)
        write(output / "summary.json", summary)
    finally:
        environment.close()
    return seal_directory(output, "matched-experiment")


def replay(
    *,
    bundle: Path,
    expected_root: str,
    source: Path,
    python: Path,
    oac_python: Path,
    oac_source: Path,
    output: Path,
) -> str:
    verify_directory(bundle, expected_root, "matched-experiment")
    summary = load(bundle / "summary.json")
    if load(bundle / "implementation.json") != implementation_identity():
        raise ValueError("PUBLIC_LAB_EXECUTION_IMPLEMENTATION_MISMATCH")
    if (
        summary.get("schema_version") != "orgrebase.public-outcome-summary.v1"
        or summary.get("scope") != SCOPE
    ):
        raise ValueError("PUBLIC_LAB_SUMMARY_SCOPE_INVALID")
    prepared, inputs = bundle / "prepared", bundle / "inputs"
    check_input_sources(inputs, summary["inputs_root"], summary["prepared_root"], oac_source)
    output.mkdir(exist_ok=False)
    environment = TauRetailEnvironment(source=source, python=python, stderr=output / "worker.stderr.log")
    try:
        candidate = verify_prepared(prepared, summary["prepared_root"], source, environment)
        mapping, oracle, _ = map_plan(prepared, inputs, environment.build_digest)
        if mapping.model_dump(mode="json") != load(bundle / "mapping.json") or oracle.model_dump(
            mode="json"
        ) != load(bundle / "oracle.json"):
            raise ValueError("PUBLIC_LAB_EXACT_MAPPING_ORACLE_MISMATCH")
        certificate = public_gate(oac_python, oac_source).verify(
            mapping,
            snapshot=load(inputs / "snapshot.json"),
            change=load(inputs / "change.json"),
            plan=load(inputs / "plan.json"),
        )
        if certificate != load(bundle / "plan-certificate.json"):
            raise ValueError("PUBLIC_LAB_PLAN_CERTIFICATE_MISMATCH")
        results = []
        for repetition in range(2):
            for system in SYSTEMS:
                receipt = load(bundle / f"{system}-{repetition}.json")
                if receipt["system"] != system:
                    raise ValueError("PUBLIC_LAB_SYSTEM_RECEIPT_MISMATCH")
                verify_outcome_receipt(
                    receipt,
                    mapping=mapping,
                    oracle=oracle,
                    plan_certificate_digest=certificate["digest"],
                    controller_authority=CONTROLLER,
                    acting_authority=ACTOR,
                )
                results.append(receipt)
        _compare_repetitions(results)
        if encode(summary) != encode(
            experiment_summary(results, candidate, summary["prepared_root"], summary["inputs_root"])
        ):
            raise ValueError("PUBLIC_LAB_SUMMARY_RECEIPT_MISMATCH")
        failure = load(bundle / "accepted-plan-failed-outcome.json")
        verify_outcome_receipt(
            failure,
            mapping=mapping,
            oracle=oracle,
            plan_certificate_digest=certificate["digest"],
            controller_authority=CONTROLLER,
            acting_authority=ACTOR,
        )
        if (
            failure["system"] != "oac"
            or failure["verdict"] != "REJECT"
            or failure["dimensions"]["task_goal"] != "FAIL"
        ):
            raise ValueError("PUBLIC_LAB_FAILURE_COUNTEREXAMPLE_INVALID")
        write(
            output / "verification.json",
            {
                "status": "PASS",
                "scope": SCOPE,
                "bundle_root": expected_root,
                "receipt_count": 9,
                "task_tools_reexecuted": 0,
                "seed_normalization": "SAME_PINNED_UPSTREAM_RETAILDB",
                "verification": "SOURCE_AND_INPUT_ROOTS_PUBLIC_PLAN_CERTIFICATE_AND_CORE_RECEIPT_REASSESSMENT",
                "actual_llm_calls": 0,
                "real_external_effects": 0,
            },
        )
    finally:
        environment.close()
    return seal_directory(output, "offline-verification")
