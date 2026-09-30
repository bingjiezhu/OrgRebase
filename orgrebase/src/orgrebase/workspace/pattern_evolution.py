"""Bounded case learning over the existing Skill execution and release path.

Corpus admission, evaluation and governance are controller capabilities. The
learner receives training observations only; an executing Skill receives public
inputs only. Local scripted principals are never represented as human review.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Literal

from pydantic import Field, JsonValue, model_validator

from orgrebase.auth import AuthenticationError
from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    AuthorizationError,
    ContentAddressedModel,
    CoverageBasis,
    DependencyEdge,
    DependencyStrength,
    EdgeStatus,
    IntegrityError,
    ObjectState,
    VersionedObject,
)
from orgrebase.fixture import EnterpriseFixture
from orgrebase.impact import ImpactEngine
from orgrebase.store import StateStore
from orgrebase.workspace.skill_packages import (
    PARTITIONS,
    QUOTE_DIAGNOSTIC_REASON_CATALOG,
    SKILL_REGISTRY_AUTHORITY,
    InvocationContext,
    LoadedSkillPackage,
    SkillCandidateOverlayRegistry,
    SkillContentBundle,
    SkillEvaluationCase,
    SkillInvocation,
    SkillPackageEvaluator,
    SkillPackageRegistry,
    SkillReleaseLedger,
    load_skill_package_snapshot,
    quote_diagnostic_policy_digest,
    skill_package_snapshot,
)

MEDIA = "application/vnd.orgrebase.pattern-evolution+json"
OBSERVATION_MEDIA = "application/vnd.orgrebase.pattern-evaluation-observations+json"
OBSERVATION_SCHEMA = "orgrebase.pattern-evaluation-observations.v1"
PACKAGE_SNAPSHOT_MEDIA = "application/vnd.orgrebase.skill-package-snapshot+json"
BOUNDARY = "LOCAL_GOVERNED_CANDIDATE_NOT_ENTERPRISE_EFFECTIVENESS"

_PERSISTABLE_INVOCATION_REASONS = frozenset(
    {
        "PATTERN_INVOCATION_CAPTURE_INVALIDATED",
        "PATTERN_INVOCATION_DEPENDENCY_DRIFT",
        "PATTERN_RESTORATION_CAPTURE_INVALIDATED",
        "PATTERN_SOURCE_RETRACTED",
    }
)


def _invocation_failure(error: Exception) -> tuple[str, str]:
    """Map failures to stable audit codes without persisting exception text."""

    if isinstance(error, AuthenticationError):
        return "FAILED", "PATTERN_INVOCATION_AUTHORIZATION_REVOKED"
    if isinstance(error, AuthorizationError):
        return "FAILED", "PATTERN_INVOCATION_AUTHORIZATION_DENIED"
    if isinstance(error, IntegrityError):
        value = str(error)
        return (
            "FAILED",
            value
            if value in _PERSISTABLE_INVOCATION_REASONS
            else "PATTERN_INVOCATION_INTEGRITY_FAILED",
        )
    if isinstance(error, ValueError):
        return "FAILED", "PATTERN_INVOCATION_INPUT_INVALID"
    return "RESULT_UNKNOWN", "PATTERN_INVOCATION_RESULT_UNKNOWN"


def _record(kind: str, **body: Any) -> dict[str, Any]:
    value = {"schema_version": "orgrebase.pattern-evolution.v1", "kind": kind, **body}
    return {**value, "digest": sha256_digest(value)}


def _verify(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    if result.get("digest") != sha256_digest({k: v for k, v in result.items() if k != "digest"}):
        raise IntegrityError("PATTERN_EVIDENCE_DIGEST_MISMATCH")
    return result


def _identity(record: Mapping[str, Any]) -> str:
    return f"pattern-evolution:{record['kind']}:{record['digest'].removeprefix('sha256:')}"


def _observation_id(evaluation: Mapping[str, Any]) -> str:
    return "pattern-evaluation-observations:" + evaluation["digest"].removeprefix("sha256:")


def _observed_side(
    side: Mapping[str, Any], receipt: Mapping[str, Any], cases: list[dict[str, Any]]
) -> dict[str, Any]:
    """Check retained result bodies against the original evaluation's exact digests."""
    if (
        not isinstance(side, dict)
        or not isinstance(side.get("manifest"), dict)
        or not isinstance(side.get("cases"), list)
    ):
        raise IntegrityError("PATTERN_EVALUATION_OBSERVATION_INVALID")
    manifest = side["manifest"]
    package_digest = manifest["manifest_digest"]
    if (
        sha256_digest({k: v for k, v in manifest.items() if k != "manifest_digest"}) != package_digest
        or package_digest != receipt["premise_lock"]["package"]
        or manifest["program_content_digest"] != receipt["candidate_program_digest"]
    ):
        raise IntegrityError("PATTERN_EVALUATION_OBSERVATION_PACKAGE_MISMATCH")
    rows = side["cases"]
    if any(not isinstance(row, dict) or not isinstance(row.get("result"), dict) for row in rows):
        raise IntegrityError("PATTERN_EVALUATION_OBSERVATION_INVALID")
    expected = {case["case_id"]: case for case in cases}
    outcomes = {case["case_ref"]: case for case in receipt["case_results"]}
    if (
        len(rows) != len(expected)
        or len(outcomes) != len(expected)
        or {row["case_ref"] for row in rows} != set(expected)
        or set(outcomes) != set(expected)
    ):
        raise IntegrityError("PATTERN_EVALUATION_OBSERVATION_CASE_SET_MISMATCH")
    for row in rows:
        result = row["result"]
        receipt = row.get("receipt")
        if (
            row["input_digest"] != sha256_digest(expected[row["case_ref"]]["public_input"])
            or sha256_digest(result) != outcomes[row["case_ref"]]["candidate_output_digest"]
            or result.get("package_digest") != package_digest
            or not isinstance(result.get("action"), str)
            or not result["action"]
            or result.get("candidate_only") is not True
            or result.get("target_writes") != 0
        ):
            raise IntegrityError("PATTERN_EVALUATION_OBSERVATION_RESULT_MISMATCH")
        if receipt is not None:
            receipt = _verify(receipt)
            if (
                receipt.get("input_digest") != row["input_digest"]
                or receipt.get("output_digest") != sha256_digest(result)
                or receipt.get("package_digest") != package_digest
                or receipt.get("candidate_only") is not True
                or receipt.get("target_writes") != 0
            ):
                raise IntegrityError("PATTERN_EVALUATION_OBSERVATION_RECEIPT_MISMATCH")
    return {row["case_ref"]: row for row in rows}


def _features(public_input: Mapping[str, Any], paths: tuple[str, ...]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for path in paths:
        current: Any = public_input
        for part in path.split("/")[1:]:
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, Mapping) or part not in current:
                current = {"observation": "MISSING"}
                break
            current = current[part]
        if isinstance(current, (dict, list)) and current != {"observation": "MISSING"}:
            raise IntegrityError("PATTERN_FEATURE_MUST_BE_SCALAR")
        values[path] = current
    return values


def _independent_case_membership(
    corpus: Mapping[str, Any], pattern: Mapping[str, Any]
) -> dict[str, list[str]]:
    """Retain every version while counting one conservative outcome per case."""
    refs = set(pattern["case_refs"])
    observed: dict[str, set[str]] = {}
    resolved = set()
    for row in corpus["cases"]:
        case = row["case"]
        if case["digest"] in refs:
            resolved.add(case["digest"])
            observed.setdefault(case["case_id"], set()).add(case["outcome"])
    if resolved != refs:
        raise IntegrityError("PATTERN_CASE_IDENTITY_BINDING_MISMATCH")
    membership: dict[str, list[str]] = {
        outcome: [] for outcome in ("SUPPORT", "COUNTEREXAMPLE", "NULL", "UNKNOWN")
    }
    for case_id, outcomes in sorted(observed.items()):
        outcome = next(name for name in ("COUNTEREXAMPLE", "UNKNOWN", "NULL", "SUPPORT") if name in outcomes)
        membership[outcome].append(case_id)
    return membership


def _has_independent_support(evaluation: Mapping[str, Any]) -> bool:
    membership = evaluation.get("independent_case_membership")
    if not isinstance(membership, dict) or set(membership) != {
        "SUPPORT",
        "COUNTEREXAMPLE",
        "NULL",
        "UNKNOWN",
    }:
        return False
    if any(
        not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values)
        for values in membership.values()
    ):
        return False
    cases = [case_id for values in membership.values() for case_id in values]
    return (
        len(cases) == len(set(cases))
        and len(membership["SUPPORT"]) >= 2
        and bool(membership["COUNTEREXAMPLE"])
    )


def _quote_recovery_business_oracle(
    candidate: Mapping[str, Any],
    suite: Mapping[str, Any],
    baseline_rows: tuple[dict[str, Any], ...],
    candidate_rows: tuple[dict[str, Any], ...],
) -> dict[str, Any] | None:
    """Score one frozen deterministic profile without exposing gold to its consumer."""

    content = candidate.get("content_bundle")
    if content is None:
        return None
    bundle = SkillContentBundle.from_payload(content)
    expected = {item["case_id"]: item["expected_action"] for item in suite["cases"]}
    partitions = {item["case_id"]: item["partition"] for item in suite["cases"]}
    baseline = {row["case_ref"]: row for row in baseline_rows}
    current = {row["case_ref"]: row for row in candidate_rows}
    if set(expected) != set(baseline) or set(expected) != set(current):
        raise IntegrityError("PATTERN_BUSINESS_ORACLE_CASE_SET_MISMATCH")
    repairs = regressions = 0
    applicable = consumed = 0
    rows = []
    for case_id in sorted(expected):
        left = baseline[case_id]
        right = current[case_id]
        before = left["result"].get("action") == expected[case_id]
        after = right["result"].get("action") == expected[case_id]
        repairs += int(not before and after)
        regressions += int(before and not after)
        trace = right.get("receipt", {}).get("candidate_content")
        if not isinstance(trace, Mapping) or trace.get("bundle_digest") != bundle.digest:
            raise IntegrityError("PATTERN_BUSINESS_ORACLE_CONTENT_TRACE_MISSING")
        if trace.get("applicable") is True:
            applicable += 1
            if trace.get("consumed_resource_digests"):
                consumed += 1
        rows.append(
            {
                "case_ref": case_id,
                "partition": partitions[case_id],
                "expected_action": expected[case_id],
                "baseline_action": left["result"].get("action"),
                "candidate_action": right["result"].get("action"),
                "repaired_baseline_failure": not before and after,
                "regressed_baseline_success": before and not after,
                "content_applicable": trace.get("applicable") is True,
                "content_consumed": bool(trace.get("consumed_resource_digests")),
            }
        )
    all_candidate_correct = all(
        row["candidate_action"] == row["expected_action"] for row in rows
    )
    holdout_repaired = any(
        row["partition"] == "HELD_OUT" and row["repaired_baseline_failure"]
        for row in rows
    )
    improved = (
        repairs > 0
        and regressions == 0
        and all_candidate_correct
        and holdout_repaired
        and applicable > 0
        and consumed == applicable
    )
    status = "IMPROVED" if improved else "REGRESSED" if regressions else "NO_BEHAVIOR_DELTA"
    return _record(
        "quote-recovery-business-oracle",
        profile_id=bundle.payload["profile_id"],
        bundle_digest=bundle.digest,
        suite_digest=suite["digest"],
        status=status,
        repaired_case_count=repairs,
        regressed_case_count=regressions,
        applicable_case_count=applicable,
        consumed_case_count=consumed,
        holdout_repaired=holdout_repaired,
        all_candidate_correct=all_candidate_correct,
        rows=rows,
        model_invocations=0,
        model_cost_usd=0,
        target_writes=0,
        scope="CONTROLLED_LOCAL_DETERMINISTIC_ORACLE_NOT_CUSTOMER_EFFECTIVENESS",
    )


class FeatureProfile(ContentAddressedModel):
    profile_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    paths: tuple[str, ...] = Field(min_length=1)
    transfer_scope: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_paths(self):
        if self.paths != tuple(sorted(set(self.paths))) or any(
            not path.startswith("/") or "//" in path for path in self.paths
        ):
            raise ValueError("PATTERN_FEATURE_PATH_SET_INVALID")
        return self


class CaseObservation(ContentAddressedModel):
    case_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    public_input: dict[str, JsonValue]
    outcome: Literal["SUPPORT", "COUNTEREXAMPLE", "NULL", "UNKNOWN"]
    certificate: dict[str, JsonValue]

    @model_validator(mode="after")
    def verify_certificate(self):
        certificate = _verify(self.certificate)
        if (
            certificate.get("case_id"),
            certificate.get("case_revision"),
            certificate.get("input_digest"),
            certificate.get("outcome"),
        ) != (self.case_id, self.revision, sha256_digest(self.public_input), self.outcome):
            raise ValueError("PATTERN_CASE_CERTIFICATE_BINDING_MISMATCH")
        return self


class ReplayCase(ContentAddressedModel):
    case_id: str = Field(min_length=1)
    partition: Literal[
        "REPLAY",
        "HELD_OUT",
        "NEGATIVE_TRANSFER",
        "PERMISSION",
        "INJECTION",
        "MALFORMED",
        "RESOURCE_OR_DEADLINE",
        "CANARY",
    ]
    role: Literal["HELD_OUT", "COUNTERFACTUAL", "PRIOR_VERSION", "REGRESSION"]
    public_input: dict[str, JsonValue]
    expected_action: str = Field(min_length=1)
    counterfactual_of: str | None = None


class SkillBoundary(ContentAddressedModel):
    preconditions: tuple[str, ...] = Field(min_length=1)
    required_knowledge: tuple[str, ...] = Field(min_length=1)
    required_qualifications: tuple[str, ...] = Field(min_length=1)
    allowed_tools: tuple[str, ...] = ()
    effect_ceiling: Literal["CANDIDATE_ONLY_ZERO_TARGET_WRITES"] = "CANDIDATE_ONLY_ZERO_TARGET_WRITES"
    evidence_duties: tuple[str, ...] = Field(min_length=1)
    rollback_package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    known_failure_envelope: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_boundaries(self):
        for values in (
            self.preconditions,
            self.required_knowledge,
            self.required_qualifications,
            self.evidence_duties,
            self.known_failure_envelope,
            self.allowed_tools,
        ):
            if len(values) != len(set(values)) or any(not value.strip() for value in values):
                raise ValueError("PATTERN_SKILL_BOUNDARY_VALUES_INVALID")
        return self


class PatternCandidate(ContentAddressedModel):
    schema_version: Literal["orgrebase.pattern-evolution.v1"] = "orgrebase.pattern-evolution.v1"
    kind: Literal["pattern"] = "pattern"
    proposal_ref: str
    corpus_ref: str
    feature_profile_digest: str
    feature_values: dict[str, JsonValue]
    feature_root: str
    membership: dict[Literal["SUPPORT", "COUNTEREXAMPLE", "NULL", "UNKNOWN"], tuple[str, ...]]
    case_refs: tuple[str, ...]
    certificate_refs: tuple[str, ...]
    status: Literal["CANDIDATE"] = "CANDIDATE"
    similarity_edges: Literal[0] = 0

    @model_validator(mode="after")
    def validate_membership(self):
        if set(self.membership) != {"SUPPORT", "COUNTEREXAMPLE", "NULL", "UNKNOWN"}:
            raise ValueError("PATTERN_OUTCOME_CLASSES_INCOMPLETE")
        members = [ref for values in self.membership.values() for ref in values]
        if (
            len(members) != len(set(members))
            or set(members) != set(self.case_refs)
            or len(self.certificate_refs) < len(self.case_refs)
            or len(set(self.certificate_refs)) != len(self.certificate_refs)
            or self.feature_root != sha256_digest(self.feature_values)
        ):
            raise ValueError("PATTERN_MEMBERSHIP_BINDING_INVALID")
        return self


class PatternSkillCandidate(ContentAddressedModel):
    schema_version: Literal["orgrebase.pattern-evolution.v1"] = "orgrebase.pattern-evolution.v1"
    kind: Literal["skill-candidate"] = "skill-candidate"
    proposal_ref: str
    pattern_ref: str
    corpus_ref: str
    replay_ref: str
    skill_name: str
    base_package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_head_ref: str | None = None
    base_head_digest: str | None = None
    base_head_generation: int | None = Field(default=None, ge=0)
    boundary: SkillBoundary
    status: Literal["CANDIDATE"] = "CANDIDATE"
    executable: Literal[False] = False
    author_id: str
    principal_binding: dict[str, JsonValue] | None = None
    content_bundle: dict[str, JsonValue] | None = None
    diagnostic_reason_map: dict[str, str] | None = None
    diagnostic_policy_digest: str | None = None
    claim_boundary: Literal["LOCAL_GOVERNED_CANDIDATE_NOT_ENTERPRISE_EFFECTIVENESS"] = BOUNDARY


class _LegacyPatternSkillCandidate(ContentAddressedModel):
    """The exact original S05 candidate shape; retained for byte-preserving reads."""

    schema_version: Literal["orgrebase.pattern-evolution.v1"] = "orgrebase.pattern-evolution.v1"
    kind: Literal["skill-candidate"] = "skill-candidate"
    proposal_ref: str
    pattern_ref: str
    corpus_ref: str
    replay_ref: str
    skill_name: str
    base_package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    boundary: SkillBoundary
    status: Literal["CANDIDATE"] = "CANDIDATE"
    executable: Literal[False] = False
    author_id: str
    claim_boundary: Literal["LOCAL_GOVERNED_CANDIDATE_NOT_ENTERPRISE_EFFECTIVENESS"] = BOUNDARY


class ProposalBudget(ContentAddressedModel):
    max_cases: int = Field(ge=1, le=10000)
    max_skill_invocations: int = Field(ge=1, le=100000)
    max_seconds: float = Field(gt=0, le=86400, allow_inf_nan=False)


class SkillInvocationBudget(ContentAddressedModel):
    max_invocations: int = Field(ge=1, le=100000)
    max_seconds: float = Field(gt=0, le=86400, allow_inf_nan=False)


@dataclass(frozen=True)
class _ProductionInvocationAuthority:
    candidate_ref: str
    reviewed_bundle_digest: str
    production_chain_digest: str
    scope_digest: str


class _EffectiveSkillRegistry(SkillPackageRegistry):
    """Use one verified persisted parent with the existing interpreter."""

    def __init__(self, installed: SkillPackageRegistry, package: LoadedSkillPackage) -> None:
        self.installed = installed
        self.package = package
        self.resource_mode = "PERSISTED_EFFECTIVE_PACKAGE"

    def _project_license_files(self) -> dict[str, Any]:
        return self.installed._project_license_files()

    def load(
        self, name: str, *, expected_package_digest: str | None = None
    ) -> LoadedSkillPackage:
        package = self.package if name == self.package.name else self.installed.load(name)
        if expected_package_digest is not None and expected_package_digest != package.package_digest:
            raise IntegrityError("SKILL_PACKAGE_MANIFEST_DIGEST_MISMATCH")
        return load_skill_package_snapshot(skill_package_snapshot(package))


class GovernedPatternService:
    """Persisted learning and admission facade; no alternate Skill interpreter."""

    def __init__(
        self,
        store: StateStore,
        *,
        corpus_authority: str,
        evaluator_authority: str,
        governance_authority: str,
        registry: SkillPackageRegistry | None = None,
        clock: Callable[[], float] = time.time,
        prerequisite_resolver: Callable[[str, str], bool] | None = None,
        invocation_budget: SkillInvocationBudget | None = None,
        case_evidence_resolver: Callable[[CaseObservation], tuple[str, ...]] | None = None,
        skill_dependency_resolver: Callable[[str], Mapping[str, str]] | None = None,
        before_invocation_result_commit: Callable[[str], None] | None = None,
    ) -> None:
        authorities = (corpus_authority, evaluator_authority, governance_authority)
        if len(set(authorities)) != 3 or not all(authorities):
            raise ValueError("PATTERN_CONTROLLER_AUTHORITIES_MUST_BE_DISTINCT")
        self.store = store
        self.registry = registry or SkillPackageRegistry()
        self.corpus_authority, self.evaluator_authority, self.governance_authority = authorities
        self.clock = clock
        self.prerequisite_resolver = prerequisite_resolver or (lambda actor, ref: False)
        self.case_evidence_resolver = case_evidence_resolver
        self.skill_dependency_resolver = skill_dependency_resolver or (
            lambda name: self.registry.load(name).manifest["dependencies"]
        )
        self.before_invocation_result_commit = before_invocation_result_commit
        self._production_authority_lock = Lock()
        self._production_invocation_authorities: dict[
            int, _ProductionInvocationAuthority
        ] = {}
        self.invocation_budget = (
            invocation_budget or SkillInvocationBudget(max_invocations=100, max_seconds=3600)
        ).revalidated()

    @contextmanager
    def _transaction(self):
        with self.store.transaction() as connection:
            if self.store.backend == "postgresql":
                value = int(
                    sha256_digest({"scope": "pattern-evolution", "workspace": self.store.workspace_id})[-16:],
                    16,
                )
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (value - (1 << 63),))
            yield connection

    def _authority(self, actual: str, expected: str) -> None:
        if actual != expected:
            raise AuthorizationError("PATTERN_CONTROLLER_AUTHORITY_DENIED")

    def _phase_principal_binding(
        self,
        binding: Mapping[str, Any] | None,
        *,
        actor_id: str,
        role_phase: str,
    ) -> dict[str, Any] | None:
        if binding is None:
            return None
        value = deepcopy(dict(binding))
        if (
            set(value)
            != {
                "schema_version",
                "identity_mode",
                "issuer",
                "issuer_digest",
                "subject",
                "subject_digest",
                "tenant_id",
                "workspace_id",
                "actor_id",
                "expires_at",
                "scope_digest",
                "role_phase",
            }
            or value.get("schema_version") != "orgrebase.pattern-principal-binding.v1"
            or value.get("identity_mode") != "VERIFIED_PRINCIPAL_IDENTITY"
            or not isinstance(value.get("issuer"), str)
            or not value["issuer"]
            or value.get("issuer_digest") != sha256_digest(value["issuer"])
            or not isinstance(value.get("subject"), str)
            or not value["subject"]
            or value.get("subject_digest") != sha256_digest(value["subject"])
            or value.get("tenant_id") != self.store.tenant_id
            or value.get("workspace_id") != self.store.workspace_id
            or value.get("actor_id") != actor_id
            or type(value.get("expires_at")) is not int
            or value["expires_at"] <= 0
            or not isinstance(value.get("scope_digest"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", value["scope_digest"]) is None
            or value.get("role_phase") != role_phase
        ):
            raise AuthorizationError("PATTERN_PHASE_PRINCIPAL_BINDING_INVALID")
        return value

    def _save(self, connection: Any, value: Mapping[str, Any]) -> str:
        record = _verify(value)
        ref = _identity(record)
        self.store.save_artifact(connection, ref, MEDIA, record)
        return ref

    def _load(self, ref: str, kind: str | None = None) -> dict[str, Any]:
        record = _verify(self.store.load_artifact(ref, MEDIA).payload)
        if _identity(record) != ref or (kind is not None and record["kind"] != kind):
            raise IntegrityError("PATTERN_ARTIFACT_IDENTITY_MISMATCH")
        if record["kind"] == "pattern":
            PatternCandidate.model_validate(record)
        elif record["kind"] == "skill-candidate":
            if "base_head_ref" in record:
                PatternSkillCandidate.model_validate(record)
            else:
                _LegacyPatternSkillCandidate.model_validate(record)
        return record

    def _family(self, kind: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            _verify(item.payload)
            for item in self.store.list_artifacts(
                artifact_id_prefix=f"pattern-evolution:{kind}:", expected_media_type=MEDIA
            )
        )

    def _case_certificate_refs(self, case: CaseObservation) -> tuple[str, ...]:
        own_certificate = case.certificate["digest"]
        if case.certificate.get("kind") not in {
            "retail-outcome-case-candidate",
            "quote-evidence-recovery-case-candidate",
        }:
            return (str(own_certificate),)
        if self.case_evidence_resolver is None:
            raise AuthorizationError("PATTERN_CASE_EVIDENCE_RESOLVER_REQUIRED")
        resolved = self.case_evidence_resolver(case)
        if (
            not isinstance(resolved, tuple)
            or not resolved
            or any(
                not isinstance(ref, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", ref) is None
                for ref in resolved
            )
            or own_certificate in resolved
            or len(set(resolved)) != len(resolved)
        ):
            raise IntegrityError("PATTERN_CASE_RESOLVED_CERTIFICATES_INVALID")
        return tuple(sorted((str(own_certificate), *resolved)))

    def freeze_corpus(
        self,
        profile: FeatureProfile,
        cases: tuple[CaseObservation, ...],
        *,
        actor_id: str,
        principal_binding: Mapping[str, Any] | None = None,
        authorization_check: Callable[[], None] | None = None,
    ) -> str:
        self._authority(actor_id, self.corpus_authority)
        phase_principal = self._phase_principal_binding(
            principal_binding, actor_id=actor_id, role_phase="CORPUS_FREEZE"
        )
        if authorization_check is not None:
            authorization_check()
        profile = profile.revalidated()
        selected = tuple(case.revalidated() for case in cases)
        ids = [(case.case_id, case.revision) for case in selected]
        if not selected or len(ids) != len(set(ids)):
            raise IntegrityError("PATTERN_CORPUS_CASE_SET_INVALID")
        rows = []
        for case in sorted(selected, key=lambda item: (item.case_id, item.revision)):
            if case.certificate.get("issuer") != actor_id:
                raise AuthorizationError("PATTERN_CASE_CERTIFICATE_AUTHORITY_DENIED")
            rows.append(
                {
                    "case": case.model_dump(mode="json"),
                    "features": _features(case.public_input, profile.paths),
                    "certificate_refs": list(self._case_certificate_refs(case)),
                }
            )
        corpus = _record(
            "corpus",
            profile=profile.model_dump(mode="json"),
            cases=rows,
            issuer=actor_id,
            principal_binding=phase_principal,
            claim_boundary=BOUNDARY,
        )
        with self._transaction() as connection:
            if authorization_check is not None:
                authorization_check()
                self.store.require_before_commit(connection, authorization_check)
            corpus_ref = self._save(connection, corpus)
            for row in rows:
                for certificate_ref in row["certificate_refs"]:
                    self._link(connection, certificate_ref, row["case"]["digest"], "certificate", "case")
            return corpus_ref

    def freeze_corpus_v2(
        self,
        *,
        profile_id: str,
        assessment_refs: tuple[str, ...],
        assessment_service: Any,
        actor_id: str,
        principal_binding: Mapping[str, Any],
        authorization_check: Callable[[], None],
    ) -> str:
        """Freeze reviewed experience without reinterpreting v1 case labels.

        The corpus actor and the independent assessment issuer have distinct
        current Principals.  Every positive cluster is backed by rechecked
        source-basis proof; provisional collector hints are never counted.
        """

        self._authority(actor_id, self.corpus_authority)
        if (
            not profile_id
            or not assessment_refs
            or len(assessment_refs) != len(set(assessment_refs))
            or assessment_service.workspace.store is not self.store
            or assessment_service.evaluator_actor_id != self.evaluator_authority
            or actor_id == self.evaluator_authority
            or authorization_check is None
        ):
            raise IntegrityError("PATTERN_V2_CORPUS_SCOPE_INVALID")
        binding = self._phase_principal_binding(
            principal_binding, actor_id=actor_id, role_phase="CORPUS_FREEZE"
        )
        if binding is None:
            raise AuthorizationError("PATTERN_V2_CORPUS_PRINCIPAL_REQUIRED")

        def selected_rows() -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
            authorization_check()
            rows: list[dict[str, Any]] = []
            cases: set[str] = set()
            selected_clusters: set[str] = set()
            basis_owner: dict[str, str] = {}
            clustered: dict[str, set[str]] = {}
            unknown_refs: set[str] = set()
            for assessment_ref in sorted(assessment_refs):
                case, assessment, proof = assessment_service.verify_assessment_for_corpus(
                    assessment_ref, authorized_corpus_actor_id=actor_id
                )
                selection = assessment_service.revision_selection_for_corpus(
                    assessment_ref, authorized_corpus_actor_id=actor_id,
                )
                if (
                    case.profile_id not in {
                        profile_id, "workspace-quote-evidence-recovery-v1"
                    }
                    or assessment.profile_id != profile_id
                    or case.case_id in cases
                    or case.observation_status != "OBSERVED"
                ):
                    raise IntegrityError("PATTERN_V2_CORPUS_CASE_INVALID")
                cases.add(case.case_id)
                if assessment.independence_cluster_id is not None:
                    if assessment.independence_cluster_id in selected_clusters:
                        raise IntegrityError("PATTERN_V2_CORPUS_CLUSTER_DUPLICATED")
                    selected_clusters.add(assessment.independence_cluster_id)
                if assessment.verdict in {"SUPPORT", "COUNTEREXAMPLE"}:
                    if (
                        proof is None
                        or assessment.independence_cluster_id != proof.independence_cluster_id
                        or case.cluster_status == "UNDETERMINED"
                    ):
                        raise IntegrityError("PATTERN_V2_CLUSTER_PROOF_REQUIRED")
                    cluster = proof.independence_cluster_id
                    for basis in proof.basis_ref_digests:
                        old_cluster = basis_owner.setdefault(basis, cluster)
                        if old_cluster != cluster:
                            raise IntegrityError("PATTERN_V2_CLUSTER_BASIS_OVERLAP")
                    clustered.setdefault(cluster, set()).add(assessment.verdict)
                else:
                    if proof is not None or assessment.independence_cluster_id is not None:
                        raise IntegrityError("PATTERN_V2_UNTRUSTED_CLUSTER_LABEL")
                    unknown_refs.add(assessment_ref)
                rows.append(
                    {
                        "case_ref": assessment.case_ref,
                        "case_digest": case.digest,
                        "case_id": case.case_id,
                        "case_revision": case.revision,
                        "assessment_ref": assessment_ref,
                        "assessment_digest": assessment.digest,
                        "assessment_verdict": assessment.verdict,
                        "cluster_id": assessment.independence_cluster_id,
                        "cluster_proof_ref": assessment.cluster_proof_ref,
                        "cluster_proof_digest": assessment.cluster_proof_digest,
                        "basis_ref_digests": list(proof.basis_ref_digests) if proof else [],
                        "revision_selection_digest": selection["digest"],
                        "revision_selection_policy": selection["policy_version"],
                        "revision_selection_member_digest": selection["member_digest"],
                        "revision_selection_members": selection["members"],
                        "revision_selection_excluded_refs": list(selection["historical_counter_refs"]),
                    }
                )
            membership: dict[str, list[str]] = {
                "SUPPORT": [], "COUNTEREXAMPLE": [], "UNKNOWN": sorted(unknown_refs)
            }
            for cluster, outcomes in sorted(clustered.items()):
                category = "COUNTEREXAMPLE" if "COUNTEREXAMPLE" in outcomes else "SUPPORT"
                membership[category].append(cluster)
            return rows, membership

        rows, membership = selected_rows()
        record = _record(
            "corpus-v2",
            profile_id=profile_id,
            issuer=actor_id,
            principal_binding=binding,
            cases=rows,
            independent_cluster_membership=membership,
            revision_selection_policy="latest-observed-case-and-independent-repair.v1",
            revision_selection_member_digest=sha256_digest([
                row["revision_selection_digest"] for row in rows
            ]),
            source_qualification="REVALIDATE_BEFORE_EVALUATION_AND_ADMISSION",
            claim_boundary=BOUNDARY,
        )
        with self._transaction() as connection:
            self.store.require_before_commit(connection, authorization_check)
            fresh_rows, fresh_membership = selected_rows()
            if fresh_rows != rows or fresh_membership != membership:
                raise IntegrityError("PATTERN_V2_CORPUS_INPUT_CHANGED")
            corpus_ref = self._save(connection, record)
            for row in rows:
                self._link(
                    connection, row["assessment_ref"], corpus_ref,
                    "assessment", "corpus-v2",
                )
            return corpus_ref

    def revalidate_corpus_v2(
        self, corpus_ref: str, *, assessment_service: Any, actor_id: str,
        authorization_check: Callable[[], None],
    ) -> dict[str, Any]:
        """Recheck every assessment/source/cluster before future use or restart."""
        self._authority(actor_id, self.corpus_authority)
        if (
            assessment_service.workspace.store is not self.store
            or assessment_service.evaluator_actor_id != self.evaluator_authority
        ):
            raise IntegrityError("PATTERN_V2_CORPUS_SCOPE_INVALID")
        authorization_check()
        record = self._load(corpus_ref, "corpus-v2")
        if record["issuer"] != actor_id:
            raise AuthorizationError("PATTERN_V2_CORPUS_ISSUER_MISMATCH")
        if (
            record.get("revision_selection_policy")
            != "latest-observed-case-and-independent-repair.v1"
            or record.get("revision_selection_member_digest") != sha256_digest([
                row.get("revision_selection_digest") for row in record["cases"]
            ])
        ):
            raise IntegrityError("PATTERN_V2_CORPUS_REVISION_SELECTION_INVALID")
        basis_owner: dict[str, str] = {}
        clusters: dict[str, set[str]] = {}
        unknown_refs: set[str] = set()
        for row in record["cases"]:
            case, assessment, proof = assessment_service.verify_assessment_for_corpus(
                row["assessment_ref"], authorized_corpus_actor_id=actor_id
            )
            selection = assessment_service.revision_selection_for_corpus(
                row["assessment_ref"], authorized_corpus_actor_id=actor_id,
            )
            if (
                assessment.profile_id != record["profile_id"]
                or case.digest != row["case_digest"]
                or case.case_id != row["case_id"]
                or case.revision != row["case_revision"]
                or assessment.digest != row["assessment_digest"]
                or assessment.verdict != row["assessment_verdict"]
                or assessment.independence_cluster_id != row["cluster_id"]
                or assessment.cluster_proof_ref != row["cluster_proof_ref"]
                or assessment.cluster_proof_digest != row["cluster_proof_digest"]
                or selection["digest"] != row.get("revision_selection_digest")
                or selection["policy_version"] != row.get("revision_selection_policy")
                or selection["member_digest"] != row.get("revision_selection_member_digest")
                or sha256_digest(selection["members"])
                != sha256_digest(row.get("revision_selection_members"))
                or list(selection["historical_counter_refs"]) != row.get("revision_selection_excluded_refs")
            ):
                raise IntegrityError("PATTERN_V2_CORPUS_INPUT_CHANGED")
            if proof is None:
                if row["basis_ref_digests"] or assessment.verdict in {"SUPPORT", "COUNTEREXAMPLE"}:
                    raise IntegrityError("PATTERN_V2_CORPUS_PROOF_CHANGED")
                unknown_refs.add(row["assessment_ref"])
                continue
            if list(proof.basis_ref_digests) != row["basis_ref_digests"]:
                raise IntegrityError("PATTERN_V2_CORPUS_PROOF_CHANGED")
            for basis in proof.basis_ref_digests:
                earlier = basis_owner.setdefault(basis, proof.independence_cluster_id)
                if earlier != proof.independence_cluster_id:
                    raise IntegrityError("PATTERN_V2_CLUSTER_BASIS_OVERLAP")
            clusters.setdefault(proof.independence_cluster_id, set()).add(assessment.verdict)
        membership = {
            "SUPPORT": [], "COUNTEREXAMPLE": [], "UNKNOWN": sorted(unknown_refs)
        }
        for cluster, verdicts in sorted(clusters.items()):
            category = "COUNTEREXAMPLE" if "COUNTEREXAMPLE" in verdicts else "SUPPORT"
            membership[category].append(cluster)
        if membership != record["independent_cluster_membership"]:
            raise IntegrityError("PATTERN_V2_CORPUS_MEMBERSHIP_CHANGED")
        return record

    def freeze_replay(self, cases: tuple[ReplayCase, ...], *, actor_id: str) -> str:
        self._authority(actor_id, self.evaluator_authority)
        cases = tuple(case.revalidated() for case in cases)
        ids = {case.case_id for case in cases}
        if len(ids) != len(cases) or {case.partition for case in cases} != set(PARTITIONS):
            raise IntegrityError("PATTERN_REPLAY_PARTITION_SET_INVALID")
        if not {"HELD_OUT", "COUNTERFACTUAL", "PRIOR_VERSION"}.issubset({case.role for case in cases}):
            raise IntegrityError("PATTERN_REPLAY_ROLE_SET_INCOMPLETE")
        for case in cases:
            if case.role == "COUNTERFACTUAL":
                if case.counterfactual_of not in ids or case.counterfactual_of == case.case_id:
                    raise IntegrityError("PATTERN_COUNTERFACTUAL_PAIR_MISSING")
                original = next(item for item in cases if item.case_id == case.counterfactual_of)
                if original.public_input == case.public_input:
                    raise IntegrityError("PATTERN_COUNTERFACTUAL_NOT_CHANGED")
            elif case.counterfactual_of is not None:
                raise IntegrityError("PATTERN_COUNTERFACTUAL_PAIR_UNEXPECTED")
        suite = _record(
            "replay-suite",
            cases=[case.model_dump(mode="json") for case in sorted(cases, key=lambda item: item.case_id)],
            issuer=actor_id,
        )
        with self._transaction() as connection:
            return self._save(connection, suite)

    def open_proposal(
        self,
        *,
        proposal_id: str,
        corpus_ref: str,
        replay_ref: str,
        skill_name: str,
        author_id: str,
        budget: ProposalBudget,
        principal_binding: Mapping[str, Any] | None = None,
        authorization_check: Callable[[], None] | None = None,
    ) -> str:
        if (
            not proposal_id
            or not author_id
            or author_id in {self.corpus_authority, self.evaluator_authority, self.governance_authority}
        ):
            raise AuthorizationError("PATTERN_LEARNER_AUTHORITY_COLLISION")
        corpus, suite = self._load(corpus_ref, "corpus"), self._load(replay_ref, "replay-suite")
        training_ids = {item["case"]["case_id"] for item in corpus["cases"]}
        if training_ids & {item["case_id"] for item in suite["cases"]}:
            raise IntegrityError("PATTERN_TRAINING_REPLAY_OVERLAP")
        training_inputs = {sha256_digest(item["case"]["public_input"]) for item in corpus["cases"]}
        held_out_inputs = {
            sha256_digest(item["public_input"]) for item in suite["cases"] if item["role"] == "HELD_OUT"
        }
        if training_inputs & held_out_inputs:
            raise IntegrityError("PATTERN_HELD_OUT_INPUT_LEAKAGE")
        budget = budget.revalidated()
        phase_principal = self._phase_principal_binding(
            principal_binding, actor_id=author_id, role_phase="PROPOSAL_OPEN"
        )
        if authorization_check is not None:
            authorization_check()
        base_head_ref, base_head_digest, base_head_generation, base_digest = (
            self._skill_head_capture(skill_name)
        )
        package = self._package_for_digest(skill_name, base_digest)
        proposal = _record(
            "proposal",
            proposal_id=proposal_id,
            corpus_ref=corpus_ref,
            replay_ref=replay_ref,
            author_id=author_id,
            principal_binding=phase_principal,
            skill_name=skill_name,
            base_package_digest=package.package_digest,
            base_head_ref=base_head_ref,
            base_head_digest=base_head_digest,
            base_head_generation=base_head_generation,
            budget=budget.model_dump(mode="json"),
            opened_at=self.clock(),
            status="CANDIDATE_ONLY",
            claim_boundary=BOUNDARY,
        )
        with self._transaction() as connection:
            if authorization_check is not None:
                authorization_check()
                self.store.require_before_commit(connection, authorization_check)
            if any(item["proposal_id"] == proposal_id for item in self._family("proposal")):
                raise IntegrityError("PATTERN_PROPOSAL_ID_ALREADY_USED")
            return self._save(connection, proposal)

    def _usage(self, proposal_ref: str) -> tuple[dict[str, Any], ...]:
        return tuple(item for item in self._family("usage") if item["proposal_ref"] == proposal_ref)

    def _terminal(self, proposal_ref: str) -> dict[str, Any] | None:
        return next((item for item in self._family("terminal") if item["proposal_ref"] == proposal_ref), None)

    def _charge(
        self, proposal_ref: str, *, cases: int = 0, invocations: int = 0, candidate_ref: str | None = None
    ) -> None:
        error = None
        with self._transaction() as connection:
            if candidate_ref is not None:
                self._require_current_candidate_evidence(candidate_ref)
            proposal = self._load(proposal_ref, "proposal")
            if self._terminal(proposal_ref) is not None:
                raise IntegrityError("PATTERN_PROPOSAL_TERMINAL")
            usage = self._usage(proposal_ref)
            case_count = sum(item["cases"] for item in usage) + cases
            call_count = sum(item["invocations"] for item in usage) + invocations
            now = self.clock()
            budget = proposal["budget"]
            if (
                case_count > budget["max_cases"]
                or call_count > budget["max_skill_invocations"]
                or now < proposal["opened_at"]
                or now - proposal["opened_at"] > budget["max_seconds"]
            ):
                self._save(
                    connection,
                    _record(
                        "terminal",
                        proposal_ref=proposal_ref,
                        status="EXHAUSTED",
                        reason="PROPOSAL_BUDGET_EXHAUSTED",
                        observed_at=now,
                    ),
                )
                error = "PATTERN_PROPOSAL_BUDGET_EXHAUSTED"
            else:
                self._save(
                    connection,
                    _record(
                        "usage",
                        proposal_ref=proposal_ref,
                        ordinal=len(usage),
                        cases=cases,
                        invocations=invocations,
                        observed_at=now,
                    ),
                )
        if error:
            raise IntegrityError(error)

    def abandon(self, proposal_ref: str, *, actor_id: str, reason: str) -> str:
        proposal = self._load(proposal_ref, "proposal")
        self._authority(actor_id, proposal["author_id"])
        if not reason:
            raise ValueError("PATTERN_ABANDON_REASON_REQUIRED")
        with self._transaction() as connection:
            if self._terminal(proposal_ref):
                raise IntegrityError("PATTERN_PROPOSAL_TERMINAL")
            return self._save(
                connection,
                _record(
                    "terminal",
                    proposal_ref=proposal_ref,
                    status="ABANDONED",
                    reason=reason,
                    observed_at=self.clock(),
                ),
            )

    def propose(
        self,
        proposal_ref: str,
        *,
        actor_id: str,
        boundary: SkillBoundary,
        content_bundle: Mapping[str, Any] | None = None,
        diagnostic_reason_map: Mapping[str, str] | None = None,
        principal_binding: Mapping[str, Any] | None = None,
        authorization_check: Callable[[], None] | None = None,
    ) -> tuple[str, ...]:
        proposal = self._load(proposal_ref, "proposal")
        self._authority(actor_id, proposal["author_id"])
        corpus = self._load(proposal["corpus_ref"], "corpus")
        boundary = boundary.revalidated()
        phase_principal = self._phase_principal_binding(
            principal_binding, actor_id=actor_id, role_phase="CANDIDATE_AUTHOR"
        )
        if authorization_check is not None:
            authorization_check()
        if boundary.rollback_package_digest != proposal["base_package_digest"] or boundary.allowed_tools:
            raise IntegrityError("PATTERN_SKILL_BOUNDARY_WIDENED")
        verified_content = None
        if content_bundle is not None:
            verified_content = SkillContentBundle.from_payload(content_bundle)
            if (
                verified_content.payload["target_skill"] != proposal["skill_name"]
                or verified_content.payload["predecessor_package_digest"]
                != proposal["base_package_digest"]
            ):
                raise IntegrityError("PATTERN_CONTENT_BUNDLE_BASE_MISMATCH")
        inherited_reasons = self._package_for_digest(
            proposal["skill_name"], proposal["base_package_digest"]
        ).manifest.get("candidate_diagnostic_reason_map")
        effective_reasons = (
            dict(diagnostic_reason_map)
            if diagnostic_reason_map is not None
            else deepcopy(inherited_reasons)
        )
        if effective_reasons is not None and (
            not isinstance(effective_reasons, dict)
            or any(
                key not in QUOTE_DIAGNOSTIC_REASON_CATALOG
                or value != QUOTE_DIAGNOSTIC_REASON_CATALOG[key]
                for key, value in effective_reasons.items()
            )
        ):
            raise IntegrityError("PATTERN_DIAGNOSTIC_POLICY_DENIED")
        diagnostic_policy_digest = (
            quote_diagnostic_policy_digest(
                self._package_for_digest(proposal["skill_name"], proposal["base_package_digest"])
            )
            if effective_reasons is not None else None
        )
        self._charge(proposal_ref, cases=len(corpus["cases"]))
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in corpus["cases"]:
            groups.setdefault(sha256_digest(row["features"]), []).append(row)
        results = []
        with self._transaction() as connection:
            if authorization_check is not None:
                authorization_check()
                self.store.require_before_commit(connection, authorization_check)
            if self._terminal(proposal_ref):
                raise IntegrityError("PATTERN_PROPOSAL_TERMINAL")
            for key, rows in sorted(groups.items()):
                membership = {
                    name: [row["case"]["digest"] for row in rows if row["case"]["outcome"] == name]
                    for name in ("SUPPORT", "COUNTEREXAMPLE", "NULL", "UNKNOWN")
                }
                pattern = _record(
                    "pattern",
                    proposal_ref=proposal_ref,
                    corpus_ref=proposal["corpus_ref"],
                    feature_profile_digest=corpus["profile"]["digest"],
                    feature_values=rows[0]["features"],
                    feature_root=key,
                    membership=membership,
                    case_refs=[row["case"]["digest"] for row in rows],
                    certificate_refs=sorted({ref for row in rows for ref in row["certificate_refs"]}),
                    status="CANDIDATE",
                    similarity_edges=0,
                )
                pattern = PatternCandidate.model_validate(pattern).model_dump(mode="json")
                pattern_ref = self._save(connection, pattern)
                candidate = _record(
                    "skill-candidate",
                    proposal_ref=proposal_ref,
                    pattern_ref=pattern_ref,
                    corpus_ref=proposal["corpus_ref"],
                    replay_ref=proposal["replay_ref"],
                    skill_name=proposal["skill_name"],
                    base_package_digest=proposal["base_package_digest"],
                    base_head_ref=proposal["base_head_ref"],
                    base_head_digest=proposal["base_head_digest"],
                    base_head_generation=proposal["base_head_generation"],
                    boundary=boundary.model_dump(mode="json"),
                    status="CANDIDATE",
                    executable=False,
                    author_id=actor_id,
                    principal_binding=phase_principal,
                    content_bundle=(
                        deepcopy(verified_content.payload)
                        if verified_content is not None
                        else None
                    ),
                    diagnostic_reason_map=effective_reasons,
                    diagnostic_policy_digest=diagnostic_policy_digest,
                    claim_boundary=BOUNDARY,
                )
                candidate = PatternSkillCandidate.model_validate(candidate).model_dump(mode="json")
                ref = self._save(connection, candidate)
                self._link(connection, pattern_ref, ref, "pattern", "skill")
                for row in rows:
                    self._link(connection, row["case"]["digest"], pattern_ref, "case", "pattern")
                    for certificate_ref in row["certificate_refs"]:
                        self._link(connection, certificate_ref, pattern_ref, "certificate", "pattern")
                results.append(ref)
        return tuple(results)

    def _overlay(self, candidate: Mapping[str, Any]) -> SkillCandidateOverlayRegistry:
        pattern = self._load(candidate["pattern_ref"], "pattern")
        package = self._package_for_digest(
            candidate["skill_name"], candidate["base_package_digest"]
        )
        if package.package_digest != candidate["base_package_digest"]:
            raise IntegrityError("PATTERN_BASE_PACKAGE_DRIFT")
        return SkillCandidateOverlayRegistry(
            _EffectiveSkillRegistry(self.registry, package),
            candidate_ref=_identity(candidate),
            candidate_digest=candidate["digest"],
            proposed_version=f"pattern.{candidate['digest'].removeprefix('sha256:')[:20]}",
            source_run_id=candidate["proposal_ref"],
            target_skill=candidate["skill_name"],
            applicability=pattern["feature_values"],
            boundary=candidate["boundary"],
            content_bundle=candidate.get("content_bundle"),
            diagnostic_reason_map=candidate.get("diagnostic_reason_map"),
        )

    def _require_current_candidate_evidence(self, candidate_ref: str) -> None:
        if self.evidence_status(candidate_ref) != "NOT_RETRACTED":
            raise IntegrityError("PATTERN_CANDIDATE_EVIDENCE_RETRACTED")

    def current_skill_head_package_digest(self, skill_name: str) -> str:
        """Return the one canonical Pattern head, or the installed predecessor."""
        source = self._current_skill_source(skill_name)
        head = self._current_stable_head(skill_name)
        if head is not None:
            if head.payload.get("transition_kind") == "ROLLBACK":
                if source is not None or head.payload.get("adoption_enabled") is not False:
                    raise IntegrityError("PATTERN_ROLLBACK_HEAD_INVALID")
                return str(head.payload["package_digest"])
            if (
                source is None
                or head.payload.get("effective_version_ref") != source.ref
                or head.payload.get("effective_version_digest") != source.digest
                or head.payload.get("package_digest") != source.payload.get("package_digest")
            ):
                raise IntegrityError("PATTERN_STABLE_HEAD_SOURCE_MISMATCH")
        elif source is None and self._skill_admissions(skill_name):
            # A withdrawn v1 admission is history, never a fresh installed
            # genesis.  Migration must resolve the old effective state first.
            raise IntegrityError("PATTERN_LEGACY_HEAD_AMBIGUOUS_OR_RETRACTED")
        return (
            str(source.payload["package_digest"])
            if source is not None
            else self.registry.load(skill_name).package_digest
        )

    def _stable_head_id(self, skill_name: str) -> str:
        scope = sha256_digest(
            {
                "tenant_id": self.store.tenant_id,
                "workspace_id": self.store.workspace_id,
                "skill_name": skill_name,
                "profile_identity": "workspace-quote-evidence-recovery-v1",
            }
        )
        return "pattern-skill-head:" + scope.removeprefix("sha256:")

    def _current_stable_head(self, skill_name: str) -> VersionedObject | None:
        try:
            head = self.store.get_object(self._stable_head_id(skill_name))
        except KeyError:
            return None
        payload = head.payload
        if (
            head.kind != "Source"
            or head.domain != "skill-governance"
            or head.state is not ObjectState.CURRENT
            or re.fullmatch(r"g[0-9]{8}", head.version) is None
            or payload.get("schema_version") != "orgrebase.pattern-skill-head.v2"
            or payload.get("skill_name") != skill_name
            or payload.get("generation") != int(head.version.removeprefix("g"))
            or payload.get("generation", 0) < 1
        ):
            raise IntegrityError("PATTERN_STABLE_HEAD_INVALID")
        return head

    def _publish_stable_head(
        self, connection: Any, *, candidate: Mapping[str, Any], source: VersionedObject,
        package_snapshot_ref: str, decision_ref: str, evaluation_ref: str,
    ) -> None:
        """Advance the one stable pointer in the same admission transaction."""
        name = candidate["skill_name"]
        current = self._current_stable_head(name)
        head_id = self._stable_head_id(name)
        if current is None:
            if any(
                item["source_ref"] != source.ref
                for item in self._skill_admissions(name)
            ):
                raise IntegrityError("PATTERN_LEGACY_HEAD_MIGRATION_REQUIRED")
            if candidate["base_head_generation"] != 0:
                raise IntegrityError("PATTERN_HEAD_MIGRATION_REQUIRED")
            installed = self.registry.load(name)
            installed_snapshot = skill_package_snapshot(installed)
            installed_ref = (
                "pattern-package-snapshot:"
                + installed_snapshot["digest"].removeprefix("sha256:")
            )
            self.store.save_artifact(
                connection, installed_ref, PACKAGE_SNAPSHOT_MEDIA, installed_snapshot
            )
            current = VersionedObject(
                id=head_id, version="g00000000", kind="Source", label=name,
                domain="skill-governance", state=ObjectState.CURRENT,
                payload={
                    "schema_version": "orgrebase.pattern-skill-head.v2",
                    "skill_name": name,
                    "generation": 0,
                    "transition_kind": "GENESIS",
                    "previous_head_ref": None,
                    "previous_head_digest": None,
                    "effective_version_ref": installed_ref,
                    "effective_version_digest": installed_snapshot["digest"],
                    "package_digest": installed.package_digest,
                    "package_snapshot_ref": installed_ref,
                    "qualification_status": "UNQUALIFIED",
                    "adoption_enabled": False,
                },
                source_refs=(installed_ref,),
                allowed_purposes=("skill_evaluation", "skill_governance"),
            )
            self.store.create_current_if_absent(connection, current)
        elif (
            current.ref != candidate["base_head_ref"]
            or current.digest != candidate["base_head_digest"]
            or current.payload["generation"] != candidate["base_head_generation"]
            or current.payload["package_digest"] != candidate["base_package_digest"]
        ):
            raise IntegrityError("PATTERN_GOVERNANCE_HEAD_CHANGED")
        generation = candidate["base_head_generation"] + 1
        promoted = VersionedObject(
            id=head_id, version=f"g{generation:08d}", kind="Source", label=name,
            domain="skill-governance", state=ObjectState.PROPOSED,
            payload={
                "schema_version": "orgrebase.pattern-skill-head.v2",
                "skill_name": name,
                "generation": generation,
                "transition_kind": "PROMOTE",
                "previous_head_ref": current.ref,
                "previous_head_digest": current.digest,
                "effective_version_ref": source.ref,
                "effective_version_digest": source.digest,
                "effective_version_parent_ref": current.payload["effective_version_ref"],
                "effective_version_parent_digest": current.payload["effective_version_digest"],
                "package_digest": source.payload["package_digest"],
                "package_snapshot_ref": package_snapshot_ref,
                "candidate_ref": source.payload["candidate_ref"],
                "decision_ref": decision_ref,
                "evaluation_ref": evaluation_ref,
                "qualification_status": "QUALIFIED",
                "adoption_enabled": False,
            },
            source_refs=(current.ref, source.ref, package_snapshot_ref, decision_ref, evaluation_ref),
            allowed_purposes=("skill_evaluation", "skill_governance"),
        )
        self.store.insert_version(connection, promoted, make_current=False)
        self.store.promote_version(connection, head_id, current.version, promoted.version)
        self.store.append_event(
            connection, "PATTERN_SKILL_HEAD_PROMOTED",
            {
                "head_ref": promoted.ref,
                "head_digest": promoted.digest,
                "previous_head_ref": current.ref,
                "previous_head_digest": current.digest,
                "generation": generation,
                "source_ref": source.ref,
                "package_digest": source.payload["package_digest"],
            },
        )

    def _skill_head_capture(self, skill_name: str) -> tuple[str, str, int, str]:
        source = self._current_skill_source(skill_name)
        head = self._current_stable_head(skill_name)
        if head is not None:
            if head.payload.get("transition_kind") == "ROLLBACK":
                raise IntegrityError("PATTERN_ROLLBACK_REQUALIFICATION_REQUIRED")
            if (
                source is None
                or head.payload["effective_version_ref"] != source.ref
                or head.payload["effective_version_digest"] != source.digest
                or head.payload["package_digest"] != source.payload["package_digest"]
            ):
                raise IntegrityError("PATTERN_STABLE_HEAD_SOURCE_MISMATCH")
            return head.ref, head.digest, head.payload["generation"], head.payload["package_digest"]
        if source is None:
            if self._skill_admissions(skill_name):
                raise IntegrityError("PATTERN_LEGACY_HEAD_AMBIGUOUS_OR_RETRACTED")
            package = self.registry.load(skill_name)
            return (
                f"installed:{skill_name}@{package.version}",
                package.package_digest,
                0,
                package.package_digest,
            )
        raise IntegrityError("PATTERN_LEGACY_HEAD_MIGRATION_REQUIRED")

    def _skill_admissions(self, skill_name: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            admission for admission in self._family("admission")
            if self._load(admission["candidate_ref"], "skill-candidate")["skill_name"]
            == skill_name
        )

    def _require_source_temporally_current(self, source: VersionedObject) -> None:
        try:
            valid_from = datetime.fromisoformat(source.valid_from.replace("Z", "+00:00"))
            valid_to = (
                datetime.fromisoformat(source.valid_to.replace("Z", "+00:00"))
                if source.valid_to is not None else None
            )
            now = datetime.fromtimestamp(self.clock(), UTC)
            if (
                valid_from.tzinfo is None
                or (valid_to is not None and valid_to.tzinfo is None)
                or now < valid_from
                or (valid_to is not None and now >= valid_to)
            ):
                raise IntegrityError("PATTERN_SOURCE_TEMPORAL_QUALIFICATION_HOLD")
        except (OverflowError, TypeError, ValueError) as exc:
            raise IntegrityError("PATTERN_SOURCE_TEMPORAL_QUALIFICATION_HOLD") from exc

    def migrate_legacy_head(
        self,
        skill_name: str,
        *,
        actor_id: str,
        expected_source_ref: str,
        expected_source_digest: str,
        expected_package_digest: str,
        maintenance_fence: Callable[[], None],
        authorization_check: Callable[[], None] | None = None,
        principal_binding: Mapping[str, Any] | None = None,
        maintenance_window_digest: str | None = None,
    ) -> str:
        """Import one qualified v1 head during an externally fenced upgrade.

        The deployment must stop old binaries and deny new adoption before
        supplying the fence callback. A new binary cannot fence a process that
        runs the old implementation; this method rechecks that external fence
        at transaction entry and immediately before commit. No legacy record
        bytes are rewritten. An absent or ambiguous legacy state is a HOLD.
        """
        self._authority(actor_id, self.governance_authority)
        phase_principal = self._phase_principal_binding(
            principal_binding, actor_id=actor_id, role_phase="LEGACY_MIGRATION"
        )
        if not callable(maintenance_fence):
            raise IntegrityError("PATTERN_MIGRATION_MAINTENANCE_FENCE_REQUIRED")
        if maintenance_window_digest is not None and re.fullmatch(
            r"sha256:[0-9a-f]{64}", maintenance_window_digest
        ) is None:
            raise IntegrityError("PATTERN_MIGRATION_WINDOW_DIGEST_INVALID")
        if not all(
            isinstance(value, str) and value
            for value in (skill_name, expected_source_ref, expected_source_digest,
                          expected_package_digest)
        ):
            raise IntegrityError("PATTERN_MIGRATION_EXACT_BASE_REQUIRED")
        maintenance_fence()
        if authorization_check is not None:
            authorization_check()
        with self._transaction() as connection:
            maintenance_fence()
            self.store.require_before_commit(connection, maintenance_fence)
            if authorization_check is not None:
                authorization_check()
                self.store.require_before_commit(connection, authorization_check)
            if self._current_stable_head(skill_name) is not None:
                raise IntegrityError("PATTERN_MIGRATION_HEAD_ALREADY_EXISTS")
            admissions = self._skill_admissions(skill_name)
            if not admissions:
                raise IntegrityError("PATTERN_MIGRATION_LEGACY_HEAD_MISSING")
            source = self._current_skill_source(skill_name)
            if source is None:
                raise IntegrityError("PATTERN_MIGRATION_LEGACY_HEAD_RETRACTED")
            self._require_source_temporally_current(source)
            matching = [item for item in admissions if item["source_ref"] == source.ref]
            if len(matching) != 1 or (
                source.ref != expected_source_ref
                or source.digest != expected_source_digest
                or source.payload.get("package_digest") != expected_package_digest
                or matching[0]["source_digest"] != source.digest
                or source.kind != "Source"
                or source.domain != "skill-governance"
                or source.payload.get("generation") is not None
                or source.payload.get("package_snapshot_ref") is not None
            ):
                raise IntegrityError("PATTERN_MIGRATION_LEGACY_HEAD_INVALID")
            candidate_ref = matching[0]["candidate_ref"]
            candidate = self._load(candidate_ref, "skill-candidate")
            decision_ref = matching[0]["decision_ref"]
            decision = self._load(decision_ref, "decision")
            evaluation_ref = source.payload.get("evaluation_ref")
            if not isinstance(evaluation_ref, str):
                raise IntegrityError("PATTERN_MIGRATION_LEGACY_EVIDENCE_INVALID")
            evaluation = self._load(evaluation_ref, "evaluation")
            if (
                source.payload.get("candidate_ref") != candidate_ref
                or source.payload.get("decision_ref") != decision_ref
                or tuple(source.source_refs)
                != (candidate_ref, decision_ref, evaluation_ref)
                or candidate.get("base_head_ref") is not None
                or candidate.get("base_head_digest") is not None
                or candidate.get("base_head_generation") is not None
                or decision.get("candidate_ref") != candidate_ref
                or decision.get("evaluation_ref") != evaluation_ref
                or decision.get("verdict") != "ADMIT"
                or decision.get("actor_id") != actor_id
                or evaluation.get("candidate_ref") != candidate_ref
                or evaluation.get("verdict") != "QUALIFIED"
                or evaluation.get("issuer") != self.evaluator_authority
                or not _has_independent_support(evaluation)
                or self.evidence_status(candidate_ref) != "NOT_RETRACTED"
                or self.evidence_status(source.ref) != "NOT_RETRACTED"
            ):
                raise IntegrityError("PATTERN_MIGRATION_LEGACY_EVIDENCE_INVALID")
            package = self._package_for_digest(skill_name, expected_package_digest)
            release_history = source.payload.get("release_history")
            evaluation_receipt = evaluation.get("current")
            if not isinstance(release_history, list) or not isinstance(
                evaluation_receipt, dict
            ):
                raise IntegrityError("PATTERN_MIGRATION_LEGACY_RELEASE_INVALID")
            effective_registry = _EffectiveSkillRegistry(self.registry, package)
            release_evaluator = SkillPackageEvaluator(effective_registry)
            release_ledger = SkillReleaseLedger(effective_registry, release_evaluator)
            release_ledger._restore_verified_history(
                skill_name, evaluation_receipt, release_history,
                store=self.store, source_ref=source.ref,
                governance_authority=self.governance_authority,
                evaluator_authority=self.evaluator_authority,
            )
            snapshot = skill_package_snapshot(package)
            snapshot_ref = (
                "pattern-package-snapshot:" + snapshot["digest"].removeprefix("sha256:")
            )
            self.store.save_artifact(
                connection, snapshot_ref, PACKAGE_SNAPSHOT_MEDIA, snapshot
            )
            installed = self.registry.load(skill_name)
            installed_snapshot = skill_package_snapshot(installed)
            installed_ref = (
                "pattern-package-snapshot:"
                + installed_snapshot["digest"].removeprefix("sha256:")
            )
            self.store.save_artifact(
                connection, installed_ref, PACKAGE_SNAPSHOT_MEDIA, installed_snapshot
            )
            head = VersionedObject(
                id=self._stable_head_id(skill_name), version="g00000001",
                kind="Source", label=skill_name, domain="skill-governance",
                state=ObjectState.CURRENT,
                payload={
                    "schema_version": "orgrebase.pattern-skill-head.v2",
                    "skill_name": skill_name,
                    "generation": 1,
                    "transition_kind": "MIGRATE",
                    "previous_head_ref": source.ref,
                    "previous_head_digest": source.digest,
                    "effective_version_ref": source.ref,
                    "effective_version_digest": source.digest,
                    "effective_version_parent_ref": installed_ref,
                    "effective_version_parent_digest": installed_snapshot["digest"],
                    "package_digest": package.package_digest,
                    "package_snapshot_ref": snapshot_ref,
                    "migration_receipt": "LEGACY_V1_NO_GENERATION",
                    "maintenance_window_digest": maintenance_window_digest,
                    "candidate_ref": candidate_ref,
                    "decision_ref": decision_ref,
                    "evaluation_ref": evaluation_ref,
                    "principal_binding": phase_principal,
                    "qualification_status": "QUALIFIED_CONTROLLED_LOCAL",
                    "adoption_enabled": False,
                },
                source_refs=(source.ref, snapshot_ref, installed_ref, decision_ref,
                             evaluation_ref),
                allowed_purposes=("skill_evaluation", "skill_governance"),
            )
            self.store.create_current_if_absent(connection, head)
            self.store.append_event(connection, "PATTERN_SKILL_HEAD_MIGRATED", {
                "head_ref": head.ref,
                "head_digest": head.digest,
                "legacy_source_ref": source.ref,
                "legacy_source_digest": source.digest,
                "package_digest": package.package_digest,
                "package_snapshot_ref": snapshot_ref,
                "adoption_enabled": False,
                "actor_id": actor_id,
                "maintenance_window_digest": maintenance_window_digest,
                "principal_binding_digest": (
                    sha256_digest(phase_principal)
                    if phase_principal is not None else None
                ),
            })
        return head.ref

    def _require_candidate_head(self, candidate: Mapping[str, Any]) -> None:
        proposal = self._load(candidate["proposal_ref"], "proposal")
        expected = (
            candidate.get("base_head_ref"),
            candidate.get("base_head_digest"),
            candidate.get("base_head_generation"),
            candidate["base_package_digest"],
        )
        if any(value is None for value in expected[:3]) or expected != (
            proposal.get("base_head_ref"),
            proposal.get("base_head_digest"),
            proposal.get("base_head_generation"),
            proposal["base_package_digest"],
        ):
            raise IntegrityError("PATTERN_CANDIDATE_HEAD_BINDING_INVALID")
        if self._skill_head_capture(candidate["skill_name"]) != expected:
            raise IntegrityError("PATTERN_GOVERNANCE_HEAD_CHANGED")

    def _current_skill_source(self, skill_name: str) -> VersionedObject | None:
        current = []
        for admission in self._family("admission"):
            candidate = self._load(admission["candidate_ref"], "skill-candidate")
            if candidate["skill_name"] != skill_name:
                continue
            source_id, version = admission["source_ref"].rsplit("@", 1)
            source = self.store.get_object(source_id, version)
            if source.state is ObjectState.CURRENT:
                if source.digest != admission["source_digest"]:
                    raise IntegrityError("PATTERN_HEAD_SOURCE_DIGEST_MISMATCH")
                current.append(source)
        if len(current) > 1:
            raise IntegrityError("PATTERN_MULTIPLE_CURRENT_SKILL_HEADS")
        return current[0] if current else None

    def _package_for_digest(self, skill_name: str, package_digest: str) -> LoadedSkillPackage:
        installed = self.registry.load(skill_name)
        if installed.package_digest == package_digest:
            return installed
        matched: list[VersionedObject] = []
        for admission in self._family("admission"):
            candidate = self._load(admission["candidate_ref"], "skill-candidate")
            if candidate["skill_name"] != skill_name:
                continue
            source_id, version = admission["source_ref"].rsplit("@", 1)
            source = self.store.get_object(source_id, version)
            if (
                source.digest != admission["source_digest"]
                or source.kind != "Source"
                or source.payload.get("candidate_ref") != admission["candidate_ref"]
            ):
                raise IntegrityError("PATTERN_PACKAGE_SOURCE_BINDING_INVALID")
            if source.payload.get("package_digest") == package_digest:
                matched.append(source)
        if len(matched) != 1:
            raise IntegrityError("PATTERN_EFFECTIVE_PACKAGE_NOT_UNIQUE")
        source = matched[0]
        candidate = self._load(source.payload["candidate_ref"], "skill-candidate")
        pattern = self._load(candidate["pattern_ref"], "pattern")
        snapshot_ref = source.payload.get("package_snapshot_ref")
        if snapshot_ref is None:
            # Read-only compatibility for one original v1 admission.  The
            # predecessor was necessarily the installed package at that time.
            if candidate["base_package_digest"] != installed.package_digest:
                raise IntegrityError("PATTERN_LEGACY_PACKAGE_SNAPSHOT_MISSING")
            overlay = SkillCandidateOverlayRegistry(
                self.registry,
                candidate_ref=_identity(candidate),
                candidate_digest=candidate["digest"],
                proposed_version=f"pattern.{candidate['digest'].removeprefix('sha256:')[:20]}",
                source_run_id=candidate["proposal_ref"],
                target_skill=skill_name,
                applicability=pattern["feature_values"],
                boundary=candidate["boundary"],
                content_bundle=candidate.get("content_bundle"),
                historical_package_digest=package_digest,
            )
            package = overlay.load(skill_name)
        else:
            snapshot = self.store.load_artifact(snapshot_ref, PACKAGE_SNAPSHOT_MEDIA).payload
            package = load_skill_package_snapshot(snapshot)
            if (
                snapshot_ref != "pattern-package-snapshot:" + snapshot["digest"].removeprefix("sha256:")
                or source.payload.get("package_snapshot_digest") != snapshot["digest"]
            ):
                raise IntegrityError("PATTERN_PACKAGE_SNAPSHOT_BINDING_INVALID")
        if package.name != skill_name or package.package_digest != package_digest:
            raise IntegrityError("PATTERN_PACKAGE_SNAPSHOT_DIGEST_MISMATCH")
        release = package.manifest.get("release_artifact", {})
        overlay = package.manifest.get("candidate_overlay", {})
        if (
            release.get("source_candidate_ref") != source.payload["candidate_ref"]
            or release.get("source_candidate_digest") != candidate["digest"]
            or release.get("predecessor_package_digest") != candidate["base_package_digest"]
            or overlay.get("candidate_digest") != candidate["digest"]
            or overlay.get("base_package_digest") != candidate["base_package_digest"]
            or package.manifest.get("candidate_applicability") != pattern["feature_values"]
            or package.manifest.get("candidate_boundary") != candidate["boundary"]
            or package.manifest.get("candidate_content_bundle") != candidate.get("content_bundle")
            or package.manifest.get("candidate_diagnostic_reason_map")
            != candidate.get("diagnostic_reason_map")
            or package.manifest.get("candidate_diagnostic_policy_digest")
            != candidate.get("diagnostic_policy_digest")
        ):
            raise IntegrityError("PATTERN_PACKAGE_CANDIDATE_BINDING_INVALID")
        if (
            package.contract != installed.contract
            or package.program != installed.program
            or package.input_schema != installed.input_schema
            or package.output_schema != installed.output_schema
            or package.skill_bytes != installed.skill_bytes
            or package.reference_bytes != installed.reference_bytes
            or package.raw_resource_bytes != installed.raw_resource_bytes
            or {
                key: digest
                for key, digest in package.resource_digests.items()
                if not key.startswith("candidate:")
            } != installed.resource_digests
        ):
            raise IntegrityError("PATTERN_PACKAGE_PROTECTED_KERNEL_DRIFT")
        return package

    def _charge_candidate(self, candidate: Mapping[str, Any], candidate_ref: str) -> None:
        self._charge(candidate["proposal_ref"], invocations=1, candidate_ref=candidate_ref)

    def evaluate(
        self,
        candidate_ref: str,
        *,
        actor_id: str,
        principal_binding: Mapping[str, Any] | None = None,
        authorization_check: Callable[[], None] | None = None,
    ) -> str:
        self._authority(actor_id, self.evaluator_authority)
        phase_principal = self._phase_principal_binding(
            principal_binding, actor_id=actor_id, role_phase="INDEPENDENT_EVALUATOR"
        )
        if authorization_check is not None:
            authorization_check()
        self._require_current_candidate_evidence(candidate_ref)
        candidate = self._load(candidate_ref, "skill-candidate")
        suite = self._load(candidate["replay_ref"], "replay-suite")
        overlay = self._overlay(candidate)
        cases = tuple(
            SkillEvaluationCase(
                item["case_id"], item["partition"], item["public_input"], item["expected_action"]
            )
            for item in suite["cases"]
        )

        # Charge before each actual invocation through the existing evaluator.
        def charge() -> None:
            self._charge_candidate(candidate, candidate_ref)

        evaluated_at = datetime.fromtimestamp(self.clock(), UTC).isoformat()
        evaluator = SkillPackageEvaluator(overlay, before_invocation=charge)
        current = evaluator.evaluate(candidate["skill_name"], cases, evaluated_at=evaluated_at)
        baseline_registry = _EffectiveSkillRegistry(
            self.registry,
            self._package_for_digest(candidate["skill_name"], candidate["base_package_digest"]),
        )
        baseline_evaluator = SkillPackageEvaluator(baseline_registry, before_invocation=charge)
        prior = baseline_evaluator.evaluate(
            candidate["skill_name"], cases, evaluated_at=evaluated_at
        )
        candidate_observations = evaluator.invocation_observations(current)
        baseline_observations = baseline_evaluator.invocation_observations(prior)
        business_oracle = _quote_recovery_business_oracle(
            candidate,
            suite,
            baseline_observations,
            candidate_observations,
        )
        pattern = self._load(candidate["pattern_ref"], "pattern")
        membership = _independent_case_membership(self._load(candidate["corpus_ref"], "corpus"), pattern)
        all_current = all(item["passed"] for item in current["case_results"])
        qualified = (
            all_current
            and current["verdict"] == "CANARY"
            and {"observation": "MISSING"} not in pattern["feature_values"].values()
            and len(membership["SUPPORT"]) >= 2
            and bool(membership["COUNTEREXAMPLE"])
            and (business_oracle is None or business_oracle["status"] == "IMPROVED")
            and candidate.get("diagnostic_reason_map") is None
        )
        evidence = _record(
            "evaluation",
            candidate_ref=candidate_ref,
            candidate_digest=candidate["digest"],
            replay_ref=candidate["replay_ref"],
            replay_digest=suite["digest"],
            current=current,
            prior=prior,
            issuer=actor_id,
            principal_binding=phase_principal,
            verdict="QUALIFIED" if qualified else "REJECTED",
            independent_case_membership=membership,
            business_oracle=business_oracle,
            evaluated_at=evaluated_at,
            claim_boundary=BOUNDARY,
        )
        with self._transaction() as connection:
            if authorization_check is not None:
                authorization_check()
                self.store.require_before_commit(connection, authorization_check)
            self._require_current_candidate_evidence(candidate_ref)
            evaluation_ref = self._save(connection, evidence)
            observations = {
                "schema_version": OBSERVATION_SCHEMA,
                "evaluation_ref": evaluation_ref,
                "evaluation_digest": evidence["digest"],
                "replay_digest": suite["digest"],
                "baseline_receipt_digest": prior["digest"],
                "candidate_receipt_digest": current["digest"],
                "baseline": {
                    "manifest": baseline_registry.load(candidate["skill_name"]).manifest,
                    "cases": list(baseline_observations),
                },
                "candidate": {
                    "manifest": overlay.load(candidate["skill_name"]).manifest,
                    "cases": list(candidate_observations),
                },
            }
            _observed_side(observations["baseline"], prior, suite["cases"])
            _observed_side(observations["candidate"], current, suite["cases"])
            self.store.save_artifact(
                connection, _observation_id(evidence), OBSERVATION_MEDIA,
                {**observations, "digest": sha256_digest(observations)},
            )
            return evaluation_ref

    def evaluation_comparison(self, evaluation_ref: str, *, actor_id: str) -> dict[str, Any]:
        """Read existing paired observations; never invoke a Skill or infer a missing action."""
        if actor_id not in {self.evaluator_authority, self.governance_authority}:
            raise AuthorizationError("PATTERN_CONTROLLER_AUTHORITY_DENIED")
        with self.store.read_snapshot():
            evaluation = self._load(evaluation_ref, "evaluation")
            suite = self._load(evaluation["replay_ref"], "replay-suite")
            if suite["digest"] != evaluation["replay_digest"]:
                raise IntegrityError("PATTERN_EVALUATION_REPLAY_MISMATCH")
            cases = suite["cases"]
            suite_digest = sha256_digest(
                [
                    {
                        "case_id": case["case_id"],
                        "partition": case["partition"],
                        "input_digest": sha256_digest(case["public_input"]),
                        "expected_output_digest": sha256_digest({"action": case["expected_action"]}),
                    }
                    for case in cases
                ]
            )
            prior, current = _verify(evaluation["prior"]), _verify(evaluation["current"])
            if any(receipt["evaluation_suite_digest"] != suite_digest for receipt in (prior, current)):
                raise IntegrityError("PATTERN_EVALUATION_INPUT_SET_MISMATCH")
            try:
                observations = _verify(
                    self.store.load_artifact(_observation_id(evaluation), OBSERVATION_MEDIA).payload
                )
            except KeyError:
                observations = None
            sources = None
            baseline_rows = candidate_rows = {}
            if observations is not None:
                if (
                    observations.get("schema_version") != OBSERVATION_SCHEMA
                    or observations.get("evaluation_ref") != evaluation_ref
                    or observations.get("evaluation_digest") != evaluation["digest"]
                    or observations.get("replay_digest") != suite["digest"]
                    or observations.get("baseline_receipt_digest") != prior["digest"]
                    or observations.get("candidate_receipt_digest") != current["digest"]
                ):
                    raise IntegrityError("PATTERN_EVALUATION_OBSERVATION_BINDING_MISMATCH")
                try:
                    baseline_rows = _observed_side(observations["baseline"], prior, cases)
                    candidate_rows = _observed_side(observations["candidate"], current, cases)
                    baseline, candidate = (observations[side]["manifest"] for side in ("baseline", "candidate"))
                    resources = {}
                    for side in ("baseline", "candidate"):
                        manifest = observations[side]["manifest"]
                        declared = {
                            key: value["sha256"]
                            for key, value in manifest["resources"].items()
                        }
                        content = manifest.get("candidate_content_bundle")
                        if content is not None:
                            bundle = SkillContentBundle.from_payload(content)
                            declared.update(
                                {
                                    f"candidate:{path}": digest
                                    for path, digest in bundle.resource_digests().items()
                                }
                            )
                        resources[side] = declared
                    candidate_content = candidate.get("candidate_content_bundle")
                    unused_content = ["skill", "reference_en", "reference_zh_cn"]
                    if candidate_content is not None:
                        bundle = SkillContentBundle.from_payload(candidate_content)
                        unused_content.extend(
                            f"candidate:{resource['path']}"
                            for resource in bundle.payload["resources"]
                            if resource["kind"] != "checklist"
                        )
                    sources = {
                        "program_changed": baseline["program_content_digest"]
                        != candidate["program_content_digest"],
                        "program_digests": {
                            "baseline": baseline["program_content_digest"],
                            "candidate": candidate["program_content_digest"],
                        },
                        "entry_point_changed": baseline["entry_point"] != candidate["entry_point"],
                        "applicability_changed": baseline.get("candidate_applicability", {})
                        != candidate.get("candidate_applicability", {}),
                        "applicability_digests": {
                            side: sha256_digest(observations[side]["manifest"].get("candidate_applicability", {}))
                            for side in ("baseline", "candidate")
                        },
                        "resource_digests": resources,
                        "changed_resources": sorted(
                            key
                            for key in resources["baseline"].keys() | resources["candidate"].keys()
                            if resources["baseline"].get(key) != resources["candidate"].get(key)
                        ),
                        "resources_not_used_as_candidate_instructions": unused_content,
                    }
                except (AttributeError, KeyError, TypeError, ValueError) as exc:
                    raise IntegrityError("PATTERN_EVALUATION_OBSERVATION_INVALID") from exc
            rows = []
            for case in cases:
                left = baseline_rows.get(case["case_id"], {}).get("result")
                right = candidate_rows.get(case["case_id"], {}).get("result")
                known = left is not None and right is not None
                # Package identity is provenance, not a change in the returned candidate behavior.
                semantics = (
                    [
                        {key: value for key, value in result.items() if key != "package_digest"}
                        for result in (left, right)
                    ]
                    if known
                    else None
                )
                rows.append(
                    {
                        "case_ref": case["case_id"],
                        "partition": case["partition"],
                        "input_digest": sha256_digest(case["public_input"]),
                        "baseline_action": left["action"] if known else None,
                        "candidate_action": right["action"] if known else None,
                        "action_changed": left["action"] != right["action"] if known else None,
                        "behavior_changed": semantics[0] != semantics[1] if known else None,
                        "observation_status": "OBSERVED" if known else "UNKNOWN",
                    }
                )
            unknown = sum(row["observation_status"] == "UNKNOWN" for row in rows)
            changed = sum(row["behavior_changed"] is True for row in rows)
            return {
                "schema_version": "orgrebase.pattern-evaluation-comparison.v1",
                "evaluation_ref": evaluation_ref,
                "evaluation_digest": evaluation["digest"],
                "baseline_receipt_digest": prior["digest"],
                "candidate_receipt_digest": current["digest"],
                "behavior_delta": "UNKNOWN" if unknown else "OBSERVED_CHANGE" if changed else "NO_BEHAVIOR_DELTA",
                "case_count": len(rows),
                "observed_case_count": len(rows) - unknown,
                "unknown_case_count": unknown,
                "changed_case_count": changed if not unknown else None,
                "cases": rows,
                "change_sources": sources,
                "target_writes": 0,
                "scope": "SAME_FROZEN_CASE_INPUTS_ONLY_NOT_BUSINESS_IMPROVEMENT",
                "observation_receipt_digest": observations["digest"] if observations else None,
                "business_oracle": evaluation.get("business_oracle"),
            }

    def _replay_release(self, candidate: Mapping[str, Any], evaluation: Mapping[str, Any]):
        suite = self._load(candidate["replay_ref"], "replay-suite")
        overlay = self._overlay(candidate)
        evaluator = SkillPackageEvaluator(
            overlay, before_invocation=lambda: self._charge_candidate(candidate, _identity(candidate))
        )
        cases = tuple(
            SkillEvaluationCase(
                item["case_id"], item["partition"], item["public_input"], item["expected_action"]
            )
            for item in suite["cases"]
        )
        actual = evaluator.evaluate(candidate["skill_name"], cases, evaluated_at=evaluation["evaluated_at"])
        if actual != evaluation["current"]:
            raise IntegrityError("PATTERN_RELEASE_REPLAY_MISMATCH")
        ledger = SkillReleaseLedger(overlay, evaluator)
        for state in ("EVALUATED", "SHADOW", "CANARY"):
            ledger.transition(
                candidate["skill_name"],
                actual,
                to_state=state,
                actor_id=SKILL_REGISTRY_AUTHORITY,
                reason_codes=("EXACT_GOVERNANCE_ADMISSION",),
                created_at=evaluation["evaluated_at"],
            )
        return ledger, overlay

    def decide(
        self,
        candidate_ref: str,
        evaluation_ref: str,
        *,
        actor_id: str,
        verdict: Literal["ADMIT", "REJECT"],
        expected_candidate_digest: str,
        expected_head_package_digest: str | None = None,
        authorization_check: Callable[[], None] | None = None,
        principal_binding: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        production_chain_digest: str | None = None,
    ) -> str:
        self._authority(actor_id, self.governance_authority)
        self._require_current_candidate_evidence(candidate_ref)
        candidate = self._load(candidate_ref, "skill-candidate")
        evaluation = self._load(evaluation_ref, "evaluation")
        if (
            expected_candidate_digest != candidate["digest"]
            or evaluation["candidate_ref"] != candidate_ref
            or evaluation["issuer"] != self.evaluator_authority
            or actor_id == candidate["author_id"]
        ):
            raise AuthorizationError("PATTERN_GOVERNANCE_BINDING_MISMATCH")
        if verdict not in {"ADMIT", "REJECT"}:
            raise ValueError("PATTERN_GOVERNANCE_VERDICT_INVALID")
        if verdict == "ADMIT" and expected_head_package_digest is None:
            raise IntegrityError("PATTERN_GOVERNANCE_EXPECTED_HEAD_REQUIRED")
        if idempotency_key is not None and (
            not idempotency_key.strip() or len(idempotency_key) > 256
        ):
            raise ValueError("PATTERN_GOVERNANCE_IDEMPOTENCY_KEY_INVALID")
        phase_principal = self._phase_principal_binding(
            principal_binding, actor_id=actor_id, role_phase="RELEASE_GOVERNOR"
        )
        if production_chain_digest is not None and re.fullmatch(
            r"sha256:[0-9a-f]{64}", production_chain_digest
        ) is None:
            raise IntegrityError("PATTERN_PRODUCTION_CHAIN_DIGEST_INVALID")
        if authorization_check is not None:
            authorization_check()
        existing = [
            item for item in self._family("decision") if item["candidate_ref"] == candidate_ref
        ]
        if existing and idempotency_key is not None:
            if len(existing) != 1:
                raise IntegrityError("PATTERN_GOVERNANCE_ALREADY_FINAL")
            prior = existing[0]
            if (
                prior.get("idempotency_key") == idempotency_key
                and prior["evaluation_ref"] == evaluation_ref
                and prior["verdict"] == verdict
                and prior["actor_id"] == actor_id
                and prior.get("expected_head_package_digest")
                == expected_head_package_digest
                and prior.get("production_chain_digest") == production_chain_digest
            ):
                return _identity(prior)
            raise IntegrityError("PATTERN_GOVERNANCE_IDEMPOTENCY_CONFLICT")
        if (
            expected_head_package_digest is not None
            and candidate["base_package_digest"] != expected_head_package_digest
        ):
            raise IntegrityError("PATTERN_GOVERNANCE_PREDECESSOR_MISMATCH")
        if verdict == "ADMIT":
            self._require_candidate_head(candidate)
        if (
            expected_head_package_digest is not None
            and self.current_skill_head_package_digest(candidate["skill_name"])
            != expected_head_package_digest
        ):
            raise IntegrityError("PATTERN_GOVERNANCE_HEAD_CHANGED")
        self._charge(candidate["proposal_ref"])
        if any(item["candidate_ref"] == candidate_ref for item in self._family("decision")):
            raise IntegrityError("PATTERN_GOVERNANCE_ALREADY_FINAL")
        if verdict == "ADMIT" and (
            evaluation["verdict"] != "QUALIFIED" or not _has_independent_support(evaluation)
        ):
            raise IntegrityError("PATTERN_CANDIDATE_NOT_QUALIFIED")
        decision = _record(
            "decision",
            candidate_ref=candidate_ref,
            evaluation_ref=evaluation_ref,
            verdict=verdict,
            actor_id=actor_id,
            human_review_verified=False,
            authority_basis=(
                "VERIFIED_CURRENT_PRINCIPAL"
                if phase_principal is not None
                else "CONFIGURED_CONTROLLER_PRINCIPAL"
            ),
            principal_binding=phase_principal,
            expected_head_package_digest=expected_head_package_digest,
            idempotency_key=idempotency_key,
            production_chain_digest=production_chain_digest,
            decided_at=self.clock(),
        )
        ledger = overlay = None
        if verdict == "ADMIT":
            ledger, overlay = self._replay_release(candidate, evaluation)
        with self._transaction() as connection:
            if authorization_check is not None:
                authorization_check()
                self.store.require_before_commit(connection, authorization_check)
            if (
                expected_head_package_digest is not None
                and self.current_skill_head_package_digest(candidate["skill_name"])
                != expected_head_package_digest
            ):
                raise IntegrityError("PATTERN_GOVERNANCE_HEAD_CHANGED")
            if verdict == "ADMIT":
                self._require_candidate_head(candidate)
            if self._terminal(candidate["proposal_ref"]):
                raise IntegrityError("PATTERN_PROPOSAL_TERMINAL")
            self._require_current_candidate_evidence(candidate_ref)
            if any(item["candidate_ref"] == candidate_ref for item in self._family("decision")):
                raise IntegrityError("PATTERN_GOVERNANCE_ALREADY_FINAL")
            decision_ref = self._save(connection, decision)
            if ledger is not None and overlay is not None:
                effective_package = overlay.load(candidate["skill_name"])
                package_snapshot = skill_package_snapshot(effective_package)
                package_snapshot_ref = (
                    "pattern-package-snapshot:"
                    + package_snapshot["digest"].removeprefix("sha256:")
                )
                self.store.save_artifact(
                    connection, package_snapshot_ref, PACKAGE_SNAPSHOT_MEDIA, package_snapshot
                )
                if expected_head_package_digest is not None:
                    for prior in self._family("admission"):
                        prior_candidate = self._load(
                            prior["candidate_ref"], "skill-candidate"
                        )
                        if prior_candidate["skill_name"] != candidate["skill_name"]:
                            continue
                        prior_id, prior_version = prior["source_ref"].rsplit("@", 1)
                        prior_source = self.store.get_object(prior_id, prior_version)
                        if prior_source.state is ObjectState.CURRENT:
                            self.store.transition_current(
                                connection, prior_id, ObjectState.SUPERSEDED
                            )
                source = VersionedObject(
                    id=f"admitted-pattern-skill:{candidate['digest'].removeprefix('sha256:')}",
                    version="1",
                    kind="Source",
                    label=candidate["skill_name"],
                    domain="skill-governance",
                    state=ObjectState.CURRENT,
                    payload={
                        "candidate_ref": candidate_ref,
                        "decision_ref": decision_ref,
                        "evaluation_ref": evaluation_ref,
                        "release_history": list(ledger.history),
                        "package_digest": effective_package.package_digest,
                        "generation": candidate["base_head_generation"] + 1,
                        "previous_head_ref": candidate["base_head_ref"],
                        "previous_head_digest": candidate["base_head_digest"],
                        "package_snapshot_ref": package_snapshot_ref,
                        "package_snapshot_digest": package_snapshot["digest"],
                        "production_chain_digest": production_chain_digest,
                        "claim_boundary": BOUNDARY,
                    },
                    source_refs=(candidate_ref, decision_ref, evaluation_ref),
                )
                self.store.insert_version(connection, source, make_current=True)
                self._save(
                    connection,
                    _record(
                        "admission",
                        candidate_ref=candidate_ref,
                        source_ref=source.ref,
                        source_digest=source.digest,
                        decision_ref=decision_ref,
                    ),
                )
                self._publish_stable_head(
                    connection,
                    candidate=candidate,
                    source=source,
                    package_snapshot_ref=package_snapshot_ref,
                    decision_ref=decision_ref,
                    evaluation_ref=evaluation_ref,
                )
                self._link(connection, candidate_ref, source.ref, "skill", "source")
            if verdict == "ADMIT":
                self._save(
                    connection,
                    _record(
                        "terminal",
                        proposal_ref=candidate["proposal_ref"],
                        status="ADMITTED",
                        reason="GOVERNANCE_DECISION_FINAL",
                        decision_ref=decision_ref,
                        observed_at=self.clock(),
                    ),
                )
            self.store.append_event(
                connection, "PATTERN_GOVERNANCE_DECIDED", {"decision_ref": decision_ref, "verdict": verdict}
            )
        return decision_ref

    def _issue_production_invocation_authority(
        self,
        *,
        candidate_ref: str,
        reviewed_bundle_digest: str,
        production_chain_digest: str,
        scope_digest: str,
    ) -> _ProductionInvocationAuthority:
        authority = _ProductionInvocationAuthority(
            candidate_ref=candidate_ref,
            reviewed_bundle_digest=reviewed_bundle_digest,
            production_chain_digest=production_chain_digest,
            scope_digest=scope_digest,
        )
        with self._production_authority_lock:
            self._production_invocation_authorities[id(authority)] = authority
        return authority

    @staticmethod
    def _invocation_capture_digest(
        *,
        candidate: Mapping[str, Any],
        source: VersionedObject,
        evaluation: Mapping[str, Any],
        decision: Mapping[str, Any],
        package_digest: str,
        dependency_digest: str,
        public_input: Mapping[str, Any],
        context: InvocationContext,
    ) -> str:
        return sha256_digest(
            {
                "candidate_digest": candidate["digest"],
                "source_ref": source.ref,
                "source_digest": source.digest,
                "evaluation_digest": evaluation["digest"],
                "decision_digest": decision["digest"],
                "production_chain_digest": source.payload.get(
                    "production_chain_digest"
                ),
                "package_digest": package_digest,
                "dependency_digest": dependency_digest,
                "input_digest": sha256_digest(public_input),
                "run_id": context.run_id,
                "task_id": context.task_id,
                "delegation_id": context.delegation_id,
                "actor_id": context.actor_id,
            }
        )

    def invoke(
        self,
        candidate_ref: str,
        public_input: Mapping[str, Any],
        *,
        context: InvocationContext,
        knowledge_refs: tuple[str, ...],
        qualification_refs: tuple[str, ...],
        authorization_check: Callable[[], None] | None = None,
        production_authority: _ProductionInvocationAuthority | None = None,
    ):
        if authorization_check is None or production_authority is None:
            raise AuthorizationError(
                "PATTERN_PRODUCTION_INVOCATION_AUTHORITY_REQUIRED"
            )
        with self._production_authority_lock:
            issued = self._production_invocation_authorities.pop(
                id(production_authority), None
            )
        if issued is not production_authority:
            raise AuthorizationError("PATTERN_PRODUCTION_INVOCATION_AUTHORITY_INVALID")
        candidate = self._load(candidate_ref, "skill-candidate")
        admissions = [
            item
            for item in self._family("admission")
            if item["candidate_ref"] == candidate_ref
        ]
        if (
            production_authority.candidate_ref != candidate_ref
            or candidate.get("content_bundle", {}).get("digest")
            != production_authority.reviewed_bundle_digest
            or len(admissions) != 1
        ):
            raise AuthorizationError("PATTERN_PRODUCTION_INVOCATION_AUTHORITY_INVALID")
        source_id, version = admissions[0]["source_ref"].rsplit("@", 1)
        source = self.store.get_object(source_id, version)
        if (
            source.payload.get("production_chain_digest")
            != production_authority.production_chain_digest
            or not production_authority.scope_digest
        ):
            raise AuthorizationError("PATTERN_PRODUCTION_INVOCATION_AUTHORITY_INVALID")
        return self._invoke_admitted(
            candidate_ref,
            public_input,
            context=context,
            knowledge_refs=knowledge_refs,
            qualification_refs=qualification_refs,
            authorization_check=authorization_check,
        )

    def _invoke_admitted(
        self,
        candidate_ref: str,
        public_input: Mapping[str, Any],
        *,
        context: InvocationContext,
        knowledge_refs: tuple[str, ...],
        qualification_refs: tuple[str, ...],
        authorization_check: Callable[[], None] | None = None,
    ):
        candidate = self._load(candidate_ref, "skill-candidate")
        admissions = [item for item in self._family("admission") if item["candidate_ref"] == candidate_ref]
        if len(admissions) != 1:
            raise AuthorizationError("PATTERN_ADMISSION_REQUIRED")
        admission = admissions[0]
        source_id, version = admission["source_ref"].rsplit("@", 1)
        source = self.store.get_object(source_id, version)
        if source.state is not ObjectState.CURRENT or source.digest != admission["source_digest"]:
            raise AuthorizationError("PATTERN_SOURCE_RETRACTED")
        boundary = candidate["boundary"]
        if (
            set(knowledge_refs) != set(boundary["required_knowledge"])
            or set(qualification_refs) != set(boundary["required_qualifications"])
            or not all(
                self.prerequisite_resolver(context.actor_id, ref)
                for ref in (*knowledge_refs, *qualification_refs)
            )
        ):
            raise AuthorizationError("PATTERN_INVOCATION_PREREQUISITES_MISSING")
        if authorization_check is not None:
            authorization_check()
        with self.store.read_snapshot():
            fresh_source = self.store.get_object(source_id, version)
            if (
                fresh_source.state is not ObjectState.CURRENT
                or fresh_source.digest != admission["source_digest"]
                or self.evidence_status(candidate_ref) != "NOT_RETRACTED"
            ):
                raise AuthorizationError("PATTERN_SOURCE_RETRACTED")
            evaluation = self._load(fresh_source.payload["evaluation_ref"], "evaluation")
            decision = self._load(fresh_source.payload["decision_ref"], "decision")
            if (
                evaluation["candidate_ref"] != candidate_ref
                or evaluation["verdict"] != "QUALIFIED"
                or not _has_independent_support(evaluation)
                or evaluation["issuer"] != self.evaluator_authority
                or decision["candidate_ref"] != candidate_ref
                or decision["verdict"] != "ADMIT"
                or decision["actor_id"] != self.governance_authority
                or decision["evaluation_ref"] != fresh_source.payload["evaluation_ref"]
                or decision["digest"] != self._load(admission["decision_ref"])["digest"]
            ):
                raise IntegrityError("PATTERN_PERSISTED_ADMISSION_BINDING_MISMATCH")
            overlay = self._overlay(candidate)
            package = overlay.load(candidate["skill_name"])
            if package.package_digest != fresh_source.payload["package_digest"]:
                raise IntegrityError("PATTERN_PERSISTED_RELEASE_PACKAGE_MISMATCH")
            observed_dependencies = dict(
                self.skill_dependency_resolver(candidate["skill_name"])
            )
            if observed_dependencies != package.manifest["dependencies"]:
                raise IntegrityError("PATTERN_INVOCATION_DEPENDENCY_DRIFT")
            ledger = SkillReleaseLedger(overlay, SkillPackageEvaluator(overlay))
            ledger._restore_verified_history(
                candidate["skill_name"],
                evaluation["current"],
                fresh_source.payload["release_history"],
                store=self.store,
                source_ref=fresh_source.ref,
                governance_authority=self.governance_authority,
                evaluator_authority=self.evaluator_authority,
            )
            dependency_digest = sha256_digest(observed_dependencies)
            capture_digest = self._invocation_capture_digest(
                candidate=candidate,
                source=fresh_source,
                evaluation=evaluation,
                decision=decision,
                package_digest=package.package_digest,
                dependency_digest=dependency_digest,
                public_input=public_input,
                context=context,
            )
        reservation = self._reserve_invocation(
            candidate_ref,
            context,
            source_ref=fresh_source.ref,
            source_digest=fresh_source.digest,
            package_digest=package.package_digest,
            dependency_digest=dependency_digest,
            capture_digest=capture_digest,
            candidate_digest=candidate["digest"],
            evaluation_digest=evaluation["digest"],
            decision_digest=decision["digest"],
            public_input=public_input,
            authorization_check=authorization_check,
        )

        # The deterministic consumer executes after the short capture/reserve
        # transaction.  Model/network consumers must use the same boundary.
        invocation_scope = sha256_digest(
            {"candidate_ref": candidate_ref, "run_id": context.run_id}
        )
        try:
            result = ledger.invoke(
                candidate["skill_name"],
                public_input,
                context=context,
                observed_dependencies=observed_dependencies,
            )
            if self.before_invocation_result_commit is not None:
                self.before_invocation_result_commit(candidate_ref)
        except Exception as error:
            state, reason_code = _invocation_failure(error)
            with self._transaction() as connection:
                self._save(
                    connection,
                    _record(
                        "invocation-terminal",
                        scope=invocation_scope,
                        reservation_ref=reservation,
                        candidate_ref=candidate_ref,
                        run_id=context.run_id,
                        state=state,
                        reason_code=reason_code,
                        capture_digest=capture_digest,
                        target_writes=0,
                        observed_at=self.clock(),
                    ),
                )
            raise IntegrityError(reason_code) from None
        try:
            with self._transaction() as connection:
                if authorization_check is not None:
                    authorization_check()
                    self.store.require_before_commit(connection, authorization_check)
                committed_source = self.store.get_object(source_id, version)
                if (
                    committed_source.state is not ObjectState.CURRENT
                    or committed_source.digest != fresh_source.digest
                    or self.evidence_status(candidate_ref) != "NOT_RETRACTED"
                    or self.current_skill_head_package_digest(candidate["skill_name"])
                    != package.package_digest
                    or dict(self.skill_dependency_resolver(candidate["skill_name"]))
                    != observed_dependencies
                ):
                    raise IntegrityError("PATTERN_INVOCATION_CAPTURE_INVALIDATED")
                current_evaluation = self._load(
                    committed_source.payload["evaluation_ref"], "evaluation"
                )
                current_decision = self._load(
                    committed_source.payload["decision_ref"], "decision"
                )
                if (
                    current_evaluation["digest"] != evaluation["digest"]
                    or current_decision["digest"] != decision["digest"]
                    or committed_source.payload["package_digest"]
                    != package.package_digest
                    or self._invocation_capture_digest(
                        candidate=candidate,
                        source=committed_source,
                        evaluation=current_evaluation,
                        decision=current_decision,
                        package_digest=package.package_digest,
                        dependency_digest=sha256_digest(observed_dependencies),
                        public_input=public_input,
                        context=context,
                    )
                    != capture_digest
                ):
                    raise IntegrityError("PATTERN_INVOCATION_CAPTURE_INVALIDATED")
                self._save(
                    connection,
                    _record(
                        "invocation-result",
                        scope=invocation_scope,
                        reservation_ref=reservation,
                        source_digest=committed_source.digest,
                        dependency_digest=sha256_digest(observed_dependencies),
                        capture_digest=capture_digest,
                        result=result.result,
                        receipt=result.receipt,
                    ),
                )
        except (AuthenticationError, AuthorizationError, IntegrityError) as error:
            _, reason_code = _invocation_failure(error)
            with self._transaction() as connection:
                self._save(
                    connection,
                    _record(
                        "invocation-rejection",
                        scope=invocation_scope,
                        reservation_ref=reservation,
                        candidate_ref=candidate_ref,
                        run_id=context.run_id,
                        reason_code=reason_code,
                        result_digest=result.receipt["output_digest"],
                        capture_digest=capture_digest,
                        target_writes=0,
                        observed_at=self.clock(),
                    ),
                )
            raise
        return result

    def _reserve_invocation(
        self,
        candidate_ref: str,
        context: InvocationContext,
        *,
        source_ref: str | None = None,
        source_digest: str | None = None,
        package_digest: str | None = None,
        dependency_digest: str | None = None,
        capture_digest: str | None = None,
        candidate_digest: str | None = None,
        evaluation_digest: str | None = None,
        decision_digest: str | None = None,
        public_input: Mapping[str, Any] | None = None,
        authorization_check: Callable[[], None] | None = None,
    ) -> str:
        scope = sha256_digest(
            {"candidate_ref": candidate_ref, "run_id": context.run_id}
        )
        error: str | None = None
        with self._transaction() as connection:
            if authorization_check is not None:
                authorization_check()
                self.store.require_before_commit(connection, authorization_check)
            if source_ref is not None:
                source_id, version = source_ref.rsplit("@", 1)
                captured = self.store.get_object(source_id, version)
                if (
                    captured.state is not ObjectState.CURRENT
                    or captured.digest != source_digest
                    or captured.payload.get("package_digest") != package_digest
                    or self.evidence_status(candidate_ref) != "NOT_RETRACTED"
                    or self.current_skill_head_package_digest(
                        self._load(candidate_ref, "skill-candidate")["skill_name"]
                    )
                    != package_digest
                ):
                    raise IntegrityError("PATTERN_INVOCATION_CAPTURE_INVALIDATED")
                current_candidate = self._load(candidate_ref, "skill-candidate")
                current_evaluation = self._load(
                    captured.payload["evaluation_ref"], "evaluation"
                )
                current_decision = self._load(
                    captured.payload["decision_ref"], "decision"
                )
                current_dependencies = dict(
                    self.skill_dependency_resolver(current_candidate["skill_name"])
                )
                if (
                    current_candidate["digest"] != candidate_digest
                    or current_evaluation["digest"] != evaluation_digest
                    or current_decision["digest"] != decision_digest
                    or sha256_digest(current_dependencies) != dependency_digest
                    or public_input is None
                    or self._invocation_capture_digest(
                        candidate=current_candidate,
                        source=captured,
                        evaluation=current_evaluation,
                        decision=current_decision,
                        package_digest=str(package_digest),
                        dependency_digest=str(dependency_digest),
                        public_input=public_input,
                        context=context,
                    )
                    != capture_digest
                ):
                    raise IntegrityError("PATTERN_INVOCATION_CAPTURE_INVALIDATED")
            usage = [item for item in self._family("invocation-reservation") if item["scope"] == scope]
            completed = [
                item
                for kind in (
                    "invocation-result",
                    "invocation-rejection",
                    "invocation-terminal",
                )
                for item in self._family(kind)
                if item.get("scope") == scope
            ]
            now = self.clock()
            opened_at = min((item["opened_at"] for item in usage), default=now)
            budget = self.invocation_budget
            if usage or completed:
                self._save(
                    connection,
                    _record(
                        "invocation-denial",
                        scope=scope,
                        candidate_ref=candidate_ref,
                        run_id=context.run_id,
                        observed_at=now,
                        reason="INVOCATION_ALREADY_RESERVED",
                        budget=budget.model_dump(mode="json"),
                    ),
                )
                error = "PATTERN_INVOCATION_ALREADY_RESERVED"
            elif (
                len(usage) >= budget.max_invocations
                or now < opened_at
                or now - opened_at > budget.max_seconds
            ):
                ref = self._save(
                    connection,
                    _record(
                        "invocation-denial",
                        scope=scope,
                        observed_at=now,
                        reason="INVOCATION_BUDGET_EXHAUSTED",
                        budget=budget.model_dump(mode="json"),
                    ),
                )
                error = "PATTERN_INVOCATION_BUDGET_EXHAUSTED"
            else:
                ref = self._save(
                    connection,
                    _record(
                        "invocation-reservation",
                        scope=scope,
                        candidate_ref=candidate_ref,
                        run_id=context.run_id,
                        actor_id=context.actor_id,
                        source_ref=source_ref,
                        source_digest=source_digest,
                        package_digest=package_digest,
                        dependency_digest=dependency_digest,
                        capture_digest=capture_digest,
                        input_digest=(
                            sha256_digest(public_input)
                            if public_input is not None
                            else None
                        ),
                        ordinal=len(usage),
                        opened_at=opened_at,
                        observed_at=now,
                        budget=budget.model_dump(mode="json"),
                    ),
                )
        if error:
            raise IntegrityError(error)
        return ref

    def _link(
        self, connection: Any, provider: str, consumer: str, provider_kind: str, consumer_kind: str
    ) -> str:
        allowed = {
            ("certificate", "case"),
            ("case", "pattern"),
            ("certificate", "pattern"),
            ("pattern", "skill"),
            ("skill", "source"),
            ("source", "plan"),
            ("plan", "compatibility"),
            ("assessment", "corpus-v2"),
        }
        if (provider_kind, consumer_kind) not in allowed:
            raise IntegrityError("PATTERN_DEPENDENCY_TYPE_INVALID")
        if self.evidence_status(provider) != "NOT_RETRACTED":
            raise IntegrityError("PATTERN_DEPENDENCY_PROVIDER_RETRACTED")
        return self._save(
            connection,
            _record(
                "dependency",
                provider=provider,
                consumer=consumer,
                provider_kind=provider_kind,
                consumer_kind=consumer_kind,
            ),
        )

    def register_successor(
        self,
        parent_ref: str,
        *,
        kind: Literal["plan", "compatibility"],
        payload: Mapping[str, Any],
        actor_id: str,
    ) -> str:
        self._authority(actor_id, self.governance_authority)
        if kind == "plan":
            if not any(item["source_ref"] == parent_ref for item in self._family("admission")):
                raise IntegrityError("PATTERN_SUCCESSOR_SOURCE_NOT_ADMITTED")
            provider_kind = "source"
        elif kind == "compatibility":
            self._load(parent_ref, "plan")
            provider_kind = "plan"
        else:
            raise IntegrityError("PATTERN_SUCCESSOR_TYPE_INVALID")
        record = _record(
            kind,
            parent_ref=parent_ref,
            payload=_verify(payload),
            issuer=actor_id,
            claim_boundary="REGISTERED_DEPENDENCY_NOT_BUSINESS_QUALIFICATION",
        )
        with self._transaction() as connection:
            if self.evidence_status(parent_ref) != "NOT_RETRACTED":
                raise IntegrityError("PATTERN_SUCCESSOR_PARENT_RETRACTED")
            ref = self._save(connection, record)
            self._link(connection, parent_ref, ref, provider_kind, kind)
            return ref

    def restore_predecessor(
        self,
        candidate_ref: str,
        *,
        actor_id: str,
        expected_predecessor_digest: str,
        reason: str,
        authorization_check: Callable[[], None] | None = None,
        principal_binding: Mapping[str, Any] | None = None,
        production_chain_digest: str | None = None,
    ) -> str:
        """Record a fail-closed return to the exact still-installed predecessor.

        This stops Pattern adoption and proves predecessor bytes are available;
        it does not rewrite historical invocations or assert an external rollout.
        """

        self._authority(actor_id, self.governance_authority)
        if not reason:
            raise ValueError("PATTERN_RESTORATION_REASON_REQUIRED")
        candidate = self._load(candidate_ref, "skill-candidate")
        phase_principal = self._phase_principal_binding(
            principal_binding,
            actor_id=actor_id,
            role_phase="PREDECESSOR_RESTORE",
        )
        if production_chain_digest is not None and re.fullmatch(
            r"sha256:[0-9a-f]{64}", production_chain_digest
        ) is None:
            raise IntegrityError("PATTERN_PRODUCTION_CHAIN_DIGEST_INVALID")
        predecessor = self.registry.load(candidate["skill_name"])
        if (
            self.evidence_status(candidate_ref) != "REQUALIFICATION_REQUIRED"
            or candidate["base_package_digest"] != expected_predecessor_digest
            or predecessor.package_digest != expected_predecessor_digest
        ):
            raise IntegrityError("PATTERN_PREDECESSOR_RESTORATION_INVALID")
        if authorization_check is not None:
            authorization_check()
        restoration = _record(
            "restoration",
            candidate_ref=candidate_ref,
            skill_name=candidate["skill_name"],
            withdrawn_package_digest=self._overlay(candidate)
            .load(candidate["skill_name"])
            .package_digest,
            predecessor_package_digest=predecessor.package_digest,
            predecessor_resource_digests=predecessor.resource_digests,
            actor_id=actor_id,
            principal_binding=phase_principal,
            production_chain_digest=production_chain_digest,
            reason=reason,
            adoption_enabled=False,
            historical_invocations_modified=0,
            observed_at=self.clock(),
            scope="CONTROLLED_LOCAL_PREDECESSOR_BYTES_AVAILABLE_NOT_EXTERNAL_ROLLOUT",
        )
        with self._transaction() as connection:
            if authorization_check is not None:
                authorization_check()
                self.store.require_before_commit(connection, authorization_check)
            if self.evidence_status(candidate_ref) != "REQUALIFICATION_REQUIRED":
                raise IntegrityError("PATTERN_PREDECESSOR_RESTORATION_INVALID")
            restoration_ref = self._save(connection, restoration)
            self._record_rollback_head(
                connection, candidate=candidate, predecessor=predecessor,
                restoration_ref=restoration_ref,
            )
            return restoration_ref

    def _record_rollback_head(
        self, connection: Any, *, candidate: Mapping[str, Any],
        predecessor: LoadedSkillPackage, restoration_ref: str,
    ) -> None:
        """Append a disabled generation while preserving the withdrawn version."""
        head = self._current_stable_head(candidate["skill_name"])
        if head is None or head.payload.get("transition_kind") != "PROMOTE":
            raise IntegrityError("PATTERN_ROLLBACK_HEAD_INVALID")
        admissions = [
            item for item in self._family("admission")
            if item["candidate_ref"] == _identity(candidate)
        ]
        if len(admissions) != 1 or head.payload.get("effective_version_ref") != admissions[0]["source_ref"]:
            raise IntegrityError("PATTERN_ROLLBACK_HEAD_INVALID")
        predecessor_ref = head.payload.get("effective_version_parent_ref")
        predecessor_digest = head.payload.get("effective_version_parent_digest")
        if not isinstance(predecessor_ref, str) or not isinstance(predecessor_digest, str):
            raise IntegrityError("PATTERN_ROLLBACK_PARENT_MISSING")
        parent = load_skill_package_snapshot(
            self.store.load_artifact(predecessor_ref, PACKAGE_SNAPSHOT_MEDIA).payload
        )
        if parent.package_digest != predecessor.package_digest:
            raise IntegrityError("PATTERN_ROLLBACK_PARENT_DRIFT")
        self._append_rollback_head(
            connection, head=head, target_ref=predecessor_ref,
            target_digest=predecessor_digest,
            target_package_digest=predecessor.package_digest,
            next_parent_ref=None, next_parent_digest=None,
            restoration_ref=restoration_ref,
        )

    def _append_rollback_head(
        self, connection: Any, *, head: VersionedObject,
        target_ref: str, target_digest: str, target_package_digest: str,
        next_parent_ref: str | None, next_parent_digest: str | None,
        restoration_ref: str,
    ) -> VersionedObject:
        if (next_parent_ref is None) != (next_parent_digest is None):
            raise IntegrityError("PATTERN_ROLLBACK_PARENT_CHAIN_INVALID")
        generation = head.payload["generation"] + 1
        rollback = VersionedObject(
            id=head.id, version=f"g{generation:08d}", kind="Source",
            label=head.label, domain="skill-governance",
            state=ObjectState.PROPOSED,
            payload={
                "schema_version": "orgrebase.pattern-skill-head.v2",
                "skill_name": head.payload["skill_name"],
                "generation": generation,
                "transition_kind": "ROLLBACK",
                "previous_head_ref": head.ref,
                "previous_head_digest": head.digest,
                "rollback_target_ref": target_ref,
                "rollback_target_digest": target_digest,
                "effective_version_ref": target_ref,
                "effective_version_digest": target_digest,
                "effective_version_parent_ref": next_parent_ref,
                "effective_version_parent_digest": next_parent_digest,
                "package_digest": target_package_digest,
                "package_snapshot_ref": (
                    target_ref if target_ref.startswith("pattern-package-snapshot:")
                    else None
                ),
                "restoration_ref": restoration_ref,
                "qualification_status": "REQUALIFICATION_REQUIRED",
                "adoption_enabled": False,
            },
            source_refs=(head.ref, target_ref, restoration_ref),
            allowed_purposes=("skill_evaluation", "skill_governance"),
        )
        self.store.insert_version(connection, rollback, make_current=False)
        self.store.promote_version(connection, head.id, head.version, rollback.version)
        self.store.append_event(
            connection, "PATTERN_SKILL_HEAD_ROLLED_BACK",
            {
                "head_ref": rollback.ref,
                "head_digest": rollback.digest,
                "previous_head_ref": head.ref,
                "generation": generation,
                "target_package_digest": target_package_digest,
                "restoration_ref": restoration_ref,
                "adoption_enabled": False,
            },
        )
        return rollback

    def _version_package_and_parent(
        self, skill_name: str, version_ref: str, version_digest: str
    ) -> tuple[LoadedSkillPackage, str | None, str | None]:
        """Resolve one exact lineage node, not the previous head in time."""
        if version_ref.startswith("pattern-package-snapshot:"):
            snapshot = self.store.load_artifact(
                version_ref, PACKAGE_SNAPSHOT_MEDIA
            ).payload
            package = load_skill_package_snapshot(snapshot)
            if (
                snapshot["digest"] != version_digest
                or version_ref != "pattern-package-snapshot:"
                + snapshot["digest"].removeprefix("sha256:")
                or package.name != skill_name
                or package.package_digest != self.registry.load(skill_name).package_digest
                or package.resource_digests != self.registry.load(skill_name).resource_digests
            ):
                raise IntegrityError("PATTERN_ROLLBACK_INSTALLED_SNAPSHOT_DRIFT")
            return package, None, None
        try:
            source_id, version = version_ref.rsplit("@", 1)
            source = self.store.get_object(source_id, version)
        except (KeyError, ValueError) as exc:
            raise IntegrityError("PATTERN_ROLLBACK_VERSION_MISSING") from exc
        admissions = [
            item for item in self._skill_admissions(skill_name)
            if item["source_ref"] == version_ref
        ]
        if (
            len(admissions) != 1
            or source.ref != version_ref
            or source.digest != version_digest
            or source.kind != "Source"
            or source.domain != "skill-governance"
            or source.state is not ObjectState.SUPERSEDED
            or admissions[0]["source_digest"] != source.digest
        ):
            raise IntegrityError("PATTERN_ROLLBACK_VERSION_INVALID")
        self._require_source_temporally_current(source)
        candidate_ref = admissions[0]["candidate_ref"]
        candidate = self._load(candidate_ref, "skill-candidate")
        evaluation_ref = source.payload.get("evaluation_ref")
        decision_ref = source.payload.get("decision_ref")
        if not isinstance(evaluation_ref, str) or not isinstance(decision_ref, str):
            raise IntegrityError("PATTERN_ROLLBACK_VERSION_EVIDENCE_INVALID")
        evaluation = self._load(evaluation_ref, "evaluation")
        decision = self._load(decision_ref, "decision")
        if (
            source.payload.get("candidate_ref") != candidate_ref
            or admissions[0]["decision_ref"] != decision_ref
            or evaluation.get("candidate_ref") != candidate_ref
            or evaluation.get("verdict") != "QUALIFIED"
            or evaluation.get("issuer") != self.evaluator_authority
            or not _has_independent_support(evaluation)
            or decision.get("candidate_ref") != candidate_ref
            or decision.get("evaluation_ref") != evaluation_ref
            or decision.get("verdict") != "ADMIT"
            or decision.get("actor_id") != self.governance_authority
            or self.evidence_status(candidate_ref) != "NOT_RETRACTED"
            or self.evidence_status(source.ref) != "NOT_RETRACTED"
        ):
            raise IntegrityError("PATTERN_ROLLBACK_VERSION_EVIDENCE_INVALID")
        package = self._package_for_digest(skill_name, source.payload["package_digest"])
        previous_head_ref = source.payload.get("previous_head_ref")
        if previous_head_ref is None:
            migrated = self.store.get_object(self._stable_head_id(skill_name), "g00000001")
            if (
                migrated.payload.get("transition_kind") != "MIGRATE"
                or migrated.payload.get("effective_version_ref") != source.ref
                or migrated.payload.get("effective_version_digest") != source.digest
            ):
                raise IntegrityError("PATTERN_ROLLBACK_LEGACY_PARENT_INVALID")
            return (
                package, migrated.payload["effective_version_parent_ref"],
                migrated.payload["effective_version_parent_digest"],
            )
        if not isinstance(previous_head_ref, str):
            raise IntegrityError("PATTERN_ROLLBACK_PARENT_HEAD_INVALID")
        if previous_head_ref.startswith(f"installed:{skill_name}@"):
            genesis = self.store.get_object(self._stable_head_id(skill_name), "g00000000")
            if (
                genesis.payload.get("transition_kind") != "GENESIS"
                or genesis.payload.get("package_digest") != candidate["base_package_digest"]
                or source.payload.get("previous_head_digest")
                != candidate["base_package_digest"]
                or source.payload.get("previous_head_ref")
                != f"installed:{skill_name}@{self.registry.load(skill_name).version}"
            ):
                raise IntegrityError("PATTERN_ROLLBACK_GENESIS_PARENT_INVALID")
            return (
                package, genesis.payload["effective_version_ref"],
                genesis.payload["effective_version_digest"],
            )
        try:
            head_id, head_version = previous_head_ref.rsplit("@", 1)
            parent_head = self.store.get_object(head_id, head_version)
        except (KeyError, ValueError) as exc:
            raise IntegrityError("PATTERN_ROLLBACK_PARENT_HEAD_MISSING") from exc
        if (
            parent_head.ref != previous_head_ref
            or parent_head.digest != source.payload.get("previous_head_digest")
            or parent_head.id != self._stable_head_id(skill_name)
        ):
            raise IntegrityError("PATTERN_ROLLBACK_PARENT_HEAD_INVALID")
        return (
            package, parent_head.payload["effective_version_ref"],
            parent_head.payload["effective_version_digest"],
        )

    def restore_direct_parent(
        self, skill_name: str, *, actor_id: str, reason: str,
        expected_head_ref: str, expected_head_digest: str,
        expected_generation: int, expected_package_digest: str,
        qualification_check: Callable[[], None],
        authorization_check: Callable[[], None] | None = None,
        principal_binding: Mapping[str, Any] | None = None,
        maintenance_window_digest: str | None = None,
    ) -> str:
        """Append a disabled rollback to the exact direct version parent.

        The withdrawn current version must already be retracted. The caller's
        qualification check is re-run before commit so source/ACL/policy drift
        leaves the head unchanged. A rollback cannot turn adoption on.
        """
        self._authority(actor_id, self.governance_authority)
        phase_principal = self._phase_principal_binding(
            principal_binding, actor_id=actor_id, role_phase="DIRECT_PARENT_RESTORE"
        )
        if not reason:
            raise IntegrityError("PATTERN_ROLLBACK_REASON_REQUIRED")
        if not callable(qualification_check):
            raise IntegrityError("PATTERN_ROLLBACK_QUALIFICATION_CHECK_REQUIRED")
        if maintenance_window_digest is not None and re.fullmatch(
            r"sha256:[0-9a-f]{64}", maintenance_window_digest
        ) is None:
            raise IntegrityError("PATTERN_ROLLBACK_WINDOW_DIGEST_INVALID")
        qualification_check()
        if authorization_check is not None:
            authorization_check()
        with self._transaction() as connection:
            qualification_check()
            self.store.require_before_commit(connection, qualification_check)
            if authorization_check is not None:
                authorization_check()
                self.store.require_before_commit(connection, authorization_check)
            head = self._current_stable_head(skill_name)
            if head is None or (
                head.ref != expected_head_ref
                or head.digest != expected_head_digest
                or head.payload["generation"] != expected_generation
                or head.payload["package_digest"] != expected_package_digest
            ):
                raise IntegrityError("PATTERN_ROLLBACK_STALE_HEAD")
            if head.payload["transition_kind"] not in {"MIGRATE", "PROMOTE", "ROLLBACK"}:
                raise IntegrityError("PATTERN_ROLLBACK_HEAD_INVALID")
            current_ref = head.payload["effective_version_ref"]
            if current_ref.startswith("pattern-package-snapshot:"):
                raise IntegrityError("PATTERN_ROLLBACK_NO_DIRECT_PARENT")
            current_id, current_version = current_ref.rsplit("@", 1)
            current_source = self.store.get_object(current_id, current_version)
            current_candidate = current_source.payload.get("candidate_ref")
            if (
                current_source.digest != head.payload["effective_version_digest"]
                or current_source.state is not ObjectState.REQUALIFICATION_REQUIRED
                or not isinstance(current_candidate, str)
                or self.evidence_status(current_candidate) != "REQUALIFICATION_REQUIRED"
            ):
                raise IntegrityError("PATTERN_ROLLBACK_CURRENT_NOT_WITHDRAWN")
            target_ref = head.payload.get("effective_version_parent_ref")
            target_digest = head.payload.get("effective_version_parent_digest")
            if not isinstance(target_ref, str) or not isinstance(target_digest, str):
                raise IntegrityError("PATTERN_ROLLBACK_NO_DIRECT_PARENT")
            parent, next_ref, next_digest = self._version_package_and_parent(
                skill_name, target_ref, target_digest
            )
            restoration = _record(
                "restoration", skill_name=skill_name,
                withdrawn_head_ref=head.ref, withdrawn_head_digest=head.digest,
                withdrawn_version_ref=current_ref,
                predecessor_version_ref=target_ref,
                predecessor_version_digest=target_digest,
                predecessor_package_digest=parent.package_digest,
                predecessor_resource_digests=parent.resource_digests,
                actor_id=actor_id, reason=reason, adoption_enabled=False,
                principal_binding=phase_principal,
                maintenance_window_digest=maintenance_window_digest,
                historical_invocations_modified=0, observed_at=self.clock(),
                scope="CONTROLLED_LOCAL_DIRECT_PARENT_NOT_EXTERNAL_ROLLOUT",
            )
            restoration_ref = self._save(connection, restoration)
            self._append_rollback_head(
                connection, head=head, target_ref=target_ref,
                target_digest=target_digest,
                target_package_digest=parent.package_digest,
                next_parent_ref=next_ref, next_parent_digest=next_digest,
                restoration_ref=restoration_ref,
            )
            return restoration_ref

    def invoke_restored_predecessor(
        self,
        restoration_ref: str,
        public_input: Mapping[str, Any],
        *,
        context: InvocationContext,
        authorization_check: Callable[[], None] | None = None,
    ) -> SkillInvocation:
        """Execute the exact installed predecessor for one new run only."""

        restoration = self._load(restoration_ref, "restoration")
        candidate = self._load(restoration["candidate_ref"], "skill-candidate")
        package = self.registry.load(candidate["skill_name"])
        if (
            restoration["predecessor_package_digest"] != package.package_digest
            or restoration["predecessor_resource_digests"] != package.resource_digests
            or restoration["adoption_enabled"] is not False
            or self.evidence_status(restoration["candidate_ref"])
            != "REQUALIFICATION_REQUIRED"
            or self.current_skill_head_package_digest(candidate["skill_name"])
            != package.package_digest
        ):
            raise AuthorizationError("PATTERN_PREDECESSOR_RESTORATION_INVALID")
        if authorization_check is not None:
            authorization_check()
        scope = sha256_digest(
            {
                "restoration_ref": restoration_ref,
                "run_id": context.run_id,
            }
        )
        with self._transaction() as connection:
            if authorization_check is not None:
                authorization_check()
                self.store.require_before_commit(connection, authorization_check)
            prior_run_records = [
                item
                for kind in (
                    "invocation-reservation",
                    "invocation-result",
                    "invocation-rejection",
                    "invocation-terminal",
                    "predecessor-invocation-reservation",
                    "predecessor-invocation-result",
                    "predecessor-invocation-rejection",
                    "predecessor-invocation-terminal",
                )
                for item in self._family(kind)
                if item.get("run_id") == context.run_id
                or item.get("receipt", {}).get("run_id") == context.run_id
            ]
            if prior_run_records:
                raise IntegrityError("PATTERN_RESTORATION_REQUIRES_NEW_RUN")
            if (
                self._load(restoration_ref, "restoration")["digest"]
                != restoration["digest"]
                or self.evidence_status(restoration["candidate_ref"])
                != "REQUALIFICATION_REQUIRED"
                or self.registry.load(candidate["skill_name"]).package_digest
                != package.package_digest
            ):
                raise IntegrityError("PATTERN_RESTORATION_CAPTURE_INVALIDATED")
            reservation = self._save(
                connection,
                _record(
                    "predecessor-invocation-reservation",
                    scope=scope,
                    restoration_ref=restoration_ref,
                    candidate_ref=restoration["candidate_ref"],
                    run_id=context.run_id,
                    actor_id=context.actor_id,
                    package_digest=package.package_digest,
                    observed_at=self.clock(),
                ),
            )

        try:
            self.registry._check_context_bindings(public_input, context)
            result = self.registry.interpret_candidate(package, public_input)
            if self.before_invocation_result_commit is not None:
                self.before_invocation_result_commit(restoration["candidate_ref"])
        except Exception as error:
            state, reason_code = _invocation_failure(error)
            with self._transaction() as connection:
                self._save(
                    connection,
                    _record(
                        "predecessor-invocation-terminal",
                        scope=scope,
                        reservation_ref=reservation,
                        restoration_ref=restoration_ref,
                        run_id=context.run_id,
                        state=state,
                        reason_code=reason_code,
                        target_writes=0,
                        observed_at=self.clock(),
                    ),
                )
            raise IntegrityError(reason_code) from None
        receipt_body = {
            "schema_version": "orgrebase.restored-predecessor-invocation.v1",
            "id": f"restored-predecessor-invocation:{scope.removeprefix('sha256:')}",
            "restoration_ref": restoration_ref,
            "production_chain_digest": restoration.get("production_chain_digest"),
            "run_id": context.run_id,
            "task_id": context.task_id,
            "delegation_id": context.delegation_id,
            "actor_id": context.actor_id,
            "package_digest": package.package_digest,
            "resource_digests": package.resource_digests,
            "input_digest": sha256_digest(public_input),
            "output_digest": sha256_digest(result),
            "authorization_mode": "EXACT_PREDECESSOR_RESTORATION",
            "candidate_only": True,
            "model_invocations": 0,
            "model_cost_usd": 0,
            "target_writes": 0,
            "created_at": datetime.fromtimestamp(self.clock(), UTC).isoformat(),
        }
        invocation = SkillInvocation(result=result, receipt=_record("receipt", **receipt_body))
        try:
            with self._transaction() as connection:
                if authorization_check is not None:
                    authorization_check()
                    self.store.require_before_commit(connection, authorization_check)
                if (
                    self._load(restoration_ref, "restoration")["digest"]
                    != restoration["digest"]
                    or self.evidence_status(restoration["candidate_ref"])
                    != "REQUALIFICATION_REQUIRED"
                    or self.current_skill_head_package_digest(candidate["skill_name"])
                    != package.package_digest
                    or self.registry.load(candidate["skill_name"]).resource_digests
                    != package.resource_digests
                ):
                    raise IntegrityError("PATTERN_RESTORATION_CAPTURE_INVALIDATED")
                self._save(
                    connection,
                    _record(
                        "predecessor-invocation-result",
                        reservation_ref=reservation,
                        restoration_ref=restoration_ref,
                        receipt=invocation.receipt,
                    ),
                )
        except (AuthenticationError, AuthorizationError, IntegrityError) as error:
            _, reason_code = _invocation_failure(error)
            with self._transaction() as connection:
                self._save(
                    connection,
                    _record(
                        "predecessor-invocation-rejection",
                        reservation_ref=reservation,
                        restoration_ref=restoration_ref,
                        run_id=context.run_id,
                        reason_code=reason_code,
                        output_digest=sha256_digest(result),
                        target_writes=0,
                        observed_at=self.clock(),
                    ),
                )
            raise
        return invocation

    def retract(self, subject_ref: str, *, actor_id: str, reason: str) -> str:
        self._authority(actor_id, self.governance_authority)
        if not reason:
            raise ValueError("PATTERN_RETRACTION_REASON_REQUIRED")
        with self._transaction() as connection:
            links = self._family("dependency")
            if subject_ref not in {item["provider"] for item in links} | {item["consumer"] for item in links}:
                raise IntegrityError("PATTERN_RETRACTION_SUBJECT_UNKNOWN")
            edges = tuple(
                DependencyEdge(
                    id=item["digest"],
                    source_id=item["provider"],
                    target_id=item["consumer"],
                    relation="REQUIRES_EVIDENCE",
                    strength=DependencyStrength.HARD,
                    coverage_basis=CoverageBasis.CONTRACT_DECLARED,
                    status=EdgeStatus.ADMITTED,
                    provenance_refs=(_identity(item),),
                )
                for item in links
            )
            fixture = EnterpriseFixture(
                schema_version="pattern-evidence-graph.v1",
                organization_id=self.store.workspace_id,
                revisions={},
                change={},
                objects=(),
                dependencies=edges,
                dependency_manifests=(),
                impact_targets=(),
                context_profiles={},
                agents=(),
                evaluation_cases=(),
            )
            closure = ImpactEngine(fixture).dependency_closure((subject_ref,))
            if not closure["complete"]:
                raise IntegrityError("PATTERN_RETRACTION_CLOSURE_INCOMPLETE")
            affected = set(closure["affected_refs"])
            receipt = _record(
                "retraction",
                subject_ref=subject_ref,
                affected_refs=sorted(affected),
                graph_digest=sha256_digest(links),
                edge_refs=closure["edge_refs"],
                reason=reason,
                actor_id=actor_id,
                observed_at=self.clock(),
            )
            for admission in self._family("admission"):
                if admission["source_ref"] in affected:
                    self.store.transition_current(
                        connection,
                        admission["source_ref"].rsplit("@", 1)[0],
                        ObjectState.REQUALIFICATION_REQUIRED,
                    )
            ref = self._save(connection, receipt)
            self.store.append_event(connection, "PATTERN_EVIDENCE_RETRACTED", {"receipt_ref": ref})
            return ref

    def evidence_status(self, ref: str) -> str:
        return (
            "REQUALIFICATION_REQUIRED"
            if any(ref in item["affected_refs"] for item in self._family("retraction"))
            else "NOT_RETRACTED"
        )
