"""Registry service Alembic env — thin shim over sdk.common.alembic_env."""
from sdk.common.alembic_env import run
from services.registry.models import Agent, AgentPermission  # noqa: F401

run(
    version_table="alembic_version_registry",
    owned_tables={
        "agents",
        "permissions",
        "permission_action_enum",
        "agent_status_enum",
    },
)
