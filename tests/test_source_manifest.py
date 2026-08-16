from __future__ import annotations

import pytest

from scripts.verify_source_manifest import ManifestError, verify_manifest, write_manifest


def test_source_manifest_is_closed_world(tmp_path) -> None:
    (tmp_path / "orgrebase").mkdir()
    (tmp_path / "README.md").write_text("bundle\n", encoding="utf-8")
    (tmp_path / "orgrebase" / "module.py").write_text("value = 1\n", encoding="utf-8")

    assert write_manifest(tmp_path) == 2
    assert verify_manifest(tmp_path) == 2

    (tmp_path / "orgrebase" / "extra.py").write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="UNLISTED_FILES"):
        verify_manifest(tmp_path)


def test_source_manifest_rejects_private_or_generated_paths(tmp_path) -> None:
    private = tmp_path / "orgrebase" / "evidence" / "agentteams" / "live-sources"
    private.mkdir(parents=True)
    (private / "session.jsonl").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ManifestError, match="BANNED_RELEASE_PATHS"):
        write_manifest(tmp_path)
