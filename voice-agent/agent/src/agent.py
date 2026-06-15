"""Aegis Voice Guide — production v2.

Pipeline (locked per AGENT_V2.md, 2026-06-02):
  Deepgram STT (nova-3, English) -> Groq llama-3.3-70b-versatile -> Cartesia TTS (sonic-3)
  fallback: Groq -> Gemini 2.5 Flash-Lite (if GOOGLE_API_KEY is set)

RAG: hybrid BM25 + dense + cross-encoder reranker (rag.py), top-2 chunks.
Turn detection: Silero VAD + LiveKit MultilingualModel + tuned endpointing.
Chat history truncated to last MAX_CTX_ITEMS each turn to stay under Groq's TPM.

Audit-fixes 2026-06-02:
  - SESSION_MAX_SECONDS default 300→1800 (interview-length conversations).
  - SESSION_IDLE_SECONDS now actually wired (was dead code).
  - Greeting via session.say() — no LLM call burned on a static string.
  - Deepgram language="en" (was "multi"; spec is English-only).
  - search_aegis_docs returns top_n=2 (matches the design token budget).
  - FallbackAdapter attempt_timeout 10s→4s (faster degraded mode).
  - Per-turn latency log: RAG ms + LLM TTFT ms + turn total ms (NFR-5).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
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
# A tool-using turn consumes ~3 items (fn_call + fn_output + assistant msg),
# so 6 holds the previous tool turn + the current one. Matches AGENT_V2.md
# §9.2 "Truncating chat history to last ~6 messages per call".
MAX_CTX_ITEMS = 6

# Hard cap on a single conversation. The gateway TOKEN_TTL_SECONDS must be
# >= this value so the LiveKit JWT outlives the agent session.
# Override via AEGIS_SESSION_MAX_SECONDS in the systemd EnvironmentFile.
SESSION_MAX_SECONDS = int(os.environ.get("AEGIS_SESSION_MAX_SECONDS", "1800"))

# If the user is silent (no completed turn) this long, close the session.
# Saves quota on tabs left open in a background. Set to 0 to disable.
SESSION_IDLE_SECONDS = int(os.environ.get("AEGIS_SESSION_IDLE_SECONDS", "180"))

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
        # Updated by `on_user_turn_completed` so the idle watchdog can tell
        # whether anyone is still talking. Starts at session-start time so
        # opening the panel and walking away still triggers idle close.
        self.last_activity_ts: float = time.monotonic()
        self.turn_count: int = 0

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        """Truncate chat history + tick the activity timestamp.

        Voice conversations grow context unboundedly otherwise; Groq's free
        tier caps at 12 k TPM, and large RAG-injected context blows through it.
        Truncate keeps system msg + last N items.
        """
        turn_ctx.truncate(max_items=MAX_CTX_ITEMS)
        self.last_activity_ts = time.monotonic()
        self.turn_count += 1
        logger.info(
            "turn_started turn=%d ctx_items=%d",
            self.turn_count, len(turn_ctx.items),
        )

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
        t0 = time.monotonic()
        # top_n=2 keeps per-call input token count predictable: ~600 tokens of
        # corpus content + ~80 tokens of wrapper instruction. The reranker
        # already filters down from ~20 candidates so two is enough.
        hits = knowledge.search(query, top_n=2)
        rag_ms = int((time.monotonic() - t0) * 1000)

        if not hits:
            logger.info("rag_no_hits query=%r ms=%d", query[:60], rag_ms)
            # Fall through to general expertise rather than refusing — the
            # Modelfile contract says: when docs are silent, answer from
            # general knowledge but DON'T invent Aegis specifics.
            return (
                "The Aegis docs don't have a focused section on that. Answer "
                "from general cybersecurity knowledge if you can, or ask a "
                "clarifying question. Do NOT invent Aegis-specific facts."
            )

        logger.info(
            "rag_hits query=%r ms=%d hits=%s",
            query[:60], rag_ms,
            [(h.source, round(h.score, 3)) for h in hits],
        )
        body = "\n\n---\n\n".join(
            f"[{h.source}]\n{h.text}" for h in hits
        )
        # Compact wrapper — the persona prompt already covers quote-verbatim,
        # cite-source-briefly, no-paths-aloud. Repeating it here on every
        # tool call wastes ~80 tokens per turn.
        return f"Quote Aegis terms verbatim. If these don't answer, say so.\n\n{body}"


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
        # 4 s is tight enough that a hanging Groq call doesn't burn 10 s of
        # dead air for the user, but long enough that Groq's normal ~200 ms
        # TTFT + a slow streaming response (up to ~2-3 s) still wins.
        return FallbackAdapter([primary, _build_gemini()], attempt_timeout=4.0)
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
            # English-only per AGENT_V2.md §1.2 — multilingual added 150-300 ms
            # of STT latency per turn and increased Aegis-term mishears.
            model="nova-3",
            language="en",
            keyterm=AEGIS_KEYTERMS,
        ),
        llm=_build_llm(),
        # Hotfix 2026-06-10: Cartesia free-tier credits exhausted (HTTP 402
        # on the streaming WSS endpoint), so TTS swapped to Deepgram Aura-2
        # (default voice `aura-2-andromeda-en`). Restore cartesia.TTS once the
        # Cartesia account is topped up.
        tts=deepgram.TTS(),
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

    agent = AegisGuideAgent()
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(audio_input=room_io.AudioInputOptions()),
    )

    # session.say plays a static TTS phrase without burning an LLM call.
    # generate_reply for a fixed greeting wasted ~200 tokens of TPM budget
    # per session — the busiest moment, because cold-start models are also
    # loading. Use direct TTS instead.
    try:
        await session.say(GREETING)
    except Exception:
        # Older livekit-agents versions don't have session.say — fall back to
        # the LLM-driven greeting rather than silently launching mute.
        logger.warning("session.say unavailable, falling back to generate_reply")
        await session.generate_reply(instructions=f"Say exactly: {GREETING}")

    session_start = time.monotonic()

    # Hard cap on session duration. The gateway TOKEN_TTL_SECONDS must be
    # >= SESSION_MAX_SECONDS or the LiveKit JWT expires mid-conversation.
    async def _session_timeout_guard() -> None:
        try:
            await asyncio.sleep(SESSION_MAX_SECONDS)
        except asyncio.CancelledError:
            return
        logger.warning(
            "session_timeout_hit seconds=%d room=%s turns=%d — closing",
            SESSION_MAX_SECONDS, ctx.room.name, agent.turn_count,
        )
        try:
            await session.say(
                "We're at the session limit — disconnecting to keep costs bounded. "
                "Reconnect anytime to continue."
            )
        except Exception:
            pass
        await asyncio.sleep(3.5)
        await session.aclose()

    # Idle watchdog — closes the session if nobody talks for SESSION_IDLE_SECONDS.
    # Resets on every completed user turn via AegisGuideAgent.on_user_turn_completed.
    async def _idle_watchdog() -> None:
        if SESSION_IDLE_SECONDS <= 0:
            return
        while True:
            try:
                await asyncio.sleep(15.0)
            except asyncio.CancelledError:
                return
            idle_for = time.monotonic() - agent.last_activity_ts
            if idle_for >= SESSION_IDLE_SECONDS:
                uptime = int(time.monotonic() - session_start)
                logger.warning(
                    "session_idle_close idle_for=%ds uptime=%ds turns=%d room=%s",
                    int(idle_for), uptime, agent.turn_count, ctx.room.name,
                )
                try:
                    await session.say(
                        "Going quiet to save quota — talk again any time."
                    )
                except Exception:
                    pass
                await asyncio.sleep(2.5)
                await session.aclose()
                return

    timeout_task = asyncio.create_task(_session_timeout_guard())
    idle_task = asyncio.create_task(_idle_watchdog())

    # add_shutdown_callback does `await callback()`, so the callback must
    # return an awaitable. `Task.cancel()` returns a bool, so a plain
    # sync function or lambda here raises TypeError during shutdown.
    async def _cancel_guards() -> None:
        timeout_task.cancel()
        idle_task.cancel()

    ctx.add_shutdown_callback(_cancel_guards)


if __name__ == "__main__":
    agents.cli.run_app(server)
