# Changelog — aegis-bedrock

All notable changes to the `aegis-bedrock` PyPI package land here.
Follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [1.1.1] — 2026-06-18

### Fixed
- Source `__version__` string corrected from `1.0.0` to `1.1.1`. The
  published 1.1.0 wheel shipped a stale `__version__` (`1.0.0` inside
  the package); PyPI does not allow republishing a fixed wheel under
  the same version, hence the 1.1.1 release per SPRINT.md §5 B2.

### Notes
- No functional changes versus 1.1.0. The governance contract,
  `_aegis_blocked` metadata shape, and SDK API surface are unchanged.
- Drop-in replacement for 1.1.0 — no caller code changes required.

## [1.1.0] — 2026-05-30

### Added
- First public release of the Bedrock Agents drop-in.
- `AegisBedrockAgentRuntime` wrapper for `boto3.client("bedrock-agent-runtime")`.
- `AegisClient` synchronous /execute governance client.
- `_aegis_blocked` metadata attached to denied tool calls.
