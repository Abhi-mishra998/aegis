"""Audit service Alembic env — thin shim over sdk.common.alembic_env."""
from sdk.common.alembic_env import run

# Side-effect import: pull model classes into Base.metadata so alembic
# autogenerate sees them. The names are unused locally; ruff exception
# below silences the F401 noise.
from services.audit.models import (  # noqa: F401
    AuditLog,
    AuditNote,
    PendingUsageEvent,
    TransparencyRoot,
)

run(
    version_table="alembic_version_audit",
    owned_tables={
        "audit_logs",
        "audit_notes",
        "pending_usage_events",
        "transparency_roots",
    },
    match_types=("table",),  # audit historically excluded custom types
)
