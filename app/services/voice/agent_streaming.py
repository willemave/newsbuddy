"""Haiku-based streaming agent for in-house voice conversations."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage

from app.core.db import get_db
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.admin_conversational_agent import search_knowledge, search_web
from app.services.langfuse_tracing import langfuse_trace_context
from app.services.llm_models import (
    build_pydantic_model,
    resolve_effective_api_key,
)

logger = get_logger(__name__)

MAX_KNOWLEDGE_HITS = 5
MAX_WEB_HITS = 5
MAX_TOOL_CONTEXT_CHARS = 8_000
SMALL_TALK_PHRASES = {
    "hi",
    "hello",
    "hey",
    "yo",
    "thanks",
    "thank you",
    "how are you",
    "good morning",
    "good afternoon",
    "good evening",
}
KNOWLEDGE_HINTS = (
    "my favorite",
    "my favourites",
    "my favorites",
    "my saved",
    "my bookmarked",
    "what did i save",
    "what i saved",
    "my article",
    "my podcast",
    "i read",
    "i listened",
    "my anthro",
    "favorited",
)
WEB_HINTS = (
    "latest",
    "recent",
    "today",
    "current",
    "news",
    "find",
    "look up",
    "search",
    "who is",
    "what is",
    "what are",
    "how to",
)

VOICE_SYSTEM_PROMPT = """
You are Newsly Voice, a conversational assistant for spoken dialogue.

Core behavior:
- Keep responses natural, concise, and helpful for audio playback.
- Do not include citation brackets, source lists, or markdown unless explicitly requested.
- Prefer tool use for information-seeking requests instead of answering from prior assumptions.
- If the user asks about their saved/favorite content, call SearchKnowledge first.
- For broad, recent, or factual questions outside saved content, call SearchWeb before answering.
- Never claim to have personal memory beyond this session's context and tool results.
- If tools return no relevant data, say so plainly and ask one targeted follow-up question.

Tool-use examples:
- Use SearchKnowledge for requests like:
  - "What was my favorite Anthropic article?"
  - "Summarize the last podcast I saved."
  - "In the article I bookmarked, what did it say about MCP?"
- Use SearchWeb for requests like:
  - "What's the latest on Anthropic funding?"
  - "Give me a recent Rust project to check out."
  - "Who is the current CEO of OpenAI?"
- If a request mixes saved context and fresh facts:
  - Start with SearchKnowledge, then use SearchWeb only if the saved results are missing details.

Style:
- Use short paragraphs and straightforward language.
- Prefer direct answers over long preambles.
- Avoid boilerplate like "As an AI model".
""".strip()


@dataclass
class VoiceAgentDeps:
    """Dependencies injected into each voice agent run."""

    user_id: int


@dataclass
class VoiceAgentResult:
    """Result of one streaming voice agent turn."""

    assistant_text: str
    new_messages: list[ModelMessage]
    model_spec: str


def _truncate_tool_context(text: str) -> str:
    """Limit tool context to a bounded size."""

    if len(text) <= MAX_TOOL_CONTEXT_CHARS:
        return text
    return text[:MAX_TOOL_CONTEXT_CHARS].rstrip() + "\n... [truncated]"


def _truncate_for_trace(text: str | None) -> str:
    """Bound trace text payloads to avoid noisy logs."""

    if not text:
        return ""
    settings = get_settings()
    max_chars = max(120, int(settings.voice_trace_max_chars))
    max_chars = min(max_chars, 4_000)
    trimmed = text.strip()
    if len(trimmed) <= max_chars:
        return trimmed
    return trimmed[:max_chars].rstrip() + "..."


def _format_knowledge_hits_for_tool(hits: list) -> str:
    """Serialize knowledge hits for LLM tool context."""

    if not hits:
        return "No matching favorites were found."

    lines = ["Favorited knowledge matches:"]
    for idx, hit in enumerate(hits, start=1):
        summary = (hit.summary or "").strip()
        transcript = (hit.transcript_excerpt or "").strip()
        lines.append(
            f"{idx}. title={hit.title} | source={hit.source or 'unknown'} "
            f"| url={hit.url} | type={hit.content_type}"
        )
        if summary:
            lines.append(f"   summary: {summary}")
        if transcript:
            lines.append(f"   transcript_excerpt: {transcript}")
    return _truncate_tool_context("\n".join(lines))


def _format_web_hits_for_tool(hits: list) -> str:
    """Serialize web hits for LLM tool context."""

    if not hits:
        return "No web results were found."

    lines = ["Web search matches:"]
    for idx, hit in enumerate(hits, start=1):
        snippet = (hit.snippet or "").strip()
        lines.append(f"{idx}. title={hit.title} | url={hit.url}")
        if snippet:
            lines.append(f"   snippet: {snippet}")
    return _truncate_tool_context("\n".join(lines))


def _normalize_turn_text(user_text: str) -> str:
    """Normalize turn text for routing heuristics."""

    return " ".join(user_text.strip().lower().split())


def _is_small_talk(user_text: str) -> bool:
    """Detect short conversational turns that do not require tool calls."""

    normalized = _normalize_turn_text(user_text)
    if not normalized:
        return True
    if normalized in SMALL_TALK_PHRASES:
        return True
    return len(normalized.split()) <= 3 and normalized in {
        "hi there",
        "hello there",
        "thank you",
    }


def _should_route_to_knowledge(user_text: str) -> bool:
    """Detect turns that should prioritize SearchKnowledge."""

    normalized = _normalize_turn_text(user_text)
    if " my " in f" {normalized} " and any(
        marker in normalized for marker in ("favorite", "saved", "bookmarked", "article", "podcast")
    ):
        return True
    return any(hint in normalized for hint in KNOWLEDGE_HINTS)


def _should_route_to_web(user_text: str) -> bool:
    """Detect turns that should prioritize SearchWeb."""

    normalized = _normalize_turn_text(user_text)
    if _should_route_to_knowledge(normalized):
        return False
    if _is_small_talk(normalized):
        return False
    if any(hint in normalized for hint in WEB_HINTS):
        return True
    return "?" in user_text and normalized.startswith(
        ("what ", "who ", "when ", "where ", "why ", "how ")
    )


def _build_turn_instructions(user_text: str) -> str | None:
    """Build per-turn instructions that bias reliable tool invocation."""

    if _is_small_talk(user_text):
        return None

    if _should_route_to_knowledge(user_text):
        return (
            "For this turn, call SearchKnowledge before answering. "
            "Use a concise query from the user's request. "
            "If SearchKnowledge has no relevant matches, call SearchWeb next. "
            "Examples: 'my favorited anthropic article', "
            "'last podcast I saved leadership parenting', "
            "'what did my saved MCP article say'."
        )

    if _should_route_to_web(user_text):
        return (
            "For this turn, call SearchWeb before answering. "
            "Use a concise web query from the user's request. "
            "If the request is actually about saved or favorited user content, "
            "call SearchKnowledge first. "
            "Examples: 'latest Anthropic funding', "
            "'recent Rust project this week', "
            "'current CEO company name'."
        )

    return (
        "For this turn, if the user is asking for factual or specific information, "
        "call either SearchKnowledge or SearchWeb before answering. "
        "Prefer SearchKnowledge for user-saved context. "
        "Examples: use SearchKnowledge for 'my saved/favorited...' and SearchWeb for "
        "'latest/current/recent...'."
    )


def _build_agent_cache_key(model_spec: str, api_key_override: str | None) -> tuple[str, str]:
    """Build a stable cache key without keeping raw provider secrets."""
    if not api_key_override:
        return model_spec, ""
    return model_spec, hashlib.sha256(api_key_override.encode("utf-8")).hexdigest()


_VOICE_AGENTS: dict[tuple[str, str], Agent[VoiceAgentDeps, str]] = {}


def get_voice_agent(
    model_spec: str,
    api_key_override: str | None = None,
) -> Agent[VoiceAgentDeps, str]:
    """Build or fetch a cached voice agent for a specific model."""
    cache_key = _build_agent_cache_key(model_spec, api_key_override)
    if cache_key in _VOICE_AGENTS:
        return _VOICE_AGENTS[cache_key]
    model, model_settings = build_pydantic_model(
        model_spec,
        api_key_override=api_key_override,
    )
    agent: Agent[VoiceAgentDeps, str] = Agent(
        model,
        deps_type=VoiceAgentDeps,
        output_type=str,
        system_prompt=VOICE_SYSTEM_PROMPT,
        model_settings=model_settings,
    )

    @agent.tool(name="SearchKnowledge")
    def search_knowledge_tool(
        ctx: RunContext[VoiceAgentDeps],
        query: str,
        limit: int = MAX_KNOWLEDGE_HITS,
    ) -> str:
        """Search favorited user content by text match.

        Args:
            query: Query string to search in favorited content.
            limit: Maximum results to return.

        Returns:
            Tool context string with favorited result snippets.

        Examples:
            User says: "What did my saved Anthropic article say about MCP?"
            Tool call: SearchKnowledge(query="anthropic mcp", limit=5)

            User says: "Summarize the last podcast I favorited."
            Tool call: SearchKnowledge(query="last favorited podcast summary", limit=5)

            User says: "Which leadership article did I bookmark?"
            Tool call: SearchKnowledge(query="leadership bookmarked article", limit=5)
        """

        with get_db() as db:
            hits = search_knowledge(
                db=db,
                user_id=ctx.deps.user_id,
                query=query,
                limit=max(1, min(limit, 10)),
            )

        logger.info(
            "Voice tool SearchKnowledge completed",
            extra={
                "component": "voice_agent",
                "operation": "search_knowledge",
                "item_id": ctx.deps.user_id,
                "context_data": {"query": query[:160], "results": len(hits)},
            },
        )
        return _format_knowledge_hits_for_tool(hits)

    @agent.tool(name="SearchWeb")
    def search_web_tool(
        _ctx: RunContext[VoiceAgentDeps],
        query: str,
        limit: int = MAX_WEB_HITS,
    ) -> str:
        """Search the public web for additional context.

        Args:
            query: Web search query.
            limit: Maximum number of web results.

        Returns:
            Tool context string with web result snippets.

        Examples:
            User says: "What's the latest on Anthropic?"
            Tool call: SearchWeb(query="latest Anthropic news", limit=5)

            User says: "Give me a recent Rust project this week."
            Tool call: SearchWeb(query="recent Rust project launched this week", limit=5)

            User says: "Who is the current CEO of Nvidia?"
            Tool call: SearchWeb(query="current CEO Nvidia", limit=5)
        """

        hits = search_web(query=query, limit=max(1, min(limit, 10)))
        logger.info(
            "Voice tool SearchWeb completed",
            extra={
                "component": "voice_agent",
                "operation": "search_web",
                "context_data": {"query": query[:160], "results": len(hits)},
            },
        )
        return _format_web_hits_for_tool(hits)

    _VOICE_AGENTS[cache_key] = agent
    return agent


async def stream_voice_agent_turn(
    *,
    user_id: int,
    user_text: str,
    message_history: list[ModelMessage],
    on_text_delta: Callable[[str], Awaitable[None]],
    content_context: str | None = None,
    launch_mode: str = "general",
    assistant_carryover: str | None = None,
) -> VoiceAgentResult:
    """Run one streaming Haiku turn and emit text deltas.

    Args:
        user_id: Authenticated user identifier.
        user_text: Final user transcript text.
        message_history: Previous model messages for session continuity.
        on_text_delta: Callback for each streamed assistant text chunk.

    Returns:
        Final assistant text plus pydantic-ai message updates.
    """

    settings = get_settings()
    model_spec = settings.voice_haiku_model
    with get_db() as db:
        provider_api_key = resolve_effective_api_key(
            db=db,
            user_id=user_id,
            model_spec=model_spec,
        )
    agent = get_voice_agent(model_spec, api_key_override=provider_api_key)
    deps = VoiceAgentDeps(user_id=user_id)
    text_fragments: list[str] = []
    turn_instructions = _build_turn_instructions(user_text)
    prompt_text = user_text.strip()
    carryover_text = (assistant_carryover or "").strip()
    if carryover_text:
        carryover_instructions = (
            "The previous assistant response was interrupted mid-stream. "
            "Use this partial response as continuity context when answering this turn.\n"
            f"<interrupted_assistant_partial>\n{carryover_text}\n</interrupted_assistant_partial>"
        )
        if turn_instructions:
            turn_instructions = f"{turn_instructions}\n\n{carryover_instructions}"
        else:
            turn_instructions = carryover_instructions

    if content_context:
        contextual_instructions = (
            "Use this content context first for grounding. "
            "If the answer is not in this context, say that clearly and then use tools.\n"
            f"<content_context>\n{content_context}\n</content_context>"
        )
        if turn_instructions:
            turn_instructions = f"{turn_instructions}\n\n{contextual_instructions}"
        else:
            turn_instructions = contextual_instructions

    if launch_mode == "dictate_summary":
        summary_instructions = (
            "The user launched dictate-summary mode. Start with a spoken summary "
            "that is detailed enough for audio playback (roughly 45 to 90 seconds), "
            "then invite follow-up questions."
        )
        if turn_instructions:
            turn_instructions = f"{turn_instructions}\n\n{summary_instructions}"
        else:
            turn_instructions = summary_instructions

    logger.info(
        "Voice turn routing",
        extra={
            "component": "voice_agent",
            "operation": "route_turn",
            "item_id": user_id,
            "context_data": {
                "knowledge_first": _should_route_to_knowledge(user_text),
                "web_first": _should_route_to_web(user_text),
                "has_instructions": bool(turn_instructions),
                "has_content_context": bool(content_context),
                "launch_mode": launch_mode,
                "has_assistant_carryover": bool(carryover_text),
                "assistant_carryover_chars": len(carryover_text),
            },
        },
    )
    if settings.voice_trace_logging:
        logger.info(
            "Voice turn started",
            extra={
                "component": "voice_agent",
                "operation": "turn_start",
                "item_id": user_id,
                "context_data": {
                    "history_messages": len(message_history),
                    "prompt_chars": len(prompt_text),
                    "prompt_preview": _truncate_for_trace(prompt_text),
                    "has_turn_instructions": bool(turn_instructions),
                    "turn_instructions_preview": _truncate_for_trace(turn_instructions),
                },
            },
        )

    try:
        with langfuse_trace_context(
            trace_name="voice.turn",
            user_id=user_id,
            metadata={
                "source": "realtime",
                "model_spec": model_spec,
                "launch_mode": launch_mode,
            },
            tags=["realtime", "voice"],
        ):
            async with agent.run_stream(
                user_prompt=prompt_text,
                message_history=message_history,
                deps=deps,
                instructions=turn_instructions,
            ) as stream_result:
                delta_count = 0
                async for text_delta in stream_result.stream_text(delta=True, debounce_by=0):
                    if not text_delta:
                        continue
                    delta_count += 1
                    text_fragments.append(text_delta)
                    await on_text_delta(text_delta)

                final_text = "".join(text_fragments).strip()
                if settings.voice_trace_logging:
                    logger.info(
                        "Voice turn completed",
                        extra={
                            "component": "voice_agent",
                            "operation": "turn_complete",
                            "item_id": user_id,
                            "context_data": {
                                "delta_count": delta_count,
                                "assistant_chars": len(final_text),
                                "assistant_preview": _truncate_for_trace(final_text),
                                "model_spec": model_spec,
                            },
                        },
                    )
                return VoiceAgentResult(
                    assistant_text=final_text,
                    new_messages=stream_result.new_messages(),
                    model_spec=model_spec,
                )
    except Exception:
        logger.exception(
            "Voice turn failed in model stream",
            extra={
                "component": "voice_agent",
                "operation": "turn_stream",
                "item_id": user_id,
                "context_data": {"model_spec": model_spec},
            },
        )
        raise
