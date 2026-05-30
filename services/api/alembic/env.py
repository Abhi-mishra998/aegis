"""API service Alembic env — thin shim over sdk.common.alembic_env."""
from sdk.common.alembic_env import run

run(
    version_table="alembic_version_api",
    owned_tables={"api_keys"},
    match_types=("table",),
)
