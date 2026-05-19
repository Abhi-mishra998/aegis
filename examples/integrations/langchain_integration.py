"""
ACP + LangChain Integration
============================
Wrap any LangChain tool with ACP governance.
Every tool call the LLM makes is checked by ACP before it runs.

Install:
    pip install langchain langchain-openai langchain-ollama requests
"""

from __future__ import annotations

import os
import requests
from typing import Any, Type
from langchain.tools import BaseTool
from langchain_core.tools import tool
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# ACP CLIENT — thin wrapper, no SDK needed
# ─────────────────────────────────────────────────────────────────────────────

class ACPClient:
    def __init__(self, base_url: str, token: str, tenant_id: str, agent_id: str):
        self.base_url  = base_url.rstrip("/")
        self.tenant_id = tenant_id
        self.agent_id  = agent_id
        self.headers   = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID":   tenant_id,
            "X-Agent-ID":    agent_id,
        }

    def check(self, tool_name: str, parameters: dict, tokens: int = 100) -> dict:
        """Ask ACP if this tool call is allowed. Raises PermissionError on deny."""
        resp = requests.post(
            f"{self.base_url}/execute/{tool_name}",
            headers=self.headers,
            json={"parameters": parameters, "metadata": {"tokens": tokens}},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 403:
            raise PermissionError(resp.json().get("error", "denied"))
        if resp.status_code == 429:
            raise RuntimeError(f"rate_limited: retry after {resp.headers.get('Retry-After')}s")
        resp.raise_for_status()
        return resp.json()


# ─────────────────────────────────────────────────────────────────────────────
# ACP-WRAPPED LANGCHAIN TOOL BASE CLASS
# Subclass this instead of BaseTool. Automatically calls ACP before _run.
# ─────────────────────────────────────────────────────────────────────────────

class ACPTool(BaseTool):
    """
    BaseTool subclass that calls ACP before running.
    Override: name, description, args_schema, _run_impl().
    Do NOT override _run() — it's the ACP gatekeeper.
    """
    acp: ACPClient = Field(exclude=True)

    class Config:
        arbitrary_types_allowed = True

    def _run(self, **kwargs: Any) -> str:
        # 1. Ask ACP
        try:
            decision = self.acp.check(self.name, kwargs)
            risk = decision.get("risk", 0.0)
        except PermissionError as e:
            return f"[BLOCKED by ACP policy: {e}]"
        except RuntimeError as e:
            return f"[ACP rate limited: {e}]"

        # 2. ACP said ALLOW — run the real implementation
        return self._run_impl(**kwargs)

    def _run_impl(self, **kwargs: Any) -> str:
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# YOUR ACTUAL TOOLS — real implementations go in _run_impl
# ─────────────────────────────────────────────────────────────────────────────

class ReadFileInput(BaseModel):
    path: str = Field(description="File path to read")

class ReadFileTool(ACPTool):
    name: str = "read_file"
    description: str = "Read the contents of a file"
    args_schema: Type[BaseModel] = ReadFileInput

    def _run_impl(self, path: str, **_) -> str:
        # REAL implementation — only runs if ACP allows
        try:
            with open(path) as f:
                return f.read()
        except FileNotFoundError:
            return f"File not found: {path}"


class QueryDBInput(BaseModel):
    sql: str = Field(description="SQL query to execute")

class QueryDBTool(ACPTool):
    name: str = "db_query"
    description: str = "Run a SQL query against the database"
    args_schema: Type[BaseModel] = QueryDBInput

    def _run_impl(self, sql: str, **_) -> str:
        # REAL implementation — only runs if ACP allows
        # Replace with: return str(db.session.execute(sql).fetchall())
        return f"[query result for: {sql}]"


class WebSearchInput(BaseModel):
    query: str = Field(description="Search query")

class WebSearchTool(ACPTool):
    name: str = "web_search"
    description: str = "Search the web for information"
    args_schema: Type[BaseModel] = WebSearchInput

    def _run_impl(self, query: str, **_) -> str:
        # REAL implementation — only runs if ACP allows
        # Replace with: return search_api.search(query)
        return f"[search results for: {query}]"


# ─────────────────────────────────────────────────────────────────────────────
# WIRE IT ALL TOGETHER
# ─────────────────────────────────────────────────────────────────────────────

def build_acp_agent(llm, acp: ACPClient) -> AgentExecutor:
    """
    Build a LangChain agent where every tool call goes through ACP.
    llm: any LangChain chat model (ChatOpenAI, ChatOllama, ChatAnthropic, etc.)
    """
    tools = [
        ReadFileTool(acp=acp),
        QueryDBTool(acp=acp),
        WebSearchTool(acp=acp),
    ]

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a helpful assistant. Use tools to answer user questions."),
        ("human",  "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True)


# ─────────────────────────────────────────────────────────────────────────────
# EXAMPLE — run with OpenAI or Ollama
# ─────────────────────────────────────────────────────────────────────────────

def run_with_openai(acp: ACPClient) -> None:
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model="gpt-4o-mini", api_key=os.environ["OPENAI_API_KEY"])
    agent = build_acp_agent(llm, acp)
    result = agent.invoke({"input": "Read the file /data/report.csv and summarize it"})
    print(result["output"])


def run_with_ollama(acp: ACPClient) -> None:
    from langchain_ollama import ChatOllama
    llm = ChatOllama(model="llama3.2", base_url="http://localhost:11434")
    agent = build_acp_agent(llm, acp)
    result = agent.invoke({"input": "Read the file /data/report.csv and summarize it"})
    print(result["output"])


if __name__ == "__main__":
    # Setup ACP connection
    TOKEN = os.environ.get("ACP_TOKEN", "your-jwt-token-here")
    acp = ACPClient(
        base_url  = os.environ.get("ACP_BASE_URL",   "http://localhost:8000"),
        token     = TOKEN,
        tenant_id = os.environ.get("ACP_TENANT_ID",  "00000000-0000-0000-0000-000000000001"),
        agent_id  = os.environ.get("ACP_AGENT_ID",   "your-agent-uuid-here"),
    )

    # Pick your LLM
    if os.environ.get("OPENAI_API_KEY"):
        run_with_openai(acp)
    else:
        run_with_ollama(acp)
