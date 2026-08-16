"""Frozen legacy proofs must remain byte-for-byte stable when Workspace seams are unused."""

from __future__ import annotations

from orgrebase.service import OrgRebaseService


def test_legacy_demo_frozen_digests_remain_unchanged() -> None:
    service = OrgRebaseService()
    try:
        preview = service.preview()
        applied = service.apply()
    finally:
        service.store.close()
    assert preview["change_set"].digest == (
        "sha256:b77167b642e10601341ecd82576cd0908754a67ca72595caee29aac3819f6d75"
    )
    assert preview["preview"].digest == (
        "sha256:3f63d00ac12f4fa000ca90e26ce63824877b41f96c4c97705e609c5d94d18741"
    )
    assert preview["minimal_rebase_certificate"].digest == (
        "sha256:2dd3a778bcd936e5d51bc548a25bba758f54c23a6059083db12acb1a59c6603f"
    )
    assert preview["collaboration"]["orchestration_plan"].digest == (
        "sha256:2869a6106aa4b032b4c12370f6e437b3561fc6f4d05c1cd0145bc3bf5738f85d"
    )
    assert applied["receipt"].digest == (
        "sha256:33e9b3167cef4f809a1cd441c401e2d29b236d75aa0cde6a322dcf4a3dd2a592"
    )
