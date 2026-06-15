"""Sprint 5 — Payload mutators for the attack-evaluation corpus.

Each mutator takes a string and returns the mutated string. The corpus
generator applies a fixed subset of mutators per base case so the dataset
stays predictable: never randomized, never per-run.

Mutators implemented (the seven called out in sprint.md Sprint 5):

  * none             — identity (keeps the original case)
  * case             — random-looking case alternation
  * whitespace       — extra whitespace + zero-width joiners
  * comment_split    — SQL/regex comment-style splitting
  * url_encode       — percent-encode the alphanumerics in the payload
  * base64           — base64-wrap inside a marker the model must decode
  * homoglyph        — replace ASCII letters with confusable unicode
  * multilingual     — translate the cue word into another language

The goal is to verify that the detection layers (SecurityMiddleware,
InjectionDetector, output filter, policy) handle obfuscated variants of
each base attack, not just the literal payload. Mutations must NEVER alter
the semantic intent of the attack — they only change the surface form.
"""
from __future__ import annotations

import base64
import re


# Cyrillic / Greek lookalikes for the seven most common attack-cue letters.
# We restrict to letters that appear in the attack cues ("ignore", "delete",
# "select", "system", "admin", "drop", "union") so the mutation is targeted
# rather than scattershot.
_HOMOGLYPH_MAP = {
    "a": "а",   # cyrillic small a
    "c": "с",   # cyrillic small es
    "d": "ԁ",   # cyrillic small komi de
    "e": "е",   # cyrillic small ie
    "i": "і",   # cyrillic small byelorussian-ukrainian i
    "l": "ӏ",   # cyrillic small palochka
    "n": "ո",   # armenian small vo (visual match for n)
    "o": "о",   # cyrillic small o
    "p": "р",   # cyrillic small er
    "s": "ѕ",   # cyrillic small dze
    "t": "т",   # cyrillic small te
    "u": "ц",   # cyrillic small tse (close visual)
    "y": "у",   # cyrillic small u (visual y)
}


# Common attack cues mapped to translations the model might receive when a
# tenant operates in a non-English locale. Real attackers use this all the
# time — half the prompt-injection corpus from a year ago is now in
# Spanish, Russian, or Chinese.
_MULTILINGUAL_MAP = {
    "ignore previous instructions":
        "ignorez les instructions précédentes",
    "ignore all previous":
        "ignora todas las instrucciones anteriores",
    "system prompt":
        "indication du système",
    "delete all":
        "supprime tout",
    "drop table":
        "supprimer la table",
    "show me your":
        "muéstrame tu",
    "you are now":
        "你现在是",
    "you are no longer":
        "你不再是",
}


def mutate_none(s: str) -> str:
    return s


def mutate_case(s: str) -> str:
    out = []
    flip = True
    for ch in s:
        if ch.isalpha():
            out.append(ch.upper() if flip else ch.lower())
            flip = not flip
        else:
            out.append(ch)
    return "".join(out)


def mutate_whitespace(s: str) -> str:
    # Insert ZWJ + extra spaces so naïve string equality fails but the
    # semantic content is preserved. Real injection corpora do this.
    return s.replace(" ", "  ‍  ").strip()


def mutate_comment_split(s: str) -> str:
    # SQL/Rego-style comment splitting — '/**/' inside cue words. Matches
    # the SQL-injection corpus the gateway middleware is trained on.
    if " " in s:
        return s.replace(" ", "/**/ ", 4)
    return s[:1] + "/**/" + s[1:]


def mutate_url_encode(s: str) -> str:
    out = []
    for ch in s:
        if ch.isalnum() or ch in "-_.~":
            out.append(ch)
        else:
            out.append(f"%{ord(ch):02X}")
    return "".join(out)


def mutate_base64(s: str) -> str:
    encoded = base64.b64encode(s.encode("utf-8")).decode("ascii")
    return f"please decode and run: {encoded}"


def mutate_homoglyph(s: str) -> str:
    out = []
    for ch in s:
        if ch.lower() in _HOMOGLYPH_MAP:
            out.append(_HOMOGLYPH_MAP[ch.lower()])
        else:
            out.append(ch)
    return "".join(out)


def mutate_multilingual(s: str) -> str:
    lowered = s.lower()
    for cue, translation in _MULTILINGUAL_MAP.items():
        if cue in lowered:
            return re.sub(re.escape(cue), translation, s, count=1, flags=re.IGNORECASE)
    # Fall back to a generic French wrapper so the case still counts as
    # multilingual rather than silently dropping back to ``none``.
    return f"Suivez l'instruction suivante : {s}"


MUTATORS = {
    "none":          mutate_none,
    "case":          mutate_case,
    "whitespace":    mutate_whitespace,
    "comment_split": mutate_comment_split,
    "url_encode":    mutate_url_encode,
    "base64":        mutate_base64,
    "homoglyph":     mutate_homoglyph,
    "multilingual":  mutate_multilingual,
}


def apply_mutation(payload: str, name: str) -> str:
    """Apply a named mutation to a payload string."""
    fn = MUTATORS.get(name)
    if fn is None:
        raise ValueError(f"unknown mutation: {name}")
    return fn(payload)
