# Changelog — aegis-aevf

All notable changes to the `aegis-aevf` PyPI package land here. The package
implements the AEVF (Aegis Evidence Verification Format) spec at `aevf/0.1.0`;
the spec version is independent of the package version and is recorded in
each evidence bundle as `format_version`.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the package version follows [Semantic Versioning 2.0.0](https://semver.org/).

## [1.1.0] — 2026-06-18

### Changed
- Version-sync release. Source `__version__` and `pyproject.toml` version
  brought to `1.1.0` to match the `agies-bussiness.md` v1.3.0 product context
  ahead of the SPRINT.md Track B1 PyPI publish.

### Notes
- **No functional changes** versus 1.0.0. AEVF spec version unchanged at
  `aevf/0.1.0`. The V1–V6 verification checks are byte-identical to 1.0.0.
- Existing reference bundles validated under 1.0.0 continue to validate
  under 1.1.0.

## [1.0.0] — 2026-05-30

### Added
- First public release of the offline verifier.
- AEVF `aevf/0.1.0` reference implementation.
- V1–V6 verification checks (signature, Merkle chain, daily-root chain,
  bundle integrity, key fingerprint, Article-mapping coverage).
- `aegis-verify` CLI entry point.
- Zero network calls — single runtime dependency on `cryptography`.
