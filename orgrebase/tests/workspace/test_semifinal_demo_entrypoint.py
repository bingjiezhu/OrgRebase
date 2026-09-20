from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "run-semifinal-demo.sh"
PILOT_ENTRYPOINT = ROOT / "run-enterprise-pilot.sh"


def _install_fake_uv(bin_dir: Path) -> None:
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s\\n' \"${ORGREBASE_OAC_ADAPTATION_MODE:-UNSET}\" "
        '>> \"$ORGREBASE_TEST_CAPTURE\"\n'
        'if [[ -n "${ORGREBASE_TEST_DETAIL_CAPTURE:-}" ]]; then\n'
        '  printf "%s\\n" "$ORGREBASE_OAC_EXECUTION_MODE" "$@" '
        '> "$ORGREBASE_TEST_DETAIL_CAPTURE"\n'
        'fi\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)


def test_semifinal_demo_documents_interactive_default_and_retired_guided() -> None:
    source = ENTRYPOINT.read_text(encoding="utf-8")

    assert ENTRYPOINT.stat().st_mode & os.X_OK
    syntax = subprocess.run(
        ["bash", "-n", str(ENTRYPOINT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr
    help_result = subprocess.run(
        [str(ENTRYPOINT), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0
    assert "interactive|live" in help_result.stdout
    assert "Default mode: interactive" in help_result.stdout
    assert "Both modes require OAC adaptation" in help_result.stdout
    assert "guided mode is retired" in help_result.stdout
    assert source.splitlines()[:3] == [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "umask 077",
    ]


def test_default_and_explicit_modes_dispatch_required_pilot_with_exact_provider(
    tmp_path: Path,
) -> None:
    source = ENTRYPOINT.read_text(encoding="utf-8")
    fake_bin = tmp_path / "bin"
    _install_fake_uv(fake_bin)
    oac_root = tmp_path / "oac-spec"
    (oac_root / "src" / "oac").mkdir(parents=True)
    (oac_root / "pyproject.toml").write_text(
        "[project]\nname = 'test-oac'\n",
        encoding="utf-8",
    )
    capture = tmp_path / "adaptation-modes.txt"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = str(listener.getsockname()[1])

    base_env = os.environ.copy()
    base_env.update(
        {
            "ORGREBASE_NO_OPEN": "1",
            "ORGREBASE_OAC_ROOT": str(oac_root),
            "ORGREBASE_TEST_CAPTURE": str(capture),
            "ORGREBASE_DEMO_PORT": port,
            "PATH": f"{fake_bin}{os.pathsep}{base_env['PATH']}",
        }
    )
    for index, (arguments, caller_override, mode, execution_mode, provider) in enumerate(
        (
            ([], "off", "interactive", "OFFLINE_LOCAL", "ollama-local"),
            (["interactive"], "optional", "interactive", "OFFLINE_LOCAL", "ollama-local"),
            (["live"], "optional", "live", "LIVE_VERTEX", "vertex-ai"),
        )
    ):
        details = tmp_path / f"dispatch-{index}.txt"
        env = base_env | {
            "ORGREBASE_DEMO_WORK_DIR": str(tmp_path / f"state-{index}"),
            "ORGREBASE_OAC_ADAPTATION_MODE": caller_override,
            "ORGREBASE_OAC_EXECUTION_MODE": "FROZEN_REPLAY",
            "ORGREBASE_TEST_DETAIL_CAPTURE": str(details),
        }
        result = subprocess.run(
            [str(ENTRYPOINT), *arguments],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert f"Starting semifinal demo mode: {mode} ({execution_mode})" in result.stdout
        dispatched = details.read_text(encoding="utf-8").splitlines()
        assert dispatched[0] == execution_mode
        assert dispatched[dispatched.index("--competition-model-provider") + 1] == provider
        assert (tmp_path / f"state-{index}").is_dir()
        assert not (tmp_path / f"state-{index}" / "oac-agentic-adaptation").exists()

    assert capture.read_text(encoding="utf-8").splitlines() == ["required"] * 3
    assert source.count("export ORGREBASE_OAC_ADAPTATION_MODE=required") == 1
    assert "ORGREBASE_OAC_ADAPTATION_MODE=optional" not in source


@pytest.mark.parametrize("no_open", ["0", "1"])
def test_occupied_port_preserves_listener_and_refuses_before_pilot_or_browser(
    tmp_path: Path, no_open: str,
) -> None:
    fake_bin = tmp_path / "bin"
    _install_fake_uv(fake_bin)
    capture = tmp_path / "forbidden-calls.txt"
    for command in ("open", "curl"):
        executable = fake_bin / command
        executable.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$0" >> "$ORGREBASE_TEST_CAPTURE"\n')
        executable.chmod(0o755)
    oac_root = tmp_path / "oac-spec"
    (oac_root / "src" / "oac").mkdir(parents=True)
    (oac_root / "pyproject.toml").write_text("[project]\nname = 'test-oac'\n")
    work = tmp_path / "state"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = str(listener.getsockname()[1])
        env = os.environ | {
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "ORGREBASE_OAC_ROOT": str(oac_root),
            "ORGREBASE_NO_OPEN": no_open,
            "ORGREBASE_DEMO_PORT": port,
            "ORGREBASE_DEMO_WORK_DIR": str(work),
            "ORGREBASE_TEST_CAPTURE": str(capture),
        }
        result = subprocess.run([str(ENTRYPOINT)], capture_output=True, text=True, env=env, check=False)
        assert result.returncode == 2, result.stderr
        assert "DEMO_PORT_UNAVAILABLE" in result.stderr
        assert f"127.0.0.1:{port}" in result.stderr
        listener.settimeout(1)
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1) as client:
            connection, _ = listener.accept()
            with connection:
                client.sendall(b"preserved")
                assert connection.recv(9) == b"preserved"
    assert not work.exists()
    assert not capture.exists()


@pytest.mark.parametrize("work_directory", ["unspecified", "new", "existing"])
def test_guided_is_rejected_before_directory_receipt_or_provider_side_effects(
    tmp_path: Path, work_directory: str,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "forbidden-calls.txt"
    for command in ("mkdir", "mktemp", "cp", "uv", "curl", "open"):
        trap = fake_bin / command
        trap.write_text(
            '#!/usr/bin/env bash\n'
            'printf "%s\\n" "$0" >> "$ORGREBASE_TEST_FORBIDDEN_CALLS"\n'
            'exit 91\n',
            encoding="utf-8",
        )
        trap.chmod(0o755)
    work_dir = tmp_path / "state"
    if work_directory == "existing":
        work_dir.mkdir()
        (work_dir / "retained-receipt.json").write_bytes(b'{"retained":true}\n')
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    before_paths = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    env = os.environ.copy()
    env.pop("ORGREBASE_DEMO_WORK_DIR", None)
    env.update(
        {
            "ORGREBASE_OAC_ROOT": str(tmp_path / "absent-oac"),
            "ORGREBASE_TEST_FORBIDDEN_CALLS": str(calls),
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
        }
    )
    if work_directory != "unspecified":
        env["ORGREBASE_DEMO_WORK_DIR"] = str(work_dir)
    result = subprocess.run(
        [str(ENTRYPOINT), "guided"], check=False, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 2
    assert "GUIDED_MODE_RETIRED" in result.stderr
    assert "./run-semifinal-demo.sh interactive" in result.stderr
    assert "Starting" not in result.stdout
    assert not calls.exists()
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before_paths
    assert {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_public_demo_never_depends_on_or_copies_a_frozen_runtime_database() -> None:
    source = ENTRYPOINT.read_text(encoding="utf-8")

    assert "workspace.sqlite3" not in source
    assert "DEMO_FROZEN_ROOT" not in source
    assert "FROZEN_DB" not in source
    assert 'cp -R "$FROZEN_ROOT"' not in source
    assert 'ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED=1' in source
    assert "FROZEN_AGENT_MAPPING" not in source
    assert "FROZEN_REPLAY" not in source
    assert "mapping-receipt.json" not in source


def test_interactive_and_live_reuse_enterprise_pilot_with_truthful_modes() -> None:
    source = ENTRYPOINT.read_text(encoding="utf-8")

    assert 'PILOT_ENTRYPOINT="${SCRIPT_DIR}/run-enterprise-pilot.sh"' in source
    assert 'exec "$PILOT_ENTRYPOINT" "${start_args[@]}"' in source
    assert 'export ORGREBASE_OAC_EXECUTION_MODE=OFFLINE_LOCAL' in source
    assert 'export ORGREBASE_OAC_EXECUTION_MODE=LIVE_VERTEX' in source
    assert 'provider="ollama-local"' in source
    assert 'provider="vertex-ai"' in source
    assert 'start_args+=(--work-dir "$ORGREBASE_DEMO_WORK_DIR")' in source
    assert 'start_args+=(--vertex-project "$ORGREBASE_VERTEX_PROJECT")' in source
    assert "PYTHONPATH=src exec uv run" not in source


def test_enterprise_launcher_defaults_to_required_and_documents_diagnostic_overrides(
    tmp_path: Path,
) -> None:
    source = PILOT_ENTRYPOINT.read_text(encoding="utf-8")

    syntax = subprocess.run(
        ["bash", "-n", str(PILOT_ENTRYPOINT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr
    help_result = subprocess.run(
        [str(PILOT_ENTRYPOINT), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0
    assert "required by default" in help_result.stdout
    assert "optional/off are explicit diagnostic" in help_result.stdout
    assert 'selected_oac_adaptation_mode="${ORGREBASE_OAC_ADAPTATION_MODE:-required}"' in source
    assert "optional|off)" in source
    assert "DIAGNOSTIC OVERRIDE" in source
    assert 'competition_mode="golden"' in source
    assert source.splitlines()[:3] == [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "umask 077",
    ]

    fake_bin = tmp_path / "bin"
    _install_fake_uv(fake_bin)
    capture = tmp_path / "default-mode.txt"
    env = os.environ.copy()
    env.pop("ORGREBASE_OAC_ADAPTATION_MODE", None)
    env.update(
        {
            "ORGREBASE_TEST_CAPTURE": str(capture),
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
        }
    )
    result = subprocess.run(
        [
            str(PILOT_ENTRYPOINT),
            "start",
            "--pack",
            str(ROOT / "examples" / "enterprise-quote-pilot" / "evergreen"),
            "--work-dir",
            str(tmp_path / "state"),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == ["required"]
    assert "OAC adaptation mode: required" in result.stdout


def test_enterprise_launcher_keeps_explicit_optional_and_off_as_diagnostic_overrides(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    _install_fake_uv(fake_bin)
    pack = ROOT / "examples" / "enterprise-quote-pilot" / "evergreen"

    for mode in ("optional", "off"):
        capture = tmp_path / f"{mode}.txt"
        env = os.environ.copy()
        env.update(
            {
                "ORGREBASE_OAC_ADAPTATION_MODE": mode,
                "ORGREBASE_TEST_CAPTURE": str(capture),
                "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            }
        )
        result = subprocess.run(
            [
                str(PILOT_ENTRYPOINT),
                "start",
                "--pack",
                str(pack),
                "--work-dir",
                str(tmp_path / f"state-{mode}"),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert capture.read_text(encoding="utf-8").splitlines() == [mode]
        assert f"OAC adaptation mode is {mode}" in result.stderr

    invalid_env = os.environ.copy()
    invalid_env.update(
        {
            "ORGREBASE_OAC_ADAPTATION_MODE": "sometimes",
            "ORGREBASE_TEST_CAPTURE": str(tmp_path / "invalid.txt"),
            "PATH": f"{fake_bin}{os.pathsep}{invalid_env['PATH']}",
        }
    )
    invalid = subprocess.run(
        [
            str(PILOT_ENTRYPOINT),
            "start",
            "--pack",
            str(pack),
            "--work-dir",
            str(tmp_path / "state-invalid"),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=invalid_env,
    )
    assert invalid.returncode == 2
    assert "must be required, optional, or off" in invalid.stderr
