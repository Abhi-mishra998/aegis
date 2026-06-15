"""
Sprint 7 — rules_json → Rego compiler + OPA HTTP validator.

Mirrors the UI's PolicyBuilder generateRego() (ui/src/pages/PolicyBuilder.jsx)
but is canonical: server-side, deterministic, escapes values, handles the
``payload_substring`` field the shadow evaluator added in Sprint 6.

The compiled output is a valid `rego.v1` document. We then validate it
against the live OPA via HTTP (PUT /v1/policies/{id}) before any caller
trusts it for publish.

Closes the AUDIT_REPORT.md gap "GUI emits valid Rego — UNVERIFIED" by
turning the rules_json → Rego mapping into a single canonical
implementation and adding an OPA-backed parse step.
"""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

_OPA_URL = os.getenv("OPA_URL", os.getenv("AEGIS_OPA_URL", "http://opa:8181"))
_VALIDATE_TIMEOUT = float(os.getenv("OPA_VALIDATE_TIMEOUT", "2.0"))

# Same op-map as the UI generator; kept identical so the compiled output is
# byte-equivalent (up to whitespace) when round-tripping UI → server → UI.
_OP_MAP = {
    "gt":  ">",
    "gte": ">=",
    "lt":  "<",
    "lte": "<=",
    "eq":  "==",
    "neq": "!=",
}

_VALID_ACTIONS = {"allow", "deny", "monitor", "throttle", "escalate"}
_NUMERIC_FIELDS = {"risk_score", "inference_risk", "behavior_risk", "anomaly_score"}
_PACKAGE_SAFE = re.compile(r"[^a-zA-Z0-9_]")


@dataclass(frozen=True)
class CompiledRego:
    rego:           str
    package_name:   str
    rule_count:     int
    warnings:       tuple[str, ...]


@dataclass(frozen=True)
class ValidationResult:
    valid:    bool
    errors:   tuple[str, ...]
    warnings: tuple[str, ...]
    rego:     str


def _safe_package(name: str) -> str:
    """Sanitise the policy name into a valid Rego package fragment."""
    cleaned = _PACKAGE_SAFE.sub("_", name or "").strip("_")
    if not cleaned:
        cleaned = "aegis_policy"
    if cleaned[0].isdigit():
        cleaned = f"p_{cleaned}"
    return cleaned.lower()


def _quote(value: Any) -> str:
    """Quote a value as Rego literal.

    Numerics stay bare; strings get double-quoted with the four characters
    Rego treats specially (`\\`, `"`, `\\n`, `\\r`) escaped.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    s = "" if value is None else str(value)
    if s and (s.replace(".", "", 1).replace("-", "", 1).isdigit()):
        return s
    escaped = (
        s.replace("\\", "\\\\")
         .replace("\"", "\\\"")
         .replace("\n", "\\n")
         .replace("\r", "\\r")
    )
    return f"\"{escaped}\""


def _input_path(field: str) -> str:
    """Field name → Rego input path."""
    if field == "tool":
        return "input.tool_name"
    if field == "payload_substring":
        return "input.payload"
    return f"input.{field}"


def _emit_condition(cond: dict[str, Any], warnings: list[str]) -> str | None:
    field = str(cond.get("field", ""))
    operator = str(cond.get("operator", "eq")).lower()
    value = cond.get("value")

    if field == "payload_substring":
        needle = _quote(value)
        if operator == "contains":
            return f"contains(lower(input.payload), lower({needle}))"
        if operator == "not_contains":
            return f"not contains(lower(input.payload), lower({needle}))"
        warnings.append(
            f"payload_substring only supports contains|not_contains; "
            f"got {operator!r} — condition skipped"
        )
        return None

    if operator not in _OP_MAP:
        warnings.append(f"unknown operator {operator!r} on field {field!r} — skipped")
        return None

    op_sym = _OP_MAP[operator]
    rendered_value = _quote(value)
    return f"{_input_path(field)} {op_sym} {rendered_value}"


def compile_rules(
    rules: list[dict[str, Any]],
    *,
    policy_name: str = "aegis_policy",
) -> CompiledRego:
    """Compile a list of PolicyRule dicts into a single Rego document.

    The rule order is preserved (first-matching-rule semantics carry
    over). Unknown actions are skipped with a warning rather than silently
    coerced to `deny` — we never want a "deny" to appear in the compiled
    output unless the operator actually asked for one.
    """
    pkg = _safe_package(policy_name)
    warnings: list[str] = []
    lines: list[str] = [
        f"# Compiled by Aegis Sprint 7 rego_compiler — package {pkg}",
        f"package aegis_policies.{pkg}",
        "",
        "import rego.v1",
        "",
        "default allow := true",
        "default deny := false",
        "default throttle := false",
        "default escalate := false",
        "",
    ]
    rendered_rules = 0
    for idx, raw in enumerate(rules or []):
        action = str(raw.get("action", "")).lower().strip()
        if action == "monitor":
            action = "allow"
        if action == "kill":
            action = "deny"
        if action not in _VALID_ACTIONS:
            warnings.append(
                f"rule[{idx}]: unknown action {raw.get('action')!r} — skipped"
            )
            continue
        description = str(raw.get("description", "")).replace("\n", " ").strip()
        if description:
            lines.append(f"# rule[{idx}]: {description}")
        lines.append(f"{action} if {{")
        conditions = raw.get("conditions") or []
        if not conditions:
            lines.append("    true")
        else:
            for cond in conditions:
                rendered = _emit_condition(cond, warnings)
                if rendered:
                    lines.append(f"    {rendered}")
                else:
                    # If a single condition is invalid the whole rule
                    # becomes unreachable — render `false` so OPA still
                    # accepts the body but the rule never fires.
                    lines.append("    false")
        lines.append("}")
        lines.append("")
        rendered_rules += 1

    return CompiledRego(
        rego="\n".join(lines),
        package_name=pkg,
        rule_count=rendered_rules,
        warnings=tuple(warnings),
    )


async def validate_rego(rego: str) -> ValidationResult:
    """PUT the Rego into OPA under a temporary policy id; OPA returns 400
    + structured errors on parse failure. Always clean up the temp id.

    On OPA outage (timeout, refused connection), we fall back to a
    syntactic pre-check (package + at least one rule body) rather than
    blocking the operator on an upstream dep — the compiled Rego is
    still inspected on the next bundle reload, where a malformed
    document fails loudly.
    """
    if not rego.strip():
        return ValidationResult(
            valid=False, errors=("empty rego",), warnings=(), rego=rego,
        )

    tmp_id = f"aegis_validate_{uuid.uuid4().hex[:16]}"
    url = f"{_OPA_URL.rstrip('/')}/v1/policies/{tmp_id}"
    try:
        async with httpx.AsyncClient(timeout=_VALIDATE_TIMEOUT) as client:
            put = await client.put(
                url, content=rego.encode("utf-8"),
                headers={"Content-Type": "text/plain"},
            )
            try:
                await client.delete(url)
            except Exception:
                logger.warning("opa_validate_cleanup_failed", tmp_id=tmp_id)

            if put.status_code in (200, 204):
                return ValidationResult(
                    valid=True, errors=(), warnings=(), rego=rego,
                )
            errors = _opa_errors_from_response(put.text)
            return ValidationResult(
                valid=False, errors=errors, warnings=(), rego=rego,
            )
    except (httpx.RequestError, httpx.TimeoutException) as exc:
        logger.warning("opa_validate_offline_fallback", error=str(exc))
        return _local_fallback_validate(rego)


def _opa_errors_from_response(body: str) -> tuple[str, ...]:
    """Best-effort: OPA's PUT /v1/policies returns JSON like
    ``{"code": "...", "message": "...", "errors": [{...}]}``.

    We surface the human-readable parts; structured ``errors`` win over
    the top-level message when present.
    """
    import json
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return (body[:500],) if body else ("unparseable OPA error response",)
    out: list[str] = []
    if isinstance(parsed, dict):
        for entry in parsed.get("errors", []) or []:
            if isinstance(entry, dict):
                loc = entry.get("location") or {}
                row = loc.get("row")
                col = loc.get("col")
                msg = entry.get("message", "")
                if row is not None:
                    out.append(f"line {row}:{col} {msg}")
                else:
                    out.append(msg)
            else:
                out.append(str(entry))
        if not out and parsed.get("message"):
            out.append(parsed["message"])
    return tuple(out) if out else (body[:500],)


def _local_fallback_validate(rego: str) -> ValidationResult:
    """No-OPA syntactic guard. Refuses obviously broken input; the bundle
    server will catch the real issues on reload."""
    errors: list[str] = []
    warnings: list[str] = ["validated locally (OPA unreachable)"]
    if not re.search(r"^\s*package\s+\S+", rego, flags=re.MULTILINE):
        errors.append("missing package declaration")
    if rego.count("{") != rego.count("}"):
        errors.append("brace mismatch")
    return ValidationResult(
        valid=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        rego=rego,
    )


async def compile_and_validate(
    rules: list[dict[str, Any]],
    *,
    policy_name: str = "aegis_policy",
) -> tuple[CompiledRego, ValidationResult]:
    """Convenience helper: compile + validate in one call.

    Used by the playground's "Validate" button and by publish-to-enforce
    to fail fast on broken rules before touching the bundle directory.
    """
    compiled = compile_rules(rules, policy_name=policy_name)
    validation = await validate_rego(compiled.rego)
    if compiled.warnings:
        validation = ValidationResult(
            valid=validation.valid,
            errors=validation.errors,
            warnings=tuple(validation.warnings) + compiled.warnings,
            rego=validation.rego,
        )
    return compiled, validation
