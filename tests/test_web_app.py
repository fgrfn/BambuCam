"""Regression tests for WebUI application startup."""

from bambucam.web.app import _resolve_session_secret


class FakeConfig:
    def __init__(self, secret="", save_error=None):
        self.data = {"web": {"secret_key": secret}}
        self.save_error = save_error
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
        if self.save_error is not None:
            raise self.save_error


def test_existing_session_secret_is_reused():
    config = FakeConfig(secret="existing-secret")

    assert _resolve_session_secret(config) == "existing-secret"
    assert config.saved == 0


def test_generated_session_secret_is_persisted(monkeypatch):
    config = FakeConfig()
    monkeypatch.setattr("bambucam.web.app.secrets.token_hex", lambda _length: "generated-secret")

    assert _resolve_session_secret(config) == "generated-secret"
    assert config.data["web"]["secret_key"] == "generated-secret"
    assert config.saved == 1


def test_read_only_config_does_not_prevent_webui_startup(monkeypatch, caplog):
    config = FakeConfig(save_error=PermissionError("permission denied"))
    monkeypatch.setattr("bambucam.web.app.secrets.token_hex", lambda _length: "memory-secret")

    assert _resolve_session_secret(config) == "memory-secret"
    assert config.data["web"]["secret_key"] == "memory-secret"
    assert config.saved == 1
    assert "continuing with an in-memory secret" in caplog.text
