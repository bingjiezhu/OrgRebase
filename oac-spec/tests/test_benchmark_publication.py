from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runner(monkeypatch):
    spec = importlib.util.spec_from_file_location("benchmark_publication", ROOT / "scripts/run_benchmark.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "build_run_manifests", lambda root: {"a": {"value": 1}, "b": {"value": 2}})
    return module


def test_default_writer_refuses_existing_coordinate_without_partial_writes(tmp_path, monkeypatch, runner):
    output = tmp_path / "benchmark/runs"
    output.mkdir(parents=True)
    existing = output / "b.run.json"
    existing.write_bytes(b"historical bytes")
    monkeypatch.setattr(sys, "argv", ["benchmark", "--root", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2
    assert existing.read_bytes() == b"historical bytes"
    assert not (output / "a.run.json").exists()


def test_successor_coordinate_is_written_once(tmp_path, monkeypatch, runner):
    output = tmp_path / "new-coordinate"
    monkeypatch.setattr(sys, "argv", ["benchmark", "--output", str(output)])
    assert runner.main() == 0
    original = {path.name: path.read_bytes() for path in output.iterdir()}
    assert json.loads(original["a.run.json"]) == {"value": 1}
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2
    assert {path.name: path.read_bytes() for path in output.iterdir()} == original
