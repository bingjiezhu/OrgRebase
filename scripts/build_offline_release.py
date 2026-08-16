#!/usr/bin/env python3
"""Build a reproducible pure-Python wheel and source archive without build backends.

This is a release fallback for air-gapped review environments where Hatchling
cannot be downloaded. The canonical build remains ``uv build``; this script
mirrors the runtime asset mappings declared in ``pyproject.toml`` and produces
standards-compliant wheel metadata and RECORD hashes.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import tarfile
import tomllib
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXED_ZIP_TIME = (2026, 8, 16, 0, 0, 0)
FIXED_TAR_MTIME = 1_786_838_400  # 2026-08-16T00:00:00Z


@dataclass(frozen=True)
class BuildConfig:
    name: str
    version: str
    description: str
    requires_python: str
    dependencies: tuple[str, ...]
    optional_dependencies: tuple[tuple[str, tuple[str, ...]], ...]
    license_text: str
    license_files: tuple[str, ...]
    authors: tuple[str, ...]

    @property
    def normalized_name(self) -> str:
        return self.name.replace("-", "_")

    @property
    def dist_info(self) -> str:
        return f"{self.normalized_name}-{self.version}.dist-info"


def load_config() -> BuildConfig:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = payload["project"]
    license_value = project.get("license", "")
    if isinstance(license_value, dict):
        license_text = str(license_value.get("text", ""))
    else:
        license_text = str(license_value)
    return BuildConfig(
        name=str(project["name"]),
        version=str(project["version"]),
        description=str(project["description"]),
        requires_python=str(project["requires-python"]),
        dependencies=tuple(str(item) for item in project.get("dependencies", ())),
        optional_dependencies=tuple(
            (str(extra), tuple(str(item) for item in dependencies))
            for extra, dependencies in sorted(project.get("optional-dependencies", {}).items())
        ),
        license_text=license_text,
        license_files=tuple(str(item) for item in project.get("license-files", ())),
        authors=tuple(str(item["name"]) for item in project.get("authors", ()) if item.get("name")),
    )


def _iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        yield path


def wheel_payloads() -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}

    package_root = ROOT / "src" / "orgrebase"
    for path in _iter_files(package_root):
        destination = Path("orgrebase") / path.relative_to(package_root)
        payloads[destination.as_posix()] = path.read_bytes()

    directory_mappings = (
        (ROOT / "demo" / "console", Path("orgrebase/static")),
        (ROOT / "agentteams" / "identities", Path("orgrebase/_assets/agentteams/identities")),
        (ROOT / "benchmark" / "orgworkbench", Path("orgrebase/_assets/benchmark/orgworkbench")),
    )
    for source_root, destination_root in directory_mappings:
        for path in _iter_files(source_root):
            destination = destination_root / path.relative_to(source_root)
            payloads[destination.as_posix()] = path.read_bytes()

    file_mappings = (
        (
            ROOT / "skills" / "enterprise-launch-readiness" / "contract.json",
            Path("orgrebase/skill_contracts/enterprise-launch-readiness.json"),
        ),
        (
            ROOT / "fixtures" / "canonical-enterprise.json",
            Path("orgrebase/_assets/fixtures/canonical-enterprise.json"),
        ),
        (
            ROOT / "orchestration" / "task-intents.json",
            Path("orgrebase/_assets/orchestration/task-intents.json"),
        ),
        (
            ROOT / "configs" / "workspace" / "metric-registry.json",
            Path("orgrebase/_assets/configs/workspace/metric-registry.json"),
        ),
        (
            ROOT / "evidence" / "release-facts.json",
            Path("orgrebase/_assets/evidence/release-facts.json"),
        ),
    )
    for source, destination in file_mappings:
        payloads[destination.as_posix()] = source.read_bytes()

    return payloads


def _metadata(config: BuildConfig) -> bytes:
    lines = [
        "Metadata-Version: 2.4",
        f"Name: {config.name}",
        f"Version: {config.version}",
        f"Summary: {config.description}",
        f"Requires-Python: {config.requires_python}",
        f"License: {config.license_text}",
    ]
    lines.extend(f"Author: {author}" for author in config.authors)
    lines.extend(f"License-File: {path}" for path in config.license_files)
    lines.extend(f"Requires-Dist: {dependency}" for dependency in config.dependencies)
    for extra, dependencies in config.optional_dependencies:
        lines.append(f"Provides-Extra: {extra}")
        lines.extend(f"Requires-Dist: {dependency}; extra == '{extra}'" for dependency in dependencies)
    lines.extend(
        [
            "Description-Content-Type: text/markdown",
            "",
            (ROOT / "README.md").read_text(encoding="utf-8"),
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"sha256={encoded}"


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    return info


def build_wheel(config: BuildConfig, dist: Path) -> Path:
    payloads = wheel_payloads()
    dist_info = config.dist_info
    for relative in config.license_files:
        source = ROOT / relative
        payloads[f"{dist_info}/licenses/{relative}"] = source.read_bytes()
    payloads[f"{dist_info}/METADATA"] = _metadata(config)
    payloads[f"{dist_info}/WHEEL"] = (
        b"Wheel-Version: 1.0\n"
        b"Generator: orgrebase-offline-release-builder\n"
        b"Root-Is-Purelib: true\n"
        b"Tag: py3-none-any\n"
    )
    payloads[f"{dist_info}/entry_points.txt"] = (
        b"[console_scripts]\norgrebase = orgrebase.cli:main\n"
    )
    payloads[f"{dist_info}/top_level.txt"] = b"orgrebase\n"

    record_path = f"{dist_info}/RECORD"
    record_buffer = io.StringIO(newline="")
    writer = csv.writer(record_buffer, lineterminator="\n")
    for name in sorted(payloads):
        data = payloads[name]
        writer.writerow((name, _record_hash(data), str(len(data))))
    writer.writerow((record_path, "", ""))
    payloads[record_path] = record_buffer.getvalue().encode("utf-8")

    output = dist / f"{config.normalized_name}-{config.version}-py3-none-any.whl"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(payloads):
            archive.writestr(_zip_info(name), payloads[name])
    return output


SDIST_TOP_LEVEL = (
    "agentteams",
    "benchmark",
    "configs",
    "contracts",
    "demo/console",
    "docs",
    "evidence",
    "examples",
    "fixtures",
    "orchestration",
    "schemas",
    "scripts",
    "skills",
    "src",
    "tests",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "COMMERCIAL-LICENSE.md",
    "LICENSE",
    "Makefile",
    "NOTICE.md",
    "README.md",
    "README.zh-CN.md",
    "RELEASE-VERIFICATION.md",
    "SECURITY.md",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "uv.lock",
)

SDIST_EXCLUDED_PATHS = (
    Path("evidence/agentteams/live-sources"),
    Path("evidence/agentteams/nonce-ledger.json"),
    Path("evidence/agentteams/.nonce-ledger.json.lock"),
    Path("evidence/latest/git-tool-repo"),
)


def _is_sdist_excluded(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return any(relative == prefix or prefix in relative.parents for prefix in SDIST_EXCLUDED_PATHS)


def _sdist_files() -> list[Path]:
    files: list[Path] = []
    for relative in SDIST_TOP_LEVEL:
        source = ROOT / relative
        if not source.exists():
            continue
        if source.is_file():
            files.append(source)
            continue
        for path in _iter_files(source):
            if _is_sdist_excluded(path):
                continue
            ignored_directories = {".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv", ".tmp"}
            if any(part in ignored_directories for part in path.parts):
                continue
            if path.name in {".coverage"} or path.suffix in {".db", ".sqlite", ".sqlite3"}:
                continue
            files.append(path)
    return sorted(set(files), key=lambda item: item.relative_to(ROOT).as_posix())


def build_sdist(config: BuildConfig, dist: Path) -> Path:
    output = dist / f"{config.normalized_name}-{config.version}.tar.gz"
    prefix = Path(f"{config.normalized_name}-{config.version}")
    with tarfile.open(output, "w:gz", compresslevel=9) as archive:
        for path in _sdist_files():
            relative = path.relative_to(ROOT)
            info = archive.gettarinfo(str(path), arcname=(prefix / relative).as_posix())
            info.mtime = FIXED_TAR_MTIME
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            with path.open("rb") as handle:
                archive.addfile(info, handle)
    return output


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--wheel-only", action="store_true")
    parser.add_argument("--sdist-only", action="store_true")
    args = parser.parse_args()
    if args.wheel_only and args.sdist_only:
        raise SystemExit("choose at most one of --wheel-only/--sdist-only")

    config = load_config()
    dist = args.dist.resolve()
    dist.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    if not args.sdist_only:
        outputs.append(build_wheel(config, dist))
    if not args.wheel_only:
        outputs.append(build_sdist(config, dist))

    for path in outputs:
        print(f"{sha256_file(path)}  {path}")


if __name__ == "__main__":
    main()
