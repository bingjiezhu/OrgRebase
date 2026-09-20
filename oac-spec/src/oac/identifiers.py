"""Normative derived-identifier constructions used by the OAC reference tools.

The legacy 96-bit constructions are retained byte-for-byte for the v0.2
Supplier Profile.  New compiler-owned instance identifiers use a full SHA-256
digest with explicit domain separation.  Keeping both functions in one small
module makes the compatibility boundary executable and reviewable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, Literal

import rfc8785

from .json_types import JsonValue

LEGACY_DERIVED_ID_SCHEME = "oac.id/sha256-rfc8785-96/v0.2"
INSTANCE_ID_SCHEME = "oac.id/sha256-rfc8785/v1"

LegacyIdentifierKind = Literal["evaluation", "path", "obligation"]
InstanceIdentifierKind = Literal["role-instance", "work-unit", "plan-decision"]


def legacy_derived_id(kind: LegacyIdentifierKind, payload: Mapping[str, Any]) -> str:
    """Return the frozen v0.2 identifier; this is not a security identifier."""

    digest = hashlib.sha256(rfc8785.dumps(dict(payload))).hexdigest()[:24]
    return f"urn:oac:mvp:{kind}:{digest}"


def evaluation_identifier(
    *,
    snapshot_digest: str | None,
    change_digest: str | None,
    change_subject_ref: str,
    source_ref: str,
    predicate_version: str,
    result: str,
    reason_codes: Sequence[str],
    witness_refs: Sequence[str],
) -> str:
    """Build the exact legacy ApplicabilityEvaluation identifier preimage."""

    return legacy_derived_id(
        "evaluation",
        {
            "snapshotDigest": snapshot_digest or "MISSING",
            "changeDigest": change_digest or "MISSING",
            "changeSubjectRef": change_subject_ref,
            "sourceRef": source_ref,
            "predicateVersion": predicate_version,
            "result": result,
            "reasonCodes": list(reason_codes),
            "witnessRefs": list(witness_refs),
        },
    )


def impact_path_identifier(
    *,
    snapshot_digest: str | None,
    change_digest: str | None,
    target_ref: str,
    state: str,
    edge_refs: Sequence[str],
    rule_refs: Sequence[str],
    evaluation_refs: Sequence[str],
    duty_refs: Sequence[str],
    origin: str,
    truncated: bool,
) -> str:
    """Build the exact legacy ImpactPath identifier preimage."""

    return legacy_derived_id(
        "path",
        {
            "snapshot": snapshot_digest,
            "change": change_digest,
            "target": target_ref,
            "state": state,
            "edges": list(edge_refs),
            "rules": list(rule_refs),
            "evaluations": list(evaluation_refs),
            "duties": list(duty_refs),
            "origin": origin,
            "truncated": truncated,
        },
    )


def coverage_obligation_identifier(
    *,
    snapshot_digest: str | None,
    change_digest: str | None,
    origin: str,
    target_ref: str,
    required_role_ref: str,
    obligation_type: str,
    resolution_state: str,
    path_refs: Sequence[str],
) -> str:
    """Build the exact legacy CoverageObligation identifier preimage."""

    return legacy_derived_id(
        "obligation",
        {
            "snapshot": snapshot_digest,
            "change": change_digest,
            "origin": origin,
            "target": target_ref,
            "role": required_role_ref,
            "type": obligation_type,
            "state": resolution_state,
            "paths": list(path_refs),
        },
    )


def instance_identifier(
    kind: InstanceIdentifierKind,
    *,
    snapshot_digest: str,
    change_digest: str,
    body: Mapping[str, Any],
) -> str:
    """Return a full-digest, kind-separated identifier for new compiler output."""

    envelope: dict[str, JsonValue] = {
        "scheme": INSTANCE_ID_SCHEME,
        "kind": kind,
        "roots": {
            "snapshotDigest": snapshot_digest,
            "changeDigest": change_digest,
        },
        "body": dict(body),
    }
    digest = hashlib.sha256(rfc8785.dumps(envelope)).hexdigest()
    return f"urn:oac:id:sha256:v1:{kind}:{digest}"
