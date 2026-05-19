"""Example: protect an agent function with ACP in 5 lines.

Run:
    export ACP_API_KEY=acp_...       # API key issued by POST /api-keys
    # — OR —
    export ACP_TOKEN=<jwt>           # raw JWT bearer (from POST /auth/token)
    export ACP_BASE_URL=http://localhost:8000
    python examples/agent.py

What this demonstrates:
  * @acp.protect wraps a normal Python function. Every call is routed
    through the gateway: policy check before execution, audit + signed
    receipt after.
  * If the policy denies (e.g. running ``shell.exec``), the wrapped call
    raises ``acp.DeniedError`` and the body of the function never runs.
  * After a successful call, you can fetch the signed receipt and
    verify it offline with the gateway's public key.
"""
from __future__ import annotations

import os
import sys

from sdk.acp_client import ACPError, Client, DeniedError, verify_receipt


def main() -> int:
    try:
        # Reads ACP_API_KEY / ACP_TOKEN / ACP_BASE_URL from env (or .env).
        acp = Client()
    except ACPError as exc:
        print(f"setup error: {exc}", file=sys.stderr)
        print(
            "hint: export ACP_API_KEY (or ACP_TOKEN) — JWT bearers are accepted",
            file=sys.stderr,
        )
        return 1

    @acp.protect(agent_id="agent_42", tool="db.query")
    def query(sql: str) -> list[dict[str, str]]:
        # Pretend this is a real database call. ACP authorises before we
        # run it; the gateway logged + signed the request already.
        return [{"row": "1", "value": sql}]

    @acp.protect(agent_id="agent_42", tool="shell.exec")
    def run_shell(cmd: str) -> dict[str, str]:
        return {"output": cmd}

    # 1. Allowed call — policy lets SELECT through.
    rows = query("SELECT * FROM customers LIMIT 1")
    print("allow ->", rows)

    # 2. Denied call — shell.exec is not in the agent's allow-list.
    try:
        run_shell("rm -rf /")
    except DeniedError as exc:
        print("deny  ->", exc)

    # 3. Verify the last receipt offline. The execution_id is returned by
    #    the gateway in the X-Request-ID header of the /execute response;
    #    in real code you'd capture it from there. We fetch the most recent
    #    receipt for this agent to demonstrate the verifier.
    pk_info = acp.public_key()
    public_key = pk_info.get("public_key_pem")
    exec_id = os.environ.get("ACP_LAST_EXECUTION_ID")
    if not exec_id or not public_key:
        print("receipt verifies -> skipped (set ACP_LAST_EXECUTION_ID to a real id)")
        return 0
    try:
        receipt = acp.get_receipt(exec_id)
    except ACPError as exc:
        print(f"receipt fetch failed: {exc}")
        return 0
    if receipt:
        ok = verify_receipt(receipt, public_key)
        print("receipt verifies ->", ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
