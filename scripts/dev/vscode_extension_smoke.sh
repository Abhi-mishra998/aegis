#!/usr/bin/env bash
# Sprint 8 — Smoke test the VS Code extension compile + runtime URL logic.
#
# Run from the repo root:
#
#   bash scripts/dev/vscode_extension_smoke.sh
#
# Exits non-zero if the TypeScript compile fails OR the smoke assertions
# trip. Used in CI to catch regressions in the extension's HTTP client
# without spinning up a full VS Code Extension Development Host.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXT_DIR="${REPO_ROOT}/vscode-extension"

cd "${EXT_DIR}"

if [[ ! -d node_modules ]]; then
  echo "[vscode-smoke] installing npm deps…"
  npm install --no-audit --no-fund --silent
fi

echo "[vscode-smoke] compiling TypeScript…"
./node_modules/.bin/tsc -p .

echo "[vscode-smoke] running runtime assertions…"
node out/smoke.js

echo "[vscode-smoke] OK"
