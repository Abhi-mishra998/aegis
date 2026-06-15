"""Sprint 5 — Pydantic schemas for the Attack Evaluation Suite.

These mirror the audit-service convention (Sprint 4 fleet schemas): plain
BaseModel, no ORM-style attrs, all date/uuid serialized as strings so the
dashboard doesn't need to teach Recharts about UUIDs.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


class DatasetCreateBody(BaseModel):
    name:         str
    kind:         str = Field("mixed", description="attack | benign | mixed")
    version:      str = Field("1")
    description:  str | None = None


class DatasetResponse(BaseModel):
    id:           str
    tenant_id:    str
    name:         str
    kind:         str
    version:      str
    description:  str | None = None
    case_count:   int
    created_by:   str | None = None
    created_at:   str


class DatasetCaseBody(BaseModel):
    case_kind:         str   = Field(..., description="attack | benign")
    owasp_category:    str   = Field(..., description="LLM01..LLM10 | benign | other")
    base_id:           str
    mutation:          str   = Field("none")
    payload_json:      dict[str, Any]
    expected_outcome:  str   = Field(..., description="deny | allow")
    expected_findings: list[str] = Field(default_factory=list)
    notes:             str | None = None


class DatasetCaseResponse(DatasetCaseBody):
    id:         str
    dataset_id: str
    tenant_id:  str
    created_at: str


# ---------------------------------------------------------------------------
# Evaluators
# ---------------------------------------------------------------------------


class EvaluatorCreateBody(BaseModel):
    name:        str
    kind:        str = Field(..., description="detection_rate | fp_rate | per_rule_efficacy")
    config_json: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    enabled:     bool = True


class EvaluatorResponse(BaseModel):
    id:          str
    tenant_id:   str
    name:        str
    kind:        str
    config_json: dict[str, Any]
    description: str | None = None
    enabled:     bool
    created_at:  str


# ---------------------------------------------------------------------------
# Eval Jobs + Results
# ---------------------------------------------------------------------------


class EvalJobCreateBody(BaseModel):
    dataset_id:    str
    evaluator_ids: list[str] = Field(default_factory=list)
    schedule:      str       = Field("manual", description="manual | nightly | shadow")


class EvalJobResponse(BaseModel):
    id:             str
    tenant_id:      str
    dataset_id:     str
    evaluator_ids:  list[str]
    schedule:       str
    status:         str
    cases_total:    int
    cases_done:     int
    summary_json:   dict[str, Any]
    error_message:  str | None = None
    created_by:     str | None = None
    queued_at:      str
    started_at:     str | None = None
    finished_at:    str | None = None


class EvalJobResultRow(BaseModel):
    id:                    str
    eval_job_id:           str
    case_id:               str
    owasp_category:        str
    case_kind:             str
    expected_outcome:      str
    actual_outcome:        str
    passed:                bool
    findings:              list[str]
    rule_attribution_json: dict[str, Any]
    latency_ms:            float
    error_message:         str | None = None
    created_at:            str


class EfficacyTrendPoint(BaseModel):
    evaluator_id:  str
    rule_id:       str | None = None
    snapshot_date: str
    score:         float
    samples:       int


class EfficacyOverview(BaseModel):
    detection_rate:      float
    fp_rate:             float
    cases_evaluated:     int
    attack_cases:        int
    benign_cases:        int
    last_run_at:         str | None
    per_owasp_category:  dict[str, dict[str, float]]
    per_rule:            dict[str, dict[str, float]]
