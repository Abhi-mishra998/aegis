"""Flight Recorder Alembic env — thin shim over sdk.common.alembic_env."""
from sdk.common.alembic_env import run
from services.flight_recorder.models import (  # noqa: F401
    ExecutionArtifact,
    ExecutionSnapshot,
    ExecutionStep,
    ExecutionTimeline,
)

run(
    version_table="alembic_version_flight_recorder",
    owned_tables={
        "execution_timelines",
        "execution_steps",
        "execution_snapshots",
        "execution_artifacts",
    },
)
