"""Prospective licensing metadata preserves sealed business and Skill history."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.benchmark import OWBBenchmarkRepository
from orgrebase.workspace.skill_packages import (
    EXPECTED_ENTRY_POINTS,
    SkillCandidateOverlayRegistry,
    SkillPackageRegistry,
    _json_schema_valid,
    load_skill_package_snapshot,
    skill_license_files,
    skill_package_snapshot,
)

ROOT = Path(__file__).resolve().parents[2]
LEGACY_LICENSE = "PolyForm-Noncommercial-1.0.0"


def _overlay(registry: SkillPackageRegistry, name: str = "structured-domain-handoff"):
    return SkillCandidateOverlayRegistry(
        registry, candidate_ref="candidate:license-review",
        candidate_digest=sha256_digest("license-review"), proposed_version="9.0.0",
        source_run_id="run:license-review", target_skill=name,
    ).load(name)


def _reseal_snapshot(snapshot):
    manifest = snapshot["manifest"]
    manifest["manifest_digest"] = sha256_digest({
        key: value for key, value in manifest.items() if key != "manifest_digest"
    })
    snapshot["package_digest"] = manifest["manifest_digest"]
    snapshot["digest"] = sha256_digest({
        key: value for key, value in snapshot.items() if key != "digest"
    })
    return snapshot


@pytest.mark.parametrize("name", sorted(EXPECTED_ENTRY_POINTS))
def test_new_overlay_carries_apache_grant_without_rewriting_predecessor(name):
    registry = SkillPackageRegistry(ROOT)
    base = registry.load(name)
    before = skill_package_snapshot(base)
    package = _overlay(registry, name)
    assert package.manifest["license"] == "Apache-2.0"
    grant = package.manifest["license_files"]["LICENSE"]
    license_bytes = (ROOT / "LICENSE").read_bytes()
    assert base64.b64decode(grant["content_base64"], validate=True) == license_bytes
    assert grant["sha256"] == "sha256:" + hashlib.sha256(license_bytes).hexdigest()
    assert grant["size_bytes"] == len(license_bytes)
    assert grant["encoding"] == "base64"
    assert skill_license_files(package) == {"LICENSE": license_bytes}
    assert package.manifest["release_artifact"]["predecessor_package_digest"] == base.package_digest
    assert package.contract == base.contract
    assert package.program == base.program
    assert package.raw_resource_bytes == base.raw_resource_bytes
    assert package.resource_digests == base.resource_digests
    assert base.manifest["license"] == LEGACY_LICENSE
    assert skill_package_snapshot(registry.load(name)) == before
    assert load_skill_package_snapshot(before).package_digest == base.package_digest
    assert skill_license_files(base) == {}
    restored = load_skill_package_snapshot(skill_package_snapshot(package))
    assert restored.manifest == package.manifest
    assert skill_license_files(restored) == {"LICENSE": license_bytes}
    assert restored.raw_resource_bytes == base.raw_resource_bytes
    assert restored.manifest["permissions"] == base.manifest["permissions"]


@pytest.mark.parametrize("schema_name", [
    "skill-package-manifest-v2.schema.json", "workspace-skill-package-manifest.schema.json",
])
def test_manifest_license_schema_accepts_old_and_new_only(schema_name):
    schema = json.loads((ROOT / "schemas" / schema_name).read_bytes())["properties"]["license"]
    assert _json_schema_valid(LEGACY_LICENSE, schema)
    assert _json_schema_valid("Apache-2.0", schema)
    assert not _json_schema_valid("Unreviewed-License", schema)
    assert not _json_schema_valid("MIT", schema)
    assert not _json_schema_valid(None, schema)


@pytest.mark.parametrize("mutation", [
    "bytes", "size", "sha", "encoding", "path", "base64", "self-resealed-notice",
    "unknown-license", "oversize", "missing-attachment",
])
def test_restored_license_files_have_independent_integrity_closure(mutation):
    snapshot = skill_package_snapshot(_overlay(SkillPackageRegistry(ROOT)))
    grant = snapshot["manifest"]["license_files"]["LICENSE"]
    if mutation == "bytes":
        grant["content_base64"] = base64.b64encode(b"truncated notice").decode()
    elif mutation == "size":
        grant["size_bytes"] += 1
    elif mutation == "sha":
        grant["sha256"] = sha256_digest("unrelated notice")
    elif mutation == "encoding":
        grant["encoding"] = "gzip"
    elif mutation == "base64":
        grant["content_base64"] = "not base64 !"
    elif mutation == "self-resealed-notice":
        raw = b"truncated notice"
        grant.update({
            "content_base64": base64.b64encode(raw).decode(),
            "size_bytes": len(raw),
            "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        })
    elif mutation == "unknown-license":
        snapshot["manifest"]["license"] = "Unreviewed-License"
    elif mutation == "oversize":
        grant["content_base64"] = "A" * 45_001
    elif mutation == "missing-attachment":
        snapshot["manifest"].pop("license_files")
    else:
        snapshot["manifest"]["license_files"]["../LICENSE"] = grant
        del snapshot["manifest"]["license_files"]["LICENSE"]
    with pytest.raises(IntegrityError, match="SKILL_LICENSE_FILE_INVALID"):
        load_skill_package_snapshot(_reseal_snapshot(snapshot))


def test_installed_wheel_overlay_uses_only_packaged_grant(monkeypatch, tmp_path):
    package_root = tmp_path / "orgrebase"
    shutil.copytree(ROOT / "configs/workspace", package_root / "_assets/configs/workspace")
    for name in EXPECTED_ENTRY_POINTS:
        shutil.copytree(ROOT / "skills" / name, package_root / "skill_packages" / name)
    license_path = package_root / "_assets/licenses/Apache-2.0.txt"
    license_path.parent.mkdir(parents=True)
    license_path.write_bytes((ROOT / "LICENSE").read_bytes())
    monkeypatch.setattr("orgrebase.workspace.skill_packages.resources.files",
                        lambda name: package_root)
    registry = SkillPackageRegistry(tmp_path / "unavailable-checkout")
    assert registry.resource_mode == "INSTALLED_WHEEL"
    assert skill_license_files(_overlay(registry)) == {"LICENSE": license_path.read_bytes()}
    license_path.write_bytes(b"incomplete license")
    with pytest.raises(IntegrityError, match="SKILL_LICENSE_FILE_INVALID"):
        _overlay(registry)


@pytest.mark.parametrize("schema_name", [
    "skill-package-manifest-v2.schema.json", "workspace-skill-package-manifest.schema.json",
])
def test_license_attachment_schema_is_fixed_and_non_executable(schema_name):
    schema = json.loads((ROOT / "schemas" / schema_name).read_bytes())
    attachment_schema = {
        **schema["properties"]["license_files"], "$defs": schema["$defs"],
    }
    files = _overlay(SkillPackageRegistry(ROOT)).manifest["license_files"]
    assert _json_schema_valid(files, attachment_schema)
    assert not _json_schema_valid({"../LICENSE": files["LICENSE"]}, attachment_schema)
    altered = deepcopy(files)
    altered["LICENSE"]["execute"] = True
    assert not _json_schema_valid(altered, attachment_schema)


@pytest.mark.parametrize("schema_name", [
    "skill-package-manifest-v2.schema.json", "workspace-skill-package-manifest.schema.json",
])
def test_manifest_schema_requires_notice_only_for_current_apache(schema_name):
    schema = json.loads((ROOT / "schemas" / schema_name).read_bytes())
    # Manifest schemas are distribution contracts. The interpreter's smaller
    # input/output vocabulary is deliberately not expanded by this condition.
    assert schema["if"] == {
        "required": ["license"],
        "properties": {"license": {"const": "Apache-2.0"}},
    }
    assert schema["then"] == {"required": ["license_files"]}
    assert _json_schema_valid({"license": "Apache-2.0"}, schema["if"])
    assert not _json_schema_valid({"license": LEGACY_LICENSE}, schema["if"])
    assert not _json_schema_valid({"license": "Apache-2.0"}, schema["then"])
    assert _json_schema_valid(_overlay(SkillPackageRegistry(ROOT)).manifest, schema["then"])


def test_new_overlay_cannot_claim_apache_without_shipping_notice(tmp_path):
    shutil.copytree(ROOT / "configs/workspace", tmp_path / "configs/workspace")
    for name in EXPECTED_ENTRY_POINTS:
        shutil.copytree(ROOT / "skills" / name, tmp_path / "skills" / name)
    registry = SkillPackageRegistry(tmp_path)
    assert registry.load("structured-domain-handoff").manifest["license"] == LEGACY_LICENSE
    with pytest.raises(IntegrityError, match="SKILL_PACKAGE_RESOURCE_MISSING"):
        _overlay(registry)


def test_next_overlay_keeps_verified_notice_and_previous_package_identity():
    registry = SkillPackageRegistry(ROOT)
    first = SkillCandidateOverlayRegistry(
        registry, candidate_ref="candidate:license-first",
        candidate_digest=sha256_digest("license-first"), proposed_version="9.0.0",
        source_run_id="run:license-first",
    )
    parent = first.load("structured-domain-handoff")
    child = _overlay(first)
    assert child.manifest["release_artifact"]["predecessor_package_digest"] == parent.package_digest
    assert skill_license_files(child) == skill_license_files(parent)
    assert child.raw_resource_bytes == parent.raw_resource_bytes
    assert child.resource_digests == parent.resource_digests
    assert load_skill_package_snapshot(skill_package_snapshot(child)).package_digest == child.package_digest


def test_original_polyform_admission_without_snapshot_restores_exact_history(monkeypatch, tmp_path):
    from orgrebase.workspace import pattern_evolution
    from tests.workspace.test_pattern_legacy_migration import (
        _migrate,
        _seed_one_legal_v1_head,
        _service,
    )

    class OriginalMetadataOverlay(SkillCandidateOverlayRegistry):
        """Independent reconstruction of the pre-migration metadata writer."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            package = self._overlay
            manifest = deepcopy(package.manifest)
            manifest["license"] = LEGACY_LICENSE
            manifest.pop("license_files", None)
            digest = sha256_digest({key: value for key, value in manifest.items()
                                    if key != "manifest_digest"})
            manifest["manifest_digest"] = digest
            self._overlay = replace(package, manifest=manifest, package_digest=digest)

    with StateStore(tmp_path / "original-polyform.sqlite3") as store:
        service = _service(store)
        with monkeypatch.context() as original_writer:
            original_writer.setattr(pattern_evolution, "SkillCandidateOverlayRegistry",
                                    OriginalMetadataOverlay)
            source, package, (corpus, suite), _ = _seed_one_legal_v1_head(service)
        assert package.manifest["license"] == LEGACY_LICENSE
        assert "license_files" not in package.manifest
        before = skill_package_snapshot(package)
        candidate_before = deepcopy(service._load(source.payload["candidate_ref"]))
        assert service._package_for_digest(package.name, package.package_digest).manifest == package.manifest
        _migrate(service, source, package.package_digest)
        restored = service._package_for_digest(package.name, package.package_digest)
        assert skill_package_snapshot(restored) == before
        assert restored.raw_resource_bytes == package.raw_resource_bytes
        assert store.get_object(source.id, source.version) == source
        assert service._load(source.payload["candidate_ref"]) == candidate_before
        assert store.verify_event_chain()["status"] == "PASS"
        proposal = service.open_proposal(
            proposal_id="proposal:after-license-migration", corpus_ref=corpus,
            replay_ref=suite, skill_name=package.name, author_id="learner:bounded",
            budget=pattern_evolution.ProposalBudget(
                max_cases=10, max_skill_invocations=100, max_seconds=100,
            ),
        )
        boundary = deepcopy(candidate_before["boundary"])
        boundary.pop("digest")
        boundary["rollback_package_digest"] = package.package_digest
        (candidate,) = service.propose(
            proposal, actor_id="learner:bounded",
            boundary=pattern_evolution.SkillBoundary(**boundary),
        )
        evaluation = service.evaluate(candidate, actor_id=service.evaluator_authority)
        service.decide(
            candidate, evaluation, actor_id=service.governance_authority, verdict="ADMIT",
            expected_candidate_digest=service._load(candidate)["digest"],
            expected_head_package_digest=package.package_digest,
        )
        next_digest = service.current_skill_head_package_digest(package.name)
        successor = service._package_for_digest(package.name, next_digest)
        assert successor.manifest["license"] == "Apache-2.0"
        assert skill_license_files(successor) == {"LICENSE": (ROOT / "LICENSE").read_bytes()}
        assert successor.raw_resource_bytes == package.raw_resource_bytes
        assert skill_package_snapshot(
            service._package_for_digest(package.name, package.package_digest)
        ) == before
        assert service._current_stable_head(package.name).payload["adoption_enabled"] is False


def test_historical_read_requires_exact_known_package_digest():
    registry = SkillPackageRegistry(ROOT)
    fields = {
        "candidate_ref": "candidate:license-review",
        "candidate_digest": sha256_digest("license-review"),
        "proposed_version": "9.0.0", "source_run_id": "run:license-review",
    }
    current = SkillCandidateOverlayRegistry(registry, **fields).load("structured-domain-handoff")
    restored = SkillCandidateOverlayRegistry(
        registry, **fields, historical_package_digest=current.package_digest,
    ).load("structured-domain-handoff")
    assert skill_package_snapshot(restored) == skill_package_snapshot(current)
    with pytest.raises(IntegrityError, match="SKILL_PACKAGE_MANIFEST_DIGEST_MISMATCH"):
        SkillCandidateOverlayRegistry(
            registry, **fields, historical_package_digest=sha256_digest("unknown package"),
        )


@pytest.mark.parametrize("license_id", [LEGACY_LICENSE, "Apache-2.0", "CC0-1.0"])
def test_benchmark_accepts_redistributable_synthetic_license_metadata(license_id):
    repository = OWBBenchmarkRepository()
    repository._licenses = deepcopy(repository._licenses)
    canonical = next(item for item in repository._licenses["assets"]
                     if item["usage"] == "CANONICAL_BENCHMARK")
    canonical["license_spdx"] = license_id
    assert repository.verify()["license_gate"] == "PASS"
    canonical["redistribution"] = "NOT_ALLOWED"
    with pytest.raises(IntegrityError, match="BENCHMARK_LICENSE_GATE_FAILED"):
        repository.verify()


def test_benchmark_unknown_license_remains_rejected():
    repository = OWBBenchmarkRepository()
    repository._licenses = deepcopy(repository._licenses)
    next(item for item in repository._licenses["assets"]
         if item["usage"] == "CANONICAL_BENCHMARK")["license_spdx"] = "Unreviewed-License"
    with pytest.raises(IntegrityError, match="BENCHMARK_LICENSE_GATE_FAILED"):
        repository.verify()
