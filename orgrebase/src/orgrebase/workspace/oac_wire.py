"""OAC public-CLI adapter and independent RFC 8785 wire helpers.

Nothing here imports sibling OAC implementation code. The published CLI is the
only compiler/lowerer boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from contextlib import suppress
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import rfc8785

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.models import OACRuntimeCapsule

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POLICY_PATH = PROJECT_ROOT / "configs" / "oac" / "runtime-admission-policy.json"

CAPSULE_MEDIA_TYPE = "application/vnd.orgrebase.oac-runtime-capsule+json"
PREVIEW_MEDIA_TYPE = "application/vnd.orgrebase.oac-formation-preview+json"
APPROVAL_MEDIA_TYPE = "application/vnd.orgrebase.oac-runtime-approval+json"
FORMATION_MEDIA_TYPE = "application/vnd.orgrebase.oac-formation-control-record+json"
RECEIPT_MEDIA_TYPE = "application/vnd.orgrebase.oac-runtime-admission-receipt+json"

_ZERO_EFFECT = "zero_effect"
_ZERO_DIGEST = "sha256:" + "0" * 64
_LOWERING_PROFILE = "oac.runtime-lowering/zero-effect/v0.1"
_OBLIGATION_PROJECTION = "oac.obligation-contract/topology-free/v0.1"
_OAC_API_VERSION = "oac.dev/v0alpha1"
_OAC_ROOT_ENV = "ORGREBASE_OAC_ROOT"
_OAC_PYTHON_ENV = "ORGREBASE_OAC_PYTHON"
_OAC_PROBE_TIMEOUT_SECONDS = 10.0
_OAC_COMMAND_TIMEOUT_SECONDS = 60.0
_OAC_SOURCE_MANIFEST_SCHEMA = "orgrebase.oac-public-source-manifest.v1"
_SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RFC3339_UTC_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
_OAC_PUBLIC_SOURCE_PATHS = (
    "pyproject.toml",
    "uv.lock",
    "schemas/OrganizationalDemand.schema.json",
    "schemas/OutcomeCertificate.schema.json",
    "schemas/SourceAdmissionReceipt.schema.json",
    "schemas/evolution-semantic-validation-rules.json",
    "schemas/kind-registry.json",
    "experiments/plan-verification-portability/v0.1-seed-1/artifacts/SC-008/SC-008.change.json",
    "experiments/plan-verification-portability/v0.1-seed-1/artifacts/SC-008/veracier-proc01-contextual.snapshot.json",
    "experiments/plan-verification-portability/v0.1-seed-1/artifacts/plans/PV-POS-SC008-SPLIT.plan.json",
    "src/oac/__init__.py",
    "src/oac/applicability.py",
    "src/oac/benchmark.py",
    "src/oac/canonical.py",
    "src/oac/cli.py",
    "src/oac/closure_micro.py",
    "src/oac/compiler.py",
    "src/oac/ctk_adapter.py",
    "src/oac/ctk_adapter_v2.py",
    "src/oac/derivation.py",
    "src/oac/evolution.py",
    "src/oac/identifiers.py",
    "src/oac/json_types.py",
    "src/oac/lowering.py",
    "src/oac/models.py",
    "src/oac/registry.py",
    "src/oac/resource_profile.py",
    "src/oac/sealed.py",
    "src/oac/supplier.py",
    "src/oac/text.py",
    "src/oac/verifier.py",
    "src/oac/witness.py",
    "tck/fixtures/evolution/root-vector.json",
    "src/oac/__main__.py",
    "src/oac/annotation.py",
    "src/oac/annotation_models.py",
    "src/oac/change_profiles.py",
    "src/oac/enterprise_intake.py",
    "src/oac/evolution_roots.py",
    "src/oac/intake_state.py",
    "src/oac/outcome_profiles.py",
    "profiles/change-profiles/retail-cancellation-review-v0.1/profile.json",
    "profiles/outcome-profiles/disposable-local-v0.1/profile.json",
    "schemas/change-profile-semantic-validation-rules.json",
    "schemas/outcome-profile-semantic-validation-rules.json",
)
_CHECKS = (
    "OAC_PUBLIC_SOURCE_COMMITMENT",
    "OAC_API_AND_AUTHORITY_ENVELOPE",
    "OAC_DETACHED_DIGESTS",
    "OAC_ROOT_REFERENCE_CLOSURE",
    "OAC_ACCEPT_CERTIFICATE_BINDING",
    "OAC_LOWERING_PRODUCED_NOT_EXECUTED",
    "ZERO_EFFECT_ZERO_TARGET_WRITES",
    "RUNTIME_ADMISSION_REQUIRED",
    "CANONICAL_STEP_ORDER_AND_DAG",
    "ROLE_PRINCIPAL_RUNTIME_SUBJECT_SEPARATION",
    "EXACT_CAPABILITY_COVERAGE",
    "EXACT_HANDLER_AND_EVIDENCE_COVERAGE",
    "RUNTIME_POLICY_ALLOWLIST",
    "RUNTIME_OWNER_MAPPING",
)


def _dict(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IntegrityError(code)
    return cast(dict[str, Any], value)


def _list(value: object, code: str) -> list[Any]:
    if not isinstance(value, list):
        raise IntegrityError(code)
    return value


def _str(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise IntegrityError(code)
    return value


def _sorted(values: Sequence[str] | set[str]) -> tuple[str, ...]:
    return tuple(sorted(values, key=str.encode))


def _jcs_digest(value: Mapping[str, Any]) -> str:
    try:
        payload = rfc8785.dumps(dict(value))
    except (rfc8785.CanonicalizationError, UnicodeError, TypeError, ValueError) as exc:
        raise IntegrityError("OAC_NON_I_JSON") from exc
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _oac_projection(resource: Mapping[str, Any]) -> dict[str, Any]:
    """Exclude only the detached digest; optional members remain as received."""
    value = deepcopy(dict(resource))
    value.pop("digest", None)
    return value


def _verify_oac_resource(resource: Mapping[str, Any], expected_kind: str) -> None:
    if resource.get("apiVersion") != _OAC_API_VERSION:
        raise IntegrityError(f"OAC_API_VERSION_MISMATCH:{expected_kind}")
    if resource.get("kind") != expected_kind:
        raise IntegrityError(f"OAC_KIND_MISMATCH:{expected_kind}")
    if set(resource) != {"apiVersion", "kind", "metadata", "spec", "digest"}:
        raise IntegrityError(f"OAC_RESOURCE_ENVELOPE_INVALID:{expected_kind}")
    metadata = _dict(resource.get("metadata"), "OAC_METADATA_INVALID")
    required_metadata = {
        "createdAt",
        "governanceRef",
        "id",
        "namespace",
        "ownerRef",
        "revision",
        "sourceRefs",
    }
    allowed_metadata = required_metadata | {"effectiveFrom", "effectiveTo"}
    if not required_metadata <= set(metadata) or not set(metadata) <= allowed_metadata:
        raise IntegrityError(f"OAC_AUTHORITY_ENVELOPE_INVALID:{expected_kind}")
    for field in ("id", "namespace", "ownerRef", "governanceRef"):
        _str(metadata.get(field), f"OAC_AUTHORITY_ENVELOPE_INVALID:{expected_kind}")
    created_at = metadata.get("createdAt")
    if not isinstance(created_at, str) or _RFC3339_UTC_PATTERN.fullmatch(created_at) is None:
        raise IntegrityError(f"OAC_AUTHORITY_ENVELOPE_INVALID:{expected_kind}")
    if not isinstance(metadata.get("revision"), int) or metadata["revision"] < 1:
        raise IntegrityError(f"OAC_AUTHORITY_ENVELOPE_INVALID:{expected_kind}")
    source_refs = _list(metadata.get("sourceRefs"), f"OAC_AUTHORITY_ENVELOPE_INVALID:{expected_kind}")
    if any(not isinstance(value, str) or not value for value in source_refs):
        raise IntegrityError(f"OAC_AUTHORITY_ENVELOPE_INVALID:{expected_kind}")
    digest = resource.get("digest")
    if not isinstance(digest, str) or digest != _jcs_digest(_oac_projection(resource)):
        raise IntegrityError(f"OAC_DIGEST_MISMATCH:{expected_kind}")


def _resource_ref(resource: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _dict(resource.get("metadata"), "OAC_METADATA_INVALID")
    return {
        "apiVersion": resource.get("apiVersion"),
        "kind": resource.get("kind"),
        "namespace": metadata.get("namespace"),
        "resourceId": metadata.get("id"),
        "revision": metadata.get("revision"),
        "digest": resource.get("digest"),
    }


def _require_ref(actual: object, resource: Mapping[str, Any], label: str) -> None:
    if actual != _resource_ref(resource):
        raise IntegrityError(f"OAC_REFERENCE_MISMATCH:{label}")


def _require_sources(resource: Mapping[str, Any], roots: Sequence[Mapping[str, Any]]) -> None:
    metadata = _dict(resource.get("metadata"), "OAC_METADATA_INVALID")
    expected = [_dict(item.get("metadata"), "OAC_METADATA_INVALID")["id"] for item in roots]
    if metadata.get("sourceRefs") != expected:
        raise IntegrityError(f"OAC_SOURCE_REFS_MISMATCH:{resource.get('kind')}")


def _topological_order(plan: Mapping[str, Any]) -> tuple[str, ...]:
    spec = _dict(plan.get("spec"), "OAC_PLAN_SPEC_INVALID")
    work_units = _list(spec.get("workUnits"), "OAC_WORK_UNITS_INVALID")
    work_refs = [
        _str(_dict(item, "OAC_WORK_UNIT_INVALID").get("workUnitId"), "OAC_WORK_ID_INVALID")
        for item in work_units
    ]
    if len(work_refs) != len(set(work_refs)):
        raise IntegrityError("OAC_WORK_UNIT_DUPLICATE")
    predecessors = {ref: set() for ref in work_refs}
    edges_seen: set[tuple[str, str]] = set()
    for raw_edge in _list(spec.get("happensBefore"), "OAC_DAG_INVALID"):
        edge = _dict(raw_edge, "OAC_DAG_EDGE_INVALID")
        before = _str(edge.get("predecessorRef"), "OAC_DAG_EDGE_INVALID")
        after = _str(edge.get("successorRef"), "OAC_DAG_EDGE_INVALID")
        if before not in predecessors or after not in predecessors or before == after:
            raise IntegrityError("OAC_DAG_EDGE_INVALID")
        if (before, after) in edges_seen:
            raise IntegrityError("OAC_DAG_EDGE_DUPLICATE")
        edges_seen.add((before, after))
        predecessors[after].add(before)
    emitted: list[str] = []
    emitted_set: set[str] = set()
    while len(emitted) < len(work_refs):
        ready = sorted(
            (ref for ref in work_refs if ref not in emitted_set and predecessors[ref] <= emitted_set),
            key=str.encode,
        )
        if not ready:
            raise IntegrityError("OAC_DAG_CYCLE")
        emitted.append(ready[0])
        emitted_set.add(ready[0])
    return tuple(emitted)


def _topology_digest(plan: Mapping[str, Any]) -> str:
    spec = _dict(plan.get("spec"), "OAC_PLAN_SPEC_INVALID")
    roles = []
    for raw in _list(spec.get("roleInstances"), "OAC_ROLE_INSTANCES_INVALID"):
        item = _dict(raw, "OAC_ROLE_INSTANCE_INVALID")
        roles.append(
            {
                "roleInstanceId": item.get("roleInstanceId"),
                "roleDefinitionRef": item.get("roleDefinitionRef"),
                "principalRef": item.get("principalRef"),
                "mission": item.get("mission"),
                "obligationRefs": list(_sorted(set(item.get("obligationRefs", [])))),
                "qualificationRefs": list(_sorted(set(item.get("qualificationRefs", [])))),
                "effectCeiling": item.get("effectCeiling"),
            }
        )
    work_units = []
    for raw in _list(spec.get("workUnits"), "OAC_WORK_UNITS_INVALID"):
        item = _dict(raw, "OAC_WORK_UNIT_INVALID")
        work_units.append(
            {
                "workUnitId": item.get("workUnitId"),
                "roleInstanceRefs": list(_sorted(set(item.get("roleInstanceRefs", [])))),
                "accountableRoleInstanceRef": item.get("accountableRoleInstanceRef"),
                "obligationRefs": list(_sorted(set(item.get("obligationRefs", [])))),
                "evidenceOutputs": list(_sorted(set(item.get("evidenceOutputs", [])))),
                "effectCeiling": item.get("effectCeiling"),
            }
        )
    edges = []
    for raw in _list(spec.get("happensBefore"), "OAC_DAG_INVALID"):
        item = _dict(raw, "OAC_DAG_EDGE_INVALID")
        edges.append(
            {
                "predecessorRef": item.get("predecessorRef"),
                "successorRef": item.get("successorRef"),
                "relation": item.get("relation"),
                "reasonRefs": list(_sorted(set(item.get("reasonRefs", [])))),
            }
        )
    return _jcs_digest(
        {
            "roleInstances": sorted(roles, key=lambda item: str(item["roleInstanceId"]).encode()),
            "workUnits": sorted(work_units, key=lambda item: str(item["workUnitId"]).encode()),
            "happensBefore": sorted(
                edges,
                key=lambda item: (
                    str(item["predecessorRef"]).encode(),
                    str(item["successorRef"]).encode(),
                ),
            ),
        }
    )


def _obligation_contract_digest(
    snapshot: Mapping[str, Any], change: Mapping[str, Any], plan: Mapping[str, Any]
) -> str:
    spec = _dict(plan.get("spec"), "OAC_PLAN_SPEC_INVALID")
    obligations = sorted(
        (
            deepcopy(_dict(item, "OAC_OBLIGATION_INVALID"))
            for item in _list(spec.get("obligations"), "OAC_OBLIGATIONS_INVALID")
        ),
        key=lambda item: str(item.get("obligationId")).encode(),
    )
    return _jcs_digest(
        {
            "projection": _OBLIGATION_PROJECTION,
            "snapshotDigest": snapshot.get("digest"),
            "changeDigest": change.get("digest"),
            "obligations": obligations,
        }
    )


def locate_oac_root(explicit: str | Path | None = None) -> Path:
    environment = os.environ.get(_OAC_ROOT_ENV)
    if explicit is not None:
        candidates = [Path(explicit)]
        selected_by = "--oac-root"
    elif environment:
        candidates = [Path(environment)]
        selected_by = _OAC_ROOT_ENV
    else:
        candidates = [
            PROJECT_ROOT.parent / "oac-spec",
            Path.cwd().parent / "oac-spec",
            Path.cwd() / "oac-spec",
        ]
        selected_by = "sibling discovery"
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if (resolved / "pyproject.toml").is_file() and (resolved / "src" / "oac").is_dir():
            return resolved
    attempted = ", ".join(str(candidate.expanduser().resolve()) for candidate in candidates)
    raise FileNotFoundError(
        "OAC_SPEC_CHECKOUT_NOT_FOUND: OrgRebase does not bundle OAC; expected the final "
        "combined snapshot layout 'orgrebase/' + sibling 'oac-spec/', or pass --oac-root "
        f"PATH / set {_OAC_ROOT_ENV}. selected_by={selected_by}; attempted={attempted}"
    )


def build_oac_public_source_manifest(root: Path) -> tuple[dict[str, Any], str]:
    entries: list[dict[str, Any]] = []
    for relative in _OAC_PUBLIC_SOURCE_PATHS:
        path = (root / relative).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            raise FileNotFoundError(f"OAC_PUBLIC_SOURCE_FILE_MISSING:{relative}")
        payload = path.read_bytes()
        entries.append(
            {
                "path": relative,
                "sha256": f"sha256:{hashlib.sha256(payload).hexdigest()}",
                "size": len(payload),
            }
        )
    manifest = {
        "schema_version": _OAC_SOURCE_MANIFEST_SCHEMA,
        "files": entries,
    }
    return manifest, sha256_digest(manifest)


def load_runtime_policy(path: str | Path = DEFAULT_POLICY_PATH) -> tuple[dict[str, Any], str]:
    selected = Path(path)
    try:
        if selected.is_file():
            text = selected.read_text(encoding="utf-8")
        elif selected == DEFAULT_POLICY_PATH:
            text = (
                files("orgrebase")
                .joinpath("_assets/configs/oac/runtime-admission-policy.json")
                .read_text(encoding="utf-8")
            )
        else:
            raise FileNotFoundError(selected)
        value = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"OAC_RUNTIME_POLICY_UNREADABLE:{selected}") from exc
    policy = _dict(value, "OAC_RUNTIME_POLICY_INVALID")
    required = {
        "schema_version",
        "policy_id",
        "revision",
        "allowed_namespaces",
        "allowed_governance_refs",
        "allowed_lowering_profiles",
        "allowed_oac_source_fingerprints",
        "approver_by_namespace",
        "plan_commitments_by_case",
        "runtime_subject_prefix",
        "max_steps",
        "allowed_handler_bindings",
    }
    if set(policy) != required or policy["schema_version"] != ("orgrebase.oac-runtime-admission-policy.v2"):
        raise IntegrityError("OAC_RUNTIME_POLICY_INVALID")
    return policy, sha256_digest(policy)


class OACBlackBoxCLI:
    """Public CLI adapter; it never imports sibling ``oac`` Python modules."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = locate_oac_root(root)
        pyproject = tomllib.loads((self.root / "pyproject.toml").read_text(encoding="utf-8"))
        self.version = str(pyproject["project"]["version"])
        selected_python = os.environ.get(_OAC_PYTHON_ENV)
        # Preserve a virtual-environment interpreter path instead of resolving
        # its symlink to the base Python.  The latter silently drops the venv's
        # dependency search path and only fails when both projects run from
        # installed wheels outside their source checkouts.
        self.distribution_python = Path(selected_python).expanduser().absolute() if selected_python else None
        if self.distribution_python is not None and not self.distribution_python.is_file():
            raise FileNotFoundError(f"OAC_DISTRIBUTION_PYTHON_NOT_FOUND:{self.distribution_python}")
        self.execution_runtime = self._execution_runtime()
        self.source_manifest, self.source_fingerprint = build_oac_public_source_manifest(self.root)

    def _execution_runtime(self) -> dict[str, str]:
        if self.distribution_python is None:
            return {"mode": "SOURCE_CHECKOUT_CLI", "version": self.version}
        probe = self._invoke(
            [
                str(self.distribution_python),
                "-c",
                (
                    "import importlib.metadata,json,oac;"
                    "print(json.dumps({'version':importlib.metadata.version('oac-contract'),"
                    "'module':oac.__file__}))"
                ),
            ],
            timeout=_OAC_PROBE_TIMEOUT_SECONDS,
            timeout_code="OAC_DISTRIBUTION_PROBE_TIMEOUT",
        )
        try:
            payload = json.loads(probe.stdout)
            module = Path(payload["module"]).resolve()
        except (json.JSONDecodeError, KeyError, TypeError, OSError) as exc:
            raise RuntimeError("OAC_DISTRIBUTION_PROBE_FAILED") from exc
        if probe.returncode != 0 or payload.get("version") != self.version:
            raise RuntimeError("OAC_DISTRIBUTION_VERSION_MISMATCH")
        if module == self.root or self.root in module.parents:
            raise RuntimeError("OAC_DISTRIBUTION_NOT_ISOLATED")
        return {"mode": "INSTALLED_WHEEL_CLI", "version": self.version}

    def _command(self, arguments: Sequence[str]) -> list[str]:
        if self.distribution_python is not None:
            return [str(self.distribution_python), "-m", "oac.cli", *arguments]
        python = self.root / ".venv" / "bin" / "python"
        if python.is_file():
            return [str(python), "-m", "oac.cli", *arguments]
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError("OAC_CLI_RUNTIME_NOT_FOUND")
        return [uv, "run", "--project", str(self.root), "oac", *arguments]

    def _invoke(
        self, command: Sequence[str], *, timeout: float, timeout_code: str,
    ) -> subprocess.CompletedProcess[str]:
        with subprocess.Popen(
            command,
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=os.name == "posix",
        ) as process:
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # uv may have spawned the actual CLI; terminate that process too.
                if os.name == "posix":
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.communicate(timeout=1)
                raise RuntimeError(timeout_code) from None
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def run(self, *arguments: str) -> dict[str, Any]:
        completed = self._invoke(
            self._command(arguments),
            timeout=_OAC_COMMAND_TIMEOUT_SECONDS,
            timeout_code=f"OAC_CLI_TIMEOUT:{arguments[0]}",
        )
        if completed.returncode != 0:
            raise RuntimeError(f"OAC_CLI_FAILED:{arguments[0]}")
        if not completed.stdout.strip():
            return {"status": "PASS"}
        try:
            return _dict(json.loads(completed.stdout), "OAC_CLI_OUTPUT_INVALID")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OAC_CLI_OUTPUT_INVALID:{arguments[0]}") from exc

    def run_to_file(self, arguments: Sequence[str], output: Path) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="orgrebase-oac-output-", dir=output.parent) as raw:
            pending = Path(raw) / output.name
            completed = self._invoke(
                self._command((*arguments, "-o", str(pending))),
                timeout=_OAC_COMMAND_TIMEOUT_SECONDS,
                timeout_code=f"OAC_CLI_TIMEOUT:{arguments[0]}",
            )
            if completed.returncode != 0:
                raise RuntimeError(f"OAC_CLI_FAILED:{arguments[0]}")
            try:
                result = _dict(json.loads(pending.read_text(encoding="utf-8")), "OAC_CLI_OUTPUT_INVALID")
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"OAC_CLI_OUTPUT_INVALID:{arguments[0]}") from exc
            pending.replace(output)
            return result

    @staticmethod
    def _write_resource(path: Path, value: Mapping[str, Any]) -> None:
        path.write_bytes(rfc8785.dumps(dict(value)) + b"\n")

    @staticmethod
    def build_runtime_binding(
        snapshot: Mapping[str, Any],
        plan: Mapping[str, Any],
        certificate: Mapping[str, Any],
    ) -> dict[str, Any]:
        plan_spec = _dict(plan.get("spec"), "OAC_PLAN_SPEC_INVALID")
        obligations = {
            _str(item.get("obligationId"), "OAC_OBLIGATION_INVALID"): item
            for item in (
                _dict(raw, "OAC_OBLIGATION_INVALID")
                for raw in _list(plan_spec.get("obligations"), "OAC_OBLIGATIONS_INVALID")
            )
        }
        evidence_by_type: dict[str, set[str]] = {}
        for obligation in obligations.values():
            kind = _str(obligation.get("obligationType"), "OAC_OBLIGATION_INVALID")
            evidence_by_type.setdefault(kind, set()).update(
                _str(value, "OAC_EVIDENCE_REF_INVALID")
                for value in _list(obligation.get("requiredEvidence"), "OAC_EVIDENCE_INVALID")
            )
        role_bindings = []
        for raw_role in _list(plan_spec.get("roleInstances"), "OAC_ROLE_INSTANCES_INVALID"):
            role = _dict(raw_role, "OAC_ROLE_INSTANCE_INVALID")
            capabilities = {
                f"capability:{_str(obligations[_str(ref, 'OAC_ROLE_OBLIGATION_REF_INVALID')].get('obligationType'), 'OAC_OBLIGATION_INVALID')}"
                for ref in _list(role.get("obligationRefs"), "OAC_ROLE_OBLIGATIONS_INVALID")
            }
            principal = _str(role.get("principalRef"), "OAC_PRINCIPAL_REF_INVALID")
            role_bindings.append(
                {
                    "roleInstanceRef": role.get("roleInstanceId"),
                    "principalRef": principal,
                    "runtimeSubject": f"runtime-subject:{principal}",
                    "capabilityRefs": list(_sorted(capabilities)),
                }
            )
        handlers = []
        for obligation_type in sorted(evidence_by_type, key=str.encode):
            handlers.append(
                {
                    "obligationType": obligation_type,
                    "handlerRef": f"urn:oac:zero-effect-handler:{obligation_type}",
                    "handlerDigest": "sha256:"
                    + hashlib.sha256(f"handler:{obligation_type}".encode()).hexdigest(),
                    "capabilityRef": f"capability:{obligation_type}",
                    "evidenceOutputRefs": list(_sorted(evidence_by_type[obligation_type])),
                    "effectCeiling": _ZERO_EFFECT,
                }
            )
        snapshot_metadata = _dict(snapshot.get("metadata"), "OAC_METADATA_INVALID")
        plan_metadata = _dict(plan.get("metadata"), "OAC_METADATA_INVALID")
        certificate_metadata = _dict(certificate.get("metadata"), "OAC_METADATA_INVALID")
        binding: dict[str, Any] = {
            "apiVersion": "oac.dev/v0alpha1",
            "kind": "RuntimeBinding",
            "metadata": {
                "id": f"runtime-binding:{plan.get('digest')}",
                "namespace": snapshot_metadata["namespace"],
                "revision": 1,
                "ownerRef": snapshot_metadata["ownerRef"],
                "governanceRef": snapshot_metadata["governanceRef"],
                "createdAt": snapshot_metadata["createdAt"],
                "effectiveFrom": None,
                "effectiveTo": None,
                "sourceRefs": [plan_metadata["id"], certificate_metadata["id"]],
            },
            "spec": {
                "subjectPlanRef": _resource_ref(plan),
                "subjectCertificateRef": _resource_ref(certificate),
                "roleBindings": role_bindings,
                "handlerBindings": handlers,
                "effectCeiling": _ZERO_EFFECT,
                "targetWrites": 0,
            },
        }
        binding["digest"] = _jcs_digest(binding)
        return binding

    def build_capsule(self, case_id: str) -> OACRuntimeCapsule:
        if case_id not in {"BASE", "SPLIT"}:
            raise ValueError("OAC_BRIDGE_CASE_UNSUPPORTED")
        inputs = (
            self.root
            / "experiments"
            / "plan-verification-portability"
            / "v0.1-seed-1"
            / "artifacts"
            / "SC-008"
        )
        snapshot_path = inputs / "veracier-proc01-contextual.snapshot.json"
        change_path = inputs / "SC-008.change.json"
        plan_fixture = (
            self.root
            / "experiments"
            / "plan-verification-portability"
            / "v0.1-seed-1"
            / "artifacts"
            / "plans"
            / "PV-POS-SC008-SPLIT.plan.json"
        )
        with tempfile.TemporaryDirectory(prefix="orgrebase-oac-wire-") as raw:
            root = Path(raw)
            plan_path = root / "plan.json"
            certificate_path = root / "certificate.json"
            binding_path = root / "binding.json"
            lowering_path = root / "lowering.json"
            self.run("validate", str(snapshot_path), "--verify-digest")
            self.run("validate", str(change_path), "--verify-digest")
            if case_id == "BASE":
                plan = self.run_to_file(("compile", str(snapshot_path), str(change_path)), plan_path)
                plan_source = "OAC_REFERENCE_COMPILER"
            else:
                plan = _dict(
                    json.loads(plan_fixture.read_text(encoding="utf-8")),
                    "OAC_PLAN_FIXTURE_INVALID",
                )
                self._write_resource(plan_path, plan)
                self.run("validate", str(plan_path), "--verify-digest")
                plan_source = "OAC_PUBLIC_VERIFIER_ACCEPTED_FIXTURE"
            certificate = self.run_to_file(
                ("verify", str(snapshot_path), str(change_path), str(plan_path)),
                certificate_path,
            )
            snapshot = _dict(
                json.loads(snapshot_path.read_text(encoding="utf-8")),
                "OAC_SNAPSHOT_INVALID",
            )
            change = _dict(
                json.loads(change_path.read_text(encoding="utf-8")),
                "OAC_CHANGE_INVALID",
            )
            binding = self.build_runtime_binding(snapshot, plan, certificate)
            self._write_resource(binding_path, binding)
            self.run("validate", str(binding_path), "--verify-digest")
            lowering = self.run_to_file(
                (
                    "lower",
                    str(snapshot_path),
                    str(change_path),
                    str(plan_path),
                    str(certificate_path),
                    str(binding_path),
                    "--admit-binding-digest",
                    _str(binding.get("digest"), "OAC_BINDING_DIGEST_INVALID"),
                ),
                lowering_path,
            )
        bundle = _dict(lowering.get("bundle"), "OAC_LOWERING_BUNDLE_MISSING")
        receipt = _dict(lowering.get("receipt"), "OAC_LOWERING_RECEIPT_MISSING")
        return OACRuntimeCapsule(
            case_id=case_id,
            plan_source=cast(Any, plan_source),
            oac_source_manifest=self.source_manifest,
            oac_source_fingerprint=self.source_fingerprint,
            snapshot=snapshot,
            change=change,
            plan=plan,
            plan_certificate=certificate,
            runtime_binding=binding,
            runtime_bundle=bundle,
            runtime_lowering_receipt=receipt,
            oac_cli_version=self.version,
        )
