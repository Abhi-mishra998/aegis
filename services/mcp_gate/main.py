"""ATF §5.1 MCP proxy — FastAPI service entrypoint."""
from __future__ import annotations

from fastapi import FastAPI

from sdk.utils import setup_app
from services.mcp_gate.router import router

app = FastAPI(title="Aegis MCP Gate", version="3.0")
setup_app(app, "mcp_gate")
app.include_router(router)
