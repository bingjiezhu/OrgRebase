"""Bounded query witnesses over admitted source coverage, never arbitrary queries."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)
from sqlalchemy import select

from orgrebase.clock import utc_datetime
from orgrebase.database import in_transaction, workspace_registry
from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    ContentAddressedModel,
    FreshnessError,
    IntegrityError,
    ObjectState,
    VersionedObject,
)

DIGEST = r"^sha256:[0-9a-f]{64}$"
GUID = r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$"
MEDIA_TYPE = "application/vnd.orgrebase.read-dependency-witness+json"
ReadDependencyValidator = Callable[[tuple[VersionedObject, ...], str], None]


class ReadDependencyError(FreshnessError):
    """Stable current-read failure codes, distinct from preview expiry."""

    def __init__(self, code: str, reasons: tuple[str, ...] = ()) -> None:
        if code not in {
            "READ_DEPENDENCY_CHANGED",
            "READ_DEPENDENCY_UNKNOWN",
            "READ_DEPENDENCY_SOURCE_CONFIG_UNAVAILABLE",
        }:
            raise ValueError("READ_DEPENDENCY_ERROR_CODE_INVALID")
        self.code = code
        super().__init__(code + (":" + ",".join(reasons) if reasons else ""))


class ReadQuery(ContentAddressedModel):
    schema_version: Literal["orgrebase.bounded-read-query.v1"] = "orgrebase.bounded-read-query.v1"
    connector_id: str = Field(min_length=1, max_length=160)
    record_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)
    operator: Literal["eq", "is_missing"]
    field: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,127}$")
    value: StrictStr | StrictBool | StrictInt | StrictFloat | None = None

    @model_validator(mode="after")
    def bounded_predicate(self):
        import math
        import re

        if self.record_ids != tuple(sorted(set(self.record_ids))) or any(
            not re.fullmatch(GUID, item) for item in self.record_ids
        ):
            raise ValueError("READ_QUERY_RECORD_SCOPE_INVALID")
        if self.field is None:
            raise ValueError("READ_QUERY_FIELD_REQUIRED")
        if self.operator == "is_missing" and self.value is not None:
            raise ValueError("READ_QUERY_MISSING_REQUIRES_NULL_VALUE")
        if (isinstance(self.value, str) and len(self.value) > 4096) or (
            isinstance(self.value, float) and not math.isfinite(self.value)
        ):
            raise ValueError("READ_QUERY_VALUE_INVALID")
        return self


class QueryObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    result: Literal["TRUE", "FALSE", "UNKNOWN"]
    matched_record_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


class CoverageBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["orgrebase.source-coverage.v1"]
    status: Literal["COMPLETE"]
    source_config_digest: str = Field(pattern=DIGEST)
    source_binding_digest: str = Field(pattern=DIGEST)
    inventory_digest: str = Field(pattern=DIGEST)
    generation_digest: str = Field(pattern=DIGEST)
    connector_id: str = Field(min_length=1)
    record_ids: tuple[str, ...]
    fields: tuple[str, ...]
    cursor_revision: int = Field(strict=True, ge=1)
    cursor_digest: str = Field(pattern=DIGEST)
    page_ref: str = Field(min_length=1)
    page_digest: str = Field(pattern=DIGEST)
    observed_at: str
    expires_at: str
    coverage_digest: str = Field(pattern=DIGEST)

    @model_validator(mode="after")
    def valid_interval(self):
        if utc_datetime(self.observed_at) >= utc_datetime(self.expires_at):
            raise ValueError("READ_COVERAGE_INTERVAL_INVALID")
        if self.record_ids != tuple(sorted(set(self.record_ids))) or self.fields != tuple(
            sorted(set(self.fields))
        ):
            raise ValueError("READ_COVERAGE_SCOPE_INVALID")
        return self


class ReadWitness(ContentAddressedModel):
    schema_version: Literal["orgrebase.read-witness.v1"] = "orgrebase.read-witness.v1"
    query: ReadQuery
    coverage: CoverageBinding
    result: Literal["TRUE", "FALSE"]
    matched_record_ids: tuple[str, ...]

    @property
    def artifact_id(self) -> str:
        return "read-witness:" + self.digest[7:]


class WitnessReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_id: str = Field(pattern=r"^read-witness:[0-9a-f]{64}$")
    witness_digest: str = Field(pattern=DIGEST)
    query_digest: str = Field(pattern=DIGEST)
    expected_result: Literal["TRUE", "FALSE"]


class ReadDependencies(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["orgrebase.read-dependencies.v1"] = "orgrebase.read-dependencies.v1"
    witnesses: tuple[WitnessReference, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_queries(self):
        if len({item.query_digest for item in self.witnesses}) != len(self.witnesses):
            raise ValueError("READ_DEPENDENCY_DUPLICATE_QUERY")
        return self


def observe_query(query: ReadQuery, coverage: dict[str, Any], *, now: str) -> QueryObservation:
    """Absence requires complete observation of the exact admitted search boundary."""
    query = query.revalidated()
    if coverage.get("coverage_digest") != sha256_digest(
        {key: value for key, value in coverage.items() if key != "coverage_digest"}
    ):
        raise IntegrityError("READ_COVERAGE_DIGEST_INVALID")
    if coverage.get("status") != "COMPLETE" or coverage.get("reasons"):
        return QueryObservation(
            result="UNKNOWN", reasons=tuple(coverage.get("reasons") or ("SOURCE_COVERAGE_INCOMPLETE",))
        )
    try:
        binding = CoverageBinding.model_validate({key: coverage[key] for key in CoverageBinding.model_fields})
    except (KeyError, TypeError, ValueError) as error:
        raise IntegrityError("READ_COVERAGE_CONTRACT_INVALID") from error
    if query.connector_id != binding.connector_id or not set(query.record_ids).issubset(binding.record_ids):
        raise IntegrityError("READ_QUERY_OUTSIDE_SOURCE_SCOPE")
    if query.field not in binding.fields:
        raise IntegrityError("READ_QUERY_FIELD_OUTSIDE_SOURCE_SCOPE")
    if utc_datetime(now) < utc_datetime(binding.observed_at) or utc_datetime(now) >= utc_datetime(
        binding.expires_at
    ):
        return QueryObservation(result="UNKNOWN", reasons=("SOURCE_COVERAGE_EXPIRED_OR_FUTURE",))
    records = coverage.get("records")
    if not isinstance(records, dict):
        raise IntegrityError("READ_COVERAGE_RECORDS_INVALID")
    matches = []
    for record_id in query.record_ids:
        record = records.get(record_id)
        if not isinstance(record, dict) or type(record.get("deleted")) is not bool:
            return QueryObservation(result="UNKNOWN", reasons=("SOURCE_RECORD_NOT_OBSERVED",))
        if record["deleted"]:
            return QueryObservation(result="UNKNOWN", reasons=("SOURCE_RECORD_ABSENCE_UNQUALIFIED",))
        if not isinstance(record.get("revision"), str) or not record["revision"]:
            return QueryObservation(result="UNKNOWN", reasons=("SOURCE_RECORD_REVISION_NOT_OBSERVED",))
        try:
            if utc_datetime(record["observed_at"]) > utc_datetime(binding.observed_at):
                raise ValueError("future observation")
        except (KeyError, TypeError, ValueError):
            return QueryObservation(result="UNKNOWN", reasons=("SOURCE_RECORD_TIME_NOT_OBSERVED",))
        fields = record.get("field_digests")
        if not isinstance(fields, dict) or query.field not in fields:
            return QueryObservation(result="UNKNOWN", reasons=("SOURCE_FIELD_NOT_OBSERVED",))
        import re

        if not isinstance(fields[query.field], str) or not re.fullmatch(DIGEST, fields[query.field]):
            raise IntegrityError("READ_COVERAGE_FIELD_DIGEST_INVALID")
        expected_digest = sha256_digest(None if query.operator == "is_missing" else query.value)
        if fields[query.field] == expected_digest:
            matches.append(record_id)
    return QueryObservation(result="TRUE" if matches else "FALSE", matched_record_ids=tuple(matches))


def _now(workspace: Any, requested: str | None) -> str:
    # Replay compilation passes historical creation time. Commit checks still
    # use the current trusted clock and cannot revive an expired witness.
    current = workspace.clock.now()
    return max((current, requested), key=utc_datetime) if requested is not None else current


def _lock_scope_if_transaction(workspace: Any) -> None:
    store = workspace.store
    if in_transaction(store.connection):
        store.execute(
            store.connection,
            store._lock_row(
                select(workspace_registry.c.workspace_id)
                .where(workspace_registry.c.workspace_id == store.workspace_id)
            ),
        ).fetchone()


def _coverage(workspace: Any, *, now: str) -> dict[str, Any]:
    from orgrebase.workspace.source_bindings import load_source_config, source_coverage

    try:
        config = load_source_config(getattr(workspace, "source_config_path", None))
    except ValueError as error:
        raise ReadDependencyError("READ_DEPENDENCY_SOURCE_CONFIG_UNAVAILABLE") from error
    # The workspace append head, acquired above in a commit transaction, also
    # serializes mapping/revocation events. Do not acquire a checkpoint lock in
    # the reverse order of the source worker (checkpoint -> append head).
    return source_coverage(workspace, config, now=now, connection=None)


def capture_read_witness(
    workspace: Any, query: ReadQuery, *, now: str | None = None, connection: Any | None = None
) -> ReadWitness:
    """Persist a factual witness from the configured connector's durable coverage."""
    if connection is not None and (
        connection is not workspace.store.connection or not in_transaction(connection)
    ):
        raise RuntimeError("READ_WITNESS_FOREIGN_OR_MISSING_TRANSACTION")
    selected_now = _now(workspace, now)
    scope = workspace.store.transaction() if connection is None else nullcontext(connection)
    with scope as selected:
        _lock_scope_if_transaction(workspace)
        coverage = _coverage(workspace, now=selected_now)
        observation = observe_query(query, coverage, now=selected_now)
        if observation.result == "UNKNOWN":
            raise ReadDependencyError("READ_DEPENDENCY_UNKNOWN", observation.reasons)
        witness = ReadWitness(
            query=query,
            coverage=CoverageBinding.model_validate(
                {key: coverage[key] for key in CoverageBinding.model_fields}
            ),
            result=observation.result,
            matched_record_ids=observation.matched_record_ids,
        )
        workspace.store.save_artifact(
            selected, witness.artifact_id, MEDIA_TYPE, witness.model_dump(mode="json")
        )
        return witness


def dependency_payload(*witnesses: ReadWitness) -> dict[str, Any]:
    return ReadDependencies(
        witnesses=tuple(
            WitnessReference(
                artifact_id=item.artifact_id,
                witness_digest=item.digest,
                query_digest=item.query.digest,
                expected_result=item.result,
            )
            for item in witnesses
        )
    ).model_dump(mode="json")


def _validate_source_observation(
    workspace: Any, proposal: VersionedObject, coverage: dict[str, Any], now: str,
) -> None:
    reference = proposal.payload["source_observation_ref"]
    try:
        observation = workspace.store.load_artifact(reference, "application/json").payload
        source = re.search(rf"\(({GUID[1:-1]})\)#([a-z][a-z0-9_]{{0,127}})$", observation["source_ref"])
        if source is None or not isinstance(observation["revision"], str) or not observation["revision"]:
            raise ValueError("invalid source identity")
        if observation["value_digest"] != sha256_digest(proposal.payload["canonical_value"]):
            raise ValueError("source value differs from proposal")
        record_id, field = source.groups()
        expected = "source-observation:" + sha256_digest({
            "connector": coverage.get("connector_id"), "record": record_id,
            "revision": observation["revision"], "field": field,
        })[7:]
    except (KeyError, TypeError, ValueError) as error:
        raise IntegrityError("SOURCE_OBSERVATION_BINDING_INVALID") from error
    if coverage.get("status") != "COMPLETE" or coverage.get("reasons"):
        raise ReadDependencyError("READ_DEPENDENCY_UNKNOWN", tuple(
            coverage.get("reasons") or ("SOURCE_COVERAGE_INCOMPLETE",)
        ))
    if reference != expected:
        raise ReadDependencyError("READ_DEPENDENCY_CHANGED", ("SOURCE_OBSERVATION_BINDING_CHANGED",))
    query = ReadQuery(
        connector_id=coverage["connector_id"], record_ids=(record_id,),
        operator="is_missing", field=field,
    )
    result = observe_query(query, coverage, now=now)
    if result.result == "UNKNOWN":
        raise ReadDependencyError("READ_DEPENDENCY_UNKNOWN", result.reasons)
    # Opaque revisions identify an observation; only the committed coverage
    # selects the current one. A same-value new revision still replaces it.
    if coverage["records"][record_id]["revision"] != observation["revision"]:
        raise ReadDependencyError("READ_DEPENDENCY_CHANGED", ("SOURCE_OBSERVATION_REPLACED",))


def validate_source_observations(workspace: Any, proposals: Iterable[VersionedObject], now: str) -> None:
    selected = tuple(item for item in proposals
                     if item.state == ObjectState.PROPOSED and item.version.startswith("source-"))
    if not selected:
        return
    references = [item.payload.get("source_observation_ref") for item in selected]
    if any(not isinstance(ref, str) or not re.fullmatch(r"source-observation:[0-9a-f]{64}", ref)
           for ref in references):
        raise IntegrityError("SOURCE_OBSERVATION_REFERENCE_INVALID")
    selected_now = _now(workspace, now)
    _lock_scope_if_transaction(workspace)
    coverage = _coverage(workspace, now=selected_now)
    for proposal in selected:
        _validate_source_observation(workspace, proposal, coverage, selected_now)


def validate_read_dependencies(workspace: Any, premises: Iterable[VersionedObject], now: str) -> None:
    dependencies = [
        item.payload["read_dependencies"] for item in premises if "read_dependencies" in item.payload
    ]
    if not dependencies:
        return
    try:
        references = tuple(
            reference
            for payload in dependencies
            for reference in ReadDependencies.model_validate(payload).witnesses
        )
    except (TypeError, ValueError) as error:
        raise IntegrityError("READ_DEPENDENCIES_CONTRACT_INVALID") from error
    selected_now = _now(workspace, now)
    _lock_scope_if_transaction(workspace)
    coverage = _coverage(workspace, now=selected_now)
    for reference in references:
        try:
            stored = workspace.store.load_artifact(reference.artifact_id, MEDIA_TYPE)
            witness = ReadWitness.model_validate(stored.payload)
        except (KeyError, TypeError, ValueError) as error:
            raise IntegrityError("READ_DEPENDENCY_WITNESS_INVALID") from error
        if (
            witness.digest != reference.witness_digest
            or witness.artifact_id != reference.artifact_id
            or witness.query.digest != reference.query_digest
            or witness.result != reference.expected_result
        ):
            raise IntegrityError("READ_DEPENDENCY_WITNESS_BINDING_INVALID")
        if coverage.get("status") == "COMPLETE" and witness.coverage.coverage_digest != coverage.get(
            "coverage_digest"
        ):
            raise ReadDependencyError("READ_DEPENDENCY_CHANGED")
        observation = observe_query(witness.query, coverage, now=selected_now)
        if observation.result == "UNKNOWN":
            raise ReadDependencyError("READ_DEPENDENCY_UNKNOWN", observation.reasons)
        current_binding = CoverageBinding.model_validate(
            {key: coverage[key] for key in CoverageBinding.model_fields}
        )
        if (
            witness.coverage != current_binding
            or witness.result != observation.result
            or witness.matched_record_ids != observation.matched_record_ids
        ):
            raise ReadDependencyError("READ_DEPENDENCY_CHANGED")


def require_read_dependency_validator(
    premises: tuple[VersionedObject, ...], now: str, validator: ReadDependencyValidator | None
) -> None:
    if not any("read_dependencies" in item.payload for item in premises):
        return
    if validator is None:
        raise IntegrityError("READ_DEPENDENCY_VALIDATOR_REQUIRED")
    validator(premises, now)
