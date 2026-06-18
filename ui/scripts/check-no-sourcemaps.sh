#!/usr/bin/env bash
# Asserts no source maps (file OR inline) in the prod bundle.
set -euo pipefail
cd "$(dirname "$0")/.."
if find dist -name "*.map" 2>/dev/null | grep -q .; then
  echo "ERROR: source map files found in dist/" >&2
  find dist -name "*.map" >&2
  exit 1
fi
if grep -rEl "sourceMappingURL=" dist/assets 2>/dev/null | grep -q .; then
  echo "ERROR: inline sourceMappingURL found in dist/assets/" >&2
  grep -rEl "sourceMappingURL=" dist/assets >&2
  exit 1
fi
echo "✓ no source maps in dist/"
