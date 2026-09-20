from __future__ import annotations

import json
from pathlib import Path

import pytest

from orgrebase.agentteams_source import (
    default_agentteams_checkout,
    load_agentteams_source,
    load_teamharness_lock,
    packaged_agentteams_bundle,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    assets = tmp_path / "agentteams"
    assets.mkdir()
    for name in ("source-lock.json", "teamharness-lock.json"):
        (assets / name).write_bytes((ROOT / "agentteams" / name).read_bytes())
    return tmp_path


@pytest.mark.parametrize(
    ("field", "value"),
    (("commit", "latest"), ("tag", "main"), ("repository", "https://example.test/fork")),
)
def test_source_identity_rejects_unpinned_or_unexpected_origins(
    source_root: Path, field: str, value: str
) -> None:
    path = source_root / "agentteams/source-lock.json"
    lock = json.loads(path.read_text())
    lock[field] = value
    path.write_text(json.dumps(lock))
    with pytest.raises(ValueError, match="AGENTTEAMS_SOURCE_LOCK_INVALID"):
        load_agentteams_source(source_root)


@pytest.mark.parametrize("field", ("tag", "commit", "upstream"))
def test_teamharness_source_cannot_disagree_with_active_source(source_root: Path, field: str) -> None:
    path = source_root / "agentteams/teamharness-lock.json"
    lock = json.loads(path.read_text())
    lock[field] = "different"
    path.write_text(json.dumps(lock))
    with pytest.raises(ValueError, match="AGENTTEAMS_SOURCE_IDENTITY_MISMATCH"):
        load_teamharness_lock(source_root)


@pytest.mark.parametrize("relative", ("../bundle", "/bundle", "vendor/agentteams/../../outside"))
def test_release_bundle_must_stay_inside_vendor(source_root: Path, relative: str) -> None:
    path = source_root / "agentteams/teamharness-lock.json"
    lock = json.loads(path.read_text())
    lock["offline_bundle"]["path"] = relative
    path.write_text(json.dumps(lock))
    with pytest.raises(ValueError, match="AGENTTEAMS_BUNDLE_PATH_ESCAPE"):
        packaged_agentteams_bundle(source_root)


def test_checkout_directory_is_independent_of_release_version(source_root: Path) -> None:
    assert default_agentteams_checkout(source_root) == source_root / ".tmp/agentteams"
    source = load_agentteams_source(source_root)
    source.require_teamharness_identity(load_teamharness_lock(source_root))
