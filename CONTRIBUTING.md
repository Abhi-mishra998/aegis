# Contributing to Aegis

Thanks for wanting to contribute. This is a runtime security control plane — contributions that improve correctness, test coverage, or documentation are especially welcome.

## Before you open a PR

- **Bug fixes**: open an issue first describing the bug and reproduction steps. Link the issue in your PR.
- **New features**: open an issue with the `enhancement` label and discuss the approach before writing code. This avoids wasted effort on designs that won't merge.
- **Documentation**: PRs welcome without a prior issue for typos, clarifications, and missing detail.

## Setup

```bash
git clone https://github.com/Abhi-mishra998/aegis.git
cd aegis
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,server]"
```

To run the full stack locally (requires Docker):

```bash
cd infra && docker compose up --build -d
sleep 90
python scripts/utils/seed_admin.py
```

## Running tests

Unit tests (no Docker required):

```bash
python -m pytest tests/ -m "not integration" -v --tb=short
```

Full integration test suite (requires live stack):

```bash
python -m pytest tests/ -v --tb=short
```

Run the three end-to-end demo packs:

```bash
ACP_DRY_RUN=1 python demos/run_all_demos.py   # dry-run, no stack needed
python demos/run_all_demos.py                  # live run, stack required
```

## Pull request guidelines

- **One PR per change.** Don't bundle unrelated fixes.
- **Keep tests passing.** Run `pytest tests/ -m "not integration" -q` before pushing.
- **Match the existing code style.** Run `ruff check .` before pushing. Zero ruff warnings required.
- **Write real commit messages.** `fix: resolve JWT revocation race under high concurrency` not `update stuff`.
- **Update tests.** If you change behavior, add or update a test that covers it.

### Branch naming

```
fix/describe-the-fix
feat/describe-the-feature
docs/describe-the-doc-change
chore/describe-the-maintenance-task
```

## What's in scope

- Bug fixes in any of the 12 services
- Test coverage improvements (particularly integration gaps)
- Documentation improvements
- Performance improvements with benchmarks
- Additional OPA policy examples
- SDK language ports (TypeScript is next)

## What's out of scope (for now)

- Changes to the cryptographic primitives (ed25519, HMAC chain) without a detailed security rationale
- New external dependencies in the SDK's core runtime (`httpx`, `cryptography`, `PyYAML` are intentionally minimal)
- Breaking changes to the `/execute` API contract

## Code of conduct

Be direct. Be accurate. Don't be a jerk. If you see something wrong in the security model, say so clearly — that's the most valuable contribution possible.

## Questions

Open a GitHub Discussion or email `abhishekmishra09896@gmail.com`.
