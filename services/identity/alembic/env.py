"""Identity service Alembic env — thin shim over sdk.common.alembic_env."""
from sdk.common.alembic_env import run
from services.identity.models import (  # noqa: F401
    AgentCredential,
    Organization,
    Tenant,
    User,
)

run(
    version_table="alembic_version_identity",
    owned_tables={
        "users",
        "agent_credentials",
        "credential_status_enum",
        "user_role_enum",
    },
)
