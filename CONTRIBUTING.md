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
- **Behavioural tests, not string-match.** Don't add tests of the form
  `assert "X" in Path("ui/src/pages/Foo.jsx").read_text()`. Sprint-6
  deleted 16 of those files (1,324 LOC) because they caught zero bugs
  and broke on every cosmetic rename. Use Playwright (`ui/tests/e2e/`)
  or pytest with FastAPI's TestClient instead.
- **CHANGELOG entry.** Add a one-line entry under `## [Unreleased]` for
  any user-visible change.

## Architectural rules (so future PRs stay reviewable)

Codified from the post-sprint-6 audit closure. Breaking these is what
created the security findings the past sprints had to fix.

1. **Never `except: pass`.** Bare except + pass swallows real errors.
   Use a typed exception and either re-raise or log structured.
2. **Never fire-and-forget billing or audit writes.** The outbox pattern
   exists for a reason. `services/audit/writer.py:64-96` is the
   canonical example.
3. **Every new tenant-scoped route gets `Depends(get_tenant_id)`** and a
   downstream `tenant_id ==` check. Path tenant_id without a JWT match
   is what created the sprint-1 cross-tenant kill-switch CRITICAL.
4. **Every new `/admin/*` route gets `require_admin_role(request)`** at
   the gateway. The middleware only blocks WRITE methods for non-admin;
   GETs slip through without an explicit gate. See
   `services/gateway/_helpers.py:require_admin_role`.
5. **Every new container port is bound to `127.0.0.1`** unless it is the
   gateway (8000) or the UI (5173). See `infra/docker-compose.yml`.
6. **Every new env-loaded secret is in `sdk/common/config.py`'s Pydantic
   `Field(...)` with no default.** Defaults like `"change_me_internal"`
   become production trust boundaries.
7. **Every new alert rule gets a Slack/PagerDuty route in
   `infra/alertmanager.yml`.** A rule that doesn't page anybody is
   monitoring theater.
8. **Every new gateway route under `/admin/*`, `/decision/*`, or
   `/autonomy/*` lives in `services/gateway/routers/<domain>.py`,
   not `main.py`.** Add a new file if there isn't one already; do not
   grow the god-file. Current size: 3,654 LOC; target: under 1,000.

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
