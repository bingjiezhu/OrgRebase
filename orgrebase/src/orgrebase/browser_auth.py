"""OIDC code flow and encrypted, revocable server-side browser sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2 as httpx
import jwt
from authlib.integrations.httpx_client import OAuth2Client
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from orgrebase.auth import AuthenticationError, JWTAuthenticator, Principal
from orgrebase.browser_session_store import BrowserSessionStore, opaque_digest

SESSION_COOKIE = "__Host-orgrebase_session"
LOGIN_COOKIE = "__Host-orgrebase_login"
_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"


def https_origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("AUTH_HTTPS_ENDPOINT_REQUIRED")
    return f"https://{parsed.netloc}"


def _private_file(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 16_384
                    or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077):
                raise ValueError("permissions")
            return stream.read(16_385).strip()
    except (OSError, ValueError) as exc:
        raise AuthenticationError("AUTH_PRIVATE_CONFIGURATION_UNAVAILABLE", 503) from exc


@dataclass(frozen=True)
class BrowserSessionSettings:
    client_id: str
    client_secret_file: Path
    encryption_key_file: Path
    public_origin: str
    scopes: tuple[str, ...] = ("openid",)
    session_seconds: int = 900
    trusted_endpoint_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.public_origin != https_origin(self.public_origin):
            raise ValueError("AUTH_PUBLIC_ORIGIN_INVALID")
        if not self.client_id.strip() or len(self.client_id) > 512:
            raise ValueError("AUTH_CLIENT_ID_INVALID")
        if (not self.scopes or "openid" not in self.scopes or "offline_access" in self.scopes
                or len(set(self.scopes)) != len(self.scopes)
                or any(not scope or any(char.isspace() for char in scope) for scope in self.scopes)):
            raise ValueError("AUTH_OIDC_SCOPES_INVALID")
        if isinstance(self.session_seconds, bool) or not 30 <= self.session_seconds <= 3600:
            raise ValueError("AUTH_SESSION_LIFETIME_INVALID")
        for origin in self.trusted_endpoint_origins:
            if origin != https_origin(origin):
                raise ValueError("AUTH_ENDPOINT_ORIGIN_INVALID")

    @property
    def redirect_uri(self) -> str:
        return self.public_origin + "/api/session/callback"

    def validate_private_files(self) -> None:
        try:
            key = base64.b64decode(_private_file(self.encryption_key_file), altchars=b"-_", validate=True)
            secret = _private_file(self.client_secret_file).decode("utf-8")
            if len(key) != 32 or not secret or len(secret) > 4096 or any(char in secret for char in "\r\n\x00"):
                raise ValueError("configuration")
        except (UnicodeError, ValueError) as exc:
            raise AuthenticationError("AUTH_PRIVATE_CONFIGURATION_INVALID", 503) from exc


class BrowserSessions:
    def __init__(self, settings: BrowserSessionSettings, authenticator: JWTAuthenticator,
                 repository: BrowserSessionStore) -> None:
        self.settings = settings
        self.authenticator = authenticator
        self.repository = repository
        self._metadata: dict[str, Any] | None = None
        self._metadata_expires = 0.0
        self._metadata_lock = threading.Lock()
        if repository.store.tenant_id != authenticator.settings.tenant_id:
            raise ValueError("AUTH_SESSION_TENANT_MISMATCH")
        if settings.client_id == authenticator.settings.audience:
            raise ValueError("AUTH_DISTINCT_CLIENT_AND_API_AUDIENCE_REQUIRED")
        self._key()
        self._secret()

    def _key(self) -> bytes:
        try:
            key = base64.b64decode(_private_file(self.settings.encryption_key_file), altchars=b"-_", validate=True)
            if len(key) != 32:
                raise ValueError("length")
            return key
        except ValueError as exc:
            raise AuthenticationError("AUTH_SESSION_KEY_INVALID", 503) from exc

    def _secret(self) -> str:
        try:
            value = _private_file(self.settings.client_secret_file).decode("utf-8")
            if not value or len(value) > 4096 or any(char in value for char in "\r\n\x00"):
                raise ValueError("secret")
            return value
        except (UnicodeError, ValueError) as exc:
            raise AuthenticationError("AUTH_CLIENT_SECRET_INVALID", 503) from exc

    def _aad(self, kind: str, key: str) -> bytes:
        return json.dumps([self.authenticator.settings.tenant_id, self.authenticator.settings.issuer,
                           self.settings.client_id, self.settings.public_origin, kind, key], separators=(",", ":")).encode()

    def _encrypt(self, kind: str, key: str, payload: dict[str, Any]) -> str:
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._key()).encrypt(nonce, json.dumps(payload, separators=(",", ":")).encode(), self._aad(kind, key))
        return base64.urlsafe_b64encode(nonce + ciphertext).decode()

    def _decrypt(self, kind: str, key: str, ciphertext: str) -> dict[str, Any]:
        try:
            raw = base64.b64decode(ciphertext, altchars=b"-_", validate=True)
            return json.loads(AESGCM(self._key()).decrypt(raw[:12], raw[12:], self._aad(kind, key)))
        except (ValueError, InvalidTag, TypeError, UnicodeError) as exc:
            raise AuthenticationError("AUTH_SESSION_INVALID") from exc

    def _endpoint(self, endpoint: Any) -> str:
        if not isinstance(endpoint, str) or len(endpoint) > 2048:
            raise AuthenticationError("AUTH_DISCOVERY_INVALID", 503)
        try:
            origin = https_origin(endpoint)
            allowed = {https_origin(self.authenticator.settings.issuer), *self.settings.trusted_endpoint_origins}
            if origin not in allowed:
                raise ValueError("origin")
        except ValueError as exc:
            raise AuthenticationError("AUTH_DISCOVERY_INVALID", 503) from exc
        return endpoint

    def metadata(self) -> dict[str, Any]:
        with self._metadata_lock:
            if self._metadata is not None and time.monotonic() < self._metadata_expires:
                return dict(self._metadata)
            try:
                with (httpx.Client(verify=self.authenticator.settings.ssl_context(), trust_env=False,
                                   follow_redirects=False, timeout=5) as client,
                      client.stream("GET", self.authenticator.settings.issuer.rstrip("/") + "/.well-known/openid-configuration") as response):
                    response.raise_for_status()
                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > 65_536:
                            raise ValueError("size")
                data = json.loads(chunks)
                if (data["issuer"] != self.authenticator.settings.issuer
                        or data["jwks_uri"] != self.authenticator.settings.jwks_url
                        or "code" not in data["response_types_supported"]
                        or "S256" not in data["code_challenge_methods_supported"]
                        or "client_secret_basic" not in data.get("token_endpoint_auth_methods_supported", ["client_secret_basic"])):
                    raise ValueError("metadata")
                self._endpoint(data["authorization_endpoint"])
                self._endpoint(data["token_endpoint"])
                self._metadata, self._metadata_expires = data, time.monotonic() + 300
                return dict(data)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                raise AuthenticationError("AUTH_PROVIDER_UNAVAILABLE", 503) from exc

    def _client(self) -> OAuth2Client:
        return OAuth2Client(self.settings.client_id, self._secret(),
                            token_endpoint_auth_method="client_secret_basic", code_challenge_method="S256",
                            scope=" ".join(self.settings.scopes), redirect_uri=self.settings.redirect_uri,
                            verify=self.authenticator.settings.ssl_context(), trust_env=False,
                            follow_redirects=False, timeout=5)

    def begin_login(self) -> tuple[str, str]:
        metadata = self.metadata()
        state, browser, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
        now = int(time.time())
        with self._client() as client:
            uri, _ = client.create_authorization_url(metadata["authorization_endpoint"], state=state,
                                                      nonce=nonce, code_verifier=verifier, response_mode="query")
        key = opaque_digest(state)
        payload = {"nonce": nonce, "verifier": verifier, "token_endpoint": metadata["token_endpoint"],
                   "created_at": now, "expires_at": now + 300,
                   "issuer_response_required": metadata.get("authorization_response_iss_parameter_supported") is True}
        self.repository.save_login({"transaction_id": key, "browser_digest": opaque_digest(browser),
                                    "payload_ciphertext": self._encrypt("login", key, payload),
                                    "created_at": now, "expires_at": now + 300}, now=now)
        return uri, browser

    def _signed_claims(self, token: str, *, required: list[str]) -> dict[str, Any]:
        try:
            if not isinstance(token, str) or len(token) > 16_384:
                raise ValueError("token")
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in {"RS256", "ES256"} or not isinstance(header.get("kid"), str) or not header["kid"]:
                raise ValueError("algorithm")
            key = self.authenticator.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=["RS256", "ES256"],
                                issuer=self.authenticator.settings.issuer, audience=self.settings.client_id,
                                options={"require": required}, leeway=0)
            if isinstance(claims["iat"], bool) or not isinstance(claims["iat"], int):
                raise ValueError("issued")
            audience = claims["aud"]
            if ((isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != self.settings.client_id)
                    or ("azp" in claims and claims["azp"] != self.settings.client_id)):
                raise ValueError("authorized party")
            return claims
        except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
            raise AuthenticationError("AUTH_OIDC_TOKEN_INVALID") from exc

    def complete_login(self, params: dict[str, str], browser: str | None) -> str:
        state = params.get("state", "")
        if not browser or not state or len(state) > 128 or len(browser) > 128:
            raise AuthenticationError("AUTH_LOGIN_STATE_INVALID")
        now = int(time.time())
        key = opaque_digest(state)
        transaction = self.repository.consume_login(key, opaque_digest(browser), now=now)
        login = self._decrypt("login", key, transaction["payload_ciphertext"])
        if (login["created_at"], login["expires_at"]) != (transaction["created_at"], transaction["expires_at"]):
            raise AuthenticationError("AUTH_LOGIN_STATE_INVALID")
        if ((login["issuer_response_required"] or "iss" in params)
                and params.get("iss") != self.authenticator.settings.issuer):
            raise AuthenticationError("AUTH_LOGIN_ISSUER_MISMATCH")
        code = params.get("code", "")
        if "error" in params or not code or len(code) > 4096:
            raise AuthenticationError("AUTH_LOGIN_DENIED")
        try:
            with self._client() as client:
                tokens = client.fetch_token(self._endpoint(login["token_endpoint"]), code=code,
                                             code_verifier=login["verifier"], grant_type="authorization_code")
            if str(tokens.get("token_type", "")).lower() != "bearer":
                raise ValueError("type")
            access = tokens["access_token"]
            principal = self.authenticator.authenticate("Bearer " + access)
            claims = self._signed_claims(tokens["id_token"], required=["iss", "aud", "sub", "iat", "exp", "nonce"])
            if (claims["sub"] != principal.subject or not isinstance(claims["nonce"], str)
                    or not hmac.compare_digest(claims["nonce"], login["nonce"])
                    or isinstance(claims["exp"], bool) or not isinstance(claims["exp"], int)
                    or claims["iat"] < transaction["created_at"]):
                raise ValueError("identity binding")
            expected_hash = base64.urlsafe_b64encode(hashlib.sha256(access.encode()).digest()[:16]).rstrip(b"=").decode()
            if "at_hash" in claims and (not isinstance(claims["at_hash"], str)
                                         or not hmac.compare_digest(claims["at_hash"], expected_hash)):
                raise ValueError("access token binding")
            sid = claims.get("sid")
            if sid is not None and (not isinstance(sid, str) or not sid or len(sid) > 512):
                raise ValueError("session id")
        except AuthenticationError:
            raise
        except Exception as exc:
            # Provider errors may contain the authorization code or credentials.
            raise AuthenticationError("AUTH_LOGIN_TOKEN_EXCHANGE_FAILED") from exc
        expires = min(principal.expires_at, claims["exp"], now + self.settings.session_seconds)
        if expires <= now:
            raise AuthenticationError("AUTH_SESSION_EXPIRED")
        cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        session_id = opaque_digest(cookie)
        payload = {"access_token": access, "csrf_token": csrf, "subject": principal.subject, "sid": sid,
                   "created_at": now, "expires_at": expires}
        self.repository.create_session({"session_id": session_id, "issuer": principal.issuer,
                                        "subject": principal.subject, "sid": sid,
                                        "payload_ciphertext": self._encrypt("session", session_id, payload),
                                        "created_at": now, "expires_at": expires, "revoked_at": None},
                                       login_started_at=transaction["created_at"], fence_until=now + 3900)
        return cookie

    def authenticate(self, cookie: str | None) -> tuple[Principal, str, int]:
        if not cookie or len(cookie) > 128:
            raise AuthenticationError("AUTH_SESSION_REQUIRED")
        key = opaque_digest(cookie)
        session = self.repository.read_session(key, now=int(time.time()))
        payload = self._decrypt("session", key, session["payload_ciphertext"])
        principal = self.authenticator.authenticate("Bearer " + payload["access_token"])
        if (session["issuer"] != principal.issuer or session["subject"] != principal.subject
                or payload["subject"] != principal.subject or payload["sid"] != session["sid"]
                or (payload["created_at"], payload["expires_at"]) != (session["created_at"], session["expires_at"])):
            raise AuthenticationError("AUTH_SESSION_INVALID")
        return principal, payload["csrf_token"], session["expires_at"]

    def logout(self, cookie: str) -> None:
        self.repository.revoke_session(opaque_digest(cookie), now=int(time.time()))

    def backchannel_logout(self, token: str) -> None:
        claims = self._signed_claims(token, required=["iss", "aud", "iat", "jti", "events"])
        now = int(time.time())
        subject, sid = claims.get("sub"), claims.get("sid")
        if ("nonce" in claims or claims["events"] != {_LOGOUT_EVENT: {}}
                or not isinstance(claims["jti"], str) or not claims["jti"] or len(claims["jti"]) > 512
                or not now - 300 <= claims["iat"] <= now or not (subject or sid)
                or any(value is not None and (not isinstance(value, str) or not value or len(value) > 512)
                       for value in (subject, sid))):
            raise AuthenticationError("AUTH_LOGOUT_TOKEN_INVALID")
        self.repository.backchannel_logout(issuer=self.authenticator.settings.issuer, subject=subject, sid=sid,
                                          event_id=claims["jti"], issued_at=claims["iat"], now=now, fence_until=now + 3900)
