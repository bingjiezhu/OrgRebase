"""Explicit filesystem entry points; every output directory must be new."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from .artifacts import verify_source


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Pinned public retail state experiment, not the complete tau benchmark"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-source")
    check.add_argument("--tau-source", type=Path, required=True)
    prepare_parser = commands.add_parser("prepare")
    inputs = commands.add_parser("inputs")
    run_parser = commands.add_parser("run")
    replay_parser = commands.add_parser("replay")
    for command in (prepare_parser, run_parser, replay_parser):
        command.add_argument("--tau-source", type=Path, required=True)
        command.add_argument("--tau-python", type=Path, required=True)
    for command in (inputs, run_parser, replay_parser):
        command.add_argument("--oac-source", type=Path, required=True)
        command.add_argument("--oac-python", type=Path, required=True)
    for command in (inputs, run_parser):
        command.add_argument("--prepared", type=Path, required=True)
        command.add_argument("--prepared-root", required=True)
    for command in (prepare_parser, inputs, run_parser, replay_parser):
        command.add_argument("--output", type=Path, required=True)
    inputs.add_argument("--created-at", required=True)
    run_parser.add_argument("--inputs", type=Path, required=True)
    run_parser.add_argument("--inputs-root", required=True)
    replay_parser.add_argument("--bundle", type=Path, required=True)
    replay_parser.add_argument("--expected-root", required=True)
    args = parser.parse_args(argv)
    if args.command == "check-source":
        print(json.dumps(verify_source(args.tau_source), indent=2))
        return 0
    if args.command == "inputs":
        result = subprocess.run(
            [
                str(args.oac_python.absolute()),
                "-B",
                "-m",
                "public_outcome_lab.oac_inputs",
                "--prepared",
                str(args.prepared.resolve()),
                "--prepared-root",
                args.prepared_root,
                "--output",
                str(args.output.resolve()),
                "--oac-source",
                str(args.oac_source.resolve()),
                "--created-at",
                args.created_at,
            ],
            env={
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": str(args.oac_source.resolve() / "src")
                + ":"
                + str(Path(__file__).resolve().parents[1]),
            },
            cwd=args.output.parent,
            timeout=120,
            check=False,
        )
        return result.returncode
    from .experiment import prepare, replay, run

    if args.command == "prepare":
        root = prepare(args.tau_source, args.tau_python, args.output)
    elif args.command == "run":
        root = run(
            source=args.tau_source,
            python=args.tau_python,
            prepared=args.prepared,
            prepared_root=args.prepared_root,
            inputs=args.inputs,
            inputs_root=args.inputs_root,
            oac_python=args.oac_python,
            oac_source=args.oac_source,
            output=args.output,
        )
    else:
        root = replay(
            bundle=args.bundle,
            expected_root=args.expected_root,
            source=args.tau_source,
            python=args.tau_python,
            oac_python=args.oac_python,
            oac_source=args.oac_source,
            output=args.output,
        )
    print(json.dumps({"artifact_root": root, "output": str(args.output.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
