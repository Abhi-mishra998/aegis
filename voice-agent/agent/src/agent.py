"""Aegis Voice Guide — production v2.

Pipeline (locked per AGENT_V2.md, 2026-06-01):
  Deepgram STT (nova-3) -> Groq llama-3.3-70b-versatile -> Cartesia TTS (sonic-3)
  fallback: Groq -> Gemini 2.5 Flash-Lite (if GOOGLE_API_KEY is set)

RAG: hybrid BM25 + dense + cross-encoder reranker (rag.py).
Turn detection: Silero VAD + LiveKit MultilingualModel + tuned endpointing.
Chat history truncated to last 8 messages each turn to stay under Groq's TPM.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from livekit import agents
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ChatContext,
    ChatMessage,
    JobContext,
    function_tool,
    room_io,
)
from livekit.agents.llm import FallbackAdapter
from livekit.plugins import cartesia, deepgram, openai as lk_openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from modelfile import parse_modelfile
from rag import AegisKnowledge

AGENT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(AGENT_DIR / ".env.local")

# Persona + parameters loaded from the Modelfile. Single source of truth.
MODELFILE = parse_modelfile(AGENT_DIR / "persona" / "Modelfile")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("aegis-agent")

knowledge = AegisKnowledge(
    persist_dir=str(AGENT_DIR / "chroma_db"),
    bm25_dir=str(AGENT_DIR / "bm25_index"),
)

# Boosts STT recognition of Aegis-specific vocabulary
# (mitigates "Aegis"->"eggs", "kill switch"->"keel switch" mishearings).
AEGIS_KEYTERMS = [
    "Aegis",
    "kill switch",
    "enforce mode",
    "shadow mode",
    "audit log",
    "behavioral firewall",
    "policy engine",
    "control plane",
    "tamper-evident",
    "runtime governance",
    "transactional outbox",
]

# Persona and greeting now sourced from agent/persona/Modelfile.
INSTRUCTIONS = MODELFILE.system
GREETING = MODELFILE.param_str(
    "system_greeting",
    "Hey, I'm your cybersecurity voice guide. What are you working on?",
)

# Keep only this many items of chat history per LLM call (TPM safety on Groq).
MAX_CTX_ITEMS = 8

# Hard cap on a single conversation. Groq's free RPD is 14,400; a long voice
# session can chew through that. After this many seconds we close the session
# regardless of activity. Configurable via env so demos can be longer.
SESSION_MAX_SECONDS = int(os.environ.get("AEGIS_SESSION_MAX_SECONDS", "300"))

# If the user is silent this long, end the session (saves Groq quota when a
# tab is left open in a background).
SESSION_IDLE_SECONDS = int(os.environ.get("AEGIS_SESSION_IDLE_SECONDS", "120"))

# Groq's llama-3.3-70b sometimes emits the tool call as visible text content
# instead of via the structured tool_calls channel. The Modelfile tells the
# model not to, but defense-in-depth: strip any `<function=...>...</function>`
# or `<function_call>...</function_call>` block from LLM output before it
# reaches TTS. The actual tool call still fires through the structured path.
_FUNCTION_TAG_RE = re.compile(
    r"<function(?:_call)?(?:\s+[^>]*)?>.*?</function(?:_call)?>",
    re.IGNORECASE | re.DOTALL,
)


class AegisGuideAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        """Truncate chat history to a small window before each LLM call.

        Voice conversations grow context unboundedly otherwise; Groq's free
        tier caps at 12 k TPM, and large RAG-injected context blows through it.
        Truncate keeps system msg + last N items.
        """
        turn_ctx.truncate(max_items=MAX_CTX_ITEMS)

    async def tts_node(self, text, model_settings):
        """Filter `<function=...>` tool-call syntax out of text before TTS.

        Groq's llama-3.3-70b occasionally emits the function call as visible
        content rather than via the structured tool_calls channel. The
        Modelfile prompt tells the model not to, but we strip it here as a
        belt-and-braces guard so the user never hears tool-call markup.

        We buffer enough characters to detect a partial `<function...` tag
        spanning chunk boundaries — release safe prefix, hold the rest.
        """

        async def filtered():
            buf = ""
            async for chunk in text:
                buf += chunk
                # Strip any complete <function...>...</function> blocks
                stripped = _FUNCTION_TAG_RE.sub("", buf)
                # If we're holding what might be a partial opening tag, keep
                # holding it. Otherwise release everything we have.
                tail_idx = stripped.rfind("<")
                if tail_idx >= 0 and tail_idx > len(stripped) - 32:
                    # Possibly mid-tag — release everything before the "<"
                    safe = stripped[:tail_idx]
                    buf = stripped[tail_idx:]
                else:
                    safe = stripped
                    buf = ""
                if safe:
                    yield safe
            # Flush remainder
            tail = _FUNCTION_TAG_RE.sub("", buf)
            if tail:
                yield tail

        async for frame in Agent.default.tts_node(self, filtered(), model_settings):
            yield frame

    @function_tool
    async def search_aegis_docs(self, query: str) -> str:
        """Search the Aegis docs (hybrid BM25 + dense + reranker).

        Use ONLY for Aegis-specific questions. Call at most ONCE per topic
        per turn.

        Args:
            query: A short focused natural-language query, e.g.
                "kill switch behavior", "enforce mode rollout".
        """
        hits = knowledge.search(query, top_n=3)
        if not hits:
            return (
                "The Aegis docs don't have a clear section on that. Tell the "
                "user the docs don't cover it and either answer from general "
                "knowledge or ask a clarifying question."
            )
        logger.info(
            "rag hits: %s",
            [(h.source, round(h.score, 3)) for h in hits],
        )
        # Instruct the LLM to quote rather than paraphrase, briefly attributing
        # the source area (e.g. "kill-switch runbook") — never reading paths aloud.
        body = "\n\n---\n\n".join(
            f"[source: {h.source}]\n{h.text}" for h in hits
        )
        return (
            "Use these doc excerpts to answer. Quote specific Aegis terms verbatim "
            "rather than paraphrasing. Briefly mention which doc area it came from "
            "(e.g. 'per the kill-switch runbook'), but do NOT read file paths or "
            "URLs aloud. If the excerpts don't actually answer the user's question, "
            "say so plainly.\n\n"
            f"{body}"
        )


def _build_groq() -> lk_openai.LLM:
    api_key = os.environ["GROQ_API_KEY"]
    return lk_openai.LLM(
        model="llama-3.3-70b-versatile",
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
        temperature=MODELFILE.param_float("temperature", 0.5),
        max_completion_tokens=MODELFILE.param_int("num_predict", 160),
        extra_body={
            "frequency_penalty": MODELFILE.param_float("frequency_penalty", 0.6),
            "presence_penalty": MODELFILE.param_float("presence_penalty", 0.3),
            "top_p": MODELFILE.param_float("top_p", 0.9),
        },
    )


def _build_gemini() -> lk_openai.LLM:
    api_key = os.environ["GOOGLE_API_KEY"]
    return lk_openai.LLM(
        model="gemini-2.5-flash-lite",
        api_key=api_key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        temperature=MODELFILE.param_float("temperature", 0.5),
        max_completion_tokens=MODELFILE.param_int("num_predict", 160),
    )


def _build_llm():
    """Build Groq as primary; wrap with Gemini fallback if GOOGLE_API_KEY is set."""
    if not os.environ.get("GROQ_API_KEY"):
        raise RuntimeError(
            "GROQ_API_KEY is not set. Add it to .env.local — "
            "get one free at https://console.groq.com/keys"
        )
    primary = _build_groq()
    if os.environ.get("GOOGLE_API_KEY"):
        logger.info("LLM: Groq primary + Gemini fallback")
        return FallbackAdapter([primary, _build_gemini()], attempt_timeout=10.0)
    logger.info("LLM: Groq only (no GOOGLE_API_KEY for fallback)")
    return primary


server = AgentServer(
    # t3.medium has only 4 GB RAM. Prewarming 10 idle Job processes (each loading
    # Silero + turn-detector + MiniLM + cross-encoder) exhausts memory. Spawn
    # on-demand instead; the small TTFT hit on first call is fine for a portfolio demo.
    num_idle_processes=0,
    # Models take longer than 10 s to load on a CPU instance.
    initialize_process_timeout=60.0,
    # Don't warn for memory; loading sentence-transformers + cross-encoder is normal.
    job_memory_warn_mb=2000.0,
)


@server.rtc_session(agent_name="aegis-guide")
async def entrypoint(ctx: JobContext) -> None:
    logger.info("connecting room=%s", ctx.room.name)
    await ctx.connect()

    participant = await ctx.wait_for_participant()
    logger.info("participant joined: %s", participant.identity)

    session = AgentSession(
        stt=deepgram.STT(
            model="nova-3",
            language="multi",
            keyterm=AEGIS_KEYTERMS,
        ),
        llm=_build_llm(),
        tts=cartesia.TTS(model="sonic-3"),
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
        min_endpointing_delay=0.4,
        max_endpointing_delay=2.0,
        min_interruption_duration=0.6,
        min_interruption_words=2,
        false_interruption_timeout=1.5,
        allow_interruptions=True,
    )
    ctx.add_shutdown_callback(session.aclose)

    await session.start(
        agent=AegisGuideAgent(),
        room=ctx.room,
        room_options=room_io.RoomOptions(audio_input=room_io.AudioInputOptions()),
    )

    await session.generate_reply(instructions=f"Say exactly: {GREETING}")

    # Hard cap on session duration so a forgotten-tab session can't churn
    # Groq/Deepgram/Cartesia free quota for hours. Cancelled cleanly when
    # the session ends normally via shutdown_callback.
    async def _session_timeout_guard() -> None:
        try:
            await asyncio.sleep(SESSION_MAX_SECONDS)
        except asyncio.CancelledError:
            return
        logger.warning(
            "session_timeout_hit seconds=%d room=%s — closing",
            SESSION_MAX_SECONDS, ctx.room.name,
        )
        try:
            await session.generate_reply(
                instructions="Say only: time's up — disconnecting to keep costs bounded. "
                             "Reconnect anytime to continue."
            )
        except Exception:
            pass
        await asyncio.sleep(3.5)
        await session.aclose()

    timeout_task = asyncio.create_task(_session_timeout_guard())
    ctx.add_shutdown_callback(lambda: timeout_task.cancel())


if __name__ == "__main__":
    agents.cli.run_app(server)
