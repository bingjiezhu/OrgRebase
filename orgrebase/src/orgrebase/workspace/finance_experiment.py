"""Pre-registered Finance experiment identities, budgets and independent oracle.

This module stores control-plane evidence; it never dispatches a provider call
or promotes a Skill.  An intent without a verified terminal observation is
UNKNOWN, including after process death.  Fixture observations cannot become
live-model or qualification evidence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from orgrebase.auth import AuthenticationError, authorize, current_authorization, request_principal
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.private_records import PrivateRecordStore
from orgrebase.store import StateStore
from orgrebase.workspace.advisory import DomainAdvisoryCandidate, finance_case_revision
from orgrebase.workspace.models import ModelRequestV4, ModelResponseReceiptV4
from orgrebase.workspace.vertex_candidate import build_vertex_advice_body

FAMILY_MEDIA = "application/vnd.orgrebase.finance-experiment-family.v1+json"
TRIAL_MEDIA = "application/vnd.orgrebase.finance-experiment-trial.v1+json"
OBSERVATION_MEDIA = "application/vnd.orgrebase.finance-experiment-observation.v1+json"
GOLD_COMMITMENT_MEDIA = "application/vnd.orgrebase.finance-oracle-gold-commitment.v1+json"
ASSESSMENT_MEDIA = "application/vnd.orgrebase.finance-independent-assessment.v1+json"
ARMS = (
    "STATIC_CURRENT", "NO_MEMORY", "HUMAN_REVIEWED", "SPARSE_RECALL",
    "SINGLE_REFLECTION", "BOUNDED_GEPA",
)
ORDERS = ("CHRONOLOGICAL", "ORDER_STRATIFIED", "CLUSTER_SHUFFLED")
SPLITS = frozenset({"DEVELOPMENT", "VALIDATION", "SEALED_HOLDOUT"})
REVIEWED_FINANCE_RUBRIC = {
    "schema_version": "orgrebase.finance-independent-rubric.v1",
    "revision": "finance-explanation-review-2026-09-28.v1",
    "grounded_score": {
        "0": "unsupported or contradicted business fact",
        "1": "material omissions or unsupported details",
        "2": "partially grounded, needs substantive correction",
        "3": "grounded with limited clarification required",
        "4": "fully grounded to current allowed enterprise sources",
    },
    "next_step_score": {
        "0": "wrong or unsafe next step",
        "1": "next step missing",
        "2": "ambiguous next step requiring rework",
        "3": "reviewable next step with minor clarification",
        "4": "specific authorized next review action and stop condition",
    },
    "hard_checks": [
        "current-case-and-preview", "exact-finance-source-and-object-scope",
        "numeric-and-currency-literals-grounded", "zero-unauthorized-business-writes",
        "current-dependency-cluster", "actual-v4-wire-and-receipt",
    ],
    "semantic_basis": "predeclared-private-required-and-forbidden-concepts-plus-independent-assessor",
    "claim_ceiling": "controlled-local-quality-not-customer-or-causal-value",
}
REVIEWED_FINANCE_RUBRIC_DIGEST = sha256_digest(REVIEWED_FINANCE_RUBRIC)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def derive_finance_case_cluster(workspace: Any, event_id: str) -> dict[str, Any]:
    """Conservative current-business dependency identity, independent of labels.

    Shared object IDs, upstream source refs or observation refs must not be
    represented as independent clusters.  A full family additionally rejects
    partial overlap across claimed clusters.  This is a grouping mechanism,
    not proof that any real sample is statistically independent.
    """
    from orgrebase.workspace.finance_explanation_operations import _current_case

    fixture, bundle = _current_case(workspace, event_id)
    keys: set[str] = set()
    for delta in bundle.change_set.deltas:
        source = fixture.object(delta.object_id, delta.base_version)
        if source.domain != "finance":
            continue
        keys.add("object:" + source.id)
        keys.update("source:" + ref for ref in source.source_refs)
        observation_ref = source.payload.get("source_observation_ref")
        if isinstance(observation_ref, str) and observation_ref:
            keys.add("observation:" + observation_ref)
    event = workspace.changes.get(event_id)
    proposal = event.proposal
    keys.add("proposal-object:" + proposal.id)
    keys.update("proposal-source:" + ref for ref in proposal.source_refs)
    proposal_observation = proposal.payload.get("source_observation_ref")
    if isinstance(proposal_observation, str) and proposal_observation:
        keys.add("observation:" + proposal_observation)
    dependencies = proposal.payload.get("read_dependencies")
    if dependencies is not None:
        from orgrebase.workspace.read_dependencies import (
            MEDIA_TYPE as WITNESS_MEDIA,
        )
        from orgrebase.workspace.read_dependencies import (
            ReadDependencies,
            ReadWitness,
        )

        try:
            refs = ReadDependencies.model_validate(dependencies).witnesses
            for reference in refs:
                stored = workspace.store.load_artifact(reference.artifact_id, WITNESS_MEDIA)
                witness = ReadWitness.model_validate(stored.payload).revalidated()
                if (
                    witness.artifact_id != reference.artifact_id
                    or witness.digest != reference.witness_digest
                    or witness.query.digest != reference.query_digest
                    or witness.result != reference.expected_result
                ):
                    raise IntegrityError("FINANCE_EXPERIMENT_WITNESS_BINDING_INVALID")
                keys.add("connector:" + witness.query.connector_id)
                keys.add("connector-coverage:" + witness.coverage.source_binding_digest)
                keys.add("connector-page:" + witness.coverage.page_ref)
                keys.update(
                    f"connector-record:{witness.query.connector_id}:{record_id}"
                    for record_id in witness.query.record_ids
                )
        except (KeyError, TypeError, ValueError, IntegrityError) as exc:
            raise IntegrityError("FINANCE_EXPERIMENT_DEPENDENCY_CLOSURE_UNDETERMINED") from exc
    if not keys:
        raise IntegrityError("FINANCE_EXPERIMENT_DEPENDENCY_CLOSURE_UNDETERMINED")
    scope = {
        "tenant_id": workspace.profile.organization_id,
        "workspace_id": workspace.store.workspace_id,
        "profile_id": "workspace-change-explanation-v1",
    }
    digest = sha256_digest({"scope": scope, "dependency_keys": sorted(keys)})
    return {
        "cluster_id": "finance-dependency-cluster:" + digest[7:],
        "source_change_key": "finance-dependency-source:" + digest[7:],
        "dependency_keys_digest": sha256_digest(sorted(keys)),
        "dependency_key_digests": tuple(sorted(sha256_digest(key) for key in keys)),
        "case_revision_digest": finance_case_revision(bundle.change_set, bundle.preview),
    }


def _first_exposure_audit(workspace: Any, event_id: str, cluster_id: str) -> dict[str, Any]:
    """Find known prior trials; stay UNKNOWN when historical exposure is open."""
    cursor = None
    examined = 0
    exposed = 0
    uncertain = 0
    while True:
        page = workspace.store.artifact_page(
            artifact_id_prefix="finance-evaluation-intent:",
            after=cursor, limit=100,
            expected_media_type="application/vnd.orgrebase.finance-evaluation-intent.v1+json",
        )
        for row in page["items"]:
            examined += 1
            if examined > 10_000:
                raise IntegrityError("FINANCE_ORACLE_EXPOSURE_AUDIT_UNBOUNDED")
            prior_case = row.payload.get("event_id")
            if prior_case == event_id:
                exposed += 1
                continue
            if isinstance(prior_case, str):
                try:
                    if derive_finance_case_cluster(workspace, prior_case)["cluster_id"] == cluster_id:
                        exposed += 1
                except (IntegrityError, KeyError, ValueError):
                    uncertain += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    head = workspace.store.audit_head()
    return {
        "schema_version": "orgrebase.finance-first-exposure-audit.v1",
        "event_id": event_id, "cluster_id": cluster_id,
        "known_finance_intent_count": examined,
        "known_cluster_exposure_count": exposed,
        "unresolved_prior_case_count": uncertain,
        "event_head_digest": head["head_digest"],
        "event_sequence_no": head["sequence_no"],
        "coverage": "KNOWN_FINANCE_INTENTS_ONLY_NORMAL_AND_EXTERNAL_MODEL_HISTORY_UNVERIFIED",
        "status": "EXPOSED" if exposed else "UNDETERMINED",
    }


def require_independent_dependency_clusters(
    cases: Mapping[str, tuple[str, tuple[str, ...]]],
) -> None:
    """Reject even partial A∩B overlap between claimed independent clusters."""
    owners: dict[str, str] = {}
    for case_ref, (cluster_id, key_digests) in sorted(cases.items()):
        _identifier(case_ref, "CASE_REF")
        _identifier(cluster_id, "CLUSTER")
        if not key_digests or len(key_digests) != len(set(key_digests)):
            raise IntegrityError("FINANCE_EXPERIMENT_DEPENDENCY_SET_INVALID")
        for key_digest in key_digests:
            _digest(key_digest, "DEPENDENCY_KEY_DIGEST")
            if owners.get(key_digest, cluster_id) != cluster_id:
                raise IntegrityError("FINANCE_EXPERIMENT_OVERLAPPING_CLUSTERS")
            owners[key_digest] = cluster_id


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise IntegrityError(f"FINANCE_EXPERIMENT_{label}_INVALID")
    return value


def _digest(value: str, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise IntegrityError(f"FINANCE_EXPERIMENT_{label}_INVALID")
    return value


class FinanceExperimentService:
    """One frozen family and an append-only, family-budgeted trial ledger.

    PostgreSQL uses StateStore's transaction-scoped advisory idempotency lock;
    SQLite uses BEGIN IMMEDIATE.  Trial IDs derive from pre-registered case,
    seed, order and arm, so caller operation/candidate IDs cannot reset budget.
    """

    def __init__(
        self, store: StateStore, *, tenant_id: str, evaluator_actor_id: str,
        operator_actor_id: str, workspace: Any | None = None,
        release_governor_actor_id: str | None = None,
    ) -> None:
        if (
            not tenant_id or not evaluator_actor_id or not operator_actor_id
            or evaluator_actor_id == operator_actor_id
            or release_governor_actor_id in {evaluator_actor_id, operator_actor_id}
            or store.tenant_id not in {None, tenant_id}
        ):
            raise ValueError("FINANCE_EXPERIMENT_SCOPE_OR_ROLES_INVALID")
        self.store = store
        self.tenant_id = tenant_id
        self.evaluator_actor_id = evaluator_actor_id
        self.operator_actor_id = operator_actor_id
        self.release_governor_actor_id = release_governor_actor_id
        if workspace is not None and (
            workspace.store is not store
            or workspace.profile.organization_id != tenant_id
            or workspace.private_retention_seconds <= 0
        ):
            raise ValueError("FINANCE_EXPERIMENT_WORKSPACE_SCOPE_INVALID")
        self.workspace = workspace
        self.scope_digest = sha256_digest({
            "tenant_id": tenant_id, "workspace_id": store.workspace_id,
            "profile_id": "workspace-change-explanation-v1",
        })

    def _actor(self, expected: str, action: str) -> str:
        principal = request_principal.get()
        if principal is None:
            raise AuthenticationError("FINANCE_EXPERIMENT_PRINCIPAL_REQUIRED")
        authorize(principal, action, self.tenant_id)
        if principal.actor_id != expected:
            raise AuthorizationError("FINANCE_EXPERIMENT_ACTOR_REQUIRED")
        check = current_authorization()
        if check is not None:
            check()
        return principal.actor_id

    def _release_actor(self) -> str:
        if not self.release_governor_actor_id:
            raise IntegrityError("FINANCE_EXPERIMENT_RELEASE_GOVERNOR_UNCONFIGURED")
        return self._actor(self.release_governor_actor_id, "govern")

    def _family_ref(self, family_id: str) -> str:
        return "finance-experiment-family:" + sha256_digest({
            "scope_digest": self.scope_digest, "family_id": _identifier(family_id, "FAMILY_ID"),
        })[7:]

    def family_ref(self, family_id: str) -> str:
        """Derive the exact tenant/workspace/profile-scoped family identity."""
        return self._family_ref(family_id)

    def _family_lock(self, connection: Any, family_ref: str) -> None:
        # The same key is locked even before the registration row exists.
        self.store.get_idempotent(
            "finance-experiment-lock:" + family_ref,
            sha256_digest({"family_ref": family_ref}), connection=connection,
        )

    def freeze_family(
        self, *, family_id: str, parent_head_ref: str, parent_head_digest: str,
        cases: Mapping[str, Mapping[str, Any]], seeds: tuple[int, int, int],
        orders: Mapping[str, tuple[str, ...]], rubric_digest: str,
        max_queries: int, max_reserved_calls: int, max_reserved_microusd: int,
        sealed_suite_ref: str | None = None, sealed_suite_digest: str | None = None,
        execution_scope: str = "CONTROLLED_FIXTURE",
    ) -> str:
        """Freeze split, cluster lineage, six-arm matrix and all family ceilings."""
        actor_id = self._actor(self.evaluator_actor_id, "govern")
        family_ref = self._family_ref(family_id)
        _digest(parent_head_digest, "PARENT_HEAD_DIGEST")
        _digest(rubric_digest, "RUBRIC_DIGEST")
        _identifier(parent_head_ref.replace("@", ":"), "PARENT_HEAD_REF")
        if (
            execution_scope not in {"CONTROLLED_FIXTURE", "REAL_VERTEX"}
            or (execution_scope == "REAL_VERTEX" and (not sealed_suite_ref or not sealed_suite_digest))
            or (sealed_suite_ref is None) != (sealed_suite_digest is None)
            or (sealed_suite_digest is not None and _DIGEST.fullmatch(sealed_suite_digest) is None)
            or len(cases) < 3 or len(cases) > 100
            or len(seeds) != 3 or len(set(seeds)) != 3
            or any(isinstance(seed, bool) or not isinstance(seed, int) or seed < 0 for seed in seeds)
            or not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in (
                max_queries, max_reserved_calls, max_reserved_microusd
            ))
            or max_queries > 10_000 or max_reserved_calls > 10_000
            or set(orders) != set(ORDERS)
        ):
            raise IntegrityError("FINANCE_EXPERIMENT_FAMILY_INVALID")
        normalized: dict[str, dict[str, Any]] = {}
        cluster_split: dict[str, str] = {}
        source_cluster: dict[str, str] = {}
        for case_ref, raw in sorted(cases.items()):
            _identifier(case_ref, "CASE_REF")
            if not isinstance(raw, Mapping) or set(raw) != {
                "case_revision_digest", "independence_cluster_id", "split",
                "source_change_key", "time_ordinal",
            }:
                raise IntegrityError("FINANCE_EXPERIMENT_CASE_INVALID")
            revision = _digest(raw["case_revision_digest"], "CASE_REVISION")
            cluster = _identifier(raw["independence_cluster_id"], "CLUSTER")
            source_key = _identifier(raw["source_change_key"], "SOURCE_CHANGE")
            split = raw["split"]
            ordinal = raw["time_ordinal"]
            if (
                split not in SPLITS or isinstance(ordinal, bool)
                or not isinstance(ordinal, int) or ordinal < 0
                or cluster_split.get(cluster, split) != split
                or source_cluster.get(source_key, cluster) != cluster
            ):
                raise IntegrityError("FINANCE_EXPERIMENT_CLUSTER_LEAKAGE")
            cluster_split[cluster] = split
            source_cluster[source_key] = cluster
            normalized[case_ref] = {
                "case_revision_digest": revision, "independence_cluster_id": cluster,
                "split": split, "source_change_key": source_key, "time_ordinal": ordinal,
            }
        clusters = set(cluster_split)
        if len(clusters) < 3 or set(cluster_split.values()) != SPLITS:
            raise IntegrityError("FINANCE_EXPERIMENT_SPLIT_INSUFFICIENT")
        frozen_orders: dict[str, list[str]] = {}
        for order_name in ORDERS:
            permutation = orders[order_name]
            if len(permutation) != len(clusters) or set(permutation) != clusters:
                raise IntegrityError("FINANCE_EXPERIMENT_ORDER_INVALID")
            frozen_orders[order_name] = list(permutation)
        if frozen_orders["CHRONOLOGICAL"] != list(dict.fromkeys(
            normalized[case]["independence_cluster_id"]
            for case in sorted(normalized, key=lambda key: (normalized[key]["time_ordinal"], key))
        )):
            raise IntegrityError("FINANCE_EXPERIMENT_CHRONOLOGY_INVALID")
        if len({tuple(items) for items in frozen_orders.values()}) != len(ORDERS):
            raise IntegrityError("FINANCE_EXPERIMENT_ORDER_NOT_DISTINCT")
        split_times = {
            split: tuple(item["time_ordinal"] for item in normalized.values()
                         if item["split"] == split)
            for split in ("DEVELOPMENT", "VALIDATION", "SEALED_HOLDOUT")
        }
        if not (
            max(split_times["DEVELOPMENT"]) < min(split_times["VALIDATION"])
            and max(split_times["VALIDATION"]) < min(split_times["SEALED_HOLDOUT"])
        ):
            raise IntegrityError("FINANCE_EXPERIMENT_TIME_SPLIT_ORDER_INVALID")
        plan = {
            "schema_version": "orgrebase.finance-experiment-family.v1",
            "scope_digest": self.scope_digest, "family_id": family_id,
            "tenant_id": self.tenant_id, "workspace_id": self.store.workspace_id,
            "profile_id": "workspace-change-explanation-v1",
            "parent_head_ref": parent_head_ref, "parent_head_digest": parent_head_digest,
            "model_id": "gemini-3.8-flash", "consumer_version": "workspace-change-advisory@4.0.0",
            "execution_scope": execution_scope,
            "sealed_suite_ref": sealed_suite_ref, "sealed_suite_digest": sealed_suite_digest,
            "cases": normalized, "seeds": list(seeds), "orders": frozen_orders,
            "arms": list(ARMS), "rubric_digest": rubric_digest,
            "max_queries": max_queries, "max_reserved_calls": max_reserved_calls,
            "max_reserved_microusd": max_reserved_microusd,
            "evaluator_actor_id": actor_id, "target_writes": 0,
            "release_governor_actor_id": self.release_governor_actor_id,
        }
        if execution_scope == "REAL_VERTEX":
            from orgrebase.workspace.skill_evolution_v2 import FinanceSkillHeadService

            if (
                self.workspace is None
                or rubric_digest != REVIEWED_FINANCE_RUBRIC_DIGEST
                or not self.release_governor_actor_id
            ):
                raise IntegrityError("FINANCE_EXPERIMENT_REAL_WORKSPACE_REQUIRED")
            dependency_cases: dict[str, tuple[str, tuple[str, ...]]] = {}
            for case_ref, item in normalized.items():
                derived = derive_finance_case_cluster(self.workspace, case_ref)
                if (
                    item["case_revision_digest"] != derived["case_revision_digest"]
                    or item["independence_cluster_id"] != derived["cluster_id"]
                    or item["source_change_key"] != derived["source_change_key"]
                ):
                    raise IntegrityError("FINANCE_EXPERIMENT_REAL_CLUSTER_SPOOFED")
                dependency_cases[case_ref] = (
                    derived["cluster_id"], derived["dependency_key_digests"],
                )
                item["dependency_keys_digest"] = derived["dependency_keys_digest"]
                item["dependency_key_digests"] = list(derived["dependency_key_digests"])
            require_independent_dependency_clusters(dependency_cases)
            head = FinanceSkillHeadService(self.store, tenant_id=self.tenant_id).resolve()
            suite = self.store.load_artifact(
                sealed_suite_ref,
                "application/vnd.orgrebase.finance-sealed-suite.v1+json",
            ).payload
            holdout_cases = {
                case_ref: item for case_ref, item in normalized.items()
                if item["split"] == "SEALED_HOLDOUT"
            }
            if (
                sha256_digest(suite) != sealed_suite_digest
                or suite.get("schema_version") != "orgrebase.finance-sealed-suite.v1"
                or suite.get("experiment_family_id") != family_id
                or suite.get("evaluator_actor_id") != actor_id
                or suite.get("parent_head_ref") != parent_head_ref
                or suite.get("parent_head_digest") != parent_head_digest
                or head.head_ref != parent_head_ref
                or head.head_digest != parent_head_digest
                or self.store.get_object(FinanceSkillHeadService(
                    self.store, tenant_id=self.tenant_id,
                ).head_id).payload["actor_id"] != self.release_governor_actor_id
                or suite.get("rubric_digest") != rubric_digest
                or suite.get("case_clusters") != {
                    case_ref: item["independence_cluster_id"]
                    for case_ref, item in holdout_cases.items()
                }
                or any(
                    suite.get("case_gold", {}).get(case_ref, {}).get("case_revision_digest")
                    != item["case_revision_digest"]
                    for case_ref, item in holdout_cases.items()
                )
                or max_reserved_calls > suite.get("max_physical_attempts", 0)
                or max_reserved_microusd > suite.get("max_reserved_microusd", 0)
            ):
                raise IntegrityError("FINANCE_EXPERIMENT_REAL_SUITE_INVALID")
            for case_ref in holdout_cases:
                commitment_ref = "finance-oracle-gold-commitment:" + sha256_digest({
                    "suite_id": suite["suite_id"], "event_id": case_ref,
                    "workspace_id": self.store.workspace_id,
                })[7:]
                try:
                    commitment = self.store.load_artifact(
                        commitment_ref, GOLD_COMMITMENT_MEDIA,
                    ).payload
                except KeyError as exc:
                    raise IntegrityError("FINANCE_EXPERIMENT_SEALED_GOLD_UNVERIFIED") from exc
                if (
                    commitment.get("gold_ref") != suite["case_gold"][case_ref]["record_ref"]
                    or commitment.get("gold_digest") != suite["case_gold"][case_ref]["record_digest"]
                    or commitment.get("dependency_keys_digest")
                    != holdout_cases[case_ref]["dependency_keys_digest"]
                    or commitment.get("first_exposure_status") != "VERIFIED_FIRST_EXPOSURE"
                    or commitment.get("sealed_eligibility") != "ELIGIBLE"
                ):
                    raise IntegrityError("FINANCE_EXPERIMENT_SEALED_GOLD_UNVERIFIED")
        plan_digest = sha256_digest(plan)
        with self.store.transaction() as connection:
            self._actor(actor_id, "govern")
            self._family_lock(connection, family_ref)
            for case_ref in normalized:
                key = "finance-experiment-case:" + sha256_digest({
                    "scope_digest": self.scope_digest, "case_ref": case_ref,
                })[7:]
                request_digest = sha256_digest({"case_ref": case_ref, "scope_digest": self.scope_digest})
                binding = self.store.get_idempotent(key, request_digest, connection=connection)
                if binding is not None and binding != {"family_ref": family_ref}:
                    raise IntegrityError("FINANCE_EXPERIMENT_CASE_ALREADY_BOUND")
                if binding is None:
                    self.store.save_idempotent(connection, key, request_digest, {"family_ref": family_ref})
            try:
                prior = self.store.load_artifact(family_ref, FAMILY_MEDIA).payload
            except KeyError:
                prior = None
            if prior is not None:
                if prior != plan:
                    raise IntegrityError("FINANCE_EXPERIMENT_FAMILY_CONFLICT")
                return family_ref
            self.store.save_artifact(connection, family_ref, FAMILY_MEDIA, plan)
            self.store.append_event(connection, "FINANCE_EXPERIMENT_FAMILY_FROZEN", {
                "family_ref": family_ref, "plan_digest": plan_digest,
                "case_count": len(normalized), "cluster_count": len(clusters),
                "lineage_count": len(seeds) * len(ORDERS),
                "max_reserved_calls": max_reserved_calls,
                "max_reserved_microusd": max_reserved_microusd,
                "evaluator_actor_id": actor_id, "target_writes": 0,
            })
        return family_ref

    def _load_family(self, family_ref: str) -> dict[str, Any]:
        plan = self.store.load_artifact(family_ref, FAMILY_MEDIA).payload
        if (
            plan.get("schema_version") != "orgrebase.finance-experiment-family.v1"
            or plan.get("scope_digest") != self.scope_digest
            or plan.get("tenant_id") != self.tenant_id
            or plan.get("workspace_id") != self.store.workspace_id
            or plan.get("evaluator_actor_id") != self.evaluator_actor_id
            or plan.get("release_governor_actor_id") != self.release_governor_actor_id
            or plan.get("arms") != list(ARMS)
            or family_ref != self._family_ref(plan["family_id"])
        ):
            raise IntegrityError("FINANCE_EXPERIMENT_FAMILY_STALE_OR_INVALID")
        return plan

    @staticmethod
    def _trial_ref(
        family_ref: str, *, case_ref: str, seed: int, order: str, arm: str,
    ) -> str:
        return "finance-experiment-trial:" + sha256_digest({
            "family_ref": family_ref, "case_ref": case_ref,
            "seed": seed, "order": order, "arm": arm,
        })[7:]

    def _trials(self, family_ref: str) -> tuple[dict[str, Any], ...]:
        prefix = "finance-experiment-trial:"
        rows: list[dict[str, Any]] = []
        cursor = None
        while True:
            page = self.store.artifact_page(
                artifact_id_prefix=prefix, after=cursor, limit=100,
                expected_media_type=TRIAL_MEDIA,
            )
            rows.extend(row.payload for row in page["items"] if row.payload.get("family_ref") == family_ref)
            if len(rows) > 10_000:
                raise IntegrityError("FINANCE_EXPERIMENT_LEDGER_UNBOUNDED")
            cursor = page["next_cursor"]
            if cursor is None:
                return tuple(rows)

    def reserve_trial(
        self, *, family_ref: str, case_ref: str, seed: int, order: str, arm: str,
        bundle_digest: str, snapshot_digest: str, manifest_digest: str,
        budget_contract_digest: str, reserved_calls: int, reserved_microusd: int,
    ) -> dict[str, Any]:
        """Reserve a single immutable identity before any provider I/O.

        Until a separately verified terminal observation is attached, another
        arm or ID for the same case/lineage is denied.  Budget counts intents,
        so a crash or transport ambiguity never refunds its reservation.
        """
        actor_id = self._actor(self.operator_actor_id, "propose")
        for value, label in ((bundle_digest, "BUNDLE_DIGEST"), (snapshot_digest, "SNAPSHOT_DIGEST"),
                             (manifest_digest, "MANIFEST_DIGEST"),
                             (budget_contract_digest, "BUDGET_DIGEST")):
            _digest(value, label)
        if (
            isinstance(reserved_calls, bool) or not isinstance(reserved_calls, int)
            or reserved_calls < 1 or isinstance(reserved_microusd, bool)
            or not isinstance(reserved_microusd, int) or reserved_microusd < 1
        ):
            raise IntegrityError("FINANCE_EXPERIMENT_RESERVATION_INVALID")
        with self.store.transaction() as connection:
            self._actor(actor_id, "propose")
            self._family_lock(connection, family_ref)
            family = self._load_family(family_ref)
            if family["execution_scope"] != "CONTROLLED_FIXTURE":
                raise IntegrityError("FINANCE_EXPERIMENT_REAL_RUNNER_NOT_QUALIFIED")
            if (
                case_ref not in family["cases"] or arm not in ARMS
                or seed not in family["seeds"] or order not in ORDERS
            ):
                raise IntegrityError("FINANCE_EXPERIMENT_TRIAL_NOT_REGISTERED")
            trial_ref = self._trial_ref(
                family_ref, case_ref=case_ref, seed=seed, order=order, arm=arm,
            )
            operation_id = "finance-experiment:" + trial_ref.rsplit(":", 1)[1]
            intent = {
                "schema_version": "orgrebase.finance-experiment-trial.v1",
                "family_ref": family_ref, "family_digest": sha256_digest(family),
                "case_ref": case_ref,
                "case_revision_digest": family["cases"][case_ref]["case_revision_digest"],
                "cluster_id": family["cases"][case_ref]["independence_cluster_id"],
                "split": family["cases"][case_ref]["split"],
                "seed": seed, "order": order, "arm": arm,
                "operation_id": operation_id, "actor_id": actor_id,
                "execution_scope": family["execution_scope"], "execution_mode": "EVALUATION_ONLY",
                "candidate_bundle_digest": bundle_digest,
                "snapshot_digest": snapshot_digest, "manifest_digest": manifest_digest,
                "budget_contract_digest": budget_contract_digest,
                "reserved_calls": reserved_calls, "reserved_microusd": reserved_microusd,
                "model_id": family["model_id"], "consumer_version": family["consumer_version"],
                "target_writes": 0,
            }
            try:
                prior = self.store.load_artifact(trial_ref, TRIAL_MEDIA).payload
            except KeyError:
                prior = None
            if prior is not None:
                if prior != intent:
                    raise IntegrityError("FINANCE_EXPERIMENT_TRIAL_IDENTITY_CONFLICT")
                return {"trial_ref": trial_ref, "trial_digest": sha256_digest(prior),
                        "operation_id": operation_id, "status": self.trial_status(trial_ref)}
            trials = self._trials(family_ref)
            if len(trials) >= family["max_queries"]:
                raise IntegrityError("FINANCE_EXPERIMENT_QUERY_BUDGET_EXHAUSTED")
            if sum(item["reserved_calls"] for item in trials) + reserved_calls > family["max_reserved_calls"]:
                raise IntegrityError("FINANCE_EXPERIMENT_CALL_BUDGET_EXHAUSTED")
            if sum(item["reserved_microusd"] for item in trials) + reserved_microusd > family["max_reserved_microusd"]:
                raise IntegrityError("FINANCE_EXPERIMENT_COST_BUDGET_EXHAUSTED")
            for item in trials:
                if item["case_ref"] == case_ref and item["seed"] == seed and item["order"] == order:
                    prior_ref = self._trial_ref(
                        family_ref, case_ref=case_ref, seed=seed, order=order, arm=item["arm"],
                    )
                    if self.trial_status(prior_ref) not in {"FIXTURE_TERMINAL", "CONTROLLED_PROTOCOL_VALID"}:
                        raise IntegrityError("FINANCE_EXPERIMENT_PREDECESSOR_UNKNOWN")
            self.store.require_before_commit(connection, lambda: self._actor(actor_id, "propose"))
            self.store.save_artifact(connection, trial_ref, TRIAL_MEDIA, intent)
            self.store.append_event(connection, "FINANCE_EXPERIMENT_TRIAL_RESERVED", {
                "family_ref": family_ref, "trial_ref": trial_ref,
                "trial_digest": sha256_digest(intent), "operation_id": operation_id,
                "case_ref": case_ref, "arm": arm,
                "reserved_calls": reserved_calls,
                "reserved_microusd": reserved_microusd,
                "target_writes": 0,
            })
            return {"trial_ref": trial_ref, "trial_digest": sha256_digest(intent),
                    "operation_id": operation_id, "status": "RESULT_UNKNOWN"}

    def trial_status(self, trial_ref: str) -> str:
        trial = self.store.load_artifact(trial_ref, TRIAL_MEDIA).payload
        family = self._load_family(trial["family_ref"])
        if (
            trial.get("schema_version") != "orgrebase.finance-experiment-trial.v1"
            or trial.get("family_digest") != sha256_digest(family)
            or trial.get("execution_scope") != family["execution_scope"]
            or trial.get("actor_id") != self.operator_actor_id
            or trial.get("target_writes") != 0
            or trial_ref != self._trial_ref(
                trial["family_ref"], case_ref=trial["case_ref"],
                seed=trial["seed"], order=trial["order"], arm=trial["arm"],
            )
        ):
            raise IntegrityError("FINANCE_EXPERIMENT_TRIAL_BINDING_INVALID")
        observation_ref = "finance-experiment-observation:" + trial_ref.rsplit(":", 1)[1]
        try:
            observation = self.store.load_artifact(observation_ref, OBSERVATION_MEDIA).payload
        except KeyError:
            return "RESULT_UNKNOWN"
        if (
            observation.get("schema_version") != "orgrebase.finance-experiment-observation.v1"
            or observation.get("trial_ref") != trial_ref
            or observation.get("trial_digest") != sha256_digest(trial)
            or observation.get("target_writes") != 0
            or trial["execution_scope"] != "CONTROLLED_FIXTURE"
        ):
            raise IntegrityError("FINANCE_EXPERIMENT_OBSERVATION_BINDING_INVALID")
        if observation.get("status") == "FIXTURE_TERMINAL":
            if (
                observation.get("evidence_scope") != "DETERMINISTIC_MECHANISM_ONLY"
                or observation.get("evaluator_actor_id") != self.evaluator_actor_id
                or observation.get("quality_status") != "NOT_EVALUATED"
            ):
                raise IntegrityError("FINANCE_EXPERIMENT_OBSERVATION_BINDING_INVALID")
        elif observation.get("status") == "CONTROLLED_PROTOCOL_VALID":
            if (
                observation.get("evidence_scope") != "CONTROLLED_MODEL_STUB_PROTOCOL_ONLY"
                or observation.get("operator_actor_id") != self.operator_actor_id
                or observation.get("quality_status") != "NOT_EVALUATED"
                or self.workspace is None
            ):
                raise IntegrityError("FINANCE_EXPERIMENT_OBSERVATION_BINDING_INVALID")
            result = self._validate_controlled_result(trial_ref, trial, observation["result_ref"])
            if sha256_digest(result) != observation.get("result_digest"):
                raise IntegrityError("FINANCE_EXPERIMENT_OBSERVATION_RESULT_CHANGED")
        else:
            raise IntegrityError("FINANCE_EXPERIMENT_OBSERVATION_STATUS_INVALID")
        return observation["status"]

    def _validate_controlled_result(
        self, trial_ref: str, trial: Mapping[str, Any], result_ref: str,
    ) -> dict[str, Any]:
        from orgrebase.workspace.finance_explanation_operations import INTENT_MEDIA, RESULT_MEDIA

        if self.workspace is None:
            raise IntegrityError("FINANCE_EXPERIMENT_WORKSPACE_REQUIRED")
        result = self.store.load_artifact(result_ref, RESULT_MEDIA).payload
        intent_ref = result.get("intent_ref")
        if not isinstance(intent_ref, str):
            raise IntegrityError("FINANCE_EXPERIMENT_RESULT_BINDING_INVALID")
        intent = self.store.load_artifact(intent_ref, INTENT_MEDIA).payload
        if (
            result.get("schema_version") != "orgrebase.finance-evaluation-result.v1"
            or intent.get("schema_version") != "orgrebase.finance-evaluation-intent.v1"
            or intent.get("scope") != "CONTROLLED_FAMILY_ARM"
            or result.get("scope") != "CONTROLLED_FAMILY_ARM"
            or intent.get("trial_ref") != trial_ref
            or result.get("intent_digest") != sha256_digest(intent)
            or intent.get("operation_id") != trial["operation_id"]
            or result.get("operation_id") != trial["operation_id"]
            or intent.get("evaluation_arm") != trial["arm"]
            or result.get("evaluation_arm") != trial["arm"]
            or intent.get("event_id") != trial["case_ref"]
            or result.get("event_id") != trial["case_ref"]
            or intent.get("case_revision_digest") != trial["case_revision_digest"]
            or intent.get("candidate_bundle_digest") != trial["candidate_bundle_digest"]
            or result.get("candidate_bundle_digest") != trial["candidate_bundle_digest"]
            or intent.get("snapshot_digest") != trial["snapshot_digest"]
            or intent.get("manifest_digest") != trial["manifest_digest"]
            or intent.get("budget_contract_digest") != trial["budget_contract_digest"]
            or intent.get("reserved_calls") != trial["reserved_calls"]
            or intent.get("reserved_microusd") != trial["reserved_microusd"]
            or intent.get("actor_id") != self.operator_actor_id
            or result.get("status") != "PROTOCOL_VALID"
            or result.get("quality_status") != "NOT_EVALUATED"
            or result.get("target_writes") != 0
            or intent.get("target_writes") != 0
        ):
            raise IntegrityError("FINANCE_EXPERIMENT_RESULT_BINDING_INVALID")
        private = PrivateRecordStore(
            self.store, self.workspace.clock,
            retention_seconds=self.workspace.private_retention_seconds,
        ).read_owned(
            result["private_record_ref"], owner_id=self.operator_actor_id,
            scope_ref=result["intent_ref"],
        )
        if private is None or sha256_digest(private) != result["private_record_digest"]:
            raise IntegrityError("FINANCE_EXPERIMENT_PRIVATE_RESULT_INVALID")
        handoffs = private.get("collaboration", {}).get("handoffs", [])
        finance = [
            row["payload"]["model_advisory"]
            for row in handoffs
            if isinstance(row, dict) and isinstance(row.get("payload"), dict)
            and isinstance(row["payload"].get("model_advisory"), dict)
            and row["payload"]["model_advisory"].get("request", {}).get("contract_version") == "4"
        ]
        if len(finance) != 1:
            raise IntegrityError("FINANCE_EXPERIMENT_V4_REQUIRED")
        try:
            request = ModelRequestV4.model_validate(finance[0]["request"]).revalidated()
            receipt = ModelResponseReceiptV4.model_validate(finance[0]["receipt"]).revalidated()
            candidate = DomainAdvisoryCandidate.model_validate(receipt.value)
            wire = build_vertex_advice_body(request, DomainAdvisoryCandidate)
        except (KeyError, TypeError, ValueError) as exc:
            raise IntegrityError("FINANCE_EXPERIMENT_V4_INVALID") from exc
        if (
            receipt.status != "VALID"
            or receipt.request_digest != request.digest
            or receipt.body_digest != sha256_digest(wire)
            or private.get("finance_wire_body") != wire
            or result.get("finance_wire_body_digest") != receipt.body_digest
            or result.get("finance_request_digest") != request.digest
            or result.get("finance_receipt_digest") != receipt.digest
            or request.advice_context.memory_snapshot_digest != trial["snapshot_digest"]
            or request.advice_context.recall_manifest_digest != trial["manifest_digest"]
            or request.advice_context.package_ref != "skill-content-v2:" + trial["candidate_bundle_digest"][7:]
            or request.model_id != "gemini-3.8-flash"
            or candidate.domain_id != "finance"
            or sorted(candidate.source_refs) != sorted(item.ref for item in request.business_input_projections)
            or sorted(candidate.object_ids) != sorted(request.object_ids)
        ):
            raise IntegrityError("FINANCE_EXPERIMENT_V4_BINDING_INVALID")
        return result

    def record_controlled_protocol_result(self, *, trial_ref: str, result_ref: str) -> str:
        """Attach a V4 consumer result with a model-stub-only claim ceiling."""
        actor_id = self._actor(self.operator_actor_id, "propose")
        trial = self.store.load_artifact(trial_ref, TRIAL_MEDIA).payload
        family = self._load_family(trial["family_ref"])
        if trial["execution_scope"] != "CONTROLLED_FIXTURE" or trial["family_digest"] != sha256_digest(family):
            raise IntegrityError("FINANCE_EXPERIMENT_CONTROLLED_SCOPE_REQUIRED")
        result = self._validate_controlled_result(trial_ref, trial, result_ref)
        observation_ref = "finance-experiment-observation:" + trial_ref.rsplit(":", 1)[1]
        body = {
            "schema_version": "orgrebase.finance-experiment-observation.v1",
            "trial_ref": trial_ref, "trial_digest": sha256_digest(trial),
            "status": "CONTROLLED_PROTOCOL_VALID", "result_ref": result_ref,
            "result_digest": sha256_digest(result),
            "evidence_scope": "CONTROLLED_MODEL_STUB_PROTOCOL_ONLY",
            "operator_actor_id": actor_id,
            "quality_status": "NOT_EVALUATED", "target_writes": 0,
        }
        with self.store.transaction() as connection:
            self._actor(actor_id, "propose")
            self._family_lock(connection, trial["family_ref"])
            self._validate_controlled_result(trial_ref, trial, result_ref)
            try:
                prior = self.store.load_artifact(observation_ref, OBSERVATION_MEDIA).payload
            except KeyError:
                prior = None
            if prior is not None:
                if prior != body:
                    raise IntegrityError("FINANCE_EXPERIMENT_OBSERVATION_CONFLICT")
                return observation_ref
            self.store.save_artifact(connection, observation_ref, OBSERVATION_MEDIA, body)
            self.store.append_event(connection, "FINANCE_EXPERIMENT_CONTROLLED_RESULT_ATTACHED", {
                "trial_ref": trial_ref, "observation_ref": observation_ref,
                "observation_digest": sha256_digest(body), "target_writes": 0,
            })
        return observation_ref

    def verify_sealed_pair_lineage(
        self, *, family_ref: str, suite_ref: str, case_ref: str,
        case_revision_digest: str, cluster_id: str,
        baseline_result_ref: str, candidate_result_ref: str,
        parent_bundle_digest: str, candidate_bundle_digest: str,
        _release_verification: bool = False,
    ) -> dict[str, Any]:
        """Release gate for the exact REAL_VERTEX paired lineage.

        The current implementation intentionally has no trusted real-terminal
        reconciler.  Even a structurally valid pair therefore ends in a
        stable fail-closed reason until provider observations, sealed exposure
        and per-arm terminal receipts are implemented and audited.
        """
        if _release_verification:
            self._release_actor()
        else:
            self._actor(self.evaluator_actor_id, "govern")
        if self.workspace is None:
            raise IntegrityError("FINANCE_EXPERIMENT_REAL_WORKSPACE_REQUIRED")
        family = self._load_family(family_ref)
        if family["execution_scope"] != "REAL_VERTEX":
            raise IntegrityError("FINANCE_EXPERIMENT_REAL_FAMILY_REQUIRED")
        case = family["cases"].get(case_ref)
        derived = derive_finance_case_cluster(self.workspace, case_ref)
        if (
            case is None or case["split"] != "SEALED_HOLDOUT"
            or family["sealed_suite_ref"] != suite_ref
            or case["case_revision_digest"] != case_revision_digest
            or case["independence_cluster_id"] != cluster_id
            or case["dependency_keys_digest"] != derived["dependency_keys_digest"]
            or case["dependency_key_digests"] != list(derived["dependency_key_digests"])
        ):
            raise IntegrityError("FINANCE_EXPERIMENT_SEALED_CASE_BINDING_INVALID")
        require_independent_dependency_clusters({
            ref: (item["independence_cluster_id"], tuple(item["dependency_key_digests"]))
            for ref, item in family["cases"].items()
        })
        if baseline_result_ref == candidate_result_ref:
            raise IntegrityError("FINANCE_EXPERIMENT_PAIR_DUPLICATED")
        from orgrebase.workspace.finance_explanation_operations import INTENT_MEDIA, RESULT_MEDIA

        pair = []
        for result_ref, expected_bundle in (
            (baseline_result_ref, parent_bundle_digest),
            (candidate_result_ref, candidate_bundle_digest),
        ):
            result = self.store.load_artifact(result_ref, RESULT_MEDIA).payload
            intent = self.store.load_artifact(result["intent_ref"], INTENT_MEDIA).payload
            trial_ref = intent.get("trial_ref")
            if not isinstance(trial_ref, str):
                raise IntegrityError("FINANCE_EXPERIMENT_TRIAL_BINDING_REQUIRED")
            trial = self.store.load_artifact(trial_ref, TRIAL_MEDIA).payload
            if (
                trial.get("family_ref") != family_ref
                or trial.get("family_digest") != sha256_digest(family)
                or trial.get("case_ref") != case_ref
                or trial.get("case_revision_digest") != case_revision_digest
                or trial.get("cluster_id") != cluster_id
                or trial.get("split") != "SEALED_HOLDOUT"
                or trial.get("candidate_bundle_digest") != expected_bundle
                or trial.get("execution_scope") != "REAL_VERTEX"
                or trial.get("actor_id") != self.operator_actor_id
                or intent.get("operation_id") != trial.get("operation_id")
                or result.get("operation_id") != trial.get("operation_id")
                or intent.get("candidate_bundle_digest") != expected_bundle
                or result.get("candidate_bundle_digest") != expected_bundle
                or intent.get("budget_contract_digest") != trial.get("budget_contract_digest")
                or intent.get("reserved_calls") != trial.get("reserved_calls")
                or intent.get("reserved_microusd") != trial.get("reserved_microusd")
                or result.get("intent_digest") != sha256_digest(intent)
                or result.get("status") != "PROTOCOL_VALID"
                or result.get("target_writes") != 0
            ):
                raise IntegrityError("FINANCE_EXPERIMENT_TRIAL_BINDING_INVALID")
            pair.append(trial_ref)
        if (
            pair[0] == pair[1]
            or sum(item["reserved_calls"] for item in self._trials(family_ref))
            > family["max_reserved_calls"]
            or sum(item["reserved_microusd"] for item in self._trials(family_ref))
            > family["max_reserved_microusd"]
        ):
            raise IntegrityError("FINANCE_EXPERIMENT_FAMILY_BUDGET_OR_PAIR_INVALID")
        # No code path can currently emit a verified REAL_VERTEX terminal.
        # Never infer it from a model result row or caller-provided status.
        raise IntegrityError("FINANCE_EXPERIMENT_REAL_TRIAL_TERMINAL_UNVERIFIED")

    def verify_sealed_pair_lineage_for_release(
        self, *, family_ref: str, suite_ref: str, case_ref: str,
        case_revision_digest: str, cluster_id: str,
        baseline_result_ref: str, candidate_result_ref: str,
        parent_bundle_digest: str, candidate_bundle_digest: str,
    ) -> dict[str, Any]:
        """Deployment-bound governor recheck; no gold or model text is returned."""
        return self.verify_sealed_pair_lineage(
            family_ref=family_ref, suite_ref=suite_ref, case_ref=case_ref,
            case_revision_digest=case_revision_digest, cluster_id=cluster_id,
            baseline_result_ref=baseline_result_ref,
            candidate_result_ref=candidate_result_ref,
            parent_bundle_digest=parent_bundle_digest,
            candidate_bundle_digest=candidate_bundle_digest,
            _release_verification=True,
        )

    def record_fixture_terminal(self, *, trial_ref: str, outcome_code: str) -> str:
        """Deterministic lineage fixture only; never a model-quality receipt."""
        actor_id = self._actor(self.evaluator_actor_id, "govern")
        trial = self.store.load_artifact(trial_ref, TRIAL_MEDIA).payload
        family = self._load_family(trial["family_ref"])
        if (
            trial.get("family_digest") != sha256_digest(family)
            or trial.get("actor_id") != self.operator_actor_id
            or trial.get("target_writes") != 0
        ):
            raise IntegrityError("FINANCE_EXPERIMENT_TRIAL_BINDING_INVALID")
        if trial["execution_scope"] != "CONTROLLED_FIXTURE":
            raise IntegrityError("FINANCE_EXPERIMENT_FIXTURE_ON_REAL_FORBIDDEN")
        _identifier(outcome_code, "FIXTURE_OUTCOME")
        observation_ref = "finance-experiment-observation:" + trial_ref.rsplit(":", 1)[1]
        body = {
            "schema_version": "orgrebase.finance-experiment-observation.v1",
            "trial_ref": trial_ref, "trial_digest": sha256_digest(trial),
            "status": "FIXTURE_TERMINAL", "outcome_code": outcome_code,
            "evidence_scope": "DETERMINISTIC_MECHANISM_ONLY", "evaluator_actor_id": actor_id,
            "quality_status": "NOT_EVALUATED", "target_writes": 0,
        }
        with self.store.transaction() as connection:
            self._actor(actor_id, "govern")
            self._family_lock(connection, trial["family_ref"])
            if self._load_family(trial["family_ref"]) != family:
                raise IntegrityError("FINANCE_EXPERIMENT_FAMILY_CHANGED")
            try:
                prior = self.store.load_artifact(observation_ref, OBSERVATION_MEDIA).payload
            except KeyError:
                prior = None
            if prior is not None:
                if prior != body:
                    raise IntegrityError("FINANCE_EXPERIMENT_OBSERVATION_CONFLICT")
                return observation_ref
            self.store.save_artifact(connection, observation_ref, OBSERVATION_MEDIA, body)
            self.store.append_event(connection, "FINANCE_EXPERIMENT_FIXTURE_TERMINAL", {
                "trial_ref": trial_ref, "observation_ref": observation_ref,
                "observation_digest": sha256_digest(body), "target_writes": 0,
            })
        return observation_ref

    def family_projection(self, family_ref: str) -> dict[str, Any]:
        """Low-sensitivity progress; no sealed gold, prompt or candidate text."""
        self._actor(self.evaluator_actor_id, "govern")
        family = self._load_family(family_ref)
        trials = self._trials(family_ref)
        statuses = {"RESULT_UNKNOWN": 0, "FIXTURE_TERMINAL": 0, "CONTROLLED_PROTOCOL_VALID": 0}
        for item in trials:
            ref = self._trial_ref(
                family_ref, case_ref=item["case_ref"], seed=item["seed"],
                order=item["order"], arm=item["arm"],
            )
            statuses[self.trial_status(ref)] += 1
        return {
            "family_ref": family_ref, "family_digest": sha256_digest(family),
            "execution_scope": family["execution_scope"],
            "case_count": len(family["cases"]),
            "cluster_count": len({item["independence_cluster_id"] for item in family["cases"].values()}),
            "lineage_count_registered": len(family["seeds"]) * len(family["orders"]),
            "arm_names": list(ARMS), "trial_count": len(trials),
            "reserved_calls": sum(item["reserved_calls"] for item in trials),
            "reserved_microusd": sum(item["reserved_microusd"] for item in trials),
            "statuses": statuses,
            "quality_status": "NOT_EVALUATED",
            "qualification_status": "NOT_QUALIFIED",
        }


_NUMBER = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)*%?(?![\w])")
_CURRENCY = re.compile(r"\b(?:USD|EUR|GBP|CNY|JPY|CAD|AUD)\b")


def _literal_facts(value: Any) -> tuple[set[str], set[str]]:
    numbers: set[str] = set()
    currencies: set[str] = set()
    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (tuple, list)):
            for nested in item:
                visit(nested)
        elif isinstance(item, (int, float, str)) and not isinstance(item, bool):
            text = str(item)
            numbers.update(_NUMBER.findall(text))
            currencies.update(_CURRENCY.findall(text.upper()))
    visit(value)
    return numbers, currencies


class FinanceIndependentOracle:
    """Evaluator-only, sealed-gold assessment of real V4 result evidence."""

    def __init__(
        self, workspace: Any, *, gold_owner_actor_id: str, assessor_actor_id: str,
        candidate_author_actor_id: str, optimizer_actor_id: str,
        release_governor_actor_id: str,
    ) -> None:
        if (
            not gold_owner_actor_id or not assessor_actor_id
            or len({gold_owner_actor_id, assessor_actor_id,
                    candidate_author_actor_id, optimizer_actor_id,
                    release_governor_actor_id}) != 5
            or workspace.private_retention_seconds <= 0
        ):
            raise ValueError("FINANCE_ORACLE_INDEPENDENT_ROLES_REQUIRED")
        self.workspace = workspace
        self.gold_owner_actor_id = gold_owner_actor_id
        self.assessor_actor_id = assessor_actor_id
        self.candidate_author_actor_id = candidate_author_actor_id
        self.optimizer_actor_id = optimizer_actor_id
        self.release_governor_actor_id = release_governor_actor_id
        self.private = PrivateRecordStore(
            workspace.store, workspace.clock,
            retention_seconds=workspace.private_retention_seconds,
        )

    def _actor(self, actor_id: str) -> str:
        principal = request_principal.get()
        if principal is None:
            raise AuthenticationError("FINANCE_ORACLE_PRINCIPAL_REQUIRED")
        authorize(principal, "govern", self.workspace.profile.organization_id)
        if principal.actor_id != actor_id:
            raise AuthorizationError("FINANCE_ORACLE_ACTOR_REQUIRED")
        from orgrebase.workspace.skill_evolution_v2 import FinanceSkillHeadService

        head = FinanceSkillHeadService(
            self.workspace.store, tenant_id=self.workspace.profile.organization_id,
        )
        if principal.actor_id == self.workspace.store.get_object(head.head_id).payload["actor_id"]:
            raise AuthorizationError("FINANCE_ORACLE_HEAD_GOVERNOR_COLLISION")
        check = current_authorization()
        if check is not None:
            check()
        return principal.actor_id

    def _release_actor(self) -> str:
        principal = request_principal.get()
        if principal is None:
            raise AuthenticationError("FINANCE_ORACLE_PRINCIPAL_REQUIRED")
        authorize(principal, "govern", self.workspace.profile.organization_id)
        if principal.actor_id != self.release_governor_actor_id:
            raise AuthorizationError("FINANCE_ORACLE_RELEASE_GOVERNOR_REQUIRED")
        check = current_authorization()
        if check is not None:
            check()
        return principal.actor_id

    def freeze_gold(
        self, *, event_id: str, suite_id: str, independence_cluster_id: str,
        required_concepts: tuple[str, ...], forbidden_claims: tuple[str, ...],
        next_step_concepts: tuple[str, ...], rubric_digest: str,
    ) -> str:
        """Construct source/object/fact bounds from current business, not output."""
        actor_id = self._actor(self.gold_owner_actor_id)
        from orgrebase.workspace.finance_explanation_operations import _current_case

        _identifier(suite_id, "SUITE_ID")
        _identifier(independence_cluster_id, "CLUSTER")
        _digest(rubric_digest, "RUBRIC_DIGEST")
        if rubric_digest != REVIEWED_FINANCE_RUBRIC_DIGEST:
            raise IntegrityError("FINANCE_ORACLE_RUBRIC_STALE")
        if not all((required_concepts, forbidden_claims, next_step_concepts)):
            raise IntegrityError("FINANCE_ORACLE_RUBRIC_EMPTY")
        for group in (required_concepts, forbidden_claims, next_step_concepts):
            if len(group) > 16 or any(not term or len(term) > 80 for term in group):
                raise IntegrityError("FINANCE_ORACLE_RUBRIC_INVALID")
        fixture, bundle = _current_case(self.workspace, event_id)
        derived = derive_finance_case_cluster(self.workspace, event_id)
        if independence_cluster_id != derived["cluster_id"]:
            raise IntegrityError("FINANCE_ORACLE_CLUSTER_SPOOFED")
        first_exposure = _first_exposure_audit(
            self.workspace, event_id, derived["cluster_id"],
        )
        if first_exposure["status"] == "EXPOSED":
            raise IntegrityError("FINANCE_ORACLE_PRIOR_MODEL_EXPOSURE")
        source_refs = []
        object_ids = []
        factual_values: list[Any] = []
        for delta in bundle.change_set.deltas:
            source = fixture.object(delta.object_id, delta.base_version)
            if source.domain != "finance":
                continue
            source_refs.append(source.ref)
            object_ids.append(source.id)
            factual_values.extend((delta.base_value, delta.proposed_value))
        if not source_refs:
            raise IntegrityError("FINANCE_ORACLE_FINANCE_CASE_REQUIRED")
        case_revision = finance_case_revision(bundle.change_set, bundle.preview)
        private = {
            "schema_version": "orgrebase.finance-sealed-gold-case.v1",
            "case_ref": event_id,
            "case_revision_digest": case_revision,
            "independence_cluster_id": independence_cluster_id,
            "dependency_keys_digest": derived["dependency_keys_digest"],
            "dependency_key_digests": list(derived["dependency_key_digests"]),
            "first_exposure_audit": first_exposure,
            "expected_source_refs_digest": sha256_digest(sorted(source_refs)),
            "expected_object_ids_digest": sha256_digest(sorted(object_ids)),
            "business_change_digest": bundle.change_set.digest,
            "business_preview_digest": bundle.preview.digest,
            "quote_before_digest": self.workspace.current_quote().digest,
            "factual_values": factual_values,
            "required_concepts": list(required_concepts),
            "forbidden_claims": list(forbidden_claims),
            "next_step_concepts": list(next_step_concepts),
            "rubric_digest": rubric_digest,
            "gold_owner_actor_id": actor_id,
        }
        gold_ref = f"finance-sealed-gold:{suite_id}:{event_id}"
        commitment_ref = "finance-oracle-gold-commitment:" + sha256_digest({
            "suite_id": suite_id, "event_id": event_id,
            "workspace_id": self.workspace.store.workspace_id,
        })[7:]
        commitment = {
            "schema_version": "orgrebase.finance-oracle-gold-commitment.v1",
            "gold_ref": gold_ref, "gold_digest": sha256_digest(private),
            "suite_id": suite_id,
            "case_ref": event_id, "case_revision_digest": case_revision,
            "independence_cluster_id": independence_cluster_id,
            "dependency_keys_digest": derived["dependency_keys_digest"],
            "first_exposure_audit_digest": sha256_digest(first_exposure),
            "first_exposure_status": first_exposure["status"],
            "sealed_eligibility": "HOLD_FIRST_EXPOSURE_UNVERIFIED",
            "rubric_digest": rubric_digest,
            "gold_owner_actor_id": actor_id,
            "assessor_actor_id": self.assessor_actor_id,
            "candidate_author_actor_id": self.candidate_author_actor_id,
            "optimizer_actor_id": self.optimizer_actor_id,
            "release_governor_actor_id": self.release_governor_actor_id,
            "target_writes": 0,
        }
        with self.workspace.store.transaction() as connection:
            self._actor(actor_id)
            self.workspace.store.require_before_commit(connection, lambda: self._actor(actor_id))
            _, current = _current_case(self.workspace, event_id)
            if finance_case_revision(current.change_set, current.preview) != case_revision:
                raise IntegrityError("FINANCE_ORACLE_CASE_CHANGED")
            if derive_finance_case_cluster(self.workspace, event_id)["dependency_keys_digest"] != derived["dependency_keys_digest"]:
                raise IntegrityError("FINANCE_ORACLE_CLUSTER_CHANGED")
            if _first_exposure_audit(self.workspace, event_id, derived["cluster_id"])["status"] == "EXPOSED":
                raise IntegrityError("FINANCE_ORACLE_PRIOR_MODEL_EXPOSURE")
            try:
                prior = self.workspace.store.load_artifact(
                    commitment_ref, GOLD_COMMITMENT_MEDIA,
                ).payload
            except KeyError:
                prior = None
            if prior is not None:
                live = self.private.read_owned(
                    gold_ref, owner_id=actor_id,
                    scope_ref="finance-sealed-suite:" + suite_id,
                )
                if prior != commitment or live is None or sha256_digest(live) != prior["gold_digest"]:
                    raise IntegrityError("FINANCE_ORACLE_GOLD_CONFLICT_OR_UNAVAILABLE")
                return commitment_ref
            self.private.write(
                connection, record_id=gold_ref,
                scope_ref="finance-sealed-suite:" + suite_id,
                owner_id=actor_id, payload=private,
            )
            self.workspace.store.save_artifact(
                connection, commitment_ref, GOLD_COMMITMENT_MEDIA, commitment,
            )
            self.workspace.store.append_event(connection, "FINANCE_ORACLE_GOLD_FROZEN", {
                "commitment_ref": commitment_ref,
                "commitment_digest": sha256_digest(commitment),
                "case_ref": event_id, "gold_digest": sha256_digest(private),
                "target_writes": 0,
            })
        return commitment_ref

    def assess_result(
        self, *, commitment_ref: str, result_ref: str,
        grounded_score: int, next_step_score: int, reason_code: str,
    ) -> str:
        """Assess actual persisted V4 candidate and replay current business state.

        A score is a signed human judgment over a pre-frozen rubric.  Hard
        source/object/state checks are computed independently.  The receipt
        never exposes sealed gold or model text to candidate authors.
        """
        actor_id = self._actor(self.assessor_actor_id)
        from orgrebase.workspace.finance_explanation_operations import (
            INTENT_MEDIA,
            RESULT_MEDIA,
            _current_case,
        )

        _identifier(reason_code, "ASSESSMENT_REASON")
        if any(isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 4
               for score in (grounded_score, next_step_score)):
            raise IntegrityError("FINANCE_ORACLE_SCORE_INVALID")
        commitment = self.workspace.store.load_artifact(
            commitment_ref, GOLD_COMMITMENT_MEDIA,
        ).payload
        if (
            commitment.get("schema_version") != "orgrebase.finance-oracle-gold-commitment.v1"
            or commitment.get("assessor_actor_id") != actor_id
            or commitment.get("gold_owner_actor_id") == actor_id
            or commitment.get("candidate_author_actor_id") != self.candidate_author_actor_id
            or commitment.get("optimizer_actor_id") != self.optimizer_actor_id
            or commitment.get("release_governor_actor_id") != self.release_governor_actor_id
        ):
            raise IntegrityError("FINANCE_ORACLE_COMMITMENT_INVALID")
        gold_ref = commitment["gold_ref"]
        suite_id = commitment["suite_id"]
        try:
            gold = self.private.read_owned(
                gold_ref, owner_id=commitment["gold_owner_actor_id"],
                scope_ref="finance-sealed-suite:" + suite_id,
            )
        except (PermissionError, KeyError) as exc:
            raise IntegrityError("FINANCE_ORACLE_GOLD_UNAVAILABLE") from exc
        if gold is None or sha256_digest(gold) != commitment["gold_digest"]:
            raise IntegrityError("FINANCE_ORACLE_GOLD_UNAVAILABLE")
        if (
            commitment.get("first_exposure_audit_digest")
            != sha256_digest(gold.get("first_exposure_audit"))
            or commitment.get("first_exposure_status")
            != gold["first_exposure_audit"].get("status")
            or commitment.get("dependency_keys_digest") != gold.get("dependency_keys_digest")
            or gold.get("rubric_digest") != REVIEWED_FINANCE_RUBRIC_DIGEST
            or commitment.get("rubric_digest") != REVIEWED_FINANCE_RUBRIC_DIGEST
        ):
            raise IntegrityError("FINANCE_ORACLE_GOLD_AUDIT_INVALID")
        result = self.workspace.store.load_artifact(result_ref, RESULT_MEDIA).payload
        intent = self.workspace.store.load_artifact(result["intent_ref"], INTENT_MEDIA).payload
        if (
            result.get("status") != "PROTOCOL_VALID"
            or result.get("intent_digest") != sha256_digest(intent)
            or result.get("event_id") != gold["case_ref"]
            or intent.get("case_revision_digest") != gold["case_revision_digest"]
            or intent.get("actor_id") in {actor_id, commitment["gold_owner_actor_id"]}
            or result.get("target_writes") != 0
            or result.get("private_record_digest") is None
        ):
            raise IntegrityError("FINANCE_ORACLE_RESULT_INVALID")
        self._require_gold_precedes_intent(commitment_ref, result["intent_ref"])
        hard_checks, concept_checks = self._recompute_checks(gold, intent, result)
        # These lexical checks are a necessary bound, not a sufficient proof
        # of semantic correctness or a customer-visible improvement.
        status = "ASSESSOR_REVIEWED" if all(hard_checks.values()) else "ASSESSED_HARD_FAIL"
        body = {
            "schema_version": "orgrebase.finance-independent-assessment.v1",
            "commitment_ref": commitment_ref, "commitment_digest": sha256_digest(commitment),
            "gold_ref": gold_ref, "gold_digest": commitment["gold_digest"],
            "result_ref": result_ref, "result_digest": sha256_digest(result),
            "intent_ref": result["intent_ref"], "intent_digest": sha256_digest(intent),
            "case_ref": gold["case_ref"], "case_revision_digest": gold["case_revision_digest"],
            "independence_cluster_id": gold["independence_cluster_id"],
            "rubric_digest": gold["rubric_digest"],
            "hard_checks": hard_checks, "concept_checks": concept_checks,
            "grounded_score": grounded_score, "next_step_score": next_step_score,
            "status": status, "reason_code": reason_code,
            "evidence_scope": "CONTROLLED_LOCAL" if intent.get("scope") == "STATIC_BASELINE_ONLY"
            else "SEALED_PAIR_PROTOCOL_AND_ASSESSOR",
            "quality_claim_ceiling": "NO_CUSTOMER_OR_CAUSAL_IMPROVEMENT_CLAIM",
            "first_exposure_status": commitment["first_exposure_status"],
            "sealed_eligibility": commitment["sealed_eligibility"],
            "assessor_actor_id": actor_id,
            "gold_owner_actor_id": commitment["gold_owner_actor_id"],
            "candidate_author_actor_id": intent["actor_id"],
            "declared_content_author_actor_id": commitment["candidate_author_actor_id"],
            "optimizer_actor_id": commitment["optimizer_actor_id"],
            "release_governor_actor_id": commitment["release_governor_actor_id"],
            "target_writes": 0,
        }
        assessment_ref = "finance-independent-assessment:" + sha256_digest({
            "commitment_ref": commitment_ref, "result_ref": result_ref,
        })[7:]
        with self.workspace.store.transaction() as connection:
            self._actor(actor_id)
            self.workspace.store.require_before_commit(connection, lambda: self._actor(actor_id))
            live_gold = self.private.read_owned(
                gold_ref, owner_id=commitment["gold_owner_actor_id"],
                scope_ref="finance-sealed-suite:" + suite_id,
            )
            if live_gold is None or sha256_digest(live_gold) != commitment["gold_digest"]:
                raise IntegrityError("FINANCE_ORACLE_GOLD_CHANGED")
            _, latest = _current_case(self.workspace, gold["case_ref"])
            if finance_case_revision(latest.change_set, latest.preview) != gold["case_revision_digest"]:
                raise IntegrityError("FINANCE_ORACLE_CASE_CHANGED")
            try:
                prior = self.workspace.store.load_artifact(
                    assessment_ref, ASSESSMENT_MEDIA,
                ).payload
            except KeyError:
                prior = None
            if prior is not None:
                if prior != body:
                    raise IntegrityError("FINANCE_ORACLE_ASSESSMENT_CONFLICT")
                return assessment_ref
            self.workspace.store.save_artifact(connection, assessment_ref, ASSESSMENT_MEDIA, body)
            self.workspace.store.append_event(connection, "FINANCE_INDEPENDENT_ASSESSMENT_RECORDED", {
                "assessment_ref": assessment_ref,
                "assessment_digest": sha256_digest(body),
                "result_ref": result_ref, "status": status,
                "assessor_actor_id": actor_id, "target_writes": 0,
            })
        return assessment_ref

    def _recompute_checks(
        self, gold: Mapping[str, Any], intent: Mapping[str, Any], result: Mapping[str, Any],
    ) -> tuple[dict[str, bool], dict[str, bool]]:
        """Rebuild checks from live private bytes and current canonical state."""
        from orgrebase.workspace.finance_explanation_operations import _current_case

        try:
            private = self.private.read_owned(
                result["private_record_ref"], owner_id=intent["actor_id"],
                scope_ref=result["intent_ref"],
            )
        except (PermissionError, KeyError) as exc:
            raise IntegrityError("FINANCE_ORACLE_PRIVATE_RESULT_UNAVAILABLE") from exc
        if private is None or sha256_digest(private) != result["private_record_digest"]:
            raise IntegrityError("FINANCE_ORACLE_PRIVATE_RESULT_UNAVAILABLE")
        handoffs = private.get("collaboration", {}).get("handoffs", [])
        finance_rows = [
            row["payload"]["model_advisory"]
            for row in handoffs
            if isinstance(row, dict) and isinstance(row.get("payload"), dict)
            and isinstance(row["payload"].get("model_advisory"), dict)
            and row["payload"]["model_advisory"].get("request", {}).get("contract_version") == "4"
        ]
        if len(finance_rows) != 1:
            raise IntegrityError("FINANCE_ORACLE_V4_REQUIRED")
        try:
            request = ModelRequestV4.model_validate(finance_rows[0]["request"]).revalidated()
            receipt = ModelResponseReceiptV4.model_validate(finance_rows[0]["receipt"]).revalidated()
            candidate = DomainAdvisoryCandidate.model_validate(receipt.value)
            wire = build_vertex_advice_body(request, DomainAdvisoryCandidate)
        except (KeyError, TypeError, ValueError) as exc:
            raise IntegrityError("FINANCE_ORACLE_V4_INVALID") from exc
        if (
            receipt.status != "VALID"
            or receipt.request_digest != request.digest
            or receipt.request_ref != request.request_id
            or receipt.body_digest != sha256_digest(wire)
            or wire != private.get("finance_wire_body")
            or result.get("finance_wire_body_digest") != receipt.body_digest
            or result.get("finance_request_digest") != request.digest
            or result.get("finance_receipt_digest") != receipt.digest
            or request.model_id != "gemini-3.8-flash"
            or request.domain_id != candidate.domain_id
        ):
            raise IntegrityError("FINANCE_ORACLE_WIRE_BINDING_INVALID")
        _, current = _current_case(self.workspace, gold["case_ref"])
        current_revision = finance_case_revision(current.change_set, current.preview)
        source_refs = sorted(item.ref for item in request.business_input_projections)
        hard_checks = {
            "case_current": current_revision == gold["case_revision_digest"],
            "business_change_current": current.change_set.digest == gold["business_change_digest"],
            "business_preview_current": current.preview.digest == gold["business_preview_digest"],
            "source_refs_exact": sha256_digest(source_refs) == gold["expected_source_refs_digest"]
            and sorted(candidate.source_refs) == source_refs,
            "object_ids_exact": sha256_digest(sorted(request.object_ids)) == gold["expected_object_ids_digest"]
            and sorted(candidate.object_ids) == sorted(request.object_ids),
            "finance_domain_exact": candidate.domain_id == "finance",
            "quote_unchanged": self.workspace.current_quote().digest == gold["quote_before_digest"],
            "approval_absent": self.workspace._approval_record(gold["case_ref"]) is None,
            "outcome_absent": self.workspace._outcome_record(gold["case_ref"]) is None,
            "dependency_cluster_current": (
                derive_finance_case_cluster(self.workspace, gold["case_ref"])["cluster_id"]
                == gold["independence_cluster_id"]
                and derive_finance_case_cluster(self.workspace, gold["case_ref"])["dependency_keys_digest"]
                == gold["dependency_keys_digest"]
            ),
        }
        explanation = candidate.explanation.casefold()
        concept_checks = {
            "required_concepts_present": all(term.casefold() in explanation for term in gold["required_concepts"]),
            "forbidden_claims_absent": all(term.casefold() not in explanation for term in gold["forbidden_claims"]),
            "next_step_present": any(term.casefold() in explanation for term in gold["next_step_concepts"]),
        }
        allowed_numbers, allowed_currencies = _literal_facts(gold["factual_values"])
        observed_numbers, observed_currencies = _literal_facts(candidate.explanation)
        hard_checks["numeric_literals_grounded"] = observed_numbers <= allowed_numbers
        hard_checks["currency_literals_grounded"] = observed_currencies <= allowed_currencies
        return hard_checks, concept_checks

    def _require_gold_precedes_intent(self, commitment_ref: str, intent_ref: str) -> None:
        gold_sequence = None
        intent_sequence = None
        cursor = 0
        observed = 0
        while True:
            page = self.workspace.store.event_page(
                after=cursor, limit=500,
                event_types=("FINANCE_ORACLE_GOLD_FROZEN", "FINANCE_EVALUATION_INTENT_RESERVED"),
            )
            for event in page["items"]:
                observed += 1
                if observed > 10_000:
                    raise IntegrityError("FINANCE_ORACLE_EVENT_ORDER_UNBOUNDED")
                if event["event_type"] == "FINANCE_ORACLE_GOLD_FROZEN" and event["payload"].get("commitment_ref") == commitment_ref:
                    gold_sequence = event["sequence_no"]
                if event["event_type"] == "FINANCE_EVALUATION_INTENT_RESERVED" and event["payload"].get("intent_ref") == intent_ref:
                    intent_sequence = event["sequence_no"]
            cursor = page["next_cursor"]
            if cursor is None:
                break
        if gold_sequence is None or intent_sequence is None or gold_sequence >= intent_sequence:
            raise IntegrityError("FINANCE_ORACLE_GOLD_NOT_PREDECLARED")

    def verify_assessment_receipt(
        self, assessment_ref: str, *, expected_result_ref: str,
        expected_gold_ref: str, expected_rubric_digest: str,
        expected_case_ref: str, expected_cluster_id: str,
        expected_grounded_score: int, expected_next_step_score: int,
        require_sealed_pair: bool = True,
        require_hard_pass: bool = False,
        require_concept_pass: bool = False,
        _release_verification: bool = False,
    ) -> dict[str, Any]:
        """Recheck a signed receipt before it can support a qualification gate."""
        if _release_verification:
            self._release_actor()
        else:
            self._actor(self.gold_owner_actor_id)
        from orgrebase.workspace.finance_explanation_operations import INTENT_MEDIA, RESULT_MEDIA

        receipt = self.workspace.store.load_artifact(assessment_ref, ASSESSMENT_MEDIA).payload
        result = self.workspace.store.load_artifact(expected_result_ref, RESULT_MEDIA).payload
        intent = self.workspace.store.load_artifact(result["intent_ref"], INTENT_MEDIA).payload
        commitment = self.workspace.store.load_artifact(
            receipt["commitment_ref"], GOLD_COMMITMENT_MEDIA,
        ).payload
        gold = self.private.read_owned(
            expected_gold_ref, owner_id=self.gold_owner_actor_id,
            scope_ref="finance-sealed-suite:" + commitment["suite_id"],
        )
        if (
            gold is None or sha256_digest(gold) != receipt.get("gold_digest")
            or commitment.get("gold_digest") != receipt.get("gold_digest")
            or commitment.get("first_exposure_audit_digest")
            != sha256_digest(gold.get("first_exposure_audit"))
            or commitment.get("first_exposure_status")
            != gold["first_exposure_audit"].get("status")
            or receipt.get("first_exposure_status") != commitment.get("first_exposure_status")
            or receipt.get("sealed_eligibility") != commitment.get("sealed_eligibility")
            or commitment.get("dependency_keys_digest") != gold.get("dependency_keys_digest")
            or receipt.get("commitment_digest") != sha256_digest(commitment)
            or receipt.get("result_ref") != expected_result_ref
            or receipt.get("result_digest") != sha256_digest(result)
            or receipt.get("intent_ref") != result["intent_ref"]
            or receipt.get("intent_digest") != sha256_digest(intent)
            or receipt.get("gold_ref") != expected_gold_ref
            or receipt.get("rubric_digest") != expected_rubric_digest
            or receipt.get("rubric_digest") != REVIEWED_FINANCE_RUBRIC_DIGEST
            or receipt.get("case_ref") != expected_case_ref
            or receipt.get("case_revision_digest") != gold["case_revision_digest"]
            or receipt.get("independence_cluster_id") != expected_cluster_id
            or receipt.get("grounded_score") != expected_grounded_score
            or receipt.get("next_step_score") != expected_next_step_score
            or receipt.get("status") not in {"ASSESSOR_REVIEWED", "ASSESSED_HARD_FAIL"}
            or receipt.get("assessor_actor_id") != self.assessor_actor_id
            or receipt.get("gold_owner_actor_id") != self.gold_owner_actor_id
            or receipt.get("declared_content_author_actor_id") != self.candidate_author_actor_id
            or receipt.get("optimizer_actor_id") != self.optimizer_actor_id
            or receipt.get("release_governor_actor_id") != self.release_governor_actor_id
            or receipt.get("target_writes") != 0
            or (require_sealed_pair and (
                intent.get("scope") != "SEALED_HOLDOUT_PAIR"
                or receipt.get("evidence_scope") != "SEALED_PAIR_PROTOCOL_AND_ASSESSOR"
                or commitment.get("first_exposure_status") != "VERIFIED_FIRST_EXPOSURE"
                or commitment.get("sealed_eligibility") != "ELIGIBLE"
            ))
        ):
            raise IntegrityError("FINANCE_ORACLE_ASSESSMENT_INVALID")
        self._require_gold_precedes_intent(receipt["commitment_ref"], result["intent_ref"])
        hard_checks, concept_checks = self._recompute_checks(gold, intent, result)
        if (
            receipt["hard_checks"] != hard_checks
            or receipt["concept_checks"] != concept_checks
            or receipt["status"] != (
                "ASSESSOR_REVIEWED" if all(hard_checks.values()) else "ASSESSED_HARD_FAIL"
            )
            or (require_hard_pass and not all(hard_checks.values()))
            or (require_concept_pass and not all(concept_checks.values()))
        ):
            raise IntegrityError("FINANCE_ORACLE_ASSESSMENT_STALE")
        return receipt

    def verify_assessment_for_release(
        self, assessment_ref: str, *, expected_result_ref: str,
        expected_gold_ref: str, expected_rubric_digest: str,
        expected_case_ref: str, expected_cluster_id: str,
        expected_grounded_score: int, expected_next_step_score: int,
        require_sealed_pair: bool = True,
        require_hard_pass: bool = False,
        require_concept_pass: bool = False,
    ) -> dict[str, Any]:
        """A deployment-bound governor may recheck, but never receive gold text."""
        return self.verify_assessment_receipt(
            assessment_ref,
            expected_result_ref=expected_result_ref,
            expected_gold_ref=expected_gold_ref,
            expected_rubric_digest=expected_rubric_digest,
            expected_case_ref=expected_case_ref,
            expected_cluster_id=expected_cluster_id,
            expected_grounded_score=expected_grounded_score,
            expected_next_step_score=expected_next_step_score,
            require_sealed_pair=require_sealed_pair,
            require_hard_pass=require_hard_pass,
            require_concept_pass=require_concept_pass,
            _release_verification=True,
        )


__all__ = [
    "ARMS",
    "ASSESSMENT_MEDIA",
    "FAMILY_MEDIA",
    "GOLD_COMMITMENT_MEDIA",
    "OBSERVATION_MEDIA",
    "ORDERS",
    "TRIAL_MEDIA",
    "FinanceExperimentService",
    "FinanceIndependentOracle",
]
