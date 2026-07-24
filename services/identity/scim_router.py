"""ATF v3.2 §4.2 SCIM reconciliation endpoint.

`POST /scim/reconcile` — caller supplies the agent list to reconcile.
Returns per-agent action + a summary the ops dashboard renders. The
agent list is caller-supplied (not fetched from the registry inside this
handler) so a single call handles both bulk-audit ops runs and
per-agent on-demand checks without coupling identity to registry.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from sdk.common.auth import verify_internal_secret
from sdk.common.response import APIResponse
from services.identity.scim_reconciler import run_once_async, summarize
from services.policy.scim_agent import AgentRecord

router = APIRouter(
    prefix="/scim",
    tags=["scim"],
    dependencies=[Depends(verify_internal_secret)],
)


class _ReconcileRequest(BaseModel):
    agents: list[AgentRecord]


@router.post("/reconcile")
async def reconcile_endpoint(req: Annotated[_ReconcileRequest, Depends()]) -> APIResponse[dict[str, Any]]:
    """Run SCIM reconciliation for the supplied agent list.

    Response `data` shape:
        {
          "enabled":     bool,
          "totals":      {"OK": N, "QUARANTINE": N, "RESTORE": N},
          "quarantine":  [{agent_id, reason}, ...],
          "restore":     [{agent_id, reason}, ...],
          "sample":      [first 10 ReconcileResult as dicts]
        }
    """
    results = await run_once_async(req.agents)
    return APIResponse(data=summarize(results))
