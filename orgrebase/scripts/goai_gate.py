"""Machine-verifiable GOAI stage gates; evidence labels are never inferred."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

from orgrebase.agentteams_ingest import ingest_local_proposals
from orgrebase.collaboration import OrchestrationCompiler
from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    AgentCandidateIngestionReceipt,
    AgentRun,
    ChangeSetRevision,
    CompilationReceipt,
    CoordinationReceipt,
    ImpactPreview,
    OrchestrationPlan,
    QualificationReport,
    RunEnvelope,
    StructuredHandoff,
)
from orgrebase.fixture import load_fixture
from orgrebase.goai_agentteams import (
    EXPECTED_FRESH_CORE_EVIDENCE,
    EXPECTED_PUBLIC_TRANSPORT_FIXTURE,
    FRESH_CORE_CLASSIFICATION,
    HISTORICAL_TRANSPORT_CLASSIFICATION,
    load_agentteams_evidence,
)
from orgrebase.service import OrgRebaseService
from orgrebase.skills import load_skill_contract

ROOT = Path(__file__).resolve().parents[1]
AGENTTEAMS_SOURCE_COMMIT = "849182af8e017168a5a200a87b1062142caf462d"
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
OAC_EVOLUTION_QUOTE_DIGEST = "sha256:0212b4cfc2a86552beb1aea4aa35e5771e7c6204a4c45f0d7f9dc59379953b44"
OAC_EVOLUTION_BOUNDARIES = {
    "data": "SYNTHETIC_FIXTURE",
    "assurance": "SYNTHETIC_CONTROLLED_PROCESS_ASSURANCE_ONLY",
    "observation": "SYNTHETIC_CONTROLLED_OBSERVATION_NOT_EXTERNAL_GROUND_TRUTH",
    "runtime_evidence": "ZERO_EFFECT_HANDLER_COMPLETION_NOT_BUSINESS_TRUTH",
    "demand_admission": "NO_INDEPENDENT_KIND_SCHEMA_VALIDATED_AND_EXECUTION_APPROVAL_BOUND",
    "source_admission_authority": "human:veracier-shadow-owner",
    "authority_assurance": "DECLARED_NOT_AUTHENTICATED",
    "human_review": "NOT_RUN",
    "real_enterprise": "NOT_RUN",
    "external_effects": "NONE",
    "target_writes": 0,
    "production_ready": False,
}


def _json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_manifest_verifier() -> ModuleType:
    path = Path(__file__).with_name("verify_evidence_manifest.py")
    spec = importlib.util.spec_from_file_location("orgrebase_manifest_verifier", path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging invariant
        raise RuntimeError("manifest verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_oac_evolution_evaluator(root: Path) -> ModuleType:
    path = root / "scripts" / "verify_oac_evolution_evidence.py"
    spec = importlib.util.spec_from_file_location("orgrebase_oac_evolution_evaluator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("OAC evolution evaluator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def verify_integrated_semifinal_closure(root: Path) -> dict[str, Any] | None:
    """Replay the retained Spec 045 parent pack as a blocking GOAI gate.

    The parent verifier deliberately runs in a separate interpreter and does
    not import product code.  A published pack must therefore remain
    independently replayable even when the pinned AgentTeams source checkout
    is not shipped beside it.
    """

    script = root / "scripts" / "verify_semifinal_closure.py"
    evidence = root / "evidence" / "semifinal-closure" / "latest"
    if not script.is_file() or not (evidence / "evidence-index.json").is_file():
        return None
    source_lock_digest = "sha256:d62e5470ad2d803d19fdc00b26958cb2d5f1e237b84870cf7c79464d75cb4888"
    lock_path = root / "agentteams/historical/teamharness-v1.2.2.json"
    try:
        native = _json(evidence / "agentteams/lifecycle-receipt.json")
        source = native.get("source_verification") if native is not None else None
        if (
            not isinstance(source, dict)
            or source.get("source_lock_digest") != source_lock_digest
            or source.get("commit") != AGENTTEAMS_SOURCE_COMMIT
            or lock_path.is_symlink()
            or not lock_path.is_file()
            or not lock_path.resolve().is_relative_to(root.resolve())
        ):
            return None
        lock = _json(lock_path)
        if lock is None or sha256_digest(lock) != source_lock_digest:
            return None
    except (OSError, UnicodeError, ValueError):
        return None
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--evidence",
                str(evidence),
                "--retained-quote-value-inputs",
                str(root / "evidence/semifinal-closure/supporting/quote-value-inputs"),
                "--retained-build",
                "--lock",
                str(lock_path),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        result = json.loads(completed.stdout) if completed.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    valid = (
        isinstance(result, dict)
        and result.get("status") == "PASS"
        and result.get("evidence_class")
        == "CONTROLLED_LOCAL_INTEGRATED_VERTICAL_SLICE"
        and result.get("terminal_state") == "CANDIDATE_ACCEPTED"
        and result.get("canonical_target_writes") == 0
        and result.get("production_readiness") is False
        and result.get("native_verification_strength") == "RETAINED_LOCK_REPLAY"
        and result.get("verification_scope") == "RETAINED_ARTIFACT"
        and result.get("current_release_qualified") is False
        and result.get("agentteams_commit") == AGENTTEAMS_SOURCE_COMMIT
        and result.get("source_lock_digest") == source_lock_digest
        and isinstance(result.get("current_build_binding"), dict)
    )
    return result if valid else None


def _integrated_semifinal_closure_valid(root: Path) -> bool:
    return verify_integrated_semifinal_closure(root) is not None


def _quote_value_evidence_valid(root: Path) -> bool:
    """Replay the independent Spec 041 publication and close its exact index.

    Spec 045 retains a run-local QuoteValue child, but Spec 041 intentionally
    publishes a second, top-level coordinate that must bind current inputs.
    The historical child is replayed against its fixed input snapshot; accepting
    that child alone would let ProductPath refreshes leave this current receipt
    stale without blocking the aggregate gate.
    """

    evidence = root / "evidence" / "quote-value" / "latest"
    receipt_path = evidence / "quote-value-receipt.json"
    verification_path = evidence / "verification.json"
    index_path = evidence / "evidence-index.json"
    script = root / "scripts" / "verify_quote_value_evidence.py"
    if not all(path.is_file() for path in (receipt_path, verification_path, index_path, script)):
        return False
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--receipt",
                str(receipt_path),
                "--project-root",
                str(root),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        replay = json.loads(completed.stdout) if completed.returncode == 0 else None
        receipt = _json(receipt_path)
        verification = _json(verification_path)
        index = _json(index_path)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False
    if not all(isinstance(item, dict) for item in (replay, receipt, verification, index)):
        return False
    entries = index.get("entries")
    if not isinstance(entries, list) or entries != [
        {"path": "quote-value-receipt.json", "file_sha256": _sha256_file(receipt_path)},
        {"path": "verification.json", "file_sha256": _sha256_file(verification_path)},
    ]:
        return False
    index_payload = {key: value for key, value in index.items() if key != "digest"}
    receipt_digest = receipt.get("digest")
    return bool(
        completed.returncode == 0
        and replay.get("status") == "PASS"
        and replay.get("receipt_digest") == receipt_digest
        and replay.get("verifier_product_imports") == 0
        and replay.get("failures") == []
        and verification
        == {
            "schema_version": "orgrebase.quote-value-verification.v1",
            "status": "PASS",
            "verification_mode": "PRODUCT_INDEPENDENT_DETERMINISTIC_REPLAY",
            "implementation_independence": "SHARED_VERIFY_RECEIPT_IMPLEMENTATION",
            "receipt_ref": "quote-value-receipt.json",
            "receipt_digest": receipt_digest,
            "receipt_file_sha256": _sha256_file(receipt_path),
            "evaluator_product_imports": 0,
            "verifier_product_imports": 0,
            "failures": [],
        }
        and index.get("schema_version") == "orgrebase.quote-value-evidence-index.v1"
        and index.get("status") == "PASS"
        and index.get("digest") == sha256_digest(index_payload)
    )


def _retained_wheel_valid(root: Path, record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    filename, digest = record.get("file"), record.get("sha256")
    if not isinstance(filename, str) or Path(filename).name != filename:
        return False
    if not filename.endswith(".whl") or not SHA256_DIGEST.fullmatch(str(digest)):
        return False
    path = root / "evidence" / "oac-evolution" / "wheel-check" / "wheels" / filename
    return path.is_file() and _sha256_file(path) == digest


def verify_oac_governed_evolution(root: Path) -> dict[str, Any]:
    """Independently replay canonical and dual-wheel evolution evidence.

    The retained report is not accepted by label. Its generated pack is
    replayed with the product-independent evaluator, and both retained wheel
    files are rebound to their recorded bytes. Exact synthetic/controlled
    claim boundaries are part of the gate rather than presentation metadata.
    """

    evidence_root = root / "evidence" / "oac-evolution" / "latest"
    wheel_root = root / "evidence" / "oac-evolution" / "wheel-check"
    failures: list[str] = []
    canonical: dict[str, Any] = {}
    wheel_evaluation: dict[str, Any] = {}
    summary: dict[str, Any] | None = None
    wheel_check: dict[str, Any] | None = None

    def require(condition: bool, code: str) -> None:
        if not condition:
            failures.append(code)

    try:
        evaluator = _load_oac_evolution_evaluator(root)
        canonical = evaluator.evaluate(evidence_root)
        wheel_evaluation = evaluator.evaluate(wheel_root / "pack")
        summary = _json(evidence_root / "summary.json")
        wheel_check = _json(wheel_root / "wheel-check.json")
        require(canonical.get("status") == "PASS", "CANONICAL_EVALUATION_FAILED")
        require(
            canonical.get("artifact_count") == 51
            and canonical.get("event_count") == 13
            and canonical.get("product_imports") == 0
            and SHA256_DIGEST.fullmatch(str(canonical.get("pack_digest", ""))) is not None,
            "CANONICAL_EVALUATION_SHAPE_INVALID",
        )
        require(summary is not None, "CANONICAL_SUMMARY_MISSING")
        if summary is not None:
            require(
                summary.get("status") == "PASS"
                and summary.get("maturity") == "SYNTHETIC_CONTROLLED_REFERENCE_MVP",
                "CANONICAL_MATURITY_INVALID",
            )
            require(
                summary.get("boundaries") == OAC_EVOLUTION_BOUNDARIES,
                "CANONICAL_BOUNDARIES_INVALID",
            )
            if summary.get("schema_version") == "orgrebase.workspace-oac-evolution-demo.v2":
                require(
                    canonical.get("status") == "PASS"
                    and summary.get("preliminary_regression", {}).get("status") == "PASS",
                    "PRELIMINARY_QUOTE_REGRESSION_INVALID",
                )
            else:
                require(
                    summary.get("schema_version") == "orgrebase.workspace-oac-evolution-demo.v1"
                    and summary.get("preliminary_regression") == {
                        "status": "PASS",
                        "expected_quote_digest": OAC_EVOLUTION_QUOTE_DIGEST,
                        "actual_quote_digest": OAC_EVOLUTION_QUOTE_DIGEST,
                    },
                    "PRELIMINARY_QUOTE_REGRESSION_INVALID",
                )
            require(
                summary.get("runs")
                == {
                    "base-accepted": {
                        "plan_status": "ACCEPT",
                        "execution_status": "COMPLETED",
                        "outcome_verdict": "ACCEPT",
                    },
                    "split-accepted": {
                        "plan_status": "ACCEPT",
                        "execution_status": "COMPLETED",
                        "outcome_verdict": "ACCEPT",
                    },
                    "split-rejected": {
                        "plan_status": "ACCEPT",
                        "execution_status": "COMPLETED",
                        "outcome_verdict": "REJECT",
                    },
                },
                "OUTCOME_DISTINCTION_INVALID",
            )
            evolution = summary.get("evolution", {})
            require(
                evolution.get("promotion") == "r1_TO_r2"
                and evolution.get("rollback") == "r2_TO_r1"
                and evolution.get("active_pointer") == "r1"
                and evolution.get("human_review") == "NOT_RUN"
                and evolution.get("governance_mode") == "SCRIPTED_GOVERNANCE_IDENTITY",
                "GOVERNED_PROMOTION_ROLLBACK_INVALID",
            )
        require(wheel_check is not None, "DUAL_WHEEL_REPORT_MISSING")
        if wheel_check is not None:
            require(
                wheel_check.get("schema_version") == "orgrebase.oac-evolution-dual-wheel-check.v1"
                and wheel_check.get("status") == "PASS"
                and wheel_check.get("offline_build") is True
                and wheel_check.get("clean_temporary_working_directory") is True
                and wheel_check.get("oac_source_material_mode") == "READ_ONLY_PUBLIC_SOURCE_COMMITMENT",
                "DUAL_WHEEL_REPORT_INVALID",
            )
            require(
                wheel_evaluation.get("status") == "PASS"
                and wheel_check.get("independent_evaluation") == wheel_evaluation
                and wheel_evaluation == canonical
                and wheel_check.get("pack_digest") == canonical.get("pack_digest"),
                "DUAL_WHEEL_PACK_REPLAY_INVALID",
            )
            probes = wheel_check.get("probe", {})
            require(
                isinstance(probes, dict)
                and set(probes) == {"orgrebase", "oac"}
                and all(
                    isinstance(item, dict) and item.get("module_loaded_from_wheel") is True
                    for item in probes.values()
                ),
                "DUAL_WHEEL_IMPORT_PROBE_INVALID",
            )
            wheels = wheel_check.get("wheels", {})
            require(
                isinstance(wheels, dict)
                and set(wheels) == {"orgrebase", "oac"}
                and all(_retained_wheel_valid(root, item) for item in wheels.values()),
                "DUAL_WHEEL_BYTES_INVALID",
            )
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        failures.append(f"VERIFICATION_ERROR:{type(exc).__name__}")

    failures = sorted(set(failures))
    event_chain = summary.get("event_chain", {}) if summary is not None else {}
    if not isinstance(event_chain, dict):
        event_chain = {}
    retained_probes = wheel_check.get("probe", {}) if isinstance(wheel_check, dict) else {}
    retained_wheels = wheel_check.get("wheels", {}) if isinstance(wheel_check, dict) else {}
    if not isinstance(retained_probes, dict):
        retained_probes = {}
    if not isinstance(retained_wheels, dict):
        retained_wheels = {}
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "maturity": summary.get("maturity") if summary is not None else None,
        "artifact_count": canonical.get("artifact_count"),
        "event_count": canonical.get("event_count"),
        "pack_digest": canonical.get("pack_digest"),
        "event_chain_head": event_chain.get("head_digest"),
        "independent_evaluator": {
            "status": canonical.get("status"),
            "product_imports": canonical.get("product_imports"),
            "implementation": "scripts/verify_oac_evolution_evidence.py",
        },
        "dual_wheel": {
            "status": wheel_check.get("status") if wheel_check is not None else "FAIL",
            "offline_build": (wheel_check.get("offline_build") if wheel_check is not None else False),
            "clean_temporary_working_directory": (
                wheel_check.get("clean_temporary_working_directory") if wheel_check is not None else False
            ),
            "oac_source_material_mode": (
                wheel_check.get("oac_source_material_mode") if wheel_check is not None else None
            ),
            "modules_loaded_from_wheel": sorted(retained_probes),
            "wheel_names": sorted(
                item.get("file", "") for item in retained_wheels.values() if isinstance(item, dict)
            ),
            "evidence_path": "evidence/oac-evolution/wheel-check/wheel-check.json",
        },
    }


def _manifest_valid(path: Path) -> tuple[bool, dict[str, Any] | None]:
    try:
        _load_manifest_verifier().verify(path)
    except (RuntimeError, OSError, ValueError):
        return False, None
    return True, _json(path)


RETAINED_OAC_ADMISSION_POLICY_SHA256 = "sha256:0d0f798059d39b906b33f748dfd2d56e5b12b20c4ea6204b63d4299278b508ae"
RETAINED_OAC_ADMISSION_PACK_DIGEST = "sha256:eba2d72c961de47d9c99844eb6445428d3755d1a6acb67db20565586c219ca1f"



RETAINED_OAC_ADMISSION_WHEEL = "orgrebase-0.4.0-py3-none-any.whl"
RETAINED_OAC_ADMISSION_WHEEL_SHA256 = "sha256:1f2172b6185ff1cde473748a949f2d092a6d9ee3868dea2690de74aaaccbe193"
_RETAINED_ADMISSION_BOOTSTRAP = """
import importlib.metadata, json, pathlib, sys
source = pathlib.Path(sys.argv[1]).resolve(strict=True)
sys.path.insert(0, str(source))
from orgrebase.workspace.oac_bridge import verify_oac_bridge_evidence
try:
    result = verify_oac_bridge_evidence(sys.argv[2], policy_path=sys.argv[3])
    error = None
except Exception as exception:
    result, error = None, str(exception)
origins = {}
for name, module in sys.modules.items():
    if (name == "orgrebase" or name.startswith("orgrebase.")) and getattr(module, "__file__", None):
        path = pathlib.Path(module.__file__).resolve(strict=True)
        if not path.is_relative_to(source):
            raise RuntimeError("RETAINED_OAC_READER_ORIGIN_INVALID")
        origins[name] = path.relative_to(source).as_posix()
if "orgrebase.workspace.oac_bridge" not in origins:
    raise RuntimeError("RETAINED_OAC_READER_ORIGIN_INVALID")
reader = {
    "python": sys.version.split()[0],
    "environment_dependencies": dict(sorted((item.metadata["Name"], item.version)
        for item in importlib.metadata.distributions() if item.metadata["Name"].lower() != "orgrebase")),
    "module_origins": origins,
    "module_origin_scope": "VERIFIED_PINNED_WHEEL_EXTRACTION",
    "original_environment_reproduced": False,
}
print(json.dumps({"result": result, "reader": reader, "error": error}))
raise SystemExit(1 if error else 0)
"""


def _read_retained_oac_admission(root: Path, evidence: Path, policy: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    wheel_record = {"file": RETAINED_OAC_ADMISSION_WHEEL, "sha256": RETAINED_OAC_ADMISSION_WHEEL_SHA256}
    wheel = root / "evidence/oac-evolution/wheel-check/wheels" / RETAINED_OAC_ADMISSION_WHEEL
    if wheel.is_symlink() or not _retained_wheel_valid(root, wheel_record):
        raise ValueError("RETAINED_OAC_ADMISSION_WHEEL_UNAVAILABLE_OR_CHANGED")
    wheel_bytes = wheel.read_bytes()
    if "sha256:" + hashlib.sha256(wheel_bytes).hexdigest() != RETAINED_OAC_ADMISSION_WHEEL_SHA256:
        raise ValueError("RETAINED_OAC_ADMISSION_WHEEL_UNAVAILABLE_OR_CHANGED")
    # Extract the verified bytes without reopening the path. This historical
    # reader locates packaged assets through real filesystem paths.
    with tempfile.TemporaryDirectory(prefix="orgrebase-retained-admission-") as temporary:
        extracted = Path(temporary)
        evidence_snapshot = extracted / "retained-evidence"
        shutil.copytree(evidence, evidence_snapshot)
        snapshot_policy = evidence_snapshot / policy.name
        if _sha256_file(snapshot_policy) != RETAINED_OAC_ADMISSION_POLICY_SHA256:
            raise ValueError("OAC_RETAINED_POLICY_IDENTITY_MISMATCH")
        snapshot_index = _json(evidence_snapshot / "evidence-index.json")
        if snapshot_index is None or snapshot_index.get("pack_digest") != RETAINED_OAC_ADMISSION_PACK_DIGEST:
            raise ValueError("OAC_RETAINED_PACK_IDENTITY_MISMATCH")
        with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
            for member in archive.infolist():
                path = Path(member.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("RETAINED_OAC_ADMISSION_WHEEL_PATH_INVALID")
            archive.extractall(extracted)
        environment = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL") if key in os.environ}
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", _RETAINED_ADMISSION_BOOTSTRAP,
             str(extracted), str(evidence_snapshot), str(snapshot_policy)],
            cwd=extracted, env=environment, capture_output=True, text=True, timeout=60, check=False,
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise ValueError("RETAINED_OAC_ADMISSION_READER_FAILED") from error
        if completed.returncode != 0:
            raise ValueError("RETAINED_OAC_ADMISSION_REPLAY_FAILED:" + str(payload.get("error")))
        reader = payload["reader"]
        if (reader.get("module_origin_scope") != "VERIFIED_PINNED_WHEEL_EXTRACTION"
                or reader.get("original_environment_reproduced") is not False):
            raise ValueError("RETAINED_OAC_READER_ORIGIN_INVALID")
        return payload["result"], {**reader, "wheel_sha256": RETAINED_OAC_ADMISSION_WHEEL_SHA256}


def verify_retained_oac_runtime_admission(root: Path) -> dict[str, Any]:
    """Verify the frozen local demonstration under its exact historical policy.

    The fixed pack commitment binds every capsule, approval and event file.
    This evidence-only entry never substitutes its policy for live admission.
    """
    evidence = root / "evidence" / "oac-bridge" / "latest"
    policy = evidence / "policy.json"
    report: dict[str, Any] = {
        "status": "FAIL",
        "verification_scope": "RETAINED_ARTIFACT",
        "current_release_qualified": False,
        "expected_policy_sha256": RETAINED_OAC_ADMISSION_POLICY_SHA256,
        "expected_pack_digest": RETAINED_OAC_ADMISSION_PACK_DIGEST,
        "failures": [],
    }
    try:
        if not policy.is_file() or policy.is_symlink():
            raise ValueError("OAC_RETAINED_POLICY_UNAVAILABLE")
        report["policy_sha256"] = _sha256_file(policy)
        if report["policy_sha256"] != RETAINED_OAC_ADMISSION_POLICY_SHA256:
            raise ValueError("OAC_RETAINED_POLICY_IDENTITY_MISMATCH")
        index = _json(evidence / "evidence-index.json")
        report["pack_digest"] = index.get("pack_digest") if index is not None else None
        if report["pack_digest"] != RETAINED_OAC_ADMISSION_PACK_DIGEST:
            raise ValueError("OAC_RETAINED_PACK_IDENTITY_MISMATCH")
        result, reader = _read_retained_oac_admission(root, evidence, policy)
        if not isinstance(result, dict) or result != {
            "status": "PASS",
            "maturity": "LOCAL_RUNTIME_ADMISSION_PASS",
            "cases": 2,
            "entries": 13,
            "event_chain_head": result.get("event_chain_head"),
            "target_writes": 0,
            "runtime_owner_approval_input": "CONTROLLED_LOCAL_SCRIPTED_COMMAND",
            "external_human_approval": "NOT_RUN",
            "handler_execution": "NOT_RUN",
            "agent_execution": "NOT_RUN",
            "outcome_certificate": "NOT_IMPLEMENTED",
            "real_enterprise": "NOT_RUN",
        } or not SHA256_DIGEST.fullmatch(str(result.get("event_chain_head", ""))):
            raise ValueError("OAC_RETAINED_ADMISSION_RESULT_INVALID")
    except OSError:
        report["failures"] = ["OAC_RETAINED_EVIDENCE_UNAVAILABLE"]
        return report
    except (RuntimeError, TypeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report["failures"] = [str(error)]
        return report
    return {**report, "status": "PASS", "runtime_admission": result, "reader": reader}


def verify_oac_enterprise_adaptation(root: Path) -> dict[str, Any]:
    """Replay Spec 062 without importing its producer or OrgRebase product code."""

    evidence = root / "evidence" / "oac-quote-adaptation" / "latest"
    manifest_path = evidence / "manifest.json"
    summary_path = evidence / "summary.json"
    script = root / "scripts" / "verify_oac_quote_adaptation.py"
    failures: list[str] = []
    result: dict[str, Any] = {}
    manifest: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--root",
                str(evidence),
                "--retained-build",
                "--project-root",
                str(root),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        result = json.loads(completed.stdout) if completed.returncode == 0 else {}
        manifest = _json(manifest_path) or {}
        summary = _json(summary_path) or {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        failures.append("VERIFIER_EXECUTION_FAILED")

    evergreen = summary.get("evergreen", {})
    veracier = summary.get("veracier", {})
    boundary = summary.get("oac_boundary", {})
    mutations = result.get("mutation_rejections", {})

    def require(condition: bool, code: str) -> None:
        if not condition:
            failures.append(code)

    require(result.get("status") == "PASS", "VERIFIER_STATUS_INVALID")
    require(result.get("verification_scope") == "RETAINED_ARTIFACT"
            and result.get("current_release_qualified") is False,
            "RETAINED_VERIFICATION_SCOPE_INVALID")
    require(
        result.get("evidence_class") == "VALIDATED_CONTROLLED_LOCAL"
        and result.get("claim_ceiling")
        == "CONTROLLED_LOCAL_ADAPTATION_MECHANISM_ONLY"
        and result.get("failure_codes") == [],
        "CLAIM_BOUNDARY_INVALID",
    )
    require(
        manifest.get("status") == "CLOSED_WORLD"
        and isinstance(manifest.get("entry_count"), int)
        and manifest.get("entry_count") >= 20
        and len(manifest.get("entries", [])) == manifest.get("entry_count")
        and manifest.get("pack_digest") == result.get("manifest_pack_digest"),
        "MANIFEST_CLOSURE_INVALID",
    )
    require(
        evergreen.get("status") == "READY_FOR_ORGREBASE"
        and evergreen.get("mapping_count") == 5
        and evergreen.get("gap_count") == 0
        and evergreen.get("bound_execution_started") is False,
        "POSITIVE_JOURNEY_INVALID",
    )
    require(
        veracier.get("status") == "HOLD"
        and veracier.get("gap_count") == 7
        and veracier.get("capsule_produced") is False
        and veracier.get("execution_started") is False,
        "NEGATIVE_JOURNEY_INVALID",
    )
    require(
        result.get("oac_public_cli_checks") == 5
        and isinstance(mutations, dict)
        and len(mutations) == 6
        and result.get("real_review_wait_ms", 0) >= 4000
        and result.get("canonical_target_writes") == 0,
        "PUBLIC_CLI_MUTATION_REVIEW_OR_WRITE_PROOF_INVALID",
    )
    require(
        boundary.get("source_and_demand_validated") is True
        and boundary.get("source_admission_validated") is True
        and boundary.get("oac_plan_produced") is False
        and boundary.get("oac_plan_certificate_produced") is False
        and boundary.get("oac_runtime_invoked") is False,
        "OAC_P0_BOUNDARY_INVALID",
    )
    require(
        all(
            summary.get(key) == "NOT_RUN"
            for key in (
                "real_enterprise_connectors",
                "real_enterprise_data",
                "enterprise_uat",
                "production_sla_ha_dr",
            )
        ),
        "ENTERPRISE_CLAIM_BOUNDARY_INVALID",
    )
    unique_failures = sorted(set(failures), key=str.encode)
    return {
        "status": "PASS" if not unique_failures else "FAIL",
        "verification_scope": result.get("verification_scope"),
        "current_release_qualified": False,
        "oac_reader": result.get("oac_reader"),
        "failures": unique_failures,
        "evidence_class": result.get("evidence_class"),
        "claim_ceiling": result.get("claim_ceiling"),
        "entry_count": manifest.get("entry_count"),
        "pack_digest": manifest.get("pack_digest"),
        "evergreen_status": evergreen.get("status"),
        "veracier_status": veracier.get("status"),
        "veracier_gap_count": veracier.get("gap_count"),
        "real_review_wait_ms": result.get("real_review_wait_ms"),
        "oac_public_cli_checks": result.get("oac_public_cli_checks"),
        "mutation_rejections": len(mutations) if isinstance(mutations, dict) else 0,
        "canonical_target_writes": result.get("canonical_target_writes"),
        "oac_plan_produced": boundary.get("oac_plan_produced"),
        "oac_plan_certificate_produced": boundary.get("oac_plan_certificate_produced"),
        "oac_runtime_invoked": boundary.get("oac_runtime_invoked"),
        "bound_execution_started": evergreen.get("bound_execution_started"),
        "real_enterprise_validated": "NOT_RUN",
        "production_ready": False,
        "evidence_path": "evidence/oac-quote-adaptation/latest",
    }


def _oac_enterprise_adaptation_valid(root: Path) -> bool:
    return verify_oac_enterprise_adaptation(root)["status"] == "PASS"


def _load_product_path_evaluator(root: Path) -> ModuleType:
    path = root / "scripts" / "workspace_product_path_evaluator.py"
    spec = importlib.util.spec_from_file_location("orgrebase_product_path_evaluator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("ProductPath evaluator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rebuild_product_path_wheel(root: Path, retained_wheel: Path) -> tuple[bool, str | None]:
    if not retained_wheel.is_file():
        return False, "RETAINED_WHEEL_MISSING"
    with tempfile.TemporaryDirectory(prefix="orgrebase-gate-wheel-") as temporary:
        dist = Path(temporary) / "dist"
        completed = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(dist)],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return False, "WHEEL_REBUILD_FAILED"
        wheels = sorted(dist.glob("orgrebase-*.whl"))
        if len(wheels) != 1:
            return False, "WHEEL_REBUILD_SET_INVALID"
        rebuilt = hashlib.sha256(wheels[0].read_bytes()).hexdigest()
        retained = hashlib.sha256(retained_wheel.read_bytes()).hexdigest()
        if rebuilt != retained:
            return False, "WHEEL_REBUILD_DIGEST_MISMATCH"
    return True, None


CURRENT_PRODUCT_PATH_BENCHMARK = "ProductPath-v0.3-task-intake-bound"


def _current_product_path_release_failures(report: dict[str, Any]) -> list[str]:
    """Freeze the stable v0.3 release surface without pinning run-local digests."""

    failures: list[str] = []
    if report.get("schema_version") != "orgrebase.product-path-blackbox.v2":
        failures.append("CURRENT_REPORT_SCHEMA_INVALID")
    if report.get("benchmark_version") != CURRENT_PRODUCT_PATH_BENCHMARK:
        failures.append("CURRENT_BENCHMARK_VERSION_INVALID")
    if report.get("case_summary") != {"total": 12, "passed": 12, "failed": 0}:
        failures.append("CURRENT_CASE_SUMMARY_INVALID")
    mutation_summary = report.get("mutation_summary", {})
    if (
        mutation_summary.get("total") != 11
        or mutation_summary.get("killed") != 11
        or mutation_summary.get("survived") != 0
    ):
        failures.append("CURRENT_MUTATION_SUMMARY_INVALID")
    integrity_summary = report.get("integrity_attack_summary", {})
    if (
        integrity_summary.get("total") != 5
        or integrity_summary.get("rejected") != 5
        or integrity_summary.get("accepted") != 0
    ):
        failures.append("CURRENT_INTEGRITY_SUMMARY_INVALID")
    execution = report.get("execution", {})
    if (
        execution.get("uvicorn_processes_started") != 15
        or execution.get("real_process_restarts") != 3
    ):
        failures.append("CURRENT_PROCESS_RESTART_SCOPE_INVALID")
    independent = report.get("independent_evaluator", {})
    for field in ("task_intake_binding_scope", "approval_review_gate_scope"):
        value = independent.get(field)
        if not isinstance(value, str) or not value or value.startswith("NOT_APPLICABLE"):
            failures.append(f"CURRENT_{field.upper()}_INVALID")
    source_oracle = independent.get("source_bound_oracle", {})
    if (
        source_oracle.get("status") != "PASS"
        or source_oracle.get("component_root_count") != 5
        or source_oracle.get("runtime_projection") != {"matched": 5, "total": 5}
    ):
        failures.append("CURRENT_SOURCE_RUNTIME_SCOPE_INVALID")
    cases = {
        item.get("id"): item
        for item in report.get("cases", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    primary_facts = cases.get("PP-001", {}).get("facts", {})
    if "runtime_contract" not in report:
        expected_event_count = 12
    elif report["runtime_contract"] == "orgrebase.product-path-runtime-contract.v2":
        expected_event_count = 14
    else:
        expected_event_count = None
        failures.append("CURRENT_RUNTIME_CONTRACT_INVALID")
    if (
        primary_facts.get("event_count") != expected_event_count
        or primary_facts.get("task_intake_candidate_status") != "READY_FOR_CONFIRMATION"
        or primary_facts.get("task_intake_confirmation_status") != "ADMITTED_FOR_FORMATION"
        or primary_facts.get("task_intake_run_status") != "FORMATION_COMPLETED"
        or primary_facts.get("task_intake_same_run_id") is not True
        or primary_facts.get("task_intake_same_workspace_nonce") is not True
        or primary_facts.get("review_duration_ms") != 4000
        or primary_facts.get("review_gate_restart_enforced") is not True
    ):
        failures.append("CURRENT_PRIMARY_INTAKE_AND_REVIEW_SCOPE_INVALID")
    retired_route_facts = cases.get("PP-010", {}).get("facts", {})
    if (
        retired_route_facts.get("direct_form_status") != 410
        or retired_route_facts.get("direct_form_code") != "WORKSPACE_TASK_INTAKE_REQUIRED"
        or retired_route_facts.get("state_unchanged") is not True
    ):
        failures.append("CURRENT_DIRECT_FORM_RETIREMENT_INVALID")
    return failures


def verify_product_path_blackbox(
    root: Path,
    *,
    rebuild_wheel: bool = True,
    benchmark_version: str = CURRENT_PRODUCT_PATH_BENCHMARK,
    evidence_root: Path | None = None,
    retained_verifiers: bool = False,
) -> dict[str, Any]:
    """Replay a selected evidence bundle and optionally bind current wheel bytes.

    The default remains the checked-in release bundle with a strict source
    rebuild. An isolated development bundle can exercise the same checks;
    historical replay alone reports ``wheel_rebuild=NOT_RUN`` and does not
    qualify the current source tree for release.
    """

    if retained_verifiers:
        if rebuild_wheel or benchmark_version != CURRENT_PRODUCT_PATH_BENCHMARK:
            raise ValueError("retained verifier bytes are only for historical v0.3 replay without a current build claim")
        evidence = evidence_root if evidence_root is not None else root / "evidence/workspace/latest"
        try:
            with tempfile.TemporaryDirectory(prefix="orgrebase-retained-product-path-") as directory:
                isolated = Path(directory)
                _materialize_retained_product_path(root, evidence, isolated)
                result = verify_product_path_blackbox(
                    isolated, rebuild_wheel=False, benchmark_version=benchmark_version,
                )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result = {"status": "FAIL", "failures": [f"RETAINED_VERIFIER_MATERIAL_INVALID:{type(exc).__name__}"]}
        result.update(verification_scope="RETAINED_VERIFIER_BYTES", current_release_qualified=False)
        return result

    benchmark_roots = {
        "ProductPath-v0.1": "product-path-v0.1",
        "ProductPath-v0.2-source-bound": "product-path-v0.2-source-bound",
        "ProductPath-v0.3-task-intake-bound": "product-path-v0.3-task-intake-bound",
    }
    try:
        benchmark = root / "benchmark" / benchmark_roots[benchmark_version]
    except KeyError as exc:
        raise ValueError(f"unsupported ProductPath benchmark: {benchmark_version}") from exc
    evidence = evidence_root if evidence_root is not None else root / "evidence" / "workspace" / "latest"
    paths = {
        "report": evidence / "product-path-blackbox.json",
        "observations": evidence / "product-path-observations.json",
        "wheel": evidence / "product-path-wheel.whl",
        "artifact_manifest": evidence / "product-path-artifacts.json",
        "runner": root / "scripts" / "workspace_product_path_blackbox.py",
        "evaluator": root / "scripts" / "workspace_product_path_evaluator.py",
        "public_cases": benchmark / "public" / "cases.json",
        "gold": benchmark / "evaluator" / "gold.json",
        "mutations": benchmark / "evaluator" / "mutations.json",
        "benchmark_manifest": benchmark / "MANIFEST.sha256",
    }
    failures: list[str] = []
    payload: dict[str, Any] | None = None
    replay: dict[str, Any] | None = None
    gate_attacks: list[dict[str, str]] = []
    required = tuple(paths.values())
    missing = [path for path in required if not path.is_file()]
    if missing:
        return {
            "status": "FAIL",
            "failures": [
                f"MISSING:{path.relative_to(root) if path.is_relative_to(root) else path.name}"
                for path in missing
            ],
        }
    try:
        payload = _json(paths["report"])
        observations = _json(paths["observations"])
        gold = _json(paths["gold"])
        mutations = _json(paths["mutations"])
        checked_artifacts = _json(paths["artifact_manifest"])
        evaluator = _load_product_path_evaluator(root)
        if None in (payload, observations, gold, mutations, checked_artifacts):
            raise ValueError("ProductPath JSON object missing")
        expected_artifacts, artifact_paths = evaluator.build_artifact_manifest(
            project_root=root,
            runner_path=paths["runner"],
            evaluator_path=paths["evaluator"],
            observations_path=paths["observations"],
            wheel_path=paths["wheel"],
            public_cases_path=paths["public_cases"],
            gold_path=paths["gold"],
            mutations_path=paths["mutations"],
            benchmark_manifest_path=paths["benchmark_manifest"],
            benchmark_version=benchmark_version,
        )
        if checked_artifacts != expected_artifacts:
            failures.append("ARTIFACT_MANIFEST_REPLAY_MISMATCH")
        replay = evaluator.evaluate(
            observations=observations,
            gold=gold,
            mutations=mutations,
            evaluator_path=paths["evaluator"],
            gold_path=paths["gold"],
            mutations_path=paths["mutations"],
            manifest_path=paths["benchmark_manifest"],
            artifact_manifest=checked_artifacts,
            artifact_paths=artifact_paths,
            project_root=root,
        )
        if replay != payload:
            failures.append("EVALUATOR_REPLAY_MISMATCH")
        if replay.get("status") != "PASS":
            failures.append("EVALUATOR_REPLAY_FAILED")
        if benchmark_version == CURRENT_PRODUCT_PATH_BENCHMARK:
            failures.extend(_current_product_path_release_failures(replay))
        forged_report = copy.deepcopy(payload)
        forged_report["cases"] = []
        report_document = {key: value for key, value in forged_report.items() if key != "digest"}
        forged_report["digest"] = evaluator._rfc8785_digest(report_document)
        gate_attacks.append(
            {
                "id": "PP-GATE-ATTACK-CLEARED-SUMMARY",
                "status": "REJECTED" if forged_report != replay else "ACCEPTED",
            }
        )
        forged_artifacts = copy.deepcopy(checked_artifacts)
        runner_entry = next(
            entry for entry in forged_artifacts["entries"] if entry.get("id") == "runner_source"
        )
        runner_entry["sha256"] = "sha256:" + "f" * 64
        artifact_document = {key: value for key, value in forged_artifacts.items() if key != "digest"}
        forged_artifacts["digest"] = evaluator._rfc8785_digest(artifact_document)
        gate_attacks.append(
            {
                "id": "PP-GATE-ATTACK-FORGED-SOURCE-HASH",
                "status": ("REJECTED" if forged_artifacts != expected_artifacts else "ACCEPTED"),
            }
        )
        if any(attack["status"] != "REJECTED" for attack in gate_attacks):
            failures.append("GATE_NEGATIVE_REGRESSION_FAILED")
    except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
        failures.append(f"VERIFICATION_ERROR:{type(exc).__name__}")
    if rebuild_wheel:
        wheel_ok, wheel_failure = _rebuild_product_path_wheel(root, paths["wheel"])
        if not wheel_ok and wheel_failure is not None:
            failures.append(wheel_failure)
    return {
        "status": "PASS" if not failures else "FAIL",
        "benchmark_version": benchmark_version,
        "failures": sorted(set(failures)),
        "evaluator_replay": "PASS" if replay is not None and replay == payload else "FAIL",
        "wheel_rebuild": "PASS"
        if rebuild_wheel
        and "WHEEL_REBUILD_DIGEST_MISMATCH" not in failures
        and not any(
            failure.startswith("WHEEL_REBUILD_") or failure == "RETAINED_WHEEL_MISSING"
            for failure in failures
        )
        else ("NOT_RUN" if not rebuild_wheel else "FAIL"),
        "gate_attack_summary": {
            "total": len(gate_attacks),
            "rejected": sum(attack["status"] == "REJECTED" for attack in gate_attacks),
            "accepted": sum(attack["status"] == "ACCEPTED" for attack in gate_attacks),
        },
        "gate_attacks": gate_attacks,
        "report_digest": payload.get("digest") if isinstance(payload, dict) else None,
        "wheel_sha256": (
            f"sha256:{hashlib.sha256(paths['wheel'].read_bytes()).hexdigest()}"
            if paths["wheel"].is_file()
            else None
        ),
    }


def _product_path_blackbox_valid(root: Path) -> bool:
    return verify_product_path_blackbox(root)["status"] == "PASS"


def _materialize_retained_product_path(root: Path, evidence: Path, isolated: Path) -> None:
    """Stage checked historical inputs; the runner source is never executed."""

    manifest = _json(evidence / "product-path-artifacts.json")
    entries = {item["id"]: item for item in manifest["entries"]}
    retained = root / "benchmark/product-path-v0.3-task-intake-bound/retained-verifiers"
    sources = (
        ("runner_source", "workspace_product_path_blackbox.py"),
        ("evaluator_source", "workspace_product_path_evaluator.py"),
    )
    (isolated / "scripts").mkdir(parents=True)
    for identity, filename in sources:
        payload = (retained / filename).read_bytes()
        entry = entries[identity]
        if (entry["path"] != f"scripts/{filename}" or entry["size_bytes"] != len(payload)
                or entry["sha256"] != f"sha256:{hashlib.sha256(payload).hexdigest()}"):
            raise ValueError("retained source bytes differ from the frozen artifact identity")
        (isolated / "scripts" / filename).write_bytes(payload)
    for name in ("product-path-v0.1", "product-path-v0.2-source-bound", "product-path-v0.3-task-intake-bound"):
        shutil.copytree(root / "benchmark" / name, isolated / "benchmark" / name,
                        ignore=shutil.ignore_patterns("retained-verifiers", "__pycache__"))
    target = isolated / "evidence/workspace/latest"
    target.mkdir(parents=True)
    for filename in ("product-path-artifacts.json", "product-path-blackbox.json",
                     "product-path-observations.json", "product-path-wheel.whl"):
        shutil.copyfile(evidence / filename, target / filename)


def _receipt_valid(receipt: dict[str, Any] | None) -> bool:
    if not receipt or receipt.get("status") != "COMPLETED":
        return False
    try:
        OrgRebaseService.verify_receipt(receipt)
    except RuntimeError:
        return False
    return True


def _rollback_valid(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    plan = payload.get("plan", {})
    approval = payload.get("approval", {})
    receipt = payload.get("receipt", {})
    metrics = receipt.get("metrics", {})
    try:
        OrgRebaseService.verify_rollback_receipt(receipt)
    except RuntimeError:
        return False
    return bool(
        receipt.get("status") == "ROLLED_BACK_PENDING_REBASE"
        and receipt.get("authoritative_claim_unchanged") is True
        and metrics.get("authoritative_claims_changed") == 0
        and metrics.get("history_rows_deleted") == 0
        and metrics.get("work_items_pending_rebase", 0) >= 1
        and plan.get("target_status") == receipt.get("status")
        and approval.get("actor_id") != approval.get("apply_approver_id")
        and "rollback:downstream-compensation" in approval.get("authority_scope", [])
        and approval.get("rollback_plan_digest") == plan.get("digest")
        and all(item.get("status") == "SUCCEEDED" for item in receipt.get("compensation_results", []))
        and all(
            item.get("to_state") in {"ROLLED_BACK_PENDING_REBASE", "REQUALIFICATION_REQUIRED"}
            for item in receipt.get("transitions", [])
        )
    )


def _git_valid(payload: dict[str, Any] | None, run_id: str, nonce: str) -> bool:
    if not payload:
        return False
    patch = payload.get("patch", {})
    compensation = payload.get("compensation", {})
    patch_receipt = patch.get("receipt", {})
    compensation_receipt = compensation.get("receipt", {})
    patch_result = patch.get("result", {})
    compensation_result = compensation.get("result", {})
    verification = payload.get("verification", {})
    saga = payload.get("compensation_saga", {})
    return bool(
        payload.get("evidence_boundary") == "LOCAL_REAL_TOOL"
        and verification.get("status") == "PASS"
        and verification.get("worktree_clean") is True
        and verification.get("git_fsck") == "PASS"
        and COMMIT.fullmatch(str(verification.get("patch_commit", "")))
        and COMMIT.fullmatch(str(verification.get("compensation_commit", "")))
        and patch_receipt.get("workflow_run_id") == run_id
        and patch_receipt.get("run_nonce") == nonce
        and compensation_receipt.get("workflow_run_id") == run_id
        and compensation_receipt.get("run_nonce") == nonce
        and patch_receipt.get("evidence_class") == "LOCAL_REAL_TOOL"
        and compensation_receipt.get("evidence_class") == "LOCAL_REAL_TOOL"
        and compensation_receipt.get("compensation_ref") == patch_receipt.get("digest")
        and compensation_result.get("compensates_commit") == patch_result.get("commit")
        and verification.get("postcondition_digest") == patch_receipt.get("before_state_digest")
        and saga.get("status") == "ROLLED_BACK_PENDING_REBASE"
        and saga.get("workflow_run_id") == run_id
        and saga.get("run_nonce") == nonce
        and saga.get("git_patch_receipt_digest") == patch_receipt.get("digest")
        and saga.get("external_compensation", {}).get("receipt_digest") == compensation_receipt.get("digest")
        and saga.get("residual_effects") == []
        and saga.get("next_action") is None
    )


def _attributes(items: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("value"), dict):
            continue
        value = item["value"]
        encoded = next(iter(value.values()), None)
        result[str(item.get("key"))] = encoded
    return result


def _observability_valid(
    payload: dict[str, Any] | None,
    run_id: str,
    nonce: str,
    receipt_digest: str,
    demo: dict[str, Any] | None,
) -> bool:
    if not payload or not demo:
        return False
    conventions = payload.get("semantic_conventions", {})
    correlation = payload.get("correlation", {})
    privacy = payload.get("privacy", {})
    try:
        spans = payload["traces"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    except (KeyError, IndexError, TypeError):
        return False
    if not isinstance(spans, list):
        return False
    by_name = {span.get("name"): span for span in spans if isinstance(span, dict)}
    control = by_name.get("control.apply_rebase", {})
    control_id = control.get("spanId")
    agent_spans = [span for span in spans if str(span.get("name", "")).startswith("agent.run/")]
    tool_spans = [span for span in spans if str(span.get("name", "")).startswith("tool.call/")]
    every_bound = all(
        _attributes(span.get("attributes")).get("orgrebase.workflow.run_id") == run_id
        and _attributes(span.get("attributes")).get("orgrebase.run.nonce") == nonce
        for span in spans
    )
    try:
        plan = demo["collaboration"]["orchestration_plan"]
        tasks = plan["tasks"]
    except (KeyError, TypeError):
        return False
    task_by_id = {task["id"]: task for task in tasks}
    span_by_agent = {str(span.get("name", "")).removeprefix("agent.run/"): span for span in agent_spans}
    agent_by_task = {task["id"]: task["agent_name"] for task in tasks}
    orchestration_bound = True
    for task in tasks:
        span = span_by_agent.get(task["agent_name"], {})
        attributes = _attributes(span.get("attributes"))
        dependencies = task.get("depends_on", [])
        expected_parent = (
            span_by_agent[agent_by_task[dependencies[0]]].get("spanId") if dependencies else control_id
        )
        expected_links = {
            span_by_agent[agent_by_task[dependency]].get("spanId") for dependency in dependencies[1:]
        }
        actual_links = {
            link.get("spanId")
            for link in span.get("links", [])
            if _attributes(link.get("attributes")).get("orgrebase.link.kind") == "orchestration_predecessor"
        }
        orchestration_bound = orchestration_bound and bool(
            span.get("parentSpanId") == expected_parent
            and actual_links == expected_links
            and attributes.get("orgrebase.orchestration.plan.digest") == plan.get("digest")
            and attributes.get("orgrebase.orchestration.task.digest") == task.get("digest")
            and attributes.get("orgrebase.orchestration.predecessor.count") == str(len(dependencies))
        )
    standard_operations = all(
        _attributes(span.get("attributes")).get("gen_ai.operation.name") == "invoke_agent"
        for span in agent_spans
    ) and all(
        _attributes(span.get("attributes")).get("gen_ai.operation.name") == "execute_tool"
        for span in tool_spans
    )
    return bool(
        conventions.get("release") == "opentelemetry-semantic-conventions@v1.43.0"
        and conventions.get("source_commit") == "89aae43"
        and correlation.get("workflow_run_id") == run_id
        and correlation.get("run_nonce") == nonce
        and correlation.get("receipt_digest") == receipt_digest
        and privacy.get("content_capture") is False
        and privacy.get("prompt_capture") is False
        and privacy.get("output_capture") is False
        and len(agent_spans) == 5
        and len(tool_spans) >= 1
        and control_id
        and not control.get("parentSpanId")
        and set(span_by_agent) == {task["agent_name"] for task in task_by_id.values()}
        and orchestration_bound
        and standard_operations
        and every_bound
    )


def _live_valid(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    evidence = payload.get("evidence", {})
    agentteams = evidence.get("agentteams", {})
    kubernetes = evidence.get("kubernetes", {})
    matrix = evidence.get("matrix", {})
    artifacts = evidence.get("artifacts", {})
    models = evidence.get("model_calls", {})
    return bool(
        payload.get("schema_version") == "orgrebase.agentteams-live-evidence.v2"
        and payload.get("status") == "PASS"
        and payload.get("evidence_class") == "LIVE_AGENTTEAMS"
        and sha256_digest(evidence) == payload.get("receipt_digest")
        and isinstance(evidence.get("run_id"), str)
        and evidence["run_id"].startswith("run:orgrebase:live:")
        and re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("nonce", "")))
        and agentteams.get("version") == "v1.2.2"
        and agentteams.get("source_commit") == AGENTTEAMS_SOURCE_COMMIT
        and len(kubernetes.get("workers", [])) == 5
        and len(kubernetes.get("pods", [])) >= 3
        and len(matrix.get("candidate_senders", [])) >= 3
        and len(set(matrix.get("candidate_senders", []))) >= 3
        and re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(matrix.get("orchestration_plan_digest", "")),
        )
        and len(matrix.get("candidate_task_bindings", [])) >= 3
        and artifacts.get("skill", {}).get("loaded_event_id")
        and len(artifacts.get("candidate_artifacts", [])) >= 3
        and models.get("successful_calls", 0) >= 3
        and len(set(models.get("successful_workers", []))) >= 3
        and len(set(models.get("provider_request_ids", []))) >= 3
        and len(models.get("candidate_bindings", [])) >= 3
        and evidence.get("source_bundle_digest")
    )


def _minimal_certificate_valid(
    payload: dict[str, Any] | None,
    receipt: dict[str, Any] | None,
) -> bool:
    if not payload or not receipt:
        return False
    service = OrgRebaseService()
    try:
        verification = service.verify_minimal_rebase_certificate(payload)
    except RuntimeError:
        return False
    finally:
        service.store.close()
    return bool(
        verification.get("status") == "PASS"
        and receipt.get("minimal_rebase_certificate_digest") == payload.get("digest")
    )


def _candidate_ingestion_valid(
    payload: dict[str, Any] | None,
    live: dict[str, Any] | None,
    demo: dict[str, Any] | None,
) -> bool:
    if not payload or not live or not demo:
        return False
    try:
        receipt = AgentCandidateIngestionReceipt.model_validate(payload)
    except ValueError:
        return False
    live_digests = {
        item.get("digest")
        for item in live.get("evidence", {}).get("artifacts", {}).get("candidate_artifacts", [])
        if item.get("ref") != "result:summary"
    }
    decision_digests = {item.artifact_digest for item in receipt.decisions}
    return bool(
        receipt.live_receipt_digest == live.get("receipt_digest")
        and receipt.change_set_digest == demo.get("change_set", {}).get("digest")
        and receipt.preview_digest == demo.get("preview", {}).get("digest")
        and receipt.orchestration_plan_digest
        == demo.get("collaboration", {}).get("orchestration_plan", {}).get("digest")
        and live.get("evidence", {}).get("matrix", {}).get("orchestration_plan_digest")
        == receipt.orchestration_plan_digest
        and decision_digests == live_digests
        and receipt.target_writes == 0
        and all(not item.admitted_effects for item in receipt.decisions)
    )


def _orchestration_valid(demo: dict[str, Any] | None) -> bool:
    if not demo:
        return False
    try:
        collaboration = demo["collaboration"]
        plan = OrchestrationPlan.model_validate(collaboration["orchestration_plan"])
        handoffs = tuple(StructuredHandoff.model_validate(item) for item in collaboration["handoffs"])
        runs = tuple(AgentRun.model_validate(item) for item in collaboration["agent_runs"])
        envelope = RunEnvelope.model_validate(collaboration["run_envelope"])
        stored = CoordinationReceipt.model_validate(collaboration["coordination_receipt"])
        compiler = OrchestrationCompiler(load_fixture())
        verified = compiler.verify_execution(
            plan=plan,
            handoffs=handoffs,
            runs=runs,
            run_envelope=envelope,
        )
        ingestion = AgentCandidateIngestionReceipt.model_validate(collaboration["candidate_ingestion"])
        change_set = ChangeSetRevision.model_validate(demo["change_set"])
        preview = ImpactPreview.model_validate(demo["preview"])
        recomputed = ingest_local_proposals(
            change_set=change_set,
            preview=preview,
            plan=plan,
            handoffs=handoffs,
            coordination_receipt=stored,
            run_envelope=envelope,
        )
        compilation = CompilationReceipt.model_validate(collaboration["compilation_receipt"])
        recomputed_compilation = compiler.compilation_receipt(plan, change_set, preview)
    except (KeyError, TypeError, ValueError, RuntimeError):
        return False
    return bool(
        verified.digest == stored.digest
        and stored.status == "PASS"
        and len(plan.tasks) == 5
        and len(handoffs) == 5
        and len(runs) == 5
        and ingestion.digest == recomputed.digest
        and ingestion.target_writes == 0
        and not ingestion.rejected_candidate_digests
        and all(not item.admitted_effects for item in ingestion.decisions)
        and demo.get("receipt", {}).get("candidate_ingestion_digest") == ingestion.digest
        and compilation.digest == recomputed_compilation.digest
        and compilation.orchestration_plan_digest == plan.digest
        and demo.get("receipt", {}).get("compilation_receipt_digest") == compilation.digest
    )


def _proof_pack_valid(path: Path) -> bool:
    if not path.is_file():
        return False
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("verify_proof_pack.py")),
            "--root",
            str(ROOT),
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def _skill_governance_valid(demo: dict[str, Any] | None) -> bool:
    if not demo:
        return False
    try:
        report = QualificationReport.model_validate(demo["receipt"]["qualification_report"])
        contract = load_skill_contract()
        fixture = load_fixture()
        candidate = next(score for score in report.scores if score.version == "1.3")
        baselines = tuple(score for score in report.scores if score.version != "1.3")
    except (KeyError, StopIteration, TypeError, ValueError, RuntimeError):
        return False
    return bool(
        report.skill_contract_digest == contract.get("content_digest")
        and report.evaluation_set_digest == sha256_digest(fixture.evaluation_cases)
        and report.candidate_adapter_version == "enterprise-launch-readiness@1.3"
        and report.candidate_state.value == "CANARY"
        and candidate.outcome == "PASS"
        and candidate.passed == candidate.total
        and all(score.outcome == "FAIL" for score in baselines)
        and all(
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(item.get("action_candidate_digest", "")),
            )
            for item in candidate.case_results
        )
        and contract.get("permissions", {}).get("side_effects") == []
    )


def evaluate(stage: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def project_path(path: Path) -> str:
        return path.relative_to(ROOT).as_posix()

    def require(check_id: str, passed: bool, evidence: str, *, verification: dict[str, Any] | None = None) -> None:
        check = {"id": check_id, "status": "PASS" if passed else "FAIL", "evidence": evidence}
        if verification is not None:
            check.update(verification_scope=verification["verification_scope"],
                         current_release_qualified=verification["current_release_qualified"],
                         verification=verification)
        checks.append(check)

    intro_path = ROOT / "submission" / "INTRO.md"
    intro = intro_path.read_text(encoding="utf-8").strip() if intro_path.exists() else ""
    intro_body = "\n".join(line for line in intro.splitlines() if not line.startswith("#")).strip()
    require(
        "preliminary.intro",
        bool(intro_body) and len(intro_body) <= 500,
        project_path(intro_path),
    )
    decks = sorted((ROOT / "submission").glob("*.pptx")) + sorted((ROOT / "submission").glob("*.pdf"))
    require(
        "preliminary.deck",
        any(path.stat().st_size > 0 for path in decks),
        ", ".join(project_path(path) for path in decks) or "missing",
    )
    identity_path = ROOT / "submission" / "AGENT-IDENTITIES.md"
    identity_text = identity_path.read_text(encoding="utf-8") if identity_path.exists() else ""
    identity_names = {path.stem for path in (ROOT / "agentteams" / "identities").glob("*.json")}
    require(
        "preliminary.agent_identity_list",
        len(identity_names) == 5 and all(f"`{name}`" in identity_text for name in identity_names),
        project_path(identity_path),
    )
    require("opensource.license", (ROOT / "LICENSE").is_file(), "LICENSE")
    require("opensource.readme", (ROOT / "README.md").is_file(), "README.md")

    if stage in {"semifinal", "final"}:
        manifest_path = ROOT / "evidence" / "latest" / "manifest.json"
        manifest_ok, manifest = _manifest_valid(manifest_path)
        require("semifinal.evidence_manifest", manifest_ok, project_path(manifest_path))
        run_id = str((manifest or {}).get("workflow_run_id", ""))
        nonce = str((manifest or {}).get("run_nonce", ""))
        receipt = _json(ROOT / "evidence" / "latest" / "rebase-receipt.json")
        demo = _json(ROOT / "evidence" / "latest" / "demo.json")
        require(
            "semifinal.executable_demo",
            _receipt_valid(receipt),
            "evidence/latest/rebase-receipt.json",
        )
        require(
            "semifinal.authority_aware_agent_orchestration",
            _orchestration_valid(demo),
            "evidence/latest/demo.json#collaboration",
        )
        require(
            "semifinal.governed_skill_lifecycle",
            _skill_governance_valid(demo),
            "evidence/latest/demo.json#receipt.qualification_report",
        )
        minimal_path = ROOT / "evidence" / "latest" / "minimal-rebase-certificate.json"
        require(
            "semifinal.proof_carrying_minimal_rebase",
            _minimal_certificate_valid(_json(minimal_path), receipt),
            project_path(minimal_path),
        )
        require(
            "semifinal.rollback_semantics",
            _rollback_valid(_json(ROOT / "evidence" / "latest" / "rollback-evidence.json")),
            "evidence/latest/rollback-evidence.json",
        )
        require(
            "semifinal.real_reversible_tool",
            _git_valid(
                _json(ROOT / "evidence" / "latest" / "git-tool-evidence.json"),
                run_id,
                nonce,
            ),
            "evidence/latest/git-tool-evidence.json",
        )
        require(
            "semifinal.correlated_observability",
            _observability_valid(
                _json(ROOT / "evidence" / "latest" / "observability.json"),
                run_id,
                nonce,
                str((receipt or {}).get("digest", "")),
                demo,
            ),
            "evidence/latest/observability.json",
        )
        oac_admission_path = ROOT / "evidence" / "oac-bridge" / "latest"
        oac_admission = verify_retained_oac_runtime_admission(ROOT)
        require(
            "semifinal.oac_local_runtime_admission",
            oac_admission["status"] == "PASS",
            project_path(oac_admission_path / "evidence-index.json"),
            verification=oac_admission,
        )
        oac_evolution_path = ROOT / "evidence" / "oac-evolution" / "latest"
        require(
            "semifinal.oac_governed_evolution",
            verify_oac_governed_evolution(ROOT)["status"] == "PASS",
            project_path(oac_evolution_path / "evidence-index.json"),
        )
        oac_adaptation_path = (
            ROOT / "evidence" / "oac-quote-adaptation" / "latest" / "manifest.json"
        )
        require(
            "semifinal.oac_enterprise_adaptation",
            _oac_enterprise_adaptation_valid(ROOT),
            project_path(oac_adaptation_path),
        )
        product_path = ROOT / "evidence" / "workspace" / "latest" / "product-path-blackbox.json"
        require(
            "semifinal.product_path_blackbox",
            _product_path_blackbox_valid(ROOT),
            project_path(product_path),
        )
        quote_value = ROOT / "evidence" / "quote-value" / "latest" / "evidence-index.json"
        require(
            "semifinal.quote_value_evidence",
            _quote_value_evidence_valid(ROOT),
            project_path(quote_value),
        )
        integrated_closure = (
            ROOT / "evidence" / "semifinal-closure" / "latest" / "evidence-index.json"
        )
        require(
            "semifinal.integrated_candidate_closure",
            _integrated_semifinal_closure_valid(ROOT),
            project_path(integrated_closure),
        )
        agentteams_config_path = ROOT / "configs" / "goai-agentteams-demo.json"
        agentteams_config = _json(agentteams_config_path)
        historical_transport: dict[str, Any] | None = None
        fresh_core: dict[str, Any] | None = None
        try:
            if agentteams_config is not None:
                historical_transport, fresh_core = load_agentteams_evidence(
                    config=agentteams_config, project_root=ROOT
                )
        except (KeyError, TypeError, ValueError):
            historical_transport = None
            fresh_core = None
        historical_path = ROOT / EXPECTED_PUBLIC_TRANSPORT_FIXTURE
        require(
            "semifinal.historical_agentteams_transport",
            historical_transport is not None
            and {key: historical_transport.get(key) for key in HISTORICAL_TRANSPORT_CLASSIFICATION}
            == HISTORICAL_TRANSPORT_CLASSIFICATION,
            project_path(historical_path),
        )
        live_path = ROOT / EXPECTED_FRESH_CORE_EVIDENCE["live_receipt"]
        live = _json(live_path)
        require(
            "semifinal.core_change_advisory_live_agentteams",
            fresh_core is not None
            and {key: fresh_core.get(key) for key in FRESH_CORE_CLASSIFICATION} == FRESH_CORE_CLASSIFICATION
            and _live_valid(live),
            project_path(live_path),
        )
        candidate_path = ROOT / EXPECTED_FRESH_CORE_EVIDENCE["semantic_ingestion"]
        require(
            "semifinal.agent_candidate_control_plane_ingestion",
            fresh_core is not None
            and fresh_core.get("semantic_ingestion", {}).get("advisory_accepted") == 4
            and fresh_core.get("semantic_ingestion", {}).get("rejected") == 0
            and fresh_core.get("semantic_ingestion", {}).get("target_writes") == 0
            and _candidate_ingestion_valid(_json(candidate_path), live, demo),
            project_path(candidate_path),
        )
        proof_pack_path = ROOT / "evidence" / "latest" / "proof-pack.json"
        require(
            "semifinal.structural_proof_pack",
            _proof_pack_valid(proof_pack_path),
            project_path(proof_pack_path),
        )
        videos = tuple((ROOT / "submission").glob("demo.*"))
        require(
            "semifinal.demo_video",
            any(
                path.suffix.lower() in {".mp4", ".mov", ".webm"} and path.stat().st_size > 0
                for path in videos
            ),
            "submission/demo.{mp4,mov,webm}",
        )

    if stage == "final":
        for name in ("CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md"):
            require(f"final.{name.lower()}", (ROOT / name).is_file(), name)
        require(
            "final.workspace_product_runbook",
            (ROOT / "docs" / "WORKSPACE-DEMO-RUNBOOK.md").is_file(),
            "docs/WORKSPACE-DEMO-RUNBOOK.md",
        )
        require(
            "final.core_live_agentteams_runbook",
            (ROOT / "agentteams" / "LIVE-RUNBOOK.md").is_file(),
            "agentteams/LIVE-RUNBOOK.md",
        )

    failures = [item for item in checks if item["status"] == "FAIL"]
    return {
        "schema_version": "orgrebase.goai-gate.v2",
        "stage": stage,
        "decision": "GO" if not failures else "NO_GO",
        "checks": checks,
        "blocking_failures": [item["id"] for item in failures],
        "claim_boundary": (
            "This gate verifies submitted artifacts and evidence bindings; it does not "
            "predict the judges' decision."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("preliminary", "semifinal", "final"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-go", action="store_true")
    parser.add_argument("--verify-product-path-only", action="store_true")
    args = parser.parse_args()
    if args.verify_product_path_only:
        result = verify_product_path_blackbox(ROOT)
        encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded, encoding="utf-8")
        print(encoded, end="")
        if result["status"] != "PASS":
            raise SystemExit(2)
        return
    if args.stage is None:
        parser.error("--stage is required unless --verify-product-path-only is used")
    result = evaluate(args.stage)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    if args.require_go and result["decision"] != "GO":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
