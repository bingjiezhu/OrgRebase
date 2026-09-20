from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from orgrebase.store import StateStore
from orgrebase.workspace.governed_evolution import (
    GovernedProcedureEvolution,
    GovernedPromotion,
)
from orgrebase.workspace.profile_contracts import (
    ProfileLimitationCode,
    RuntimeCompatibilityMode,
)
from tests.workspace.test_oac_governance_successors import (
    EvolutionFixtures,
    _decision,
    _promote,
    _proposal,
    evolution_fixtures,
)

_IMPORTED_FIXTURES = (evolution_fixtures,)


@pytest.fixture(scope="module")
def semantic_promotion(
    tmp_path_factory: pytest.TempPathFactory,
    evolution_fixtures: EvolutionFixtures,
) -> Iterator[GovernedPromotion]:
    store = StateStore(tmp_path_factory.mktemp("oac-governance-semantics") / "state.sqlite")
    try:
        proposal = _proposal(evolution_fixtures)
        decision = _decision(evolution_fixtures, proposal)
        yield _promote(
            GovernedProcedureEvolution(store, evolution_fixtures.cli),
            evolution_fixtures,
            proposal,
            decision,
        )
    finally:
        store.close()


@pytest.mark.parametrize("resource", ("profile", "snapshot"))
def test_source_and_promotion_bind_actual_resource_payload_digest(
    semantic_promotion: GovernedPromotion,
    resource: str,
) -> None:
    profile = semantic_promotion.profile_successor.profile_payload
    snapshot = json.loads(semantic_promotion.snapshot_successor.snapshot_payload_jcs)
    expected = profile.digest if resource == "profile" else snapshot["digest"]

    assert getattr(semantic_promotion.source_successor, f"{resource}_digest") == expected
    assert getattr(semantic_promotion.receipt, f"{resource}_successor_digest") == expected


def test_portable_admission_covers_candidate_profile_and_snapshot_payloads(
    semantic_promotion: GovernedPromotion,
) -> None:
    profile_successor = semantic_promotion.profile_successor
    profile = profile_successor.profile_payload
    snapshot_successor = semantic_promotion.snapshot_successor
    snapshot = json.loads(snapshot_successor.snapshot_payload_jcs)
    metadata = snapshot["metadata"]
    expected = {
        (
            "ProcedureContractCandidate",
            profile_successor.candidate_ref,
            1,
            profile_successor.candidate_digest,
        ),
        (
            "EnterpriseSeedProfile",
            profile.profile_id,
            int(profile.revision.removeprefix("r")),
            profile.digest,
        ),
        (
            "OrganizationSnapshot",
            metadata["id"],
            metadata["revision"],
            snapshot["digest"],
        ),
    }
    spec = semantic_promotion.portable_source_admission["spec"]
    for field in ("subjectRefs", "admittedSubjectRefs"):
        actual = {
            (item["kind"], item["resourceId"], item["revision"], item["digest"]) for item in spec[field]
        }
        assert expected <= actual


def test_r3_profile_declares_candidate_bound_reference_handler(
    semantic_promotion: GovernedPromotion,
) -> None:
    successor = semantic_promotion.profile_successor
    runtime = successor.profile_payload.runtime_compatibility

    assert runtime.mode is RuntimeCompatibilityMode.REFERENCE_HANDLER
    assert runtime.handler_profile == f"{successor.candidate_ref}@{successor.candidate_digest}"
    assert ProfileLimitationCode.INTAKE_ONLY not in successor.profile_payload.declared_limitation_codes


def test_snapshot_successor_passes_public_oac_validate_and_digest_verification(
    semantic_promotion: GovernedPromotion,
    evolution_fixtures: EvolutionFixtures,
    tmp_path: Path,
) -> None:
    snapshot_path = tmp_path / "organization-snapshot-r3.json"
    snapshot_path.write_text(
        semantic_promotion.snapshot_successor.snapshot_payload_jcs + "\n",
        encoding="utf-8",
    )

    result = evolution_fixtures.cli.run("validate", str(snapshot_path), "--verify-digest")

    assert result == {"kind": "OrganizationSnapshot", "valid": True}
