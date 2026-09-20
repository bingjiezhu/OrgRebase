from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from orgrebase.auth import AuthenticationError
from orgrebase.browser_session_store import BrowserSessionStore
from orgrebase.store import StateStore


@pytest.fixture
def sessions():
    with StateStore(tenant_id="org:test") as store:
        yield BrowserSessionStore(store)


def session_values(key="session", subject="person", sid="provider-session"):
    return {"session_id": key, "issuer": "https://issuer.example", "subject": subject, "sid": sid,
            "payload_ciphertext": "encrypted-input", "created_at": 100, "expires_at": 200, "revoked_at": None}


def test_login_state_is_browser_bound_expiring_and_single_use(sessions):
    sessions.save_login({"transaction_id": "state", "browser_digest": "browser", "payload_ciphertext": "encrypted",
                         "created_at": 100, "expires_at": 200}, now=100)
    with pytest.raises(AuthenticationError, match="AUTH_LOGIN_STATE_INVALID"):
        sessions.consume_login("state", "other-browser", now=110)
    assert sessions.consume_login("state", "browser", now=110)["payload_ciphertext"] == "encrypted"
    with pytest.raises(AuthenticationError, match="AUTH_LOGIN_STATE_INVALID"):
        sessions.consume_login("state", "browser", now=110)


def test_session_revocation_and_provider_fence_prevent_late_login(sessions):
    sessions.create_session(session_values(), login_started_at=100, fence_until=4000)
    sessions.backchannel_logout(issuer="https://issuer.example", subject="person", sid="provider-session",
                                event_id="logout", issued_at=110, now=110, fence_until=4010)
    with pytest.raises(AuthenticationError, match="AUTH_SESSION_REVOKED"):
        sessions.read_session("session", now=111)
    with pytest.raises(AuthenticationError, match="AUTH_SESSION_REVOKED"):
        sessions.create_session(session_values("late"), login_started_at=109, fence_until=4010)
    with pytest.raises(AuthenticationError, match="AUTH_SESSION_REVOKED"):
        sessions.create_session(session_values("late-token"), login_started_at=120, fence_until=4010)
    sessions.create_session(session_values("new", sid="new-provider-session"), login_started_at=109, fence_until=4010)
    assert sessions.read_session("new", now=111)
    with pytest.raises(AuthenticationError, match="AUTH_LOGOUT_REPLAY"):
        sessions.backchannel_logout(issuer="https://issuer.example", subject="person", sid="provider-session",
                                    event_id="logout", issued_at=110, now=110, fence_until=4010)


def test_subject_logout_covers_sessions_without_revoking_other_subject(sessions):
    for key, subject, sid in [("a", "person", "sid-a"), ("b", "person", "sid-b"), ("c", "other", "sid-c")]:
        sessions.create_session(session_values(key, subject, sid), login_started_at=100, fence_until=4000)
    sessions.backchannel_logout(issuer="https://issuer.example", subject="person", sid=None,
                                event_id="all", issued_at=110, now=110, fence_until=4010)
    for key in ("a", "b"):
        with pytest.raises(AuthenticationError, match="AUTH_SESSION_REVOKED"):
            sessions.read_session(key, now=111)
    assert sessions.read_session("c", now=111)


@pytest.mark.parametrize("first_operation", ["login", "logout"])
def test_postgres_concurrent_login_and_logout_cannot_leave_an_active_session(postgres_runtime, monkeypatch,
                                                                          first_operation):
    database = postgres_runtime(tenant_id="org:test")
    with StateStore(database["runtime_dsn"], tenant_id="org:test", migrate=False) as first_store, \
         StateStore(database["runtime_dsn"], tenant_id="org:test", migrate=False) as second_store:
        first, second = BrowserSessionStore(first_store), BrowserSessionStore(second_store)
        lock_held, release_lock, contender_started = threading.Event(), threading.Event(), threading.Event()
        original = first._lock_scopes

        def hold_lock(*args, **kwargs):
            rows = original(*args, **kwargs)
            lock_held.set()
            assert release_lock.wait(10)
            return rows

        monkeypatch.setattr(first, "_lock_scopes", hold_lock)

        def operate(repository, operation):
            if operation == "logout":
                repository.backchannel_logout(issuer="https://issuer.example", subject="person", sid="provider-session",
                                               event_id="concurrent-logout", issued_at=110, now=110, fence_until=4010)
                return "REVOKED"
            try:
                repository.create_session(session_values(), login_started_at=100, fence_until=4000)
                return "CREATED"
            except AuthenticationError as error:
                assert error.code == "AUTH_SESSION_REVOKED"
                return "REJECTED"

        def contend():
            contender_started.set()
            return operate(second, "logout" if first_operation == "login" else "login")

        with ThreadPoolExecutor(max_workers=2) as executor:
            leader = executor.submit(operate, first, first_operation)
            try:
                assert lock_held.wait(10)
                follower = executor.submit(contend)
                assert contender_started.wait(10)
            finally:
                release_lock.set()
            expected = ("CREATED", "REVOKED") if first_operation == "login" else ("REVOKED", "REJECTED")
            assert (leader.result(timeout=10), follower.result(timeout=10)) == expected
        with pytest.raises(AuthenticationError, match="AUTH_SESSION_REVOKED"):
            second.read_session("session", now=111)
