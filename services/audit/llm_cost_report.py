"""
LLM Cost Showback Report Generator.

Produces a plain-text + HTML weekly cost digest email from the billing
cost-attribution data.  No PDF dependency — sent as an inline email body.

Usage:
    from services.audit.llm_cost_report import generate_llm_cost_email
    subject, text_body, html_body = generate_llm_cost_email(attribution_data)
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def generate_llm_cost_email(
    data: dict[str, Any],
    tenant_label: str = "your tenant",
) -> tuple[str, str, str]:
    """
    Build a weekly cost digest from cost-attribution data.

    Args:
        data: response body from GET /billing/cost-attribution
        tenant_label: human-readable tenant name for the subject line

    Returns:
        (subject, text_body, html_body)
    """
    now          = datetime.now(UTC).strftime("%Y-%m-%d")
    grand_total  = data.get("grand_total", 0.0)
    period_weeks = data.get("period_weeks", 4)
    agents       = data.get("agents", [])
    weeks        = data.get("weeks", [])
    totals       = data.get("totals_by_week", {})
    by_agent     = data.get("by_agent_by_week", {})

    subject = (
        f"Aegis Weekly LLM Cost Report — ${grand_total:.2f} "
        f"({period_weeks}w) — {now}"
    )

    # ── Plain-text body ───────────────────────────────────────────────────────
    lines: list[str] = [
        "AEGIS AI GOVERNANCE — LLM Cost Showback Report",
        f"Generated: {now}  |  Period: last {period_weeks} weeks",
        f"Tenant: {tenant_label}",
        "",
        f"GRAND TOTAL:  ${grand_total:.4f}",
        "",
    ]

    if weeks:
        lines.append("WEEKLY TOTALS:")
        for w in weeks:
            lines.append(f"  {w}:  ${totals.get(w, 0.0):.4f}")
        lines.append("")

    if agents:
        lines.append("TOP AGENTS BY COST:")
        for i, agent in enumerate(agents[:10], 1):
            aid  = agent["agent_id"]
            cost = agent["total_cost"]
            calls = agent["total_calls"]
            lines.append(f"  {i:2}. {aid[:8]}…  ${cost:.4f}  ({calls} calls)")
        lines.append("")

    lines += [
        "View full details: https://aegisagent.in/billing",
        "Manage cost caps:  https://aegisagent.in/billing",
        "",
        "— Aegis AI Governance Platform",
        "   Unsubscribe: remove your email from Scheduled Reports in the dashboard",
    ]
    text_body = "\n".join(lines)

    # ── HTML body ─────────────────────────────────────────────────────────────
    agent_rows = ""
    for _i, agent in enumerate(agents[:10], 1):
        aid   = agent["agent_id"]
        cost  = agent["total_cost"]
        calls = agent["total_calls"]
        week_cells = "".join(
            f"<td style='text-align:right;padding:4px 8px;color:#94a3b8'>"
            f"${by_agent.get(aid, {}).get(w, 0.0):.3f}</td>"
            for w in weeks[-4:]  # show last 4 weeks max
        )
        agent_rows += (
            f"<tr style='border-bottom:1px solid #1e293b'>"
            f"<td style='padding:4px 8px;font-family:monospace'>{aid[:8]}…</td>"
            f"<td style='text-align:right;padding:4px 8px;font-weight:600;color:#f1f5f9'>"
            f"${cost:.4f}</td>"
            f"<td style='text-align:right;padding:4px 8px;color:#94a3b8'>{calls}</td>"
            f"{week_cells}"
            f"</tr>"
        )

    week_headers = "".join(
        f"<th style='padding:4px 8px;color:#64748b'>{w}</th>" for w in weeks[-4:]
    )

    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="background:#0f172a;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:24px">
  <div style="max-width:680px;margin:0 auto">
    <div style="border-bottom:2px solid #6366f1;padding-bottom:16px;margin-bottom:24px">
      <h1 style="margin:0;font-size:20px;font-weight:700;color:#fff">Aegis LLM Cost Report</h1>
      <p style="margin:4px 0 0;color:#94a3b8;font-size:13px">
        {tenant_label} · Last {period_weeks} weeks · Generated {now}
      </p>
    </div>

    <div style="background:#1e293b;border-radius:12px;padding:20px;margin-bottom:20px;text-align:center">
      <p style="margin:0;font-size:13px;color:#94a3b8">Total LLM Inference Cost</p>
      <p style="margin:8px 0 0;font-size:36px;font-weight:700;color:#6366f1">${grand_total:.4f}</p>
    </div>

    <table style="width:100%;border-collapse:collapse;background:#1e293b;border-radius:8px;overflow:hidden;margin-bottom:20px">
      <thead>
        <tr style="background:#0f172a">
          <th style="text-align:left;padding:8px 12px;color:#64748b;font-size:12px">Agent</th>
          <th style="text-align:right;padding:8px 12px;color:#64748b;font-size:12px">Total Cost</th>
          <th style="text-align:right;padding:8px 12px;color:#64748b;font-size:12px">Calls</th>
          {week_headers}
        </tr>
      </thead>
      <tbody>{agent_rows}</tbody>
    </table>

    <p style="font-size:12px;color:#475569;text-align:center">
      <a href="https://aegisagent.in/billing" style="color:#6366f1">View full report</a> ·
      <a href="https://aegisagent.in/billing" style="color:#6366f1">Manage cost caps</a>
    </p>
    <p style="font-size:11px;color:#334155;text-align:center">
      Aegis AI Governance Platform · Remove from Scheduled Reports to unsubscribe
    </p>
  </div>
</body>
</html>"""

    return subject, text_body, html_body
