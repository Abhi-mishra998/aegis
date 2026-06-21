"""
Scheduled report delivery worker.

Polls Redis for ``acp:report_trigger:{report_id}`` keys written by
``trigger_report_now()``, generates the appropriate PDF, and emails it
to the report's recipients via SMTP.

SMTP configuration (all optional — delivery silently skipped when absent):
    SMTP_HOST       defaults to "localhost"
    SMTP_PORT       defaults to 587
    SMTP_USER       SMTP login username
    SMTP_PASSWORD   SMTP login password
    SMTP_FROM       From address   (default "noreply@aegisagent.in")
    SMTP_USE_TLS    "true" to enable STARTTLS (default true)
"""
from __future__ import annotations

import asyncio
import email.mime.application
import email.mime.multipart
import email.mime.text
import os
import smtplib
from datetime import UTC, datetime

import structlog

from services.audit.scheduled_reports import (
    ScheduledReport,
    record_delivery,
)

logger = structlog.get_logger(__name__)

_SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
_SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
_SMTP_USER = os.environ.get("SMTP_USER", "")
_SMTP_PASS = os.environ.get("SMTP_PASSWORD", "")
_SMTP_FROM = os.environ.get("SMTP_FROM", "noreply@aegisagent.in")
_SMTP_TLS  = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"

_POLL_INTERVAL = 30  # seconds between Redis scans


def _is_smtp_configured() -> bool:
    return bool(_SMTP_USER and _SMTP_PASS and _SMTP_HOST != "localhost")


def _send_email_sync(recipients: list[str], subject: str, body: str, pdf_bytes: bytes, filename: str) -> None:
    """Blocking SMTP send — called in a thread so it doesn't block the event loop."""
    msg = email.mime.multipart.MIMEMultipart()
    msg["From"]    = _SMTP_FROM
    msg["To"]      = ", ".join(recipients)
    msg["Subject"] = subject

    msg.attach(email.mime.text.MIMEText(body, "plain"))

    attachment = email.mime.application.MIMEApplication(pdf_bytes, Name=filename)
    attachment["Content-Disposition"] = f'attachment; filename="{filename}"'
    msg.attach(attachment)

    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as smtp:
        if _SMTP_TLS:
            smtp.starttls()
        if _SMTP_USER:
            smtp.login(_SMTP_USER, _SMTP_PASS)
        smtp.sendmail(_SMTP_FROM, recipients, msg.as_string())


def _send_html_email_sync(recipients: list[str], subject: str, text_body: str, html_body: str) -> None:
    """Send a multipart/alternative email (text + HTML, no attachment)."""
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["From"]    = _SMTP_FROM
    msg["To"]      = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(email.mime.text.MIMEText(text_body, "plain"))
    msg.attach(email.mime.text.MIMEText(html_body, "html"))

    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as smtp:
        if _SMTP_TLS:
            smtp.starttls()
        if _SMTP_USER:
            smtp.login(_SMTP_USER, _SMTP_PASS)
        smtp.sendmail(_SMTP_FROM, recipients, msg.as_string())


async def _generate_pdf_for_report(report: ScheduledReport) -> tuple[bytes, str]:
    """
    Generate a PDF for *report* and return (pdf_bytes, filename).
    Falls back to an empty-page PDF if the report type is unrecognised.
    """
    today     = datetime.now(UTC).strftime("%Y-%m-%d")
    start_date = report.last_run_at.strftime("%Y-%m-%d") if report.last_run_at else "2026-01-01"
    end_date   = today

    rtype = (report.report_type or "").lower()

    if rtype == "board":
        from services.audit.board_report import generate_board_report_pdf
        pdf_bytes = generate_board_report_pdf(
            tenant_id=report.tenant_id,
            start_date=start_date,
            end_date=end_date,
            summary={},
            incidents=[],
            top_tools=[],
        )
        filename = f"aegis-board-report-{today}.pdf"

    elif rtype == "llm_cost":
        # LLM cost digest — returns None; caller handles as HTML email
        return None, ""

    elif rtype in ("compliance", "eu-ai-act", "nist", "soc2"):
        from services.audit.pdf_export import generate_compliance_pdf
        framework_map = {
            "compliance": "EU_AI_ACT",
            "eu-ai-act":  "EU_AI_ACT",
            "nist":       "NIST_AI_RMF",
            "soc2":       "SOC2",
        }
        framework = report.framework or framework_map.get(rtype, "EU_AI_ACT")
        pdf_bytes = generate_compliance_pdf(
            tenant_id=report.tenant_id,
            framework=framework,
            start_date=start_date,
            end_date=end_date,
        )
        filename = f"aegis-compliance-{framework.lower()}-{today}.pdf"

    else:
        # Unknown type — generate a minimal placeholder
        import io

        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4)
        doc.build([Paragraph(f"Aegis Report: {report.name}", getSampleStyleSheet()["Title"])])
        pdf_bytes = buf.getvalue()
        filename = f"aegis-report-{today}.pdf"

    return pdf_bytes, filename


async def _process_trigger(redis, session_factory, report_id: str, trigger_key: str) -> None:
    """Deliver one triggered report and clean up the Redis key."""
    async with session_factory() as db:
        # Use tenant_id=None to bypass tenant check (we have the report_id directly)
        from sqlalchemy.future import select
        result = await db.execute(select(ScheduledReport).where(ScheduledReport.id == report_id))
        report = result.scalar_one_or_none()

        if report is None:
            logger.warning("report_delivery_not_found", report_id=report_id)
            await redis.delete(trigger_key)
            return

        recipients: list[str] = report.recipients or []
        if not recipients:
            logger.info("report_delivery_no_recipients", report_id=report_id, name=report.name)
            await record_delivery(db, report_id, str(report.tenant_id), "skipped",
                                  triggered_by="scheduler", error_message="no_recipients")
            await redis.delete(trigger_key)
            return

        rtype = (report.report_type or "").lower()

        # ── LLM cost digest (inline HTML, no PDF attachment) ─────────────────
        if rtype == "llm_cost":
            try:
                import httpx as _httpx

                from services.audit.llm_cost_report import generate_llm_cost_email
                _api_url = os.environ.get("API_SERVICE_URL", "http://api:8001")
                from sdk.common.auth import mesh_headers
                resp = await _httpx.AsyncClient(timeout=10.0).get(
                    f"{_api_url}/billing/cost-attribution?weeks=4",
                    headers={**mesh_headers("audit"),
                             "X-Tenant-ID": str(report.tenant_id)},
                )
                cost_data = resp.json().get("data", {}) if resp.status_code == 200 else {}
            except Exception as exc:
                logger.warning("report_delivery_cost_fetch_failed", error=str(exc))
                cost_data = {}

            subject, text_body, html_body = generate_llm_cost_email(
                cost_data, tenant_label=str(report.tenant_id)[:8]
            )
            _t0 = datetime.now(UTC)
            if _is_smtp_configured():
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, _send_html_email_sync, recipients, subject, text_body, html_body
                    )
                    logger.info("report_delivery_llm_cost_sent",
                                report_id=report_id, recipients=recipients)
                    _dur = int((datetime.now(UTC) - _t0).total_seconds() * 1000)
                    await record_delivery(db, report_id, str(report.tenant_id), "success",
                                          triggered_by="scheduler", recipients=recipients,
                                          duration_ms=_dur)
                except Exception as exc:
                    logger.error("report_delivery_smtp_failed", report_id=report_id, error=str(exc))
                    await record_delivery(db, report_id, str(report.tenant_id), "failed",
                                          triggered_by="scheduler", recipients=recipients,
                                          error_message=str(exc)[:500])
                    return
            else:
                logger.info("report_delivery_skipped_no_smtp",
                            report_id=report_id, name=report.name,
                            hint="Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD to enable email delivery")
                await record_delivery(db, report_id, str(report.tenant_id), "skipped",
                                      triggered_by="scheduler", recipients=recipients,
                                      error_message="smtp_not_configured")
            report.last_run_at = datetime.now(UTC)
            await db.commit()
            await redis.delete(trigger_key)
            return

        # ── PDF reports ───────────────────────────────────────────────────────
        try:
            pdf_bytes, filename = await _generate_pdf_for_report(report)
        except Exception as exc:
            logger.error("report_delivery_pdf_failed", report_id=report_id, error=str(exc))
            await redis.delete(trigger_key)
            return

        subject = f"Aegis Report: {report.name} — {datetime.now(UTC).strftime('%Y-%m-%d')}"
        body = (
            f"Please find attached your scheduled Aegis report: {report.name}.\n\n"
            f"Report type: {report.report_type}\n"
            f"Generated:   {datetime.now(UTC).isoformat()}\n\n"
            f"— Aegis AI Governance Platform"
        )

        _t0 = datetime.now(UTC)
        if _is_smtp_configured():
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, _send_email_sync, recipients, subject, body, pdf_bytes, filename
                )
                logger.info(
                    "report_delivery_sent",
                    report_id=report_id,
                    name=report.name,
                    recipients=recipients,
                )
                _dur = int((datetime.now(UTC) - _t0).total_seconds() * 1000)
                await record_delivery(db, report_id, str(report.tenant_id), "success",
                                      triggered_by="scheduler", recipients=recipients,
                                      duration_ms=_dur)
            except Exception as exc:
                logger.error("report_delivery_smtp_failed", report_id=report_id, error=str(exc))
                await record_delivery(db, report_id, str(report.tenant_id), "failed",
                                      triggered_by="scheduler", recipients=recipients,
                                      error_message=str(exc)[:500])
                # Don't delete the key — let the next poll retry
                return
        else:
            # SMTP not configured — log and skip silently (dev/demo mode)
            logger.info(
                "report_delivery_skipped_no_smtp",
                report_id=report_id,
                name=report.name,
                recipients=recipients,
                hint="Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD to enable email delivery",
            )
            await record_delivery(db, report_id, str(report.tenant_id), "skipped",
                                  triggered_by="scheduler", recipients=recipients,
                                  error_message="smtp_not_configured")

        # Mark delivered
        report.last_run_at = datetime.now(UTC)
        await db.commit()
        await redis.delete(trigger_key)


async def run_report_delivery_worker(session_factory) -> None:
    """
    Long-running background task started from the audit service lifespan.
    Scans Redis for ``acp:report_trigger:*`` every ``_POLL_INTERVAL`` seconds
    and delivers each triggered report via SMTP.
    """
    import redis.asyncio as aioredis
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379")
    r = aioredis.from_url(redis_url, decode_responses=True)

    logger.info("report_delivery_worker_started", poll_interval=_POLL_INTERVAL)

    try:
        while True:
            try:
                keys = await r.keys("acp:report_trigger:*")
                for key in keys:
                    report_id = key.split(":")[-1]
                    await _process_trigger(r, session_factory, report_id, key)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("report_delivery_worker_error", error=str(exc))

            await asyncio.sleep(_POLL_INTERVAL)
    finally:
        await r.aclose()
        logger.info("report_delivery_worker_stopped")
