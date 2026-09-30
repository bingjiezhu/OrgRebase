"""Verified request identity and server-owned tenant membership."""

from __future__ import annotations

import json
import ssl
import time
from collections import OrderedDict
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import Condition
from typing import Any
from urllib.parse import urlparse

import httpx2 as httpx
import jwt
from jwt import PyJWKClient


class AuthenticationError(Exception):
    def __init__(self, code: str, status_code: int = 401) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class Principal:
    issuer: str
    subject: str
    tenant_id: str
    actor_id: str
    roles: frozenset[str]
    expires_at: int


request_principal: ContextVar[Principal | None] = ContextVar("request_principal", default=None)
request_authorization: ContextVar[Callable[[], None] | None] = ContextVar("request_authorization", default=None)

CONTROLLED_LOCAL_SESSION_IDENTITY = "CONTROLLED_LOCAL_SESSION_IDENTITY"
PRINCIPAL_IDENTITY_MODES = frozenset({"VERIFIED_PRINCIPAL_IDENTITY", CONTROLLED_LOCAL_SESSION_IDENTITY})


def identity_claim_boundary(mode: str) -> str:
    return {
        "VERIFIED_PRINCIPAL_IDENTITY": "VERIFIED_JWT_SERVER_MEMBERSHIP",
        CONTROLLED_LOCAL_SESSION_IDENTITY: "CONTROLLED_LOCAL_SESSION_NOT_EXTERNAL_IAM",
        "CONTROLLED_LOCAL_HEADER_IDENTITY": "CONTROLLED_LOCAL_HEADER_IDENTITY_NOT_EXTERNAL_IAM",
        "BODY_ACTOR_COMPATIBILITY": "BODY_ACTOR_COMPATIBILITY_NOT_AUTHENTICATED_IDENTITY",
    }[mode]


def current_authorization() -> Callable[[], None] | None:
    return request_authorization.get()

_ROLE_ACTIONS = {
    "reader": frozenset({"read", "export"}),
    "operator": frozenset({"read", "export", "propose", "erase"}),
    "approver": frozenset({"read", "export", "approve"}),
    "executor": frozenset({"read", "execute"}),
    "governor": frozenset({"read", "govern"}),
    "administrator": frozenset({"read", "export", "propose", "approve", "execute", "govern", "erase", "privacy_manage"}),
}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("AUTH_MEMBERSHIP_DUPLICATE_KEY")
        result[key] = value
    return result


@dataclass(frozen=True)
class IdentitySettings:
    issuer: str
    audience: str
    jwks_url: str
    tenant_id: str
    membership_file: Path
    max_token_lifetime_seconds: int = 900
    ca_bundle: Path | None = None

    def __post_init__(self) -> None:
        for value in (self.issuer, self.jwks_url):
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("AUTH_HTTPS_ENDPOINT_REQUIRED")
        if not self.audience.strip() or not self.tenant_id.strip():
            raise ValueError("AUTH_AUDIENCE_AND_TENANT_REQUIRED")
        if not 30 <= self.max_token_lifetime_seconds <= 3600:
            raise ValueError("AUTH_TOKEN_LIFETIME_INVALID")

    def ssl_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=str(self.ca_bundle) if self.ca_bundle else None)


class VerifiedJWKClient(PyJWKClient):
    """Bound JWKS I/O, unknown-key refreshes and negative caching per process.

    Resolve rotated keys through one in-flight fetch, a refresh cooldown and
    a bounded cache of missing key IDs. Accept raw or parsed cached JWK Sets
    while retaining the library's key validation and signature-key filtering.
    """

    def __init__(
        self,
        *args: Any,
        refresh_cooldown: float = 5.0,
        negative_cache_ttl: float = 5.0,
        max_negative_kids: int = 256,
        monotonic: Callable[[], float] = time.monotonic,
        **kwargs: Any,
    ) -> None:
        if (
            not 0 < refresh_cooldown <= 60
            or not 0 < negative_cache_ttl <= 300
            or not 1 <= max_negative_kids <= 4096
        ):
            raise ValueError("AUTH_JWKS_REFRESH_POLICY_INVALID")
        self._refresh_cooldown = refresh_cooldown
        self._negative_cache_ttl = negative_cache_ttl
        self._max_negative_kids = max_negative_kids
        self._monotonic = monotonic
        self._refresh_condition = Condition()
        self._refreshing = False
        self._last_refresh: float | None = None
        self._negative_kids: OrderedDict[str, float] = OrderedDict()
        super().__init__(*args, **kwargs)

    @staticmethod
    def _signing_keys(data: Any) -> list[Any]:
        if isinstance(data, jwt.PyJWKSet):
            key_set = data
        elif isinstance(data, dict):
            key_set = jwt.PyJWKSet.from_dict(data)
        else:
            raise jwt.PyJWKClientError("The JWKS endpoint did not return a JSON object")
        keys = [
            key
            for key in key_set.keys
            if key.public_key_use in {"sig", None} and key.key_id
        ]
        if not keys:
            raise jwt.PyJWKClientError("The JWKS endpoint did not contain any signing keys")
        return keys

    def _cached_key(self, kid: str) -> Any | None:
        if self.jwk_set_cache is None:
            return None
        data = self.jwk_set_cache.get()
        if data is None:
            return None
        return self.match_kid(self._signing_keys(data), kid)

    def _negative_hit(self, kid: str, now: float) -> bool:
        expired = [cached for cached, deadline in self._negative_kids.items() if deadline <= now]
        for cached in expired:
            self._negative_kids.pop(cached, None)
        deadline = self._negative_kids.get(kid)
        if deadline is None:
            return False
        self._negative_kids.move_to_end(kid)
        return True

    def _remember_negative(self, kid: str, now: float) -> None:
        deadline = now + self._negative_cache_ttl
        if self._last_refresh is not None:
            deadline = min(deadline, self._last_refresh + self._refresh_cooldown)
        self._negative_kids[kid] = deadline
        self._negative_kids.move_to_end(kid)
        while len(self._negative_kids) > self._max_negative_kids:
            self._negative_kids.popitem(last=False)

    @staticmethod
    def _missing_key(kid: str) -> jwt.PyJWKClientError:
        return jwt.PyJWKClientError(f'Unable to find a signing key that matches: "{kid}"')

    def get_signing_key(self, kid: str) -> Any:
        if (
            not isinstance(kid, str)
            or not 1 <= len(kid) <= 256
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in kid)
        ):
            raise jwt.PyJWKClientError("AUTH_JWKS_KID_INVALID")

        cached = self._cached_key(kid)
        if cached is not None:
            with self._refresh_condition:
                self._negative_kids.pop(kid, None)
            return cached

        with self._refresh_condition:
            while True:
                cached = self._cached_key(kid)
                if cached is not None:
                    self._negative_kids.pop(kid, None)
                    return cached
                now = self._monotonic()
                if self._negative_hit(kid, now):
                    raise self._missing_key(kid)
                if self._refreshing:
                    # The network fetch itself is bounded by ``self.timeout``.
                    # A stuck custom transport therefore fails closed rather
                    # than allowing unbounded waiter accumulation.
                    self._refresh_condition.wait(timeout=self.timeout + 1)
                    if self._refreshing:
                        raise jwt.PyJWKClientConnectionError("AUTH_JWKS_UNAVAILABLE")
                    continue
                if (
                    self._last_refresh is not None
                    and now - self._last_refresh < self._refresh_cooldown
                ):
                    self._remember_negative(kid, now)
                    raise self._missing_key(kid)
                self._refreshing = True
                break

        try:
            data = self.fetch_data()
            keys = self._signing_keys(data)
            # Test transports and future PyJWT implementations need not cache
            # inside fetch_data; make the single-flight result explicit.
            if self.jwk_set_cache is not None:
                self.jwk_set_cache.put(data)
            matched = self.match_kid(keys, kid)
        except BaseException:
            with self._refresh_condition:
                self._last_refresh = self._monotonic()
                self._refreshing = False
                self._refresh_condition.notify_all()
            raise

        with self._refresh_condition:
            now = self._monotonic()
            self._last_refresh = now
            self._refreshing = False
            if matched is None:
                self._remember_negative(kid, now)
            else:
                self._negative_kids.pop(kid, None)
            self._refresh_condition.notify_all()
        if matched is None:
            raise self._missing_key(kid)
        return matched

    def fetch_data(self) -> Any:
        deadline = self._monotonic() + self.timeout
        try:
            with (httpx.Client(verify=self.ssl_context or True, follow_redirects=False, trust_env=False,
                               timeout=self.timeout) as client,
                  client.stream("GET", self.uri, headers=self.headers) as response):
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if self._monotonic() >= deadline:
                        raise ValueError("deadline")
                    raw.extend(chunk)
                    if len(raw) > 65_536:
                        raise ValueError("size")
            data = json.loads(raw, object_pairs_hook=_unique_object)
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            raise jwt.PyJWKClientConnectionError("AUTH_JWKS_UNAVAILABLE") from exc
        return data


class JWTAuthenticator:
    def __init__(self, settings: IdentitySettings) -> None:
        self.settings = settings
        self.keys = VerifiedJWKClient(settings.jwks_url, cache_keys=False, lifespan=60, timeout=5,
                                     ssl_context=settings.ssl_context())
        self._memberships()

    def _memberships(self) -> dict[str, dict[str, Any]]:
        try:
            path = self.settings.membership_file
            if path.is_symlink() or path.stat().st_size > 1_048_576:
                raise ValueError("AUTH_MEMBERSHIP_FILE_INVALID")
            payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
            if set(payload) != {"tenant_id", "members"} or payload["tenant_id"] != self.settings.tenant_id:
                raise ValueError("AUTH_MEMBERSHIP_TENANT_MISMATCH")
            members: dict[str, dict[str, Any]] = {}
            for member in payload["members"]:
                if not isinstance(member, dict) or set(member) != {"subject", "actor_id", "roles"}:
                    raise ValueError("AUTH_MEMBERSHIP_INVALID")
                subject, actor = member["subject"], member["actor_id"]
                roles = member["roles"]
                if (
                    not isinstance(subject, str) or not subject.strip()
                    or not isinstance(actor, str) or not actor.strip()
                    or not isinstance(roles, list) or not roles
                    or any(not isinstance(role, str) or role not in _ROLE_ACTIONS for role in roles)
                    or subject in members or len(roles) != len(set(roles))
                ):
                    raise ValueError("AUTH_MEMBERSHIP_INVALID")
                members[subject] = member
            return members
        except (OSError, TypeError, KeyError, ValueError) as exc:
            raise AuthenticationError("AUTH_MEMBERSHIP_UNAVAILABLE", 503) from exc

    def authenticate(self, authorization: str | None) -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthenticationError("AUTH_BEARER_REQUIRED")
        token = authorization[7:]
        if not token or len(token) > 16_384 or token.strip() != token:
            raise AuthenticationError("AUTH_TOKEN_INVALID")
        try:
            header = jwt.get_unverified_header(token)
            if (header.get("alg") not in {"RS256", "ES256"}
                    or not isinstance(header.get("kid"), str) or not header["kid"].strip()
                    or len(header["kid"]) > 256
                    or any(ord(character) < 0x20 or ord(character) == 0x7F for character in header["kid"])):
                raise AuthenticationError("AUTH_TOKEN_INVALID")
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, key.key, algorithms=["RS256", "ES256"],
                audience=self.settings.audience, issuer=self.settings.issuer,
                options={"require": ["iss", "aud", "sub", "iat", "exp"]}, leeway=0,
            )
            issued, expires = claims["iat"], claims["exp"]
            if (
                isinstance(issued, bool) or isinstance(expires, bool)
                or not isinstance(issued, int) or not isinstance(expires, int)
                or not 0 < expires - issued <= self.settings.max_token_lifetime_seconds
                or claims.get("token_use", "access") != "access"
                or not isinstance(claims["sub"], str) or not claims["sub"].strip()
            ):
                raise AuthenticationError("AUTH_TOKEN_INVALID")
        except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
            raise AuthenticationError("AUTH_TOKEN_INVALID") from exc
        member = self._memberships().get(claims["sub"])
        if member is None:
            raise AuthenticationError("AUTH_MEMBERSHIP_DENIED", 403)
        return Principal(
            self.settings.issuer, claims["sub"], self.settings.tenant_id,
            member["actor_id"], frozenset(member["roles"]), expires,
        )

    def verify_membership(self, subject: str, actor_id: str, action: str) -> None:
        """Recheck a recorded approver's current policy without minting a session."""
        member = self._memberships().get(subject)
        if member is None:
            raise AuthenticationError("AUTH_MEMBERSHIP_DENIED", 403)
        if member["actor_id"] != actor_id:
            raise AuthenticationError("AUTH_IDENTITY_CHANGED", 403)
        if not any(action in _ROLE_ACTIONS[role] for role in member["roles"]):
            raise AuthenticationError("AUTH_ACTION_DENIED", 403)

    def members_for_action(self, action: str) -> tuple[dict[str, str], ...]:
        return tuple({"subject": subject, "actor_id": member["actor_id"]}
                     for subject, member in sorted(self._memberships().items())
                     if any(action in _ROLE_ACTIONS[role] for role in member["roles"]))


def request_action(method: str, path: str) -> str:
    if method == "POST" and path in {
        "/api/workspace/experience-assessments",
        "/api/workspace/experience-lessons/decisions",
        "/api/workspace/experience-lessons/deltas/decisions",
        "/api/workspace/governed-learning/quote-recovery-evaluations",
    }:
        return "govern"
    if method == "POST" and path in {
        "/api/workspace/experience-collector/collect",
        "/api/workspace/experience-collector/revisit",
    }:
        # The deployment-bound reader service can record observations only;
        # assess/release/publish remain separate governed commands.
        return "read"
    if path.startswith("/api/workspace/onboarding-draft"):
        return "govern"
    if (method == "POST" and path.startswith("/api/workspace/organization/owner-changes/")
            and path.endswith(("/confirm", "/activate"))):
        return "approve"
    if method == "POST" and path.startswith("/api/workspace/source-readmission-groups/"):
        if path.endswith(("/approve", "/reject")):
            return "approve"
        if path.endswith("/apply"):
            return "execute"
    if method == "POST" and path.startswith("/api/workspace/source-binding/proposals/") and path.endswith(("/confirm", "/revoke")):
        return "approve"
    if path.startswith("/api/workspace/changes/") and path.endswith("/review-observation"):
        return "read"
    if path.startswith("/api/workspace/effect-proposals/"):
        if method in {"GET", "HEAD"}:
            return "read"
        if path.endswith(("/approve", "/reject")):
            return "approve"
        if path.endswith("/actions"):
            return "read"
    if path.startswith("/api/workspace/privacy/"):
        return "privacy_manage"
    if method == "DELETE" and path == "/api/workspace/task-intake/work-description":
        return "erase"
    if method in {"GET", "HEAD"}:
        return "export" if "/export/" in path else "read"
    if path.startswith("/api/workspace/approve/"):
        return "approve"
    if path.startswith("/api/workspace/changes/") and path.endswith(("/reject", "/delegate", "/revoke-delegation", "/return-for-evidence")):
        return "approve"
    if path.startswith("/api/workspace/apply/"):
        return "execute"
    if any(segment in path for segment in ("/skills/", "/experience/", "/oac-adaptation/")):
        return "govern"
    if path.endswith("/verify"):
        return "read"
    return "propose"


def authorize(principal: Principal, action: str, tenant_id: str) -> None:
    if time.time() >= principal.expires_at:
        raise AuthenticationError("AUTH_TOKEN_EXPIRED")
    if principal.tenant_id != tenant_id:
        raise AuthenticationError("AUTH_TENANT_DENIED", 403)
    if not any(action in _ROLE_ACTIONS[role] for role in principal.roles):
        raise AuthenticationError("AUTH_ACTION_DENIED", 403)
