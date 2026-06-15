# Aegis — AI Agent Governance (VS Code Extension)

View runtime decisions, signed receipts, and risk scores for every
`/execute` call your agents make. Read-only view onto your Aegis
tenant, served by the same gateway your production traffic flows
through.

## Install (development)

```bash
cd vscode-extension
npm install
npm run compile
# In VS Code: F5 to launch the Extension Development Host
```

## Configure

1. Create an Aegis API key:
   ```
   POST https://<your-gateway>/api-keys
   { "name": "vs-code-local" }
   ```
   The response carries the raw key once — save it.
2. In VS Code, run **Aegis: Set API Key** from the command palette.
3. (Optional) Set `aegis.gatewayUrl` in your settings if you're not
   pointing at `https://dev.aegisagent.in`.

## What you see

- **Aegis Decisions** view in the Explorer sidebar — the last N
  `/execute` decisions for your tenant, colored by outcome
  (allow / deny / throttle / escalate).
- Click any decision → side-by-side **signed receipt** webview
  with the ed25519 signature, signing-key id (`kid`), and the
  raw envelope JSON.

## What it does NOT do (yet)

- Edit policies (use the Aegis web UI — Sprint 7 Policy Playground).
- Run actions through the gateway (use your agent SDK).
- Show traces (use your existing OTel backend — Aegis exports decision
  spans to CloudWatch / Datadog / Grafana per Sprint 8 docs).

The extension is intentionally narrow: surface what Aegis decided,
with a tamper-evident receipt, inside the IDE where the developer
already lives.

## Privacy

The API key is stored in VS Code **SecretStorage** (OS keychain on
macOS / Linux secret service / Windows credential vault). It never
lands in `settings.json` or sync.
