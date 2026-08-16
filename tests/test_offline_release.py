from __future__ import annotations

import tarfile
import zipfile

from scripts import build_offline_release as release


def test_offline_release_preserves_license_and_package_boundary(tmp_path) -> None:
    config = release.load_config()
    wheel = release.build_wheel(config, tmp_path)
    sdist = release.build_sdist(config, tmp_path)

    dist_info = config.dist_info
    expected_license_files = {
        f"{dist_info}/licenses/LICENSE",
        f"{dist_info}/licenses/COMMERCIAL-LICENSE.md",
        f"{dist_info}/licenses/NOTICE.md",
    }
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata = archive.read(f"{dist_info}/METADATA").decode("utf-8")
    assert expected_license_files <= names
    assert "License: PolyForm-Noncommercial-1.0.0" in metadata
    assert "License-File: COMMERCIAL-LICENSE.md" in metadata
    assert "License-File: LICENSE" in metadata
    assert "License-File: NOTICE.md" in metadata
    assert "Provides-Extra: dev" in metadata
    assert "orgrebase/_assets/evidence/release-facts.json" in names

    prefix = f"{config.normalized_name}-{config.version}"
    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = set(archive.getnames())
    for relative in (
        "LICENSE",
        "COMMERCIAL-LICENSE.md",
        "NOTICE.md",
        "RELEASE-VERIFICATION.md",
        "requirements.txt",
        "requirements-dev.txt",
    ):
        assert f"{prefix}/{relative}" in sdist_names
    assert not any("evidence/agentteams/live-sources" in name for name in sdist_names)
    assert not any("nonce-ledger" in name for name in sdist_names)
