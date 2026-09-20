"""Same-origin HTTP boundary for the existing Principal authentication chain."""

from __future__ import annotations

import hmac
import time
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse

from orgrebase.auth import AuthenticationError, JWTAuthenticator
from orgrebase.browser_auth import LOGIN_COOKIE, SESSION_COOKIE, BrowserSessions
from orgrebase.local_role_session import LOCAL_SESSION_COOKIE, LocalRoleSessions

_HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie, Authorization", "Referrer-Policy": "no-referrer"}


def require_browser_origin(request: Request, sessions: BrowserSessions | LocalRoleSessions) -> None:
    if f"{request.url.scheme}://{request.url.netloc}" != sessions.settings.public_origin:
        raise AuthenticationError("AUTH_REQUEST_ORIGIN_DENIED", 403)


def require_csrf(request: Request, sessions: BrowserSessions | LocalRoleSessions, expected: str) -> None:
    require_browser_origin(request, sessions)
    supplied = request.headers.get("x-csrf-token", "")
    if (request.headers.get("origin") != sessions.settings.public_origin
            or not supplied or not hmac.compare_digest(supplied, expected)
            or request.headers.get("sec-fetch-site") not in {None, "same-origin", "none"}):
        raise AuthenticationError("AUTH_CSRF_DENIED", 403)


def session_view(*, production: bool, sessions: BrowserSessions | None, principal=None,
                 csrf_token: str | None = None, expires_at: int | None = None) -> dict:
    return {
        "mode": "oidc" if sessions else "bearer" if production else "local",
        "authentication_required": production or sessions is not None,
        "authenticated": principal is not None,
        "principal": {"actor_id": principal.actor_id, "tenant_id": principal.tenant_id,
                      "roles": sorted(principal.roles)} if principal else None,
        "csrf_token": csrf_token,
        "login_url": "/api/session/login" if sessions else None,
        "logout_url": "/api/session/logout" if sessions else None,
        "expires_at": expires_at,
    }


def session_router(*, production: bool, sessions: BrowserSessions | None,
                   authenticator: JWTAuthenticator | None,
                   local_sessions: LocalRoleSessions | None = None) -> APIRouter:
    router = APIRouter()

    def configured(request: Request) -> BrowserSessions:
        if sessions is None:
            raise AuthenticationError("AUTH_BROWSER_LOGIN_UNAVAILABLE", 404)
        require_browser_origin(request, sessions)
        return sessions

    @router.get("/api/session")
    def read_session(request: Request):
        if local_sessions is not None:
            local_sessions.require_request(request)
            if request.headers.get("authorization") or request.cookies.get(SESSION_COOKIE):
                raise AuthenticationError("AUTH_CREDENTIALS_AMBIGUOUS")
            cookie = request.cookies.get(LOCAL_SESSION_COOKIE)
            try:
                view = local_sessions.view(cookie)
            except AuthenticationError:
                view = local_sessions.view()
            # A late session read must never overwrite a subsequently rotated cookie.
            return JSONResponse(view, headers=_HEADERS)
        principal, csrf, expiry = None, None, None
        if sessions:
            require_browser_origin(request, sessions)
        cookie, bearer = request.cookies.get(SESSION_COOKIE), request.headers.get("authorization")
        if cookie and bearer:
            raise AuthenticationError("AUTH_CREDENTIALS_AMBIGUOUS")
        if cookie and sessions:
            principal, csrf, expiry = sessions.authenticate(cookie)
        elif bearer and authenticator:
            principal = authenticator.authenticate(bearer)
            expiry = principal.expires_at
        return JSONResponse(session_view(production=production, sessions=sessions, principal=principal,
                                         csrf_token=csrf, expires_at=expiry), headers=_HEADERS)

    @router.get("/api/session/login")
    def login(request: Request):
        configured(request)
        if request.query_params:
            raise AuthenticationError("AUTH_LOGIN_PARAMETERS_FORBIDDEN", 400)
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise AuthenticationError("AUTH_LOGIN_ORIGIN_DENIED", 403)
        uri, browser = sessions.begin_login()
        response = RedirectResponse(uri, status_code=302, headers=_HEADERS)
        response.set_cookie(LOGIN_COOKIE, browser, max_age=300, secure=True, httponly=True, samesite="lax", path="/")
        return response

    @router.get("/api/session/callback")
    def callback(request: Request):
        configured(request)
        pairs = list(request.query_params.multi_items())
        # Prevent the application server's access log from retaining authorization
        # codes. The ingress must likewise log the path without query strings.
        request.scope["query_string"] = b""
        params = dict(pairs)
        if len(params) != len(pairs) or set(params) - {"code", "state", "iss", "error", "error_description", "error_uri", "session_state"}:
            raise AuthenticationError("AUTH_LOGIN_PARAMETERS_INVALID", 400)
        cookie = sessions.complete_login(params, request.cookies.get(LOGIN_COOKIE))
        previous = request.cookies.get(SESSION_COOKIE)
        if previous:
            sessions.logout(previous)
        _, _, expiry = sessions.authenticate(cookie)
        response = RedirectResponse(sessions.settings.public_origin + "/", status_code=303, headers=_HEADERS)
        response.delete_cookie(LOGIN_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
        response.set_cookie(SESSION_COOKIE, cookie, max_age=max(0, expiry - int(time.time())),
                            secure=True, httponly=True, samesite="lax", path="/")
        return response

    @router.post("/api/session/logout")
    def logout(request: Request):
        if local_sessions is not None:
            local_sessions.require_request(request)
            cookie = request.cookies.get(LOCAL_SESSION_COOKIE)
            session = local_sessions.read(cookie)
            require_csrf(request, local_sessions, session.csrf)
            anonymous = local_sessions.create(previous=cookie)
            response = JSONResponse(local_sessions.view(anonymous), headers=_HEADERS)
            response.set_cookie(LOCAL_SESSION_COOKIE, anonymous, max_age=local_sessions.settings.session_seconds,
                                secure=local_sessions.settings.secure, httponly=True, samesite="strict", path="/")
            return response
        configured(request)
        cookie = request.cookies.get(SESSION_COOKIE)
        _, csrf, _ = sessions.authenticate(cookie)
        require_csrf(request, sessions, csrf)
        sessions.logout(cookie)
        response = JSONResponse(session_view(production=production, sessions=sessions), headers=_HEADERS)
        response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
        response.delete_cookie(LOGIN_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
        return response

    @router.post("/api/session/backchannel-logout")
    async def backchannel_logout(request: Request):
        configured(request)
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
            raise AuthenticationError("AUTH_LOGOUT_REQUEST_INVALID", 400)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 24_576:
                raise AuthenticationError("AUTH_LOGOUT_REQUEST_INVALID", 400)
        try:
            pairs = parse_qsl(body.decode("utf-8"), strict_parsing=True)
            if len(pairs) != 1 or pairs[0][0] != "logout_token":
                raise ValueError("parameters")
        except (UnicodeError, ValueError) as exc:
            raise AuthenticationError("AUTH_LOGOUT_REQUEST_INVALID", 400) from exc
        await run_in_threadpool(sessions.backchannel_logout, pairs[0][1])
        return JSONResponse({"status": "LOGGED_OUT"}, headers=_HEADERS)

    if local_sessions is not None:
        @router.post("/api/session/local-actor")
        def switch_local_actor(request: Request, payload: dict):
            local_sessions.require_request(request)
            if request.headers.get("authorization") or request.cookies.get(SESSION_COOKIE):
                raise AuthenticationError("AUTH_CREDENTIALS_AMBIGUOUS")
            cookie = request.cookies.get(LOCAL_SESSION_COOKIE)
            try:
                session = local_sessions.read(cookie)
            except AuthenticationError:
                # This non-simple header and exact Origin permit explicit local
                # initialization without issuing credentials from a read request.
                if (request.headers.get("origin") != local_sessions.settings.public_origin
                        or request.headers.get("sec-fetch-site") not in {None, "same-origin", "none"}):
                    raise AuthenticationError("AUTH_LOCAL_SESSION_INITIALIZATION_DENIED", 403) from None
                if request.headers.get("x-orgrebase-local-session") != "initialize":
                    if request.headers.get("x-csrf-token"):
                        raise AuthenticationError("AUTH_LOCAL_SESSION_REQUIRED") from None
                    raise AuthenticationError("AUTH_LOCAL_SESSION_INITIALIZATION_DENIED", 403) from None
                cookie = None
            else:
                require_csrf(request, local_sessions, session.csrf)
            if set(payload) != {"actor_id"} or not isinstance(payload["actor_id"], str):
                raise AuthenticationError("AUTH_LOCAL_ACTOR_REQUEST_INVALID", 400)
            selected = local_sessions.create(payload["actor_id"], previous=cookie)
            response = JSONResponse(local_sessions.view(selected), headers=_HEADERS)
            response.set_cookie(LOCAL_SESSION_COOKIE, selected, max_age=local_sessions.settings.session_seconds,
                                secure=local_sessions.settings.secure, httponly=True, samesite="strict", path="/")
            return response

    return router
