"""Fetch and verify the pinned BPI Challenge 2019 XES source.

The 728 MB source is never written inside the repository.  Callers must choose
an explicit cache path (``/tmp`` by default); an incomplete or digest-drifted
download is removed before the command returns.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ARTICLE_ID = "12715853"
FILE_ID = "24072995"
DOI = "10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1"
OFFICIAL_RECORD = "https://data.4tu.nl/articles/dataset/BPI_Challenge_2019/12715853"
# Frozen provenance field used by the reproducible dataset manifest.
SOURCE_URL = "https://data.4tu.nl/ndownloader/files/24072995"
DOWNLOAD_URL = "https://ndownloader.figshare.com/files/24072995"
FILE_NAME = "BPI_Challenge_2019.xes"
EXPECTED_SIZE = 728_558_522
EXPECTED_MD5 = "4eb909242351193a61e1c15b9c3cc814"
EXPECTED_SHA256 = "af63bc687fc4152f2123b05c3af7772b37ef3fce2d3f67f812666c9e356baae7"


def _digests(path: Path) -> tuple[str, str]:
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            md5.update(chunk)
            sha256.update(chunk)
    return md5.hexdigest(), sha256.hexdigest()


def verify_source(path: Path) -> dict[str, object]:
    """Fail closed unless ``path`` is the exact official XES artifact."""

    if not path.is_file():
        raise ValueError(f"SOURCE_NOT_FOUND:{path}")
    size = path.stat().st_size
    if size != EXPECTED_SIZE:
        raise ValueError(f"SOURCE_SIZE_MISMATCH:{size}")
    md5, sha256 = _digests(path)
    if md5 != EXPECTED_MD5:
        raise ValueError(f"SOURCE_MD5_MISMATCH:{md5}")
    if sha256 != EXPECTED_SHA256:
        raise ValueError(f"SOURCE_SHA256_MISMATCH:{sha256}")
    return {
        "status": "PASS",
        "article_id": ARTICLE_ID,
        "file_id": FILE_ID,
        "doi": DOI,
        "official_record": OFFICIAL_RECORD,
        "file_name": FILE_NAME,
        "size_bytes": size,
        "md5": f"md5:{md5}",
        "sha256": f"sha256:{sha256}",
        "license": "CC-BY-4.0",
        "raw_bytes_in_repository": False,
    }


def fetch(output: Path) -> dict[str, object]:
    """Download atomically to ``output`` and verify before publication."""

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        return {**verify_source(output), "downloaded": False, "path": str(output)}

    descriptor, temporary_name = tempfile.mkstemp(
        prefix="bpi-challenge-2019.", suffix=".partial", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        request = urllib.request.Request(
            DOWNLOAD_URL,
            headers={"User-Agent": "OrgRebase-Public-Real-Process/0.4"},
        )
        with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as sink:
            while chunk := response.read(4 * 1024 * 1024):
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
        default=Path("/tmp/orgrebase-bpi2019-raw.xes"),
    )
    return parser


def main() -> int:
    try:
        receipt = fetch(_parser().parse_args().output.resolve())
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
