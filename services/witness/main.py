"""ATF v3.2 §6 — Execution Witness FastAPI service entrypoint."""
from __future__ import annotations

from fastapi import FastAPI

from sdk.utils import setup_app
from services.witness.router import router

app = FastAPI(title="Aegis Execution Witness", version="3.0")
setup_app(app, "witness")
app.include_router(router)
