"""Autonomy service Alembic env — thin shim over sdk.common.alembic_env."""
from sdk.common.alembic_env import run
from services.autonomy.models import (  # noqa: F401
    AutonomyContract,
    AutonomyViolation,
    HumanOverrideEvent,
)

run(
    version_table="alembic_version_autonomy",
    owned_tables={
        "autonomy_contracts",
        "autonomy_contract_violations",
        "human_override_events",
    },
)
