"""Transport retry protocol tests; no actual provider requests."""
from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request

import pytest

from orgrebase.workspace import model_provider
from orgrebase.workspace.competition_worker import ReviewerDecision
from orgrebase.workspace.model_observations import ModelAttemptObserver
from tests.workspace.test_vertex_model_provider import _live_payload, _provider, _request, _Response


def success():
    return _Response(_live_payload(content=json.dumps({"verdict": "PASS", "missing_domains": [], "reason_codes": ["OK"]})))


def error(code=429, retry_after=None, usage=None):
    headers = {} if retry_after is None else {"Retry-After": str(retry_after)}
    body = {} if usage is None else {"usageMetadata": usage}
    return urllib.error.HTTPError("https://aiplatform.googleapis.com/model", code, "provider response", headers,
                                  io.BytesIO(json.dumps(body).encode()))


def test_429_retry_is_same_logical_request_with_two_durable_dispatches(tmp_path, monkeypatch):
    waits, timeouts = [], []
    def send(req, *, timeout):
        timeouts.append(timeout)
        if len(timeouts) == 1:
            raise error()
        return success()
    monkeypatch.setattr(urllib.request, "urlopen", send)
    monkeypatch.setattr(model_provider.random, "uniform", lambda a, b: b)
    monkeypatch.setattr(model_provider.time, "sleep", waits.append)
    provider = _provider(access_token="private-token", observer=ModelAttemptObserver(tmp_path))
    receipt = provider.generate_structured(request=_request(), output_model=ReviewerDecision)
    assert receipt.status == "VALID" and waits == [2]
    assert 0 < timeouts[1] <= timeouts[0] <= 60
    records = provider.observer.summary()["records"]
    assert len(records) == 2 and len({r['dispatch_id'] for r in records}) == 2
    assert len({r['request_digest'] for r in records}) == 1
    assert records[0]['error_code'] == 'VERTEX_RATE_LIMITED'
    assert records[0]['usage']['status'] == 'UNAVAILABLE' and records[0]['usage']['input_tokens'] is None
    assert records[1]['response_receipt_digest'] == receipt.digest
    assert all(r['persistence'] == 'DURABLE' for r in records)
    retry = provider.runtime_binding['transport_retry']
    assert retry['provider_attempts'] == 2 and retry['successful_calls'] == 1
    assert retry['receipt_latency_scope'] == 'FINAL_ATTEMPT_ONLY' and retry['elapsed_ms'] >= 0
    assert 'private-token' not in json.dumps(provider.runtime_binding) + json.dumps(records)
    assert len(list(tmp_path.glob('*.json'))) == 4


def test_429_exhaustion_keeps_all_failures_and_unknown_usage(monkeypatch):
    sends, waits = [], []
    def send(*args, **kwargs):
        sends.append(1)
        raise error()
    monkeypatch.setattr(urllib.request, 'urlopen', send)
    monkeypatch.setattr(model_provider.random, 'uniform', lambda a, b: b)
    monkeypatch.setattr(model_provider.time, 'sleep', waits.append)
    provider = _provider(access_token='private-token')
    receipt = provider.generate_structured(request=_request(), output_model=ReviewerDecision)
    assert receipt.error_code == 'VERTEX_HTTP_ERROR:429' and len(sends) == 3 and waits == [2, 4]
    assert len(provider.observer.summary()['records']) == 3
    assert provider.runtime_binding['transport_retry']['stop_reason'] == 'MAX_ATTEMPTS'


@pytest.mark.parametrize('code', [401, 403, 500, 503])
def test_non_429_does_not_retry(monkeypatch, code):
    sends = []
    def send(*args, **kwargs):
        sends.append(1)
        raise error(code)
    monkeypatch.setattr(urllib.request, 'urlopen', send)
    provider = _provider(access_token='private-token')
    assert provider.generate_structured(request=_request(), output_model=ReviewerDecision).status == 'PROVIDER_ERROR'
    assert len(sends) == 1


def test_retry_after_exceeding_deadline_does_not_sleep_or_send_again(monkeypatch):
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **kw: (_ for _ in ()).throw(error(retry_after=3600)))
    monkeypatch.setattr(model_provider.time, 'sleep', lambda _: pytest.fail('must not sleep past deadline'))
    provider = _provider(access_token='private-token', timeout_seconds=5)
    provider.generate_structured(request=_request(), output_model=ReviewerDecision)
    assert len(provider.observer.summary()['records']) == 1
    assert provider.runtime_binding['transport_retry']['stop_reason'] == 'TOTAL_DEADLINE_EXHAUSTED'


def test_429_usage_if_supplied_is_preserved(monkeypatch):
    sends, waits = [], []
    def send(*args, **kwargs):
        sends.append(1)
        if len(sends) == 1:
            raise error(retry_after=1, usage={'promptTokenCount': 7, 'candidatesTokenCount': 1, 'totalTokenCount': 8})
        return success()
    monkeypatch.setattr(urllib.request, 'urlopen', send)
    monkeypatch.setattr(model_provider.time, 'sleep', waits.append)
    provider = _provider(access_token='private-token')
    provider.generate_structured(request=_request(), output_model=ReviewerDecision)
    assert waits == [1]
    usage = provider.observer.summary()['records'][0]['usage']
    assert usage['status'] == 'REPORTED' and usage['total_tokens'] == 8


def test_unknown_timeout_and_bad_schema_are_not_retried(monkeypatch):
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        raise TimeoutError('unknown result')
    monkeypatch.setattr(urllib.request, 'urlopen', send)
    p = _provider(access_token='private-token')
    assert p.generate_structured(request=_request(), output_model=ReviewerDecision).status == 'PROVIDER_ERROR'
    assert len(calls) == 1
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **kw: _Response(_live_payload(content='{}')))
    p = _provider(access_token='private-token')
    assert p.generate_structured(request=_request(), output_model=ReviewerDecision).status == 'SCHEMA_ERROR'
    assert p.runtime_binding['transport_retry']['provider_attempts'] == 1


def test_total_budget_is_shared_across_wait_and_next_transport(monkeypatch):
    clock, timeouts = [0.0], []
    monkeypatch.setattr(model_provider.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(model_provider.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    def send(*args, timeout):
        timeouts.append(timeout)
        if len(timeouts) == 1:
            clock[0] += 3
            raise error(retry_after=1)
        clock[0] += 1
        return success()
    monkeypatch.setattr(urllib.request, 'urlopen', send)
    provider = _provider(access_token='private-token', timeout_seconds=5)
    assert provider.generate_structured(request=_request(), output_model=ReviewerDecision).status == 'VALID'
    assert timeouts == [5, 1]
    retry = provider.runtime_binding['transport_retry']
    assert retry['elapsed_ms'] == 5000 and retry['attempts'][0]['backoff_elapsed_ms'] == 1000


def test_failed_observation_persistence_stops_retry(tmp_path, monkeypatch):
    from orgrebase.workspace import model_observations
    writes, sends = [], []
    original = model_observations._write_record
    def write(directory, record):
        writes.append(record.phase)
        if record.phase == 'RESULT':
            raise OSError('disk unavailable')
        return original(directory, record)
    def send(*args, **kwargs):
        sends.append(1)
        raise error()
    monkeypatch.setattr(model_observations, '_write_record', write)
    monkeypatch.setattr(urllib.request, 'urlopen', send)
    p = _provider(access_token='private-token', observer=ModelAttemptObserver(tmp_path))
    r = p.generate_structured(request=_request(), output_model=ReviewerDecision)
    assert r.error_code == 'MODEL_OBSERVATION_RESULT_WRITE_FAILED' and len(sends) == 1
    assert p.runtime_binding['transport_retry']['stop_reason'] == 'OBSERVATION_DURABILITY_LOST'
    assert p.observer.summary()['records'][0]['persistence'] == 'WRITE_FAILED'


def test_rate_limit_error_body_uses_remaining_deadline(monkeypatch):
    from types import SimpleNamespace
    clock, socket_timeouts, sends = [0.0], [], []
    class SlowBody:
        length = None
        fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=socket_timeouts.append)))
        def isclosed(self):
            return False
        def read1(self, limit):
            clock[0] += 1
            return b' '
        def read(self, *args):
            raise AssertionError('Unbounded response read')
        def close(self):
            pass
    def send(*args, **kwargs):
        sends.append(1)
        raise urllib.error.HTTPError('https://example.invalid', 429, 'limited', {}, SlowBody())
    monkeypatch.setattr(model_provider.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(urllib.request, 'urlopen', send)
    provider = _provider(access_token='private-token', timeout_seconds=2)
    receipt = provider.generate_structured(request=_request(), output_model=ReviewerDecision)
    assert receipt.error_code == 'VERTEX_HTTP_ERROR:429'
    assert sends == [1] and socket_timeouts == [2, 1]
    assert provider.observer.summary()['records'][0]['usage']['status'] == 'UNAVAILABLE'
    assert provider.runtime_binding['transport_retry']['provider_attempts'] == 1


def test_submission_verifier_accepts_complete_retry_chain_and_rejects_resealed_mismatch(tmp_path, monkeypatch):
    import copy
    import importlib.util
    from pathlib import Path
    path = Path(os.environ.get("ORGREBASE_SUBMISSION_VERIFIER", ""))
    if not path.is_file():
        pytest.skip("Optional submission verifier is not part of this source tree")
    spec = importlib.util.spec_from_file_location('submission_retry_verifier', path)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    outcomes = iter([error(), success()])
    def send(*args, **kwargs):
        value = next(outcomes)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(urllib.request, 'urlopen', send)
    monkeypatch.setattr(model_provider.time, 'sleep', lambda _: None)
    provider = _provider(access_token='private-token', observer=ModelAttemptObserver(tmp_path))
    request = _request()
    receipt = provider.generate_structured(request=request, output_model=ReviewerDecision)
    collection = provider.observer.summary()
    final = collection['records'][-1]
    arguments = dict(request_digest=final['request_digest'], receipt_digest=receipt.digest,
                     provider_request_id=final['provider_request_id'], run_id=final['run_ref'],
                     model_id=final['requested_model'])
    assert verifier.verify_live_observation(collection, **arguments)['response_state'] == 'VALID'
    for key, value in [('request_digest', 'sha256:' + '0' * 64),
                       ('error_code', 'VERTEX_UNAUTHORIZED'),
                       ('dispatch_id', final['dispatch_id'])]:
        modified = copy.deepcopy(collection)
        modified['records'][0][key] = value
        modified['records'][0]['digest'] = verifier.content_digest({k: v for k, v in modified['records'][0].items() if k != 'digest'})
        with pytest.raises(ValueError):
            verifier.verify_live_observation(modified, **arguments)
