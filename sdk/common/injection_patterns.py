"""
Canonical injection pattern definitions — single source of truth.
Imported by injection_classifier.py and inference_proxy.py.
Each entry: (compiled_pattern, name, severity)
severity: "critical" | "high" | "medium"

Total: 17 patterns covering known injection vectors plus real-world jailbreaks.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# CANONICAL INJECTION PATTERN DEFINITIONS
# ---------------------------------------------------------------------------
# Format: (compiled_pattern, name, severity)

INJECTION_PATTERN_DEFS: list[tuple[re.Pattern[str], str, str]] = [
    # --- Original 11 patterns ---
    (
        re.compile(
            r"ignore\s+(?:all\s+)?(?:previous|prior)\s+"
            r"(?:instructions?|prompts?|filters?|rules?|guidelines?)",
            re.IGNORECASE,
        ),
        "ignore_previous_instructions",
        "high",
    ),
    (
        re.compile(
            r"forget\s+(?:all\s+)?(?:previous|prior)\s+(?:instructions?|context)",
            re.IGNORECASE,
        ),
        "forget_previous_context",
        "high",
    ),
    (
        re.compile(r"you\s+are\s+now\s+(?:a|an)\s+", re.IGNORECASE),
        "persona_reassignment",
        "high",
    ),
    (
        re.compile(r"act\s+as\s+(?:if\s+you\s+(?:are|were)|a|an)\s+", re.IGNORECASE),
        "act_as_persona",
        "high",
    ),
    (
        re.compile(r"reveal\s+(?:your\s+)?(?:system\s+)?prompt", re.IGNORECASE),
        "prompt_extraction",
        "high",
    ),
    (
        re.compile(
            r"bypass\s+(?:all\s+)?(?:security|restrictions?|filters?)", re.IGNORECASE
        ),
        "bypass_security",
        "high",
    ),
    (
        re.compile(r"jailbreak", re.IGNORECASE),
        "jailbreak_keyword",
        "high",
    ),
    (
        re.compile(r"DAN\s+mode", re.IGNORECASE),
        "dan_mode",
        "critical",
    ),
    (
        re.compile(
            r"override\s+(?:all\s+)?(?:safety|security|content)\s+"
            r"(?:filters?|guidelines?|rules?)",
            re.IGNORECASE,
        ),
        "override_safety_filters",
        "high",
    ),
    (
        re.compile(
            r"(?:delete|drop|destroy|wipe|truncate|erase)\s+all\s+"
            r"(?:data|records?|rows?|tables?|users?|credentials?)",
            re.IGNORECASE,
        ),
        "mass_destruction",
        "critical",
    ),
    (
        re.compile(
            r"exfiltrat\w*\s+(?:all\s+)?(?:user|admin|system|database|api|private|secret|credential|password)",
            re.IGNORECASE,
        ),
        "data_exfiltration",
        "critical",
    ),
    # --- 6 new real-world jailbreak patterns ---
    (
        re.compile(
            r"(?:<!--.*?-->|<\|.*?\|>|\[INST\]|\[/INST\]|<<SYS>>|<</SYS>>)",
            re.IGNORECASE | re.DOTALL,
        ),
        "token_smuggling",
        "high",
    ),
    (
        re.compile(
            r"(?:decode|base64).*(?:instruction|prompt|command)",
            re.IGNORECASE | re.DOTALL,
        ),
        "base64_instruction",
        "medium",
    ),
    (
        re.compile(
            r"(?:pretend|imagine|roleplay|suppose|hypothetically)\s+"
            r"(?:you\s+(?:are|were|have\s+no)|there\s+are\s+no)",
            re.IGNORECASE,
        ),
        "role_play_escape",
        "medium",
    ),
    (
        re.compile(
            r"(?:respond\s+only\s+in|output\s+only|print\s+only|return\s+only)\s+"
            r"(?:json|xml|code|raw|plain)",
            re.IGNORECASE,
        ),
        "output_format_override",
        "medium",
    ),
    (
        re.compile(
            r"(?:sudo|admin\s+mode|developer\s+mode|god\s+mode|unrestricted\s+mode)",
            re.IGNORECASE,
        ),
        "sudo_mode",
        "high",
    ),
    (
        re.compile(
            r"(?:repeat|print|output|show)\s+(?:your\s+)?(?:training\s+data|system\s+prompt|instructions|guidelines)\s+"
            r"(?:verbatim|exactly|word\s+for\s+word)",
            re.IGNORECASE,
        ),
        "training_data_extract",
        "high",
    ),
]

# ---------------------------------------------------------------------------
# CONVENIENCE EXPORTS
# ---------------------------------------------------------------------------

# Flat list of compiled patterns only — used by inference_proxy.InjectionDetector
INJECTION_PATTERNS: list[re.Pattern[str]] = [p for p, _, _ in INJECTION_PATTERN_DEFS]
