"""
Sprint 10 — SecurityHeadersMiddleware unit tests.

Runs the middleware against a minimal Starlette ASGI app so we can
assert the response headers without bringing up the full gateway.
"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from services.gateway.middleware import SecurityHeadersMiddleware


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/echo")
    def echo() -> dict:
        return {"ok": True}

    app.add_middleware(SecurityHeadersMiddleware)
    return app


def _fetch() -> dict[str, str]:
    app = _make_app()
    with TestClient(app) as client:
        resp = client.get("/echo")
    return dict(resp.headers)


# ───────────────────────────────────────────────────────────────────────


def test_strict_transport_security_is_one_year_includes_subdomains():
    headers = _fetch()
    assert headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


def test_x_content_type_options_nosniff():
    headers = _fetch()
    assert headers["x-content-type-options"] == "nosniff"


def test_referrer_policy_strict_origin_when_cross_origin():
    headers = _fetch()
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_permissions_policy_blocks_camera_microphone_geolocation():
    headers = _fetch()
    pp = headers["permissions-policy"]
    assert "camera=()" in pp
    assert "microphone=()" in pp
    assert "geolocation=()" in pp
    assert "payment=(self)" in pp
    assert "usb=()" in pp


def test_csp_present_with_frame_ancestors_none():
    headers = _fetch()
    csp = headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'self'" in csp


def test_handler_set_header_is_preserved():
    """Middleware uses .setdefault — if the handler set a stricter value,
    don't overwrite it."""
    app = FastAPI()

    @app.get("/strict")
    def strict() -> JSONResponse:
        return JSONResponse(
            {"ok": True},
            headers={"Strict-Transport-Security": "max-age=63072000; preload"},
        )

    app.add_middleware(SecurityHeadersMiddleware)
    with TestClient(app) as client:
        resp = client.get("/strict")
    assert resp.headers["strict-transport-security"] == "max-age=63072000; preload"
