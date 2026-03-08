"""Generate concat-friendly codebase reference docs under docs/codebase."""

# ruff: noqa: E501

from __future__ import annotations

import ast
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_ROOT = REPO_ROOT / "docs" / "codebase"


@dataclass(frozen=True)
class FolderDocSpec:
    """Specification for one generated folder reference doc."""

    title: str
    source_rel: str
    output_rel: str
    description: str
    behaviors: tuple[str, ...]
    suffixes: tuple[str, ...]
    recursive: bool = False
    include_directories: tuple[str, ...] = ()
    scope_notes: tuple[str, ...] = ()


SWIFT_TYPE_PATTERN = re.compile(
    r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:final\s+)?(?:indirect\s+)?"
    r"(class|struct|enum|protocol|actor)\s+(\w+)",
    re.MULTILINE,
)
SWIFT_FUNC_PATTERN = re.compile(
    r"^\s*(?:@[\w()., ]+\s+)*(?:static\s+)?(?:func)\s+(\w+)\s*\(",
    re.MULTILINE,
)


APP_DOCS: list[FolderDocSpec] = [
    FolderDocSpec(
        title="app/",
        source_rel="app",
        output_rel="app/10-root.md",
        description=(
            "Application root wiring for the FastAPI server, shared constants, and the "
            "Jinja environment bridge used by admin pages."
        ),
        behaviors=(
            "Bootstraps FastAPI with lifespan-based startup, request logging, validation handlers, static mounts, and router registration.",
            "Keeps runtime-wide constants such as worker ID generation and shared path helpers close to the app entrypoint.",
            "Binds the repo-level `templates/` directory into a reusable Jinja environment via `app/templates.py`.",
        ),
        suffixes=(".py",),
        scope_notes=(
            "This doc covers direct files in `app/`. Subpackages are documented separately.",
            "The empty `app/templates/` directory is not part of the active Jinja rendering path; admin templates live in the repo-level `templates/` directory.",
        ),
    ),
    FolderDocSpec(
        title="app/core/",
        source_rel="app/core",
        output_rel="app/20-core.md",
        description=(
            "Core runtime infrastructure: environment settings, database/session lifecycle, "
            "security primitives, FastAPI dependencies, and shared logging/timing helpers."
        ),
        behaviors=(
            "Centralizes environment-backed settings in one Pydantic settings model consumed by routers, workers, and services.",
            "Owns engine/session creation and the dependency functions that inject read-write or read-only SQLAlchemy sessions into FastAPI endpoints.",
            "Implements JWT issuance/verification plus the auth/admin dependency helpers used across the API and admin views.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/domain/",
        source_rel="app/domain",
        output_rel="app/30-domain.md",
        description=(
            "Thin domain translation layer between SQLAlchemy ORM rows and the normalized "
            "`ContentData` model used by presenters and pipeline code."
        ),
        behaviors=(
            "Normalizes ORM data into a stable domain object so downstream code does not need to know SQLAlchemy column details.",
            "Concentrates conversion logic for list/detail views, worker processing, and metadata-driven rendering in one place.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/http_client/",
        source_rel="app/http_client",
        output_rel="app/40-http-client.md",
        description=(
            "Resilient low-level HTTP access used by scrapers and URL processors when they "
            "need retries, headers, and failure classification outside of higher-level services."
        ),
        behaviors=(
            "Provides the `RobustHttpClient` abstraction for guarded GET/HEAD access with retry behavior and structured logging.",
            "Acts as the network primitive beneath processing strategies and scraping flows that need deterministic fetch behavior.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/models/",
        source_rel="app/models",
        output_rel="app/50-models.md",
        description=(
            "Shared data model layer containing SQLAlchemy ORM tables, Pydantic request/response "
            "contracts, metadata payloads, enums, pagination types, and scraper/discovery DTOs."
        ),
        behaviors=(
            "Defines the database schema for content, tasks, chat, discovery, onboarding, favorites, read-state, and user integrations.",
            "Holds the typed metadata and summary contracts that workers persist into JSON columns and that presenters/routers validate on read.",
            "Provides queue/task enums and shared Pydantic models used across services, handlers, and API endpoints.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/pipeline/",
        source_rel="app/pipeline",
        output_rel="app/60-pipeline.md",
        description=(
            "Queue execution runtime: processor loop, task envelopes/results, dispatcher, "
            "checkout coordination, and the main content/podcast worker implementations."
        ),
        behaviors=(
            "Runs the sequential task processor that claims DB-backed tasks, dispatches handlers, applies retries, and records completion/failure state.",
            "Coordinates content checkout and worker context so multiple queue consumers can safely share the same task tables.",
            "Implements the long-form processing workers that fetch source material, select strategies, and hand off to summarization or downstream tasks.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/pipeline/handlers/",
        source_rel="app/pipeline/handlers",
        output_rel="app/61-pipeline-handlers.md",
        description=(
            "Concrete queue task handlers that translate task envelopes into service calls or "
            "worker actions for each supported task type."
        ),
        behaviors=(
            "Keeps task-specific orchestration out of the processor loop by giving each task type its own handler class.",
            "Bridges queue payloads into service and worker calls for content analysis, processing, discovery, onboarding, images, chat, and integrations.",
            "Provides the place where retryability and task-result mapping become explicit per task type.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/pipeline/workflows/",
        source_rel="app/pipeline/workflows",
        output_rel="app/62-pipeline-workflows.md",
        description=(
            "Focused workflow helpers that model multi-step state transitions inside larger "
            "queue handlers, especially URL analysis and content processing."
        ),
        behaviors=(
            "Captures orchestration rules that would otherwise bloat task handlers, including flow protocols and transition models.",
            "Makes the ordering of URL-analysis and processing outcomes explicit and easier to test independently from the processor loop.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/presenters/",
        source_rel="app/presenters",
        output_rel="app/70-presenters.md",
        description=(
            "Presentation shaping layer that turns domain content into list/detail API responses "
            "with image URLs, readiness checks, and feed-subscription affordances."
        ),
        behaviors=(
            "Decides when content is ready to appear in list endpoints and how summary fields should be projected into response DTOs.",
            "Resolves public image/thumbnail URLs and attaches derived metadata that clients need without exposing raw storage details.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/processing_strategies/",
        source_rel="app/processing_strategies",
        output_rel="app/80-processing-strategies.md",
        description=(
            "Ordered URL-specific extraction strategies used by the content worker to turn raw "
            "URLs into normalized article, podcast, PDF, or discussion payloads."
        ),
        behaviors=(
            "Encapsulates source-specific logic for Hacker News, arXiv, PubMed, YouTube, PDFs, general HTML pages, and tweet shares.",
            "Uses a registry so worker code can stay generic while specialized strategies decide whether to skip, delegate, or extract content.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/repositories/",
        source_rel="app/repositories",
        output_rel="app/90-repositories.md",
        description=(
            "Query composition helpers for content feeds and visibility rules used by list, "
            "search, stats, and recently-read endpoints."
        ),
        behaviors=(
            "Builds shared feed queries so filters for visibility, read state, and pagination stay consistent across API endpoints.",
            "Concentrates SQL-specific search and full-text query behavior away from routers and presenters.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/routers/",
        source_rel="app/routers",
        output_rel="app/100-routers.md",
        description=(
            "Top-level FastAPI routers for authentication, admin pages, admin diagnostics, and "
            "the compatibility bridge that mounts the API router under legacy imports."
        ),
        behaviors=(
            "Owns Apple sign-in, token refresh, admin login/logout, current-user profile endpoints, and admin HTML pages.",
            "Serves Jinja-based dashboards for operations, admin evaluation, conversational admin tooling, and log/error inspection.",
            "Keeps the root API package decoupled by exposing a thin compatibility re-export in `api_content.py`.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/routers/api/",
        source_rel="app/routers/api",
        output_rel="app/101-routers-api.md",
        description=(
            "User-facing JSON API surface for content, chat, discovery, onboarding, voice, "
            "integrations, stats, submissions, and auxiliary OpenAI/realtime endpoints."
        ),
        behaviors=(
            "Splits the mobile-facing API into narrow route modules so each endpoint group owns its request validation and response shaping.",
            "Coordinates content list/detail actions, chat session lifecycle, discovery suggestions, onboarding state, scraper settings, and live voice sessions.",
            "Defines the Pydantic DTO layer consumed by the iOS app and share extension.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/scraping/",
        source_rel="app/scraping",
        output_rel="app/110-scraping.md",
        description=(
            "Scheduled feed and site scrapers plus the orchestration runner that inserts new "
            "content rows and enqueues downstream processing."
        ),
        behaviors=(
            "Implements scraper classes for Hacker News, Reddit, Substack, Techmeme, podcasts, Atom, Twitter, and YouTube.",
            "Normalizes source metadata, deduplicates content creation, and records scraper/event telemetry as new content is inserted.",
            "Bridges file-backed configs and DB-backed user scraper configs into runnable scraper payloads.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/services/",
        source_rel="app/services",
        output_rel="app/120-services.md",
        description=(
            "Business-logic layer for LLM access, content analysis and submission, chat, "
            "discovery, feeds, images, interactions, onboarding, event logging, and queue primitives."
        ),
        behaviors=(
            "Holds the orchestration-heavy logic that routers and handlers call into, including URL analysis, summarization, chat turns, discovery, and image generation.",
            "Contains adapter services for multiple model providers, telemetry/tracing, prompt construction, metadata merging, and provider usage accounting.",
            "Implements end-user features such as favorites, read state, feed subscription, tweet suggestions, daily digests, and onboarding workflows.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/services/gateways/",
        source_rel="app/services/gateways",
        output_rel="app/121-services-gateways.md",
        description=(
            "Narrow gateway interfaces that isolate HTTP, LLM, and queue dependencies for "
            "higher-level services and workflows."
        ),
        behaviors=(
            "Wraps lower-level infrastructure behind small interfaces so workflows can depend on stable contracts instead of concrete implementations.",
            "Makes queue, network, and model-provider dependencies easier to stub or swap during handler/workflow execution.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/services/voice/",
        source_rel="app/services/voice",
        output_rel="app/122-services-voice.md",
        description=(
            "Live voice subsystem for streaming STT/TTS, session management, chat persistence, "
            "and assistant orchestration across the realtime voice experience."
        ),
        behaviors=(
            "Creates and manages live voice sessions, including intro-state tracking, persistence, and reconnection behavior.",
            "Bridges audio capture/playback, ElevenLabs streaming, narration TTS, and agent orchestration into the websocket-based voice API.",
            "Stores or reconstructs live conversation context so voice turns can continue from content detail or chat session state.",
        ),
        suffixes=(".py",),
    ),
    FolderDocSpec(
        title="app/utils/",
        source_rel="app/utils",
        output_rel="app/130-utils.md",
        description=(
            "Cross-cutting utility functions for URLs, pagination, dates, filesystem paths, "
            "error logging, summary normalization, and image path/URL handling."
        ),
        behaviors=(
            "Keeps low-level helpers out of routers and services while preserving shared conventions around paths, dates, pagination cursors, and summary metadata.",
            "Contains reusable error logging and JSON repair utilities used by multiple service modules.",
        ),
        suffixes=(".py",),
    ),
]


CLIENT_DOCS: list[FolderDocSpec] = [
    FolderDocSpec(
        title="client/newsly/",
        source_rel="client/newsly",
        output_rel="client/10-workspace.md",
        description=(
            "Xcode workspace root and app-level configuration: xcconfig files, secrets templates, "
            "sync helpers, and the top-level package/project layout for the iOS client."
        ),
        behaviors=(
            "Stores environment-specific Xcode configuration and secret templates used by the app target and extension target.",
            "Acts as the root for the app source tree, share extension, tests, scripts, and project metadata.",
        ),
        suffixes=(".swift", ".sh", ".xcconfig", ".template", ".plist", ".entitlements"),
        scope_notes=(
            "Generated build artifacts under `client/newsly/build/` are intentionally excluded from the reference set.",
        ),
    ),
    FolderDocSpec(
        title="client/newsly/newsly/",
        source_rel="client/newsly/newsly",
        output_rel="client/20-app-target-root.md",
        description=(
            "SwiftUI app target root containing the `App` entrypoint, primary tab container, "
            "Info.plist metadata, and target entitlements."
        ),
        behaviors=(
            "Bootstraps authentication-driven root presentation and injects shared state into the authenticated SwiftUI shell.",
            "Defines app-wide configuration such as bundle metadata, entitlements, and the root `ContentView` tab/navigation container.",
            "Delegates most feature logic into Models, Services, ViewModels, and Views subfolders documented separately.",
        ),
        suffixes=(".swift", ".plist", ".entitlements"),
    ),
    FolderDocSpec(
        title="client/newsly/newsly/Models/",
        source_rel="client/newsly/newsly/Models",
        output_rel="client/30-models.md",
        description=(
            "Typed client-side models for API payloads, navigation routes, summaries, content "
            "metadata, discovery results, chat, onboarding, and live voice."
        ),
        behaviors=(
            "Mirrors the backend DTO layer so services and view models can decode stable Swift types instead of working with raw dictionaries.",
            "Captures client-only routing and presentation models such as detail routes, read filters, and chat model provider selection.",
        ),
        suffixes=(".swift",),
    ),
    FolderDocSpec(
        title="client/newsly/newsly/Models/Generated/",
        source_rel="client/newsly/newsly/Models/Generated",
        output_rel="client/31-models-generated.md",
        description=(
            "Generated API contract models synchronized from the backend schema for places where "
            "the client wants compile-time alignment with exported OpenAPI contracts."
        ),
        behaviors=(
            "Provides machine-generated request/response types instead of hand-maintained Swift models.",
            "Should be treated as generated output and regenerated from scripts rather than manually edited.",
        ),
        suffixes=(".swift",),
    ),
    FolderDocSpec(
        title="client/newsly/newsly/Repositories/",
        source_rel="client/newsly/newsly/Repositories",
        output_rel="client/40-repositories.md",
        description=(
            "Repository layer that wraps `APIClient` calls for content, read-state, and daily "
            "digest endpoints into higher-level async methods used by view models."
        ),
        behaviors=(
            "Keeps transport details out of view models by exposing feature-shaped repository methods.",
            "Encapsulates content feed pagination, read/unread updates, and daily digest retrieval behind stable interfaces.",
        ),
        suffixes=(".swift",),
    ),
    FolderDocSpec(
        title="client/newsly/newsly/Services/",
        source_rel="client/newsly/newsly/Services",
        output_rel="client/50-services.md",
        description=(
            "App services for authentication, API transport, websocket voice, image caching, "
            "notifications, settings, chat helpers, discovery, and background/shared state."
        ),
        behaviors=(
            "Owns network transport, token refresh, keychain access, and other device/service integrations that should not live inside views.",
            "Implements live voice capture/playback/websocket behavior plus utility services such as unread counts, toast state, and local notifications.",
            "Provides feature-specific helpers for content, discovery, onboarding, chat, tweet sharing, and deep links.",
        ),
        suffixes=(".swift",),
    ),
    FolderDocSpec(
        title="client/newsly/newsly/Shared/",
        source_rel="client/newsly/newsly/Shared",
        output_rel="client/60-shared.md",
        description=(
            "Shared observable state and container helpers reused across tabs, detail flows, "
            "onboarding, and the share extension."
        ),
        behaviors=(
            "Persists or coordinates cross-view state such as reading restoration, chat scroll position, and onboarding progress.",
            "Holds shared app-group/container helpers used to communicate with the extension and shared storage.",
        ),
        suffixes=(".swift",),
    ),
    FolderDocSpec(
        title="client/newsly/newsly/ViewModels/",
        source_rel="client/newsly/newsly/ViewModels",
        output_rel="client/70-view-models.md",
        description=(
            "ObservableObject view models coordinating repositories, services, and navigation "
            "state for list/detail screens, onboarding, discovery, live voice, and chat."
        ),
        behaviors=(
            "Acts as the main presentation-logic layer between decoded models and SwiftUI views.",
            "Owns pagination, filtering, optimistic updates, async loading state, and tab/navigation coordination.",
        ),
        suffixes=(".swift",),
    ),
    FolderDocSpec(
        title="client/newsly/newsly/Views/",
        source_rel="client/newsly/newsly/Views",
        output_rel="client/80-views.md",
        description=(
            "Top-level SwiftUI screens for tabs, feature entrypoints, and major routed surfaces."
        ),
        behaviors=(
            "Defines the primary user-facing screens such as long-form, short-form, knowledge, submissions, search, debug, and authentication flows.",
            "Delegates reusable view pieces into `Views/Components`, `Views/Shared`, and feature-specific subfolders.",
        ),
        suffixes=(".swift",),
    ),
    FolderDocSpec(
        title="client/newsly/newsly/Views/Components/",
        source_rel="client/newsly/newsly/Views/Components",
        output_rel="client/81-views-components.md",
        description=(
            "Reusable SwiftUI building blocks for cards, summaries, markdown rendering, "
            "filters, live voice states, discovery cards, toasts, and media presentation."
        ),
        behaviors=(
            "Holds composable UI pieces shared by multiple screens so detail and list views can stay thin.",
            "Contains summary renderers for interleaved, editorial, bulleted, and structured summary payloads returned by the backend.",
            "Packages complex UI atoms such as swipeable cards, async image wrappers, and live voice visual states.",
        ),
        suffixes=(".swift",),
    ),
    FolderDocSpec(
        title="client/newsly/newsly/Views/Onboarding/",
        source_rel="client/newsly/newsly/Views/Onboarding",
        output_rel="client/82-views-onboarding.md",
        description=(
            "New-user onboarding flow UI including reveal animation, mic interaction, and "
            "tutorial/explanatory surfaces."
        ),
        behaviors=(
            "Guides first-run users through profile capture, audio onboarding, and tutorial transitions before the main tab UI appears.",
            "Uses custom reveal and mic interaction views to make the onboarding path more tactile than standard form sheets.",
        ),
        suffixes=(".swift",),
    ),
    FolderDocSpec(
        title="client/newsly/newsly/Views/Settings/",
        source_rel="client/newsly/newsly/Views/Settings",
        output_rel="client/83-views-settings.md",
        description=(
            "SwiftUI settings screens for account, appearance, integrations, and app-level preferences."
        ),
        behaviors=(
            "Groups account/profile controls, appearance settings, and service toggles into a dedicated settings surface.",
            "Works with `AppSettings`, authentication, and integration services to persist user-facing preferences.",
        ),
        suffixes=(".swift",),
    ),
    FolderDocSpec(
        title="client/newsly/newsly/Views/Shared/",
        source_rel="client/newsly/newsly/Views/Shared",
        output_rel="client/84-views-shared.md",
        description=(
            "Cross-feature presentation primitives and design tokens such as cards, chips, "
            "headers, dividers, search bars, and branded backgrounds."
        ),
        behaviors=(
            "Defines the shared visual language for reusable rows, labels, status chips, and decorative surfaces.",
            "Keeps common styling and structural components out of feature screens so layout and branding stay consistent.",
        ),
        suffixes=(".swift",),
    ),
    FolderDocSpec(
        title="client/newsly/newsly/Views/Sources/",
        source_rel="client/newsly/newsly/Views/Sources",
        output_rel="client/85-views-sources.md",
        description=(
            "Source-management screens for feed and podcast subscriptions plus source-detail presentation."
        ),
        behaviors=(
            "Lets users inspect and manage scraper-backed content sources from within the app.",
            "Works with scraper configuration services/view models to add, inspect, or remove subscribed inputs.",
        ),
        suffixes=(".swift",),
    ),
    FolderDocSpec(
        title="client/newsly/newsly/Views/Library/",
        source_rel="client/newsly/newsly/Views/Library",
        output_rel="client/86-views-library.md",
        description=(
            "Library-oriented SwiftUI surfaces for saved/favorited content."
        ),
        behaviors=(
            "Provides focused views over saved content without overloading the main tab lists.",
        ),
        suffixes=(".swift",),
    ),
    FolderDocSpec(
        title="client/newsly/ShareExtension/",
        source_rel="client/newsly/ShareExtension",
        output_rel="client/90-share-extension.md",
        description=(
            "Share extension target that receives shared URLs from iOS, reads shared auth state, "
            "and forwards submissions into the backend pipeline."
        ),
        behaviors=(
            "Turns iOS share-sheet invocations into authenticated `POST /api/content/submit` requests.",
            "Relies on shared container/keychain state to reuse app authentication and configuration from the main app target.",
            "Includes the storyboard/resource metadata needed for the extension UI lifecycle.",
        ),
        suffixes=(".swift", ".plist", ".entitlements", ".storyboard"),
        recursive=True,
    ),
    FolderDocSpec(
        title="client/newsly/scripts/",
        source_rel="client/newsly/scripts",
        output_rel="client/94-scripts.md",
        description=(
            "Client-specific helper scripts for regenerating derived assets such as API contracts."
        ),
        behaviors=(
            "Keeps one-off maintenance tasks out of the Xcode project while preserving reproducible update steps for generated client artifacts.",
        ),
        suffixes=(".sh",),
    ),
    FolderDocSpec(
        title="client/newsly/newslyTests/",
        source_rel="client/newsly/newslyTests",
        output_rel="client/95-tests.md",
        description=(
            "Focused iOS unit tests covering share routing, onboarding animation progress, and "
            "daily-digest dig-deeper behavior."
        ),
        behaviors=(
            "Provides regression coverage for high-risk client-side behaviors that do not require full UI tests.",
        ),
        suffixes=(".swift",),
    ),
    FolderDocSpec(
        title="client/newsly/newsly.xcodeproj/",
        source_rel="client/newsly/newsly.xcodeproj",
        output_rel="client/96-xcode-project.md",
        description=(
            "Xcode project metadata including schemes, workspace settings, package resolution, "
            "and target membership for the app and share extension."
        ),
        behaviors=(
            "Controls how the app target, extension target, tests, and Swift package dependencies are built and run.",
            "Captures scheme/workspace metadata that the local simulator/debug workflow depends on.",
        ),
        suffixes=(".pbxproj", ".xcscheme", ".xcworkspacedata", ".xcsettings", ".resolved"),
        recursive=True,
    ),
]


def _iter_files(source_dir: Path, spec: FolderDocSpec) -> list[Path]:
    """Return the files that belong in a generated folder doc."""
    matched: list[Path] = []
    if not source_dir.exists():
        return matched

    iterator = source_dir.rglob("*") if spec.recursive else source_dir.iterdir()
    for path in iterator:
        if not path.is_file():
            continue
        if path.name == ".DS_Store":
            continue
        if path.suffix in spec.suffixes or path.name.endswith(spec.suffixes):
            matched.append(path)
    return sorted(matched)


def _first_sentence(text: str | None) -> str | None:
    """Extract the first meaningful sentence from a docstring or comment."""
    if not text:
        return None
    stripped = " ".join(line.strip() for line in text.strip().splitlines() if line.strip())
    if not stripped:
        return None
    return stripped.split(". ", 1)[0].strip()


def _extract_python_summary(path: Path) -> tuple[str | None, list[str], list[str]]:
    """Extract a module summary plus public classes/functions from Python code."""
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    doc_summary = _first_sentence(ast.get_docstring(module))
    classes: list[str] = []
    funcs: list[str] = []

    for node in module.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            classes.append(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            funcs.append(node.name)

    return doc_summary, classes, funcs


def _extract_swift_summary(path: Path) -> tuple[str | None, list[str], list[str]]:
    """Extract a light-weight summary plus declared Swift types/functions."""
    text = path.read_text(encoding="utf-8")
    type_names = [f"{kind} {name}" for kind, name in SWIFT_TYPE_PATTERN.findall(text)]
    func_names = sorted(set(SWIFT_FUNC_PATTERN.findall(text)))

    doc_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("///"):
            doc_lines.append(line.removeprefix("///").strip())
        elif doc_lines:
            break
    summary = _first_sentence(" ".join(doc_lines)) if doc_lines else None
    return summary, type_names, func_names


def _extract_text_summary(path: Path) -> tuple[str | None, list[str], list[str]]:
    """Extract a lightweight summary for non-code files."""
    if path.suffix == ".pbxproj":
        return "Xcode target membership, build settings, and build phases.", [], []
    if path.suffix == ".xcscheme":
        return "Shared Xcode scheme for build, run, and test actions.", [], []
    if path.name == "Package.resolved":
        return "Pinned Swift package dependency versions.", [], []
    if path.suffix == ".xcworkspacedata":
        return "Workspace file references for the Xcode project.", [], []
    if path.suffix == ".xcsettings":
        return "Workspace-level Xcode settings.", [], []

    text = path.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    comment_lines = [
        line.lstrip("#/ ").strip()
        for line in lines
        if (line.startswith("#") and not line.startswith("#!")) or line.startswith("//")
    ]
    summary = _first_sentence(" ".join(comment_lines[:4]))
    return summary, [], []


def _module_note(path: Path, summary: str | None, types: list[str], funcs: list[str]) -> str:
    """Build the module-note column for generated tables."""
    if summary:
        return summary
    parts: list[str] = []
    if types:
        parts.append(f"Types: {', '.join(f'`{value}`' for value in types[:8])}")
        if len(types) > 8:
            parts.append(f"+{len(types) - 8} more")
    if funcs:
        parts.append(f"Functions: {', '.join(f'`{value}`' for value in funcs[:8])}")
        if len(funcs) > 8:
            parts.append(f"+{len(funcs) - 8} more")
    if parts:
        return ". ".join(parts)
    return "Supporting module or configuration file."


def _render_inventory_table(spec: FolderDocSpec) -> str:
    """Render the file/module inventory table for one folder doc."""
    source_dir = REPO_ROOT / spec.source_rel
    rows = ["| File | Key symbols | Notes |", "|---|---|---|"]

    for path in _iter_files(source_dir, spec):
        rel_path = path.relative_to(REPO_ROOT)
        if path.suffix == ".py":
            summary, classes, funcs = _extract_python_summary(path)
            symbols = classes + funcs
        elif path.suffix == ".swift":
            summary, classes, funcs = _extract_swift_summary(path)
            symbols = classes + funcs
        else:
            summary, classes, funcs = _extract_text_summary(path)
            symbols = []

        symbol_text = ", ".join(f"`{symbol}`" for symbol in symbols[:10]) or "n/a"
        if len(symbols) > 10:
            symbol_text = f"{symbol_text}, +{len(symbols) - 10} more"
        rows.append(
            f"| `{rel_path}` | {symbol_text} | {_module_note(path, summary, classes, funcs)} |"
        )

    return "\n".join(rows)


def _render_folder_doc(spec: FolderDocSpec) -> str:
    """Render a folder reference document."""
    source_dir = REPO_ROOT / spec.source_rel
    if spec.recursive:
        file_scope = f"Recursive file inventory for `{spec.source_rel}`."
    else:
        file_scope = f"Direct file inventory for `{spec.source_rel}`."

    behavior_lines = "\n".join(f"- {item}" for item in spec.behaviors)
    notes = "\n".join(f"- {item}" for item in spec.scope_notes)

    parts = [
        f"# {spec.title}",
        "",
        f"Source folder: `{spec.source_rel}`",
        "",
        "## Purpose",
        spec.description,
        "",
        "## Runtime behavior",
        behavior_lines,
        "",
        "## Inventory scope",
        f"- {file_scope}",
    ]
    if spec.scope_notes:
        parts.extend(["- The generated table omits `.DS_Store` and other filesystem noise.", notes])
    parts.extend(
        [
            "",
            "## Modules and files",
            _render_inventory_table(spec),
            "",
        ]
    )
    if not source_dir.exists():
        parts.extend(
            [
                "## Status",
                "The source folder is currently missing; update or remove this doc spec if the layout changes.",
                "",
            ]
        )
    return "\n".join(parts)


def _render_section_overview(
    title: str,
    summary: str,
    bullets: tuple[str, ...],
    docs: list[FolderDocSpec],
) -> str:
    """Render an overview page for a section of docs."""
    doc_rows = ["| Doc | Source folder | Focus |", "|---|---|---|"]
    for spec in docs:
        doc_rows.append(
            f"| `{spec.output_rel.split('/', 1)[1]}` | `{spec.source_rel}` | {spec.description} |"
        )

    commands = [
        f"find docs/codebase/{docs[0].output_rel.split('/', 1)[0]} -type f -name '*.md' | sort | xargs cat"
    ]

    return "\n".join(
        [
            f"# {title}",
            "",
            summary,
            "",
            "## What this section covers",
            *(f"- {item}" for item in bullets),
            "",
            "## Documents",
            *doc_rows,
            "",
            "## Concat command",
            "```bash",
            *commands,
            "```",
            "",
        ]
    )


def _render_root_readme() -> str:
    """Render the top-level codebase docs README."""
    return "\n".join(
        [
            "# Codebase Reference",
            "",
            "Generated folder-by-folder reference for the backend (`app/`), iOS client (`client/`), and runtime configuration (`config/`).",
            "",
            "## Layout",
            "- `app/` documents the FastAPI backend, pipeline, scrapers, and services.",
            "- `client/` documents the SwiftUI app, extension, services, view models, and supporting project files.",
            "- `config/` documents file-backed feed and tooling configuration.",
            "",
            "## Concat commands",
            "```bash",
            "find docs/codebase/app -type f -name '*.md' | sort | xargs cat",
            "find docs/codebase/client -type f -name '*.md' | sort | xargs cat",
            "find docs/codebase/config -type f -name '*.md' | sort | xargs cat",
            "find docs/codebase -type f -name '*.md' | sort | xargs cat",
            "```",
            "",
            "## Regeneration",
            "```bash",
            "uv run python scripts/generate_codebase_docs.py",
            "```",
            "",
        ]
    )


def _render_config_overview() -> str:
    """Render the config-folder reference document."""
    return "\n".join(
        [
            "# config/",
            "",
            "Source folder: `config/`",
            "",
            "## Purpose",
            (
                "File-backed feed and tooling configuration used by scraper bootstrapping, "
                "onboarding defaults, and size-guard tooling."
            ),
            "",
            "## Runtime behavior",
            "- `app/utils/paths.py` resolves this folder by default and allows overrides via `NEWSAPP_CONFIG_DIR` plus per-file env vars.",
            "- `app/scraping/runner.py` actively schedules Hacker News, Reddit, Substack, Techmeme, Podcasts, and Atom scrapers; Twitter and YouTube configs remain available for disabled or ad-hoc flows.",
            "- Example files document expected shape for operators without forcing every deployment to commit secrets or local-only paths.",
            "",
            "## Files",
            "| File | What it controls | Current role |",
            "|---|---|---|",
            "| `config/substack.yml` | Curated Substack feeds (`url`, `name`, `limit`) | Default Substack inputs for onboarding/import flows; runtime subscriptions now primarily live in `user_scraper_configs`. |",
            "| `config/substack.example.yml` | Example Substack feed file | Template only. |",
            "| `config/atom.yml` | Curated Atom feed defaults | Default Atom source list; current checked-in file is placeholder-style sample data. |",
            "| `config/atom.example.yml` | Example Atom feed file | Template only. |",
            "| `config/podcasts.yml` | Podcast RSS inputs with names and per-feed limits | Default podcast feed seeds for onboarding/import flows; live subscriptions now primarily come from DB-backed configs. |",
            "| `config/podcasts.example.yml` | Example podcast feed file | Template only. |",
            "| `config/reddit.yml` | Default subreddit list and per-subreddit limits | Hybrid runtime input: the Reddit scraper can merge or override DB-backed sources with file-backed subreddits. |",
            "| `config/reddit.example.yml` | Example Reddit config | Template only. |",
            "| `config/techmeme.yml` | Techmeme feed URL plus cluster/related-link limits | Active runtime config for the scheduled Techmeme scraper. |",
            "| `config/twitter.yml` | Twitter list IDs, cookies path, limits, lookback window, filters, and optional proxy | Available for the Twitter list scraper, but the scheduled runner currently leaves that scraper disabled. |",
            "| `config/youtube.yml` | YouTube channel list plus yt-dlp cookies, PoToken, throttle, and player-client options | Supports YouTube ingestion and transcript-related flows even though the scheduled YouTube scraper is currently disabled. |",
            "| `config/module_size_guardrails.json` | Per-file size limits | Checked by `scripts/check_module_size_guardrails.py` to keep large modules from growing without an explicit budget. |",
            "",
            "## Notes",
            "- Keep secrets and machine-specific cookie files outside this folder when possible; the checked-in config files are intended to be shareable defaults.",
            "- When file-backed and DB-backed config coexist, prefer documenting which path is authoritative in the matching scraper/service module before changing either source.",
            "",
        ]
    )


def _write(path: Path, content: str) -> None:
    """Write UTF-8 text to disk, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def main() -> None:
    """Generate the codebase reference docs."""
    if DOCS_ROOT.exists():
        shutil.rmtree(DOCS_ROOT)
    DOCS_ROOT.mkdir(parents=True, exist_ok=True)

    _write(DOCS_ROOT / "README.md", _render_root_readme())

    _write(
        DOCS_ROOT / "app" / "00-overview.md",
        _render_section_overview(
            title="Backend Reference",
            summary=(
                "Folder-by-folder reference for the FastAPI backend, queue workers, scraper "
                "stack, and service layer."
            ),
            bullets=(
                "Start here when you want the backend map before diving into a specific module group.",
                "Each linked document inventories direct files in the corresponding source folder unless noted otherwise.",
            ),
            docs=APP_DOCS,
        ),
    )
    for spec in APP_DOCS:
        _write(DOCS_ROOT / spec.output_rel, _render_folder_doc(spec))

    _write(
        DOCS_ROOT / "client" / "00-overview.md",
        _render_section_overview(
            title="Client Reference",
            summary=(
                "Folder-by-folder reference for the SwiftUI app, share extension, project "
                "metadata, and supporting client scripts/tests."
            ),
            bullets=(
                "Use this section to trace backend contracts into Swift models, services, view models, and screens.",
                "Build artifacts are intentionally excluded; the reference focuses on source, project, and extension folders.",
            ),
            docs=CLIENT_DOCS,
        ),
    )
    for spec in CLIENT_DOCS:
        _write(DOCS_ROOT / spec.output_rel, _render_folder_doc(spec))

    _write(DOCS_ROOT / "config" / "00-overview.md", _render_config_overview())


if __name__ == "__main__":
    main()
