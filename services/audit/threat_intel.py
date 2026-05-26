"""Lightweight threat intel enrichment. No API key = returns cached/demo data."""
from __future__ import annotations

import hashlib
import os

import httpx

ABUSEIPDB_KEY = os.environ.get("ABUSEIPDB_API_KEY", "")
OTX_KEY = os.environ.get("OTX_API_KEY", "")
TIMEOUT = 8.0


async def enrich_ip(ip: str) -> dict:
    """Check IP against AbuseIPDB. Returns {ip, abuse_score, country, isp, is_tor, reports, source}."""
    if not ABUSEIPDB_KEY:
        return _demo_ip(ip)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(
                "https://api.abuseipdb.com/api/v2/check",
                params={"ipAddress": ip, "maxAgeInDays": 90},
                headers={"Key": ABUSEIPDB_KEY, "Accept": "application/json"},
            )
        d = r.json().get("data", {})
        return {
            "ip": ip,
            "abuse_score": d.get("abuseConfidenceScore", 0),
            "country": d.get("countryCode", ""),
            "isp": d.get("isp", ""),
            "is_tor": d.get("isTor", False),
            "reports": d.get("totalReports", 0),
            "source": "abuseipdb",
            "status": "ok",
        }
    except Exception as exc:
        return {"ip": ip, "error": str(exc), "status": "error"}


def _demo_ip(ip: str) -> dict:
    h = int(hashlib.md5(ip.encode()).hexdigest(), 16)
    return {
        "ip": ip,
        "abuse_score": h % 100,
        "country": ["US", "DE", "CN", "RU", "GB"][h % 5],
        "isp": "Demo ISP",
        "is_tor": h % 7 == 0,
        "reports": h % 50,
        "source": "demo",
        "demo_mode": True,
        "status": "ok",
    }


async def enrich_domain(domain: str) -> dict:
    """Check domain reputation. Returns {domain, score, categories, source}."""
    if not OTX_KEY:
        return _demo_domain(domain)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(
                f"https://otx.alienvault.com/api/v1/indicators/domain/{domain}/general",
                headers={"X-OTX-API-KEY": OTX_KEY},
            )
        d = r.json()
        score = len(d.get("pulse_info", {}).get("pulses", [])) * 10
        return {
            "domain": domain,
            "score": min(score, 100),
            "categories": d.get("sections", []),
            "source": "otx",
            "status": "ok",
        }
    except Exception as exc:
        return {"domain": domain, "error": str(exc), "status": "error"}


def _demo_domain(domain: str) -> dict:
    h = int(hashlib.md5(domain.encode()).hexdigest(), 16)
    categories = ["malware", "phishing", "c2", "spam", "clean"]
    return {
        "domain": domain,
        "score": h % 85,
        "categories": [categories[h % 5]],
        "source": "demo",
        "demo_mode": True,
        "status": "ok",
    }
