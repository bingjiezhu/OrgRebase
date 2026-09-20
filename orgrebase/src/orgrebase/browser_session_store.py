"""Tenant-wide browser sessions; business workspace selection grants no identity."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from orgrebase.auth import AuthenticationError
from orgrebase.database import (
    browser_session_revocations,
    browser_sessions,
    execute_core,
    oidc_login_transactions,
)
from orgrebase.store import StateStore


def opaque_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _scope(issuer: str, kind: str, value: str) -> str:
    return opaque_digest(f"{len(issuer)}:{issuer}:{kind}:{value}")


class BrowserSessionStore:
    def __init__(self, store: StateStore) -> None:
        self.store = store
        if not store.tenant_id:
            raise ValueError("AUTH_SESSION_TENANT_REQUIRED")

    def save_login(self, values: dict[str, Any], *, now: int) -> None:
        with self.store.transaction() as connection:
            for table in (oidc_login_transactions, browser_sessions, browser_session_revocations):
                execute_core(connection, delete(table).where(table.c.expires_at <= now))
            execute_core(connection, insert(oidc_login_transactions).values(**values))

    def consume_login(self, transaction_id: str, browser_digest: str, *, now: int) -> dict[str, Any]:
        with self.store.transaction() as connection:
            row = execute_core(connection, delete(oidc_login_transactions).where(
                oidc_login_transactions.c.transaction_id == transaction_id,
                oidc_login_transactions.c.browser_digest == browser_digest,
                oidc_login_transactions.c.expires_at > now,
            ).returning(*oidc_login_transactions.c)).fetchone()
            if row is None:
                raise AuthenticationError("AUTH_LOGIN_STATE_INVALID")
            return dict(row)

    def read_session(self, session_id: str, *, now: int) -> dict[str, Any]:
        with self.store.read_connection() as connection:
            row = execute_core(connection, select(browser_sessions).where(
                browser_sessions.c.session_id == session_id,
            )).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise AuthenticationError("AUTH_SESSION_REVOKED")
        if row["expires_at"] <= now:
            raise AuthenticationError("AUTH_SESSION_EXPIRED")
        return dict(row)

    def _lock_scopes(self, connection, issuer: str, subject: str | None, sid: str | None,
                     *, until: int) -> list[dict[str, Any]]:
        entries = [("SUBJECT", subject), ("SID", sid)]
        scopes = sorted((_scope(issuer, kind, value), kind) for kind, value in entries if value)
        statement = postgres_insert if self.store.backend == "postgresql" else sqlite_insert
        locked = []
        for scope_id, kind in scopes:
            execute_core(connection, statement(browser_session_revocations).values(
                scope_id=scope_id, kind=kind, revoked_before=-1, expires_at=until,
            ).on_conflict_do_nothing(index_elements=[browser_session_revocations.c.scope_id]))
            row = execute_core(connection, select(browser_session_revocations).where(
                browser_session_revocations.c.scope_id == scope_id,
            ).with_for_update()).fetchone()
            if row["expires_at"] < until:
                execute_core(connection, update(browser_session_revocations).where(
                    browser_session_revocations.c.scope_id == scope_id,
                ).values(expires_at=until))
            locked.append(dict(row))
        return locked

    def create_session(self, values: dict[str, Any], *, login_started_at: int, fence_until: int) -> None:
        with self.store.transaction() as connection:
            scopes = self._lock_scopes(connection, values["issuer"], values["subject"], values["sid"], until=fence_until)
            if any((row["kind"] == "SID" and row["revoked_before"] >= 0)
                   or row["revoked_before"] >= login_started_at for row in scopes):
                raise AuthenticationError("AUTH_SESSION_REVOKED")
            execute_core(connection, insert(browser_sessions).values(**values))

    def revoke_session(self, session_id: str, *, now: int) -> None:
        with self.store.transaction() as connection:
            execute_core(connection, update(browser_sessions).where(
                browser_sessions.c.session_id == session_id, browser_sessions.c.revoked_at.is_(None),
            ).values(revoked_at=now, payload_ciphertext=None))

    def backchannel_logout(self, *, issuer: str, subject: str | None, sid: str | None,
                           event_id: str, issued_at: int, now: int, fence_until: int) -> None:
        with self.store.transaction() as connection:
            scopes = self._lock_scopes(connection, issuer, subject, sid, until=fence_until)
            statement = postgres_insert if self.store.backend == "postgresql" else sqlite_insert
            inserted = execute_core(connection, statement(browser_session_revocations).values(
                scope_id=_scope(issuer, "EVENT", event_id), kind="EVENT", revoked_before=issued_at,
                expires_at=fence_until,
            ).on_conflict_do_nothing(index_elements=[browser_session_revocations.c.scope_id]))
            if inserted.rowcount != 1:
                raise AuthenticationError("AUTH_LOGOUT_REPLAY")
            # A sid-bound logout invalidates only that provider session. A sub-only
            # logout invalidates every browser session for that subject.
            affected = [row for row in scopes if row["kind"] == ("SID" if sid else "SUBJECT")]
            for row in affected:
                execute_core(connection, update(browser_session_revocations).where(
                    browser_session_revocations.c.scope_id == row["scope_id"],
                ).values(revoked_before=max(issued_at, row["revoked_before"]), expires_at=max(fence_until, row["expires_at"])))
            match = [browser_sessions.c.issuer == issuer, browser_sessions.c.revoked_at.is_(None)]
            if subject:
                match.append(browser_sessions.c.subject == subject)
            if sid:
                match.append(browser_sessions.c.sid == sid)
            execute_core(connection, update(browser_sessions).where(*match).values(revoked_at=now, payload_ciphertext=None))
