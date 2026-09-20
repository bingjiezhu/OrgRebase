from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest
import rfc8785

from orgrebase.domain import IntegrityError
from orgrebase.workspace.oac_wire import _verify_oac_resource, locate_oac_root


def _resource(field: str) -> dict:
    name = {"snapshot": "veracier-proc01-contextual.snapshot.json", "change": "SC-008.change.json"}[field]
    path = locate_oac_root() / "experiments/plan-verification-portability/v0.1-seed-1/artifacts/SC-008" / name
    return json.loads(path.read_bytes())


@pytest.mark.parametrize("field", ["snapshot", "change"])
@pytest.mark.parametrize("member", ["effectiveFrom", "effectiveTo"])
def test_optional_metadata_removal_requires_a_new_raw_digest(field: str, member: str) -> None:
    resource = _resource(field)
    _verify_oac_resource(resource, resource["kind"])
    resource["metadata"].pop(member)

    with pytest.raises(IntegrityError, match="OAC_DIGEST_MISMATCH"):
        _verify_oac_resource(resource, resource["kind"])


@pytest.mark.parametrize("field", ["snapshot", "change"])
@pytest.mark.parametrize("member", ["effectiveFrom", "effectiveTo"])
def test_sparse_resource_uses_the_digest_of_its_exact_members(field: str, member: str) -> None:
    resource = _resource(field)
    resource["metadata"].pop(member)
    resource.pop("digest")
    resource["digest"] = "sha256:" + hashlib.sha256(rfc8785.dumps(resource)).hexdigest()
    before = deepcopy(resource)

    _verify_oac_resource(resource, resource["kind"])

    assert resource == before
