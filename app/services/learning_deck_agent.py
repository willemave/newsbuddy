"""Agent loop for building Learning Deck artifacts inside a sandbox."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from pydantic_ai import Agent, RunContext
from pydantic_ai.settings import ModelSettings

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.exa_client import ExaSearchResult, exa_search
from app.services.learning_deck_sandbox import (
    LearningDeckSandboxSession,
    create_learning_deck_sandbox_session,
    guess_asset_content_type,
)
from app.services.learning_deck_theme import DECK_DESIGN_GUIDE
from app.services.llm_models import build_pydantic_model, resolve_model_provider
from app.services.vendor_usage import record_model_usage

logger = get_logger(__name__)

OUTPUT_INDEX_HTML = "output/index.html"
OUTPUT_SOURCE_NOTES = "output/source-notes.md"
OUTPUT_SOURCE_METADATA = "output/source-metadata.json"
OUTPUT_ASSET_DIR = "output/assets"
INPUT_DESIGN_BRIEF = "input/deck-design-brief.md"


@dataclass(frozen=True)
class LearningDeckAgentResult:
    """Generated artifact payload from the learning agent."""

    index_html: str
    source_notes_md: str
    assets: dict[str, tuple[bytes, str]]
    model_provider: str
    model_name: str
    sandbox_provider: str
    sandbox_id: str | None
    source_metadata_updates: dict[str, Any]
    agent_log_events: list[dict[str, Any]] = field(default_factory=list)


class LearningDeckAgentExecutionError(RuntimeError):
    """Raised when the agent fails after a sandbox has started."""

    def __init__(
        self,
        message: str,
        *,
        agent_log_events: list[dict[str, Any]],
        sandbox_provider: str,
        sandbox_id: str | None,
    ) -> None:
        super().__init__(message)
        self.agent_log_events = agent_log_events
        self.sandbox_provider = sandbox_provider
        self.sandbox_id = sandbox_id


@dataclass
class LearningDeckAgentDeps:
    """Dependencies exposed to the pydantic-ai tool layer."""

    sandbox: LearningDeckSandboxSession
    user_id: int
    run_id: int
    agent_log_events: list[dict[str, Any]]


LearningDeckSandboxFactory = Callable[[int, int], LearningDeckSandboxSession]


LEARNING_DECK_SYSTEM_PROMPT = (
    "You build great learning decks that explain topics thoroughly using Reveal.js.\n\n"
    """Work like a senior technical educator:
- Start from the provided primary source, then research only where it improves teaching depth.
- Explain architecture, construction, tradeoffs, alternatives, and implications.
- Keep slides visually coherent and presentation-ready; avoid dense reference dumps.
- Treat visual design as part of the teaching. Use diagrams, structured layouts, and source-specific
  graphics to explain the material, not decorative filler.

Output contract:
- Write `output/index.html`, a complete Reveal.js deck using CDN Reveal.js assets.
- Write `output/source-notes.md`, with sections for primary source metadata, web sources used,
  important inspected files, source-to-slide mapping, limitations, and GitHub branch/commit when
  applicable.
- Optionally write local assets under `output/assets/` and reference them with relative paths.
- Optionally write `output/source-metadata.json` with resolved source metadata such as
  `default_branch` and `commit_sha`.
- You may use React/JSX, Node scripts, and local JavaScript to build richer diagrams,
  interactions, and repeated slide structures.

Hosting constraints:
- Allowed external scripts are limited to Reveal.js, React, ReactDOM, D3, and Mermaid from the
  existing allowed CDNs. Prefer local scripts under `output/assets/` for deck-specific behavior.
- If using JSX or TypeScript, compile it in the VM before publishing. Do not rely on browser Babel,
  TypeScript transpilers, Vite dev clients, Next app shells, analytics, trackers, or arbitrary
  third-party script loaders in the public deck.
- Do not use inline event-handler attributes such as onclick/onload.
- External images and stylesheets are allowed. Prefer CSS in the deck or local assets.
- Do not include secrets, backend host file paths, or raw command logs in the public artifact.

Visual quality contract:
- Read `input/deck-design-brief.md` before designing the deck and follow it.
- Do not ship a default Reveal.js theme with lightly edited colors. Use the injected Daylight house
  visual system and its classes; keep any deck-authored CSS focused on source-specific diagrams,
  tables, code/file callouts, and citations.
- Include bespoke teaching graphics. For a normal deck, aim for six or more source-specific visuals:
  architecture maps, mechanism diagrams, timelines, repo/file topology, comparison matrices, method
  pipelines, annotated code snippets, or result/claim maps.
- Prefer diagram-first slides over bullet-first slides. Use Reveal sections, fragments, and
  SVG/HTML/React/D3/Mermaid layers to reveal relationships progressively when that improves
  teaching.
- Avoid generic AI presentation tropes: purple-blue gradients, glow blobs, glass cards everywhere,
  emoji, stock-photo filler, fake metrics, generic three-card feature grids, and text-heavy
  bullet dumps.
- Use color with restraint: neutral base, one primary accent, one secondary highlight, and semantic
  status colors only when they teach something.
- Make every slide answer: what should the learner see first, what relationship is being explained,
  and what evidence supports it."""
)


LEARNING_DECK_DESIGN_BRIEF = """# Learning Deck Design Brief

Build the deck like a strong technical conference talk, not a default Reveal export.

## Visual System

- The Daylight house theme is injected at view time. Do not invent a competing palette, font stack,
  centered hero system, or default Reveal theme.
- Use the house classes documented below for cover, split, section, statement, eyebrow, lede, rule,
  and bullet layouts.
- Use the single house emerald accent only for emphasis, diagrams, and status marks.
- Keep border radii restrained. Prefer crisp panes, ruled sections, timelines, and diagrams over
  oversized rounded cards.
- Use the house type system and add only minimal structural CSS when a source-specific diagram needs
  it.

## Required Slide Craft

- Use varied slide formats: title plate, concept map, architecture flow, mechanism diagram,
  comparison table, source-backed claim, code/file walkthrough, implications, and recap.
- Limit bullets. If a slide has more than four bullets, convert it into a diagram, table, sequence,
  or annotated source excerpt.
- Build diagrams as first-class teaching objects, not decorations. A strong deck should have a
  source-specific visual roughly every two or three slides.
- Each major section needs a visual anchor: an SVG/HTML/CSS diagram, a structured table, a callout
  overlay, a file tree, a method pipeline, or an argument map.
- Use real source details in visuals. For GitHub repos, show actual packages, folders, commands,
  data flow, and extension points. For papers/PDFs, show the method pipeline, experimental setup,
  key terms, limitations, and comparison to prior approaches. For articles/podcasts, show actors,
  chronology, claims, evidence, and tradeoffs.
- Prefer hand-built SVG, CSS grid diagrams, and local assets under `output/assets/`. Do not use
  decorative illustrations that do not teach.

## Diagram-First Patterns

- For GitHub repositories: include package/module maps, runtime request or task flows, extension or
  plugin graphs, build/run pipelines, command lifecycles, file ownership maps, and annotated source
  excerpts with the exact files or symbols inspected.
- For papers and PDFs: include method pipelines, equation-to-mechanism diagrams, experiment or
  ablation matrices, comparison-to-prior-work tables, limitation maps, and assumption diagrams.
- For articles and podcasts: include chronology, actor maps, claim/evidence maps, causal chains,
  tradeoff matrices, and implication trees.
- Keep diagrams legible at 16:9 landscape phone size. Use fewer, clearer nodes instead of sprawling
  maps; split complex systems across progressive slides or vertical stacks.
- Use Reveal fragments only to reveal layers of a diagram, equation, source excerpt, or argument.
  Do not animate every bullet by default.

## Rich JavaScript and React Authoring

- Use React, local JavaScript, D3, or Mermaid when they make the deck genuinely better: interactive
  architecture maps, toggled layers, source-code walkthroughs, animated pipelines, graph diagrams,
  or reusable source-specific visual components.
- Acceptable pattern: create local authoring files such as `src/deck.jsx`, `src/diagrams.jsx`, or
  `render.mjs`; run them with Node to produce static sections, compiled client code, inline SVG,
  CSS, or local assets; then write the finished Reveal deck to `output/index.html`.
- Runtime React is allowed when it powers a specific teaching interaction. Use pinned React and
  ReactDOM CDN URLs or bundle React into a local `output/assets/*.js` file. Keep the runtime small,
  deterministic, and presentation-focused.
- Deck-specific JavaScript belongs in local assets such as `output/assets/deck.js`, or in concise
  inline scripts. Wire behavior with `addEventListener`; never use inline event-handler attributes.
- Do not publish browser Babel, TypeScript transpilers, Vite dev clients, Next app shells,
  analytics, trackers, or arbitrary third-party script loaders. Compile JSX/TS in the VM first.
- D3 and Mermaid are allowed for source-specific diagrams. Initialize them after Reveal is ready and
  make sure printed/no-JS fallback content remains understandable where possible.
- Prefer small component vocabularies: `SystemMap`, `FlowLane`, `FileTree`, `ClaimEvidenceMap`,
  `Timeline`, `ComparisonMatrix`, `SourceCallout`, and `LayeredMechanism`.
- If you use React, D3, Mermaid, or local JS, document the runtime libraries and local authoring
  files in `output/source-notes.md`, but do not include raw command logs.

## Anti-Slop Rules

- No generic hero slide with a gradient background and centered huge text.
- No decorative blobs, neon glows, glassmorphism as the default surface, or fake dashboard metrics.
- No emoji or clipart.
- No random stock images. External images are acceptable only when they directly show the source,
  repo, paper figure, product, architecture, or concept being taught.
- No dense reference dumps. Put details in source notes and teach the relationships on slides.
- No unexplained acronyms or jargon. If a term matters, give it a clear definition or diagram.

## Reveal.js Implementation

- Use CDN Reveal.js scripts/styles, then override with your own CSS in `output/index.html` or
  `output/assets/theme.css`.
- Give important `<section>` elements stable, human-readable `id` attributes so
  `output/source-notes.md` can map sources to slides precisely.
- Use horizontal slides for the main story. Use nested vertical slide stacks only when a topic is a
  true drill-down or appendix under one parent idea.
- Use built-in Reveal classes where they help: `r-fit-text` for short high-impact statements,
  `r-stretch` for large diagrams/media/code panes, and `r-stack` for layered visual comparisons.
- Set `scrollActivationWidth: null` in `Reveal.initialize(...)` so phone-width viewers stay in
  slide mode and previous/next controls work.
- Design for a polished 16:9 landscape deck first. Phone portrait may show the same landscape deck
  scaled to fit; only add portrait-specific responsive CSS when it preserves presentation quality.
- Keep slide content within safe bounds on mobile landscape and desktop. Avoid text, diagrams, SVGs,
  tables, or code blocks that spill past the slide.
- Provide visible previous/next affordance through Reveal controls or clear slide navigation
  styling.
- Use speaker-friendly slide titles, but make slides visually scannable without narration.
- Add citations or compact source labels on slides where claims depend on specific sources.
- Use `data-background-*`, auto-animate, and speaker notes sparingly. They should clarify a
  relationship or presentation beat, not compensate for weak slide structure.

Before finishing, inspect `output/index.html` in a browser or with screenshot tooling. Check every
slide for overflow, unreadable contrast, dead images/assets, broken next/previous navigation,
missing citations, and source-notes mappings. Reject your own work if it still looks like a generic
AI-generated deck.
"""

LEARNING_DECK_DESIGN_BRIEF = LEARNING_DECK_DESIGN_BRIEF + "\n\n" + DECK_DESIGN_GUIDE


def run_learning_deck_agent(
    *,
    source_snapshot: dict[str, Any],
    interests_prompt: str | None,
    user_id: int,
    run_id: int,
    sandbox_factory: LearningDeckSandboxFactory | None = None,
) -> LearningDeckAgentResult:
    """Run the coarse agent loop and read the artifact files it produced."""
    settings = get_settings()
    sandbox_factory = sandbox_factory or _create_configured_sandbox
    sandbox = sandbox_factory(user_id, run_id)
    agent_log_events: list[dict[str, Any]] = []
    try:
        _append_agent_log_event(
            agent_log_events,
            "sandbox_started",
            {"provider": sandbox.provider, "sandbox_id": sandbox.sandbox_id},
        )
        _prepare_sandbox_inputs(
            sandbox,
            source_snapshot=source_snapshot,
            interests_prompt=interests_prompt,
        )
        model_spec = settings.learning_deck_model
        provider = resolve_model_provider(model_spec)
        model, base_model_settings = build_pydantic_model(model_spec)
        agent: Agent[LearningDeckAgentDeps, str] = Agent(
            model,
            deps_type=LearningDeckAgentDeps,
            output_type=str,
            system_prompt=LEARNING_DECK_SYSTEM_PROMPT,
        )
        _register_tools(agent)

        try:
            result = agent.run_sync(
                _build_agent_prompt(source_snapshot, interests_prompt),
                deps=LearningDeckAgentDeps(
                    sandbox=sandbox,
                    user_id=user_id,
                    run_id=run_id,
                    agent_log_events=agent_log_events,
                ),
                model_settings=_build_runtime_model_settings(base_model_settings),
            )
        except Exception as exc:
            _append_agent_log_event(
                agent_log_events,
                "agent_failed",
                {"error": str(exc), "failure_class": type(exc).__name__},
            )
            raise LearningDeckAgentExecutionError(
                str(exc),
                agent_log_events=agent_log_events,
                sandbox_provider=sandbox.provider,
                sandbox_id=sandbox.sandbox_id,
            ) from exc
        _append_agent_log_event(
            agent_log_events,
            "agent_completed",
            {"output_chars": len(str(result.output or ""))},
        )
        record_model_usage(
            "learning_deck_generate",
            result,
            model_spec=model_spec,
            persist={
                "provider": provider,
                "feature": "learning_deck_generation",
                "operation": "learning_deck.generate",
                "source": "queue",
                "user_id": user_id,
                "metadata": {"run_id": run_id},
            },
        )
        logger.info(
            "Learning Deck agent completed",
            extra={
                "component": "learning_decks",
                "operation": "agent_run",
                "status": "completed",
                "item_id": run_id,
                "user_id": user_id,
                "context_data": {
                    "model": model_spec,
                    "sandbox_provider": sandbox.provider,
                    "sandbox_id": sandbox.sandbox_id,
                    "agent_output_chars": len(str(result.output or "")),
                },
            },
        )

        return LearningDeckAgentResult(
            index_html=sandbox.read_file(
                OUTPUT_INDEX_HTML,
                max_bytes=settings.learning_deck_max_index_html_bytes,
            ),
            source_notes_md=sandbox.read_file(
                OUTPUT_SOURCE_NOTES,
                max_bytes=settings.learning_deck_max_source_notes_bytes,
            ),
            assets=_collect_assets(sandbox),
            model_provider=provider,
            model_name=model_spec,
            sandbox_provider=sandbox.provider,
            sandbox_id=sandbox.sandbox_id,
            source_metadata_updates=_read_source_metadata_updates(sandbox),
            agent_log_events=agent_log_events,
        )
    finally:
        sandbox.close()


def _register_tools(agent: Agent[LearningDeckAgentDeps, str]) -> None:
    @agent.tool
    def bash(
        ctx: RunContext[LearningDeckAgentDeps],
        command: str,
        timeout_seconds: int | None = None,
    ) -> str:
        """Run a bash command inside the sandbox."""
        result = ctx.deps.sandbox.run_command(command, timeout_seconds=timeout_seconds)
        _append_agent_log_event(
            ctx.deps.agent_log_events,
            "bash",
            {
                "command": command,
                "timeout_seconds": timeout_seconds,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )
        return f"exit_code={result.exit_code}\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"

    @agent.tool
    def write_file(ctx: RunContext[LearningDeckAgentDeps], path: str, text: str) -> str:
        """Write a UTF-8 file below the sandbox workdir."""
        ctx.deps.sandbox.write_file(path, text)
        _append_agent_log_event(
            ctx.deps.agent_log_events,
            "write_file",
            {"path": path, "chars": len(text)},
        )
        return f"Wrote {path}"

    @agent.tool
    def read_file(ctx: RunContext[LearningDeckAgentDeps], path: str) -> str:
        """Read a UTF-8 file below the sandbox workdir."""
        try:
            text = ctx.deps.sandbox.read_file(path, max_bytes=100_000)
        except Exception as exc:
            message = f"File not found or unreadable: {path}"
            _append_agent_log_event(
                ctx.deps.agent_log_events,
                "read_file_failed",
                {"path": path, "error": str(exc), "failure_class": type(exc).__name__},
            )
            return message
        _append_agent_log_event(
            ctx.deps.agent_log_events,
            "read_file",
            {"path": path, "chars": len(text)},
        )
        return text

    @agent.tool
    def list_files(ctx: RunContext[LearningDeckAgentDeps], path: str = ".") -> str:
        """List files below a sandbox workdir path."""
        files = ctx.deps.sandbox.list_files(path)
        _append_agent_log_event(
            ctx.deps.agent_log_events,
            "list_files",
            {"path": path, "files": files},
        )
        return "\n".join(files) if files else "No files found."

    @agent.tool
    def web_search(
        ctx: RunContext[LearningDeckAgentDeps],
        query: str,
        num_results: int = 5,
    ) -> str:
        """Search the web with Newsly's configured Exa client."""
        bounded_results = min(max(int(num_results), 1), 8)
        results = exa_search(
            query,
            num_results=bounded_results,
            max_characters=2500,
            telemetry={
                "feature": "learning_deck_generation",
                "operation": "learning_deck.web_search",
                "source": "queue",
                "user_id": ctx.deps.user_id,
                "metadata": {"run_id": ctx.deps.run_id},
            },
        )
        _append_agent_log_event(
            ctx.deps.agent_log_events,
            "web_search",
            {
                "query": query,
                "num_results": bounded_results,
                "results": [
                    {
                        "title": result.title,
                        "url": result.url,
                        "published_date": result.published_date,
                    }
                    for result in results
                ],
            },
        )
        return _format_search_results(results)


def _create_configured_sandbox(user_id: int, run_id: int) -> LearningDeckSandboxSession:
    return create_learning_deck_sandbox_session(user_id=user_id, run_id=run_id)


def _prepare_sandbox_inputs(
    sandbox: LearningDeckSandboxSession,
    *,
    source_snapshot: dict[str, Any],
    interests_prompt: str | None,
) -> None:
    sandbox.run_command("mkdir -p input output/assets")
    body_text = source_snapshot.get("body_text")
    snapshot_for_json = {key: value for key, value in source_snapshot.items() if key != "body_text"}
    if isinstance(body_text, str) and body_text.strip():
        snapshot_for_json["body_text_file"] = "input/source.txt"
        snapshot_for_json["body_text_chars"] = len(body_text)
        sandbox.write_file("input/source.txt", body_text)
    else:
        sandbox.write_file(
            "input/source.txt",
            "No primary source text was provided. Use input/source-snapshot.json for source "
            "metadata and inspect the source URL with bash or web search when needed.",
        )
    sandbox.write_file(
        "input/source-snapshot.json",
        json.dumps(snapshot_for_json, indent=2, sort_keys=True, default=str),
    )
    sandbox.write_file("input/interests.txt", interests_prompt or "")
    sandbox.write_file(INPUT_DESIGN_BRIEF, LEARNING_DECK_DESIGN_BRIEF)


def _build_agent_prompt(source_snapshot: dict[str, Any], interests_prompt: str | None) -> str:
    source_kind = source_snapshot.get("source_kind")
    source_title = source_snapshot.get("source_title") or source_snapshot.get("source_url")
    interests = interests_prompt.strip() if interests_prompt else "No additional interests given."
    github_guidance = ""
    if source_kind == "github_repo":
        github_guidance = (
            "\nFor this GitHub repository, clone or inspect the public repo, resolve the default "
            "branch and current commit SHA, inspect the architecture with bash/code tools, and "
            "write those details in source notes and output/source-metadata.json."
        )
    return f"""Build the Learning Deck now.

Primary source: {source_title}
Source kind: {source_kind}
Source snapshot file: input/source-snapshot.json
Source text file, when present: input/source.txt
Interests file: input/interests.txt
Design brief: input/deck-design-brief.md
User interests: {interests}
{github_guidance}

Before finishing, verify:
1. output/index.html exists and contains a Reveal.js slide structure.
2. output/source-notes.md exists and has source sections.
3. Source notes map important claims/slides back to sources.
4. output/index.html follows the design brief: Daylight house classes, source-specific graphics,
   varied slide layouts, and no default Reveal/AI-template styling.
5. Any React/JSX, TypeScript, or Node authoring work has been compiled so the hosted deck uses only
   valid browser HTML/CSS/JS, local assets, and allowed presentation CDN scripts.

Return a short completion summary only after the files are written.
"""


def _build_runtime_model_settings(base_model_settings: ModelSettings | None) -> ModelSettings:
    settings = get_settings()
    runtime_settings = dict(base_model_settings or {})
    runtime_settings["timeout"] = settings.learning_sandbox_timeout_seconds
    return cast(ModelSettings, runtime_settings)


def _collect_assets(sandbox: LearningDeckSandboxSession) -> dict[str, tuple[bytes, str]]:
    settings = get_settings()
    files = sandbox.list_files(OUTPUT_ASSET_DIR)
    assets: dict[str, tuple[bytes, str]] = {}
    for path in files[: settings.learning_deck_max_asset_count]:
        normalized = path.strip().lstrip("./")
        if not normalized.startswith(f"{OUTPUT_ASSET_DIR}/"):
            continue
        relative_path = normalized.removeprefix("output/")
        assets[relative_path] = (
            sandbox.read_file_bytes(normalized, max_bytes=settings.learning_deck_max_asset_bytes),
            guess_asset_content_type(relative_path),
        )
    return assets


def _read_source_metadata_updates(sandbox: LearningDeckSandboxSession) -> dict[str, Any]:
    try:
        raw_json = sandbox.read_file(OUTPUT_SOURCE_METADATA, max_bytes=100_000)
    except Exception:
        return {}
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _format_search_results(results: list[ExaSearchResult]) -> str:
    if not results:
        return "No web search results available."
    lines: list[str] = []
    for index, result in enumerate(results, start=1):
        lines.append(f"[{index}] {result.title}")
        lines.append(f"URL: {result.url}")
        if result.published_date:
            lines.append(f"Published: {result.published_date}")
        if result.snippet:
            lines.append(f"Snippet: {result.snippet}")
        lines.append("")
    return "\n".join(lines).strip()


def _append_agent_log_event(
    events: list[dict[str, Any]],
    event_type: str,
    payload: dict[str, Any],
) -> None:
    events.append(
        {
            "created_at": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "payload": payload,
        }
    )
