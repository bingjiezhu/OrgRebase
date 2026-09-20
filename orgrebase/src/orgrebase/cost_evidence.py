"""Offline cost qualification; source consistency is not invoice authentication."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator

from orgrebase.business_evaluation import COST_CATEGORIES, ObservedCost, PairedObservation, business_report
from orgrebase.digest import sha256_digest, verify_content_digest

Text = Annotated[str, Field(min_length=1, max_length=512)]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Amount = Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,6})?$")]
Category = Literal["model", "tools", "human_review", "rework", "operations", "onboarding"]


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("COST_EVIDENCE_DUPLICATE_JSON_FIELD")
    return result


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _UsageProjection(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    status: Literal["REPORTED", "PARTIAL", "UNAVAILABLE", "NOT_APPLICABLE"]
    input_tokens: int | None = Field(strict=True, ge=0)
    output_tokens: int | None = Field(strict=True, ge=0)
    invalid_fields: tuple[str, ...]


class _AttemptProjection(BaseModel):
    """Only accounting fields are interpreted; the digest binds the whole wire record."""

    model_config = ConfigDict(extra="allow", frozen=True)

    schema_version: Literal["orgrebase.model-attempt-observation.v1"]
    dispatch_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    phase: Literal["INTENT", "RESULT"]
    run_ref: Text
    task_ref: Text
    request_digest: Digest
    request_attempt: int = Field(strict=True, ge=0)
    provider: Text
    requested_model: Text
    observed_model: Text | None
    request_body_digest: Digest | None
    request_body_bytes: int | None = Field(strict=True, ge=0)
    dispatch_state: Literal["NOT_DISPATCHED", "DISPATCH_MAY_HAVE_OCCURRED", "RESPONSE_RECEIVED"]
    persistence: Literal["DURABLE", "NON_DURABLE", "WRITE_FAILED"]
    observed_at: AwareDatetime
    usage: _UsageProjection


class EvidenceFile(_Record):
    ref: Text
    path: Text
    digest: Digest


class RunCostCoverage(_Record):
    run_ref: Text
    attempt_directory: Text
    dispatch_ids: tuple[Text, ...]
    declared_complete: bool = Field(strict=True)
    no_dispatch_reason: Text | None = None


class CostEvidenceManifest(_Record):
    schema_version: Literal["orgrebase.cost-evidence-manifest.v1"]
    files: tuple[EvidenceFile, ...] = Field(max_length=256)
    runs: tuple[RunCostCoverage, ...] = Field(max_length=512)


class CostAllocation(_Record):
    run_ref: Text
    amount: Amount
    dispatch_ids: tuple[Text, ...] = ()


class RateCalculation(_Record):
    schedule_ref: Text
    approval_ref: Text


class CostSource(_Record):
    schema_version: Literal["orgrebase.cost-source.v1"]
    issuer: Text
    document_id: Text
    line_item_id: Text
    category: Category
    basis: Literal["BILL", "APPROVED_RATE", "HUMAN_ESTIMATE", "MEASURED_ZERO", "UNMEASURED"]
    recorded_at: Text
    evidence_class: Literal["SYNTHETIC", "CONTROLLED_LOCAL", "REAL_ENTERPRISE"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount: Amount | None
    measurement_note: Text
    allocations: tuple[CostAllocation, ...] = Field(max_length=512)
    rate_calculation: RateCalculation | None = None

    @field_validator("recorded_at")
    @classmethod
    def timestamp(cls, value: str) -> str:
        from datetime import datetime

        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("COST_SOURCE_TIMEZONE_REQUIRED")
        return value


class UniformTokenSchedule(_Record):
    """A deliberately narrow rate calculation; other tariffs need a priced source."""

    schema_version: Literal["orgrebase.uniform-token-schedule.v1"]
    provider: Text
    model_id: Text
    region: Text
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    input_per_token: Amount
    output_per_token: Amount
    effective_from: AwareDatetime
    effective_until: AwareDatetime
    discount_treatment: Literal["RATES_ARE_NET"]
    cache_treatment: Literal["UNIFORM_INPUT_RATE"]
    thinking_treatment: Literal["INCLUDED_IN_OUTPUT"]


class _EvidenceFiles:
    def __init__(self, directory: Path, files: tuple[EvidenceFile, ...]):
        selected = directory.absolute()
        if any(path.is_symlink() for path in (selected, *selected.parents)):
            raise ValueError("COST_EVIDENCE_SYMLINK_DENIED")
        self.directory = selected.resolve()
        self.index = {item.ref: item for item in files}
        if len(self.index) != len(files):
            raise ValueError("COST_EVIDENCE_DUPLICATE_REF")
        self.cache: dict[str, Any] = {}
        self.read_digests: dict[str, str] = {}
        self.total_bytes = 0

    def path(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("COST_EVIDENCE_PATH_INVALID")
        result = self.directory
        for part in path.parts:
            result = result / part
            if result.is_symlink():
                raise ValueError("COST_EVIDENCE_SYMLINK_DENIED")
        return result

    def read(self, path: Path, expected_digest: str | None = None) -> Any:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("COST_EVIDENCE_REGULAR_FILE_REQUIRED")
            raw = stream.read(1_048_577)
        self.total_bytes += len(raw)
        if len(raw) > 1_048_576 or self.total_bytes > 33_554_432:
            raise ValueError("COST_EVIDENCE_SIZE_LIMIT")
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        if expected_digest is not None and digest != expected_digest:
            raise ValueError("COST_EVIDENCE_DIGEST_MISMATCH")
        self.read_digests[str(path.relative_to(self.directory))] = digest
        return json.loads(raw, object_pairs_hook=_json_object)

    def resolve(self, ref: str) -> Any:
        if ref not in self.cache:
            item = self.index.get(ref)
            if item is None:
                raise ValueError("COST_EVIDENCE_REF_UNRESOLVED")
            self.cache[ref] = self.read(self.path(item.path), item.digest)
        return self.cache[ref]

    def attempts(self, run: RunCostCoverage) -> dict[str, _AttemptProjection]:
        directory = self.path(run.attempt_directory)
        paths: list[Path] = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if len(paths) >= 2048:
                    raise ValueError("COST_ATTEMPT_FILE_LIMIT")
                paths.append(Path(entry.path))
        records: dict[str, _AttemptProjection] = {}
        intents: set[str] = set()
        for path in sorted(paths):
            if path.is_symlink():
                raise ValueError("COST_EVIDENCE_SYMLINK_DENIED")
            if path.suffix != ".json" or not path.is_file():
                raise ValueError("COST_ATTEMPT_DIRECTORY_UNEXPECTED_ENTRY")
            payload = self.read(path)
            if not isinstance(payload, dict) or not verify_content_digest(payload, payload.get("digest", "")):
                raise ValueError("COST_ATTEMPT_DIGEST_MISMATCH")
            record = _AttemptProjection.model_validate(payload)
            if record.phase == "INTENT":
                intents.add(record.dispatch_id)
            if record.run_ref != run.run_ref:
                raise ValueError("COST_ATTEMPT_RUN_MISMATCH")
            if path.name != f"{record.dispatch_id}.{record.phase.lower()}.json":
                raise ValueError("COST_ATTEMPT_FILENAME_MISMATCH")
            previous = records.get(record.dispatch_id)
            if previous is not None and any(
                getattr(previous, field) != getattr(record, field)
                for field in ("run_ref", "task_ref", "request_digest", "request_attempt", "provider",
                              "requested_model", "request_body_digest", "request_body_bytes")
            ):
                raise ValueError("COST_ATTEMPT_IDENTITY_MISMATCH")
            if previous is None or record.phase == "RESULT":
                records[record.dispatch_id] = record
        if any(record.phase == "RESULT" and record.dispatch_state != "NOT_DISPATCHED" and dispatch not in intents
               for dispatch, record in records.items()):
            raise ValueError("COST_ATTEMPT_INTENT_MISSING")
        return records


def _rate_amount(source: CostSource, allocation: CostAllocation, files: _EvidenceFiles,
                 attempts: dict[str, _AttemptProjection]) -> Decimal:
    if source.rate_calculation is None:
        raise ValueError("COST_RATE_CALCULATION_REQUIRED")
    schedule = UniformTokenSchedule.model_validate(files.resolve(source.rate_calculation.schedule_ref))
    approval = files.resolve(source.rate_calculation.approval_ref)
    if not isinstance(approval, dict) or approval.get("schedule_digest") != files.index[
        source.rate_calculation.schedule_ref
    ].digest or approval.get("approved") is not True:
        raise ValueError("COST_RATE_APPROVAL_MISMATCH")
    if schedule.currency != source.currency:
        raise ValueError("COST_RATE_CURRENCY_MISMATCH")
    approved_requests = approval.get("request_body_digests")
    if not isinstance(approved_requests, list) or not all(isinstance(item, str) for item in approved_requests):
        raise ValueError("COST_RATE_REQUEST_APPLICABILITY_UNVERIFIED")
    amount = Decimal(0)
    for dispatch in allocation.dispatch_ids:
        record = attempts[dispatch]
        if record.dispatch_state == "NOT_DISPATCHED":
            continue
        if record.phase != "RESULT" or record.persistence != "DURABLE":
            raise ValueError("COST_RATE_OBSERVATION_INCOMPLETE")
        if record.provider != schedule.provider or record.observed_model != schedule.model_id:
            raise ValueError("COST_RATE_MODEL_MISMATCH")
        if record.request_body_digest is None or record.request_body_digest not in approved_requests:
            raise ValueError("COST_RATE_REQUEST_APPLICABILITY_UNVERIFIED")
        if not schedule.effective_from <= record.observed_at <= schedule.effective_until:
            raise ValueError("COST_RATE_OUTSIDE_EFFECTIVE_PERIOD")
        usage = record.usage
        if usage.status != "REPORTED" or usage.input_tokens is None or usage.output_tokens is None or usage.invalid_fields:
            raise ValueError("COST_RATE_USAGE_UNKNOWN")
        amount += Decimal(usage.input_tokens) * Decimal(schedule.input_per_token)
        amount += Decimal(usage.output_tokens) * Decimal(schedule.output_per_token)
    return amount


def qualify_cost_evidence(observations: list[dict[str, Any]], manifest_path: str | Path) -> dict[str, Any]:
    """Return a legacy report only after costs close over the supplied evidence scope."""
    path = Path(manifest_path)
    if path.is_symlink():
        raise ValueError("COST_EVIDENCE_SYMLINK_DENIED")
    files = _EvidenceFiles(path.parent, ())
    manifest_payload = files.read(files.path(path.name))
    manifest_digest = files.read_digests[path.name]
    manifest = CostEvidenceManifest.model_validate(manifest_payload)
    files = _EvidenceFiles(path.parent, manifest.files)
    diagnostics: list[dict[str, str]] = []

    def problem(code: str, *, run_ref: str = "", observation_ref: str = "") -> None:
        diagnostics.append({"code": code, "run_ref": run_ref, "observation_ref": observation_ref})

    coverage_by_run = {run.run_ref: run for run in manifest.runs}
    if len(coverage_by_run) != len(manifest.runs):
        raise ValueError("COST_RUN_COVERAGE_DUPLICATE")
    runs: dict[str, tuple[str, dict[str, Any]]] = {}
    evidence_classes: dict[str, str] = {}
    for pair in observations:
        if not isinstance(pair, dict) or any(not isinstance(pair.get(side), dict) for side in ("baseline", "product")):
            raise ValueError("COST_PAIRED_OBSERVATION_INVALID")
        if not isinstance(pair.get("currency"), str):
            raise ValueError("COST_CURRENCY_REQUIRED")
        for side in ("baseline", "product"):
            run = pair[side]
            ref = run.get("run_ref")
            if not isinstance(ref, str) or not ref or ref in runs:
                raise ValueError("COST_RUN_REF_INVALID_OR_DUPLICATE")
            if not isinstance(run.get("costs", []), list) or any(not isinstance(cost, dict) for cost in run.get("costs", [])):
                raise ValueError("COST_OBSERVATION_ARRAY_REQUIRED")
            runs[ref] = pair["currency"], run
            evidence_classes[ref] = pair.get("evidence_class", "")
    if set(coverage_by_run) != set(runs):
        problem("COST_RUN_COVERAGE_MISMATCH")

    records_by_run: dict[str, dict[str, _AttemptProjection]] = {}
    coverage: list[dict[str, Any]] = []
    dispatch_owners: dict[str, str] = {}
    for ref, (_, run) in runs.items():
        declared = coverage_by_run.get(ref)
        records: dict[str, _AttemptProjection] = {}
        if declared is not None:
            try:
                records = files.attempts(declared)
                if len(set(declared.dispatch_ids)) != len(declared.dispatch_ids):
                    raise ValueError("COST_DISPATCH_DECLARATION_DUPLICATE")
                if not declared.declared_complete or set(records) != set(declared.dispatch_ids):
                    raise ValueError("COST_DISPATCH_COVERAGE_INCOMPLETE")
                if not records and not declared.no_dispatch_reason:
                    raise ValueError("COST_NO_DISPATCH_EXPLANATION_REQUIRED")
                for dispatch in records:
                    if dispatch in dispatch_owners:
                        raise ValueError("COST_DISPATCH_MULTIPLE_RUNS")
                    dispatch_owners[dispatch] = ref
            except (OSError, ValueError) as error:
                problem(_reason(error), run_ref=ref)
        records_by_run[ref] = records
        categories = {cost["category"] for cost in run.get("costs", ())
                      if isinstance(cost.get("category"), str)}
        if categories != COST_CATEGORIES:
            problem("COST_CATEGORIES_INCOMPLETE", run_ref=ref)
        coverage.append({"run_ref": ref, "categories_present": sorted(categories),
                         "dispatches_declared": len(declared.dispatch_ids) if declared else None,
                         "dispatches_observed": len(records),
                         "usage_unknown_dispatches": sorted(
                             key for key, record in records.items()
                             if record.usage.status not in {"REPORTED", "NOT_APPLICABLE"}
                         )})

    sources: dict[tuple[str, str, str], tuple[CostSource, str]] = {}
    accounted: set[tuple[tuple[str, str, str], str]] = set()
    source_digests: dict[str, tuple[str, str, str]] = {}
    model_dispatches: dict[str, set[str]] = {ref: set() for ref in runs}
    basis_counts: Counter[str] = Counter()
    observed_refs: set[str] = set()
    for ref, (currency, run) in runs.items():
        for cost_payload in run.get("costs", ()):
            observation_ref = cost_payload.get("observation_ref", "")
            try:
                if cost_payload.get("amount") is None:
                    raise ValueError("COST_AMOUNT_UNKNOWN")
                cost = ObservedCost.model_validate(cost_payload)
                if cost.observation_ref in observed_refs:
                    raise ValueError("COST_OBSERVATION_COUNTED_MORE_THAN_ONCE")
                observed_refs.add(cost.observation_ref)
                source = CostSource.model_validate(files.resolve(cost.observation_ref))
                if source.basis == "UNMEASURED" or source.amount is None:
                    raise ValueError("COST_AMOUNT_UNKNOWN")
                if source.category != cost.category or source.currency != currency:
                    raise ValueError("COST_SOURCE_CATEGORY_OR_CURRENCY_MISMATCH")
                if source.evidence_class != evidence_classes[ref]:
                    raise ValueError("COST_SOURCE_EVIDENCE_CLASS_MISMATCH")
                if source.basis == "MEASURED_ZERO" and Decimal(source.amount) != 0:
                    raise ValueError("COST_MEASURED_ZERO_NONZERO")
                identity = source.issuer, source.document_id, source.line_item_id
                digest = files.index[cost.observation_ref].digest
                previous = sources.get(identity)
                if previous is not None and previous[0] != source:
                    raise ValueError("COST_UNDERLYING_SOURCE_CONFLICT")
                if digest in source_digests and source_digests[digest] != identity:
                    raise ValueError("COST_SOURCE_DIGEST_IDENTITY_CONFLICT")
                sources[identity] = source, digest
                source_digests[digest] = identity
                allocations = {item.run_ref: item for item in source.allocations}
                if len(allocations) != len(source.allocations) or not set(allocations) <= set(runs):
                    raise ValueError("COST_ALLOCATION_SCOPE_INVALID")
                if sum((Decimal(item.amount) for item in source.allocations), Decimal(0)) != Decimal(source.amount):
                    raise ValueError("COST_ALLOCATION_TOTAL_MISMATCH")
                allocation = allocations.get(ref)
                if allocation is None or Decimal(allocation.amount) != Decimal(cost.amount):
                    raise ValueError("COST_SOURCE_AMOUNT_MISMATCH")
                if (identity, ref) in accounted:
                    raise ValueError("COST_UNDERLYING_CHARGE_COUNTED_MORE_THAN_ONCE")
                accounted.add((identity, ref))
                dispatches = set(allocation.dispatch_ids)
                if len(dispatches) != len(allocation.dispatch_ids):
                    raise ValueError("COST_ALLOCATION_DISPATCH_DUPLICATE")
                if cost.category == "model":
                    if not dispatches <= set(records_by_run[ref]):
                        raise ValueError("COST_ALLOCATION_DISPATCH_UNRESOLVED")
                    if model_dispatches[ref] & dispatches:
                        raise ValueError("COST_DISPATCH_COUNTED_MORE_THAN_ONCE")
                    model_dispatches[ref].update(dispatches)
                    if source.basis == "MEASURED_ZERO" and any(
                        records_by_run[ref][dispatch].dispatch_state != "NOT_DISPATCHED" for dispatch in dispatches
                    ):
                        raise ValueError("COST_MODEL_ZERO_REQUIRES_PRICED_BASIS")
                elif dispatches:
                    raise ValueError("COST_NONMODEL_DISPATCH_ALLOCATION")
                if source.basis == "APPROVED_RATE":
                    if cost.category != "model":
                        raise ValueError("COST_RATE_CATEGORY_UNSUPPORTED")
                    if _rate_amount(source, allocation, files, records_by_run[ref]) != Decimal(cost.amount):
                        raise ValueError("COST_RATE_AMOUNT_MISMATCH")
                elif source.rate_calculation is not None:
                    raise ValueError("COST_RATE_BASIS_MISMATCH")
                basis_counts[source.basis] += 1
            except (OSError, ValueError, KeyError) as error:
                problem(_reason(error), run_ref=ref, observation_ref=observation_ref)
    for ref, records in records_by_run.items():
        if model_dispatches[ref] != set(records):
            problem("COST_MODEL_DISPATCH_COVERAGE_INCOMPLETE", run_ref=ref)
    for identity, (source, _) in sources.items():
        if any((identity, allocation.run_ref) not in accounted for allocation in source.allocations):
            problem("COST_SHARED_ALLOCATION_UNACCOUNTED")

    result: dict[str, Any] = {
        "schema_version": "orgrebase.cost-evidence-qualification.v1",
        "status": "INCOMPLETE_COST" if diagnostics else "COMPLETE_COST",
        "input_digest": sha256_digest(observations), "manifest_digest": manifest_digest,
        "resolved_evidence_digest": sha256_digest(files.read_digests),
        "file_digest_algorithm": "SHA256_RAW_FILE_BYTES",
        "coverage_scope": "DECLARED_RUNS_AND_SUPPLIED_OBSERVATION_DIRECTORIES",
        "unrecorded_dispatches_ruled_out": False,
        "cost_source_authenticity": "REQUIRES_INDEPENDENT_REVIEW",
        "qualified_result_provenance": "REQUIRES_INDEPENDENT_REVIEW",
        "enterprise_roi_proven": False,
        "cost_basis_counts": dict(sorted(basis_counts.items())),
        "coverage": coverage, "diagnostics": diagnostics,
    }
    if not diagnostics:
        result["report"] = business_report(tuple(PairedObservation.model_validate(item) for item in observations))
    return result


def _reason(error: Exception) -> str:
    if isinstance(error, OSError):
        return "COST_EVIDENCE_FILE_UNAVAILABLE"
    if isinstance(error, ValidationError):
        return "COST_EVIDENCE_SCHEMA_INVALID"
    value = str(error)
    return value if value.startswith("COST_") and value.replace("_", "").isalnum() else "COST_EVIDENCE_INVALID"
