"""CLI for the OAC CTK black-box runner."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import nullcontext
from pathlib import Path

import rfc8785

from .archive import materialize_bundle
from .bundle import BundleError, load_bundle
from .runner import run_bundle


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oac-ctk")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--archive", action="store_true", help="Treat --bundle as bounded ZIP transport")
    parser.add_argument("--archive-signature", type=Path)
    parser.add_argument("--archive-public-key", type=Path, help="Operator-pinned Ed25519 public key")
    parser.add_argument("--build-input", type=Path, action="append", default=[], help="Explicit SUT build material; required for successor results")
    parser.add_argument(
        "--adapter-command-json",
        required=True,
        help='JSON argv array, for example ["python","-m","adapter"]',
    )
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        command_value = json.loads(args.adapter_command_json)
        if not isinstance(command_value, list) or not command_value or not all(
            isinstance(item, str) and item for item in command_value
        ):
            raise ValueError("adapter command must be a non-empty JSON string array")
        if not args.archive and (args.archive_signature or args.archive_public_key):
            raise ValueError("ARCHIVE_SIGNATURE_REQUIRES_ARCHIVE_MODE")
        source = (materialize_bundle(args.bundle, signature_path=args.archive_signature,
                                     public_key_path=args.archive_public_key) if args.archive
                  else nullcontext(load_bundle(args.bundle)))
        with source as bundle:
            result = run_bundle(bundle, tuple(command_value), build_inputs=tuple(args.build_input))
    except (BundleError, OSError, ValueError) as exc:
        sys.stderr.write(f"CTK harness error: {exc}\n")
        return 2
    payload = rfc8785.dumps(result) + b"\n"
    if args.output is None:
        sys.stdout.buffer.write(payload)
    else:
        args.output.write_bytes(payload)
    return 0 if result["requiredPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
