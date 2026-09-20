"""Target conflict identity, separate from the approved request and receipt identity.

This persisted projection belongs to StateStore schema v4. Changes to target
equivalence require a migration; old approved requests remain byte-for-byte intact.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit
from uuid import UUID

import httpx2 as httpx

from orgrebase.digest import sha256_digest

DATAVERSE_DRAFT_ACTION = "dataverse.quote.update_draft_metadata"
_QUOTE_PATH = re.compile(r"/api/data/v9\.2/quotes\(([0-9a-f-]{36})\)")


def dataverse_target_identity(target_key: str) -> str:
    """Coalesce origin spelling aliases without rewriting the approved target key."""
    try:
        if not isinstance(target_key, str) or not 1 <= len(target_key) <= 1024:
            raise ValueError
        parsed = urlsplit(target_key)
        resource = _QUOTE_PATH.fullmatch(parsed.path)
        if (any(ord(character) <= 32 or ord(character) == 127 for character in target_key)
                or parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or resource is None
                or str(UUID(resource[1])) != resource[1]):
            raise ValueError
        # Use the same public URL parser as the HTTP transport for IDNA and ports.
        endpoint = httpx.URL(target_key)
        host = endpoint.raw_host.decode("ascii").removesuffix(".")
        if not host or "%" in host or "\\" in host:
            raise ValueError
        if ":" in host:
            host = f"[{ipaddress.IPv6Address(host).compressed}]"
        elif not re.fullmatch(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*", host):
            raise ValueError
        port = endpoint.port
        if port is not None and not 0 < port < 65536:
            raise ValueError
        authority = host if port in (None, 443) else f"{host}:{port}"
        return f"https://{authority}{parsed.path}"
    except (ValueError, httpx.InvalidURL):
        raise ValueError("DATAVERSE_TARGET_IDENTITY_INVALID") from None


def effect_barrier_key(*, tenant_id: str, target_key: str, action: str) -> str:
    target = dataverse_target_identity(target_key) if action == DATAVERSE_DRAFT_ACTION else target_key
    return sha256_digest({"tenant": tenant_id, "target": target})
