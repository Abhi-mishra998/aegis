"""
Anomaly Detection Evaluation Harness
=====================================
Generates a synthetic golden dataset (200 normal training + 100 normal test + 50
anomalous test) and measures Precision, Recall, and F1 for both the Isolation
Forest primary method and heuristic fallback.

Anomaly distribution (50 total test anomalies):
  - obvious_tool   (10): brand-new rare tool — caught by heuristic
  - obvious_vel    (10): velocity spike 4–8× baseline — caught by heuristic
  - obvious_tok    (10): token spike 4–8× baseline — caught by heuristic
  - subtle_vel     (10): 1.5–2.9× velocity + unusual hour — heuristic misses
  - subtle_combo   (10): 2× velocity + 2× tokens — heuristic misses

This distribution produces heuristic recall ≈ 0.60 (high precision, low recall)
while Isolation Forest (trained on temporal + multi-feature patterns) captures
most subtle cases too.

Usage:
    PYTHONPATH=/path/to/acp python3 tests/eval/anomaly_eval.py
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from services.decision.anomaly import AnomalyDetector

# ── Configuration ─────────────────────────────────────────────────────────────
N_TRAIN_NORMAL   = 200
N_TEST_NORMAL    = 100
N_TEST_ANOMALOUS = 50

_NORMAL_TOOLS = ["read_file", "list_dir", "http_get", "db_query", "send_email", "log_event"]
_RARE_TOOLS   = ["exec_shell", "write_file", "delete_file", "escalate_privilege"]

_BASE_VELOCITY  = 1.8   # calls/min — normal baseline
_BASE_TOKENS    = 220   # tokens — normal baseline
_WORK_HOUR_MIN  = 8     # normal hours: 08:00–18:00
_WORK_HOUR_MAX  = 18

rng = random.Random(42)


# ── Generators ────────────────────────────────────────────────────────────────

def _work_ts(base: datetime, minute_offset: int) -> datetime:
    """Returns a timestamp inside working hours (09:00–17:00)."""
    ts = base + timedelta(minutes=minute_offset)
    # Keep hour in working window
    hour = _WORK_HOUR_MIN + (ts.hour % (_WORK_HOUR_MAX - _WORK_HOUR_MIN))
    return ts.replace(hour=hour, minute=ts.minute % 60)


def _off_hours_ts(base: datetime, minute_offset: int) -> datetime:
    """Returns a 03:00 timestamp — off hours for anomaly detection."""
    ts = base + timedelta(minutes=minute_offset)
    return ts.replace(hour=3, minute=ts.minute % 60)


def make_normal_row(base: datetime, i: int) -> dict[str, Any]:
    tool = rng.choice(_NORMAL_TOOLS)
    return {
        "tool": tool,
        "timestamp": _work_ts(base, i * 7),
        "velocity": rng.gauss(_BASE_VELOCITY, 0.4),
        "tokens": max(50, int(rng.gauss(_BASE_TOKENS, 40))),
    }


def make_anomaly_row(kind: str, base: datetime, i: int) -> dict[str, Any]:
    if kind == "obvious_tool":
        return {
            "tool": rng.choice(_RARE_TOOLS),
            "timestamp": _work_ts(base, i * 7),
            "velocity": rng.gauss(_BASE_VELOCITY, 0.4),
            "tokens": max(50, int(rng.gauss(_BASE_TOKENS, 40))),
        }
    if kind == "obvious_vel":
        return {
            "tool": rng.choice(_NORMAL_TOOLS),
            "timestamp": _work_ts(base, i * 7),
            "velocity": _BASE_VELOCITY * rng.uniform(4.0, 8.0),
            "tokens": max(50, int(rng.gauss(_BASE_TOKENS, 40))),
        }
    if kind == "obvious_tok":
        return {
            "tool": rng.choice(_NORMAL_TOOLS),
            "timestamp": _work_ts(base, i * 7),
            "velocity": rng.gauss(_BASE_VELOCITY, 0.4),
            "tokens": int(_BASE_TOKENS * rng.uniform(4.0, 8.0)),
        }
    if kind == "subtle_vel":
        # Velocity 1.5–2.9× baseline (below 3× threshold) + off-hours
        return {
            "tool": rng.choice(_NORMAL_TOOLS),
            "timestamp": _off_hours_ts(base, i * 7),
            "velocity": _BASE_VELOCITY * rng.uniform(1.5, 2.9),
            "tokens": max(50, int(rng.gauss(_BASE_TOKENS, 40))),
        }
    # subtle_combo: 2× velocity + 2× tokens, off-hours
    return {
        "tool": rng.choice(_NORMAL_TOOLS),
        "timestamp": _off_hours_ts(base, i * 7),
        "velocity": _BASE_VELOCITY * rng.uniform(1.8, 2.5),
        "tokens": int(_BASE_TOKENS * rng.uniform(1.8, 2.5)),
    }


def build_dataset():
    base = datetime(2026, 1, 6, 9, 0, 0)  # Monday

    train_rows = [make_normal_row(base, i) for i in range(N_TRAIN_NORMAL)]

    test_offset = N_TRAIN_NORMAL * 7  # minutes — well after training window
    test_normal = [
        make_normal_row(base, test_offset + i * 7) for i in range(N_TEST_NORMAL)
    ]

    kinds = (
        ["obvious_tool"] * 10 +
        ["obvious_vel"]  * 10 +
        ["obvious_tok"]  * 10 +
        ["subtle_vel"]   * 10 +
        ["subtle_combo"] * 10
    )
    test_anomalous = [
        make_anomaly_row(kind, base, test_offset + N_TEST_NORMAL * 7 + i * 7)
        for i, kind in enumerate(kinds)
    ]

    return train_rows, test_normal, test_anomalous


# ── Evaluation ────────────────────────────────────────────────────────────────

def evaluate(detector: AnomalyDetector, agent_id: str,
             normal_rows: list[dict], anomalous_rows: list[dict]) -> dict:
    tp = fp = tn = fn = 0

    def _score(row: dict) -> bool:
        ts = row["timestamp"] if isinstance(row["timestamp"], datetime) else datetime.fromisoformat(row["timestamp"])
        result = detector.score(
            agent_id=agent_id,
            tool=row.get("tool", "unknown"),
            velocity=float(row.get("velocity", 0.0)),
            tokens=int(row.get("tokens", 0)),
            ts=ts,
        )
        return result.is_anomaly

    for row in normal_rows:
        if _score(row):
            fp += 1
        else:
            tn += 1

    for row in anomalous_rows:
        if _score(row):
            tp += 1
        else:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {
        "precision": round(precision, 4),
        "recall":    round(recall, 4),
        "f1":        round(f1, 4),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run_eval() -> None:
    train_rows, test_normal, test_anomalous = build_dataset()
    failures = []

    # ── Isolation Forest ──────────────────────────────────────────────────────
    if_detector = AnomalyDetector()
    if_detector.train("eval_if", train_rows)

    if if_detector._models.get("eval_if") is not None:
        m = evaluate(if_detector, "eval_if", test_normal, test_anomalous)
        print("Isolation Forest:")
        print(f"  Precision={m['precision']:.2f}  Recall={m['recall']:.2f}  F1={m['f1']:.2f}")
        print(f"  TP={m['tp']}  FP={m['fp']}  TN={m['tn']}  FN={m['fn']}")
        if m["f1"] < 0.55:
            failures.append(f"IF F1 {m['f1']:.2f} < 0.55 (regression)")
        if_metrics = m
    else:
        print("Isolation Forest: scikit-learn not installed — skipping")
        if_metrics = None

    # ── Heuristic fallback ────────────────────────────────────────────────────
    h_detector = AnomalyDetector()
    h_detector.train("eval_h", train_rows)
    h_detector._models["eval_h"] = None  # force heuristic branch

    hm = evaluate(h_detector, "eval_h", test_normal, test_anomalous)
    print("\nHeuristic fallback:")
    print(f"  Precision={hm['precision']:.2f}  Recall={hm['recall']:.2f}  F1={hm['f1']:.2f}")
    print(f"  TP={hm['tp']}  FP={hm['fp']}  TN={hm['tn']}  FN={hm['fn']}")

    if if_metrics is not None:
        if if_metrics["recall"] <= hm["recall"]:
            print("\n  Note: IF recall should exceed heuristic (catches subtle anomalies)")

    # ── Regression guards ─────────────────────────────────────────────────────
    print("\nRegression checks:")
    if hm["precision"] < 0.80:
        failures.append(f"Heuristic precision {hm['precision']:.2f} < 0.80")
    if hm["recall"] < 0.30:
        failures.append(f"Heuristic recall {hm['recall']:.2f} < 0.30")

    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        sys.exit(1)
    else:
        print("  All checks PASSED")


if __name__ == "__main__":
    run_eval()
