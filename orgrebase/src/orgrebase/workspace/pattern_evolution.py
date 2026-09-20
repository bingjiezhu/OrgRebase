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
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, JsonValue, model_validator

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
    SKILL_REGISTRY_AUTHORITY,
    InvocationContext,
    SkillCandidateOverlayRegistry,
    SkillEvaluationCase,
    SkillPackageEvaluator,
    SkillPackageRegistry,
    SkillReleaseLedger,
)

MEDIA = "application/vnd.orgrebase.pattern-evolution+json"
OBSERVATION_MEDIA = "application/vnd.orgrebase.pattern-evaluation-observations+json"
OBSERVATION_SCHEMA = "orgrebase.pattern-evaluation-observations.v1"
BOUNDARY = "LOCAL_GOVERNED_CANDIDATE_NOT_ENTERPRISE_EFFECTIVENESS"


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
            PatternSkillCandidate.model_validate(record)
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
        if case.certificate.get("kind") != "retail-outcome-case-candidate":
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
    ) -> str:
        self._authority(actor_id, self.corpus_authority)
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
            claim_boundary=BOUNDARY,
        )
        with self._transaction() as connection:
            corpus_ref = self._save(connection, corpus)
            for row in rows:
                for certificate_ref in row["certificate_refs"]:
                    self._link(connection, certificate_ref, row["case"]["digest"], "certificate", "case")
            return corpus_ref

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
        package = self.registry.load(skill_name)
        proposal = _record(
            "proposal",
            proposal_id=proposal_id,
            corpus_ref=corpus_ref,
            replay_ref=replay_ref,
            author_id=author_id,
            skill_name=skill_name,
            base_package_digest=package.package_digest,
            budget=budget.model_dump(mode="json"),
            opened_at=self.clock(),
            status="CANDIDATE_ONLY",
            claim_boundary=BOUNDARY,
        )
        with self._transaction() as connection:
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

    def propose(self, proposal_ref: str, *, actor_id: str, boundary: SkillBoundary) -> tuple[str, ...]:
        proposal = self._load(proposal_ref, "proposal")
        self._authority(actor_id, proposal["author_id"])
        corpus = self._load(proposal["corpus_ref"], "corpus")
        boundary = boundary.revalidated()
        if boundary.rollback_package_digest != proposal["base_package_digest"] or boundary.allowed_tools:
            raise IntegrityError("PATTERN_SKILL_BOUNDARY_WIDENED")
        self._charge(proposal_ref, cases=len(corpus["cases"]))
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in corpus["cases"]:
            groups.setdefault(sha256_digest(row["features"]), []).append(row)
        results = []
        with self._transaction() as connection:
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
                    boundary=boundary.model_dump(mode="json"),
                    status="CANDIDATE",
                    executable=False,
                    author_id=actor_id,
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
        package = self.registry.load(candidate["skill_name"])
        if package.package_digest != candidate["base_package_digest"]:
            raise IntegrityError("PATTERN_BASE_PACKAGE_DRIFT")
        return SkillCandidateOverlayRegistry(
            self.registry,
            candidate_ref=_identity(candidate),
            candidate_digest=candidate["digest"],
            proposed_version=f"pattern.{candidate['digest'].removeprefix('sha256:')[:20]}",
            source_run_id=candidate["proposal_ref"],
            target_skill=candidate["skill_name"],
            applicability=pattern["feature_values"],
            boundary=candidate["boundary"],
        )

    def _require_current_candidate_evidence(self, candidate_ref: str) -> None:
        if self.evidence_status(candidate_ref) != "NOT_RETRACTED":
            raise IntegrityError("PATTERN_CANDIDATE_EVIDENCE_RETRACTED")

    def _charge_candidate(self, candidate: Mapping[str, Any], candidate_ref: str) -> None:
        self._charge(candidate["proposal_ref"], invocations=1, candidate_ref=candidate_ref)

    def evaluate(self, candidate_ref: str, *, actor_id: str) -> str:
        self._authority(actor_id, self.evaluator_authority)
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
        baseline_evaluator = SkillPackageEvaluator(self.registry, before_invocation=charge)
        prior = baseline_evaluator.evaluate(
            candidate["skill_name"], cases, evaluated_at=evaluated_at
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
            verdict="QUALIFIED" if qualified else "REJECTED",
            independent_case_membership=membership,
            evaluated_at=evaluated_at,
            claim_boundary=BOUNDARY,
        )
        with self._transaction() as connection:
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
                    "manifest": self.registry.load(candidate["skill_name"]).manifest,
                    "cases": list(baseline_evaluator.invocation_observations(prior)),
                },
                "candidate": {
                    "manifest": overlay.load(candidate["skill_name"]).manifest,
                    "cases": list(evaluator.invocation_observations(current)),
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
                    resources = {
                        side: {
                            key: value["sha256"]
                            for key, value in observations[side]["manifest"]["resources"].items()
                        }
                        for side in ("baseline", "candidate")
                    }
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
                        "resources_not_used_as_candidate_instructions": [
                            "skill",
                            "reference_en",
                            "reference_zh_cn",
                        ],
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
            authority_basis="CONFIGURED_CONTROLLER_PRINCIPAL",
            decided_at=self.clock(),
        )
        ledger = overlay = None
        if verdict == "ADMIT":
            ledger, overlay = self._replay_release(candidate, evaluation)
        with self._transaction() as connection:
            if self._terminal(candidate["proposal_ref"]):
                raise IntegrityError("PATTERN_PROPOSAL_TERMINAL")
            self._require_current_candidate_evidence(candidate_ref)
            if any(item["candidate_ref"] == candidate_ref for item in self._family("decision")):
                raise IntegrityError("PATTERN_GOVERNANCE_ALREADY_FINAL")
            decision_ref = self._save(connection, decision)
            if ledger is not None and overlay is not None:
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
                        "package_digest": overlay.load(candidate["skill_name"]).package_digest,
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

    def invoke(
        self,
        candidate_ref: str,
        public_input: Mapping[str, Any],
        *,
        context: InvocationContext,
        knowledge_refs: tuple[str, ...],
        qualification_refs: tuple[str, ...],
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
        reservation = self._reserve_invocation(candidate_ref, context)
        with self._transaction() as connection:
            fresh_source = self.store.get_object(source_id, version)
            if fresh_source.state is not ObjectState.CURRENT:
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
            if overlay.load(candidate["skill_name"]).package_digest != fresh_source.payload["package_digest"]:
                raise IntegrityError("PATTERN_PERSISTED_RELEASE_PACKAGE_MISMATCH")
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
            result = ledger.invoke(
                candidate["skill_name"],
                public_input,
                context=context,
                observed_dependencies=overlay.load(candidate["skill_name"]).manifest["dependencies"],
            )
            self._save(
                connection,
                _record(
                    "invocation-result",
                    reservation_ref=reservation,
                    source_digest=fresh_source.digest,
                    receipt=result.receipt,
                ),
            )
            return result

    def _reserve_invocation(self, candidate_ref: str, context: InvocationContext) -> str:
        scope = sha256_digest(
            {"candidate_ref": candidate_ref, "run_id": context.run_id, "actor_id": context.actor_id}
        )
        error = False
        with self._transaction() as connection:
            usage = [item for item in self._family("invocation-reservation") if item["scope"] == scope]
            now = self.clock()
            opened_at = min((item["opened_at"] for item in usage), default=now)
            budget = self.invocation_budget
            if (
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
                error = True
            else:
                ref = self._save(
                    connection,
                    _record(
                        "invocation-reservation",
                        scope=scope,
                        candidate_ref=candidate_ref,
                        ordinal=len(usage),
                        opened_at=opened_at,
                        observed_at=now,
                        budget=budget.model_dump(mode="json"),
                    ),
                )
        if error:
            raise IntegrityError("PATTERN_INVOCATION_BUDGET_EXHAUSTED")
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
