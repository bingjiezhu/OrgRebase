"""Content-addressed public-task evidence for the existing governed learning path.

Portable evidence is verified one run at a time and stored once. Case records
contain exact references; replay authority always comes from a caller-owned
trust resolver, never from the exported evidence or case itself.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.oac_wire import _resource_ref
from orgrebase.workspace.outcome_lab import SYSTEMS, OACPlanGate
from orgrebase.workspace.outcome_portability import LabOutcomeTrust, verify_portable_lab_outcome
from orgrebase.workspace.pattern_evolution import CaseObservation

PROFILE_ID = "orgrebase.tau2-retail-outcome-case/v2"
TASK_SOURCE_PATH = "data/tau2/domains/retail/tasks.json"
PUBLIC_PATHS = (
    "/user_scenario/persona",
    "/user_scenario/instructions/task_instructions",
    "/user_scenario/instructions/domain",
    "/user_scenario/instructions/reason_for_call",
    "/user_scenario/instructions/known_info",
    "/user_scenario/instructions/unknown_info",
)
PORTABLE_MEDIA = "application/vnd.orgrebase.outcome-case-portable+json"
TRUST_MEDIA = "application/vnd.orgrebase.outcome-case-trust+json"
TASK_MEDIA = "application/vnd.orgrebase.outcome-case-task+json"
_KINDS = {PORTABLE_MEDIA: "portable", TRUST_MEDIA: "trust", TASK_MEDIA: "task"}


@dataclass(frozen=True)
class OutcomeRunEvidence:
    """A complete portable bundle and its independent controller-held trust."""

    bundle: Mapping[str, Any]
    trust: LabOutcomeTrust


def _json_object(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as exc:
        raise IntegrityError("OUTCOME_CASE_JSON_OBJECT_REQUIRED") from exc
    if not isinstance(result, dict):
        raise IntegrityError("OUTCOME_CASE_JSON_OBJECT_REQUIRED")
    return result


def _public_projection(task: dict[str, Any]) -> dict[str, Any]:
    task_id = task.get("id")
    if isinstance(task_id, bool) or not isinstance(task_id, (str, int)) or not str(task_id):
        raise IntegrityError("OUTCOME_CASE_TASK_ID_REQUIRED")
    scenario = task.get("user_scenario")
    instructions = scenario.get("instructions") if isinstance(scenario, dict) else None
    if not isinstance(instructions, dict) or instructions.get("domain") != "retail":
        raise IntegrityError("OUTCOME_CASE_RETAIL_SCENARIO_REQUIRED")
    result: dict[str, Any] = {}
    for path in PUBLIC_PATHS:
        parts = path.split("/")[1:]
        source: Any = task
        for part in parts:
            if not isinstance(source, dict) or part not in source:
                break
            source = source[part]
        else:
            if source is not None and not isinstance(source, str):
                raise IntegrityError("OUTCOME_CASE_PUBLIC_FEATURE_MUST_BE_TEXT_OR_NULL")
            target = result
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = source
    return result


def _artifact_id(media: str, digest: str) -> str:
    return f"outcome-case:{_KINDS[media]}:{digest.removeprefix('sha256:')}"


def _save(store: StateStore, connection: Any, media: str, payload: Mapping[str, Any]) -> dict[str, str]:
    digest = sha256_digest(dict(payload))
    identity = _artifact_id(media, digest)
    store.save_artifact(connection, identity, media, payload)
    return {"artifact_id": identity, "media_type": media, "payload_digest": digest}


def _load(store: StateStore, ref: Mapping[str, Any], media: str) -> dict[str, Any]:
    if (
        set(ref) != {"artifact_id", "media_type", "payload_digest"}
        or ref["media_type"] != media
        or not isinstance(ref["payload_digest"], str)
        or ref["artifact_id"] != _artifact_id(media, ref["payload_digest"])
    ):
        raise IntegrityError("OUTCOME_CASE_ARTIFACT_REF_INVALID")
    artifact = store.load_artifact(ref["artifact_id"], media)
    if artifact.payload_digest != ref["payload_digest"]:
        raise IntegrityError("OUTCOME_CASE_ARTIFACT_DIGEST_MISMATCH")
    return artifact.payload


def _trust_record(trust: LabOutcomeTrust) -> dict[str, Any]:
    return {
        "mapping": trust.mapping.model_dump(mode="json"),
        "oracle": trust.oracle.model_dump(mode="json"),
        **{
            name: getattr(trust, name)
            for name in (
                "controller_authority",
                "acting_authority",
                "lifecycle_digest",
                "receipt_digest",
                "approval_digest",
                "oracle_build_digest",
                "recorded_at",
            )
        },
    }


def _verify_run(
    task: Mapping[str, Any], run: OutcomeRunEvidence, gate: OACPlanGate, issuer: str
) -> dict[str, Any]:
    mapping, oracle = run.trust.mapping.revalidated(), run.trust.oracle.revalidated()
    if issuer == run.trust.acting_authority:
        raise IntegrityError("OUTCOME_CASE_ACTOR_CANNOT_ISSUE_CORPUS")
    if mapping.task_digest != sha256_digest(dict(task)) or mapping.task_id != str(task["id"]):
        raise IntegrityError("OUTCOME_CASE_PUBLIC_TASK_BINDING_MISMATCH")
    verify_portable_lab_outcome(gate, run.bundle, run.trust)
    receipt = run.bundle["receipt"]
    outcome = run.bundle["outcome_certificate"]
    # The verifier above binds this actual OAC certificate to the independently
    # pinned run; an arbitrary case-supplied digest never creates a dependency.
    context = {
        "mapping_digest": mapping.digest,
        "oracle_digest": oracle.digest,
        "plan_certificate_digest": run.bundle["lifecycle"]["plan_certificate"]["digest"],
        "controller_authority": run.trust.controller_authority,
        "acting_authority": run.trust.acting_authority,
        "oracle_build_digest": run.trust.oracle_build_digest,
    }
    return {
        "source": {
            "upstream_repository": mapping.upstream_repository,
            "upstream_revision": mapping.upstream_revision,
            "task_path": TASK_SOURCE_PATH,
            "task_id": mapping.task_id,
            "task_digest": mapping.task_digest,
        },
        "context_digest": sha256_digest(context),
        "receipt_digest": receipt["digest"],
        "approval_digest": run.trust.approval_digest,
        "system": receipt["system"],
        "verdict": receipt["verdict"],
        "outcome_certificate_ref": _resource_ref(outcome),
    }


def _assemble(
    task: dict[str, Any], task_ref: dict[str, str], rows: list[dict[str, Any]], issuer: str
) -> CaseObservation:
    if not isinstance(issuer, str) or not issuer.strip():
        raise IntegrityError("OUTCOME_CASE_CORPUS_AUTHORITY_REQUIRED")
    public_input = _public_projection(task)
    if not rows:
        raise IntegrityError("OUTCOME_CASE_EXPERIMENT_REQUIRED")
    source = rows[0]["source"]
    counts: dict[str, Counter[str]] = {}
    approvals: set[str] = set()
    verdicts: set[str] = set()
    for row in rows:
        if row["source"] != source:
            raise IntegrityError("OUTCOME_CASE_SOURCE_COORDINATE_MISMATCH")
        if row["approval_digest"] in approvals:
            raise IntegrityError("OUTCOME_CASE_REPEATED_APPROVAL")
        approvals.add(row["approval_digest"])
        counts.setdefault(row["context_digest"], Counter())[row["system"]] += 1
        if row["system"] == "oac":
            verdicts.add(row["verdict"])
    if any(set(group) != set(SYSTEMS) or len(set(group.values())) != 1 for group in counts.values()):
        raise IntegrityError("OUTCOME_CASE_BALANCED_FOUR_SYSTEMS_REQUIRED")
    outcome: Literal["SUPPORT", "COUNTEREXAMPLE", "UNKNOWN"] = (
        "COUNTEREXAMPLE" if "REJECT" in verdicts else "UNKNOWN" if "UNKNOWN" in verdicts else "SUPPORT"
    )
    case_id = "public-task:" + sha256_digest(
        {key: source[key] for key in ("upstream_repository", "task_id")}
    ).removeprefix("sha256:")
    revision = sha256_digest({key: source[key] for key in ("upstream_revision", "task_digest")})
    certificate = {
        "schema_version": "orgrebase.outcome-case-evidence.v2",
        "kind": "retail-outcome-case-candidate",
        "profile_id": PROFILE_ID,
        "case_id": case_id,
        "case_revision": revision,
        "input_digest": sha256_digest(public_input),
        "outcome": outcome,
        "issuer": issuer,
        "source": source,
        "public_task_ref": task_ref,
        "public_projection_paths": list(PUBLIC_PATHS),
        "runs": sorted(rows, key=lambda item: (item["context_digest"], item["receipt_digest"])),
        "experiment_counts": {key: dict(sorted(value.items())) for key, value in sorted(counts.items())},
        "source_case_count": 1,
        "corpus_admitted": False,
        "claim_scope": "DISPOSABLE_LOCAL_TASK_CANDIDATE_NOT_INDEPENDENT_OR_ENTERPRISE_EVIDENCE",
    }
    certificate["digest"] = sha256_digest(certificate)
    return CaseObservation(
        case_id=case_id,
        revision=revision,
        public_input=public_input,
        outcome=outcome,
        certificate=certificate,
    )


def build_retail_case_candidate(
    public_task: Mapping[str, Any],
    runs: Iterable[OutcomeRunEvidence],
    *,
    store: StateStore,
    gate: OACPlanGate,
    corpus_authority: str,
) -> CaseObservation:
    """Atomically retain verified evidence and return a small, unadmitted case.

    Pass a generator when reading large archives. No complete run is retained in
    the resulting case, and repeated content-addressed writes are idempotent.
    """
    task = _json_object(public_task)
    _public_projection(task)
    rows = []
    with store.transaction() as connection:
        task_ref = _save(store, connection, TASK_MEDIA, task)
        for run in runs:
            # Detach caller-owned containers once. Verification and retention
            # must consume the same bytes even if the caller reuses its buffer.
            run = OutcomeRunEvidence(_json_object(run.bundle), run.trust)
            row = _verify_run(task, run, gate, corpus_authority)
            row["portable_ref"] = _save(store, connection, PORTABLE_MEDIA, run.bundle)
            row["trust_ref"] = _save(store, connection, TRUST_MEDIA, _trust_record(run.trust))
            rows.append(row)
        return _assemble(task, task_ref, rows, corpus_authority)


def make_retail_case_resolver(
    store: StateStore,
    gate: OACPlanGate,
    trust_resolver: Callable[[str], LabOutcomeTrust],
) -> Callable[[CaseObservation], tuple[str, ...]]:
    """Bind corpus admission to scoped artifacts and independent host trust pins."""

    def resolve(case: CaseObservation) -> tuple[str, ...]:
        case = case.revalidated()
        certificate = case.certificate
        if (certificate.get("schema_version"), certificate.get("kind"), certificate.get("profile_id")) != (
            "orgrebase.outcome-case-evidence.v2",
            "retail-outcome-case-candidate",
            PROFILE_ID,
        ):
            raise IntegrityError("OUTCOME_CASE_PROFILE_UNSUPPORTED")
        task_ref = certificate["public_task_ref"]
        task = _load(store, task_ref, TASK_MEDIA)
        rows = []
        for row in certificate["runs"]:
            bundle = _load(store, row["portable_ref"], PORTABLE_MEDIA)
            try:
                trust = trust_resolver(row["receipt_digest"])
            except LookupError as exc:
                raise IntegrityError("OUTCOME_CASE_TRUST_NOT_FOUND") from exc
            if canonical_json(_load(store, row["trust_ref"], TRUST_MEDIA)) != canonical_json(
                _trust_record(trust)
            ):
                raise IntegrityError("OUTCOME_CASE_TRUST_CONTEXT_MISMATCH")
            verified = _verify_run(task, OutcomeRunEvidence(bundle, trust), gate, certificate["issuer"])
            verified.update(portable_ref=row["portable_ref"], trust_ref=row["trust_ref"])
            if canonical_json(verified) != canonical_json(row):
                raise IntegrityError("OUTCOME_CASE_PORTABLE_REFERENCE_BINDING_MISMATCH")
            rows.append(verified)
            # Drop this complete payload before loading the next run.
            del bundle
        expected = _assemble(task, task_ref, rows, certificate["issuer"])
        if canonical_json(expected.model_dump(mode="json")) != canonical_json(case.model_dump(mode="json")):
            raise IntegrityError("OUTCOME_CASE_RESOLVED_PROJECTION_MISMATCH")
        return tuple(sorted({row["outcome_certificate_ref"]["digest"] for row in rows}))

    return resolve
