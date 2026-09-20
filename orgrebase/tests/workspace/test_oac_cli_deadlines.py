from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from orgrebase.workspace import oac_wire


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> oac_wire.OACBlackBoxCLI:
    monkeypatch.delenv("ORGREBASE_OAC_PYTHON", raising=False)
    return oac_wire.OACBlackBoxCLI()


@pytest.mark.parametrize("to_file", [False, True])
def test_timeout_never_admits_partial_success(
    cli: oac_wire.OACBlackBoxCLI, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, to_file: bool,
) -> None:
    monkeypatch.setattr(oac_wire, "_OAC_COMMAND_TIMEOUT_SECONDS", 0.3)
    output = tmp_path / "certificate.json"
    output.write_text('{"status":"previous"}')
    script = (
        "import pathlib,sys,time; "
        "pathlib.Path(sys.argv[-1]).write_text('{\"status\":\"PASS\"}')"
        if to_file else "print('{\"status\":\"PASS\"}',flush=True)"
    )
    script = "import time; " + script + "; time.sleep(60)"
    monkeypatch.setattr(cli, "_command", lambda arguments: [sys.executable, "-c", script, *arguments])

    started = time.monotonic()
    with pytest.raises(RuntimeError, match=r"^OAC_CLI_TIMEOUT:verify$"):
        if to_file:
            cli.run_to_file(("verify",), output)
        else:
            cli.run("verify")

    assert time.monotonic() - started < 5
    assert output.read_text() == '{"status":"previous"}'
    assert sorted(path.name for path in tmp_path.iterdir()) == ["certificate.json"]


def test_missing_output_cannot_reuse_previous_certificate(
    cli: oac_wire.OACBlackBoxCLI, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    output = tmp_path / "certificate.json"
    output.write_text('{"status":"PASS"}')
    monkeypatch.setattr(cli, "_command", lambda arguments: [sys.executable, "-c", "pass"])

    with pytest.raises(RuntimeError, match=r"^OAC_CLI_OUTPUT_INVALID:verify$"):
        cli.run_to_file(("verify",), output)

    assert output.read_text() == '{"status":"PASS"}'


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group termination")
def test_timeout_terminates_cli_child_process(
    cli: oac_wire.OACBlackBoxCLI, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    marker = tmp_path / "late-result"
    started = tmp_path / "child-started"
    child = (
        f"import time,pathlib; pathlib.Path({str(started)!r}).touch(); "
        f"time.sleep(1); pathlib.Path({str(marker)!r}).touch()"
    )
    script = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)"
    monkeypatch.setattr(oac_wire, "_OAC_COMMAND_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(cli, "_command", lambda arguments: [sys.executable, "-c", script])

    with pytest.raises(RuntimeError, match=r"^OAC_CLI_TIMEOUT:validate$"):
        cli.run("validate")

    assert started.exists()
    time.sleep(1)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX executable fixture")
def test_distribution_probe_has_its_own_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\nexec sleep 60\n")
    interpreter.chmod(0o700)
    monkeypatch.setenv("ORGREBASE_OAC_PYTHON", str(interpreter))
    monkeypatch.setattr(oac_wire, "_OAC_PROBE_TIMEOUT_SECONDS", 0.3)

    with pytest.raises(RuntimeError, match=r"^OAC_DISTRIBUTION_PROBE_TIMEOUT$"):
        oac_wire.OACBlackBoxCLI()
