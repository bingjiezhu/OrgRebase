from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_evolution import _demand, _digest, _intake_receipt, _outcome_certificate, _ref, _snapshot

from oac.canonical import OACValidationError, parse_resource, seal_resource
from oac.cli import main
from oac.evolution import (
    outcome_certificate_source_refs,
    project_execution_root,
    verify_outcome_certificate,
    verify_outcome_certificate_from_admitted,
)
from oac.models import (
    DimensionVerdict,
    OutcomeCertificate,
    OutcomeCertificateSpec,
    OutcomeDimension,
    OutcomeProfileBinding,
    OutcomeVerdict,
)
from oac.outcome_profiles import (
    DISPOSABLE_OUTCOME_DIMENSIONS,
    disposable_outcome_binding,
    get_outcome_profile,
    parse_outcome_profile,
)
from oac.sealed import admit_sealed_resource

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "profiles/outcome-profiles/disposable-local-v0.1"


@contextmanager
def _reject(codes: str) -> Iterator[None]:
    with pytest.raises(OACValidationError) as caught:
        yield
    assert caught.value.reason_code in codes.split("|")


def _seal(spec: OutcomeCertificateSpec, template: OutcomeCertificate) -> OutcomeCertificate:
    metadata = template.metadata.model_copy(
        update={"source_refs": outcome_certificate_source_refs(spec)}
    )
    return seal_resource(template.model_copy(update={"spec": spec, "metadata": metadata}))


def disposable_certificate(*, fail: bool = False, unknown: bool = False) -> OutcomeCertificate:
    snapshot = _snapshot()
    original = _outcome_certificate(
        snapshot, _intake_receipt(snapshot), _demand(snapshot), verdict=OutcomeVerdict.ACCEPT
    )
    profile = disposable_outcome_binding()
    binding = _ref("DisposableRuntimeBinding", "binding:disposable-local")
    bundle = _ref("DisposableRuntimeBundle", "bundle:disposable-local")
    grant = _ref("DisposableExecutionGrant", "grant:one-use-local-sandbox")
    evidence = original.spec.evidence_refs
    missing = _ref("OutcomeObservation", "observation:worker-state-unavailable")
    dimensions = []
    for name in DISPOSABLE_OUTCOME_DIMENSIONS:
        if name == "forbidden_effects" and fail:
            dimension = OutcomeDimension(
                name=name,
                verdict=DimensionVerdict.FAIL,
                evidenceRefs=evidence,
                reasonCodes=("OUTCOME_FORBIDDEN_EFFECT_OBSERVED",),
            )
        elif name == "replayability" and unknown:
            dimension = OutcomeDimension(
                name=name,
                verdict=DimensionVerdict.UNKNOWN,
                reasonCodes=("OUTCOME_OBSERVATION_UNRESOLVED",),
                unresolvedRefs=(missing,),
            )
        else:
            dimension = OutcomeDimension(
                name=name, verdict=DimensionVerdict.PASS, evidenceRefs=evidence
            )
        dimensions.append(dimension)
    spec = original.spec.model_copy(
        update={
            "profile_binding": profile,
            "execution_authorization_ref": grant,
            "runtime_binding_ref": binding,
            "runtime_bundle_ref": bundle,
            "execution_root": project_execution_root(
                binding,
                bundle,
                original.spec.execution_receipt_ref,
                original.spec.execution_evidence_refs,
                profile_binding=profile,
                execution_authorization_ref=grant,
            ),
            "dimensions": tuple(dimensions),
            "verdict": OutcomeVerdict.REJECT
            if fail
            else OutcomeVerdict.UNKNOWN
            if unknown
            else OutcomeVerdict.ACCEPT,
            "reason_codes": tuple(
                sorted({reason for dimension in dimensions for reason in dimension.reason_codes})
            ),
            "unresolved_refs": (missing,) if unknown else (),
        }
    )
    return _seal(spec, original)


@pytest.mark.parametrize(
    ("fail", "unknown"), [(False, False), (True, False), (False, True), (True, True)]
)
def test_disposable_verdicts_parse_verify_and_cli(
    fail: bool, unknown: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    certificate = disposable_certificate(fail=fail, unknown=unknown)
    verify_outcome_certificate(certificate)
    raw = certificate.model_dump_json(by_alias=True)
    assert parse_resource(raw.encode(), verify_digest=True) == certificate
    path = tmp_path / "outcome.json"
    path.write_text(raw)
    assert main(["validate-evolution", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"valid": True, "kind": "OutcomeCertificate"}


def test_default_wire_root_and_semantics_remain_identical() -> None:
    snapshot = _snapshot()
    certificate = _outcome_certificate(snapshot, _intake_receipt(snapshot), _demand(snapshot))
    raw = certificate.model_dump(mode="json", by_alias=True)
    assert "profileBinding" not in raw["spec"]
    assert "executionAuthorizationRef" not in raw["spec"]
    verify_outcome_certificate(certificate)
    spec = certificate.spec
    assert spec.execution_root == project_execution_root(
        spec.runtime_binding_ref,
        spec.runtime_bundle_ref,
        spec.execution_receipt_ref,
        spec.execution_evidence_refs,
    )
    # The original two-dimension contract remains valid; the new six-dimension
    # closure applies only to certificates explicitly selecting the new profile.
    assert len(spec.dimensions) == 2


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("profile_binding", None, "EVOLUTION_REF_KIND_MISMATCH"),
        (
            "profile_binding",
            OutcomeProfileBinding(
                profileId="untrusted:any-profile",
                profileVersion="v0.1",
                profileDigest=_digest("untrusted"),
            ),
            "OUTCOME_PROFILE_UNKNOWN",
        ),
        (
            "profile_binding",
            disposable_outcome_binding().model_copy(update={"profile_version": "v999"}),
            "OUTCOME_PROFILE_UNKNOWN",
        ),
        (
            "profile_binding",
            disposable_outcome_binding().model_copy(update={"profile_digest": _digest("drift")}),
            "OUTCOME_PROFILE_INVALID",
        ),
        (
            "runtime_binding_ref",
            _ref("RuntimeBinding", "binding:disposable-local"),
            "EVOLUTION_REF_KIND_MISMATCH",
        ),
        (
            "runtime_bundle_ref",
            _ref("ZeroEffectRuntimeBundle", "bundle:disposable-local"),
            "EVOLUTION_REF_KIND_MISMATCH",
        ),
        (
            "execution_receipt_ref",
            _ref("RuntimeLoweringReceipt", "receipt:wrong"),
            "EVOLUTION_REF_KIND_MISMATCH",
        ),
        ("execution_authorization_ref", None, "OUTCOME_EXECUTION_AUTHORIZATION_INVALID"),
        (
            "execution_authorization_ref",
            _ref("PlanCertificate", "grant:plan-is-not-authority"),
            "EVOLUTION_REF_KIND_MISMATCH",
        ),
        (
            "execution_authorization_ref",
            _ref("DisposableExecutionGrant", "grant:substituted"),
            "EVOLUTION_ROOT_MISMATCH",
        ),
        (
            "execution_authorization_ref",
            _ref("DisposableExecutionGrant", "grant:other", namespace="other-enterprise"),
            "EVOLUTION_NAMESPACE_MISMATCH",
        ),
    ],
)
def test_profile_kind_grant_and_root_attacks(field: str, value: object, code: str) -> None:
    certificate = disposable_certificate()
    attacked = _seal(certificate.spec.model_copy(update={field: value}), certificate)
    with pytest.raises(OACValidationError) as caught:
        verify_outcome_certificate(attacked)
    assert caught.value.reason_code == code


def test_default_profile_refuses_a_disposable_grant() -> None:
    snapshot = _snapshot()
    certificate = _outcome_certificate(snapshot, _intake_receipt(snapshot), _demand(snapshot))
    attacked = _seal(
        certificate.spec.model_copy(
            update={
                "execution_authorization_ref": _ref("DisposableExecutionGrant", "grant:forbidden")
            }
        ),
        certificate,
    )
    with _reject("OUTCOME_EXECUTION_AUTHORIZATION_INVALID"):
        verify_outcome_certificate(attacked)


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate"])
def test_exact_dimension_set(mutation: str) -> None:
    certificate = disposable_certificate()
    dimensions = certificate.spec.dimensions
    dimensions = (
        dimensions[:-1]
        if mutation == "missing"
        else (
            *dimensions,
            dimensions[0].model_copy(
                update={"name": "arbitrary" if mutation == "extra" else dimensions[0].name}
            ),
        )
    )
    attacked = _seal(certificate.spec.model_copy(update={"dimensions": dimensions}), certificate)
    with _reject("OUTCOME_DIMENSION_SET_INVALID|OUTCOME_VERDICT_INVALID"):
        verify_outcome_certificate(attacked)


@pytest.mark.parametrize(
    "mutation",
    [
        "no-evidence",
        "outside-evidence",
        "cross-namespace",
        "pass-unresolved",
        "failure-no-reason",
        "unknown-no-ref",
        "lost-reason",
        "lost-unresolved",
        "invented-unresolved",
    ],
)
def test_dimension_evidence_is_closed(mutation: str) -> None:
    certificate = disposable_certificate(
        fail=mutation == "failure-no-reason",
        unknown=mutation in {"unknown-no-ref", "lost-reason", "lost-unresolved"},
    )
    dimensions = list(certificate.spec.dimensions)
    updates: dict[str, object] = {}
    if mutation == "no-evidence":
        dimensions[0] = dimensions[0].model_copy(update={"evidence_refs": ()})
    elif mutation in {"outside-evidence", "cross-namespace"}:
        dimensions[0] = dimensions[0].model_copy(
            update={
                "evidence_refs": (
                    _ref(
                        "OutcomeEvidence",
                        "evidence:missing",
                        namespace="other-enterprise"
                        if mutation == "cross-namespace"
                        else certificate.metadata.namespace,
                    ),
                )
            }
        )
    elif mutation == "pass-unresolved":
        dimensions[0] = dimensions[0].model_copy(
            update={"unresolved_refs": (_ref("OutcomeObservation", "missing"),)}
        )
    elif mutation == "failure-no-reason":
        dimensions[1] = dimensions[1].model_copy(update={"reason_codes": ()})
    elif mutation == "unknown-no-ref":
        dimensions[4] = dimensions[4].model_copy(update={"unresolved_refs": ()})
    elif mutation == "lost-reason":
        updates["reason_codes"] = ()
    elif mutation == "lost-unresolved":
        updates["unresolved_refs"] = ()
    else:
        updates["unresolved_refs"] = (_ref("OutcomeObservation", "missing"),)
    updates["dimensions"] = tuple(dimensions)
    with _reject("OUTCOME_DIMENSION_EVIDENCE_INVALID|EVOLUTION_NAMESPACE_MISMATCH"):
        verify_outcome_certificate(_seal(certificate.spec.model_copy(update=updates), certificate))


@pytest.mark.parametrize(
    "fail,unknown,verdict",
    [
        (True, True, OutcomeVerdict.UNKNOWN),
        (True, False, OutcomeVerdict.ACCEPT),
        (False, True, OutcomeVerdict.ACCEPT),
        (False, True, OutcomeVerdict.PROVISIONAL),
        (False, False, OutcomeVerdict.PROVISIONAL),
    ],
)
def test_failure_and_unknown_cannot_be_promoted(
    fail: bool, unknown: bool, verdict: OutcomeVerdict
) -> None:
    certificate = disposable_certificate(fail=fail, unknown=unknown)
    with _reject("OUTCOME_VERDICT_INVALID"):
        verify_outcome_certificate(
            _seal(certificate.spec.model_copy(update={"verdict": verdict}), certificate)
        )


@pytest.mark.parametrize("identity", ["actor", "grant", "receipt"])
def test_runtime_identity_cannot_be_relabelled_as_oracle(identity: str) -> None:
    certificate = disposable_certificate()
    runtime = {
        "actor": certificate.spec.acting_principal_refs[0],
        "grant": certificate.spec.execution_authorization_ref,
        "receipt": certificate.spec.execution_receipt_ref,
    }[identity]
    assert runtime is not None
    oracle = runtime.model_copy(update={"kind": "OutcomeOracle"})
    with _reject("OUTCOME_SELF_CERTIFICATION_FORBIDDEN"):
        verify_outcome_certificate(
            _seal(certificate.spec.model_copy(update={"oracle_ref": oracle}), certificate)
        )


def test_descriptor_cannot_be_extended_or_weakened() -> None:
    descriptor = get_outcome_profile(disposable_outcome_binding())
    assert descriptor == parse_outcome_profile((PROFILE / "profile.json").read_bytes())
    body = descriptor.model_dump(mode="json", by_alias=True)
    for field, value in [
        ("runtimeBundleKind", "ZeroEffectRuntimeBundle"),
        ("requiredDimensions", ["task_goal"]),
        ("callback", "allow_everything"),
        ("verificationScope", "production"),
    ]:
        changed = dict(body)
        changed[field] = value
        with _reject("OUTCOME_PROFILE_INVALID"):
            parse_outcome_profile(json.dumps(changed).encode())
    with _reject("OUTCOME_PROFILE_INVALID"):
        parse_outcome_profile(b" " * 16385)


def test_binding_extra_callback_is_structurally_rejected() -> None:
    with pytest.raises(ValidationError):
        OutcomeProfileBinding.model_validate(
            {**disposable_outcome_binding().model_dump(by_alias=True), "callback": "custom"}
        )


def test_grant_is_required_in_ordered_provenance() -> None:
    certificate = disposable_certificate()
    metadata = certificate.metadata.model_copy(
        update={"source_refs": certificate.metadata.source_refs[:-1]}
    )
    attacked = seal_resource(certificate.model_copy(update={"metadata": metadata}))
    with _reject("EVOLUTION_PROVENANCE_MISMATCH"):
        verify_outcome_certificate(attacked)


def test_explicit_profile_keeps_unique_shared_observation_provenance() -> None:
    certificate = disposable_certificate()
    observation = certificate.spec.observation_refs[0]
    spec = certificate.spec.model_copy(
        update={"evidence_refs": (observation, *certificate.spec.evidence_refs)}
    )
    certificate = _seal(spec, certificate)
    assert certificate.metadata.source_refs.count(observation.resource_id) == 1
    verify_outcome_certificate(certificate)
    admission = admit_sealed_resource(
        certificate.model_dump_json(by_alias=True).encode(), "OutcomeCertificate"
    )
    verify_outcome_certificate_from_admitted(admission)

    metadata = certificate.metadata.model_copy(
        update={"source_refs": (*certificate.metadata.source_refs, observation.resource_id)}
    )
    attacked = seal_resource(certificate.model_copy(update={"metadata": metadata}))
    with _reject("EVOLUTION_PROVENANCE_MISMATCH"):
        verify_outcome_certificate(attacked)
    with _reject("CORE_SCHEMA_INVALID"):
        admit_sealed_resource(attacked.model_dump_json(by_alias=True).encode(), "OutcomeCertificate")


def test_cli_rejects_structurally_valid_verdict_downgrade(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    certificate = disposable_certificate(fail=True, unknown=True)
    attacked = _seal(
        certificate.spec.model_copy(update={"verdict": OutcomeVerdict.UNKNOWN}), certificate
    )
    path = tmp_path / "forged-outcome.json"
    path.write_text(attacked.model_dump_json(by_alias=True))
    assert main(["validate-evolution", str(path)]) == 2
    assert "OUTCOME_VERDICT_INVALID" in capsys.readouterr().err


@pytest.mark.parametrize(
    "name,fail,unknown",
    [
        ("accept", False, False),
        ("reject", True, False),
        ("unknown", False, True),
        ("reject-with-unknown", True, True),
    ],
)
def test_published_fixtures_match_construction(name: str, fail: bool, unknown: bool) -> None:
    parsed = parse_resource(
        (PROFILE / "examples" / f"{name}.json").read_bytes(), verify_digest=True
    )
    assert parsed == disposable_certificate(fail=fail, unknown=unknown)
    assert isinstance(parsed, OutcomeCertificate)
    verify_outcome_certificate(parsed)


def test_default_kind_error_precedes_namespace_error() -> None:
    snapshot = _snapshot()
    certificate = _outcome_certificate(snapshot, _intake_receipt(snapshot), _demand(snapshot))
    wrong_kind_and_namespace = _ref(
        "DisposableRuntimeBundle", "bundle:wrong", namespace="other-enterprise"
    )
    attacked = _seal(
        certificate.spec.model_copy(update={"runtime_bundle_ref": wrong_kind_and_namespace}),
        certificate,
    )
    with _reject("EVOLUTION_REF_KIND_MISMATCH"):
        verify_outcome_certificate(attacked)
