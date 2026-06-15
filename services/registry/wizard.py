"""
Sprint 2 — Agent Onboarding Wizard.

Composes the 3 calls a customer would otherwise have to make by hand
(create agent + whitelist default tools + mint Aegis API key) into a
single POST /agents/wizard so the OnboardingWizard.jsx can finish in
one round trip.

PRODUCT_PLAN.md §1.3 is non-negotiable: the customer's Anthropic /
OpenAI / Bedrock / etc. API key NEVER touches Aegis. The wizard
issues only the Aegis-side key (`acp_…`); the SDK snippets contain
explicit `# stays on YOUR machine` comments on the LLM env-var lines.

Authentication: the gateway already validates either an Aegis JWT
(legacy HS256) or a Clerk JWT (RS256) before forwarding, then injects
internal_secret for the registry-side dep. So the registry's wizard
endpoint runs behind the existing `verify_internal_secret` chain.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Annotated, Literal

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.audit_stream import push_audit_event
from sdk.common.config import settings
from sdk.common.db import get_db, get_tenant_id
from sdk.common.deadline import check_deadline
from sdk.common.enums import PermissionAction
from sdk.common.redis import get_redis_client
from sdk.common.response import APIResponse
from services.registry.repository import AgentRepository, PermissionRepository
from services.registry.schemas import (
    AgentCreate,
    AgentResponse,
    PermissionCreate,
)
from services.registry.service import AgentService

logger = structlog.get_logger(__name__)

# Wizard mounts its routes under /agents but exposes its own router so the
# composer can sit alongside the existing /agents CRUD without modifying it.
router = APIRouter(prefix="/agents", tags=["agents", "wizard"])


# ────────────────────────────────────────────────────────────────────────
# Provider catalog (8 cards in the UI)
# ────────────────────────────────────────────────────────────────────────

Provider = Literal[
    "anthropic",
    "openai",
    "bedrock",
    "langchain",
    "cursor",
    "claude-code",
    "openhands",
    "custom",
]

# The 8 default tools we whitelist when a wizard agent is created. Every
# customer can refine later via /agents/{id}/permissions. These are the
# common agent capabilities; the policy engine still enforces per-call
# rules (transfer_money, drop_table, etc. trigger the existing detectors
# regardless of whether the tool itself is in this whitelist).
_DEFAULT_TOOL_WHITELIST: tuple[str, ...] = (
    "read_file",
    "write_file",
    "web_search",
    "send_email",
    "query_database",
    "post_message",
    "http_request",
    "file_search",
)


# ────────────────────────────────────────────────────────────────────────
# Wizard request / response schemas
# ────────────────────────────────────────────────────────────────────────


class WizardCreateRequest(BaseModel):
    """Step 2 of the wizard collapses into this payload."""

    name: str = Field(..., min_length=3, max_length=100)
    provider: Provider
    risk_level: Literal["low", "medium", "high"] = "medium"
    description: str | None = Field(
        default=None, max_length=500,
        description="Optional human description. Defaults to provider+name template.",
    )
    owner_id: str = Field(
        default="self-serve",
        max_length=100,
        description="Identifier for the human creating the agent. Defaults to 'self-serve'.",
    )

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: str) -> str:
        # Reuse the canonical AgentCreate validator's contract — agents are
        # uniquely keyed on (tenant_id, name) so a typo here surfaces as a
        # 409 from the create_agent call.
        return v.strip().lower().replace(" ", "-")


class WizardCreatedResponse(BaseModel):
    """Returned to the OnboardingWizard.jsx — every value here is needed
    for the Step-3 install snippet."""

    agent_id: uuid.UUID
    tenant_id: uuid.UUID
    aegis_api_key: str = Field(
        ...,
        description="Raw `acp_...` key. Returned ONCE; the wizard tells the customer to copy it now.",
    )
    install_snippet_url: str
    provider: Provider
    name: str
    risk_level: str
    shadow_mode_until_hint: str | None = Field(
        default=None,
        description=(
            "Best-effort human reminder that the workspace is in shadow mode. "
            "Authoritative value lives on the tenant row (read via /workspace/me)."
        ),
    )


# ────────────────────────────────────────────────────────────────────────
# Internal helpers
# ────────────────────────────────────────────────────────────────────────


async def _mint_api_key(tenant_id: uuid.UUID, agent_name: str) -> str:
    """
    Call the API service to mint a fresh `acp_...` key bound to this tenant.

    Uses INTERNAL_SECRET for the service-mesh hop. The key returned is
    surfaced to the customer ONCE in the wizard response; we never log
    it.
    """
    url = f"{settings.API_SERVICE_URL.rstrip('/')}/api-keys"
    payload = {"name": f"wizard-{agent_name}"[:64]}
    headers = {
        "X-Internal-Secret": settings.INTERNAL_SECRET,
        "X-Tenant-ID": str(tenant_id),
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=6.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code >= 400:
        # Defensive — log just the status code, NEVER the body (would leak
        # any partial key fragments).
        logger.error(
            "wizard_api_key_mint_failed",
            tenant_id=str(tenant_id),
            status=resp.status_code,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="API key minting service is unavailable",
        )
    try:
        body = resp.json()
        raw_key = body.get("data", {}).get("api_key")
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="API key minting service returned non-JSON",
        ) from exc
    if not raw_key:
        raise HTTPException(
            status_code=502,
            detail="API key minting service returned no key",
        )
    return raw_key


async def _whitelist_default_tools(
    perm_repo: PermissionRepository,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    owner_id: str,
) -> int:
    """Grant the 8 default tool permissions. Returns the count actually
    added. Idempotent — IntegrityError on the (agent_id, tool_name) unique
    constraint is treated as already-present and skipped."""
    added = 0
    for tool_name in _DEFAULT_TOOL_WHITELIST:
        payload = PermissionCreate(
            tool_name=tool_name,
            action=PermissionAction.ALLOW,
            granted_by=owner_id or "self-serve",
        )
        try:
            await perm_repo.create(tenant_id, agent_id, payload)
            added += 1
        except Exception as exc:
            # Either an IntegrityError (perm already exists — fine) or a
            # transient DB error (caller will see the partial whitelist).
            # We don't fail the whole wizard for a single perm row.
            logger.warning(
                "wizard_perm_insert_skipped",
                agent_id=str(agent_id),
                tool=tool_name,
                error=str(exc),
            )
    return added


# ────────────────────────────────────────────────────────────────────────
# POST /agents/wizard — the composer
# ────────────────────────────────────────────────────────────────────────


@router.post(
    "/wizard",
    response_model=APIResponse[WizardCreatedResponse],
    status_code=status.HTTP_201_CREATED,
    summary="One-shot agent provisioning for the OnboardingWizard UI",
)
async def wizard_create_agent(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    payload: WizardCreateRequest,
    _: Annotated[bool, Depends(check_deadline)] = True,
) -> APIResponse[WizardCreatedResponse]:
    """
    Composes 3 calls into 1:
        1. POST /agents              (create row, tag provider in metadata)
        2. POST /agents/{id}/permissions  ×8  (default tool whitelist)
        3. POST /api-keys             (mint a fresh acp_... key)

    Failure semantics:
      - Step 1 hard-fails: wizard returns the upstream 4xx (409 on
        duplicate name).
      - Step 2 partial-fails: logged, wizard still returns 201 — the
        customer can re-grant from the Permissions UI.
      - Step 3 hard-fails: agent row + perms are kept (idempotent on
        retry) but the wizard returns 502; the customer must retry from
        the same step.
    """
    request_id = getattr(request.state, "request_id", "unknown")

    # ── Step 1: create the agent row ───────────────────────────────────
    description = (
        payload.description
        or f"{payload.provider.title()} agent created via onboarding wizard."
    )
    agent_payload = AgentCreate(
        name=payload.name,
        description=description,
        owner_id=payload.owner_id,
        risk_level=payload.risk_level,
    )
    repo = AgentRepository(db)
    perm_repo = PermissionRepository(db)
    service = AgentService(repo, perm_repo)

    agent_response: AgentResponse = await service.create_agent(tenant_id, agent_payload)

    # Tag the provider in agents.metadata so the Dashboard inventory can
    # group by provider without a schema change. Stored as a small dict so
    # we can add additional onboarding-only fields later (timezone,
    # contact email, etc.) without another migration.
    agent_row = await repo.get_by_id(tenant_id, agent_response.id)
    if agent_row is not None:
        meta = dict(agent_row.metadata_data or {})
        meta["provider"] = payload.provider
        meta["wizard"] = True
        agent_row.metadata_data = meta
        await db.commit()
        await db.refresh(agent_row)

    # ── Step 2: whitelist default tools (best-effort) ──────────────────
    added = await _whitelist_default_tools(
        perm_repo,
        tenant_id=tenant_id,
        agent_id=agent_response.id,
        owner_id=payload.owner_id,
    )
    logger.info(
        "wizard_tools_whitelisted",
        agent_id=str(agent_response.id),
        added=added,
        target=len(_DEFAULT_TOOL_WHITELIST),
    )

    # ── Step 3: mint the Aegis-side API key ────────────────────────────
    aegis_api_key = await _mint_api_key(tenant_id, payload.name)

    # ── Audit trail (no key value logged) ──────────────────────────────
    _redis = get_redis_client(settings.REDIS_URL)
    try:
        with contextlib.suppress(Exception):
            await push_audit_event(
                redis=_redis,
                tenant_id=tenant_id,
                agent_id=agent_response.id,
                action="agent_wizard_provision",
                request_id=request_id,
                metadata={
                    "name": payload.name,
                    "provider": payload.provider,
                    "risk_level": payload.risk_level,
                    "tools_whitelisted": added,
                },
            )
    finally:
        await _redis.aclose()

    return APIResponse(
        data=WizardCreatedResponse(
            agent_id=agent_response.id,
            tenant_id=tenant_id,
            aegis_api_key=aegis_api_key,
            install_snippet_url=(
                f"/agents/wizard/install-snippet/{agent_response.id}/{payload.provider}"
            ),
            provider=payload.provider,
            name=payload.name,
            risk_level=payload.risk_level,
            shadow_mode_until_hint=(
                "Your workspace is in 14-day shadow mode by default — every "
                "decision is logged but no real block fires until you exit "
                "shadow mode."
            ),
        ),
    )


# ────────────────────────────────────────────────────────────────────────
# GET /agents/wizard/install-snippet/{agent_id}/{provider}
# ────────────────────────────────────────────────────────────────────────


class InstallSnippetResponse(BaseModel):
    provider: Provider
    language: Literal["python", "javascript", "shell", "markdown"]
    install_command: str
    env_vars: list[str]
    snippet: str
    notes: list[str]


# Hosted Aegis endpoint baked into every snippet. SDK lets the user
# override but we ship a sensible default so the wizard's first paste
# works without any extra config.
_DEFAULT_AEGIS_ENDPOINT = "https://ha.aegisagent.in"


def _make_env_block(
    *, tenant_id: uuid.UUID, agent_id: uuid.UUID, api_key: str, llm_env_name: str | None,
) -> tuple[list[str], list[str]]:
    """Build the export-style env block + the trimmed list of just the vars.

    The LLM-key env var is included as a placeholder comment ONLY — the
    snippet must never carry the customer's actual LLM key. Returns
    (raw_lines, var_names).
    """
    raw_lines = [
        f"export AEGIS_API_KEY={api_key}",
        f"export AEGIS_TENANT_ID={tenant_id}",
        f"export AEGIS_AGENT_ID={agent_id}",
        f"export AEGIS_ENDPOINT={_DEFAULT_AEGIS_ENDPOINT}",
    ]
    var_names = ["AEGIS_API_KEY", "AEGIS_TENANT_ID", "AEGIS_AGENT_ID", "AEGIS_ENDPOINT"]
    if llm_env_name:
        raw_lines.append(
            f"export {llm_env_name}=...   # stays on YOUR machine — Aegis never sees this",
        )
        var_names.append(llm_env_name)
    return raw_lines, var_names


def _build_snippet(provider: Provider, *, tenant_id: uuid.UUID, agent_id: uuid.UUID, api_key: str) -> InstallSnippetResponse:
    """Builder for the per-provider snippet."""
    if provider == "anthropic":
        env_lines, var_names = _make_env_block(
            tenant_id=tenant_id, agent_id=agent_id, api_key=api_key,
            llm_env_name="ANTHROPIC_API_KEY",
        )
        snippet = "\n".join(
            env_lines
            + [
                "",
                "# Python code (replaces `from anthropic import Anthropic`)",
                "from aegis_anthropic import AegisAnthropic",
                "",
                "client = AegisAnthropic()",
                "resp = client.messages.create(",
                "    model='claude-opus-4-7',",
                "    max_tokens=1024,",
                "    messages=[{'role':'user','content':'Hello'}],",
                ")",
                "print(resp.content[0].text)",
            ],
        )
        return InstallSnippetResponse(
            provider=provider,
            language="python",
            install_command="pip install aegis-anthropic",
            env_vars=var_names,
            snippet=snippet,
            notes=[
                "Anthropic key never leaves your machine.",
                "Aegis only sees the tool-call intent — the LLM call itself is local.",
            ],
        )

    if provider == "openai":
        env_lines, var_names = _make_env_block(
            tenant_id=tenant_id, agent_id=agent_id, api_key=api_key,
            llm_env_name="OPENAI_API_KEY",
        )
        snippet = "\n".join(
            env_lines
            + [
                "",
                "# Python (replaces `from openai import OpenAI`)",
                "from aegis_openai import AegisOpenAI",
                "",
                "client = AegisOpenAI()",
                "resp = client.chat.completions.create(",
                "    model='gpt-4o-mini',",
                "    messages=[{'role':'user','content':'Hello'}],",
                ")",
                "print(resp.choices[0].message.content)",
            ],
        )
        return InstallSnippetResponse(
            provider=provider,
            language="python",
            install_command="pip install aegis-openai",
            env_vars=var_names,
            snippet=snippet,
            notes=[
                "OpenAI key never leaves your machine.",
                "Drop-in replacement: import + constructor; rest of code unchanged.",
            ],
        )

    if provider == "bedrock":
        env_lines, var_names = _make_env_block(
            tenant_id=tenant_id, agent_id=agent_id, api_key=api_key,
            llm_env_name="AWS_PROFILE",
        )
        snippet = "\n".join(
            env_lines
            + [
                "",
                "# Python (replaces boto3 bedrock-agent-runtime)",
                "from aegis_bedrock import AegisBedrock",
                "",
                "client = AegisBedrock(region_name='us-east-1')",
                "resp = client.invoke_agent(",
                "    agentId='<your-bedrock-agent-id>',",
                "    sessionId='session-1',",
                "    inputText='Hello',",
                ")",
            ],
        )
        return InstallSnippetResponse(
            provider=provider,
            language="python",
            install_command="pip install aegis-bedrock",
            env_vars=var_names,
            snippet=snippet,
            notes=[
                "AWS creds (profile / env / IRSA) stay on your machine.",
                "Aegis only sees the invoke_agent calls + tool actions.",
            ],
        )

    if provider == "langchain":
        env_lines, var_names = _make_env_block(
            tenant_id=tenant_id, agent_id=agent_id, api_key=api_key,
            llm_env_name="OPENAI_API_KEY",
        )
        snippet = "\n".join(
            env_lines
            + [
                "",
                "# Python (LangChain + Aegis-instrumented tool calls)",
                "from aegis_langchain import AegisToolWrapper",
                "from langchain.agents import initialize_agent, Tool",
                "from langchain_openai import ChatOpenAI",
                "",
                "tools = [AegisToolWrapper(Tool(name='search', func=..., description='...'))]",
                "agent = initialize_agent(tools, ChatOpenAI(model='gpt-4o-mini'))",
                "agent.run('Find the latest pricing for SKU 12345')",
            ],
        )
        return InstallSnippetResponse(
            provider=provider,
            language="python",
            install_command="pip install aegis-langchain",
            env_vars=var_names,
            snippet=snippet,
            notes=[
                "Wrap each LangChain Tool with AegisToolWrapper — Aegis sees every action.",
                "LLM provider keys stay on your machine.",
            ],
        )

    if provider == "cursor":
        env_lines, var_names = _make_env_block(
            tenant_id=tenant_id, agent_id=agent_id, api_key=api_key,
            llm_env_name=None,
        )
        snippet = "\n".join(
            [
                "# Cursor + Aegis MCP server (settings.json → cursor.mcpServers)",
                "{",
                '  "aegis": {',
                f'    "command": "npx",',
                f'    "args": ["-y", "@aegis/mcp-server"],',
                '    "env": {',
                f'      "AEGIS_API_KEY": "{api_key}",',
                f'      "AEGIS_TENANT_ID": "{tenant_id}",',
                f'      "AEGIS_AGENT_ID": "{agent_id}",',
                f'      "AEGIS_ENDPOINT": "{_DEFAULT_AEGIS_ENDPOINT}"',
                '    }',
                '  }',
                "}",
            ],
        )
        return InstallSnippetResponse(
            provider=provider,
            language="javascript",
            install_command="(no install — npx fetches the MCP server)",
            env_vars=var_names,
            snippet=snippet,
            notes=[
                "Paste into Cursor → Settings → MCP Servers.",
                "Cursor's LLM provider auth stays on your machine — Aegis only sees the tool calls.",
            ],
        )

    if provider == "claude-code":
        env_lines, var_names = _make_env_block(
            tenant_id=tenant_id, agent_id=agent_id, api_key=api_key,
            llm_env_name=None,
        )
        snippet = "\n".join(
            [
                "# Claude Code → ~/.claude/mcp-servers.json (or via CLI: `claude mcp add aegis`)",
                "{",
                '  "aegis": {',
                f'    "command": "npx",',
                f'    "args": ["-y", "@aegis/mcp-server"],',
                '    "env": {',
                f'      "AEGIS_API_KEY": "{api_key}",',
                f'      "AEGIS_TENANT_ID": "{tenant_id}",',
                f'      "AEGIS_AGENT_ID": "{agent_id}",',
                f'      "AEGIS_ENDPOINT": "{_DEFAULT_AEGIS_ENDPOINT}"',
                '    }',
                '  }',
                "}",
            ],
        )
        return InstallSnippetResponse(
            provider=provider,
            language="javascript",
            install_command="claude mcp add aegis npx -- -y @aegis/mcp-server",
            env_vars=var_names,
            snippet=snippet,
            notes=[
                "Claude Code's Anthropic key stays on your machine via `claude auth`.",
                "Aegis only mediates the tool calls Claude Code makes.",
            ],
        )

    if provider == "openhands":
        env_lines, var_names = _make_env_block(
            tenant_id=tenant_id, agent_id=agent_id, api_key=api_key,
            llm_env_name="LLM_API_KEY",
        )
        snippet = "\n".join(
            env_lines
            + [
                "",
                "# OpenHands config.toml",
                "[aegis]",
                f"endpoint = \"{_DEFAULT_AEGIS_ENDPOINT}\"",
                f"api_key  = \"{api_key}\"",
                f"agent_id = \"{agent_id}\"",
                "wrap_tools = true   # route every tool call through Aegis /execute",
            ],
        )
        return InstallSnippetResponse(
            provider=provider,
            language="shell",
            install_command="pip install aegis-openhands",
            env_vars=var_names,
            snippet=snippet,
            notes=[
                "OpenHands' LLM provider key stays in OpenHands' own config.",
            ],
        )

    # custom — raw HTTP POST template
    env_lines, var_names = _make_env_block(
        tenant_id=tenant_id, agent_id=agent_id, api_key=api_key, llm_env_name=None,
    )
    snippet = "\n".join(
        env_lines
        + [
            "",
            "# Raw HTTP POST template — works from any language",
            "curl -X POST \"$AEGIS_ENDPOINT/execute\" \\",
            "  -H \"Authorization: Bearer $AEGIS_API_KEY\" \\",
            "  -H \"X-Tenant-ID: $AEGIS_TENANT_ID\" \\",
            "  -H \"X-Agent-ID: $AEGIS_AGENT_ID\" \\",
            "  -H \"Content-Type: application/json\" \\",
            "  -d '{",
            "    \"tool_name\": \"send_email\",",
            "    \"arguments\": {\"to\": \"a@b.com\", \"subject\": \"hi\"}",
            "  }'",
        ],
    )
    return InstallSnippetResponse(
        provider="custom",
        language="shell",
        install_command="(none — direct HTTP)",
        env_vars=var_names,
        snippet=snippet,
        notes=[
            "Use this template if you wrote your own agent harness.",
            "Aegis returns {decision: allow|deny|escalate|monitor|quarantine}.",
            "Your LLM provider key stays on your machine — Aegis only sees tool-call intents.",
        ],
    )


@router.get(
    "/wizard/install-snippet/{agent_id}/{provider}",
    response_model=APIResponse[InstallSnippetResponse],
    summary="SDK-specific copy-paste install block (no LLM-provider key inside)",
)
async def wizard_install_snippet(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID,
    provider: Provider,
    aegis_api_key: str = "<paste-your-aegis-key>",
) -> APIResponse[InstallSnippetResponse]:
    """
    Returns the per-provider install snippet for a given agent. The
    `aegis_api_key` query param is OPTIONAL — the wizard pre-fills it
    from the POST /agents/wizard response so the customer never sees a
    placeholder; if the snippet is requested out-of-band, we leave the
    placeholder string so the snippet doesn't fall over on a copy/paste
    while also not leaking a key that we'd otherwise have to look up.
    """
    repo = AgentRepository(db)
    agent = await repo.get_by_id(tenant_id, agent_id)
    if agent is None or agent.deleted_at is not None:
        raise HTTPException(
            status_code=404, detail="Agent not found in this workspace",
        )
    snippet = _build_snippet(
        provider, tenant_id=tenant_id, agent_id=agent_id, api_key=aegis_api_key,
    )
    return APIResponse(data=snippet)
