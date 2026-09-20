from __future__ import annotations

from html.parser import HTMLParser

from fastapi.testclient import TestClient

from orgrebase.api import create_app


def test_local_http_schema_and_docs_remain_available_for_integration():
    application = create_app()
    with TestClient(application) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
        assert client.get("/openapi.json").json() == application.openapi()
    assert "/api/workspace/state" in application.openapi()["paths"]


def test_console_revalidates_assets_without_discarding_etags():
    with TestClient(create_app()) as client:
        document = client.get("/")
        assert document.status_code == 200
        assert document.headers["cache-control"] == "no-cache"
        assert document.headers["content-type"].startswith("text/html")
        assert document.headers["x-content-type-options"] == "nosniff"

        asset = client.get("/assets/workspace-shell.js")
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "no-cache"
        assert asset.headers["etag"]
        unchanged = client.get("/assets/workspace-shell.js", headers={"If-None-Match": asset.headers["etag"]})
        assert unchanged.status_code == 304
        assert unchanged.headers["cache-control"] == "no-cache"
        assert unchanged.headers["etag"] == asset.headers["etag"]
        assert unchanged.headers["x-content-type-options"] == "nosniff"
        assert unchanged.content == b""


def test_local_api_and_handled_errors_are_not_stored():
    with TestClient(create_app()) as client:
        for path in ("/api/health", "/readyz", "/api/session", "/api/workspace/operating-model"):
            response = client.get(path)
            assert response.status_code == 200
            assert "no-store" in response.headers["cache-control"]
            assert response.headers["x-content-type-options"] == "nosniff"
        unavailable = client.get("/api/session/login")
        assert unavailable.status_code == 404
        assert unavailable.headers["cache-control"] == "no-store"
        assert unavailable.headers["referrer-policy"] == "no-referrer"
        invalid = client.post("/api/workspace/agentteams-observation", json={"run_id": 3})
        assert invalid.status_code == 422
        assert invalid.headers["cache-control"] == "no-store"
        unknown_asset = client.get("/assets/does-not-exist.js")
        assert unknown_asset.status_code == 404
        assert unknown_asset.headers["cache-control"] == "no-store"
        untrusted = client.get("/api/health", headers={"Host": "attacker.example"})
        assert untrusted.status_code == 400
        assert untrusted.headers["cache-control"] == "no-store"
        for response in (unavailable, invalid, unknown_asset, untrusted):
            assert response.headers["x-content-type-options"] == "nosniff"


def test_console_scripts_and_styles_have_explicit_executable_content_types():
    class Assets(HTMLParser):
        def __init__(self):
            super().__init__()
            self.resources = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "script" and attrs.get("src", "").startswith("/assets/"):
                self.resources.append((attrs["src"], {"text/javascript", "application/javascript"}))
            if tag == "link" and attrs.get("rel") == "stylesheet":
                self.resources.append((attrs["href"], {"text/css"}))

    with TestClient(create_app()) as client:
        assets = Assets()
        assets.feed(client.get("/").text)
        assert assets.resources
        for path, allowed_types in assets.resources:
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.headers["content-type"].split(";", 1)[0] in allowed_types, path
            assert response.headers["x-content-type-options"] == "nosniff", path
