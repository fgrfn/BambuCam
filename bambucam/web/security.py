"""Web authentication, password migration, CSRF checks, and response hardening."""

import logging
import secrets

from flask import Flask, Response, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

log = logging.getLogger(__name__)
_HASH_PREFIXES = ("scrypt:", "pbkdf2:")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_PUBLIC_PATHS = {"/health"}


def is_password_hash(value: str) -> bool:
    return bool(value) and value.startswith(_HASH_PREFIXES)


def hash_password(value: str) -> str:
    if not value:
        return ""
    return value if is_password_hash(value) else generate_password_hash(value)


def configure_web_security(app: Flask, config) -> None:
    """Attach authentication and security hooks to a Flask app."""
    auth = config.get("web", "auth", default={}) or {}
    if not isinstance(auth, dict):
        raise ValueError("web.auth must be a mapping")

    enabled = bool(auth.get("enabled", False))
    username = str(auth.get("username", "admin"))
    password = str(auth.get("password", ""))
    api_token = str(auth.get("api_token", ""))

    if enabled and not password and not api_token:
        raise ValueError(
            "Web authentication is enabled but neither a password nor API token is configured"
        )

    if password and not is_password_hash(password):
        password = hash_password(password)
        config.set("web", "auth", "password", value=password)
        config.save()
        log.info("Migrated WebUI password to a secure hash")

    def _authorized() -> tuple[bool, str]:
        authorization = request.authorization
        if authorization and authorization.type.lower() == "basic" and password:
            valid_user = secrets.compare_digest(authorization.username or "", username)
            valid_password = check_password_hash(password, authorization.password or "")
            if valid_user and valid_password:
                return True, "basic"

        bearer = request.headers.get("Authorization", "")
        if bearer.lower().startswith("bearer ") and api_token:
            candidate = bearer[7:].strip()
            if secrets.compare_digest(candidate, api_token):
                return True, "bearer"

        return False, ""

    def _same_origin_or_explicit() -> bool:
        expected = request.host_url.rstrip("/")
        origin = request.headers.get("Origin")
        if origin:
            return secrets.compare_digest(origin.rstrip("/"), expected)

        referer = request.headers.get("Referer")
        if referer:
            return referer.startswith(f"{expected}/")

        return request.headers.get("X-BambuCam-CSRF") == "1"

    @app.before_request
    def _require_authentication():
        if not enabled or request.path in _PUBLIC_PATHS:
            return None

        authorized, auth_type = _authorized()
        if not authorized:
            return Response(
                "Authentication required",
                status=401,
                headers={"WWW-Authenticate": 'Basic realm="BambuCam", charset="UTF-8"'},
                mimetype="text/plain",
            )

        if request.method not in _SAFE_METHODS and auth_type == "basic":
            if not _same_origin_or_explicit():
                return jsonify({"error": "CSRF validation failed"}), 403
        return None

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'",
        )
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
