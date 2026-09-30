"""Non-destructive authoring helpers for the Enterprise Quote Pilot Pack.

This module is deliberately mechanical.  It can copy the packaged template and
recompute content addresses after a human edits JSON, but it cannot invent
enterprise facts, owners, dependencies, or executable handlers.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from orgrebase.digest import sha256_digest
from orgrebase.resource_paths import runtime_asset_path
from orgrebase.workspace.pilot import (
    PILOT_PACK_FILENAME,
    EnterpriseQuotePilotPack,
    load_enterprise_quote_pilot_pack,
    validate_enterprise_quote_pilot_payloads,
)
from orgrebase.workspace.profile_admission import enterprise_input_preflight
from orgrebase.workspace.profile_contracts import EnterpriseSeedProfile, SeedComponentKind
from orgrebase.workspace.source_admission import (
    EnterpriseSeedComponentRoot,
    component_declaration_digest_for_observed_root,
)

MAX_AUTHORING_JSON_BYTES = 1_048_576
EVERGREEN_TEMPLATE_ASSET = "examples/enterprise-quote-pilot/evergreen"
PILOT_DRAFT_TEMPLATES = {
    "evergreen": EVERGREEN_TEMPLATE_ASSET,
    "priced-quote": "examples/enterprise-quote-pilot/priced-quote",
}


class EnterpriseQuotePilotAuthoringError(ValueError):
    """Stable authoring failure that never mutates the source draft."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(code if not detail else f"{code}:{detail}")


def _strict_json(path: Path, *, root: Path) -> dict[str, Any]:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_PATH_ESCAPE", str(path)) from exc
    if path.is_symlink() or not path.is_file():
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_FILE_INVALID", str(path))
    raw = path.read_bytes()
    if len(raw) > MAX_AUTHORING_JSON_BYTES:
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_JSON_SIZE_LIMIT", str(path))

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise EnterpriseQuotePilotAuthoringError(
                    "PILOT_AUTHOR_JSON_DUPLICATE_KEY",
                    f"{path.name}:{key}",
                )
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=reject_duplicates)
    except EnterpriseQuotePilotAuthoringError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_JSON_INVALID", str(path)) from exc
    if not isinstance(value, dict):
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_JSON_ROOT_NOT_OBJECT", str(path))
    return value


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _raw_digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _parse_pack(value: dict[str, Any]) -> EnterpriseQuotePilotPack:
    try:
        return EnterpriseQuotePilotPack.model_validate(value)
    except ValidationError as exc:
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_PACK_SCHEMA_INVALID", str(exc)) from exc


def _declared_paths(manifest: EnterpriseQuotePilotPack) -> tuple[str, ...]:
    return (
        PILOT_PACK_FILENAME,
        manifest.profile_path,
        *(item.path for item in manifest.components),
    )


def _validate_exact_tree(root: Path, declared: tuple[str, ...]) -> None:
    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise EnterpriseQuotePilotAuthoringError(
                "PILOT_AUTHOR_SYMLINK_FORBIDDEN",
                path.relative_to(root).as_posix(),
            )
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise EnterpriseQuotePilotAuthoringError(
                "PILOT_AUTHOR_SPECIAL_FILE_FORBIDDEN",
                path.relative_to(root).as_posix(),
            )
    if actual != set(declared):
        missing = sorted(set(declared) - actual)
        extra = sorted(actual - set(declared))
        raise EnterpriseQuotePilotAuthoringError(
            "PILOT_AUTHOR_FILE_SET_MISMATCH",
            f"missing={missing},extra={extra}",
        )


def _output_location(output_root: str | Path) -> Path:
    selected = Path(output_root)
    if selected.name in {"", ".", ".."}:
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_OUTPUT_INVALID", str(selected))
    selected.parent.mkdir(parents=True, exist_ok=True)
    parent = selected.parent.resolve(strict=True)
    output = parent / selected.name
    if output.exists() or output.is_symlink():
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_OUTPUT_EXISTS", str(output))
    return output


def _publish_files(
    *,
    output: Path,
    payloads: dict[str, bytes],
    validate: bool,
) -> object | None:
    with tempfile.TemporaryDirectory(prefix=f".{output.name}.authoring-", dir=output.parent) as temporary:
        scratch = Path(temporary) / "pack"
        scratch.mkdir()
        for relative, raw in sorted(payloads.items()):
            selected = scratch / relative
            selected.parent.mkdir(parents=True, exist_ok=True)
            selected.write_bytes(raw)
        runtime = load_enterprise_quote_pilot_pack(scratch) if validate else None
        if output.exists() or output.is_symlink():
            raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_OUTPUT_EXISTS", str(output))
        scratch.rename(output)
        return runtime


def initialize_enterprise_quote_pilot_draft(
    output_root: str | Path, *, template_name: str = "evergreen",
) -> dict[str, Any]:
    """Copy one allowlisted synthetic template without granting business authority."""

    asset = PILOT_DRAFT_TEMPLATES.get(template_name)
    if asset is None:
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_TEMPLATE_UNSUPPORTED")
    template = runtime_asset_path(asset).resolve(strict=True)
    if template.is_symlink() or not template.is_dir():
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_TEMPLATE_INVALID", str(template))
    manifest = _parse_pack(_strict_json(template / PILOT_PACK_FILENAME, root=template))
    declared = _declared_paths(manifest)
    _validate_exact_tree(template, declared)
    source_runtime = load_enterprise_quote_pilot_pack(template)
    output = _output_location(output_root)
    payloads = {relative: (template / relative).read_bytes() for relative in declared}
    copied_runtime = _publish_files(output=output, payloads=payloads, validate=True)
    if copied_runtime is None:  # pragma: no cover - validate=True invariant
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_TEMPLATE_VALIDATION_MISSING")
    return {
        "schema_version": "orgrebase.enterprise-quote-pilot-init-receipt.v1",
        "status": "DRAFT_CREATED",
        "output_locator": f"directory:{output.name}",
        "pack_id": source_runtime.pack_id,
        "pack_revision": source_runtime.pack_revision,
        "adapter_id": source_runtime.adapter_id,
        "organization_id": source_runtime.profile.organization_id,
        "synthetic": source_runtime.profile.synthetic,
        "data_class": source_runtime.profile.data_class.value,
        "template_pack_digest": source_runtime.pack_digest,
        "copied_pack_digest": copied_runtime.pack_digest,
        "file_count": len(payloads),
        "sealed_after_edit": False,
        "canonical_target_writes": 0,
    }


def seal_enterprise_quote_pilot_pack(
    draft_root: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    """Recompute exact Pack content addresses into a new, strictly admitted directory."""

    selected = Path(draft_root)
    if selected.is_symlink():
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_DRAFT_SYMLINK_FORBIDDEN", str(selected))
    try:
        draft = selected.resolve(strict=True)
    except OSError as exc:
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_DRAFT_INVALID", str(selected)) from exc
    if not draft.is_dir():
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_DRAFT_NOT_DIRECTORY", str(draft))

    manifest_value = _strict_json(draft / PILOT_PACK_FILENAME, root=draft)
    manifest = _parse_pack(manifest_value)
    declared = _declared_paths(manifest)
    _validate_exact_tree(draft, declared)
    files = {relative: _strict_json(draft / relative, root=draft) for relative in declared}
    sealed_files, receipt = seal_enterprise_quote_pilot_payloads(files)
    payloads = sealed_enterprise_quote_pilot_bytes(
        sealed_files, expected_pack_digest=receipt["pack_digest"],
    )
    output = _output_location(output_root)
    runtime = _publish_files(output=output, payloads=payloads, validate=True)
    if runtime is None or runtime.pack_digest != receipt["pack_digest"]:
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_EXPORT_IDENTITY_MISMATCH")
    return {**receipt, "output_locator": f"directory:{output.name}"}


def seal_enterprise_quote_pilot_payloads(
    files: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Seal and admit private draft values without writing them to the OS.

    Both directory and private-draft authoring use this single content compiler.
    Private request content never needs a crash-persistent scratch directory.
    """

    copied: dict[str, dict[str, Any]] = {}
    for relative, value in files.items():
        if not isinstance(relative, str) or not isinstance(value, Mapping):
            raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_MEMORY_PAYLOAD_INVALID")
        raw = _json_bytes(dict(value))
        if len(raw) > MAX_AUTHORING_JSON_BYTES:
            raise EnterpriseQuotePilotAuthoringError(
                "PILOT_AUTHOR_JSON_SIZE_LIMIT",
                relative,
            )
        copied[relative] = json.loads(raw)
    try:
        manifest_value = copied[PILOT_PACK_FILENAME]
    except KeyError as exc:
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_FILE_SET_MISMATCH") from exc
    manifest = _parse_pack(manifest_value)
    declared = set(_declared_paths(manifest))
    if set(copied) != declared:
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_FILE_SET_MISMATCH")
    profile_value = copied[manifest.profile_path]
    source_roots = profile_value.get("source_roots")
    components = profile_value.get("components")
    if not isinstance(source_roots, list) or not isinstance(components, list):
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_PROFILE_STRUCTURE_INVALID")
    source_by_locator = {
        item.get("locator"): item for item in source_roots if isinstance(item, dict)
    }
    component_by_kind = {
        item.get("kind"): item for item in components if isinstance(item, dict)
    }
    if len(source_by_locator) != len(source_roots) or len(component_by_kind) != len(components):
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_PROFILE_BINDING_DUPLICATE")

    payloads: dict[str, bytes] = {PILOT_PACK_FILENAME: _json_bytes(manifest_value)}
    root_digests: dict[str, str] = {}
    projection_digests: dict[str, str] = {}
    component_digests: dict[str, str] = {}
    for binding in manifest.components:
        root_value = copied[binding.path]
        projection = root_value.get("projection")
        if not isinstance(projection, dict) or not projection:
            raise EnterpriseQuotePilotAuthoringError(
                "PILOT_AUTHOR_COMPONENT_PROJECTION_INVALID",
                binding.component_kind.value,
            )
        projection_digest = sha256_digest(projection)
        root_value["projection_digest"] = projection_digest
        if "digest" in root_value:
            root_value["digest"] = sha256_digest(
                {key: item for key, item in root_value.items() if key != "digest"}
            )
        try:
            EnterpriseSeedComponentRoot.model_validate(root_value)
        except ValidationError as exc:
            raise EnterpriseQuotePilotAuthoringError(
                "PILOT_AUTHOR_COMPONENT_SCHEMA_INVALID",
                f"{binding.component_kind.value}:{exc}",
            ) from exc
        raw = _json_bytes(root_value)
        observed_digest = _raw_digest(raw)
        source = source_by_locator.get(binding.locator)
        component = component_by_kind.get(binding.component_kind.value)
        if not isinstance(source, dict) or not isinstance(component, dict):
            raise EnterpriseQuotePilotAuthoringError(
                "PILOT_AUTHOR_PROFILE_BINDING_MISSING",
                binding.component_kind.value,
            )
        source["declared_digest"] = observed_digest
        try:
            component_digest = component_declaration_digest_for_observed_root(
                component_kind=SeedComponentKind(binding.component_kind.value),
                source_root_id=str(source["id"]),
                revision=str(source["revision"]),
                media_type=str(source["media_type"]),
                observed_digest=observed_digest,
            )
        except (KeyError, ValueError) as exc:
            raise EnterpriseQuotePilotAuthoringError(
                "PILOT_AUTHOR_SOURCE_DECLARATION_INVALID",
                binding.component_kind.value,
            ) from exc
        component["declared_digest"] = component_digest
        payloads[binding.path] = raw
        root_digests[binding.component_kind.value] = observed_digest
        projection_digests[binding.component_kind.value] = projection_digest
        component_digests[binding.component_kind.value] = component_digest

    profile_value["digest"] = sha256_digest(
        {key: item for key, item in profile_value.items() if key != "digest"}
    )
    try:
        profile = EnterpriseSeedProfile.model_validate(profile_value)
    except ValidationError as exc:
        raise EnterpriseQuotePilotAuthoringError(
            "PILOT_AUTHOR_PROFILE_SCHEMA_INVALID", str(exc)
        ) from exc
    payloads[manifest.profile_path] = _json_bytes(profile_value)
    validation = validate_enterprise_quote_pilot_payloads(payloads)
    sealed_files = {
        relative: json.loads(raw.decode("utf-8")) for relative, raw in payloads.items()
    }
    receipt = {
        "schema_version": "orgrebase.enterprise-quote-pilot-seal-receipt.v1",
        "status": "SEALED_AND_PREFLIGHT_PASSED",
        "output_locator": "memory:retention-managed-private-draft",
        "pack_id": validation.manifest.pack_id,
        "pack_revision": validation.manifest.revision,
        "adapter_id": validation.manifest.adapter_id,
        "organization_id": validation.profile.organization_id,
        "synthetic": validation.profile.synthetic,
        "data_class": validation.profile.data_class.value,
        "pack_digest": validation.pack_digest,
        "profile_ref": profile.ref,
        "profile_digest": profile.digest,
        "source_root_digests": root_digests,
        "projection_digests": projection_digests,
        "component_declaration_digests": component_digests,
        "source_admission_receipt_digest": validation.source_admission.digest,
        "runtime_projection_receipt_digest": validation.runtime_projection.digest,
        "universe_digest": validation.universe.digest,
        "file_count": len(payloads),
        "canonical_target_writes": 0,
        "claim_ceiling_after_acceptance": "PILOT_READY_CONTROLLED_LOCAL",
        "real_enterprise_validated": "NOT_RUN",
        "production_ready": False,
    }
    return sealed_files, receipt


def sealed_enterprise_quote_pilot_bytes(
    files: Mapping[str, Mapping[str, Any]], *, expected_pack_digest: str,
    exact_texts: Mapping[str, str] | None = None,
) -> dict[str, bytes]:
    """Export existing sealed content exactly; never repair or reseal it.

    The indent-2 UTF-8 encoding is part of this export contract, since component
    source declarations bind raw bytes. Existing directory Pack identities stay
    unchanged. Old memory-only receipts remain readable but require a new seal
    before export; they are never silently reinterpreted as directory receipts.
    """

    if exact_texts is None:
        payloads = {relative: _json_bytes(dict(value)) for relative, value in files.items()}
    else:
        if set(exact_texts) != set(files) or any(not isinstance(value, str) for value in exact_texts.values()):
            raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_EXPORT_BYTES_INVALID")
        payloads = {relative: value.encode("utf-8") for relative, value in exact_texts.items()}
        if sha256_digest({relative: json.loads(raw) for relative, raw in payloads.items()}) != sha256_digest(files):
            raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_EXPORT_CONTENT_MISMATCH")
    validation = validate_enterprise_quote_pilot_payloads(payloads)
    if validation.pack_digest != expected_pack_digest:
        raise EnterpriseQuotePilotAuthoringError("PILOT_AUTHOR_EXPORT_IDENTITY_MISMATCH")
    return payloads


def preflight_enterprise_quote_pilot_payloads(
    files: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate private draft values in memory without publishing a Pack."""

    sealed_files, sealed = seal_enterprise_quote_pilot_payloads(files)
    payloads = {relative: _json_bytes(value) for relative, value in sealed_files.items()}
    validation = validate_enterprise_quote_pilot_payloads(payloads)
    inputs = enterprise_input_preflight(
        validation.profile,
        source_admission=validation.source_admission,
    )
    return {
        "schema_version": "orgrebase.enterprise-quote-pilot-draft-preflight.v1",
        "status": "DRAFT_PREFLIGHT_PASSED",
        "pack_id": sealed["pack_id"],
        "pack_revision": sealed["pack_revision"],
        "adapter_id": sealed["adapter_id"],
        "organization_id": sealed["organization_id"],
        "candidate_pack_digest": sealed["pack_digest"],
        "profile_ref": sealed["profile_ref"],
        "profile_digest": sealed["profile_digest"],
        "source_admission_receipt_digest": sealed["source_admission_receipt_digest"],
        "runtime_projection_receipt_digest": sealed["runtime_projection_receipt_digest"],
        "universe_digest": sealed["universe_digest"],
        "required_inputs": inputs["inputs"],
        "profile_admission_status": inputs["status"],
        "sealed_output_created": False,
        "profile_admitted_for_workspace": False,
        "workspace_activated": False,
        "authority_created_by_preflight": False,
        "canonical_target_writes": 0,
        "real_enterprise_validated": "NOT_RUN",
    }


def preflight_enterprise_quote_pilot_draft(draft_root: str | Path) -> dict[str, Any]:
    """Validate one editable draft without publishing or activating a Pack.

    The existing seal path remains the single implementation of digest
    recomputation and strict Pack admission.  This function runs that path in an
    ephemeral directory, returns the would-be exact identities, and discards the
    temporary sealed bytes before returning.
    """

    with tempfile.TemporaryDirectory(prefix="orgrebase-pilot-preflight-") as temporary:
        candidate = Path(temporary) / "candidate"
        sealed = seal_enterprise_quote_pilot_pack(draft_root, candidate)
        runtime = load_enterprise_quote_pilot_pack(candidate)
        inputs = enterprise_input_preflight(
            runtime.profile,
            source_admission=runtime.source_admission,
        )

    return {
        "schema_version": "orgrebase.enterprise-quote-pilot-draft-preflight.v1",
        "status": "DRAFT_PREFLIGHT_PASSED",
        "pack_id": sealed["pack_id"],
        "pack_revision": sealed["pack_revision"],
        "adapter_id": sealed["adapter_id"],
        "organization_id": sealed["organization_id"],
        "candidate_pack_digest": sealed["pack_digest"],
        "profile_ref": sealed["profile_ref"],
        "profile_digest": sealed["profile_digest"],
        "source_admission_receipt_digest": sealed["source_admission_receipt_digest"],
        "runtime_projection_receipt_digest": sealed["runtime_projection_receipt_digest"],
        "required_inputs": inputs["inputs"],
        "profile_admission_status": inputs["status"],
        "sealed_output_created": False,
        "profile_admitted_for_workspace": False,
        "workspace_activated": False,
        "authority_created_by_preflight": False,
        "canonical_target_writes": 0,
        "real_enterprise_validated": "NOT_RUN",
    }


__all__ = (
    "EnterpriseQuotePilotAuthoringError",
    "initialize_enterprise_quote_pilot_draft",
    "preflight_enterprise_quote_pilot_draft",
    "preflight_enterprise_quote_pilot_payloads",
    "seal_enterprise_quote_pilot_pack",
    "seal_enterprise_quote_pilot_payloads",
)
