"""Sprint 4 — Pydantic schemas for the Fleet dashboard surface."""
from __future__ import annotations


from pydantic import BaseModel


class FleetKPIs(BaseModel):
    """KPI card payload for the Fleet Home dashboard."""

    window_minutes:  int
    decisions:       int
    denied:          int
    errors:          int
    deny_rate:       float
    error_rate:      float
    active_agents:   int
    distinct_tools:  int


class FleetTimeseriesPoint(BaseModel):
    """One bucket of a Fleet time-series. ``t`` is an ISO-8601 timestamp."""

    t: str
    v: float


class FleetAgentHealthRow(BaseModel):
    """One row in the Agent Health ranking table."""

    agent_id:   str | None
    volume:     int
    denied:     int
    errors:     int
    deny_rate:  float
    error_rate: float
    avg_risk:   float
    last_seen:  str | None


class FleetRecentEvent(BaseModel):
    """One row in the Recent Denied / Errored events table."""

    audit_id:   str
    timestamp:  str | None
    agent_id:   str | None = None
    tool:       str | None = None
    action:     str | None = None
    decision:   str | None = None
    reason:     str | None = None
    request_id: str | None = None
    risk_score: float | None = None
