"""Independent recomputation verifier for executable impact certificates."""

from __future__ import annotations

from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    ChangeSetRevision,
    EffectDisposition,
    ImpactCertificate,
    ImpactClassification,
    ImpactPreview,
    IntegrityError,
    MinimalEffect,
    MinimalRebaseCertificate,
)
from orgrebase.fixture import EnterpriseFixture
from orgrebase.impact import ImpactEngine


class ImpactCertificateVerifier:
    version = "orgrebase.impact-certificate-verifier@1.0.0"

    def __init__(self, fixture: EnterpriseFixture) -> None:
        self.fixture = fixture

    def verify(
        self, payload: dict[str, Any], change_set: ChangeSetRevision
    ) -> dict[str, Any]:
        """Recompute the claimed result from canonical fixture inputs.

        Content-address validation catches byte-level tampering.  Exact comparison
        with a fresh engine run catches a self-consistent but false certificate.
        """

        try:
            certificate = ImpactCertificate.model_validate(payload)
        except ValueError as exc:
            raise IntegrityError("impact certificate digest or schema validation failed") from exc
        if certificate.change_set_digest != change_set.digest:
            raise IntegrityError("impact certificate is bound to a different ChangeSet")
        expected_preview = ImpactEngine(self.fixture).preview(change_set)
        matches = [
            item
            for item in expected_preview.certificates
            if item.subject_id == certificate.subject_id
        ]
        if len(matches) != 1 or matches[0].digest != certificate.digest:
            raise IntegrityError("impact certificate does not match canonical recomputation")
        expected = matches[0]
        return {
            "status": "PASS",
            "certificate_id": expected.id,
            "digest": expected.digest,
            "subject_id": expected.subject_id,
            "classification": expected.classification.value,
            "certificate_type": expected.certificate_type.value,
            "verifier_version": self.version,
            "checked": (
                "content_address",
                "change_set_binding",
                "revision_lock",
                "dependency_snapshot",
                "reachable_slice",
                "path_selection",
                "coverage_boundary",
            ),
        }


DISPOSITION_BY_CLASSIFICATION = {
    ImpactClassification.AFFECTED_HARD: EffectDisposition.REBUILD,
    ImpactClassification.AFFECTED_REVIEW: EffectDisposition.HOLD_FOR_REVIEW,
    ImpactClassification.AFFECTED_INFORMATIONAL: EffectDisposition.HOLD_FOR_REVIEW,
    ImpactClassification.UNAFFECTED_WITHIN_DECLARED_BOUNDARY: (
        EffectDisposition.PRESERVE_WITHIN_BOUNDARY
    ),
    ImpactClassification.UNKNOWN: EffectDisposition.HOLD_FOR_REVIEW,
    ImpactClassification.REQUALIFICATION_REQUIRED: EffectDisposition.REQUALIFY,
}


def _disposition_for(classification: ImpactClassification) -> EffectDisposition:
    if classification == ImpactClassification.OUT_OF_SCOPE:
        raise IntegrityError("OUT_OF_SCOPE_CANNOT_AUTHORIZE_KEEP")
    try:
        return DISPOSITION_BY_CLASSIFICATION[classification]
    except KeyError as exc:
        raise IntegrityError(f"UNHANDLED_IMPACT_CLASSIFICATION:{classification.value}") from exc


def build_minimal_rebase_certificate(
    change_set: ChangeSetRevision,
    preview: ImpactPreview,
) -> MinimalRebaseCertificate:
    impact_certificates = {item.subject_id: item for item in preview.certificates}
    if set(impact_certificates) != {item.object_id for item in preview.results}:
        raise IntegrityError("impact certificate set does not cover the exact preview target set")
    effects = tuple(
        MinimalEffect(
            target_id=result.object_id,
            disposition=_disposition_for(result.classification),
            impact_result_digest=result.digest,
            impact_certificate_digest=impact_certificates[result.object_id].digest,
        )
        for result in preview.results
    )
    return MinimalRebaseCertificate(
        id=f"minimal-rebase-certificate:{change_set.id.split(':', 1)[1]}@{change_set.revision}",
        change_set_digest=change_set.digest,
        preview_digest=preview.digest,
        revision_lock_digest=preview.revision_lock.digest,
        impact_certificate_set_digest=sha256_digest(
            sorted(item.digest for item in preview.certificates)
        ),
        effects=effects,
        minimality_invariants=(
            "every AFFECTED_HARD target is REBUILD",
            "no non-AFFECTED_HARD target is REBUILD",
            "UNKNOWN is HOLD_FOR_REVIEW and never PRESERVE",
            "every effect binds one independently verifiable impact certificate",
        ),
        claim_boundary=(
            "Minimal only for the exact ChangeSet, revision lock, target scope, dependency "
            "manifests, and deterministic transfer rules committed by this certificate."
        ),
    )


class MinimalRebaseCertificateVerifier:
    version = "orgrebase.minimal-rebase-verifier@1.0.0"

    def __init__(self, fixture: EnterpriseFixture) -> None:
        self.fixture = fixture

    def verify(
        self,
        payload: dict[str, Any],
        change_set: ChangeSetRevision,
    ) -> dict[str, Any]:
        try:
            certificate = MinimalRebaseCertificate.model_validate(payload)
        except ValueError as exc:
            raise IntegrityError("minimal Rebase certificate digest or schema validation failed") from exc
        expected_preview = ImpactEngine(self.fixture).preview(change_set)
        expected = build_minimal_rebase_certificate(change_set, expected_preview)
        if certificate.change_set_digest != change_set.digest:
            raise IntegrityError("minimal Rebase certificate is bound to another ChangeSet")

        submitted = {item.target_id: item for item in certificate.effects}
        expected_effects = {item.target_id: item for item in expected.effects}
        if len(submitted) != len(certificate.effects) or set(submitted) != set(expected_effects):
            raise IntegrityError("MINIMALITY_TARGET_SET_MISMATCH")
        for target_id, expected_effect in expected_effects.items():
            actual = submitted[target_id]
            if actual.disposition == expected_effect.disposition:
                continue
            if expected_effect.disposition == EffectDisposition.REBUILD:
                raise IntegrityError(f"MINIMALITY_MISSING_REBUILD:{target_id}")
            if actual.disposition == EffectDisposition.REBUILD:
                raise IntegrityError(f"MINIMALITY_EXTRA_REBUILD:{target_id}")
            raise IntegrityError(f"MINIMALITY_DISPOSITION_MISMATCH:{target_id}")
        if certificate.digest != expected.digest:
            raise IntegrityError("minimal Rebase certificate does not match canonical recomputation")
        return {
            "status": "PASS",
            "certificate_id": expected.id,
            "digest": expected.digest,
            "verifier_version": self.version,
            "checked": (
                "content_address",
                "change_set_binding",
                "revision_lock",
                "exact_target_set",
                "no_missing_rebuild",
                "no_extra_rebuild",
                "impact_certificate_bindings",
            ),
        }
