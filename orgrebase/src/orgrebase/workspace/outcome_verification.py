"""Pure replay of retained state evidence against caller-pinned experiment inputs."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.outcome_contracts import (
    DIMENSIONS,
    SYSTEMS,
    LabToolRequest,
    OutcomeOracle,
    OutcomeTaskMapping,
)
from orgrebase.workspace.outcome_runtime import verify_runtime_bundle


def _pointer(value: Any, path: str) -> Any:
    for part in path.split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def _frozen_json(value: Any) -> Any:
    """Sever adapter-owned aliases before retaining any observation."""
    return json.loads(json.dumps(value, allow_nan=False))


def _changes(before: Any, after: Any, prefix: str = "") -> tuple[str, ...]:
    if type(before) is not type(after):
        return (prefix,)
    if isinstance(before, dict):
        paths = []
        for key in sorted(before.keys() | after.keys()):
            path = prefix + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in before or key not in after:
                paths.append(path)
            else:
                paths.extend(_changes(before[key], after[key], path))
        return tuple(paths)
    if before != after:
        return (prefix,)
    return ()


def _completed_work_units(mapping: OutcomeTaskMapping, completed_requests: set[str]) -> set[str]:
    return {
        unit
        for unit in mapping.work_unit_predecessors
        if {grant.request.digest for grant in mapping.grants if grant.work_unit_ref == unit}
        <= completed_requests
    }


def _assess_outcome(
    mapping: OutcomeTaskMapping,
    oracle: OutcomeOracle,
    *,
    trace: Sequence[Mapping[str, Any]],
    states: Mapping[str, Any],
    initial_root: str | None,
    final_root: str | None,
    reset: Mapping[str, Any],
    approval: Mapping[str, Any],
    unresolved: Sequence[str],
) -> dict[str, Any]:
    grants = {grant.request.digest: grant for grant in mapping.grants}
    initial = states.get(initial_root) if initial_root is not None else None
    final = states.get(final_root) if final_root is not None else None
    changed = set(_changes(initial, final)) if initial is not None and final is not None else set()
    readonly_effects = []
    completed_requests = set()
    state_complete = initial is not None and final is not None
    previous_root = initial_root
    unattributed = False
    for item in trace:
        before_root = item["before_root"]
        if previous_root is not None and before_root is not None and previous_root != before_root:
            changed.update(_changes(states[previous_root], states[before_root]))
            unattributed = True
        if not item["dispatched"] and item["changed_paths"]:
            unattributed = True
        if item["changed_paths"] is None:
            state_complete = False
        else:
            changed.update(item["changed_paths"])
        grant = grants.get(LabToolRequest.model_validate(item["request"]).digest)
        if grant is not None and item["dispatched"]:
            if not grant.mutates_state and item["changed_paths"]:
                readonly_effects.append(item["index"])
            if item["status"] == "COMPLETED":
                completed_requests.add(grant.request.digest)
        previous_root = item["after_root"]
    if previous_root is not None and final_root is not None and previous_root != final_root:
        changed.update(_changes(states[previous_root], states[final_root]))
        unattributed = True
    forbidden = sorted(
        path
        for path in changed
        if not any(path == allowed or path.startswith(allowed + "/") for allowed in mapping.writable_paths)
    )
    observed_evidence = {
        evidence
        for evidence in mapping.required_evidence
        if {grant.request.digest for grant in mapping.grants if grant.evidence_ref == evidence}
        <= completed_requests
    }
    goals = []
    for path, expected in oracle.expected.items():
        try:
            actual = _pointer(final, path)
            passed = type(actual) is type(expected) and actual == expected
            goals.append({"path": path, "expected": expected, "actual": actual, "passed": passed})
        except (KeyError, IndexError, TypeError, ValueError):
            goals.append({"path": path, "expected": expected, "passed": False, "missing": True})
    effect_violation = bool(forbidden or readonly_effects or unattributed)
    dimensions = {
        "task_goal": "UNKNOWN"
        if any(item.get("missing") for item in goals)
        else "PASS"
        if all(item["passed"] for item in goals)
        else "FAIL",
        "forbidden_effects": "FAIL" if effect_violation else "PASS" if state_complete else "UNKNOWN",
        "evidence_completion": "PASS" if set(mapping.required_evidence) <= observed_evidence else "FAIL",
        "scope": "FAIL"
        if effect_violation or any(item["status"] == "DENIED" for item in trace)
        else "PASS"
        if state_complete
        else "UNKNOWN",
        "replayability": "UNKNOWN"
        if reset["state_root"] is None
        else "PASS"
        if (
            reset["state_root"] == mapping.seed_root
            and reset["snapshot_id"] is not None
            and reset["snapshot_id"] != approval["snapshot_id"]
        )
        else "FAIL",
        "unresolved_observations": "UNKNOWN"
        if unresolved or any(item.get("missing") for item in goals)
        else "PASS",
    }
    verdict = (
        "REJECT"
        if "FAIL" in dimensions.values()
        else "UNKNOWN"
        if "UNKNOWN" in dimensions.values()
        else "ACCEPT"
    )

    return {
        "goals": goals,
        "forbidden_paths": forbidden,
        "readonly_effects": readonly_effects,
        "observed_evidence": sorted(observed_evidence),
        "dimensions": dimensions,
        "verdict": verdict,
    }


def verify_outcome_receipt(
    receipt: Mapping[str, Any],
    *,
    mapping: OutcomeTaskMapping,
    oracle: OutcomeOracle,
    plan_certificate_digest: str,
    controller_authority: str,
    acting_authority: str,
) -> None:
    """Recompute retained observations against separately trusted experiment inputs.

    This verifies consistency and identity, not a signature or a human identity.
    The host must obtain the expected pins and authorities through its own trusted
    intake. A receipt cannot supply its own admission context.
    """
    try:
        _verify_outcome_receipt(
            receipt,
            mapping=mapping.revalidated(),
            oracle=oracle.revalidated(),
            plan_certificate_digest=plan_certificate_digest,
            controller_authority=controller_authority,
            acting_authority=acting_authority,
        )
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise IntegrityError("LAB_OUTCOME_MALFORMED") from exc


def _verify_outcome_receipt(
    receipt: Mapping[str, Any],
    *,
    mapping: OutcomeTaskMapping,
    oracle: OutcomeOracle,
    plan_certificate_digest: str,
    controller_authority: str,
    acting_authority: str,
) -> None:
    def require(condition: bool, code: str) -> None:
        if not condition:
            raise IntegrityError(code)

    def same(left: Any, right: Any) -> bool:
        return sha256_digest(left) == sha256_digest(right)

    def duration(value: Any) -> bool:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0

    payload = _frozen_json(dict(receipt))
    digest = payload.pop("digest", None)
    require(digest == sha256_digest(payload), "LAB_OUTCOME_DIGEST_MISMATCH")
    require(
        set(payload)
        == {
            "schema_version",
            "mapping_digest",
            "oracle_digest",
            "plan_certificate_digest",
            "approval_receipt",
            "runtime_bundle",
            "system",
            "acting_authority",
            "oracle_authority",
            "initial_root",
            "final_root",
            "state_observations",
            "trace",
            "trace_digest",
            "tool_timings",
            "tool_calls",
            "dispatched_calls",
            "denied_calls",
            "write_calls",
            "elapsed_seconds",
            "unresolved",
            "reset_receipt",
            "goals",
            "forbidden_paths",
            "readonly_effects",
            "observed_evidence",
            "dimensions",
            "verdict",
            "claim_scope",
        },
        "LAB_OUTCOME_FIELDS_MISMATCH",
    )
    require(
        payload["schema_version"] == "orgrebase.outcome-lab.certificate.v2"
        and payload["claim_scope"] == "DISPOSABLE_LOCAL_STATE_EXPERIMENT",
        "LAB_OUTCOME_PROFILE_MISMATCH",
    )
    require(
        len({controller_authority, acting_authority, oracle.authority}) == 3
        and all((controller_authority, acting_authority, oracle.authority)),
        "LAB_AUTHORITY_SEPARATION_REQUIRED",
    )
    require(
        payload["mapping_digest"] == mapping.digest
        and payload["oracle_digest"] == oracle.digest
        and oracle.digest == mapping.oracle_digest
        and payload["plan_certificate_digest"] == plan_certificate_digest,
        "LAB_OUTCOME_INPUT_MISMATCH",
    )
    require(
        payload["acting_authority"] == acting_authority and payload["oracle_authority"] == oracle.authority,
        "LAB_OUTCOME_AUTHORITY_MISMATCH",
    )
    dimensions = payload["dimensions"]
    require(
        set(dimensions) == set(DIMENSIONS)
        and all(v in {"PASS", "FAIL", "UNKNOWN"} for v in dimensions.values()),
        "LAB_SIX_OUTCOME_DIMENSIONS_REQUIRED",
    )
    approval = payload["approval_receipt"]
    require(
        set(approval)
        == {
            "mapping_digest",
            "plan_certificate_digest",
            "system",
            "snapshot_id",
            "seed_root",
            "controller",
            "budget_digest",
            "approved_at_monotonic",
            "recorded_at",
            "digest",
        },
        "LAB_APPROVAL_FIELDS_MISMATCH",
    )
    require(
        approval["digest"] == sha256_digest({k: v for k, v in approval.items() if k != "digest"}),
        "LAB_APPROVAL_DIGEST_MISMATCH",
    )
    require(
        approval["mapping_digest"] == mapping.digest
        and approval["seed_root"] == mapping.seed_root
        and approval["plan_certificate_digest"] == plan_certificate_digest
        and approval["controller"] == controller_authority
        and approval["budget_digest"] == mapping.budget.digest
        and approval["system"] == payload["system"]
        and payload["system"] in SYSTEMS
        and isinstance(approval["snapshot_id"], str)
        and bool(approval["snapshot_id"])
        and duration(approval["approved_at_monotonic"]),
        "LAB_APPROVAL_INPUT_MISMATCH",
    )
    require(
        isinstance(approval["recorded_at"], str) and approval["recorded_at"].endswith("Z"),
        "LAB_APPROVAL_TIME_MALFORMED",
    )
    verify_runtime_bundle(payload["runtime_bundle"], mapping, approval, acting_authority=acting_authority)
    states, trace, reset, unresolved = (
        payload[k] for k in ("state_observations", "trace", "reset_receipt", "unresolved")
    )
    require(
        isinstance(states, dict)
        and all(isinstance(state, dict) and sha256_digest(state) == root for root, state in states.items()),
        "LAB_STATE_ROOT_MISMATCH",
    )
    require(
        set(reset) == {"snapshot_id", "state_root"}
        and (
            reset["snapshot_id"] is None
            or (isinstance(reset["snapshot_id"], str) and bool(reset["snapshot_id"]))
        ),
        "LAB_RESET_RECEIPT_MALFORMED",
    )
    require(
        isinstance(unresolved, list) and all(isinstance(item, str) and item for item in unresolved),
        "LAB_UNRESOLVED_OBSERVATIONS_MALFORMED",
    )
    require(
        isinstance(trace, list)
        and len(trace) <= mapping.budget.tool_calls + 1
        and payload["trace_digest"] == sha256_digest(trace),
        "LAB_TRACE_DIGEST_MISMATCH",
    )
    roots = [payload["initial_root"], payload["final_root"], reset["state_root"]]
    require(payload["initial_root"] in (None, mapping.seed_root), "LAB_APPROVED_SNAPSHOT_CHANGED")
    grants = {grant.request.digest: grant for grant in mapping.grants}
    roles = set(mapping.system_roles[payload["system"]])
    completed: set[str] = set()
    writes = dispatched = 0
    previous_root = payload["initial_root"]
    for index, item in enumerate(trace):
        require(
            set(item)
            == {
                "index",
                "request",
                "dispatched",
                "status",
                "reason",
                "result",
                "before_root",
                "after_root",
                "changed_paths",
            },
            "LAB_TRACE_FIELDS_MISMATCH",
        )
        require(
            type(item["index"]) is int and item["index"] == index and type(item["dispatched"]) is bool,
            "LAB_TRACE_INDEX_MISMATCH",
        )
        request = LabToolRequest.model_validate(item["request"])
        grant = grants.get(request.digest)
        before, after = item["before_root"], item["after_root"]
        roots.extend((before, after))
        require(before is None or before in states, "LAB_OBSERVATION_MISSING")
        require(after is None or after in states, "LAB_OBSERVATION_MISSING")
        require(
            same(
                item["changed_paths"],
                _changes(states[before], states[after]) if before is not None and after is not None else None,
            ),
            "LAB_OBSERVATION_DIFF_MISMATCH",
        )
        if item["dispatched"]:
            require(
                index < mapping.budget.tool_calls and grant is not None and grant.role in roles,
                "LAB_DISPATCH_SCOPE_MISMATCH",
            )
            assert grant is not None
            require(before is not None and before == previous_root, "LAB_STATE_CHAIN_MISMATCH")
            require(
                set(mapping.work_unit_predecessors[grant.work_unit_ref])
                <= _completed_work_units(mapping, completed),
                "LAB_WORK_UNIT_ORDER_MISMATCH",
            )
            writes += int(grant.mutates_state)
            dispatched += 1
            require(writes <= mapping.budget.write_calls, "LAB_WRITE_BUDGET_MISMATCH")
            require(item["status"] in {"COMPLETED", "UNKNOWN"}, "LAB_DISPATCH_STATUS_MISMATCH")
            if item["status"] == "COMPLETED":
                require(item["reason"] is None and after is not None, "LAB_COMPLETION_UNOBSERVABLE")
                completed.add(request.digest)
            else:
                require(
                    bool(item["reason"]) and bool(unresolved) and index == len(trace) - 1,
                    "LAB_UNKNOWN_NOT_RETAINED",
                )
        else:
            require(
                item["status"] == "DENIED" and bool(item["reason"]) and item["result"] is None,
                "LAB_DENIAL_MISMATCH",
            )
        previous_root = after
    require(all(root is None or root in states for root in roots), "LAB_OBSERVATION_MISSING")
    require(set(states) == {root for root in roots if root is not None}, "LAB_UNREFERENCED_OBSERVATION")
    require(all(root is not None for root in roots) or bool(unresolved), "LAB_MISSING_STATE_NOT_UNRESOLVED")
    for key, expected in (
        ("tool_calls", len(trace)),
        ("dispatched_calls", dispatched),
        ("denied_calls", len(trace) - dispatched),
        ("write_calls", writes),
    ):
        require(type(payload[key]) is int and payload[key] == expected, "LAB_TOOL_ACCOUNTING_MISMATCH")
    timings = payload["tool_timings"]
    require(isinstance(timings, list) and len(timings) == dispatched, "LAB_TOOL_TIMING_MISMATCH")
    last_completion = 0
    for timing, item in zip(timings, (item for item in trace if item["dispatched"]), strict=True):
        require(
            set(timing) == {"index", "dispatch_elapsed_seconds", "completion_elapsed_seconds"}
            and type(timing["index"]) is int
            and timing["index"] == item["index"],
            "LAB_TOOL_TIMING_MISMATCH",
        )
        start, end = timing["dispatch_elapsed_seconds"], timing["completion_elapsed_seconds"]
        require(
            duration(start)
            and duration(end)
            and last_completion <= start <= end
            and start < mapping.budget.elapsed_seconds,
            "LAB_TOOL_DEADLINE_MISMATCH",
        )
        require(
            end <= mapping.budget.elapsed_seconds or item["status"] == "UNKNOWN", "LAB_TOOL_DEADLINE_MISMATCH"
        )
        last_completion = end
    require(
        duration(payload["elapsed_seconds"]) and payload["elapsed_seconds"] >= last_completion,
        "LAB_TOOL_TIMING_MISMATCH",
    )
    assessment = _assess_outcome(
        mapping,
        oracle,
        trace=trace,
        states=states,
        initial_root=payload["initial_root"],
        final_root=payload["final_root"],
        reset=reset,
        approval=approval,
        unresolved=unresolved,
    )
    require(
        all(same(payload[key], value) for key, value in assessment.items()), "LAB_OUTCOME_ASSESSMENT_MISMATCH"
    )
