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
import fnmatch
import gzip
import hashlib
import io
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

if __package__:
    from scripts import build_source_snapshot as source_snapshot
else:  # Support ``python scripts/build_offline_release.py``.
    import build_source_snapshot as source_snapshot

ROOT = Path(__file__).resolve().parents[1]
FIXED_ZIP_TIME = (2026, 8, 16, 0, 0, 0)
FIXED_TAR_MTIME = 1_786_838_400  # 2026-08-16T00:00:00Z
DATABASE_FILE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
TRANSIENT_DATABASE_SUFFIXES = tuple(
    suffix
    for suffix in source_snapshot.DATABASE_SUFFIXES
    if suffix not in DATABASE_FILE_SUFFIXES
)


class ReleaseInputError(RuntimeError):
    """A release input violates the publishable-source boundary."""


def _release_relative(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError as exc:
        raise ReleaseInputError(f"RELEASE_INPUT_OUTSIDE_ROOT:{path}") from exc


def _validate_release_filename(relative: Path) -> None:
    name = relative.name.lower()
    environment_file = name == ".env" or name.startswith(".env.") or name.endswith(".env")
    credential_json = name.endswith(".json") and (
        "credential" in name or "service-account" in name
    )
    if environment_file or credential_json or name.endswith((".key", ".log", ".p12", ".pem", ".pfx")):
        raise ReleaseInputError(f"SENSITIVE_RELEASE_FILE_REJECTED:{relative.as_posix()}")


def _read_release_input(path: Path) -> bytes:
    """Read one publishable file and fail closed before its bytes reach an archive."""

    relative = _release_relative(path)
    if path.is_symlink():
        raise ReleaseInputError(f"SYMLINK_RELEASE_INPUT_REJECTED:{relative.as_posix()}")
    if not path.is_file():
        raise ReleaseInputError(f"NON_REGULAR_RELEASE_INPUT_REJECTED:{relative.as_posix()}")

    pure_relative = PurePosixPath(relative.as_posix())
    try:
        source_snapshot._validate_relative_path(pure_relative)
        source_snapshot._validate_noncredential_path(pure_relative, "orgrebase")
    except source_snapshot.SnapshotError as exc:
        raise ReleaseInputError(str(exc)) from exc
    _validate_release_filename(relative)

    raw = path.read_bytes()
    archive_relative = f"orgrebase/{relative.as_posix()}"
    credential_violations, _ = source_snapshot._credential_scan(archive_relative, raw)
    machine_path_violations, _, _ = source_snapshot._machine_local_path_scan(
        archive_relative,
        raw,
    )
    violations = (*credential_violations, *machine_path_violations)
    if violations:
        raise ReleaseInputError(";".join(violations))
    return raw


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
    payload = tomllib.loads(_read_release_input(ROOT / "pyproject.toml").decode("utf-8"))
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


def wheel_asset_mappings() -> dict[Path, Path]:
    """Read the same runtime asset declarations used by the canonical Hatch build."""

    payload = tomllib.loads(_read_release_input(ROOT / "pyproject.toml").decode("utf-8"))
    configured = payload["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    mappings: dict[Path, Path] = {}
    for source_name, destination_name in configured.items():
        source = PurePosixPath(source_name)
        destination = PurePosixPath(destination_name)
        try:
            source_snapshot._validate_relative_path(source)
            source_snapshot._validate_relative_path(destination)
        except source_snapshot.SnapshotError as exc:
            raise ReleaseInputError(str(exc)) from exc
        if destination.parts[0] != "orgrebase":
            raise ReleaseInputError(f"WHEEL_ASSET_OUTSIDE_PACKAGE:{destination}")
        source_path = ROOT / source
        if source_path.is_symlink() or not source_path.resolve().is_relative_to(ROOT.resolve()):
            raise ReleaseInputError(f"SYMLINK_RELEASE_INPUT_REJECTED:{source}")
        if not source_path.exists():
            raise ReleaseInputError(f"MISSING_WHEEL_ASSET:{source}")
        mappings[source_path] = Path(destination)
    return mappings


def wheel_payloads() -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}

    package_root = ROOT / "src" / "orgrebase"
    for path in _iter_files(package_root):
        destination = Path("orgrebase") / path.relative_to(package_root)
        payloads[destination.as_posix()] = _read_release_input(path)

    for source, destination in wheel_asset_mappings().items():
        if source.is_dir():
            for path in _iter_files(source):
                target = destination / path.relative_to(source)
                payloads[target.as_posix()] = _read_release_input(path)
        else:
            payloads[destination.as_posix()] = _read_release_input(source)

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
            _read_release_input(ROOT / "README.md").decode("utf-8"),
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
        payloads[f"{dist_info}/licenses/{relative}"] = _read_release_input(source)
    payloads[f"{dist_info}/METADATA"] = _metadata(config)
    payloads[f"{dist_info}/WHEEL"] = (
        b"Wheel-Version: 1.0\n"
        b"Generator: orgrebase-offline-release-builder\n"
        b"Root-Is-Purelib: true\n"
        b"Tag: py3-none-any\n"
    )
    payloads[f"{dist_info}/entry_points.txt"] = b"[console_scripts]\norgrebase = orgrebase.cli:main\n"
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
    ".python-version",
    ".github/pull_request_template.md",
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
    "vendor",
    ".gitignore",
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
    "run-agentteams-demo.sh",
    "run-enterprise-pilot.sh",
    "run-semifinal-demo.sh",
    "SECURITY.md",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "uv.lock",
    "verify-agentteams-demo.sh",
)

PUBLISHED_SDIST_DATABASES = frozenset(
    {
        "evidence/semifinal-closure/latest/operations/observability/telemetry.sqlite",
        "evidence/semifinal-closure/latest/operations/operations/canonical-state.sqlite",
        "evidence/semifinal-closure/latest/operations/operations/canonical-state.backup.sqlite",
        "evidence/semifinal-closure/latest/operations/operations/canonical-state.restored.sqlite",
        "evidence/semifinal-governed/latest/runtime/runtime-journal.sqlite",
        "evidence/semifinal-governed/latest/runtime/workspace.sqlite",
        "evidence/enterprise-quote-pilot/latest/run/workspace.sqlite3",
        "evidence/enterprise-quote-pilot/latest/run/evidence/workspace.backup.sqlite3",
        "evidence/enterprise-quote-pilot/latest/run/evidence/workspace.restored.sqlite3",
    }
)


def _sdist_exclude_patterns() -> tuple[str, ...]:
    """Use the canonical Hatch sdist denylist for the offline fallback too."""

    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    configured = payload["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
    if not isinstance(configured, list) or not all(
        isinstance(pattern, str) and pattern for pattern in configured
    ):
        raise ValueError("invalid sdist exclude configuration")
    return tuple(configured)


def _is_sdist_excluded(path: Path, patterns: tuple[str, ...]) -> bool:
    relative = path.relative_to(ROOT)
    candidates = (relative, *relative.parents[:-1])
    return any(
        fnmatch.fnmatchcase(candidate.as_posix(), pattern.lstrip("/"))
        for pattern in patterns
        for candidate in candidates
    )


def _sdist_files() -> list[Path]:
    files: list[Path] = []
    exclude_patterns = _sdist_exclude_patterns()
    for relative in SDIST_TOP_LEVEL:
        source = ROOT / relative
        if not source.exists():
            continue
        if source.is_file():
            if _is_sdist_excluded(source, exclude_patterns):
                continue
            files.append(source)
            continue
        for path in _iter_files(source):
            if _is_sdist_excluded(path, exclude_patterns):
                continue
            ignored_directories = {".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv", ".tmp"}
            if any(part in ignored_directories for part in path.parts):
                continue
            relative_path = path.relative_to(ROOT).as_posix()
            lower_name = path.name.lower()
            if (
                path.name == ".coverage"
                or lower_name.endswith(TRANSIENT_DATABASE_SUFFIXES)
                or (
                    lower_name.endswith(DATABASE_FILE_SUFFIXES)
                    and relative_path not in PUBLISHED_SDIST_DATABASES
                )
            ):
                continue
            files.append(path)
    return sorted(set(files), key=lambda item: item.relative_to(ROOT).as_posix())


def build_sdist(config: BuildConfig, dist: Path) -> Path:
    output = dist / f"{config.normalized_name}-{config.version}.tar.gz"
    prefix = Path(f"{config.normalized_name}-{config.version}")
    staged_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=dist,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as staged:
            staged_path = Path(staged.name)
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=staged,
                mtime=FIXED_TAR_MTIME,
            ) as compressed, tarfile.open(fileobj=compressed, mode="w") as archive:
                for path in _sdist_files():
                    relative = path.relative_to(ROOT)
                    raw = _read_release_input(path)
                    info = archive.gettarinfo(
                        str(path),
                        arcname=(prefix / relative).as_posix(),
                    )
                    info.size = len(raw)
                    info.mtime = FIXED_TAR_MTIME
                    info.uid = 0
                    info.gid = 0
                    info.uname = "root"
                    info.gname = "root"
                    archive.addfile(info, io.BytesIO(raw))
                package_metadata = _metadata(config)
                metadata_info = tarfile.TarInfo((prefix / "PKG-INFO").as_posix())
                metadata_info.size = len(package_metadata)
                metadata_info.mode = 0o644
                metadata_info.mtime = FIXED_TAR_MTIME
                metadata_info.uid = 0
                metadata_info.gid = 0
                metadata_info.uname = "root"
                metadata_info.gname = "root"
                archive.addfile(metadata_info, io.BytesIO(package_metadata))
        staged_path.replace(output)
    finally:
        if staged_path is not None:
            staged_path.unlink(missing_ok=True)
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
