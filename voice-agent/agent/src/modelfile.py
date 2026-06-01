"""Minimal Ollama-style Modelfile parser.

We use Ollama Modelfile syntax for persona/parameter portability. At runtime
the SYSTEM block is loaded as the system prompt for whichever hosted LLM we
point at (Groq, Gemini, etc.) — same file, different backend.

Supported directives:
    FROM <model>                  (informational; ignored by the parser)
    SYSTEM "..."                  or SYSTEM \"\"\"...\"\"\"
    PARAMETER <name> <value>

Lines beginning with "#" outside a SYSTEM block are comments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Modelfile:
    from_: str = ""
    system: str = ""
    parameters: dict[str, str] = field(default_factory=dict)

    def param_float(self, key: str, default: float | None = None) -> float | None:
        v = self.parameters.get(key)
        return float(v) if v is not None else default

    def param_int(self, key: str, default: int | None = None) -> int | None:
        v = self.parameters.get(key)
        return int(v) if v is not None else default

    def param_str(self, key: str, default: str = "") -> str:
        return self.parameters.get(key, default)


_SYSTEM_TRIPLE_RE = re.compile(r'SYSTEM\s+"""(.*?)"""', re.DOTALL)
_SYSTEM_SINGLE_RE = re.compile(r'SYSTEM\s+"((?:[^"\\]|\\.)*)"')
_FROM_RE = re.compile(r"^FROM\s+(\S+)", re.MULTILINE)
_PARAM_RE = re.compile(r'^PARAMETER\s+(\w+)\s+("([^"]*)"|(\S+))', re.MULTILINE)


def parse_modelfile(path: str | Path) -> Modelfile:
    raw = Path(path).read_text(encoding="utf-8")

    mf = Modelfile()

    m = _FROM_RE.search(raw)
    if m:
        mf.from_ = m.group(1)

    # SYSTEM block — prefer triple-quoted (multi-line)
    m = _SYSTEM_TRIPLE_RE.search(raw)
    if m:
        mf.system = m.group(1).strip()
    else:
        m = _SYSTEM_SINGLE_RE.search(raw)
        if m:
            mf.system = m.group(1).strip()

    # PARAMETERs (quoted or bare)
    for pm in _PARAM_RE.finditer(raw):
        name = pm.group(1)
        # Group 3 captures quoted; group 4 captures bare token
        value = pm.group(3) if pm.group(3) is not None else pm.group(4)
        mf.parameters[name] = value

    if not mf.system:
        raise ValueError(f"Modelfile {path} has no SYSTEM block.")
    return mf
