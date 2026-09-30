"""A new build cannot overwrite or inherit historical release qualification."""

import copy
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import generate_release_facts as facts

ROOT = Path(__file__).resolve().parents[1]


def _retained_copy(tmp_path: Path) -> Path:
    destination = tmp_path / 'evidence/release-facts.json'
    destination.parent.mkdir(parents=True)
    destination.write_bytes((ROOT / 'evidence/release-facts.json').read_bytes())
    return destination


def test_real_retained_evidence_still_closes() -> None:
    facts.check_retained_facts(root=ROOT)


def test_historical_fact_tampering_is_rejected(tmp_path: Path) -> None:
    destination = _retained_copy(tmp_path)
    value = json.loads(destination.read_text())
    value['release'] = '0.5.0b3'
    destination.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='RETAINED_RELEASE_FACTS_DIGEST_DRIFT'):
        facts.check_retained_facts(root=tmp_path)


def test_changed_retained_result_is_not_hidden_by_historical_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual = json.loads(_retained_copy(tmp_path).read_text())
    current = copy.deepcopy(actual)
    current['code_release_gate']['current_release_qualified'] = True
    monkeypatch.setattr(facts, 'build_facts', lambda **kwargs: current)
    with pytest.raises(ValueError, match='RETAINED_RELEASE_FACTS_EVIDENCE_DRIFT'):
        facts.check_retained_facts(root=tmp_path)


def test_new_project_observation_does_not_reseal_retained_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = _retained_copy(tmp_path)
    original = destination.read_bytes()
    current = json.loads(original)
    current['release'] = '0.5.0b3'
    for section in ('semifinal_integrated_candidate_closure', 'code_release_gate'):
        binding = current[section]['current_build_binding']
        binding['files'][0]['current_sha256'] = 'a' * 64
        binding['files'][0]['status'] = 'MISMATCH'
    monkeypatch.setattr(facts, 'build_facts', lambda **kwargs: current)
    facts.check_retained_facts(root=tmp_path)
    assert destination.read_bytes() == original


def test_generator_refuses_frozen_output() -> None:
    frozen = ROOT / 'evidence/release-facts.json'
    original = frozen.read_bytes()
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/generate_release_facts.py'),
         '--output', str(frozen)], cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert 'RETAINED_RELEASE_FACTS_IMMUTABLE' in result.stderr
    assert frozen.read_bytes() == original


def test_development_cli_checks_its_explicit_output_without_touching_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = _retained_copy(tmp_path)
    before = frozen.read_bytes()
    expected = {"release": "test-development", "current_release_qualified": False}
    monkeypatch.setattr(facts, 'ROOT', tmp_path)
    monkeypatch.setattr(facts, 'build_facts', lambda **kwargs: expected)
    destination = tmp_path / 'evidence/development-release-facts.json'
    arguments = ['generate_release_facts.py', '--output', str(destination)]
    monkeypatch.setattr(sys, 'argv', arguments)
    facts.main()
    monkeypatch.setattr(sys, 'argv', [*arguments, '--check'])
    facts.main()
    destination.write_text('{"tampered":true}')
    with pytest.raises(SystemExit, match='DEVELOPMENT_RELEASE_FACTS_DRIFT'):
        facts.main()
    assert frozen.read_bytes() == before


@pytest.mark.parametrize('target', ['goai-semifinal-check', 'workspace-product-path-blackbox-check'])
def test_development_fact_workflows_check_the_file_they_generated(
    tmp_path: Path, target: str,
) -> None:
    # Dry-run the actual Make target with recursive make stubbed. No model,
    # build, evidence writer or shell recipe is executed.
    result = subprocess.run(
        ['make', '--no-print-directory', '-n', '-f', str(ROOT / 'Makefile'),
         'MAKE=true', f'PYTHON={sys.executable}', target],
        cwd=tmp_path, env=os.environ.copy(), capture_output=True, text=True, check=True,
    )
    commands = [shlex.split(line) for line in result.stdout.splitlines()
                if ' scripts/generate_release_facts.py' in line and not line.startswith('#')]
    generated = [command for command in commands if '--check' not in command]
    checked = [command for command in commands if '--check' in command]
    assert generated and checked
    expected_output = 'evidence/development-release-facts.json'
    for command in generated + checked:
        assert '--output' in command
        assert command[command.index('--output') + 1] == expected_output
