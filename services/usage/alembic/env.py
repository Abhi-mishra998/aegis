"""Usage service Alembic env — thin shim over sdk.common.alembic_env."""
from sdk.common.alembic_env import run

run(
    version_table="alembic_version_usage",
    owned_tables={"usage_records"},
    match_types=("table",),
)
