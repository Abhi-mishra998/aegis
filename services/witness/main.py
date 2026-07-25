"""ATF v3.2 §6 — Execution Witness FastAPI service entrypoint."""
from __future__ import annotations

from fastapi import FastAPI

from sdk.utils import setup_app
from services.witness.router import health_router, router

app = FastAPI(title="Aegis Execution Witness", version="3.0")
setup_app(app, "witness")
# Register the auth-free /witness/health BEFORE the auth-gated /witness/*
# router, so FastAPI matches it first (duplicate-path resolution is FIFO).
app.include_router(health_router)
app.include_router(router)
