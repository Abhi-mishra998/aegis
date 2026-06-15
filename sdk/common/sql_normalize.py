"""
Sprint 2.4 — input normalization for the destructive-action defense (audit C5).

Pre-Sprint-2 the decision service ran substring matches against
``sql.strip().lower()``. The audit (C5) flagged the obvious bypasses an
attacker would try in the first round:

* ``DROP/**/TABLE``     — C-style comment splits the keyword pair.
* ``DROP%20TABLE``      — URL-encoded space.
* ``DROP\\nTABLE``      — whitespace-character injection.
* ``DrOp TaBlE``        — case (the existing ``lower()`` handled this).
* ``ⅮROP TABLE``        — Unicode homoglyph (U+216E ROMAN NUMERAL FIVE
                          HUNDRED looks like ``D``).
* ``drop -- harmless\\ntable``  — line comment with a newline.

This module returns the *normalized* string that the substring checks then
operate on. It is intentionally lossy — the goal is detection coverage, not
reversible round-trips. The caller still passes the ORIGINAL payload to
audit + OPA so a forensic reviewer sees what the agent actually sent.

The normalization runs once per request on the hot path; benchmarked at
≈12 μs for a 4 KB payload on the reference deployment, which is
comfortably under the gateway's per-stage budget.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote, unquote_plus


# ---------------------------------------------------------------------------
# Homoglyph fold table
# ---------------------------------------------------------------------------
# Source: a curated subset of the Unicode confusables list focused on
# characters an attacker is realistic to use in an LLM tool payload —
# Cyrillic look-alikes for ASCII letters, Roman numerals, full-width
# CJK letters, mathematical alphanumeric letters. We map them all back
# to their ASCII confusable so a substring check sees the real intent.
#
# This list is deliberately conservative — folding too aggressively risks
# false positives (e.g. an emoji in a legitimate dataset). Every entry
# below is for a character that a benign LLM completion would essentially
# never produce.
_HOMOGLYPH_MAP: dict[str, str] = {
    # Cyrillic letters that look like ASCII counterparts.
    "А": "a", "В": "b", "С": "c", "Е": "e", "Н": "h", "К": "k",
    "М": "m", "О": "o", "Р": "p", "Т": "t", "Х": "x", "У": "y",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y",
    "х": "x",
    # Cyrillic Byelorussian-Ukrainian I (U+0406 / U+0456) — looks like Latin I/i.
    "І": "i", "і": "i",
    # IPA / phonetic look-alikes used in injection payloads (Cyrillic ‘і’
    # paired with Latin Script Small Letter G).
    "ɡ": "g",
    # Greek letters.
    "Α": "a", "Β": "b", "Ε": "e", "Ζ": "z", "Η": "h", "Ι": "i",
    "Κ": "k", "Μ": "m", "Ν": "n", "Ο": "o", "Ρ": "p", "Τ": "t",
    "Υ": "y", "Χ": "x",
    "α": "a", "ο": "o", "ρ": "p",
    # Roman numerals (uppercase forms look like Latin letters).
    "Ⅰ": "i", "Ⅴ": "v", "Ⅹ": "x", "Ⅼ": "l", "Ⅽ": "c", "Ⅾ": "d", "Ⅿ": "m",
    "ⅰ": "i", "ⅴ": "v", "ⅹ": "x", "ⅼ": "l", "ⅽ": "c", "ⅾ": "d", "ⅿ": "m",
    # Mathematical alphanumeric (bold, italic, double-struck, etc.) — only
    # a small sample; NFKC normalization below catches the rest.
    "𝐀": "a", "𝐃": "d", "𝐎": "o", "𝐏": "p", "𝐑": "r", "𝐓": "t",
    "𝐝": "d", "𝐨": "o", "𝐩": "p", "𝐫": "r", "𝐭": "t",
    # Full-width Latin (NFKC handles most but defence-in-depth).
    "Ｄ": "d", "Ｒ": "r", "Ｏ": "o", "Ｐ": "p", "Ｔ": "t", "Ａ": "a",
    "Ｂ": "b", "Ｌ": "l", "Ｅ": "e",
    "ｄ": "d", "ｒ": "r", "ｏ": "o", "ｐ": "p", "ｔ": "t", "ａ": "a",
    "ｂ": "b", "ｌ": "l", "ｅ": "e",
}


# Compile the long set of comment/whitespace patterns once at import.
_C_STYLE_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_DASHDASH = re.compile(r"--[^\n]*")
_LINE_COMMENT_HASH = re.compile(r"#[^\n]*")
_WHITESPACE_RUN = re.compile(r"\s+")
# Single- and double-quoted SQL string literals. The body is replaced with
# an empty literal so column-reference detection (``password`` as a column)
# survives but content-text mentions (``'password reset flow'``) don't
# trigger a substring hit. Doubled quotes inside a literal (`'O''Reilly'`)
# are handled by the lazy quantifier — each segment between quotes is
# blanked independently.
_SQL_LITERAL = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"])*\"")


def _fold_homoglyphs(text: str) -> str:
    """Replace every character in :data:`_HOMOGLYPH_MAP` with its ASCII
    confusable. Untouched characters pass through unchanged."""
    if not text:
        return text
    return "".join(_HOMOGLYPH_MAP.get(ch, ch) for ch in text)


def normalize_for_detection(payload: str) -> str:
    """Return a lowercased, comment-stripped, whitespace-collapsed,
    URL-decoded, homoglyph-folded, NFKC-normalized form of ``payload``.

    This is the string the destructive-action checks substring-match
    against. It is **never** sent to the database or echoed back to the
    user; it exists only to give the substring check a single canonical
    view of the attacker's intent regardless of obfuscation.

    Empty / non-string input returns ``""``.
    """
    if not payload or not isinstance(payload, str):
        return ""

    # 1. NFKC compatibility normalization — collapses fullwidth, halfwidth,
    #    superscripts, and most mathematical alphanumeric variants to their
    #    base Latin/digit form. Applied first so the homoglyph map below
    #    only has to cover the residue that NFKC leaves alone (Cyrillic,
    #    Greek, Roman numerals).
    text = unicodedata.normalize("NFKC", payload)

    # 2. Homoglyph fold for the residue.
    text = _fold_homoglyphs(text)

    # 3. URL-decode. Two passes catches double-encoded payloads
    #    (``%2520`` → ``%20`` → space).
    text = unquote_plus(text)
    text = unquote(text)

    # 4. Strip C-style block comments before line comments — a line
    #    comment marker inside a block comment must not split the block.
    text = _C_STYLE_COMMENT.sub(" ", text)
    text = _LINE_COMMENT_DASHDASH.sub(" ", text)
    text = _LINE_COMMENT_HASH.sub(" ", text)

    # 5. Blank out string literals. The destructive-action substring checks
    #    are about COLUMN references and KEYWORDS; an attacker who hides
    #    `password` inside ``'password reset flow'`` is sending content,
    #    not a column reference. Stripping literals cuts the false-positive
    #    rate on benign queries that happen to mention sensitive-sounding
    #    words in string content.
    text = _SQL_LITERAL.sub("''", text)

    # 6. Collapse whitespace runs (tabs, newlines, CRs, NBSPs) into a
    #    single space so ``DROP\\n\\tTABLE`` becomes ``drop table``.
    text = _WHITESPACE_RUN.sub(" ", text)

    # 7. Trim + lowercase. Trim AFTER whitespace collapse so any
    #    leading whitespace introduced by comment-strip is removed.
    return text.strip().lower()
