"""Approved, bounded execution in a controller-owned disposable environment.

The public API keeps task contracts, execution control and pure result replay
separate. No planner receives a database handle, oracle or executable callback.
"""

from __future__ import annotations

import json
import secrets
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.oac_wire import _require_ref, _resource_ref, _verify_oac_resource
from orgrebase.workspace.outcome_contracts import (
    DIMENSIONS,
    SYSTEMS,
    DisposableEnvironment,
    LabBudget,
    LabToolGrant,
    LabToolRequest,
    OutcomeOracle,
    OutcomeTaskMapping,
    System,
)
from orgrebase.workspace.outcome_runtime import build_runtime_bundle
from orgrebase.workspace.outcome_verification import (
    _assess_outcome,
    _changes,
    _completed_work_units,
    _frozen_json,
    verify_outcome_receipt,
)

__all__ = [
    "DIMENSIONS",
    "SYSTEMS",
    "DisposableEnvironment",
    "LabBudget",
    "LabToolGrant",
    "LabToolRequest",
    "OACPlanGate",
    "OutcomeLab",
    "OutcomeOracle",
    "OutcomeTaskMapping",
    "System",
    "verify_outcome_receipt",
]


class OACPlanGate:
    """Recompute the certificate with the configured public CLI in a private input directory."""

    def __init__(self, command: Sequence[str], *, environment: Mapping[str, str]) -> None:
        if not command:
            raise ValueError("LAB_OAC_COMMAND_REQUIRED")
        self._command = tuple(command)
        self._environment = dict(environment)

    def verify(
        self,
        mapping: OutcomeTaskMapping,
        *,
        snapshot: dict[str, Any],
        change: dict[str, Any],
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        inputs = (
            ("snapshot", snapshot, "OrganizationSnapshot"),
            ("change", change, "SemanticChangeSet"),
            ("plan", plan, "OrganizationPlan"),
        )
        with tempfile.TemporaryDirectory(prefix="orgrebase-lab-plan-") as temporary:
            directory = Path(temporary)
            files = []
            for name, resource, kind in inputs:
                _verify_oac_resource(resource, kind)
                if resource["digest"] != getattr(mapping, name + "_digest"):
                    raise IntegrityError("LAB_MAPPING_ROOT_MISMATCH")
                path = directory / (name + ".json")
                path.write_text(json.dumps(resource, ensure_ascii=False), encoding="utf-8")
                files.append(str(path))
            output = directory / "certificate.json"
            result = subprocess.run(
                [*self._command, "verify", *files, "--profile", mapping.oac_profile, "--output", str(output)],
                env=self._environment,
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode != 0 or not output.is_file():
                raise IntegrityError("LAB_PLAN_VERIFICATION_FAILED")
            certificate = json.loads(output.read_text(encoding="utf-8"))
        _verify_oac_resource(certificate, "PlanCertificate")
        spec = certificate["spec"]
        for field, resource in (("subjectPlanRef", plan), ("snapshotRef", snapshot), ("changeRef", change)):
            _require_ref(spec.get(field), resource, "lab.certificate." + field)
        if spec.get("verdict") != "ACCEPT" or spec.get("unresolvedRefs") or spec.get("restrictions"):
            raise IntegrityError("LAB_ACCEPT_PLAN_REQUIRED")
        if not spec.get("dimensions") or any(d.get("verdict") != "PASS" for d in spec["dimensions"]):
            raise IntegrityError("LAB_PLAN_DIMENSIONS_INCOMPLETE")
        if plan["spec"].get("effectCeiling") != "zero_effect" or any(
            item.get("effectCeiling") != "zero_effect"
            for field in ("roleInstances", "workUnits")
            for item in plan["spec"][field]
        ):
            raise IntegrityError("LAB_PLAN_MUST_REMAIN_ZERO_EFFECT")
        obligations = {item["obligationId"]: item for item in plan["spec"]["obligations"]}
        work_units = {item["workUnitId"]: item for item in plan["spec"]["workUnits"]}
        role_instances = {item["roleInstanceId"]: item for item in plan["spec"]["roleInstances"]}
        expected_order: dict[str, set[str]] = {key: set() for key in work_units}
        for constraint in plan["spec"]["happensBefore"]:
            expected_order[constraint["successorRef"]].add(constraint["predecessorRef"])
        if expected_order != {key: set(value) for key, value in mapping.work_unit_predecessors.items()}:
            raise IntegrityError("LAB_WORK_UNIT_ORDER_MAPPING_MISMATCH")
        for grant in mapping.grants:
            obligation = obligations.get(grant.obligation_ref)
            if obligation is None or obligation["requiredRoleRef"] != grant.role:
                raise IntegrityError("LAB_OBLIGATION_MAPPING_MISMATCH")
            if obligation["targetRef"] not in mapping.subjects:
                raise IntegrityError("LAB_SUBJECT_MAPPING_MISMATCH")
            if grant.evidence_ref not in obligation["requiredEvidence"]:
                raise IntegrityError("LAB_EVIDENCE_MAPPING_MISMATCH")
            unit = work_units.get(grant.work_unit_ref)
            if (
                unit is None
                or grant.obligation_ref not in unit["obligationRefs"]
                or (
                    grant.evidence_ref not in unit["evidenceOutputs"]
                    or grant.role
                    not in {role_instances[ref]["roleDefinitionRef"] for ref in unit["roleInstanceRefs"]}
                )
            ):
                raise IntegrityError("LAB_WORK_UNIT_MAPPING_MISMATCH")
        roles = {item["roleDefinitionRef"] for item in plan["spec"]["roleInstances"]}
        if set(mapping.system_roles["oac"]) != roles:
            raise IntegrityError("LAB_OAC_ROLE_MAPPING_MISMATCH")
        return certificate

    def validate_lifecycle(
        self,
        resource: Mapping[str, Any],
        *,
        snapshot: Mapping[str, Any] | None = None,
    ) -> None:
        """Validate lifecycle semantics through the same configured public CLI."""
        kind = resource.get("kind")
        if kind not in {"SourceAdmissionReceipt", "OrganizationalDemand", "OutcomeCertificate"}:
            raise IntegrityError("LAB_LIFECYCLE_KIND_UNSUPPORTED")
        _verify_oac_resource(resource, kind)
        if (kind == "OrganizationalDemand") != (snapshot is not None):
            raise IntegrityError("LAB_LIFECYCLE_SNAPSHOT_REQUIRED")
        with tempfile.TemporaryDirectory(prefix="orgrebase-lab-lifecycle-") as temporary:
            directory = Path(temporary)
            resource_path = directory / "resource.json"
            resource_path.write_text(json.dumps(dict(resource), allow_nan=False), encoding="utf-8")
            arguments = [*self._command, "validate-evolution", str(resource_path)]
            if snapshot is not None:
                _verify_oac_resource(snapshot, "OrganizationSnapshot")
                snapshot_path = directory / "snapshot.json"
                snapshot_path.write_text(json.dumps(dict(snapshot), allow_nan=False), encoding="utf-8")
                arguments.extend(("--snapshot", str(snapshot_path)))
            result = subprocess.run(
                arguments,
                env=self._environment,
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        try:
            verified = json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise IntegrityError("LAB_LIFECYCLE_VERIFICATION_FAILED") from exc
        if (
            result.returncode != 0
            or not isinstance(verified, dict)
            or set(verified) != {"valid", "kind"}
            or verified["valid"] is not True
            or verified["kind"] != kind
        ):
            raise IntegrityError("LAB_LIFECYCLE_VERIFICATION_FAILED")


class OutcomeLab:
    """One-use, explicit experiment approval and append-only six-dimension receipts."""

    def __init__(
        self,
        mapping: OutcomeTaskMapping,
        oracle: OutcomeOracle,
        environment: DisposableEnvironment,
        gate: OACPlanGate,
        *,
        snapshot: dict[str, Any],
        change: dict[str, Any],
        plan: dict[str, Any],
        controller_authority: str,
        acting_authority: str,
    ) -> None:
        self.mapping = mapping.revalidated()
        self._oracle = oracle.revalidated()
        if (
            oracle.authority in {controller_authority, acting_authority}
            or controller_authority == acting_authority
        ):
            raise IntegrityError("LAB_AUTHORITY_SEPARATION_REQUIRED")
        if (
            oracle.digest != mapping.oracle_digest
            or environment.build_digest != mapping.environment_build_digest
        ):
            raise IntegrityError("LAB_FROZEN_INPUT_MISMATCH")
        self._environment = environment
        self._certificate = gate.verify(self.mapping, snapshot=snapshot, change=change, plan=plan)
        self._metadata = _frozen_json(snapshot["metadata"])
        self._plan_ref = _resource_ref(plan)
        self._controller = controller_authority
        self._actor = acting_authority
        self._approvals: dict[str, dict[str, Any]] = {}
        self._lock = Lock()
        self._closed = False

    @property
    def plan_certificate(self) -> dict[str, Any]:
        return _frozen_json(self._certificate)

    def verify_receipt(self, receipt: Mapping[str, Any]) -> None:
        receipt = _frozen_json(receipt)
        verify_outcome_receipt(
            receipt,
            mapping=self.mapping,
            oracle=self._oracle,
            plan_certificate_digest=self._certificate["digest"],
            controller_authority=self._controller,
            acting_authority=self._actor,
        )
        expected = build_runtime_bundle(
            self.mapping,
            receipt["approval_receipt"],
            plan_ref=self._plan_ref,
            certificate_ref=_resource_ref(self._certificate),
            metadata=self._metadata,
            acting_authority=self._actor,
        )
        if sha256_digest(receipt["runtime_bundle"]) != sha256_digest(expected):
            raise IntegrityError("LAB_RECEIPT_RUNTIME_BUNDLE_MISMATCH")

    def approve(self, system: System, *, authority: str) -> dict[str, Any]:
        with self._lock:
            return self._approve(system, authority=authority)

    def _approve(self, system: System, *, authority: str) -> dict[str, Any]:
        if self._closed:
            raise IntegrityError("LAB_ENVIRONMENT_CLOSED")
        if authority != self._controller or system not in SYSTEMS:
            raise IntegrityError("LAB_APPROVAL_AUTHORITY_DENIED")
        # A reset supersedes every old capability, even if the reset itself fails.
        self._approvals.clear()
        snapshot_id = self._environment.reset()
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise IntegrityError("LAB_SNAPSHOT_ID_REQUIRED")
        root = sha256_digest(_frozen_json(self._environment.snapshot()))
        if root != self.mapping.seed_root:
            raise IntegrityError("LAB_RESET_ROOT_MISMATCH")
        token = secrets.token_hex(32)
        receipt = {
            "mapping_digest": self.mapping.digest,
            "plan_certificate_digest": self._certificate["digest"],
            "system": system,
            "snapshot_id": snapshot_id,
            "seed_root": root,
            "controller": authority,
            "budget_digest": self.mapping.budget.digest,
            "approved_at_monotonic": time.monotonic(),
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        receipt["digest"] = sha256_digest(receipt)
        bundle = build_runtime_bundle(
            self.mapping,
            receipt,
            plan_ref=self._plan_ref,
            certificate_ref=_resource_ref(self._certificate),
            metadata=self._metadata,
            acting_authority=self._actor,
        )
        self._approvals[token] = {"receipt": receipt, "runtime_bundle": bundle}
        return {"token": token, "receipt": _frozen_json(receipt), "runtime_bundle": _frozen_json(bundle)}

    def run(
        self, token: str, requests: Sequence[LabToolRequest], *, runtime_bundle: Mapping[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            return self._run(token, requests, runtime_bundle)

    def _observe(self, states: dict[str, dict[str, Any]], unresolved: list[str], label: str) -> str | None:
        try:
            state = _frozen_json(self._environment.snapshot())
            if not isinstance(state, dict):
                raise TypeError("environment state must be a JSON object")
            root = sha256_digest(state)
            states[root] = state
            return root
        except Exception as exc:
            unresolved.append(label + ":" + type(exc).__name__)
            return None

    def _run(
        self, token: str, requests: Sequence[LabToolRequest], runtime_bundle: Mapping[str, Any]
    ) -> dict[str, Any]:
        authorized = self._approvals.pop(token, None)
        if authorized is None:
            raise IntegrityError("LAB_EXPLICIT_ONE_USE_APPROVAL_REQUIRED")
        if sha256_digest(runtime_bundle) != sha256_digest(authorized["runtime_bundle"]):
            raise IntegrityError("LAB_APPROVED_RUNTIME_BUNDLE_MISMATCH")
        approval = authorized["receipt"]
        runtime_bundle = authorized["runtime_bundle"]
        mapping = self.mapping.revalidated()
        oracle = self._oracle.revalidated()
        if mapping.digest != approval["mapping_digest"] or oracle.digest != mapping.oracle_digest:
            raise IntegrityError("LAB_APPROVED_INPUT_CHANGED")
        # Validate a bounded data-only prefix before the first possible dispatch.
        prepared = tuple(request.revalidated() for request in requests[: mapping.budget.tool_calls + 1])
        grants = {item.request.digest: item for item in mapping.grants}
        allowed_roles = set(runtime_bundle["bundle"]["spec"]["systemRoles"])
        started = approval["approved_at_monotonic"]
        writes = 0
        completed_requests: set[str] = set()
        trace: list[dict[str, Any]] = []
        timings: list[dict[str, Any]] = []
        unresolved: list[str] = []
        states: dict[str, dict[str, Any]] = {}
        initial_root = self._observe(states, unresolved, "initial_state")
        if initial_root is not None and initial_root != approval["seed_root"]:
            raise IntegrityError("LAB_APPROVED_SNAPSHOT_CHANGED")
        try:
            for index, request in enumerate(prepared if initial_root is not None else ()):
                before_root = self._observe(states, unresolved, f"before_state:{index}")
                grant = grants.get(request.digest)
                elapsed = time.monotonic() - started
                remaining = mapping.budget.elapsed_seconds - elapsed
                reason = None
                if before_root is None:
                    reason = "LAB_STATE_UNOBSERVABLE"
                elif before_root != (trace[-1]["after_root"] if trace else initial_root):
                    reason = "LAB_STATE_CHANGED_OUTSIDE_TOOL"
                elif index >= mapping.budget.tool_calls or remaining <= 0:
                    reason = "LAB_TOOL_BUDGET_EXHAUSTED"
                elif grant is None or grant.role not in allowed_roles:
                    reason = "LAB_TOOL_SCOPE_DENIED"
                elif not set(mapping.work_unit_predecessors[grant.work_unit_ref]) <= _completed_work_units(
                    mapping, completed_requests
                ):
                    reason = "LAB_WORK_UNIT_ORDER_DENIED"
                elif grant.mutates_state and writes >= mapping.budget.write_calls:
                    reason = "LAB_WRITE_BUDGET_EXHAUSTED"
                if reason is None:
                    assert grant is not None
                    try:
                        effect = self._environment.tool_mutates_state(request.tool)
                        if type(effect) is not bool or grant.mutates_state != effect:
                            reason = "LAB_TOOL_EFFECT_DECLARATION_MISMATCH"
                    except Exception as exc:
                        unresolved.append(f"tool_declaration:{index}:" + type(exc).__name__)
                        reason = "LAB_TOOL_EFFECT_DECLARATION_UNRESOLVED"
                result: Any = None
                dispatched = reason is None
                status = "COMPLETED" if dispatched else "DENIED"
                if dispatched:
                    assert grant is not None
                    writes += int(grant.mutates_state)
                    try:
                        result = _frozen_json(self._environment.call(request, timeout=remaining))
                        if time.monotonic() - started > mapping.budget.elapsed_seconds:
                            raise TimeoutError("tool returned after the approved deadline")
                    except Exception as exc:
                        status = "UNKNOWN"
                        reason = "LAB_TOOL_RESULT_UNRESOLVED:" + type(exc).__name__
                        unresolved.append(f"tool_result:{index}:" + type(exc).__name__)
                    timings.append(
                        {
                            "index": index,
                            "dispatch_elapsed_seconds": elapsed,
                            "completion_elapsed_seconds": time.monotonic() - started,
                        }
                    )
                after_root = self._observe(states, unresolved, f"after_state:{index}")
                if after_root is None and dispatched:
                    status = "UNKNOWN"
                    reason = reason or "LAB_AFTER_STATE_UNOBSERVABLE"
                if status == "COMPLETED":
                    completed_requests.add(request.digest)
                changed = (
                    _changes(states[before_root], states[after_root])
                    if before_root is not None and after_root is not None
                    else None
                )
                trace.append(
                    {
                        "index": index,
                        "request": request.model_dump(mode="json"),
                        "dispatched": dispatched,
                        "status": status,
                        "reason": reason,
                        "result": result,
                        "before_root": before_root,
                        "after_root": after_root,
                        "changed_paths": changed,
                    }
                )
                if (
                    reason
                    in {
                        "LAB_TOOL_BUDGET_EXHAUSTED",
                        "LAB_WRITE_BUDGET_EXHAUSTED",
                        "LAB_STATE_UNOBSERVABLE",
                        "LAB_STATE_CHANGED_OUTSIDE_TOOL",
                        "LAB_TOOL_EFFECT_DECLARATION_UNRESOLVED",
                    }
                    or status == "UNKNOWN"
                ):
                    break
            final_root = self._observe(states, unresolved, "final_state")
            reset_id = None
            reset_root = None
            try:
                reset_id = self._environment.reset()
                reset_root = self._observe(states, unresolved, "reset_state")
            except Exception as exc:
                unresolved.append("reset:" + type(exc).__name__)
            if reset_root is None or reset_root != mapping.seed_root:
                self._closed = True
                try:
                    self._environment.close()
                except Exception as exc:
                    unresolved.append("close:" + type(exc).__name__)
            reset = {"snapshot_id": reset_id, "state_root": reset_root}
            assessment = _assess_outcome(
                mapping,
                oracle,
                trace=trace,
                states=states,
                initial_root=initial_root,
                final_root=final_root,
                reset=reset,
                approval=approval,
                unresolved=unresolved,
            )
            receipt = {
                "schema_version": "orgrebase.outcome-lab.certificate.v2",
                "mapping_digest": mapping.digest,
                "oracle_digest": oracle.digest,
                "plan_certificate_digest": self._certificate["digest"],
                "approval_receipt": approval,
                "runtime_bundle": runtime_bundle,
                "system": approval["system"],
                "acting_authority": self._actor,
                "oracle_authority": oracle.authority,
                "initial_root": initial_root,
                "final_root": final_root,
                "state_observations": states,
                "trace": trace,
                "trace_digest": sha256_digest(trace),
                "tool_timings": timings,
                "tool_calls": len(trace),
                "dispatched_calls": sum(item["dispatched"] for item in trace),
                "denied_calls": sum(not item["dispatched"] for item in trace),
                "write_calls": writes,
                "elapsed_seconds": time.monotonic() - started,
                "unresolved": unresolved,
                "reset_receipt": reset,
                **assessment,
                "claim_scope": "DISPOSABLE_LOCAL_STATE_EXPERIMENT",
            }
            receipt["digest"] = sha256_digest(receipt)
            return receipt
        except BaseException:
            self._closed = True
            self._environment.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._approvals.clear()
            self._closed = True
            self._environment.close()
