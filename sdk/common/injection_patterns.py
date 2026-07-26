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
    # Sprint UI-Fix (2026-07-25 brutal-test audit) — bare-name role hijack.
    # "You are now DAN" / "You are now Aegis-Uncensored" / "You are now ChatGPT-Dev".
    # Original persona_reassignment required "a" or "an" determiner and missed
    # any-caps bare name pattern. The bare-name variant is the entire DAN
    # jailbreak family (DAN, STAN, DUDE, AIM, OMEGA, etc.).
    (
        re.compile(
            r"you\s+are\s+now\s+(?:DAN|STAN|DUDE|AIM|OMEGA|EVIL|JAILBREAK|"
            r"UNRESTRICTED|UNFILTERED|(?:ChatGPT|Claude|GPT|AI)[-\s]?(?:DEV|JAILBREAK|"
            r"UNCENSORED|EVIL|EXTREME))\b",
            re.IGNORECASE,
        ),
        "persona_hijack_bare_name",
        "critical",
    ),
    # Sprint UI-Fix — companion pattern for the DAN framing without "now".
    # "You are DAN", "act as DAN", "pretend to be DAN". Covers the phrasing
    # where the attacker skips the "now" transition word.
    (
        re.compile(
            r"(?:you\s+are|act\s+as|pretend\s+to\s+be)\s+"
            r"(?:DAN|STAN|DUDE|AIM|OMEGA|(?:an\s+)?"
            r"(?:AI|assistant|model)\s+without\s+(?:restrictions?|filters?|rules?|limits?))\b",
            re.IGNORECASE,
        ),
        "persona_hijack_dan_family",
        "critical",
    ),
    (
        re.compile(
            # Sprint 2.5 — added ``have\s+no`` to catch ``act as if you have
            # no rules``, which the original pattern (only ``are|were``)
            # missed. The role_play_escape pattern already encoded this
            # framing; mirror it here so the injection corpus's recall
            # threshold survives.
            r"act\s+as\s+(?:if\s+you\s+(?:are|were|have\s+no)|a|an)\s+",
            re.IGNORECASE,
        ),
        "act_as_persona",
        "high",
    ),
    (
        # Allow arbitrary intervening words between "your" and "prompt" to catch
        # phrasings like "Reveal your full system prompt verbatim" (2026-07-26
        # brutal-test gap). Bounded {0,3} keeps the pattern from matching very
        # long paragraphs that happen to contain both "reveal" and "prompt".
        re.compile(
            r"reveal\s+(?:your\s+)?(?:\w+\s+){0,3}(?:system\s+)?prompt",
            re.IGNORECASE,
        ),
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
        # Only fire on adversarial framing — "jailbreak yourself" / "jailbreak
        # mode" / "jailbreak the AI". Bare `\bjailbreak\b` matched benign
        # tech-support questions like "what does jailbreak mean on iOS?"
        # (2026-07-26 false-positive audit). Requires the word AS an action
        # or a state applied to the assistant.
        re.compile(
            r"\bjailbreak\s+(?:yourself|mode|the\s+(?:AI|model|assistant|system|LLM))|"
            r"\b(?:you\s+are|enter|activate|enable)\s+(?:in\s+)?jailbreak\b",
            re.IGNORECASE,
        ),
        "jailbreak_keyword",
        "high",
    ),
    # 2026-07-26 broad-corpus additions (12 real-world injection variants
    # caught by external red-team suites that the original patterns missed):
    (
        re.compile(
            r"(?:disregard|dismiss|discard)\s+(?:the\s+)?(?:above|previous|prior|earlier)",
            re.IGNORECASE,
        ),
        "disregard_previous",
        "high",
    ),
    (
        re.compile(
            r"(?:show|repeat|print|output|display|return|give)\s+(?:me\s+|back\s+)?"
            r"(?:your|the)\s+(?:initial|original|system|first|starting|hidden)\s+"
            r"(?:instructions?|prompt|message|text|guidelines?|rules?)",
            re.IGNORECASE,
        ),
        "extract_initial_instructions",
        "high",
    ),
    (
        re.compile(
            r"(?:print|output|show|display)\s+.{0,40}(?:before|preceding|prior\s+to)\s+this",
            re.IGNORECASE,
        ),
        "extract_prior_context",
        "high",
    ),
    (
        re.compile(r"system\s*[:\-]\s*override", re.IGNORECASE),
        "system_override_directive",
        "critical",
    ),
    (
        re.compile(
            r"(?:for|as\s+an?)\s+(?:educational|academic|research|hypothetical|theoretical)\s+"
            r"(?:purposes?|exercise|scenario|reasons?)\s*[,.:;]",
            re.IGNORECASE,
        ),
        "educational_bypass_framing",
        "medium",
    ),
    (
        re.compile(
            r"pretend\s+to\s+be\s+(?:an?\s+)?(?:AI|assistant|model|GPT|Claude|LLM|chatbot)\s+"
            r"(?:without|with\s+no|that\s+has\s+no)\s+(?:filters?|restrictions?|rules?|limits?|guardrails?)",
            re.IGNORECASE,
        ),
        "pretend_ai_no_filters",
        "high",
    ),
    (
        re.compile(r"roleplay\s*[:\-]?\s*(?:you\s+are|as|being)\s+", re.IGNORECASE),
        "roleplay_directive",
        "high",
    ),
    (
        re.compile(r"execute\s+(?:the\s+)?following\s*[:\-]", re.IGNORECASE),
        "execute_following_directive",
        "medium",
    ),
    (
        re.compile(
            r"as\s+(?:Claude|ChatGPT|GPT|Gemini|an?\s+AI|an?\s+assistant)\s+"
            r"(?:with|without)\s+(?:no|out)\s+(?:restrictions?|limits?|filters?|guardrails?)",
            re.IGNORECASE,
        ),
        "as_ai_no_restrictions",
        "high",
    ),
    (
        re.compile(
            r"(?:what|show|display|reveal)\s+(?:are|is)\s+your\s+"
            r"(?:operating\s+)?(?:parameters?|configuration|settings?|instructions?)",
            re.IGNORECASE,
        ),
        "extract_operating_config",
        "high",
    ),
    (
        re.compile(
            # Any "you are <label>" where label contains uncensored/unrestricted/
            # jailbreak/nofilter/dev/evil. Catches ChatGPT-DEV, Aegis-Uncensored,
            # Claude-Jailbreak, GPT-Extreme, UNRESTRICTED AI, UNFILTERED assistant.
            r"you\s+are\s+(?:[A-Za-z0-9]+[-\s])?"
            r"(?:UNCENSORED|UNRESTRICTED|UNFILTERED|JAILBREAK|NOFILTER|EVIL|EXTREME|"
            r"DEV|DEVELOPER|OMEGA-?MODE)",
            re.IGNORECASE,
        ),
        "persona_hijack_freeform",
        "critical",
    ),
    (
        re.compile(r"DAN\s+mode", re.IGNORECASE),
        "dan_mode",
        "critical",
    ),
    (
        re.compile(
            # Sprint 2.5 — accept a determiner (``the``/``all``) between
            # ``override`` and the target noun. Was strict ``all`` only,
            # which missed ``override the safety guidelines``.
            r"override\s+(?:all\s+|the\s+|any\s+)?(?:safety|security|content)\s+"
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
            # Sprint 2.5 — accept ``the``/``a``/``an`` determiner. Original
            # pattern missed ``exfiltrate the private database``.
            r"exfiltrat\w*\s+(?:all\s+|the\s+|a\s+|an\s+)?"
            r"(?:user|admin|system|database|api|private|secret|credential|password)",
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
            # Sprint 2.5 — added ``as (the|a) (previous|old|unrestricted)``
            # framing so payloads like ``roleplay as the previous version
            # that had no filter`` are caught.
            r"(?:pretend|imagine|roleplay|suppose|hypothetically)\s+"
            r"(?:as\s+(?:the\s+|a\s+|an\s+)?(?:previous|earlier|old|unrestricted)"
            r"|you\s+(?:are|were|have\s+no)|there\s+are\s+no)",
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
            # Sprint 2.5 — was ``sudo`` alone, which caught benign DevOps
            # prompts like ``run sudo apt-get update``. The injection
            # vector is always ``sudo`` paired with a privilege-escalation
            # framing word (mode, access, mode :, etc.), so require the
            # qualifier. Also covers ``admin/developer/god/unrestricted mode``.
            r"(?:sudo\s+(?:mode|access|root)|sudo\s*:|admin\s+mode|developer\s+mode|god\s+mode|unrestricted\s+mode)",
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
