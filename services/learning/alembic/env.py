"""Learning service Alembic env — thin shim over sdk.common.alembic_env."""
from sdk.common.alembic_env import run
from services.learning.models import BehaviorProfileModel  # noqa: F401

run(
    version_table="alembic_version_learning",
    owned_tables={"behavior_profiles"},
)
