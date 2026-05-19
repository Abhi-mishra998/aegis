import sys
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from sdk.utils import setup_app
from services.policy.opa_client import opa_client
from services.policy.router import init_policy_clients, router

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ready = await opa_client.wait_for_ready()
    if not ready:
        logger.critical("opa_not_ready", detail="OPA policy engine failed health check; shutting down")
        sys.exit(1)
    
    # Initialize Registry & Audit clients
    init_policy_clients()
    yield
    # Shutdown: Clean up client
    await opa_client.close()

app = FastAPI(
    title="ACP Policy Service",
    description="OPA-backed authorization engine for agent tool execution",
    version="1.0.0",
    lifespan=lifespan,
)

# Consolidated SDK Setup
setup_app(app, "policy")

app.include_router(router)
