#!/usr/bin/env python3
"""Build, verify, and retain the Spec 045 same-run semifinal proof pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orgrebase.agentteams_source import default_agentteams_checkout
from orgrebase.digest import sha256_digest
from orgrebase.workspace.coalition import build_coalition_result_binding
from orgrebase.workspace.controlled_local import (
    ControlledEnterpriseClient,
    ControlledEnterpriseServer,
)
from orgrebase.workspace.controlled_local_evidence import (
    run_controlled_local_evidence,
)
from orgrebase.workspace.native_taskflow import run_native_taskflow_slice

try:
    from scripts.run_skill_package_lifecycle import run_skill_lifecycle_evidence
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from run_skill_package_lifecycle import run_skill_lifecycle_evidence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ID = "run:orgrebase:semifinal-closure:quote-001"
DEFAULT_OUTPUT_ROOT = ROOT / "evidence/semifinal-closure"
COMPLETION_MATRIX = {
    "enterprise_quote_operating_model": "VALIDATED_SYNTHETIC_AND_MODELLED",
    "enterprise_shadow_observation_contract": "VALIDATED_SYNTHETIC_CONTRACT",
    "agentteams_native_taskflow": "VALIDATED_CONTROLLED_LOCAL",
    "four_domain_coalition_binding": "VALIDATED_CONTROLLED_LOCAL",
    "skill_package_lifecycle": "VALIDATED_RUN_LOCAL",
    "source_tool_otlp_operations": "VALIDATED_CONTROLLED_LOCAL",
    "workspace_approval_apply_same_run": "NOT_RUN",
    "live_distributed_agentteams": "NOT_RUN",
    "real_enterprise_connectors": "NOT_RUN",
    "real_enterprise_value": "NOT_RUN",
    "production_ha_dr_sla": "NOT_RUN",
}
NORMALIZED_VERIFIER_KEYS = {
    "status",
    "run_id",
    "receipt_digest",
    "quote_invocation_receipt",
    "pack_digest",
    "evidence_class",
    "claim_ceiling",
}


class SemifinalClosureError(RuntimeError):
    """Fail-closed integrated-run error."""


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SemifinalClosureError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _record(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "digest": sha256_digest(value)}


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    source_path = str(ROOT / "src")
    environment["PYTHONPATH"] = (
        source_path
        if not environment.get("PYTHONPATH")
        else source_path + os.pathsep + environment["PYTHONPATH"]
    )
    return environment


def _run_json(arguments: list[str], *, timeout: int = 180) -> dict[str, Any]:
    result = subprocess.run(
        arguments,
        cwd=ROOT,
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise SemifinalClosureError(
            "COMMAND_FAILED:"
            + Path(arguments[0]).name
            + ":"
            + (result.stderr.strip() or result.stdout.strip())
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SemifinalClosureError(
            "COMMAND_JSON_INVALID:" + Path(arguments[0]).name
        ) from exc
    if not isinstance(value, dict) or value.get("status") != "PASS":
        raise SemifinalClosureError(
            "COMMAND_DID_NOT_PASS:" + Path(arguments[0]).name
        )
    return value


def _normalized(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item for key, item in value.items() if key in NORMALIZED_VERIFIER_KEYS
    }


def _ensure_checkout(checkout: Path, lock_path: Path) -> None:
    if checkout.is_dir():
        result = _run_json(
            [
                sys.executable,
                str(ROOT / "scripts/fetch_pinned_agentteams.py"),
                "--lock",
                str(lock_path),
                "--output",
                str(checkout),
            ]
        )
    else:
        result = _run_json(
            [
                sys.executable,
                str(ROOT / "scripts/fetch_pinned_agentteams.py"),
                "--lock",
                str(lock_path),
                "--output",
                str(checkout),
            ],
            timeout=300,
        )
    if result.get("status") != "PASS":
        raise SemifinalClosureError("PINNED_AGENTTEAMS_CHECKOUT_NOT_VERIFIED")


def _build_wheel(output_dir: Path) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        raise SemifinalClosureError("UV_EXECUTABLE_REQUIRED")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="orgrebase-wheel-build-") as raw:
        build_root = Path(raw)
        result = subprocess.run(
            [uv, "build", "--wheel", "--out-dir", str(build_root)],
            cwd=ROOT,
            env=_environment(),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise SemifinalClosureError(
                "WHEEL_BUILD_FAILED:"
                + (result.stderr.strip() or result.stdout.strip())
            )
        wheels = sorted(build_root.glob("*.whl"))
        if len(wheels) != 1:
            raise SemifinalClosureError("EXACTLY_ONE_WHEEL_REQUIRED")
        retained = output_dir / wheels[0].name
        shutil.copy2(wheels[0], retained)
    return retained


def _preflight_tool_binding(
    *, run_id: str, task_id: str, graph_digest: str
) -> tuple[str, str]:
    with ControlledEnterpriseServer() as server:
        client = ControlledEnterpriseClient(server.base_url, token=server.token)
        result, receipt = client.call_dependency_tool(
            run_id=run_id,
            task_id=task_id,
            target_id="work:enterprise-quote",
            graph_digest=graph_digest,
        )
    return receipt.digest, sha256_digest(result)


def _preflight_source_binding(*, run_id: str) -> tuple[str, str]:
    with ControlledEnterpriseServer() as server:
        client = ControlledEnterpriseClient(server.base_url, token=server.token)
        source, receipt = client.read_source(run_id=run_id)
    if source is None:
        raise SemifinalClosureError("SOURCE_PREFLIGHT_PAYLOAD_REQUIRED")
    return receipt.digest, sha256_digest(source)


def _child_verifiers(stage: Path, checkout: Path) -> dict[str, dict[str, Any]]:
    results = {
        "native": _run_json(
            [
                sys.executable,
                str(ROOT / "scripts/verify_native_taskflow_evidence.py"),
                "--evidence",
                str(stage / "agentteams"),
                "--checkout",
                str(checkout),
            ]
        ),
        "skills": _run_json(
            [
                sys.executable,
                str(ROOT / "scripts/verify_skill_package_evidence.py"),
                "--evidence",
                str(stage / "skills"),
            ]
        ),
        "operations": _run_json(
            [
                sys.executable,
                str(ROOT / "scripts/verify_controlled_local_evidence.py"),
                "--evidence",
                str(stage / "operations"),
            ]
        ),
        "quote_value": _run_json(
            [
                sys.executable,
                str(ROOT / "scripts/verify_quote_value_evidence.py"),
                "--receipt",
                str(stage / "quote-value/quote-value-receipt.json"),
                "--project-root",
                str(ROOT),
            ]
        ),
        "quote_shadow": _run_json(
            [
                sys.executable,
                str(ROOT / "scripts/verify_quote_shadow_admission.py"),
                "--record",
                str(
                    ROOT
                    / "benchmark/quote-value-v0.2-shadow/synthetic/observation-record.json"
                ),
                "--source-root",
                str(ROOT / "benchmark/quote-value-v0.2-shadow"),
                "--receipt",
                str(stage / "quote-shadow/quote-shadow-admission-receipt.json"),
            ]
        ),
    }
    return {name: _normalized(value) for name, value in results.items()}


def _write_index(stage: Path) -> dict[str, Any]:
    entries = [
        {
            "path": path.relative_to(stage).as_posix(),
            "sha256": _file_digest(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(stage.rglob("*"))
        if path.is_file() and path != stage / "evidence-index.json"
    ]
    index = _record(
        {
            "schema_version": "orgrebase.semifinal-closure-evidence-index.v1",
            "entry_count": len(entries),
            "entries": entries,
            "pack_digest": sha256_digest(entries),
        }
    )
    _write(stage / "evidence-index.json", index)
    return index


def _unique_path(root: Path, name: str) -> Path:
    candidate = root / name
    counter = 1
    while candidate.exists():
        candidate = root / f"{name}-{counter}"
        counter += 1
    return candidate


def _validated_output_target(
    output_dir: str | Path,
    *,
    allowed_output_root: str | Path | None = None,
) -> tuple[Path, Path]:
    """Confine replace/archive operations to one explicit evidence namespace."""

    allowed = Path(allowed_output_root or DEFAULT_OUTPUT_ROOT).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if (
        output.parent != allowed
        or output.name in {"", ".", "archive", "failed"}
        or output.name.startswith(".semifinal-stage-")
    ):
        raise SemifinalClosureError("UNSAFE_OUTPUT_TARGET")
    return output, allowed


def _run_parent_verifier(stage: Path, checkout: Path) -> dict[str, Any]:
    return _run_json(
        [
            sys.executable,
            str(ROOT / "scripts/verify_semifinal_closure.py"),
            "--evidence",
            str(stage),
            "--checkout",
            str(checkout),
        ],
        timeout=300,
    )


def run_semifinal_closure(
    *,
    output_dir: str | Path,
    checkout: str | Path,
    lock_path: str | Path,
    run_id: str = DEFAULT_RUN_ID,
    allowed_output_root: str | Path | None = None,
) -> dict[str, Any]:
    output, output_root = _validated_output_target(
        output_dir,
        allowed_output_root=allowed_output_root,
    )
    checkout_path = Path(checkout).expanduser().resolve()
    lock = Path(lock_path).expanduser().resolve()
    if (
        checkout_path == output
        or checkout_path.is_relative_to(output)
        or output.is_relative_to(checkout_path)
    ):
        raise SemifinalClosureError("OUTPUT_CHECKOUT_OVERLAP")
    output_root.mkdir(parents=True, exist_ok=True)
    _ensure_checkout(checkout_path, lock)
    stage = Path(tempfile.mkdtemp(prefix=".semifinal-stage-", dir=output_root))
    archived: Path | None = None
    installed = False
    try:
        source_receipt_digest, source_payload_digest = _preflight_source_binding(
            run_id=run_id
        )
        native_nonce = sha256_digest(
            {
                "run_id": run_id,
                "source_receipt_digest": source_receipt_digest,
                "source_payload_digest": source_payload_digest,
            }
        ).split(":", 1)[1]
        native = run_native_taskflow_slice(
            checkout=checkout_path,
            output_dir=stage / "agentteams",
            lock_path=lock,
            run_id=run_id,
            nonce=native_nonce,
        )
        coalition = build_coalition_result_binding(native)
        _write(stage / "agentteams/coalition-result-binding.json", coalition)
        domain_result_digests = {
            str(item["domain"]): str(item["observed_result_digest"])
            for item in coalition["members"]
        }
        gtm = [
            item
            for item in native["bindings"]
            if item.get("domain") == "gtm"
            and item.get("attempt") == 1
            and str(item.get("status", "")).upper() == "COMPLETED"
        ]
        if len(gtm) != 1 or gtm[0].get("target_writes") != 0:
            raise SemifinalClosureError("EXACT_ACCEPTED_GTM_BINDING_REQUIRED")
        binding = gtm[0]
        task_id = str(binding["task_id"])
        delegation_id = str(binding["delegation_digest"])
        graph_digest = str(native["plan_digest"])
        tool_receipt_digest, dependency_result_digest = _preflight_tool_binding(
            run_id=run_id,
            task_id=task_id,
            graph_digest=graph_digest,
        )

        wheel = _build_wheel(stage / "artifacts")
        skill = run_skill_lifecycle_evidence(
            output_dir=stage / "skills",
            run_id=run_id,
            task_id=task_id,
            delegation_id=delegation_id,
            root=ROOT,
            wheel_path=wheel,
            dependency_tool_receipt_digest=tool_receipt_digest,
            dependency_result_digest=dependency_result_digest,
            coalition_result_binding_digest=str(coalition["coalition_digest"]),
            domain_result_digests=domain_result_digests,
        )
        skill_discovery = json.loads(
            (stage / "skills/discovery.json").read_text(encoding="utf-8")
        )
        quote_discovery = [
            item
            for item in skill_discovery
            if isinstance(item, dict)
            and item.get("name") == "enterprise-quote-compose"
        ]
        if len(quote_discovery) != 1 or not quote_discovery[0].get("version"):
            raise SemifinalClosureError("QUOTE_SKILL_DISCOVERY_REQUIRED")
        quote_skill_version = str(quote_discovery[0]["version"])
        quote_package_digest = str(
            skill["packages"]["enterprise-quote-compose"]
        )
        operations = run_controlled_local_evidence(
            output_dir=stage / "operations",
            run_id=run_id,
            task_id=task_id,
            delegation_id=delegation_id,
            native_receipt_digest=str(native["receipt_digest"]),
            skill_package_digest=quote_package_digest,
            skill_invocation_receipt_digest=str(skill["quote_invocation_receipt"]),
            graph_digest=graph_digest,
            artifact_paths=(wheel,),
            deployment_profile_path=ROOT / "configs/deployment/controlled-local.json",
            lock_path=ROOT / "uv.lock",
            pyproject_path=ROOT / "pyproject.toml",
            coalition_result_binding_digest=str(coalition["coalition_digest"]),
            native_nonce=str(native["nonce"]),
            agentteams_project_id=str(native["project_id"]),
            agentteams_attempt_id=str(binding["attempt_ref"]),
            agentteams_attempt_number=int(binding["attempt"]),
            agentteams_reassign_count=sum(
                item.get("predecessor_task_id") is not None
                for item in native["bindings"]
                if isinstance(item, dict)
            ),
            skill_name="enterprise-quote-compose",
            skill_version=quote_skill_version,
        )
        if operations["tool_receipt_digest"] != tool_receipt_digest:
            raise SemifinalClosureError("TOOL_RECEIPT_REPLAY_MISMATCH")
        if operations["source_receipt_digest"] != source_receipt_digest:
            raise SemifinalClosureError("SOURCE_RECEIPT_REPLAY_MISMATCH")

        _run_json(
            [
                sys.executable,
                str(ROOT / "scripts/run_quote_value_benchmark.py"),
                "--output-dir",
                str(stage / "quote-value"),
            ]
        )
        _run_json(
            [
                sys.executable,
                str(ROOT / "scripts/run_quote_shadow_admission.py"),
                "--record",
                str(
                    ROOT
                    / "benchmark/quote-value-v0.2-shadow/synthetic/observation-record.json"
                ),
                "--source-root",
                str(ROOT / "benchmark/quote-value-v0.2-shadow"),
                "--output-dir",
                str(stage / "quote-shadow"),
            ]
        )
        quote_value = _load(stage / "quote-value/quote-value-receipt.json")
        quote_shadow = _load(
            stage / "quote-shadow/quote-shadow-admission-receipt.json"
        )
        invocation = _load(stage / "skills/quote-compose/invocation-receipt.json")
        child_verifiers = _child_verifiers(stage, checkout_path)
        _write(stage / "verification/child-verifiers.json", child_verifiers)

        summary_base = {
            "schema_version": "orgrebase.semifinal-closure-summary.v1",
            "status": "PASS",
            "evidence_class": "CONTROLLED_LOCAL_INTEGRATED_VERTICAL_SLICE",
            "run_id": run_id,
            "task_id": task_id,
            "delegation_id": delegation_id,
            "source_receipt_digest": source_receipt_digest,
            "source_payload_digest": source_payload_digest,
            "native_nonce": native_nonce,
            "native_receipt_digest": native["receipt_digest"],
            "coalition_result_binding_digest": coalition["coalition_digest"],
            "native_source_commit": native["source_verification"]["commit"],
            "skill_summary_digest": skill["digest"],
            "skill_package_digest": quote_package_digest,
            "skill_runtime_resource_mode": skill["runtime_resource_mode"],
            "quote_skill_version": quote_skill_version,
            "skill_invocation_receipt_digest": invocation["digest"],
            "coalition_member_count": len(coalition["members"]),
            "tool_receipt_digest": tool_receipt_digest,
            "dependency_result_digest": dependency_result_digest,
            "graph_digest": graph_digest,
            "operations_summary_digest": operations["digest"],
            "quote_value_receipt_digest": quote_value["digest"],
            "quote_shadow_admission_receipt_digest": quote_shadow["digest"],
            "quote_shadow_evidence_ceiling": quote_shadow["evidence_ceiling"],
            "wheel_file": wheel.relative_to(stage).as_posix(),
            "wheel_sha256": _file_digest(wheel),
            "terminal_state": "CANDIDATE_ACCEPTED",
            "canonical_target_writes": 0,
            "business_value_same_run": False,
            "completion_matrix": COMPLETION_MATRIX,
            "production_readiness": False,
            "claim_boundary": (
                "One controlled-local Source+native AgentTeams+four-domain coalition+"
                "Tool+installed-wheel Skill+causal OTLP candidate chain. Workspace "
                "Approval/Apply, live distributed "
                "workers, real enterprise connectors/value, HA/DR/SLA are NOT_RUN."
            ),
        }
        summary = _record(summary_base)
        _write(stage / "summary.json", summary)
        _write_index(stage)
        _run_parent_verifier(stage, checkout_path)

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        if output.exists():
            archive_root = output_root / "archive"
            archive_root.mkdir(parents=True, exist_ok=True)
            archived = _unique_path(
                archive_root,
                f"{output.name}-{timestamp}-{summary['digest'].split(':', 1)[1][:12]}",
            )
            shutil.move(str(output), str(archived))
        os.replace(stage, output)
        installed = True
        final_verification = _run_parent_verifier(output, checkout_path)
        return {
            **final_verification,
            "output": str(output),
            "summary_digest": summary["digest"],
            "archived_previous": str(archived) if archived else None,
            "completion_matrix": COMPLETION_MATRIX,
        }
    except Exception:
        failed_root = output_root / "failed"
        failed_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        if installed and output.exists():
            failed = _unique_path(failed_root, f"{output.name}-{timestamp}")
            shutil.move(str(output), str(failed))
            if archived is not None and archived.exists():
                shutil.move(str(archived), str(output))
        else:
            if archived is not None and archived.exists() and not output.exists():
                shutil.move(str(archived), str(output))
            if stage.exists():
                failed = _unique_path(failed_root, f"stage-{timestamp}")
                shutil.move(str(stage), str(failed))
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "evidence/semifinal-closure/latest",
    )
    parser.add_argument(
        "--checkout",
        type=Path,
        default=default_agentteams_checkout(ROOT),
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=ROOT / "agentteams/teamharness-lock.json",
    )
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args()
    result = run_semifinal_closure(
        output_dir=args.output_dir,
        checkout=args.checkout,
        lock_path=args.lock,
        run_id=args.run_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
