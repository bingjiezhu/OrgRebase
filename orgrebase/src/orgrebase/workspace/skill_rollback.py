"""Fail-closed execution of an exact, retained Skill predecessor.

The normal Skill lifecycle intentionally records rollback decisions without
pretending that predecessor bytes exist.  This module is the narrow complement:
it verifies retained predecessor bytes and their provenance. The quote rollback
executor additionally verifies direct lineage, moves an append-only rehearsal
head, and invokes the old restricted program. It never writes a business target.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError

ROLLBACK_AUTHORITY = "authority:skill-registry"
ROLLBACK_SCHEMA = "orgrebase.skill-predecessor-rollback-execution-receipt.v1"
PREDECESSOR_NAME = "enterprise-quote-compose"
PREDECESSOR_VERSION = "1.3.0"
PREDECESSOR_ENTRY_POINT = "QUOTE_COMPOSE_V1"
RETAINED_PREDECESSORS = {
    PREDECESSOR_NAME: (PREDECESSOR_VERSION, PREDECESSOR_ENTRY_POINT),
    "enterprise-launch-readiness": ("1.4.1", "LAUNCH_READINESS_V1"),
    "structured-domain-handoff": ("1.1.1", "STRUCTURED_DOMAIN_HANDOFF_V1"),
}
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ZERO_DIGEST = "sha256:" + "0" * 64
_RESOURCE_FILES = {
    "skill": "SKILL.md",
    "contract": "contract.json",
    "program": "program.json",
    "input_schema": "input.schema.json",
    "output_schema": "output.schema.json",
    "manifest": "package.json",
}
_PACKAGE_RESOURCE_FILES = {key: value for key, value in _RESOURCE_FILES.items() if key != "manifest"}
_DENY_FLAGS = frozenset({"permission_expansion", "request_restricted_source", "target_write_requested"})
_ABSTAIN_FLAGS = frozenset(
    {
        "prompt_injection",
        "malformed_input",
        "deadline_expired",
        "resource_exhausted",
        "stale_input",
    }
)
_DOMAIN_NAMES = frozenset({"product", "legal", "finance", "gtm"})


def _raw_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _record(value: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(value))
    result["digest"] = sha256_digest(value)
    return result


def _verify_record(value: Mapping[str, Any], *, error: str) -> None:
    body = {key: item for key, item in value.items() if key != "digest"}
    if value.get("digest") != sha256_digest(body):
        raise IntegrityError(error)


def _load_json(path: Path, *, error: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError(error) from exc
    if not isinstance(value, dict):
        raise IntegrityError(error)
    return value


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and value != _ZERO_DIGEST and _DIGEST.fullmatch(value) is not None


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class FrozenPredecessorPackage:
    root: Path
    resource_mode: str
    manifest: dict[str, Any]
    contract: dict[str, Any]
    program: dict[str, Any]
    provenance: dict[str, Any]
    raw_digests: dict[str, str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]

    @property
    def package_digest(self) -> str:
        return str(self.manifest["manifest_digest"])


@dataclass(frozen=True)
class RollbackExecution:
    predecessor_input: dict[str, Any]
    result: dict[str, Any]
    receipt: dict[str, Any]
    idempotent_replay: bool


def default_predecessor_root(
    checkout_root: str | Path | None = None, *, name: str = PREDECESSOR_NAME
) -> Path:
    if name not in RETAINED_PREDECESSORS:
        raise IntegrityError("SKILL_PREDECESSOR_NOT_RETAINED")
    version, _ = RETAINED_PREDECESSORS[name]
    checkout = (
        Path(checkout_root).resolve() if checkout_root is not None else Path(__file__).resolve().parents[3]
    )
    source = checkout / "skills" / "predecessors" / name / version
    if checkout_root is not None or source.is_dir():
        return source
    packaged = resources.files("orgrebase").joinpath(
        "skill_predecessors", name, version
    )
    packaged_path = Path(str(packaged)).resolve()
    if not packaged_path.is_dir():
        raise IntegrityError("SKILL_PREDECESSOR_PACKAGED_RESOURCE_MISSING")
    return packaged_path


def load_frozen_predecessor(
    root: str | Path | None = None,
    *,
    checkout_root: str | Path | None = None,
    verify_retained_wheel: bool = False,
    name: str = PREDECESSOR_NAME,
) -> FrozenPredecessorPackage:
    """Verify an allowlisted, retained predecessor without rewriting its bytes."""

    if name not in RETAINED_PREDECESSORS:
        raise IntegrityError("SKILL_PREDECESSOR_NOT_RETAINED")
    version, entry_point = RETAINED_PREDECESSORS[name]
    package_root = Path(root).resolve() if root is not None else default_predecessor_root(checkout_root, name=name)
    checkout = (
        Path(checkout_root).resolve() if checkout_root is not None else Path(__file__).resolve().parents[3]
    )
    source_root = checkout / "skills" / "predecessors" / name / version
    source_mode = package_root == source_root and source_root.is_dir()
    resource_mode = "SOURCE_CHECKOUT" if source_mode else "INSTALLED_WHEEL"
    expected_files = set(_RESOURCE_FILES.values()) | {"provenance.json"}
    try:
        observed_files = {path.name for path in package_root.iterdir() if path.is_file()}
    except OSError as exc:
        raise IntegrityError("SKILL_PREDECESSOR_ROOT_MISSING") from exc
    if observed_files != expected_files:
        raise IntegrityError("SKILL_PREDECESSOR_RESOURCE_SET_INVALID")

    raw: dict[str, bytes] = {}
    for resource_name, filename in _RESOURCE_FILES.items():
        try:
            raw[resource_name] = (package_root / filename).read_bytes()
        except OSError as exc:
            raise IntegrityError("SKILL_PREDECESSOR_RESOURCE_MISSING") from exc
    raw_digests = {name: _raw_digest(value) for name, value in raw.items()}
    manifest = _load_json(package_root / "package.json", error="SKILL_PREDECESSOR_MANIFEST_INVALID_JSON")
    contract = _load_json(package_root / "contract.json", error="SKILL_PREDECESSOR_CONTRACT_INVALID_JSON")
    program = _load_json(package_root / "program.json", error="SKILL_PREDECESSOR_PROGRAM_INVALID_JSON")
    provenance = _load_json(
        package_root / "provenance.json", error="SKILL_PREDECESSOR_PROVENANCE_INVALID_JSON"
    )
    _verify_record(provenance, error="SKILL_PREDECESSOR_PROVENANCE_DIGEST_MISMATCH")

    manifest_body = {key: value for key, value in manifest.items() if key != "manifest_digest"}
    manifest_digest = sha256_digest(manifest_body)
    if (
        manifest.get("schema_version") != "orgrebase.skill-package-manifest.v2"
        or manifest.get("manifest_digest") != manifest_digest
        or manifest.get("name") != name
        or manifest.get("version") != version
        or manifest.get("package_id") != f"skill-package:{name}@{version}"
        or manifest.get("entry_point") != entry_point
    ):
        raise IntegrityError("SKILL_PREDECESSOR_MANIFEST_IDENTITY_OR_DIGEST_MISMATCH")

    declarations = manifest.get("resources")
    if not isinstance(declarations, Mapping) or set(declarations) != set(_PACKAGE_RESOURCE_FILES):
        raise IntegrityError("SKILL_PREDECESSOR_RESOURCE_SET_INVALID")
    for resource_name, filename in _PACKAGE_RESOURCE_FILES.items():
        declaration = declarations.get(resource_name)
        if (
            not isinstance(declaration, Mapping)
            or declaration.get("path") != filename
            or declaration.get("sha256") != raw_digests[resource_name]
        ):
            raise IntegrityError(f"SKILL_PREDECESSOR_RESOURCE_DIGEST_MISMATCH:{resource_name}")

    contract_body = {key: value for key, value in contract.items() if key != "content_digest"}
    program_body = {key: value for key, value in program.items() if key != "digest"}
    if (
        contract.get("content_digest") != sha256_digest(contract_body)
        or contract.get("name") != name
        or contract.get("version") != version.rsplit(".", 1)[0]
    ):
        raise IntegrityError("SKILL_PREDECESSOR_CONTRACT_DIGEST_MISMATCH")
    if program.get("digest") != sha256_digest(program_body) or manifest.get(
        "program_content_digest"
    ) != program.get("digest"):
        raise IntegrityError("SKILL_PREDECESSOR_PROGRAM_DIGEST_MISMATCH")
    if (
        manifest.get("permissions")
        != {
            "allowed_tools": [],
            "side_effects": [],
            "effect_ceiling": "CANDIDATE_ONLY",
        }
        or program.get("allowed_tool_ids") != []
        or program.get("side_effects") != []
    ):
        raise IntegrityError("SKILL_PREDECESSOR_EFFECT_BOUNDARY_WIDENED")
    operations = program.get("operations")
    if name == PREDECESSOR_NAME and (not isinstance(operations, list) or [item.get("operation") for item in operations] != [
        "REQUIRE_FIELDS",
        "MAP_VALUE",
        "RETURN_FIELD",
    ]):
        raise IntegrityError("SKILL_PREDECESSOR_PROGRAM_NOT_RESTRICTED")
    if name != PREDECESSOR_NAME and (
        program.get("schema_version") != "orgrebase.restricted-skill-program.v1"
        or program.get("adapter") != entry_point
        or not isinstance(program.get("required_fields"), list)
    ):
        raise IntegrityError("SKILL_PREDECESSOR_PROGRAM_NOT_RESTRICTED")
    deny_flags = _DENY_FLAGS | ({"recipient_expansion", "schema_expansion"} if name == "structured-domain-handoff" else set())
    abstain_flags = (_ABSTAIN_FLAGS - {"stale_input"}) | {"stale_preview"} if name == "enterprise-launch-readiness" else _ABSTAIN_FLAGS
    if (
        set(program.get("deny_flags", ())) != deny_flags
        or set(program.get("abstain_flags", ())) != abstain_flags
    ):
        raise IntegrityError("SKILL_PREDECESSOR_SECURITY_POLICY_INVALID")

    extracted = provenance.get("extracted_resources")
    if (
        provenance.get("schema_version") != "orgrebase.skill-predecessor-source.v1"
        or provenance.get("source_kind") != "RETAINED_WHEEL_READ_ONLY_EXTRACTION"
        or provenance.get("skill_name") != name
        or provenance.get("skill_version") != version
        or provenance.get("manifest_digest") != manifest_digest
        or provenance.get("program_content_digest") != program.get("digest")
        or provenance.get("contract_content_digest") != contract.get("content_digest")
        or not isinstance(extracted, Mapping)
        or set(extracted) != set(_RESOURCE_FILES.values())
        or any(
            not isinstance(extracted[filename], Mapping)
            or extracted[filename].get("sha256") != raw_digests[resource_name]
            for resource_name, filename in _RESOURCE_FILES.items()
        )
    ):
        raise IntegrityError("SKILL_PREDECESSOR_PROVENANCE_BINDING_INVALID")

    if verify_retained_wheel:
        source_wheel = provenance.get("source_wheel")
        if not isinstance(source_wheel, Mapping):
            raise IntegrityError("SKILL_PREDECESSOR_SOURCE_WHEEL_BINDING_INVALID")
        wheel = checkout / str(source_wheel.get("relative_path", ""))
        try:
            wheel_bytes = wheel.read_bytes()
        except OSError as exc:
            raise IntegrityError("SKILL_PREDECESSOR_SOURCE_WHEEL_MISSING") from exc
        if len(wheel_bytes) != source_wheel.get("bytes") or _raw_digest(wheel_bytes) != source_wheel.get(
            "sha256"
        ):
            raise IntegrityError("SKILL_PREDECESSOR_SOURCE_WHEEL_DIGEST_MISMATCH")
        try:
            with zipfile.ZipFile(wheel) as archive:
                for resource_name, filename in _RESOURCE_FILES.items():
                    member = str(extracted[filename].get("wheel_member", ""))
                    if archive.read(member) != raw[resource_name]:
                        raise IntegrityError(f"SKILL_PREDECESSOR_WHEEL_MEMBER_MISMATCH:{filename}")
        except (KeyError, zipfile.BadZipFile) as exc:
            raise IntegrityError("SKILL_PREDECESSOR_SOURCE_WHEEL_INVALID") from exc

    return FrozenPredecessorPackage(
        root=package_root,
        resource_mode=resource_mode,
        manifest=deepcopy(manifest),
        contract=deepcopy(contract),
        program=deepcopy(program),
        provenance=deepcopy(provenance),
        raw_digests=raw_digests,
        input_schema=_load_json(package_root / "input.schema.json", error="SKILL_PREDECESSOR_SCHEMA_INVALID"),
        output_schema=_load_json(package_root / "output.schema.json", error="SKILL_PREDECESSOR_SCHEMA_INVALID"),
    )


def invoke_restricted_predecessor(
    package: FrozenPredecessorPackage, public_input: Mapping[str, Any]
) -> dict[str, Any]:
    """Interpret the exact archived declarative program and nothing else."""

    value = deepcopy(dict(public_input))
    operations = package.program.get("operations")
    required_fields = (
        frozenset(operations[0].get("input_fields", ()))
        if isinstance(operations, list) and operations
        else frozenset()
    )

    def result(action: str, **fields: Any) -> dict[str, Any]:
        return {
            "schema_version": "orgrebase.skill-result-candidate.v1",
            "action": action,
            **fields,
            "package_digest": package.package_digest,
            "candidate_only": True,
            "target_writes": 0,
        }

    if not required_fields or not all(field in value for field in required_fields):
        return result("ABSTAIN", reason="MALFORMED_INPUT")
    dependencies = {
        "dependency_tool_receipt_digest": value["dependency_tool_receipt_digest"],
        "dependency_result_digest": value["dependency_result_digest"],
        "coalition_result_binding_digest": value["coalition_result_binding_digest"],
        "domain_result_digests": deepcopy(value["domain_result_digests"]),
    }
    scalar_digests = {
        key: item for key, item in dependencies.items() if key != "domain_result_digests"
    }
    domain_results = dependencies["domain_result_digests"]
    if not all(_valid_digest(item) for item in scalar_digests.values()):
        return result("ABSTAIN", reason="INVALID_DEPENDENCY_RECEIPT_DIGEST")
    if (
        not isinstance(domain_results, dict)
        or set(domain_results) != _DOMAIN_NAMES
        or not all(_valid_digest(item) for item in domain_results.values())
        or len(set(domain_results.values())) != len(_DOMAIN_NAMES)
    ):
        return result("ABSTAIN", reason="INVALID_FOUR_DOMAIN_COALITION_ROOTS")
    if set(value) - required_fields - _DENY_FLAGS - _ABSTAIN_FLAGS:
        return result("ABSTAIN", reason="UNKNOWN_INPUT_FIELD")
    if any(value.get(flag) is True for flag in package.program["deny_flags"]):
        return result("DENY", reason="AUTHORITY_OR_DISCLOSURE_VETO", **dependencies)
    if any(value.get(flag) is True for flag in package.program["abstain_flags"]):
        return result("ABSTAIN", reason="UNTRUSTED_OR_UNAVAILABLE_INPUT", **dependencies)
    if value["candidate_program_digest_required"] != package.manifest["program_content_digest"]:
        raise IntegrityError("SKILL_REQUESTED_PROGRAM_DIGEST_MISMATCH")
    mapping = operations[1].get("parameters", {}).get("mapping")
    if not isinstance(mapping, Mapping):
        raise IntegrityError("SKILL_PREDECESSOR_MAPPING_INVALID")
    action = str(mapping.get(str(value["skill_partition"]), "ABSTAIN"))
    return result(action, reason="EXACT_DECLARATIVE_PROGRAM", **dependencies)


def verify_rollback_execution_receipt(receipt: Mapping[str, Any], package: FrozenPredecessorPackage) -> None:
    _verify_record(receipt, error="SKILL_PREDECESSOR_ROLLBACK_RECEIPT_DIGEST_MISMATCH")
    raw = {name: package.raw_digests[name] for name in sorted(package.raw_digests)}
    if (
        receipt.get("schema_version") != ROLLBACK_SCHEMA
        or receipt.get("event_type") != "EXECUTABLE_PREDECESSOR_ROLLBACK"
        or receipt.get("skill_name") != PREDECESSOR_NAME
        or receipt.get("actor_id") != ROLLBACK_AUTHORITY
        or receipt.get("authority_scope") != ["skill:release-head:rollback", "skill:predecessor:invoke"]
        or receipt.get("declared_predecessor_package_digest") != package.package_digest
        or receipt.get("effective_package_digest") != package.package_digest
        or receipt.get("predecessor_package_id") != package.manifest["package_id"]
        or receipt.get("predecessor_program_digest") != package.program["digest"]
        or receipt.get("predecessor_contract_digest") != package.contract["content_digest"]
        or receipt.get("predecessor_resource_digests") != raw
        or receipt.get("predecessor_source_provenance_digest") != package.provenance["digest"]
        or receipt.get("lineage_status") != "DIRECT_DECLARED_PREDECESSOR"
        or receipt.get("restoration_status") != "EXECUTED_AND_INVOKED"
        or receipt.get("candidate_only") is not True
        or receipt.get("target_writes") != 0
        or not _valid_timestamp(receipt.get("created_at"))
    ):
        raise IntegrityError("SKILL_PREDECESSOR_ROLLBACK_RECEIPT_INVALID")


def verify_rollback_history(history: Sequence[Mapping[str, Any]], package: FrozenPredecessorPackage) -> None:
    previous: str | None = None
    idempotency_keys: set[str] = set()
    for index, receipt in enumerate(history, start=1):
        verify_rollback_execution_receipt(receipt, package)
        key = receipt.get("idempotency_key")
        if (
            receipt.get("event_index") != index
            or receipt.get("previous_receipt_digest") != previous
            or not isinstance(key, str)
            or not key
            or key in idempotency_keys
        ):
            raise IntegrityError("SKILL_PREDECESSOR_ROLLBACK_LEDGER_INVALID")
        idempotency_keys.add(key)
        previous = str(receipt["digest"])


class SkillPredecessorRollbackExecutor:
    """Authority-controlled, append-only release-head rollback executor."""

    def __init__(
        self,
        package: FrozenPredecessorPackage,
        history: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        verify_rollback_history(history, package)
        self.package = package
        self._history = [deepcopy(dict(item)) for item in history]
        self._idempotency = {
            str(item["idempotency_key"]): (
                str(item["rollback_binding_digest"]),
                deepcopy(dict(item)),
            )
            for item in self._history
        }

    @property
    def history(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(self._history))

    @staticmethod
    def _verify_current_evidence(
        *,
        run_id: str,
        current_manifest: Mapping[str, Any],
        current_invocation_receipt: Mapping[str, Any],
        current_input: Mapping[str, Any],
        current_result: Mapping[str, Any],
        tool_receipt: Mapping[str, Any],
        tool_result: Mapping[str, Any],
        predecessor_digest: str,
    ) -> tuple[str, str, str]:
        current_manifest_body = {
            key: value for key, value in current_manifest.items() if key != "manifest_digest"
        }
        current_digest = sha256_digest(current_manifest_body)
        release = current_manifest.get("release_artifact")
        if (
            current_manifest.get("manifest_digest") != current_digest
            or current_manifest.get("name") != PREDECESSOR_NAME
            or not isinstance(release, Mapping)
            or release.get("predecessor_package_digest") != predecessor_digest
        ):
            raise IntegrityError("SKILL_PREDECESSOR_DIRECT_LINEAGE_MISMATCH")

        _verify_record(
            current_invocation_receipt,
            error="SKILL_CURRENT_INVOCATION_RECEIPT_DIGEST_MISMATCH",
        )
        _verify_record(tool_receipt, error="SKILL_ROLLBACK_TOOL_RECEIPT_DIGEST_MISMATCH")
        task_id = str(current_invocation_receipt.get("task_id", ""))
        delegation_id = str(current_invocation_receipt.get("delegation_id", ""))
        if (
            not run_id
            or not task_id
            or not delegation_id
            or current_invocation_receipt.get("run_id") != run_id
            or current_invocation_receipt.get("package_digest") != current_digest
            or current_invocation_receipt.get("manifest_digest") != current_digest
            or current_invocation_receipt.get("program_digest")
            != current_manifest.get("program_content_digest")
            or current_invocation_receipt.get("input_digest") != sha256_digest(current_input)
            or current_invocation_receipt.get("output_digest") != sha256_digest(current_result)
            or current_invocation_receipt.get("candidate_only") is not True
            or current_invocation_receipt.get("target_writes") != 0
            or current_result.get("package_digest") != current_digest
            or current_result.get("candidate_only") is not True
            or current_result.get("target_writes") != 0
        ):
            raise IntegrityError("SKILL_CURRENT_INVOCATION_BINDING_INVALID")

        result_digest = sha256_digest(tool_result)
        if (
            tool_receipt.get("run_id") != run_id
            or tool_receipt.get("status") != "SUCCEEDED"
            or tool_receipt.get("response_digest") != result_digest
            or tool_receipt.get("target_writes") != 0
            or tool_result.get("run_id") != run_id
            or tool_result.get("task_id") != task_id
            or tool_result.get("target_writes") != 0
            or current_input.get("dependency_tool_receipt_digest") != tool_receipt.get("digest")
            or current_input.get("dependency_result_digest") != result_digest
            or current_input.get("candidate_program_digest_required")
            != current_manifest.get("program_content_digest")
        ):
            raise IntegrityError("SKILL_ROLLBACK_TOOL_BINDING_INVALID")
        return current_digest, task_id, delegation_id

    def execute(
        self,
        *,
        run_id: str,
        actor_id: str,
        idempotency_key: str,
        current_manifest: Mapping[str, Any],
        current_invocation_receipt: Mapping[str, Any],
        current_input: Mapping[str, Any],
        current_result: Mapping[str, Any],
        tool_receipt: Mapping[str, Any],
        tool_result: Mapping[str, Any],
        reason_codes: Sequence[str],
        created_at: str,
    ) -> RollbackExecution:
        if actor_id != ROLLBACK_AUTHORITY:
            raise IntegrityError("SKILL_PREDECESSOR_ROLLBACK_AUTHORITY_DENIED")
        if (
            not idempotency_key
            or not reason_codes
            or any(not isinstance(item, str) or not item for item in reason_codes)
            or not _valid_timestamp(created_at)
        ):
            raise IntegrityError("SKILL_PREDECESSOR_ROLLBACK_REQUEST_INVALID")
        current_digest, task_id, delegation_id = self._verify_current_evidence(
            run_id=run_id,
            current_manifest=current_manifest,
            current_invocation_receipt=current_invocation_receipt,
            current_input=current_input,
            current_result=current_result,
            tool_receipt=tool_receipt,
            tool_result=tool_result,
            predecessor_digest=self.package.package_digest,
        )
        predecessor_input = {
            "skill_partition": current_input.get("skill_partition"),
            "candidate_program_digest_required": self.package.program["digest"],
            "dependency_tool_receipt_digest": tool_receipt["digest"],
            "dependency_result_digest": sha256_digest(tool_result),
            "coalition_result_binding_digest": current_input.get(
                "coalition_result_binding_digest"
            ),
            "domain_result_digests": deepcopy(
                current_input.get("domain_result_digests")
            ),
        }
        predecessor_result = invoke_restricted_predecessor(self.package, predecessor_input)
        action = str(predecessor_result.get("action", "ABSTAIN"))
        outcome = (
            "DENY"
            if action == "DENY"
            else "ABSTAIN"
            if action in {"ABSTAIN", "SAFE_ABSTAIN", "ESCALATE"}
            else "SUCCESS"
        )
        binding_body = {
            "run_id": run_id,
            "task_id": task_id,
            "delegation_id": delegation_id,
            "idempotency_key": idempotency_key,
            "actor_id": actor_id,
            "from_package_digest": current_digest,
            "to_package_digest": self.package.package_digest,
            "current_invocation_receipt_digest": current_invocation_receipt["digest"],
            "current_input_digest": sha256_digest(current_input),
            "current_output_digest": sha256_digest(current_result),
            "dependency_tool_receipt_digest": tool_receipt["digest"],
            "dependency_result_digest": sha256_digest(tool_result),
            "predecessor_input_digest": sha256_digest(predecessor_input),
            "predecessor_output_digest": sha256_digest(predecessor_result),
            "reason_codes": list(reason_codes),
        }
        binding_digest = sha256_digest(binding_body)
        existing = self._idempotency.get(idempotency_key)
        if existing is not None:
            if existing[0] != binding_digest:
                raise IntegrityError("SKILL_PREDECESSOR_ROLLBACK_IDEMPOTENCY_CONFLICT")
            return RollbackExecution(
                predecessor_input=predecessor_input,
                result=predecessor_result,
                receipt=deepcopy(existing[1]),
                idempotent_replay=True,
            )

        raw_digests = {name: self.package.raw_digests[name] for name in sorted(self.package.raw_digests)}
        receipt_body = {
            "schema_version": ROLLBACK_SCHEMA,
            "id": f"skill-predecessor-rollback:{binding_digest.removeprefix('sha256:')}",
            "event_index": len(self._history) + 1,
            "event_type": "EXECUTABLE_PREDECESSOR_ROLLBACK",
            "run_id": run_id,
            "task_id": task_id,
            "delegation_id": delegation_id,
            "skill_name": PREDECESSOR_NAME,
            "actor_id": actor_id,
            "authority_scope": [
                "skill:release-head:rollback",
                "skill:predecessor:invoke",
            ],
            "idempotency_key": idempotency_key,
            "rollback_binding_digest": binding_digest,
            "from_package_id": str(current_manifest["package_id"]),
            "from_package_digest": current_digest,
            "declared_predecessor_package_digest": self.package.package_digest,
            "predecessor_package_id": self.package.manifest["package_id"],
            "effective_package_digest": self.package.package_digest,
            "predecessor_entry_point": self.package.manifest["entry_point"],
            "predecessor_program_digest": self.package.program["digest"],
            "predecessor_contract_digest": self.package.contract["content_digest"],
            "predecessor_resource_digests": raw_digests,
            "predecessor_source_provenance_digest": self.package.provenance["digest"],
            "source_wheel_digest": self.package.provenance["source_wheel"]["sha256"],
            "current_invocation_receipt_digest": current_invocation_receipt["digest"],
            "current_input_digest": sha256_digest(current_input),
            "current_output_digest": sha256_digest(current_result),
            "dependency_tool_receipt_digest": tool_receipt["digest"],
            "dependency_result_digest": sha256_digest(tool_result),
            "predecessor_input_digest": sha256_digest(predecessor_input),
            "predecessor_output_digest": sha256_digest(predecessor_result),
            "predecessor_action": action,
            "outcome": outcome,
            "lineage_status": "DIRECT_DECLARED_PREDECESSOR",
            "restoration_status": "EXECUTED_AND_INVOKED",
            "candidate_only": True,
            "target_writes": 0,
            "previous_receipt_digest": self._history[-1]["digest"] if self._history else None,
            "reason_codes": list(reason_codes),
            "created_at": created_at,
        }
        receipt = _record(receipt_body)
        verify_rollback_execution_receipt(receipt, self.package)
        self._history.append(receipt)
        self._idempotency[idempotency_key] = (binding_digest, deepcopy(receipt))
        return RollbackExecution(
            predecessor_input=predecessor_input,
            result=predecessor_result,
            receipt=deepcopy(receipt),
            idempotent_replay=False,
        )
