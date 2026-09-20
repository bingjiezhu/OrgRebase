from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import ObjectState, VersionedObject
from orgrebase.fixture import EnterpriseFixture


def test_canonical_json_is_order_independent() -> None:
    left = {"z": [2, 1], "a": {"é": True}}
    right = {"a": {"é": True}, "z": (2, 1)}
    assert canonical_json(left) == canonical_json(right)
    assert sha256_digest(left) == sha256_digest(right)
    assert json.loads(canonical_json(left)) == left


def test_content_addressed_model_sets_and_verifies_digest() -> None:
    item = VersionedObject(
        id="claim:test",
        version="v1",
        kind="ClaimVersion",
        label="Test",
        domain="test",
        state=ObjectState.CURRENT,
    )
    payload = item.model_dump(mode="json")
    assert item.digest.startswith("sha256:")
    assert sha256_digest(item.digest_payload()) == item.digest
    payload["digest"] = "sha256:" + "f" * 64
    with pytest.raises(ValidationError, match="content digest mismatch"):
        VersionedObject.model_validate(payload)


def test_fixture_has_one_coherent_vertical_slice(fixture: EnterpriseFixture) -> None:
    assert fixture.schema_version == "orgrebase.fixture.v2"
    assert len(fixture.agents) == 5
    assert len(fixture.impact_targets) == 6
    assert len(fixture.evaluation_cases) == 8
    assert fixture.object("claim:product.launch_date").version == "v7"
    assert fixture.object("claim:product.launch_date", "v8").state == ObjectState.PROPOSED
    assert fixture.dependency_manifest("work:legal_review_c").completeness == "COMPLETE"
    assert fixture.digest.startswith("sha256:")


def test_fixture_lookup_rejects_missing_or_ambiguous(fixture: EnterpriseFixture) -> None:
    with pytest.raises(KeyError):
        fixture.object("missing")
    payload = fixture.model_dump(mode="json")
    for item in payload["objects"]:
        if item["id"] == "claim:product.launch_date" and item["version"] == "v8":
            item["state"] = "CURRENT"
            item["digest"] = ""
    ambiguous = EnterpriseFixture.model_validate(payload)
    with pytest.raises(KeyError, match="expected one object"):
        ambiguous.object("claim:product.launch_date")
