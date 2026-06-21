"""N24 (2026-06-21) — guard rails on the init-db.sql grants for audit_user.

The DB init script (``infra/init-db.sql``) is what stands up acp_audit on
a fresh node. N24 trims audit_user from schema-level ALL PRIVILEGES down
to (USAGE + CREATE) on the schema plus (INSERT/SELECT/REFERENCES) on
tables — combined with the P0-3 audit_owner table ownership, this caps
the blast radius of a compromised superuser who briefly disables the
event trigger.

These tests guard against accidental regression of that contract by
asserting properties of the SQL text itself. We don't spin up Postgres
in the unit suite — the live grants are verified out-of-band via
``psql -c '\\dn+ public'`` in the LIVE_TEST_RECIPE.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def init_db_sql() -> str:
    """Read the init script once per module."""
    p = Path(__file__).resolve().parents[3] / "infra" / "init-db.sql"
    return p.read_text(encoding="utf-8")


def _section(sql: str, db: str) -> str:
    """Return the substring of init-db.sql scoped to one '\\c <db>' block."""
    # Each section begins with `\c <db>` and ends at the next `\c ` line.
    pattern = re.compile(
        rf"\\c\s+{re.escape(db)}\b(.+?)(?=\\c\s+\S|\Z)",
        re.DOTALL,
    )
    match = pattern.search(sql)
    assert match, f"no '\\c {db}' section found in init-db.sql"
    return match.group(1)


def test_audit_user_has_no_schema_all_privileges(init_db_sql: str) -> None:
    """The smoking-gun: GRANT ALL ON SCHEMA public TO audit_user MUST be gone."""
    audit_section = _section(init_db_sql, "acp_audit")
    # Bare 'GRANT ALL ON SCHEMA' is the N24 anti-pattern.
    assert "GRANT ALL ON SCHEMA public TO audit_user" not in audit_section


def test_audit_user_schema_grants_revoked_first(init_db_sql: str) -> None:
    """REVOKE ALL must precede the targeted GRANTs so the diff is clean."""
    audit_section = _section(init_db_sql, "acp_audit")
    revoke_idx = audit_section.find("REVOKE ALL PRIVILEGES ON SCHEMA public FROM audit_user")
    usage_idx = audit_section.find("GRANT USAGE ON SCHEMA public TO audit_user")
    assert revoke_idx >= 0, "must REVOKE schema privileges before re-granting"
    assert usage_idx > revoke_idx, "USAGE grant must come after REVOKE"


def test_audit_user_has_targeted_table_grants(init_db_sql: str) -> None:
    """Runtime needs INSERT/SELECT/REFERENCES on tables — explicit, not ALL."""
    audit_section = _section(init_db_sql, "acp_audit")
    assert "GRANT INSERT, SELECT, REFERENCES ON ALL TABLES IN SCHEMA public TO audit_user" in audit_section


def test_audit_user_keeps_create_on_schema_for_alembic(init_db_sql: str) -> None:
    """Alembic runs at audit-svc startup; without CREATE it can't add tables."""
    audit_section = _section(init_db_sql, "acp_audit")
    assert "GRANT CREATE ON SCHEMA public TO audit_user" in audit_section


def test_audit_user_no_longer_owns_public_schema(init_db_sql: str) -> None:
    """Schema ownership lets the user DROP/ALTER the schema itself — banned."""
    audit_section = _section(init_db_sql, "acp_audit")
    assert "ALTER SCHEMA public OWNER TO audit_user" not in audit_section


def test_other_service_users_still_have_owner_pattern(init_db_sql: str) -> None:
    """Sanity check: we only trimmed audit_user. The other services keep
    their existing OWNER pattern so this commit is intentionally minimal."""
    for db, user in [
        ("acp_registry",        "registry_user"),
        ("acp_identity",        "identity_user"),
        ("acp_api",             "api_user"),
        ("acp_usage",           "usage_user"),
        ("acp_identity_graph",  "identity_graph_user"),
        ("acp_flight_recorder", "flight_recorder_user"),
        ("acp_autonomy",        "autonomy_user"),
        ("acp_behavior",        "behavior_user"),
    ]:
        sec = _section(init_db_sql, db)
        assert f"GRANT ALL ON SCHEMA public TO {user}" in sec, (
            f"{user} unexpectedly lost schema ALL — N24 only targets audit_user"
        )


def test_default_privileges_keep_runtime_inserts_working(init_db_sql: str) -> None:
    """ALTER DEFAULT PRIVILEGES is what keeps new tables (added by future
    alembic migrations) readable + writable by audit_user without a
    follow-up GRANT in every migration."""
    audit_section = _section(init_db_sql, "acp_audit")
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA public" in audit_section
    assert "GRANT INSERT, SELECT, REFERENCES ON TABLES TO audit_user" in audit_section
