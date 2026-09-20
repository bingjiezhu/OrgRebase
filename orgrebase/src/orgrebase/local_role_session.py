"""Explicit loopback-only role rehearsal using the application authorization chain.

These sessions select server-defined example identities. They do not authenticate
employees and are never available in an enterprise deployment.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import ip_address
from threading import RLock
from typing import Any
from urllib.parse import urlsplit

from orgrebase.auth import _ROLE_ACTIONS, AuthenticationError, Principal

LOCAL_SESSION_COOKIE = "orgrebase_local_session"
LOCAL_SESSION_ISSUER = "urn:orgrebase:controlled-local-session"


@dataclass(frozen=True)
class LocalRoleSessionSettings:
    public_origin: str
    session_seconds: int = 3600

    def __post_init__(self) -> None:
        try:
            parsed = urlsplit(self.public_origin)
            valid = (parsed.scheme in {"http", "https"}
                     and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                     and parsed.username is None and parsed.password is None
                     and not parsed.path and not parsed.query and not parsed.fragment
                     and parsed.port is not None and 1 <= parsed.port <= 65535
                     and parsed.netloc == parsed.netloc.lower())
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("AUTH_LOCAL_SESSION_LOOPBACK_ORIGIN_REQUIRED")
        if type(self.session_seconds) is not int or not 60 <= self.session_seconds <= 3600:
            raise ValueError("AUTH_LOCAL_SESSION_LIFETIME_INVALID")

    @property
    def secure(self) -> bool:
        return self.public_origin.startswith("https:")


@dataclass(frozen=True)
class _Session:
    actor_id: str | None
    csrf: str
    expires_at: int


class LocalRoleSessions:
    """Opaque, per-browser sessions; restart or switching invalidates credentials."""

    def __init__(self, settings: LocalRoleSessionSettings, workspace: Callable[[], Any], *,
                 skill_steward_actor: str) -> None:
        self.settings, self.workspace = settings, workspace
        self.skill_steward_actor = skill_steward_actor
        self._sessions: dict[str, _Session] = {}
        self._lock = RLock()

    def require_request(self, request: Any) -> None:
        if f"{request.url.scheme}://{request.url.netloc}" != self.settings.public_origin:
            raise AuthenticationError("AUTH_REQUEST_ORIGIN_DENIED", 403)
        try:
            address = ip_address(request.client.host)
            local = address.is_loopback or bool(getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback)
        except (ValueError, AttributeError):
            local = False
        if not local or request.headers.get("sec-fetch-site") == "cross-site":
            raise AuthenticationError("AUTH_LOCAL_SESSION_LOOPBACK_REQUIRED", 403)

    def actors(self) -> tuple[dict[str, Any], ...]:
        workspace = self.workspace()
        entries: dict[str, dict[str, Any]] = {}

        def add(actor_id: str, label: str, label_en: str, role: str) -> None:
            if actor_id in entries:
                if role not in entries[actor_id]["roles"]:
                    entries[actor_id]["roles"].append(role)
                return
            entries[actor_id] = {"actor_id": actor_id, "label": label, "label_en": label_en, "roles": [role]}

        for actor in workspace.profile.governance.admission_authority_refs:
            add(actor, "企业接入负责人", "Organization onboarding owner", "governor")
        add(workspace.profile.default_task.actor_id, "业务发起人", "Business requester", "operator")
        domains: dict[str, set[str]] = {}
        for resource in workspace.enterprise_binding.resources:
            domains.setdefault(resource.owner_id, set()).add(resource.domain_id)
        labels = {"finance": ("财务负责人", "Finance owner"), "product": ("产品负责人", "Product owner"),
                  "legal": ("法务负责人", "Legal owner"), "gtm": ("市场与商业化负责人", "Go-to-market owner")}
        for actor in workspace.profile.governance.owner_refs:
            if actor == workspace.profile.default_task.actor_id:
                continue
            owned = domains.get(actor, set())
            domain = next(iter(owned)) if len(owned) == 1 else None
            label, label_en = labels.get(domain, (f"领域负责人 · {actor}", f"Domain owner · {actor}"))
            add(actor, label, label_en, "approver")
        add("human:local-change-executor", "变更执行人", "Change executor", "executor")
        add(self.skill_steward_actor, "Skill 维护负责人", "Skill steward", "governor")
        names = [entry["label"] for entry in entries.values()]
        for entry in entries.values():
            if names.count(entry["label"]) > 1:
                entry["label"] += " · " + entry["actor_id"]
                entry["label_en"] += " · " + entry["actor_id"]
        return tuple(entries.values())

    def _actor(self, actor_id: str) -> dict[str, Any]:
        for actor in self.actors():
            if actor["actor_id"] == actor_id:
                return actor
        raise AuthenticationError("AUTH_LOCAL_ACTOR_DENIED", 403)

    def _principal(self, actor_id: str, expires_at: int) -> Principal:
        actor = self._actor(actor_id)
        return Principal(LOCAL_SESSION_ISSUER, actor_id, self.workspace().profile.organization_id,
                         actor_id, frozenset(actor["roles"]), expires_at)

    @staticmethod
    def _key(cookie: str | None) -> str:
        if not isinstance(cookie, str) or len(cookie) != 43:
            raise AuthenticationError("AUTH_LOCAL_SESSION_REQUIRED")
        return hashlib.sha256(cookie.encode()).hexdigest()

    def read(self, cookie: str | None) -> _Session:
        with self._lock:
            key = self._key(cookie)
            session = self._sessions.get(key)
            if session is None or session.expires_at <= int(time.time()):
                self._sessions.pop(key, None)
                raise AuthenticationError("AUTH_LOCAL_SESSION_REQUIRED")
            return session

    def create(self, actor_id: str | None = None, *, previous: str | None = None) -> str:
        if actor_id is not None:
            self._actor(actor_id)
        with self._lock:
            now = int(time.time())
            self._sessions = {key: value for key, value in self._sessions.items() if value.expires_at > now}
            if previous is not None:
                self.read(previous)
                self._sessions.pop(self._key(previous), None)
            if len(self._sessions) >= 256:
                raise AuthenticationError("AUTH_LOCAL_SESSION_LIMIT", 503)
            cookie = secrets.token_urlsafe(32)
            self._sessions[self._key(cookie)] = _Session(actor_id, secrets.token_urlsafe(32), now + self.settings.session_seconds)
            return cookie

    def authenticate(self, cookie: str | None) -> tuple[Principal, str, int]:
        session = self.read(cookie)
        if session.actor_id is None:
            raise AuthenticationError("AUTH_LOCAL_ACTOR_REQUIRED")
        return self._principal(session.actor_id, session.expires_at), session.csrf, session.expires_at

    def logout(self, cookie: str | None) -> None:
        with self._lock:
            self._sessions.pop(self._key(cookie), None)

    def verify_membership(self, subject: str, actor_id: str, action: str) -> None:
        """Check current server-owned roles without minting a temporary session."""
        if subject != actor_id:
            raise AuthenticationError("AUTH_IDENTITY_CHANGED", 403)
        actor = self._actor(actor_id)
        if not any(action in _ROLE_ACTIONS[role] for role in actor["roles"]):
            raise AuthenticationError("AUTH_ACTION_DENIED", 403)

    def members_for_action(self, action: str) -> tuple[dict[str, str], ...]:
        members = []
        for actor in self.actors():
            try:
                self.verify_membership(actor["actor_id"], actor["actor_id"], action)
            except AuthenticationError:
                continue
            members.append({"subject": actor["actor_id"], "actor_id": actor["actor_id"]})
        return tuple(members)

    def view(self, cookie: str | None = None) -> dict[str, Any]:
        session = self.read(cookie) if cookie is not None else None
        principal = self._principal(session.actor_id, session.expires_at) if session and session.actor_id else None
        actor = self._actor(session.actor_id) if session and session.actor_id else None
        return {
            "mode": "local", "identity_source": "controlled-local-session",
            "authentication_required": True, "authenticated": principal is not None,
            "principal": {"issuer": principal.issuer, "subject": principal.subject,
                          "actor_id": principal.actor_id, "tenant_id": principal.tenant_id,
                          "roles": sorted(principal.roles), "display_name": actor["label"],
                          "display_name_en": actor["label_en"]} if principal else None,
            "csrf_token": session.csrf if session else None, "expires_at": session.expires_at if session else None,
            "actors": list(self.actors()), "switch_actor_url": "/api/session/local-actor",
            "login_url": None, "logout_url": "/api/session/logout",
        }
