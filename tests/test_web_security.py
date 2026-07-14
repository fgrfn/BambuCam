"""Tests for WebUI authentication and request hardening."""

import base64

from flask import Flask, jsonify

from bambucam.web.security import configure_web_security, is_password_hash


class FakeConfig:
    def __init__(self, auth):
        self.data = {"web": {"auth": auth}}
        self.saved = 0

    def get(self, *keys, default=None):
        node = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, *keys, value):
        node = self.data
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value

    def save(self):
        self.saved += 1


def _basic(username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _app(config: FakeConfig) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return jsonify({"ok": True})

    @app.post("/write")
    def write():
        return jsonify({"ok": True})

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    configure_web_security(app, config)
    return app


def test_disabled_auth_allows_requests():
    client = _app(FakeConfig({"enabled": False})).test_client()
    assert client.get("/").status_code == 200


def test_plaintext_password_is_migrated_and_basic_auth_works():
    config = FakeConfig(
        {"enabled": True, "username": "admin", "password": "secret", "api_token": ""}
    )
    client = _app(config).test_client()

    assert config.saved == 1
    assert is_password_hash(config.data["web"]["auth"]["password"])
    assert client.get("/").status_code == 401
    assert client.get("/", headers=_basic("admin", "wrong")).status_code == 401
    response = client.get("/", headers=_basic("admin", "secret"))
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_health_endpoint_remains_public():
    config = FakeConfig(
        {"enabled": True, "username": "admin", "password": "secret", "api_token": ""}
    )
    client = _app(config).test_client()
    assert client.get("/health").status_code == 200


def test_basic_auth_write_requires_csrf_signal():
    config = FakeConfig(
        {"enabled": True, "username": "admin", "password": "secret", "api_token": ""}
    )
    client = _app(config).test_client()
    headers = _basic("admin", "secret")

    assert client.post("/write", headers=headers).status_code == 403
    headers["X-BambuCam-CSRF"] = "1"
    assert client.post("/write", headers=headers).status_code == 200


def test_bearer_token_can_write_without_browser_csrf_header():
    config = FakeConfig(
        {
            "enabled": True,
            "username": "admin",
            "password": "",
            "api_token": "integration-token",
        }
    )
    client = _app(config).test_client()
    response = client.post(
        "/write",
        headers={"Authorization": "Bearer integration-token"},
    )
    assert response.status_code == 200
