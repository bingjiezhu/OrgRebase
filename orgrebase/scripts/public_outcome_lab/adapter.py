"""Bounded JSON transport to the real, independently installed retail toolkit."""

from __future__ import annotations

import os
import selectors
import subprocess
import time
from pathlib import Path

from orgrebase.digest import sha256_digest

from .artifacts import PINS, decode, encode, load, sha, verify_source

SANDBOX_POLICY = "(version 1)(allow default)(deny network*)(deny file-write*)"
MAX_RESPONSE = 16 * 1024 * 1024


def verify_worker_identity(runtime: dict) -> None:
    expected = load(PINS / "tau-source.json")["files"]
    tau_files = {k: v for k, v in expected.items() if k.startswith("src/tau2/")}
    if runtime.get("seed_bytes_digest") != "sha256:" + expected["data/tau2/domains/retail/db.json"]["sha256"]:
        raise ValueError("PUBLIC_LAB_OPENED_SEED_BYTES_MISMATCH")
    if runtime["tau_files"] != tau_files:
        raise ValueError("PUBLIC_LAB_INSTALLED_TAU_SOURCE_MISMATCH")
    requirements = dict(
        line.split("==", 1)
        for line in (PINS / "tau-requirements.txt").read_text().splitlines()
        if line and not line.startswith("#")
    )
    if runtime["distributions"] != {**requirements, "tau2": "1.0.1"}:
        raise ValueError("PUBLIC_LAB_TOOLKIT_DEPENDENCY_MISMATCH")
    environment_pin = load(PINS / "tau-environment-macos-arm64-py312.json")
    if {key: runtime.get(key) for key in environment_pin} != environment_pin:
        raise ValueError("PUBLIC_LAB_INSTALLED_DEPENDENCY_BYTES_MISMATCH")


class TauRetailEnvironment:
    def __init__(self, *, source: Path, python: Path, stderr: Path):
        source = source.resolve()
        self.source_identity = verify_source(source)
        if not Path("/usr/bin/sandbox-exec").is_file():
            raise ValueError("PUBLIC_LAB_OS_SANDBOX_UNAVAILABLE")
        self._stderr = stderr.open("xb")
        self._buffer = b""
        worker = Path(__file__).with_name("worker.py")
        self._process = subprocess.Popen(
            [
                "/usr/bin/sandbox-exec",
                "-p",
                SANDBOX_POLICY,
                str(python.absolute()),
                "-I",
                "-S",
                "-B",
                "-u",
                str(worker),
                str(python.absolute().parent.parent / "lib/python3.12/site-packages"),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            cwd=stderr.parent,
            env={
                "PATH": "/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHON_DOTENV_DISABLED": "1",
                "TAU2_DATA_DIR": str(source / "data"),
                "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            },
            close_fds=True,
        )
        try:
            runtime = self._request({"operation": "identity"})
            verify_worker_identity(runtime)
            self.runtime = {
                "source": self.source_identity,
                "runtime": runtime,
                "worker_digest": sha(worker.read_bytes()),
                "sandbox_policy": SANDBOX_POLICY,
                "credential_environment": "EXPLICIT_ALLOWLIST_NO_PROVIDER_CREDENTIALS",
            }
            self.build_digest = sha256_digest(self.runtime)
        except BaseException:
            self.close()
            raise

    def _request(self, body, timeout=30.0):
        raw = encode(body).replace(b"\n", b" ") + b"\n"
        if len(raw) > 65_536 or not 0 < timeout <= 3600:
            raise ValueError("PUBLIC_LAB_TRANSPORT_BOUND_REQUIRED")
        assert self._process.stdin is not None and self._process.stdout is not None
        self._process.stdin.write(raw)
        self._process.stdin.flush()
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self._process.stdout, selectors.EVENT_READ)
            while b"\n" not in self._buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    self.close()
                    raise TimeoutError("PUBLIC_LAB_UPSTREAM_DEADLINE")
                chunk = os.read(self._process.stdout.fileno(), 65_536)
                if not chunk:
                    raise RuntimeError("PUBLIC_LAB_UPSTREAM_EXITED")
                self._buffer += chunk
                if len(self._buffer) > MAX_RESPONSE:
                    self.close()
                    raise ValueError("PUBLIC_LAB_RESPONSE_TOO_LARGE")
        line, self._buffer = self._buffer.split(b"\n", 1)
        response = decode(line)
        if set(response) == {"error", "message"}:
            raise RuntimeError(response["error"] + ":" + response["message"])
        if set(response) != {"result"}:
            raise ValueError("PUBLIC_LAB_WORKER_RESPONSE_INVALID")
        return response["result"]

    def reset(self):
        return self._request({"operation": "reset"})

    def snapshot(self):
        return self._request({"operation": "snapshot"})

    def tool_mutates_state(self, tool):
        result = self._request({"operation": "mutates", "tool": tool})
        if type(result) is not bool:
            raise ValueError("PUBLIC_LAB_MUTATION_DECLARATION_INVALID")
        return result

    def call(self, request, *, timeout):
        return self._request(
            {"operation": "call", "tool": request.tool, "arguments": request.arguments}, timeout
        )

    def close(self):
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        for stream in (self._process.stdin, self._process.stdout, self._stderr):
            if stream is not None and not stream.closed:
                stream.close()
