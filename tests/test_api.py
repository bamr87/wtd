"""Smoke tests for the FastAPI app.

These tests exercise the in-process ASGI surface via httpx; they do not start
a real server and do not require any LLM provider to be reachable.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from wtd import __version__
from wtd.api.app import create_app


@pytest.mark.asyncio
async def test_root_returns_metadata() -> None:
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/")

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "WTD API"
    assert data["version"] == __version__
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_health_endpoint_ok() -> None:
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/v1/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_default_app_has_no_open_cors() -> None:
    """With no allowlist configured, the API must not echo a wildcard origin."""
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # A preflight from an arbitrary origin should not be granted access.
        resp = await client.options(
            "/v1/health",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    # The middleware is not installed at all by default, so either the
    # response carries no ACAO header or it's not "*". Both are acceptable;
    # the key invariant is that we don't blanket-allow cross-origin access.
    acao = resp.headers.get("access-control-allow-origin")
    assert acao != "*"


@pytest.mark.asyncio
async def test_cors_allowlist_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """When configured, only allowlisted origins should be granted CORS access."""
    monkeypatch.setenv("WTD_API_CORS_ORIGINS", "https://allowed.example")
    from wtd.config import reset_config

    reset_config()

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        good = await client.get("/v1/health", headers={"Origin": "https://allowed.example"})
        bad = await client.get("/v1/health", headers={"Origin": "https://evil.example"})

    assert good.headers.get("access-control-allow-origin") == "https://allowed.example"
    assert bad.headers.get("access-control-allow-origin") not in (
        "*",
        "https://evil.example",
    )
