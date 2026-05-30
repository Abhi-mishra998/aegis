"""Identity Graph Alembic env — thin shim over sdk.common.alembic_env."""
from sdk.common.alembic_env import run
from services.identity_graph.models import (  # noqa: F401
    CompromiseSimulation,
    DriftSignal,
    GraphEdge,
    GraphNode,
    TrustScoreHistory,
)

run(
    version_table="alembic_version_identity_graph",
    owned_tables={
        "graph_nodes",
        "graph_edges",
        "trust_score_history",
        "drift_signals",
        "compromise_simulations",
    },
)
