# acp — Python SDK

**Tamper-evident replay + runtime deny for AI agents.**

## Install

```bash
pip install acp
```

## Five-line integration

```python
import acp

client = acp.Client(api_key="acp_...", base_url="https://acp.example.com")

@client.protect(agent_id="agent_42")
def query(sql: str) -> list[dict]:
    return db.execute(sql)
```

Every call to `query(...)` now:
1. Hits ACP's policy engine before execution. If denied → `DeniedError`, function never runs.
2. Lands in the audit chain with a signed receipt (model version, prompt hash, tool, outcome).
3. Is replayable from the Flight Recorder for the next 90 days.

## Catching denials

```python
try:
    query("DROP TABLE users")
except acp.DeniedError as e:
    log.warning("blocked by ACP", reason=e.reason, decision=e.decision_id)
```

## Policy as code

Drop a file at `.acp/policy.yaml` in your repo:

```yaml
version: 1
agent: agent_42
allow:
  - tool: query
    when:
      payload.args.0: "^SELECT"
deny:
  - tool: query
    when:
      payload.args.0: "DROP|TRUNCATE|DELETE"
autonomy:
  max_actions_per_minute: 60
  require_approval_for: [send_email, transfer_funds]
```

Validate locally before deploy:

```bash
acp validate .acp/policy.yaml
```

The CLI exits 1 on any schema, regex, or version issue.

## Replay and verify

```python
timeline = client.replay(execution_id="exec_abc123")
chain    = client.verify_audit()                   # cryptographic integrity check

# Offline verification with the public key
receipt  = client.get_receipt("exec_abc123")
pub      = client.public_key()["public_key_pem"]
assert acp.verify_receipt(receipt, pub)            # signature roundtrip

# Daily Merkle inclusion proof
proof    = client.get_inclusion_proof("exec_abc123")
leaf     = acp.leaf_hash_for_receipt(receipt)
assert acp.verify_inclusion(leaf, proof["proof"], proof["proof"]["root"])
```

## Environment

The SDK reads `ACP_API_KEY` and `ACP_BASE_URL` if not passed explicitly:

```bash
export ACP_API_KEY=acp_...
export ACP_BASE_URL=https://acp.example.com
```
