"""Pricing candidate display accepts only the task's declared source projection."""

import copy
import json

import pytest

from orgrebase.api import _priced_candidate_fields
from orgrebase.digest import sha256_digest


def fixture_input(tmp_path, domain="product", slot="quote_basket"):
    source = dict(
        domain_id=domain,
        slot_id=slot,
        object_ref="claim:priced@v1",
        sensitivity="INTERNAL",
        value={"currency": "GBP"},
        authority_ref="authority:priced@v1",
        source_id="source:priced",
        source_version="r1",
        source_digest="sha256:" + "1" * 64,
    )
    payload = dict(
        task_id="task-a1",
        domain=domain,
        source_values={slot: source},
        projection=dict(included_refs=[source["object_ref"]]),
    )
    folder = tmp_path / "process-inputs"
    folder.mkdir()
    (folder / "task-a1.json").write_text(json.dumps(payload))
    task = dict(id="task-a1", authority_domain=domain, input_digest=sha256_digest(payload))
    candidate = dict(
        predicate=slot,
        value=source["value"],
        subject_ref=source["object_ref"],
        authority_ref=source["authority_ref"],
        sensitivity="INTERNAL",
        value_schema_ref=f"schema:workspace.{slot}@v1",
        source_refs=[{k: source[k] for k in ("source_id", "source_version", "source_digest")}],
    )
    return task, payload, candidate


@pytest.mark.parametrize("domain,slot", [("product", "quote_basket"), ("finance", "pricing_policy")])
def test_declared_pricing_source_is_projectable(tmp_path, domain, slot):
    task, _, candidate = fixture_input(tmp_path, domain, slot)
    assert _priced_candidate_fields(tmp_path, task, [candidate]) == {slot}


@pytest.mark.parametrize(
    "field,value",
    [
        ("value", {"currency": "USD"}),
        ("authority_ref", "foreign"),
        ("subject_ref", "foreign"),
        ("sensitivity", "CONFIDENTIAL"),
        ("value_schema_ref", "arbitrary-schema"),
    ],
)
def test_candidate_cannot_escape_its_source(tmp_path, field, value):
    task, _, candidate = fixture_input(tmp_path)
    candidate[field] = value
    assert _priced_candidate_fields(tmp_path, task, [candidate]) is None


@pytest.mark.parametrize("mutation", ["digest", "projection", "source_version", "duplicate", "private"])
def test_pricing_projection_fail_closed(tmp_path, mutation):
    task, payload, candidate = fixture_input(tmp_path)
    candidates = [candidate]
    if mutation == "digest":
        task["input_digest"] = "sha256:" + "0" * 64
    elif mutation == "source_version":
        candidate["source_refs"][0]["source_version"] = "foreign"
    elif mutation == "duplicate":
        candidates.append(copy.deepcopy(candidate))
    else:
        if mutation == "projection":
            payload["projection"]["included_refs"] = []
        else:
            payload["source_values"]["quote_basket"]["sensitivity"] = "CONFIDENTIAL"
            candidate["sensitivity"] = "CONFIDENTIAL"
        task["input_digest"] = sha256_digest(payload)
        (tmp_path / "process-inputs/task-a1.json").write_text(json.dumps(payload))
    assert _priced_candidate_fields(tmp_path, task, candidates) is None
