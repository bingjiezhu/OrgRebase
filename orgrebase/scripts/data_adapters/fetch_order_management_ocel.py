"""Fetch the exact OCEL 2.0 Order Management SQLite artifact.

The repository intentionally does not redistribute the 9.6 MB upstream file.
This command writes only to an explicit cache path (``/tmp`` by default),
verifies both the Zenodo MD5 and the independently pinned SHA-256, and emits a
small machine-readable fetch receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path

ZENODO_RECORD_ID = "8337464"
ZENODO_DOI = "10.5281/zenodo.8337464"
SOURCE_URL = "https://zenodo.org/api/records/8337464/files/order-management.sqlite/content"
EXPECTED_SIZE = 9_592_832
EXPECTED_MD5 = "5a1f2c98beabe0f647cb0b0b83f07844"
EXPECTED_SHA256 = "a71ee17ea394fad6fa6ac3c5e661c309cd8ca362cde838bf38fee62496bf6f40"


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_source(path: Path) -> dict[str, object]:
    """Fail closed unless ``path`` is the pinned official SQLite file."""

    if not path.is_file():
        raise ValueError(f"SOURCE_NOT_FOUND:{path}")
    size = path.stat().st_size
    md5 = _digest(path, "md5")
    sha256 = _digest(path, "sha256")
    if size != EXPECTED_SIZE:
        raise ValueError(f"SOURCE_SIZE_MISMATCH:{size}")
    if md5 != EXPECTED_MD5:
        raise ValueError(f"SOURCE_MD5_MISMATCH:{md5}")
    if sha256 != EXPECTED_SHA256:
        raise ValueError(f"SOURCE_SHA256_MISMATCH:{sha256}")
    return {
        "status": "PASS",
        "record_id": ZENODO_RECORD_ID,
        "doi": ZENODO_DOI,
        "file_name": "order-management.sqlite",
        "size_bytes": size,
        "md5": f"md5:{md5}",
        "sha256": f"sha256:{sha256}",
    }


def fetch(output: Path) -> dict[str, object]:
    """Download atomically and retain no unverified partial file."""

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        return {**verify_source(output), "downloaded": False, "path": str(output)}

    descriptor, temporary_name = tempfile.mkstemp(
        prefix="order-management.", suffix=".partial", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        request = urllib.request.Request(
            SOURCE_URL,
            headers={"User-Agent": "OrgRebase-Public-Data-Bridge/0.3"},
        )
        with urllib.request.urlopen(request, timeout=90) as response, temporary.open("wb") as sink:
            while chunk := response.read(1024 * 1024):
                sink.write(chunk)
        receipt = verify_source(temporary)
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {**receipt, "downloaded": True, "path": str(output)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(tempfile.gettempdir()) / "orgrebase-public-data-cache" / "order-management.sqlite",
    )
    return parser


def main() -> int:
    try:
        receipt = fetch(_parser().parse_args().output.resolve())
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
