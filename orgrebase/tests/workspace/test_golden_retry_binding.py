"""Evidence-format downgrade checks with synthetic HTTP responses; no model calls."""
from __future__ import annotations

from copy import deepcopy

import pytest

from orgrebase.workspace.competition_worker import ReviewerDecision
from orgrebase.workspace.model_observations import ModelAttemptObserver
from tests.workspace.test_golden_pilot_evidence import _load_finalizer, _load_verifier
from tests.workspace.test_vertex_model_provider import _provider, _request
from tests.workspace.test_vertex_rate_limit_retry import error, success


@pytest.fixture
def retry_output(tmp_path, monkeypatch):
    outcomes = iter([error(), success()])

    def send(*args, **kwargs):
        result = next(outcomes)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("urllib.request.urlopen", send)
    monkeypatch.setattr("orgrebase.workspace.model_provider.time.sleep", lambda _: None)
    provider = _provider(access_token="test-token", observer=ModelAttemptObserver(tmp_path))
    request = _request()
    receipt = provider.generate_structured(request=request, output_model=ReviewerDecision)
    observations = provider.observer.summary()
    last = observations["records"][-1]
    output = {
        "run_id": last["run_ref"],
        "task_id": last["task_ref"],
        "model_receipt": receipt.model_dump(mode="json"),
        "model_runtime_binding": dict(provider.runtime_binding),
        "model_attempt_observations": observations,
    }
    return output, {"provider_attempt_count": 2, "failed_provider_attempt_count": 1}


@pytest.mark.parametrize("loader", [_load_finalizer, _load_verifier])
@pytest.mark.parametrize("mutation", ["removed_retry", "null_retry", "removed_retry_and_counts"])
def test_multiple_observations_cannot_downgrade_to_legacy_single_dispatch(loader, mutation, retry_output):
    module = loader()
    output, attempt = retry_output
    assert module._vertex_transport_chain_valid(output, attempt)
    changed = deepcopy(output)
    if mutation == "null_retry":
        changed["model_runtime_binding"]["transport_retry"] = None
    else:
        changed["model_runtime_binding"].pop("transport_retry")
    if mutation == "removed_retry_and_counts":
        attempt = {}
    assert not module._vertex_transport_chain_valid(changed, attempt)


@pytest.mark.parametrize("loader", [_load_finalizer, _load_verifier])
def test_single_observed_dispatch_and_pre_observer_format_stay_readable(loader, retry_output):
    output, _ = retry_output
    output["model_runtime_binding"].pop("transport_retry")
    output["model_attempt_observations"]["records"] = output["model_attempt_observations"]["records"][-1:]
    assert loader()._vertex_transport_chain_valid(output, {})
    assert loader()._vertex_transport_chain_valid({"model_runtime_binding": {}}, {})


@pytest.mark.parametrize("loader", [_load_finalizer, _load_verifier])
@pytest.mark.parametrize("mutation", ["digest", "request", "run", "task", "receipt", "missing_records", "empty_records"])
def test_single_observation_cannot_bypass_its_bindings(loader, mutation, retry_output):
    from orgrebase.digest import sha256_digest

    output, _ = retry_output
    output["model_runtime_binding"].pop("transport_retry")
    collection = output["model_attempt_observations"]
    collection["records"] = collection["records"][-1:]
    record = collection["records"][0]
    if mutation == "missing_records":
        collection.pop("records")
    elif mutation == "empty_records":
        collection["records"] = []
    elif mutation == "digest":
        record["request_digest"] = "0" * 64
    else:
        field = {"request": "request_digest", "run": "run_ref", "task": "task_ref", "receipt": "response_receipt_digest"}[mutation]
        record[field] = "wrong-binding"
        record["digest"] = sha256_digest({k: v for k, v in record.items() if k != "digest"})
    assert not loader()._vertex_transport_chain_valid(output, {})


@pytest.mark.parametrize("loader", [_load_finalizer, _load_verifier])
@pytest.mark.parametrize("dispatches", [None, [0], {}, "malformed"])
def test_malformed_retry_dispatches_fail_closed(loader, dispatches, retry_output):
    output, attempt = retry_output
    output["model_runtime_binding"]["transport_retry"]["attempts"] = dispatches
    assert not loader()._vertex_transport_chain_valid(output, attempt)
