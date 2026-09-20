from __future__ import annotations

import hashlib
import json

import pytest

from orgrebase.archive_revalidation import BASE, archive_revalidation_view
from orgrebase.digest import sha256_digest
from orgrebase.semifinal_view import semifinal_evidence_view


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def bundle(tmp_path):
    base = tmp_path / BASE
    source = _write(tmp_path / 'src/version.py', {'fixture': 'source'})
    input_digest = _write(tmp_path / 'inputs/case.json', {'fixture': 'input'})
    contents = {
        'bpi': {'verification.json': {'status': 'PASS', 'receipt_digest': 'bpi-root', 'queries_replayed': 128, 'strategies_replayed': 3},
                'receipt.json': {'digest': 'bpi-root'}},
        'formation': {'verification.json': {'status': 'PASS', 'verified_receipt_digest': 'formation-root',
                     'planned_domain_ids': ['legal', 'product'], 'actual_domain_ids': ['legal', 'product'],
                     'canonical_target_writes': 0, 'candidate_only': True, 'task_binding_count': 3,
                     'agentteams_actions': 20, 'run_id': 'run:fixture'}, 'probe-receipt.json': {'digest': 'formation-root'}},
        'skill': {'verification.json': {'status': 'PASS', 'failure_count': 0},
                  'summary.json': {'status': 'PASS', 'target_writes': 0, 'negative_probe_count': 4,
                                   'restoration_status': 'EXECUTED_AND_INVOKED', 'run_id': 'run:old-input'}},
        'owb': {'verification.json': {'status': 'PASS', 'profiles': 13, 'cases_per_profile': 192,
                                    'reference_score': 100.0, 'failed_strategy_profiles': ['no-unknown']}},
    }
    lanes = {}
    for lane, files in contents.items():
        lanes[lane] = {'original_archive': f'evidence/{lane}/historical', 'inputs': {'inputs/case.json': input_digest},
                       'files': {f'{lane}/{name}': _write(base / lane / name, value) for name, value in files.items()}}
    manifest = {'schema_version': 'orgrebase.archive-revalidation.v1', 'checked_at': '2026-09-18T00:00:00Z',
                'live_model_calls': 0, 'implementation_files': {'src/version.py': source}, 'lanes': lanes}
    manifest['digest'] = sha256_digest(manifest)
    _write(base / 'manifest.json', manifest)
    return tmp_path


def test_rechecks_are_visible_even_when_historical_aggregate_is_unavailable(bundle):
    view = semifinal_evidence_view(bundle)
    assert view['status'] == 'UNAVAILABLE'
    check = view['archive_revalidation']
    assert check['status'] == 'PASS' and check['current_business_run'] is False
    assert check['live_model_calls'] == 0
    assert check['items'][0]['summary'] == {'queries': 128, 'strategies': 3}
    assert check['items'][2]['summary']['process_restart_proven'] is False


def test_changed_implementation_closes_numbers(bundle):
    (bundle / 'src/version.py').write_text('changed')
    result = archive_revalidation_view(bundle)
    assert result['status'] == 'PARTIAL'
    assert all(i['status'] == 'STALE' and i['summary'] == {} for i in result['items'])


def test_changed_input_never_borrows_previous_pass(bundle):
    (bundle / 'inputs/case.json').write_text('changed')
    assert all(i['status'] == 'STALE' and not i['summary'] for i in archive_revalidation_view(bundle)['items'])


def test_corrupt_one_report_closes_only_that_lane(bundle):
    (bundle / BASE / 'bpi/verification.json').write_text('{}')
    results = archive_revalidation_view(bundle)['items']
    assert results[0]['status'] == 'INVALID' and results[0]['summary'] == {}
    assert all(i['status'] == 'PASS' for i in results[1:])


def test_missing_manifest_has_no_invented_date_or_numbers(tmp_path):
    view = archive_revalidation_view(tmp_path)
    assert view['status'] == 'UNAVAILABLE' and view['items'] == []
    assert 'checked_at' not in view


def test_manifest_reseal_cannot_admit_an_outside_report(bundle, tmp_path):
    path = bundle / BASE / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['lanes']['bpi']['files']['bpi/../../outside.json'] = '0' * 64
    manifest.pop('digest')
    manifest['digest'] = sha256_digest(manifest)
    path.write_text(json.dumps(manifest))
    assert archive_revalidation_view(bundle)['items'][0]['status'] == 'INVALID'


SUMMARY_FILES = (
    ('bpi', 'receipt.json'),
    ('formation', 'probe-receipt.json'),
    ('skill', 'summary.json'),
)


@pytest.mark.parametrize('lane,name', SUMMARY_FILES)
def test_summary_cannot_borrow_pass_when_its_file_binding_is_omitted(bundle, lane, name):
    path = bundle / BASE / 'manifest.json'
    manifest = json.loads(path.read_text())
    del manifest['lanes'][lane]['files'][f'{lane}/{name}']
    manifest['digest'] = sha256_digest({k: v for k, v in manifest.items() if k != 'digest'})
    path.write_text(json.dumps(manifest))
    summary = bundle / BASE / lane / name
    value = json.loads(summary.read_text())
    # The old view read these bytes even though the manifest no longer bound them.
    value['unbound_mutation'] = True
    if lane == 'skill':
        value['negative_probe_count'] = 9999
    summary.write_text(json.dumps(value))
    result = archive_revalidation_view(bundle)
    assert result['status'] == 'PARTIAL'
    item = next(item for item in result['items'] if item['id'] == lane)
    assert item['status'] == 'INVALID' and item['summary'] == {}
    assert all(item['status'] == 'PASS' for item in result['items'] if item['id'] != lane)


@pytest.mark.parametrize('lane,name', SUMMARY_FILES)
@pytest.mark.parametrize('mutation', ['missing', 'changed'])
def test_bound_summary_requires_the_exact_existing_bytes(bundle, lane, name, mutation):
    path = bundle / BASE / lane / name
    if mutation == 'missing':
        path.unlink()
    else:
        path.write_text(path.read_text() + '\n')
    result = archive_revalidation_view(bundle)
    item = next(item for item in result['items'] if item['id'] == lane)
    assert item['status'] == 'INVALID' and item['summary'] == {}
    assert all(item['status'] == 'PASS' for item in result['items'] if item['id'] != lane)
