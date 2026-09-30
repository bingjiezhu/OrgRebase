"""Bounded ZIP transport for published bundles; archive bytes never replace directory identity."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import stat
import tempfile
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import rfc8785

from .bundle import (
    _BOOTSTRAP_MAX_ARTIFACTS,
    _BOOTSTRAP_MAX_BUNDLE_BYTES,
    Bundle,
    BundleError,
    _json_bytes,
    _logical_path,
    load_bundle,
)

ARCHIVE_VERSION = "oac.ctk.archive/v1"
_MAX_ARCHIVE_BYTES = 71_303_168
_SIGNATURE_DOMAIN = b"oac.ctk.archive-signature/v1\0"


def _read_archive(path: Path, *, maximum: int = _MAX_ARCHIVE_BYTES) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise BundleError("ARCHIVE_INPUT_INVALID") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
            raise BundleError("ARCHIVE_INPUT_INVALID")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(maximum + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or any(getattr(before, key) != getattr(after, key) for key in
                ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")):
            raise BundleError("ARCHIVE_INPUT_CHANGED")
        return raw
    finally:
        os.close(descriptor)


def _verify_signature(raw: bytes, signature_path: Path, public_key_path: Path) -> str:
    statement = _json_bytes(_read_archive(signature_path, maximum=16_384), label="archive signature")
    if (set(statement) != {"archiveFormatVersion", "archiveDigest", "bundleDigest", "signature"}
            or statement["archiveFormatVersion"] != ARCHIVE_VERSION
            or statement["archiveDigest"] != "sha256:" + hashlib.sha256(raw).hexdigest()):
        raise BundleError("ARCHIVE_SIGNATURE_BINDING_INVALID")
    signature = statement["signature"]
    if not isinstance(signature, dict) or set(signature) != {"algorithm", "keyId", "value"} or signature["algorithm"] != "Ed25519":
        raise BundleError("ARCHIVE_SIGNATURE_ALGORITHM_INVALID")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as error:
        raise BundleError("ARCHIVE_SIGNATURE_EXTRA_REQUIRED") from error
    try:
        public_bytes = _read_archive(public_key_path, maximum=16_384)
        key = (Ed25519PublicKey.from_public_bytes(public_bytes) if len(public_bytes) == 32
               else serialization.load_pem_public_key(public_bytes))
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("wrong public-key algorithm")
        key_id = "sha256:" + hashlib.sha256(key.public_bytes_raw()).hexdigest()
        if signature["keyId"] != key_id:
            raise ValueError("untrusted key identity")
        value = base64.b64decode(signature["value"], validate=True)
        if len(value) != 64 or base64.b64encode(value).decode("ascii") != signature["value"]:
            raise ValueError("invalid signature length")
        body = {key: value for key, value in statement.items() if key != "signature"}
        key.verify(value, _SIGNATURE_DOMAIN + rfc8785.dumps(body))
    except (InvalidSignature, TypeError, ValueError) as error:
        raise BundleError("ARCHIVE_SIGNATURE_INVALID") from error
    return str(statement["bundleDigest"])


@contextmanager
def materialize_bundle(path: Path, *, signature_path: Path | None = None,
                       public_key_path: Path | None = None) -> Iterator[Bundle]:
    """Stage into a private directory, validate with the single loader, clean on every exit."""
    if (signature_path is None) != (public_key_path is None):
        raise BundleError("ARCHIVE_SIGNATURE_AND_PINNED_KEY_REQUIRED")
    raw = _read_archive(path)
    signed_digest = None
    if signature_path is not None and public_key_path is not None:
        signed_digest = _verify_signature(raw, signature_path, public_key_path)
    with tempfile.TemporaryDirectory(prefix="ctk-archive-") as directory:
        root = Path(directory)
        total = 0
        paths: set[str] = set()
        folded: set[str] = set()
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if not entries or len(entries) > _BOOTSTRAP_MAX_ARTIFACTS + 1:
                    raise BundleError("ARCHIVE_FILE_LIMIT_EXCEEDED")
                for info in entries:
                    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                        raise BundleError("ARCHIVE_COMPRESSION_UNSUPPORTED")
                    logical = _logical_path(info.filename)
                    name = logical.as_posix()
                    mode = info.external_attr >> 16
                    if (name == "." or info.orig_filename != info.filename or info.is_dir() or info.flag_bits & 1
                            or re.match(r"^[A-Za-z]:", name)
                            or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                            or mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)):
                        raise BundleError("ARCHIVE_ENTRY_TYPE_INVALID")
                    if name in paths or name.casefold() in folded:
                        raise BundleError("ARCHIVE_PATH_COLLISION")
                    if any(str(parent).casefold() in folded for parent in logical.parents if str(parent) != ".") or any(existing.startswith(name.casefold() + "/") for existing in folded):
                        raise BundleError("ARCHIVE_PATH_COLLISION")
                    paths.add(name)
                    folded.add(name.casefold())
                    total += info.file_size
                    if total > _BOOTSTRAP_MAX_BUNDLE_BYTES + 1_048_576:
                        raise BundleError("ARCHIVE_EXPANSION_LIMIT_EXCEEDED")
                for info in entries:
                    target = root / info.filename
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with archive.open(info) as source, target.open("xb") as output:
                        os.chmod(target, 0o600)
                        count = 0
                        while chunk := source.read(min(65_536, info.file_size - count + 1)):
                            count += len(chunk)
                            if count > info.file_size:
                                raise BundleError("ARCHIVE_ENTRY_SIZE_MISMATCH")
                            output.write(chunk)
                        if count != info.file_size:
                            raise BundleError("ARCHIVE_ENTRY_SIZE_MISMATCH")
            bundle = load_bundle(root)
            if signed_digest is not None and bundle.digest != signed_digest:
                raise BundleError("ARCHIVE_SIGNED_BUNDLE_MISMATCH")
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, OSError, zlib.error) as error:
            raise BundleError("ARCHIVE_FORMAT_INVALID") from error
        yield bundle
