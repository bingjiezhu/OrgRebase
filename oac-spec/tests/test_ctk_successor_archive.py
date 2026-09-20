from __future__ import annotations

import base64
import builtins
import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
import rfc8785
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ctk/runner/src"))

from oac_ctk_runner import archive as archive_module  # noqa: E402
from oac_ctk_runner.archive import materialize_bundle  # noqa: E402
from oac_ctk_runner.bundle import BundleError, load_bundle  # noqa: E402

BUNDLES = ("phase-a-v0.1", "foundation-boundaries-v0.2")
SIGNATURE_DOMAIN = b"oac.ctk.archive-signature/v1\0"


def _zip(path: Path, entries: list[tuple[str | zipfile.ZipInfo, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return path


def _pack(source: Path, target: Path, compression: int) -> Path:
    with zipfile.ZipFile(target, "w", compression=compression) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())
    return target


@pytest.fixture(scope="module")
def safe_archives(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, int], Path]:
    directory = tmp_path_factory.mktemp("ctk-safe-archives")
    return {
        (name, method): _pack(
            ROOT / "ctk/bundles" / name,
            directory / f"{name}-{method}.zip",
            method,
        )
        for name in BUNDLES
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
    }


@pytest.fixture
def staged_directories(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    paths = []
    original = archive_module.tempfile.TemporaryDirectory

    def temporary_directory(*args: Any, **kwargs: Any) -> Any:
        directory = original(*args, **kwargs)
        paths.append(Path(directory.name))
        return directory

    monkeypatch.setattr(archive_module.tempfile, "TemporaryDirectory", temporary_directory)
    return paths


@pytest.mark.parametrize("name", BUNDLES)
@pytest.mark.parametrize("method", (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED))
def test_safe_zip_has_exact_directory_identity_and_private_cleanup(
    safe_archives: dict[tuple[str, int], Path],
    name: str,
    method: int,
    staged_directories: list[Path],
) -> None:
    expected = load_bundle(ROOT / "ctk/bundles" / name)
    with materialize_bundle(safe_archives[name, method]) as bundle:
        assert bundle.digest == expected.digest
        assert bundle.manifest == expected.manifest
        assert bundle.cases == expected.cases
        assert bundle.artifact_digests == expected.artifact_digests
        root = bundle.root
        assert root in staged_directories
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert all(
            stat.S_IMODE(path.stat().st_mode) == 0o600 for path in root.rglob("*") if path.is_file()
        )
    assert staged_directories and all(not path.exists() for path in staged_directories)


@pytest.mark.parametrize("error_type", (ValueError, RuntimeError))
def test_context_cleanup_preserves_consumer_exception(
    safe_archives: dict[tuple[str, int], Path],
    error_type: type[Exception],
    staged_directories: list[Path],
) -> None:
    error = error_type("consumer failure after successful bundle admission")
    with (
        pytest.raises(error_type) as caught,
        materialize_bundle(safe_archives[BUNDLES[0], zipfile.ZIP_STORED]),
    ):
        raise error
    assert caught.value is error
    assert staged_directories and all(not path.exists() for path in staged_directories)


def _reject_before_entry_read(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_entry_read(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unsafe archive reached entry decompression")

    monkeypatch.setattr(zipfile.ZipFile, "open", forbidden_entry_read)
    with pytest.raises(BundleError), materialize_bundle(path):
        pytest.fail("unsafe archive was admitted")


@pytest.mark.parametrize(
    "name",
    (
        "../escape",
        "/absolute",
        "a/../../escape",
        "a/./b",
        "a//b",
        "a\\b",
        ".",
        "C:/drive",
        "C:drive",
        "//host/share",
        "a/",
        "a/../b",
    ),
)
def test_unsafe_paths_fail_before_decompression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    staged_directories: list[Path],
) -> None:
    target = _zip(tmp_path / "attack.zip", [(name, b"data")])
    _reject_before_entry_read(target, monkeypatch)
    assert all(not path.exists() for path in staged_directories)
    assert not (tmp_path.parent / "escape").exists()


@pytest.mark.parametrize(
    "names",
    (
        ("same", "same"),
        ("SAME", "same"),
        ("file", "file/child"),
        ("file/child", "file"),
        ("FILE", "file/child"),
        ("file/child", "FILE"),
        ("folder/FILE", "FOLDER/file/child"),
    ),
)
def test_duplicate_and_parent_collisions_fail_before_decompression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    names: tuple[str, str],
) -> None:
    entries = [(name, b"data") for name in names]
    if names[0] == names[1]:
        with pytest.warns(UserWarning, match="Duplicate name"):
            path = _zip(tmp_path / "collision.zip", entries)
    else:
        path = _zip(tmp_path / "collision.zip", entries)
    _reject_before_entry_read(path, monkeypatch)


@pytest.mark.parametrize(
    "mode",
    (
        stat.S_IFLNK | 0o777,
        stat.S_IFIFO | 0o600,
        stat.S_IFSOCK | 0o600,
        stat.S_IFCHR | 0o600,
        stat.S_IFBLK | 0o600,
        stat.S_IFDIR | 0o700,
        stat.S_IFREG | stat.S_ISUID | 0o600,
        stat.S_IFREG | stat.S_ISGID | 0o600,
        stat.S_IFREG | stat.S_ISVTX | 0o600,
    ),
)
def test_special_and_privileged_zip_entries_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
) -> None:
    info = zipfile.ZipInfo("entry")
    info.create_system = 3
    info.external_attr = mode << 16
    _reject_before_entry_read(_zip(tmp_path / "special.zip", [(info, b"target")]), monkeypatch)


def _central_offset(raw: bytes) -> int:
    offset = raw.find(b"PK\x01\x02")
    assert offset >= 0
    return offset


@pytest.mark.parametrize("mutation", ("encrypted", "declared-bomb", "nul-filename"))
def test_zip_header_attacks_are_rejected_before_entry_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    path = _zip(tmp_path / "header.zip", [("entry", b"data")])
    raw = bytearray(path.read_bytes())
    central = _central_offset(raw)
    if mutation == "encrypted":
        struct.pack_into("<H", raw, 6, 1)
        struct.pack_into("<H", raw, central + 8, 1)
    elif mutation == "declared-bomb":
        struct.pack_into("<I", raw, central + 24, 68_157_441)
    else:
        raw[30 + 2] = 0
        raw[central + 46 + 2] = 0
    path.write_bytes(raw)
    _reject_before_entry_read(path, monkeypatch)


def test_declared_file_count_is_bounded_before_decompression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _zip(tmp_path / "many.zip", [(f"file-{index}", b"") for index in range(514)])
    _reject_before_entry_read(path, monkeypatch)


@pytest.mark.parametrize("method", (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA))
def test_unbounded_codec_paths_are_rejected_before_decompression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: int,
) -> None:
    # ZipExtFile's BZIP2/LZMA backends do not honor the caller's per-read
    # output bound internally. Only STORED and DEFLATED are admitted.
    path = tmp_path / "unsupported-codec.zip"
    with zipfile.ZipFile(path, "w", compression=method) as archive:
        archive.writestr("entry", b"x" * 1_048_576)
    _reject_before_entry_read(path, monkeypatch)


@pytest.mark.parametrize(
    "mutation",
    ("nonsense", "truncated", "crc", "compression", "deflate", "bzip2", "lzma"),
)
def test_corrupt_archive_rejects_with_typed_error_and_cleans_staging(
    tmp_path: Path,
    mutation: str,
    staged_directories: list[Path],
) -> None:
    path = _zip(tmp_path / "corrupt.zip", [("entry", b"payload")])
    raw = bytearray(path.read_bytes())
    if mutation == "nonsense":
        raw = bytearray(b"not a zip file")
    elif mutation == "truncated":
        raw = raw[:-12]
    elif mutation == "crc":
        raw[35] ^= 0xFF
    elif mutation == "compression":
        struct.pack_into("<H", raw, 8, 99)
        struct.pack_into("<H", raw, _central_offset(raw) + 10, 99)
    else:
        method = {"deflate": 8, "bzip2": 12, "lzma": 14}[mutation]
        struct.pack_into("<H", raw, 8, method)
        struct.pack_into("<H", raw, _central_offset(raw) + 10, method)
        raw[35:42] = b"\xff" * 7
    path.write_bytes(raw)
    with pytest.raises(BundleError), materialize_bundle(path):
        pytest.fail("corrupt archive was admitted")
    assert staged_directories and all(not directory.exists() for directory in staged_directories)


def test_archive_cannot_bypass_the_published_bundle_identity(
    tmp_path: Path,
    safe_archives: dict[tuple[str, int], Path],
    staged_directories: list[Path],
) -> None:
    source = ROOT / "ctk/bundles" / BUNDLES[0]
    entries = [
        (path.relative_to(source).as_posix(), path.read_bytes())
        for path in sorted(source.rglob("*"))
        if path.is_file()
    ]
    entries.append(("unlisted-public-key.pem", b"not an authority source"))
    with (
        pytest.raises(BundleError, match="file inventory mismatch"),
        materialize_bundle(_zip(tmp_path / "unlisted.zip", entries)),
    ):
        pytest.fail("unlisted content was admitted")
    assert all(not directory.exists() for directory in staged_directories)


def _sign(raw: bytes, bundle_digest: str, private_key: Ed25519PrivateKey) -> dict[str, Any]:
    body = {
        "archiveFormatVersion": "oac.ctk.archive/v1",
        "archiveDigest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "bundleDigest": bundle_digest,
    }
    signature = private_key.sign(SIGNATURE_DOMAIN + rfc8785.dumps(body))
    return {
        **body,
        "signature": {
            "algorithm": "Ed25519",
            "keyId": "sha256:"
            + hashlib.sha256(private_key.public_key().public_bytes_raw()).hexdigest(),
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }


@pytest.fixture
def signed_archive(
    tmp_path: Path,
    safe_archives: dict[tuple[str, int], Path],
) -> tuple[Path, Path, Path, dict[str, Any], Ed25519PrivateKey]:
    archive = safe_archives[BUNDLES[1], zipfile.ZIP_DEFLATED]
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    digest = json.loads((ROOT / "ctk/bundles" / BUNDLES[1] / "bundle.json").read_bytes())[
        "bundleDigest"
    ]
    statement = _sign(archive.read_bytes(), digest, private_key)
    signature_path = tmp_path / "signature.json"
    signature_path.write_text(json.dumps(statement))
    key_path = tmp_path / "pinned-key"
    key_path.write_bytes(private_key.public_key().public_bytes_raw())
    return archive, signature_path, key_path, statement, private_key


@pytest.mark.parametrize("encoding", ("raw", "pem"))
def test_real_ed25519_signature_accepts_only_the_same_published_bundle(
    signed_archive: tuple[Any, ...],
    encoding: str,
    staged_directories: list[Path],
) -> None:
    path, signature, key, statement, private_key = signed_archive
    if encoding == "pem":
        key.write_bytes(
            private_key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
    with materialize_bundle(path, signature_path=signature, public_key_path=key) as bundle:
        assert bundle.digest == statement["bundleDigest"]
    assert staged_directories and all(not directory.exists() for directory in staged_directories)


def test_valid_signature_does_not_publish_a_resealed_directory_bundle(
    tmp_path: Path,
    signed_archive: tuple[Any, ...],
) -> None:
    _, signature_path, key_path, _, private_key = signed_archive
    source = ROOT / "ctk/bundles" / BUNDLES[0]
    manifest = json.loads((source / "bundle.json").read_bytes())
    manifest.pop("bundleDigest")
    manifest["extensions"]["example.unpublishedChange"] = True
    digest = "sha256:" + hashlib.sha256(rfc8785.dumps(manifest)).hexdigest()
    manifest["bundleDigest"] = digest
    entries = [
        (
            path.relative_to(source).as_posix(),
            json.dumps(manifest).encode() if path.name == "bundle.json" else path.read_bytes(),
        )
        for path in sorted(source.rglob("*"))
        if path.is_file()
    ]
    archive = _zip(tmp_path / "signed-unpublished.zip", entries)
    signature_path.write_text(json.dumps(_sign(archive.read_bytes(), digest, private_key)))
    with (
        pytest.raises(BundleError, match=r"published|frozen|trust anchor"),
        materialize_bundle(archive, signature_path=signature_path, public_key_path=key_path),
    ):
        pytest.fail("signing key published an unauthorized suite identity")


@pytest.mark.parametrize(
    "mutation",
    (
        "signature-byte",
        "archive-byte",
        "wrong-pin",
        "bundle-digest",
        "algorithm",
        "wrong-domain",
        "noncanonical-base64",
        "extra-field",
        "short-signature",
    ),
)
def test_signature_mismatches_cannot_downgrade_to_unsigned(
    signed_archive: tuple[Any, ...],
    tmp_path: Path,
    mutation: str,
    staged_directories: list[Path],
) -> None:
    path, signature_path, key_path, statement, private_key = signed_archive
    if mutation == "signature-byte":
        raw_signature = bytearray(base64.b64decode(statement["signature"]["value"]))
        raw_signature[0] ^= 1
        statement["signature"]["value"] = base64.b64encode(raw_signature).decode()
    elif mutation == "archive-byte":
        changed = tmp_path / "changed.zip"
        changed.write_bytes(path.read_bytes() + b" ")
        path = changed
    elif mutation == "wrong-pin":
        key_path.write_bytes(
            Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
            .public_key()
            .public_bytes_raw()
        )
    elif mutation == "bundle-digest":
        statement = _sign(path.read_bytes(), "sha256:" + "0" * 64, private_key)
    elif mutation == "algorithm":
        statement["signature"]["algorithm"] = "none"
    elif mutation == "wrong-domain":
        body = {key: value for key, value in statement.items() if key != "signature"}
        statement["signature"]["value"] = base64.b64encode(
            private_key.sign(rfc8785.dumps(body))
        ).decode()
    elif mutation == "noncanonical-base64":
        value = statement["signature"]["value"]
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        altered = value[:-3] + alphabet[alphabet.index(value[-3]) + 1] + "=="
        assert base64.b64decode(altered, validate=True) == base64.b64decode(value, validate=True)
        assert altered != value
        statement["signature"]["value"] = altered
    elif mutation == "extra-field":
        statement["allowUnsigned"] = True
    else:
        statement["signature"]["value"] = base64.b64encode(b"x" * 63).decode()
    signature_path.write_text(json.dumps(statement))
    with (
        pytest.raises(BundleError),
        materialize_bundle(path, signature_path=signature_path, public_key_path=key_path),
    ):
        pytest.fail("invalid signature was accepted")
    assert all(not directory.exists() for directory in staged_directories)


@pytest.mark.parametrize("missing", ("signature", "key"))
def test_signature_requires_an_independently_pinned_key_pair(
    signed_archive: tuple[Any, ...],
    missing: str,
) -> None:
    path, signature, key, _, _ = signed_archive
    with (
        pytest.raises(BundleError, match="ARCHIVE_SIGNATURE_AND_PINNED_KEY_REQUIRED"),
        materialize_bundle(
            path,
            signature_path=None if missing == "signature" else signature,
            public_key_path=None if missing == "key" else key,
        ),
    ):
        pytest.fail("incomplete signature/key pair was admitted")


def test_missing_signature_extra_explicitly_rejects_signed_input(
    signed_archive: tuple[Any, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, signature, key, _, _ = signed_archive
    original = builtins.__import__

    def without_cryptography(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "cryptography" or name.startswith("cryptography."):
            raise ImportError("test environment without signatures extra")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_cryptography)
    with (
        pytest.raises(BundleError, match="ARCHIVE_SIGNATURE_EXTRA_REQUIRED"),
        materialize_bundle(path, signature_path=signature, public_key_path=key),
    ):
        pytest.fail("missing crypto dependency downgraded signature verification")


@pytest.mark.parametrize("field", ("archive", "signature", "key"))
@pytest.mark.parametrize("mutation", ("symlink", "hardlink", "oversized"))
def test_archive_signature_and_pin_are_bounded_regular_files(
    signed_archive: tuple[Any, ...],
    tmp_path: Path,
    field: str,
    mutation: str,
) -> None:
    path, signature, key, _, _ = signed_archive
    selected = {"archive": path, "signature": signature, "key": key}
    source = tmp_path / "input-copy"
    source.write_bytes(selected[field].read_bytes())
    attack = tmp_path / "input-attack"
    if mutation == "symlink":
        attack.symlink_to(source)
    elif mutation == "hardlink":
        os.link(source, attack)
    else:
        with attack.open("wb") as stream:
            stream.truncate(71_303_169 if field == "archive" else 16_385)
    selected[field] = attack
    with (
        pytest.raises(BundleError),
        materialize_bundle(
            selected["archive"],
            signature_path=selected["signature"],
            public_key_path=selected["key"],
        ),
    ):
        pytest.fail(f"unsafe {field} input was admitted")


@pytest.mark.skipif(os.name != "posix", reason="FIFO boundary requires POSIX")
@pytest.mark.parametrize("field", ("archive", "signature", "key"))
def test_local_transport_inputs_reject_fifo_without_blocking(
    signed_archive: tuple[Any, ...],
    tmp_path: Path,
    field: str,
) -> None:
    path, signature, key, _, _ = signed_archive
    fifo = tmp_path / "blocked.fifo"
    os.mkfifo(fifo, 0o600)
    selected = {"archive": path, "signature": signature, "key": key}
    selected[field] = fifo
    code = (
        "import sys;from pathlib import Path;"
        f"sys.path.insert(0,{str(ROOT / 'ctk/runner/src')!r});"
        "from oac_ctk_runner.archive import materialize_bundle;"
        "from oac_ctk_runner.bundle import BundleError;\n"
        "try:\n"
        f" with materialize_bundle(Path({str(selected['archive'])!r}),signature_path=Path({str(selected['signature'])!r}),public_key_path=Path({str(selected['key'])!r})):\n"
        "  raise AssertionError('FIFO input admitted')\n"
        "except BundleError:\n print('REJECTED')\n"
    )
    try:
        result = subprocess.run(
            (sys.executable, "-c", code), capture_output=True, timeout=3, check=False
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{field} FIFO blocked before regular-file admission")
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.strip() == b"REJECTED"
