"""Run only inside the independent tau environment and OS sandbox."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import uuid
from pathlib import Path

# -S prevents .pth and sitecustomize execution. Append only the explicitly supplied
# toolkit directory; do not invoke site.addsitedir or inherit environment paths.
if not sys.flags.isolated or not sys.flags.no_site or len(sys.argv) != 2:
    raise ValueError("PUBLIC_LAB_ISOLATED_TOOLKIT_PATH_REQUIRED")
_site = Path(sys.argv[1])
if not _site.is_absolute() or not _site.is_dir():
    raise ValueError("PUBLIC_LAB_TOOLKIT_PATH_INVALID")
sys.path.append(str(_site))

from tau2.domains.retail.data_model import RetailDB  # noqa: E402
from tau2.domains.retail.tools import RetailTools  # noqa: E402

TOOLS = frozenset(
    {"find_user_id_by_name_zip", "get_user_details", "get_order_details", "cancel_pending_order"}
)
MAX_REQUEST = 65_536


def identity() -> dict:
    import tau2

    package = Path(tau2.__file__).parent
    sources = {
        "src/tau2/" + p.relative_to(package).as_posix(): {
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "bytes": p.stat().st_size,
        }
        for p in package.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    versions = {}
    dependency_files = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"].lower().replace("_", "-")
        versions[name] = dist.version
        members = []
        for entry in dist.files or ():
            if (
                ".." in entry.parts
                or entry.suffix == ".pyc"
                or entry.name in {"RECORD", "INSTALLER", "REQUESTED", "direct_url.json"}
            ):
                continue
            path = Path(dist.locate_file(entry))
            if path.is_file():
                members.append((entry.as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
        raw = json.dumps(sorted(members), separators=(",", ":"), ensure_ascii=False).encode()
        dependency_files[name] = {
            "version": dist.version,
            "files": len(members),
            "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        }
    return {
        "python": platform.python_version(),
        "platform": sys.platform,
        "machine": platform.machine(),
        "python_binary_digest": "sha256:" + hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
        "distributions": versions,
        "dependency_files": dependency_files,
        "tau_files": sources,
    }


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError("PUBLIC_LAB_DUPLICATE_COMMAND_KEY")
        value[key] = item
    return value


def _invalid_constant(value):
    raise ValueError("PUBLIC_LAB_NONFINITE_COMMAND:" + value)


def main() -> None:
    seed = Path(os.environ["TAU2_DATA_DIR"]) / "tau2/domains/retail/db.json"
    raw_seed = seed.read_bytes()
    tools = None
    while True:
        line = sys.stdin.buffer.readline(MAX_REQUEST + 1)
        if not line:
            break
        if len(line) > MAX_REQUEST or not line.endswith(b"\n"):
            raise ValueError("PUBLIC_LAB_WORKER_REQUEST_LIMIT")
        try:
            command = json.loads(line, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
            op = command["operation"]
            if op == "identity" and set(command) == {"operation"}:
                result = identity()
                result["seed_bytes_digest"] = "sha256:" + hashlib.sha256(raw_seed).hexdigest()
            elif op == "reset" and set(command) == {"operation"}:
                tools = RetailTools(RetailDB.model_validate_json(raw_seed))
                result = str(uuid.uuid4())
            elif op == "snapshot" and set(command) == {"operation"} and tools is not None:
                result = tools.db.model_dump(mode="json")
            elif op in {"mutates", "call"} and tools is not None:
                expected = {"operation", "tool"} | ({"arguments"} if op == "call" else set())
                if set(command) != expected or command["tool"] not in TOOLS:
                    raise ValueError("PUBLIC_LAB_WORKER_TOOL_DENIED")
                result = (
                    tools.tool_mutates_state(command["tool"])
                    if op == "mutates"
                    else tools.use_tool(command["tool"], **command["arguments"])
                )
                if hasattr(result, "model_dump"):
                    result = result.model_dump(mode="json")
            else:
                raise ValueError("PUBLIC_LAB_WORKER_COMMAND_DENIED")
            print(json.dumps({"result": result}, allow_nan=False), flush=True)
        except Exception as exc:
            print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), flush=True)


if __name__ == "__main__":
    main()
