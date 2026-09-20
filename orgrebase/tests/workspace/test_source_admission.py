from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.workspace import source_admission as source_module
from orgrebase.workspace.domain_agents import default_source_values
from orgrebase.workspace.profile import (
    EnterpriseSeedAdmissionError,
    SeedComponentKind,
    admit_enterprise_seed_profile,
    northstar_acme_quote_profile,
    parse_enterprise_seed_profile,
    supplier_shadow_intake_profile,
)
from orgrebase.workspace.reference_profiles import (
    VERACIER_SC008_AFTER_STATUS,
    VERACIER_SC008_BEFORE_STATUS,
    VERACIER_SC008_CHANGE_REF,
    VERACIER_SC008_DEMAND_REF,
    VERACIER_SC008_SUBJECT_REF,
    supplier_sc008_source_aligned_profile,
)
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.source_admission import (
    VERACIER_SC008_CHANGED_FIELD_ALLOWLIST,
    DirectorySeedSourceResolver,
    EnterpriseSeedProfileMigrationReceipt,
    EnterpriseSeedSourceAdmissionReceipt,
    admit_enterprise_seed_sources,
    admit_veracier_sc008_profile_migration,
    exact_locator_assets,
    expected_raw_digest,
    verify_reference_runtime_projections,
)


def _unsigned(profile) -> dict[str, object]:
    return profile.model_dump(mode="json", exclude={"digest"})


def _redirect_one_asset(
    monkeypatch: pytest.MonkeyPatch,
    *,
    relative_asset: str,
    replacement: Path,
) -> None:
    original = source_module.runtime_asset_path

    def resolve(relative: str | Path) -> Path:
        if Path(relative).as_posix() == relative_asset:
            return replacement
        return original(relative)

    monkeypatch.setattr(source_module, "runtime_asset_path", resolve)


def test_ten_exact_sources_are_real_bytes_with_deterministic_receipts() -> None:
    allowlist = exact_locator_assets()
    assert len(allowlist) == 10
    assert set(allowlist) == {
        f"packaged://northstar/{kind.value.lower()}@r1"
        for kind in SeedComponentKind
    } | {
        f"fixture://veracier/{kind.value.lower()}@r1"
        for kind in SeedComponentKind
    }

    for profile in (northstar_acme_quote_profile(), supplier_shadow_intake_profile()):
        first = admit_enterprise_seed_sources(profile)
        second = admit_enterprise_seed_sources(profile)
        assert first == second
        assert first.digest == second.digest
        assert first.verdict == "ADMITTED"
        assert first.canonical_target_writes == 0
        assert len(first.root_observations) == 5
        assert len(first.component_admissions) == 5
        assert tuple(item.component_kind for item in first.root_observations) == tuple(
            SeedComponentKind
        )
        for observation in first.root_observations:
            assert observation.observed_digest == expected_raw_digest(
                observation.locator
            )
            assert observation.byte_length > 0
            assert "RAW_BYTES_SHA256_MATCH" in observation.reason_codes


def test_complete_packaged_resolver_adds_r2_without_rewriting_historical_view() -> None:
    historical = exact_locator_assets()
    complete = exact_locator_assets(include_successors=True)
    assert len(historical) == 10
    assert len(complete) == 15
    assert complete | historical == complete
    assert set(complete) - set(historical) == {
        f"fixture://veracier/{kind.value.lower()}@r2" for kind in SeedComponentKind
    }


def test_veracier_r1_bytes_and_profile_remain_frozen_while_r2_aligns_sc008() -> None:
    historical = supplier_shadow_intake_profile()
    successor = supplier_sc008_source_aligned_profile()
    example = Path(__file__).resolve().parents[2] / (
        "examples/enterprise-seed/veracier-supplier-sc008-source-aligned-r2.json"
    )

    assert historical.ref == "profile:veracier-supplier-shadow@r1"
    assert historical.digest == (
        "sha256:4f60cf1b06451778cfec0e624a57fc5f9d133307a12e4af22dec9d85b94020a6"
    )
    assert historical.default_task.input_values[0].value == "supplier:atlas"
    assert historical.change_family[0].object_id == "supplier:atlas.status"

    assert successor.ref == "profile:veracier-supplier-shadow@r2"
    assert successor.digest == (
        "sha256:82af1e95b91b42fbc007700fd6c4b69d3a22f15e90385ab739fe80da47dac4d8"
    )
    assert parse_enterprise_seed_profile(json.loads(example.read_text(encoding="utf-8"))) == successor
    assert {item.key: item.value for item in successor.default_task.input_values} == {
        "supplier_id": VERACIER_SC008_SUBJECT_REF,
        "demand_ref": VERACIER_SC008_DEMAND_REF,
    }
    change = successor.change_family[0]
    assert change.change_id == VERACIER_SC008_CHANGE_REF
    assert change.object_id == f"{VERACIER_SC008_SUBJECT_REF}.status"
    assert (change.base_version, change.proposed_version) == (
        VERACIER_SC008_BEFORE_STATUS,
        VERACIER_SC008_AFTER_STATUS,
    )
    receipt = admit_enterprise_seed_sources(successor)
    assert receipt.profile_ref == successor.ref
    assert receipt.profile_digest == successor.digest
    assert all(item.locator.endswith("@r2") for item in receipt.root_observations)


def test_veracier_r1_to_r2_migration_is_exact_allowlisted_and_zero_write() -> None:
    receipt = admit_veracier_sc008_profile_migration(
        supplier_shadow_intake_profile(),
        supplier_sc008_source_aligned_profile(),
    )
    assert receipt.predecessor_profile_ref.endswith("@r1")
    assert receipt.successor_profile_ref.endswith("@r2")
    assert receipt.predecessor_profile_digest != receipt.successor_profile_digest
    assert receipt.changed_fields == VERACIER_SC008_CHANGED_FIELD_ALLOWLIST
    assert receipt.reason == "ROOT_IDENTITY_ALIGNMENT"
    assert receipt.canonical_target_writes == 0

    payload = receipt.model_dump(mode="json", exclude={"digest"})
    payload["changed_fields"] = list(receipt.changed_fields[:-1])
    with pytest.raises(
        ValueError,
        match="SOURCE_PROFILE_MIGRATION_CHANGED_FIELDS_INVALID",
    ):
        EnterpriseSeedProfileMigrationReceipt.model_validate(payload)


@pytest.mark.parametrize(
    ("mutate", "value"),
    (
        ("supplier", "supplier:atlas"),
        ("demand", "demand:wrong"),
        ("before", "v4"),
        ("after", "v5"),
        ("scenario", "undeclared-scenario"),
    ),
)
def test_veracier_r2_root_fractures_fail_before_source_dereference(
    monkeypatch: pytest.MonkeyPatch,
    mutate: str,
    value: str,
) -> None:
    payload = _unsigned(supplier_sc008_source_aligned_profile())
    if mutate == "supplier":
        payload["default_task"]["input_values"][0]["value"] = value  # type: ignore[index]
    elif mutate == "demand":
        payload["default_task"]["input_values"][1]["value"] = value  # type: ignore[index]
    elif mutate == "before":
        payload["change_family"][0]["base_version"] = value  # type: ignore[index]
    elif mutate == "after":
        payload["change_family"][0]["proposed_version"] = value  # type: ignore[index]
    else:
        payload["scenario_id"] = value
    attacked = parse_enterprise_seed_profile(payload)

    def forbidden(_relative: str | Path) -> Path:
        raise AssertionError("invalid migration reached packaged Source dereference")

    monkeypatch.setattr(source_module, "runtime_asset_path", forbidden)
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="SOURCE_PROFILE_MIGRATION_SUCCESSOR_MISMATCH",
    ):
        admit_enterprise_seed_sources(attacked)


def test_directory_resolver_admits_exact_r2_bytes_with_no_path_authority(
    tmp_path: Path,
) -> None:
    profile = supplier_sc008_source_aligned_profile()
    complete = exact_locator_assets(include_successors=True)
    mapping: dict[str, str] = {}
    for source_root in profile.source_roots:
        relative = complete[source_root.locator]
        destination = tmp_path / f"{source_root.id.rsplit(':', 1)[1]}.json"
        destination.write_bytes(source_module.runtime_asset_path(relative).read_bytes())
        mapping[source_root.locator] = destination.name

    receipt = admit_enterprise_seed_sources(
        profile,
        resolver=DirectorySeedSourceResolver(tmp_path, mapping),
    )
    packaged = admit_enterprise_seed_sources(profile)
    assert tuple(item.observed_digest for item in receipt.root_observations) == tuple(
        item.observed_digest for item in packaged.root_observations
    )
    assert all(
        item.logical_asset_ref.startswith("directory://")
        for item in receipt.root_observations
    )
    assert receipt.limitations[0] == "INJECTED_DIRECTORY_EXACT_LOCATORS_ONLY"
    assert receipt.canonical_target_writes == 0


def test_directory_resolver_rejects_mapping_escape_unknown_and_changed_bytes(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="SOURCE_DIRECTORY_MAPPING_INVALID",
    ):
        DirectorySeedSourceResolver(
            tmp_path,
            {"fixture://veracier/domain@r2": "../domain.json"},
        )

    resolver = DirectorySeedSourceResolver(tmp_path, {})
    with pytest.raises(EnterpriseSeedAdmissionError, match="SOURCE_LOCATOR_NOT_ALLOWLISTED"):
        resolver.resolve("fixture://veracier/domain@r2")

    profile = supplier_sc008_source_aligned_profile()
    complete = exact_locator_assets(include_successors=True)
    mapping: dict[str, str] = {}
    for source_root in profile.source_roots:
        relative = complete[source_root.locator]
        destination = tmp_path / f"{source_root.id.rsplit(':', 1)[1]}.json"
        raw = source_module.runtime_asset_path(relative).read_bytes()
        destination.write_bytes(raw + (b" " if source_root.locator.endswith("domain@r2") else b""))
        mapping[source_root.locator] = destination.name
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="SOURCE_ROOT_DECLARED_DIGEST_MISMATCH",
    ):
        admit_enterprise_seed_sources(
            profile,
            resolver=DirectorySeedSourceResolver(tmp_path, mapping),
        )


def test_source_component_profile_and_runtime_digest_chain_closes() -> None:
    profile = northstar_acme_quote_profile()
    source_receipt = admit_enterprise_seed_sources(profile)
    admission = admit_enterprise_seed_profile(
        profile,
        source_admission=source_receipt,
    )
    runtime = verify_reference_runtime_projections(profile, source_receipt)

    assert admission.source_admission_receipt_digest == source_receipt.digest
    assert admission.source_profile_projection_digest == (
        source_receipt.profile_projection_digest
    )
    assert admission.admitted_source_root_digests == tuple(
        item.observed_digest for item in source_receipt.root_observations
    )
    assert admission.component_admission_digests == tuple(
        item.digest for item in source_receipt.component_admissions
    )
    assert runtime.source_admission_receipt_digest == source_receipt.digest
    assert all(item.status == "MATCH" for item in runtime.observations)
    assert tuple(item.component_kind for item in runtime.observations) == tuple(
        SeedComponentKind
    )


@pytest.mark.parametrize(
    "locator",
    (
        "https://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "../fixtures/enterprise-seed/northstar/domain.json",
        "env://${SECRET}/domain.json",
        "unknown://northstar/domain@r1",
    ),
)
def test_unknown_locator_is_rejected_before_any_dereference(
    monkeypatch: pytest.MonkeyPatch,
    locator: str,
) -> None:
    payload = _unsigned(northstar_acme_quote_profile())
    payload["source_roots"][0]["locator"] = locator  # type: ignore[index]
    profile = parse_enterprise_seed_profile(payload)

    def forbidden(_relative: str | Path) -> Path:
        raise AssertionError("unknown locator reached the filesystem resolver")

    monkeypatch.setattr(source_module, "runtime_asset_path", forbidden)
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="SOURCE_LOCATOR_NOT_ALLOWLISTED",
    ):
        admit_enterprise_seed_sources(profile)


def test_changed_source_byte_fails_before_database_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = "fixtures/enterprise-seed/northstar/domain.json"
    original = source_module.runtime_asset_path(relative)
    changed = tmp_path / "changed-domain.json"
    changed.write_bytes(original.read_bytes() + b" ")
    _redirect_one_asset(
        monkeypatch,
        relative_asset=relative,
        replacement=changed,
    )
    database = tmp_path / "must-not-exist.sqlite"
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="SOURCE_ROOT_DECLARED_DIGEST_MISMATCH",
    ):
        WorkspaceService(store_path=database)
    assert not database.exists()


def test_runtime_knowledge_drift_fails_before_database_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = default_source_values

    def drifted():
        values = original()
        values["currency"] = replace(values["currency"], value="GBP")
        return values

    monkeypatch.setattr(
        "orgrebase.workspace.domain_agents.default_source_values",
        drifted,
    )
    database = tmp_path / "runtime-drift.sqlite"
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="RUNTIME_PROJECTION_DIGEST_MISMATCH:KNOWLEDGE",
    ):
        WorkspaceService(store_path=database)
    assert not database.exists()


@pytest.mark.parametrize(
    ("raw", "code"),
    (
        (
            b'{"schema_version":"x","schema_version":"y"}',
            "SOURCE_JSON_DUPLICATE_KEY",
        ),
        (b"\xff\xfe\x00", "SOURCE_ASSET_NOT_UTF8"),
        (b"[]", "SOURCE_ASSET_ROOT_NOT_OBJECT"),
    ),
)
def test_malformed_source_bytes_are_rejected_strictly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
    code: str,
) -> None:
    selected = tmp_path / "malformed.json"
    selected.write_bytes(raw)
    monkeypatch.setattr(source_module, "runtime_asset_path", lambda _relative: selected)
    with pytest.raises(EnterpriseSeedAdmissionError, match=code):
        source_module._load_component_root_once(
            "packaged://northstar/domain@r1"
        )


def test_oversized_source_is_rejected_before_json_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "oversized.json"
    selected.write_bytes(b" " * (source_module.MAX_SOURCE_BYTES + 1))
    monkeypatch.setattr(source_module, "runtime_asset_path", lambda _relative: selected)
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="SOURCE_ASSET_SIZE_LIMIT_EXCEEDED",
    ):
        source_module._load_component_root_once(
            "packaged://northstar/domain@r1"
        )


def test_component_envelope_projection_digest_tamper_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = "fixtures/enterprise-seed/northstar/domain.json"
    value = json.loads(source_module.runtime_asset_path(relative).read_text())
    value["projection"]["scenario_id"] = "tampered"
    selected = tmp_path / "projection-tamper.json"
    selected.write_text(json.dumps(value), encoding="utf-8")
    _redirect_one_asset(
        monkeypatch,
        relative_asset=relative,
        replacement=selected,
    )
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="SOURCE_COMPONENT_SCHEMA_INVALID",
    ):
        admit_enterprise_seed_sources(northstar_acme_quote_profile())


def test_component_organization_mismatch_is_rejected_after_raw_digest_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = "fixtures/enterprise-seed/northstar/domain.json"
    value = json.loads(source_module.runtime_asset_path(relative).read_text())
    value["organization_id"] = "org:attacker"
    selected = tmp_path / "wrong-org.json"
    selected.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    raw_digest = f"sha256:{hashlib.sha256(selected.read_bytes()).hexdigest()}"

    payload = _unsigned(northstar_acme_quote_profile())
    payload["source_roots"][0]["declared_digest"] = raw_digest  # type: ignore[index]
    component_payload = {
        "schema_version": "orgrebase.enterprise-seed-component-admission.v1",
        "component_kind": "DOMAIN",
        "ordered_source_roots": [
            {
                "id": "source:northstar:domain",
                "revision": "r1",
                "media_type": source_module.SOURCE_MEDIA_TYPE,
                "observed_digest": raw_digest,
            }
        ],
    }
    payload["components"][0]["declared_digest"] = sha256_digest(component_payload)  # type: ignore[index]
    profile = parse_enterprise_seed_profile(payload)
    _redirect_one_asset(
        monkeypatch,
        relative_asset=relative,
        replacement=selected,
    )
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="SOURCE_COMPONENT_ORGANIZATION_MISMATCH",
    ):
        admit_enterprise_seed_sources(profile)


def test_source_receipt_rejects_cross_field_tamper() -> None:
    receipt = admit_enterprise_seed_sources(northstar_acme_quote_profile())
    payload = receipt.model_dump(mode="json", exclude={"digest"})
    payload["profile_projection_digest"] = "sha256:" + "0" * 64
    with pytest.raises(
        ValueError,
        match="SOURCE_ADMISSION_PROFILE_PROJECTION_DIGEST_MISMATCH",
    ):
        EnterpriseSeedSourceAdmissionReceipt.model_validate(payload)

    payload = receipt.model_dump(mode="json", exclude={"digest"})
    payload["root_observations"] = list(reversed(payload["root_observations"]))
    with pytest.raises(
        ValueError,
        match="SOURCE_ADMISSION_COMPONENT_ORDER_INVALID",
    ):
        EnterpriseSeedSourceAdmissionReceipt.model_validate(payload)


def test_formation_binding_persists_all_four_digest_layers_and_reopens(
    tmp_path: Path,
) -> None:
    database = tmp_path / "bound.sqlite"
    service = WorkspaceService(store_path=database)
    try:
        service.form_quote()
        binding = service.state()["enterprise_seed_runtime_binding"]["binding"]
        assert binding["profile"]["digest"] == service.profile.digest
        assert binding["source_admission_receipt_digest"] == (
            service.source_admission.digest
        )
        assert binding["source_profile_projection_digest"] == (
            service.source_admission.profile_projection_digest
        )
        assert binding["admitted_source_root_digests"] == [
            item.observed_digest for item in service.source_admission.root_observations
        ]
        assert binding["component_admission_digests"] == [
            item.digest for item in service.source_admission.component_admissions
        ]
        assert binding["runtime_projection_digest"] == (
            service.runtime_projection.runtime_projection_digest
        )
        assert len(binding["runtime_projection_bindings"]) == 5
    finally:
        service.close()

    reopened = WorkspaceService.reopen(database)
    try:
        assert reopened.state()["stage"] == "CURRENT"
    finally:
        reopened.close()


def test_reopen_source_drift_fails_closed_without_touching_persisted_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "reopen-drift.sqlite"
    service = WorkspaceService(store_path=database)
    try:
        service.form_quote()
    finally:
        service.close()
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    relative = "fixtures/enterprise-seed/northstar/domain.json"
    original = source_module.runtime_asset_path(relative)
    changed = tmp_path / "changed-on-reopen.json"
    changed.write_bytes(original.read_bytes() + b"\n")
    _redirect_one_asset(
        monkeypatch,
        relative_asset=relative,
        replacement=changed,
    )
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="SOURCE_ROOT_DECLARED_DIGEST_MISMATCH",
    ):
        WorkspaceService.reopen(database)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_veracier_sources_admit_but_cannot_enter_reference_runtime(
    tmp_path: Path,
) -> None:
    profile = supplier_shadow_intake_profile()
    source_receipt = admit_enterprise_seed_sources(profile)
    assert source_receipt.verdict == "ADMITTED"
    admission = admit_enterprise_seed_profile(
        profile,
        source_admission=source_receipt,
    )
    assert not admission.reference_runtime_compatible
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="RUNTIME_PROJECTION_INTAKE_ONLY_PROFILE",
    ):
        verify_reference_runtime_projections(profile, source_receipt)

    database = tmp_path / "veracier.sqlite"
    with pytest.raises(
        EnterpriseSeedAdmissionError,
        match="UNSUPPORTED_HANDLER_PROFILE",
    ):
        WorkspaceService(store_path=database, profile=profile)
    assert not database.exists()
