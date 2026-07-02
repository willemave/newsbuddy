"""One-off Mad-Lib rendering lab for the unread-briefing prototype.

Re-renders the frozen DeepSeek briefings (outputs/unread_briefing_prototype/
user_1_current/) in Mad-Lib treatments. No LLM calls: pure re-rendering of the
accepted generation so treatments can be compared on identical text.

Outputs:
- madlib_styles.html: the personalized briefing with four switchable styles.
- madlib_categories.html: "For you" plus the category lenses as filter pills,
  same style switcher, prose split into paragraphs.
- madlib_newspaper.html: newspaper edition — masthead, section index, justified
  Fill-in prose, and lead/inline figures for long articles that have generated
  images (mirrored from production into images/<id>.jpg).

Usage:
    uv run python scripts/render_madlib_style_lab.py
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path("outputs/unread_briefing_prototype/user_1_current")

INSIGHT_RE = re.compile(r"\{\{insight:([A-Za-z0-9_-]+)\}\}(.*?)\{\{/insight\}\}", re.S)
INSIGHT_OPEN_RE = re.compile(r"\{\{insight:[A-Za-z0-9_-]+\}\}")
INSIGHT_CLOSE = "{{/insight}}"
SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")
STRAY_INSIGHT_RE = re.compile(r"\{\{/?insight[^}]*\}\}")


def close_unpaired_insights(markdown: str) -> str:
    """Insert missing {{/insight}} closers at the next sentence boundary.

    Models frequently open an insight span and forget the closer; dropping the
    marker would silently lose the tap target, so close it deterministically
    instead (before the next opener if that comes first).
    """
    result: list[str] = []
    cursor = 0
    openers = list(INSIGHT_OPEN_RE.finditer(markdown))
    for index, opener in enumerate(openers):
        if opener.start() < cursor:
            continue
        segment_end = openers[index + 1].start() if index + 1 < len(openers) else len(markdown)
        segment = markdown[opener.end() : segment_end]
        close_at = segment.find(INSIGHT_CLOSE)
        if close_at != -1:
            consumed = opener.end() + close_at + len(INSIGHT_CLOSE)
            result.append(markdown[cursor:consumed])
            cursor = consumed
            continue
        sentence_end = SENTENCE_END_RE.search(segment)
        insert_at = opener.end() + (sentence_end.end() if sentence_end else len(segment))
        result.append(markdown[cursor:insert_at] + INSIGHT_CLOSE)
        cursor = insert_at
    result.append(markdown[cursor:])
    return "".join(result)


LINK_RE = re.compile(r"\[([^\]]+)\]\(newsly://briefing/([a-z_]+)/(\d+)\)")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
PLACEHOLDER_RE = re.compile(r"\x00(\d+)\x00")
TITLE_NOISE_RE = re.compile(r"^\s*#+\s*")
MAX_SENTENCES_PER_PARAGRAPH = 3
MAX_IMAGES_PER_LENS = 6


def clean_title(value: str | None) -> str:
    """Strip stray markdown heading markers that leak in from source titles."""
    return TITLE_NOISE_RE.sub("", value or "").strip()


def slot_label(source: dict) -> str:
    name = (source.get("source_name") or "").strip()
    if not name:
        parsed = urlparse(source.get("url") or source.get("link_url") or "")
        name = parsed.netloc
    return name.removeprefix("www.") or "source"


def render_links(text: str, sources: dict[str, dict]) -> str:
    # Unpaired {{insight}} markers (model forgot the closer) must not leak into prose.
    text = STRAY_INSIGHT_RE.sub("", text)
    parts: list[str] = []
    cursor = 0
    for match in LINK_RE.finditer(text):
        parts.append(html.escape(text[cursor : match.start()]))
        label, kind, item_id = match.group(1), match.group(2), match.group(3)
        source_key = f"{kind}:{item_id}"
        source = sources.get(source_key)
        tag = slot_label(source) if source else "unknown source"
        parts.append(
            f'<a class="slot slot-source" role="button" tabindex="0" '
            f'data-source-key="{html.escape(source_key)}">'
            f'<span class="slot-fill">{html.escape(clean_title(label))}</span>'
            f'<span class="slot-tag">{html.escape(tag)}</span></a>'
        )
        cursor = match.end()
    parts.append(html.escape(text[cursor:]))
    return "".join(parts)


def render_markdown(markdown: str, sources: dict[str, dict]) -> str:
    markdown = close_unpaired_insights(markdown)
    parts: list[str] = []
    cursor = 0
    for match in INSIGHT_RE.finditer(markdown):
        parts.append(render_links(markdown[cursor : match.start()], sources))
        insight_id, body = match.group(1), match.group(2)
        parts.append(
            f'<span class="slot slot-insight" role="button" tabindex="0" '
            f'data-insight-id="{html.escape(insight_id)}">'
            f'<span class="slot-fill">{render_links(body, sources)}</span>'
            f'<span class="slot-tag">insight</span></span>'
        )
        cursor = match.end()
    parts.append(render_links(markdown[cursor:], sources))
    return "".join(parts)


def split_paragraphs(markdown: str) -> list[str]:
    """Split one long chunk into readable paragraphs on sentence boundaries.

    Links and insight spans are stashed behind placeholders first so periods
    inside them can never produce a split point.
    """
    tokens: list[str] = []

    def stash(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        return f"\x00{len(tokens) - 1}\x00"

    protected = INSIGHT_RE.sub(stash, markdown)
    protected = LINK_RE.sub(stash, protected)

    def restore(text: str) -> str:
        return PLACEHOLDER_RE.sub(lambda m: tokens[int(m.group(1))], text)

    sentences = [s for s in SENTENCE_SPLIT_RE.split(protected) if s.strip()]
    groups = [
        sentences[i : i + MAX_SENTENCES_PER_PARAGRAPH]
        for i in range(0, len(sentences), MAX_SENTENCES_PER_PARAGRAPH)
    ]
    # A lone trailing sentence reads better attached to the previous paragraph.
    if len(groups) > 1 and len(groups[-1]) == 1:
        groups[-2].extend(groups.pop())
    return [restore(" ".join(group)) for group in groups]


def split_passages(markdown: str, images: dict[str, str]) -> list[dict]:
    """Split a chunk into passages weighted by article size.

    Sentences that mention a long article become roomier "feature" passages
    (up to two consecutive sentences); runs of news-item sentences pack into
    denser "brief" paragraphs (up to three sentences).
    """
    tokens: list[str] = []

    def stash(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        return f"\x00{len(tokens) - 1}\x00"

    protected = INSIGHT_RE.sub(stash, markdown)
    protected = LINK_RE.sub(stash, protected)

    def restore(text: str) -> str:
        return PLACEHOLDER_RE.sub(lambda m: tokens[int(m.group(1))], text)

    passages: list[dict] = []
    buffer: list[str] = []
    buffer_kind: str | None = None

    def flush() -> None:
        nonlocal buffer, buffer_kind
        if not buffer:
            return
        text = " ".join(buffer)
        keys = [f"{kind}:{item_id}" for _, kind, item_id in LINK_RE.findall(text)]
        passages.append({"kind": buffer_kind, "markdown": text, "source_keys": keys})
        buffer, buffer_kind = [], None

    limits = {"feature": 2, "brief": MAX_SENTENCES_PER_PARAGRAPH}
    for sentence in SENTENCE_SPLIT_RE.split(protected):
        if not sentence.strip():
            continue
        restored = restore(sentence)
        is_feature = any(
            f"{kind}:{item_id}" in images for _, kind, item_id in LINK_RE.findall(restored)
        )
        kind = "feature" if is_feature else "brief"
        if buffer_kind != kind or len(buffer) >= limits[kind]:
            flush()
        buffer_kind = kind
        buffer.append(restored)
    flush()
    return passages


def load_images(sources: dict[str, dict]) -> dict[str, str]:
    """Map source_key -> relative image path for images mirrored locally."""
    images: dict[str, str] = {}
    for key, item in sources.items():
        candidate = ROOT / "images" / f"{item['target_id']}.jpg"
        if item["kind"].startswith("long_") and candidate.exists():
            images[key] = f"images/{item['target_id']}.jpg"
    return images


def render_lens_panel(lens: dict, sources: dict[str, dict], hidden: bool) -> str:
    paragraphs = []
    for chunk in lens["briefing"]["chunks"]:
        for paragraph in split_paragraphs(chunk["markdown"]):
            paragraphs.append(f'<p class="chunk">{render_markdown(paragraph, sources)}</p>')
    deck = html.escape(lens.get("deck") or "")
    hidden_attr = " hidden" if hidden else ""
    return (
        f'<section class="lens-panel" data-lens="{html.escape(lens["lens_id"])}"{hidden_attr}>\n'
        f'<p class="lens-deck">{deck}</p>\n' + "\n".join(paragraphs) + "\n</section>"
    )


def figure_html(
    source_key: str,
    sources: dict[str, dict],
    image: str,
    *,
    variant: str = "",
    caption: str | None = None,
) -> str:
    source = sources[source_key]
    title = clean_title(source.get("original_title"))
    if len(title) > 90:
        title = title[:87].rstrip() + "…"
    if not caption:
        caption = f"{slot_label(source)} — {title}" if title else slot_label(source)
    css_class = f"cut {variant}".strip()
    return (
        f'<figure class="{css_class}" data-source-key="{html.escape(source_key)}" '
        f'role="button" tabindex="0">'
        f'<img src="{html.escape(image)}" alt="{html.escape(title)}" loading="lazy">'
        f"<figcaption>{html.escape(caption)}</figcaption></figure>"
    )


def render_newspaper_panel(
    lens: dict,
    sources: dict[str, dict],
    images: dict[str, str],
    hidden: bool,
) -> str:
    passages: list[dict] = []
    for chunk in lens["briefing"]["chunks"]:
        passages.extend(split_passages(chunk["markdown"], images))

    parts: list[str] = []
    parts.append(f"<h2>{html.escape(lens['label'])}</h2>")
    deck = lens.get("deck") or ""
    if deck:
        parts.append(f'<p class="lens-deck">{html.escape(deck)}</p>')

    used_images: set[str] = set()
    for passage in passages:
        if passage["kind"] == "brief":
            parts.append(
                f'<p class="chunk brief">{render_markdown(passage["markdown"], sources)}</p>'
            )
            continue
        figure = ""
        if len(used_images) < MAX_IMAGES_PER_LENS:
            image_key = next(
                (k for k in passage["source_keys"] if k in images and k not in used_images),
                None,
            )
            if image_key:
                used_images.add(image_key)
                figure = figure_html(image_key, sources, images[image_key])
        parts.append(
            f'<div class="passage">{figure}'
            f'<p class="chunk feature">{render_markdown(passage["markdown"], sources)}</p>'
            f"</div>"
        )

    hidden_attr = " hidden" if hidden else ""
    return (
        f'<section class="lens-panel" data-lens="{html.escape(lens["lens_id"])}"{hidden_attr}>\n'
        + "\n".join(parts)
        + "\n</section>"
    )


def render_layout_panel(
    lens_payload: dict,
    sources: dict[str, dict],
    images: dict[str, str],
    hidden: bool,
) -> str:
    """Render one lens from the LLM-composed block document."""
    parts: list[str] = [f"<h2>{html.escape(lens_payload['title'])}</h2>"]
    deck = lens_payload.get("deck") or ""
    if deck:
        parts.append(f'<p class="lens-deck">{html.escape(deck)}</p>')
    for block in lens_payload["layout"]["blocks"]:
        if block["type"] == "passage":
            weight = block.get("weight") or "brief"
            parts.append(
                f'<p class="chunk {html.escape(weight)}">'
                f"{render_markdown(block['markdown'], sources)}</p>"
            )
        elif block["type"] == "figure":
            key = block["source_key"]
            if key in images:
                parts.append(
                    figure_html(
                        key,
                        sources,
                        images[key],
                        variant=f"cut-{block.get('placement') or 'right'}",
                        caption=block.get("caption"),
                    )
                )
        elif block["type"] == "pullquote":
            attrs = ""
            if block.get("source_key"):
                attrs = (
                    f' data-source-key="{html.escape(block["source_key"])}"'
                    ' role="button" tabindex="0"'
                )
            parts.append(f'<aside class="pullquote"{attrs}>{html.escape(block["text"])}</aside>')
    hidden_attr = " hidden" if hidden else ""
    return (
        f'<section class="lens-panel" data-lens="{html.escape(lens_payload["lens_id"])}"'
        f"{hidden_attr}>\n" + "\n".join(parts) + "\n</section>"
    )


def collect_layout_insights(lens_payloads: list[dict]) -> dict[str, dict]:
    insight_map: dict[str, dict] = {}
    for lens in lens_payloads:
        for block in lens["layout"]["blocks"]:
            if block["type"] == "passage":
                for insight in block.get("insights", []):
                    insight_map[insight["insight_id"]] = insight
    return insight_map


def build_newspaper_page(
    *,
    output: Path,
    dateline: str,
    story_count: int,
    model: str,
    lenses: list[dict],
    sources: dict[str, dict],
    images: dict[str, str],
    layout_lenses: list[dict] | None = None,
) -> None:
    if layout_lenses is not None:
        panels = [
            render_layout_panel(lens, sources, images, hidden=index > 0)
            for index, lens in enumerate(layout_lenses)
        ]
        insights = collect_layout_insights(layout_lenses)
        button_lenses = [
            {
                "lens_id": lens["lens_id"],
                "label": lens["title"],
                "tier": lens.get("tier", ""),
            }
            for lens in layout_lenses
        ]
    else:
        panels = [
            render_newspaper_panel(lens, sources, images, hidden=index > 0)
            for index, lens in enumerate(lenses)
        ]
        insights = collect_insights(lenses)
        button_lenses = [
            {"lens_id": lens["lens_id"], "label": lens["label"], "tier": ""} for lens in lenses
        ]
    button_parts: list[str] = []
    news_divider_added = False
    for index, lens in enumerate(button_lenses):
        if lens["tier"] == "news" and not news_divider_added:
            button_parts.append('<span class="lens-divider">News</span>')
            news_divider_added = True
        button_parts.append(
            f'<button class="lens-button{" active" if index == 0 else ""}" '
            f'data-lens="{html.escape(lens["lens_id"])}">{html.escape(lens["label"])}</button>'
        )
    buttons = "".join(button_parts)
    source_payload = {
        key: {
            "source_key": key,
            "title": clean_title(item.get("original_title")),
            "source_name": slot_label(item),
            "url": item.get("url") or item.get("link_url") or "",
            "summary": item.get("summary") or "",
            "key_points": item.get("key_points") or [],
            "image": images.get(key, ""),
        }
        for key, item in sources.items()
    }
    document = NEWSPAPER_TEMPLATE.format(
        dateline=html.escape(dateline),
        story_count=story_count,
        model=html.escape(model),
        lens_buttons=buttons,
        panels="\n".join(panels),
        sources_json=json.dumps(source_payload, ensure_ascii=False),
        insights_json=json.dumps(insights, ensure_ascii=False),
    )
    output.write_text(document)
    print(f"wrote {output} ({output.stat().st_size} bytes)")


def collect_insights(lenses: list[dict]) -> dict[str, dict]:
    insight_map: dict[str, dict] = {}
    for lens in lenses:
        for chunk in lens["briefing"]["chunks"]:
            for insight in chunk.get("insights", []):
                insight_map[insight["insight_id"]] = insight
    return insight_map


def build_page(
    *,
    output: Path,
    page_title: str,
    subtitle: str,
    model: str,
    lenses: list[dict],
    sources: dict[str, dict],
) -> None:
    panels = [
        render_lens_panel(lens, sources, hidden=index > 0) for index, lens in enumerate(lenses)
    ]
    if len(lenses) > 1:
        buttons = "".join(
            f'<button class="lens-button{" active" if index == 0 else ""}" '
            f'data-lens="{html.escape(lens["lens_id"])}">{html.escape(lens["label"])}'
            f"<span>{lens['source_count']}</span></button>"
            for index, lens in enumerate(lenses)
        )
        lens_nav = f'<nav class="lens-nav" id="lens-nav" aria-label="Lens">{buttons}</nav>'
    else:
        lens_nav = ""

    source_payload = {
        key: {
            "source_key": key,
            "title": clean_title(item.get("original_title")),
            "source_name": slot_label(item),
            "url": item.get("url") or item.get("link_url") or "",
            "summary": item.get("summary") or "",
            "key_points": item.get("key_points") or [],
        }
        for key, item in sources.items()
    }

    document = TEMPLATE.format(
        page_title=html.escape(page_title),
        subtitle=html.escape(subtitle),
        model=html.escape(model),
        lens_nav=lens_nav,
        panels="\n".join(panels),
        sources_json=json.dumps(source_payload, ensure_ascii=False),
        insights_json=json.dumps(collect_insights(lenses), ensure_ascii=False),
    )
    output.write_text(document)
    print(f"wrote {output} ({output.stat().st_size} bytes)")


def main() -> None:
    snapshot = json.loads((ROOT / "briefing.json").read_text())
    personalized = json.loads((ROOT / "personalized.json").read_text())
    categories = json.loads((ROOT / "categories.json").read_text())
    sources = {item["source_key"]: item for item in snapshot["sources"]}

    personal_lens = {
        "lens_id": "for-you",
        "label": "For you",
        "deck": personalized["prose_lens"]["briefing"].get("deck") or "",
        "source_count": personalized["prose_lens"]["source_count"],
        "briefing": personalized["prose_lens"]["briefing"],
    }

    build_page(
        output=ROOT / "madlib_styles.html",
        page_title="Personalized briefing",
        subtitle=personal_lens["deck"],
        model=personalized.get("model", "unknown"),
        lenses=[personal_lens],
        sources=sources,
    )

    category_lenses = [
        {
            "lens_id": lens["lens_id"],
            "label": (lens.get("title") or lens["lens_id"]).removesuffix(" briefing"),
            "deck": lens.get("deck") or "",
            "source_count": lens["source_count"],
            "briefing": lens["briefing"],
        }
        for lens in categories["prose_lenses"]
    ]

    build_page(
        output=ROOT / "madlib_categories.html",
        page_title="Unread briefing",
        subtitle="Pick a lens, then read that slice as one continuous Mad-Lib briefing.",
        model=categories.get("model", "unknown"),
        lenses=[personal_lens] + category_lenses,
        sources=sources,
    )

    generated_at = datetime.fromisoformat(snapshot["generated_at"])
    podcasts_path = ROOT / "podcasts.json"
    if podcasts_path.exists():
        podcast_payload = json.loads(podcasts_path.read_text())
        for item in podcast_payload["sources"]:
            sources[item["source_key"]] = item
    newspaper_path = ROOT / "newspaper.json"
    layout_lenses = None
    newspaper_model = categories.get("model", "unknown")
    if newspaper_path.exists():
        newspaper = json.loads(newspaper_path.read_text())
        layout_lenses = newspaper["lenses"]
        newspaper_model = newspaper.get("model", newspaper_model)
    build_newspaper_page(
        output=ROOT / "madlib_newspaper.html",
        dateline=generated_at.strftime("%A, %B %-d, %Y"),
        story_count=len(sources),
        model=newspaper_model,
        lenses=[personal_lens] + category_lenses,
        sources=sources,
        images=load_images(sources),
        layout_lenses=layout_lenses,
    )


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{page_title}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #fbfbfa;
      --ink: #1f1f1d;
      --muted: #6b6a66;
      --faint: #b7b5ae;
      --line: #d9d8d4;
      --panel: #ffffff;
      --accent: #2f6f4e;
      --soft: #f1f0eb;
      --wash: rgba(31, 31, 29, .055);
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #171817;
        --ink: #f1f0ec;
        --muted: #aaa8a0;
        --faint: #5d5c56;
        --line: #383a36;
        --panel: #222420;
        --accent: #8cc7a2;
        --soft: #20221f;
        --wash: rgba(241, 240, 236, .08);
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 17px/1.85 ui-serif, "New York", Georgia, serif;
    }}
    main {{
      width: min(680px, calc(100vw - 40px));
      margin: 0 auto;
      padding: 30px 0 140px;
    }}
    header {{ margin-bottom: 20px; }}
    h1 {{
      font-size: 1.4rem;
      line-height: 1.15;
      margin: 0 0 6px;
    }}
    .meta {{
      color: var(--muted);
      font-size: .82rem;
      font-family: ui-sans-serif, -apple-system, sans-serif;
    }}
    .progress {{
      position: fixed;
      top: 0; left: 0;
      height: 2px;
      width: 0;
      background: var(--accent);
      transition: width 220ms ease;
      z-index: 40;
    }}
    .lens-nav {{
      display: flex;
      gap: 8px;
      overflow-x: auto;
      padding: 12px 0 2px;
      scrollbar-width: none;
      font-family: ui-sans-serif, -apple-system, sans-serif;
    }}
    .lens-nav::-webkit-scrollbar {{ display: none; }}
    .lens-button {{
      appearance: none;
      background: transparent;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--ink);
      cursor: pointer;
      flex: 0 0 auto;
      font: inherit;
      font-size: .86rem;
      line-height: 1.15;
      padding: 7px 12px;
    }}
    .lens-button.active {{
      background: var(--soft);
      border-color: var(--accent);
    }}
    .lens-button span {{
      color: var(--muted);
      margin-left: 5px;
      font-size: .78rem;
    }}
    .lens-deck {{
      color: var(--muted);
      font-size: .86rem;
      font-family: ui-sans-serif, -apple-system, sans-serif;
      line-height: 1.5;
      margin: 0 0 18px;
    }}
    .lens-panel[hidden] {{ display: none; }}
    .chunk {{ margin: 0 0 1.35em; }}

    .slot {{
      cursor: pointer;
      color: inherit;
      text-decoration: none;
      -webkit-tap-highlight-color: transparent;
    }}
    .slot-tag {{
      font-family: ui-sans-serif, -apple-system, sans-serif;
      font-size: .56em;
      font-weight: 600;
      letter-spacing: .09em;
      text-transform: uppercase;
      color: var(--muted);
      line-height: 0;
      vertical-align: -1.5em;
      margin: 0 .35em 0 -.1em;
      user-select: none;
      white-space: nowrap;
      transition: opacity 400ms ease;
    }}
    .slot.seen .slot-tag {{ opacity: .3; }}
    .slot.active .slot-fill {{
      background: var(--wash);
      outline: 2px solid var(--accent);
      outline-offset: 2px;
      border-radius: 2px;
    }}
    .slot:focus-visible .slot-fill {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

    /* ---- Treatment 1: Fill-in (classic mad lib blanks) ---- */
    body.theme-fillin {{ line-height: 2.05; }}
    body.theme-fillin .slot-fill {{
      font-style: italic;
      box-shadow: inset 0 -1.5px var(--ink);
      padding-bottom: 1px;
    }}
    body.theme-fillin .slot-insight .slot-fill {{
      box-shadow: none;
      background-image: linear-gradient(to right, var(--ink) 45%, transparent 45%);
      background-size: 7px 1.5px;
      background-repeat: repeat-x;
      background-position: 0 100%;
    }}
    body.theme-fillin .slot.seen .slot-fill {{
      box-shadow: inset 0 -1px var(--faint);
      background-image: none;
    }}

    /* ---- Treatment 2: Scrawl (handwritten fills on ruled blanks) ---- */
    body.theme-scrawl {{ line-height: 2.1; }}
    body.theme-scrawl .slot-fill {{
      font-family: "Bradley Hand", "Marker Felt", "Segoe Print", "Comic Sans MS", cursive;
      font-size: 1.02em;
      box-shadow: inset 0 -1.5px var(--ink);
      padding: 0 .15em 1px;
    }}
    body.theme-scrawl .slot-insight .slot-fill {{
      box-shadow: none;
      border: 1.5px solid var(--muted);
      border-radius: 999px 8px 999px 10px / 12px 999px 10px 999px;
      padding: 0 .35em;
    }}
    body.theme-scrawl .slot.seen .slot-fill {{
      box-shadow: inset 0 -1px var(--faint);
      border-color: var(--faint);
    }}

    /* ---- Treatment 3: Typed (form answers punched into the prose) ---- */
    body.theme-typed {{ line-height: 1.95; }}
    body.theme-typed .slot-fill {{
      font-family: ui-monospace, "SF Mono", Menlo, monospace;
      font-size: .84em;
      background: var(--soft);
      box-shadow: inset 0 -1.5px var(--muted);
      padding: .05em .3em;
      border-radius: 3px 3px 0 0;
      box-decoration-break: clone;
      -webkit-box-decoration-break: clone;
    }}
    body.theme-typed .slot-insight .slot-fill {{
      background: transparent;
      box-shadow: none;
      background-image: linear-gradient(to right, var(--muted) 40%, transparent 40%);
      background-size: 6px 1.5px;
      background-repeat: repeat-x;
      background-position: 0 100%;
    }}
    body.theme-typed .slot.seen .slot-fill {{
      background: transparent;
      box-shadow: inset 0 -1px var(--faint);
      background-image: none;
    }}

    /* ---- Treatment 4: Quiet (near-monochrome editorial) ---- */
    body.theme-quiet .slot-tag {{ display: none; }}
    body.theme-quiet .slot-source .slot-fill {{
      text-decoration: underline;
      text-decoration-thickness: 1px;
      text-decoration-color: var(--ink);
      text-underline-offset: 4px;
    }}
    body.theme-quiet .slot-insight .slot-fill {{
      background: var(--wash);
      box-decoration-break: clone;
      -webkit-box-decoration-break: clone;
      padding: .04em .12em;
      border-radius: 2px;
    }}
    body.theme-quiet .slot-source.seen .slot-fill {{
      text-decoration-style: dotted;
      text-decoration-color: var(--faint);
    }}
    body.theme-quiet .lens-panel:not([hidden]) .chunk:first-of-type::first-letter {{
      font-size: 3.1em;
      float: left;
      line-height: .82;
      padding: .08em .09em 0 0;
    }}

    /* ---- Switcher ---- */
    .switcher {{
      position: fixed;
      left: 50%;
      bottom: 18px;
      transform: translateX(-50%);
      display: flex;
      gap: 4px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px;
      box-shadow: 0 4px 18px rgba(0,0,0,.14);
      z-index: 25;
      font-family: ui-sans-serif, -apple-system, sans-serif;
    }}
    .switcher button {{
      appearance: none;
      border: 0;
      background: transparent;
      color: var(--muted);
      font-size: .8rem;
      padding: 7px 12px;
      border-radius: 999px;
      cursor: pointer;
    }}
    .switcher button.active {{ background: var(--soft); color: var(--ink); }}
    body.sheet-open .switcher {{ opacity: 0; pointer-events: none; }}

    /* ---- Bottom sheet ---- */
    body.sheet-open {{ overflow: hidden; }}
    .sheet-backdrop {{
      position: fixed; inset: 0;
      background: rgba(0,0,0,.30);
      opacity: 0;
      pointer-events: none;
      transition: opacity 160ms ease;
      z-index: 30;
    }}
    .sheet-backdrop.open {{ opacity: 1; pointer-events: auto; }}
    .bottom-sheet {{
      position: fixed;
      left: 50%; bottom: 0;
      width: min(680px, 100vw);
      max-height: 72vh;
      background: var(--panel);
      border: 1px solid var(--line);
      border-bottom: 0;
      border-radius: 12px 12px 0 0;
      transform: translate(-50%, calc(100% + 14px));
      transition: transform 190ms ease;
      z-index: 35;
      overflow-y: auto;
      padding: 14px 20px 30px;
      font-family: ui-sans-serif, -apple-system, sans-serif;
      font-size: .93rem;
      line-height: 1.55;
    }}
    .bottom-sheet.open {{ transform: translate(-50%, 0); }}
    .sheet-grabber {{
      width: 38px;
      height: 4px;
      border-radius: 2px;
      background: var(--line);
      margin: 0 auto 14px;
    }}
    .sheet-kicker {{
      color: var(--muted);
      font-size: .74rem;
      text-transform: uppercase;
      letter-spacing: .09em;
      margin: 0 0 6px;
    }}
    .bottom-sheet h2 {{ font-size: 1.05rem; line-height: 1.3; margin: 0 0 10px; }}
    .bottom-sheet p {{ margin: 0 0 10px; color: var(--ink); }}
    .bottom-sheet ul {{ margin: 0 0 12px; padding-left: 18px; color: var(--muted); }}
    .bottom-sheet li {{ margin-bottom: 4px; }}
    .bottom-sheet a {{ color: var(--accent); }}
  </style>
</head>
<body class="theme-fillin">
  <div class="progress" id="progress"></div>
  <main>
    <header>
      <h1>{page_title}</h1>
      <p class="meta">{subtitle}</p>
      <p class="meta">mad-lib lab &middot; {model}</p>
      {lens_nav}
    </header>
    <article id="briefing">
{panels}
    </article>
  </main>

  <nav class="switcher" id="switcher" aria-label="Rendering style">
    <button data-theme="theme-fillin" class="active">Fill-in</button>
    <button data-theme="theme-scrawl">Scrawl</button>
    <button data-theme="theme-typed">Typed</button>
    <button data-theme="theme-quiet">Quiet</button>
  </nav>

  <div class="sheet-backdrop" id="backdrop"></div>
  <section class="bottom-sheet" id="sheet" role="dialog" aria-modal="true">
    <div class="sheet-grabber"></div>
    <div id="sheet-body"></div>
  </section>

  <script>
    const SOURCES = {sources_json};
    const INSIGHTS = {insights_json};

    const body = document.body;
    const sheet = document.getElementById('sheet');
    const backdrop = document.getElementById('backdrop');
    const sheetBody = document.getElementById('sheet-body');
    const progress = document.getElementById('progress');

    // --- style switcher ---
    const switcher = document.getElementById('switcher');
    const savedTheme = localStorage.getItem('madlib-theme');
    if (savedTheme && savedTheme.startsWith('theme-')) setTheme(savedTheme);
    function setTheme(theme) {{
      body.className = body.className.replace(/theme-\\S+/, theme);
      for (const button of switcher.querySelectorAll('button')) {{
        button.classList.toggle('active', button.dataset.theme === theme);
      }}
      localStorage.setItem('madlib-theme', theme);
    }}
    switcher.addEventListener('click', (event) => {{
      const button = event.target.closest('button[data-theme]');
      if (button) setTheme(button.dataset.theme);
    }});

    // --- lens switching ---
    const lensNav = document.getElementById('lens-nav');
    if (lensNav) {{
      lensNav.addEventListener('click', (event) => {{
        const button = event.target.closest('button[data-lens]');
        if (!button) return;
        for (const other of lensNav.querySelectorAll('.lens-button')) {{
          other.classList.toggle('active', other === button);
        }}
        for (const panel of document.querySelectorAll('.lens-panel')) {{
          panel.hidden = panel.dataset.lens !== button.dataset.lens;
        }}
        window.scrollTo({{ top: 0 }});
        scanSeen();
      }});
    }}

    // --- seen tracking: a slot counts as seen once it scrolls past the top ---
    // Direct scan (no IntersectionObserver): observers miss jump-scrolls where
    // a slot goes below-viewport to above-viewport without ever intersecting.
    const unseen = new Set(document.querySelectorAll('.slot'));
    function scanSeen() {{
      for (const slot of unseen) {{
        if (slot.getBoundingClientRect().bottom < 0) {{
          slot.classList.add('seen');
          unseen.delete(slot);
        }}
      }}
      const active = document.querySelector('.lens-panel:not([hidden])') || document;
      const slots = active.querySelectorAll('.slot');
      const seenCount = active.querySelectorAll('.slot.seen').length;
      progress.style.width = (100 * seenCount / Math.max(1, slots.length)) + '%';
    }}
    window.addEventListener('scroll', scanSeen, {{ passive: true }});
    window.addEventListener('resize', scanSeen, {{ passive: true }});
    scanSeen();

    // --- bottom sheet ---
    let activeSlot = null;
    function openSheet(contentHtml, slot) {{
      sheetBody.innerHTML = contentHtml;
      body.classList.add('sheet-open');
      sheet.classList.add('open');
      backdrop.classList.add('open');
      if (activeSlot) activeSlot.classList.remove('active');
      activeSlot = slot;
      slot.classList.add('active');
    }}
    function closeSheet() {{
      body.classList.remove('sheet-open');
      sheet.classList.remove('open');
      backdrop.classList.remove('open');
      if (activeSlot) activeSlot.classList.remove('active');
      activeSlot = null;
    }}
    backdrop.addEventListener('click', closeSheet);
    document.addEventListener('keydown', (event) => {{
      if (event.key === 'Escape') closeSheet();
    }});

    function escapeHtml(value) {{
      const div = document.createElement('div');
      div.textContent = value ?? '';
      return div.innerHTML;
    }}

    function sourceSheet(sourceKey) {{
      const source = SOURCES[sourceKey];
      if (!source) return '<p>Unknown source.</p>';
      const points = (source.key_points || [])
        .map((point) => `<li>${{escapeHtml(point)}}</li>`).join('');
      return `
        <p class="sheet-kicker">${{escapeHtml(source.source_name)}}</p>
        <h2>${{escapeHtml(source.title)}}</h2>
        <p>${{escapeHtml(source.summary)}}</p>
        ${{points ? `<ul>${{points}}</ul>` : ''}}
        ${{source.url
          ? `<p><a href="${{escapeHtml(source.url)}}" target="_blank" rel="noopener">` +
            'Open original</a></p>'
          : ''}}
      `;
    }}

    function insightSheet(insightId) {{
      const insight = INSIGHTS[insightId];
      if (!insight) return '<p>Unknown insight.</p>';
      const questions = (insight.follow_up_questions || [])
        .map((question) => `<li>${{escapeHtml(question)}}</li>`).join('');
      const related = (insight.source_keys || [])
        .map((key) => SOURCES[key])
        .filter(Boolean)
        .map((source) => `<li>${{escapeHtml(source.title || source.source_name)}}</li>`)
        .join('');
      return `
        <p class="sheet-kicker">Insight</p>
        <h2>${{escapeHtml(insight.title)}}</h2>
        <p>${{escapeHtml(insight.learn_more)}}</p>
        ${{questions ? `<p class="sheet-kicker">Follow-ups</p><ul>${{questions}}</ul>` : ''}}
        ${{related ? `<p class="sheet-kicker">From</p><ul>${{related}}</ul>` : ''}}
      `;
    }}

    document.getElementById('briefing').addEventListener('click', (event) => {{
      const slot = event.target.closest('.slot');
      if (!slot) return;
      event.preventDefault();
      if (slot.dataset.sourceKey) openSheet(sourceSheet(slot.dataset.sourceKey), slot);
      else if (slot.dataset.insightId) openSheet(insightSheet(slot.dataset.insightId), slot);
    }});
    document.getElementById('briefing').addEventListener('keydown', (event) => {{
      if (event.key !== 'Enter' && event.key !== ' ') return;
      const slot = event.target.closest('.slot');
      if (!slot) return;
      event.preventDefault();
      slot.click();
    }});
  </script>
</body>
</html>
"""


NEWSPAPER_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>The Unread Times</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #faf9f5;
      --ink: #1c1c1a;
      --muted: #6b6a66;
      --faint: #b7b5ae;
      --line: #d5d3cc;
      --panel: #ffffff;
      --accent: #2f6f4e;
      --soft: #f0efe9;
      --wash: rgba(28, 28, 26, .055);
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #171817;
        --ink: #f1f0ec;
        --muted: #aaa8a0;
        --faint: #5d5c56;
        --line: #383a36;
        --panel: #222420;
        --accent: #8cc7a2;
        --soft: #20221f;
        --wash: rgba(241, 240, 236, .08);
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 16.5px/1.95 ui-serif, "New York", Georgia, serif;
    }}
    main {{
      width: min(640px, calc(100vw - 36px));
      margin: 0 auto;
      padding: 26px 0 120px;
    }}
    .progress {{
      position: fixed;
      top: 0; left: 0;
      height: 2px;
      width: 0;
      background: var(--accent);
      transition: width 220ms ease;
      z-index: 40;
    }}

    /* ---- masthead ---- */
    .masthead {{
      text-align: center;
      border-bottom: 3px double var(--ink);
      padding-bottom: 12px;
      margin-bottom: 2px;
    }}
    .masthead .kicker {{
      font-family: ui-sans-serif, -apple-system, sans-serif;
      font-size: .68rem;
      font-weight: 600;
      letter-spacing: .22em;
      text-transform: uppercase;
      color: var(--muted);
      margin: 0 0 6px;
    }}
    .masthead h1 {{
      font-size: clamp(2rem, 9vw, 3.1rem);
      font-weight: 800;
      letter-spacing: -.01em;
      line-height: 1;
      margin: 0 0 10px;
    }}
    .masthead .dateline {{
      font-family: ui-sans-serif, -apple-system, sans-serif;
      font-size: .74rem;
      letter-spacing: .06em;
      text-transform: uppercase;
      color: var(--muted);
      margin: 0;
    }}

    /* ---- section index ---- */
    .lens-nav {{
      display: flex;
      gap: 0;
      overflow-x: auto;
      border-bottom: 1px solid var(--ink);
      margin-bottom: 22px;
      scrollbar-width: none;
      font-family: ui-sans-serif, -apple-system, sans-serif;
    }}
    .lens-nav::-webkit-scrollbar {{ display: none; }}
    .lens-button {{
      appearance: none;
      background: transparent;
      border: 0;
      border-right: 1px solid var(--line);
      color: var(--muted);
      cursor: pointer;
      flex: 0 0 auto;
      font-size: .72rem;
      font-weight: 600;
      letter-spacing: .12em;
      text-transform: uppercase;
      padding: 10px 13px;
    }}
    .lens-button:last-child {{ border-right: 0; }}
    .lens-button.active {{
      color: var(--ink);
      box-shadow: inset 0 -3px var(--ink);
    }}
    .lens-divider {{
      flex: 0 0 auto;
      align-self: center;
      color: var(--faint);
      font-size: .62rem;
      font-weight: 700;
      letter-spacing: .14em;
      text-transform: uppercase;
      padding: 0 10px 0 14px;
      border-left: 3px double var(--line);
    }}

    /* ---- panel / prose ---- */
    .lens-panel[hidden] {{ display: none; }}
    .lens-panel::after {{ content: ""; display: block; clear: both; }}
    .lens-panel h2 {{
      font-size: 1.7rem;
      line-height: 1.1;
      margin: 0 0 6px;
    }}
    .lens-deck {{
      color: var(--muted);
      font-size: .88rem;
      font-family: ui-sans-serif, -apple-system, sans-serif;
      line-height: 1.5;
      margin: 0 0 16px;
    }}
    .chunk {{
      margin: 0 0 1.25em;
      text-align: justify;
      hyphens: auto;
      -webkit-hyphens: auto;
    }}
    .chunk.feature {{
      font-size: 1.05em;
      line-height: 2.0;
    }}
    .chunk.brief {{
      font-size: .92em;
      line-height: 1.8;
      margin-bottom: 1.1em;
    }}
    .lens-deck + .chunk::first-letter,
    .lens-deck + .pullquote + .chunk::first-letter,
    .lens-deck + .passage .chunk::first-letter {{
      font-size: 3.4em;
      font-weight: 700;
      float: left;
      line-height: .8;
      padding: .07em .09em 0 0;
    }}
    .pullquote {{
      border-top: 1px solid var(--ink);
      border-bottom: 1px solid var(--ink);
      font-size: 1.25em;
      font-style: italic;
      line-height: 1.5;
      text-align: left;
      margin: 6px 0 18px;
      padding: 12px 2px;
    }}
    .pullquote[data-source-key] {{ cursor: pointer; }}

    /* ---- inline dig-deeper panel ---- */
    .dig {{
      border-left: 2px solid var(--accent);
      background: var(--soft);
      font-family: ui-sans-serif, -apple-system, sans-serif;
      font-size: .84rem;
      line-height: 1.55;
      margin: -6px 0 18px;
      padding: 10px 12px 12px;
    }}
    .dig-head {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 10px;
      margin-bottom: 6px;
    }}
    .dig-kicker {{
      color: var(--muted);
      font-size: .66rem;
      font-weight: 700;
      letter-spacing: .12em;
      text-transform: uppercase;
    }}
    .dig-close {{
      appearance: none;
      border: 0;
      background: transparent;
      color: var(--muted);
      font: inherit;
      font-size: .9rem;
      cursor: pointer;
      padding: 0 2px;
    }}
    .dig-summary {{ color: var(--ink); margin: 0 0 8px; white-space: pre-wrap; }}
    .dig-sources {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px 10px;
      margin: 0 0 4px;
    }}
    .dig-sources a {{
      color: var(--accent);
      font-size: .76rem;
      text-decoration: underline;
      text-underline-offset: 2px;
    }}
    .dig-meta {{ color: var(--faint); font-size: .68rem; margin: 4px 0 0; }}
    .dig-shimmer {{
      height: .8em;
      border-radius: 3px;
      background: linear-gradient(90deg, var(--line) 25%, var(--panel) 50%, var(--line) 75%);
      background-size: 200% 100%;
      animation: dig-shimmer 1.1s linear infinite;
      margin: 6px 0;
    }}
    .dig-shimmer.short {{ width: 62%; }}
    @keyframes dig-shimmer {{
      from {{ background-position: 200% 0; }}
      to {{ background-position: -200% 0; }}
    }}
    .dig-pill {{
      position: fixed;
      z-index: 45;
      transform: translateX(-50%);
      appearance: none;
      border: 0;
      background: var(--accent);
      color: #fff;
      font-family: ui-sans-serif, -apple-system, sans-serif;
      font-size: .82rem;
      font-weight: 600;
      letter-spacing: .01em;
      padding: 8px 14px;
      border-radius: 999px;
      box-shadow: 0 4px 14px rgba(0,0,0,.3);
      cursor: pointer;
    }}
    .dig-pill[hidden] {{ display: none; }}
    .dig-tip {{
      color: var(--muted);
      font-size: .74rem;
      font-family: ui-sans-serif, -apple-system, sans-serif;
      letter-spacing: .01em;
      margin: 10px 0 0;
    }}
    .dig-tip b {{ color: var(--ink); font-weight: 600; }}

    /* ---- figures ---- */
    .cut {{
      margin: 4px 0 16px;
      cursor: pointer;
      -webkit-tap-highlight-color: transparent;
    }}
    .cut img {{
      display: block;
      width: 100%;
      border: 1px solid var(--line);
      filter: grayscale(1) contrast(1.05);
    }}
    .cut figcaption {{
      font-family: ui-sans-serif, -apple-system, sans-serif;
      font-size: .7rem;
      letter-spacing: .02em;
      line-height: 1.45;
      color: var(--muted);
      padding-top: 6px;
      text-align: left;
    }}
    .cut-right {{
      float: right;
      width: 40%;
      margin: 5px 0 10px 14px;
    }}
    .cut-left {{
      float: left;
      width: 40%;
      margin: 5px 14px 10px 0;
    }}
    .cut-full {{ margin: 8px 0 18px; }}
    .cut:focus-visible img {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

    /* ---- mad-lib fill-in slots ---- */
    .slot {{
      cursor: pointer;
      color: inherit;
      text-decoration: none;
      -webkit-tap-highlight-color: transparent;
    }}
    .slot-fill {{
      font-style: italic;
      box-shadow: inset 0 -1.5px var(--ink);
      padding-bottom: 1px;
    }}
    /* Insight phrases are soft hints, not hard targets: they mark where a dig
       is likely worthwhile, but the reader selects whatever they want. */
    .slot-insight {{ cursor: text; }}
    .slot-insight .slot-fill {{
      font-style: normal;
      box-shadow: none;
      background-image: linear-gradient(to right, var(--faint) 50%, transparent 50%);
      background-size: 4px 1px;
      background-repeat: repeat-x;
      background-position: 0 1.15em;
      transition: background-color 200ms ease;
      border-radius: 2px;
    }}
    @media (hover: hover) {{
      .slot-insight:hover .slot-fill {{ background-color: var(--wash); }}
    }}
    .slot-insight .slot-tag {{ display: none; }}
    .slot-tag {{
      font-family: ui-sans-serif, -apple-system, sans-serif;
      font-size: .56em;
      font-weight: 600;
      letter-spacing: .09em;
      text-transform: uppercase;
      color: var(--muted);
      line-height: 0;
      vertical-align: -1.5em;
      margin: 0 .35em 0 -.1em;
      user-select: none;
      white-space: nowrap;
      transition: opacity 400ms ease;
    }}
    .slot.seen .slot-fill {{
      box-shadow: inset 0 -1px var(--faint);
      background-image: none;
    }}
    .slot.seen .slot-tag {{ opacity: .3; }}
    .slot.active .slot-fill {{
      background: var(--wash);
      outline: 2px solid var(--accent);
      outline-offset: 2px;
      border-radius: 2px;
    }}
    .slot:focus-visible .slot-fill {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

    /* ---- bottom sheet ---- */
    body.sheet-open {{ overflow: hidden; }}
    .sheet-backdrop {{
      position: fixed; inset: 0;
      background: rgba(0,0,0,.30);
      opacity: 0;
      pointer-events: none;
      transition: opacity 160ms ease;
      z-index: 30;
    }}
    .sheet-backdrop.open {{ opacity: 1; pointer-events: auto; }}
    .bottom-sheet {{
      position: fixed;
      left: 50%; bottom: 0;
      width: min(680px, 100vw);
      max-height: 78vh;
      background: var(--panel);
      border: 1px solid var(--line);
      border-bottom: 0;
      border-radius: 12px 12px 0 0;
      transform: translate(-50%, calc(100% + 14px));
      transition: transform 190ms ease;
      z-index: 35;
      overflow-y: auto;
      padding: 14px 20px 30px;
      font-family: ui-sans-serif, -apple-system, sans-serif;
      font-size: .93rem;
      line-height: 1.55;
    }}
    .bottom-sheet.open {{ transform: translate(-50%, 0); }}
    .sheet-grabber {{
      width: 38px;
      height: 4px;
      border-radius: 2px;
      background: var(--line);
      margin: 0 auto 14px;
    }}
    .sheet-image {{
      display: block;
      width: 100%;
      border-radius: 8px;
      margin-bottom: 12px;
    }}
    .sheet-kicker {{
      color: var(--muted);
      font-size: .74rem;
      text-transform: uppercase;
      letter-spacing: .09em;
      margin: 0 0 6px;
    }}
    .bottom-sheet h2 {{ font-size: 1.05rem; line-height: 1.3; margin: 0 0 10px; }}
    .bottom-sheet p {{ margin: 0 0 10px; color: var(--ink); }}
    .bottom-sheet ul {{ margin: 0 0 12px; padding-left: 18px; color: var(--muted); }}
    .bottom-sheet li {{ margin-bottom: 4px; }}
    .bottom-sheet a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <div class="progress" id="progress"></div>
  <main>
    <header class="masthead">
      <p class="kicker">Newsly &middot; unread edition</p>
      <h1>The Unread Times</h1>
      <p class="dateline">{dateline} &middot; {story_count} unread stories &middot; {model}</p>
    </header>
    <nav class="lens-nav" id="lens-nav" aria-label="Section">{lens_buttons}</nav>
    <p class="dig-tip">Select any phrase to <b>dig deeper</b>. The faint underlines
    mark spots worth a closer look.</p>
    <article id="briefing">
{panels}
    </article>
  </main>

  <div class="sheet-backdrop" id="backdrop"></div>
  <section class="bottom-sheet" id="sheet" role="dialog" aria-modal="true">
    <div class="sheet-grabber"></div>
    <div id="sheet-body"></div>
  </section>

  <script>
    const SOURCES = {sources_json};
    const INSIGHTS = {insights_json};

    const body = document.body;
    const sheet = document.getElementById('sheet');
    const backdrop = document.getElementById('backdrop');
    const sheetBody = document.getElementById('sheet-body');
    const progress = document.getElementById('progress');

    // --- lens switching ---
    const lensNav = document.getElementById('lens-nav');
    lensNav.addEventListener('click', (event) => {{
      const button = event.target.closest('button[data-lens]');
      if (!button) return;
      for (const other of lensNav.querySelectorAll('.lens-button')) {{
        other.classList.toggle('active', other === button);
      }}
      for (const panel of document.querySelectorAll('.lens-panel')) {{
        panel.hidden = panel.dataset.lens !== button.dataset.lens;
      }}
      window.scrollTo({{ top: 0 }});
      scanSeen();
    }});

    // --- seen tracking (direct scan; observers miss jump-scrolls) ---
    const unseen = new Set(document.querySelectorAll('.slot'));
    function scanSeen() {{
      for (const slot of unseen) {{
        if (slot.getBoundingClientRect().bottom < 0) {{
          slot.classList.add('seen');
          unseen.delete(slot);
        }}
      }}
      const active = document.querySelector('.lens-panel:not([hidden])') || document;
      const slots = active.querySelectorAll('.slot');
      const seenCount = active.querySelectorAll('.slot.seen').length;
      progress.style.width = (100 * seenCount / Math.max(1, slots.length)) + '%';
    }}
    window.addEventListener('scroll', scanSeen, {{ passive: true }});
    window.addEventListener('resize', scanSeen, {{ passive: true }});
    scanSeen();

    // --- bottom sheet ---
    let activeSlot = null;
    function openSheet(contentHtml, slot) {{
      sheetBody.innerHTML = contentHtml;
      body.classList.add('sheet-open');
      sheet.classList.add('open');
      backdrop.classList.add('open');
      if (activeSlot) activeSlot.classList.remove('active');
      activeSlot = slot;
      slot.classList.add('active');
    }}
    function closeSheet() {{
      body.classList.remove('sheet-open');
      sheet.classList.remove('open');
      backdrop.classList.remove('open');
      if (activeSlot) activeSlot.classList.remove('active');
      activeSlot = null;
    }}
    backdrop.addEventListener('click', closeSheet);
    document.addEventListener('keydown', (event) => {{
      if (event.key === 'Escape') closeSheet();
    }});

    function escapeHtml(value) {{
      const div = document.createElement('div');
      div.textContent = value ?? '';
      return div.innerHTML;
    }}

    function sourceSheet(sourceKey) {{
      const source = SOURCES[sourceKey];
      if (!source) return '<p>Unknown source.</p>';
      const points = (source.key_points || [])
        .map((point) => `<li>${{escapeHtml(point)}}</li>`).join('');
      return `
        ${{source.image
          ? `<img class="sheet-image" src="${{escapeHtml(source.image)}}" alt="">`
          : ''}}
        <p class="sheet-kicker">${{escapeHtml(source.source_name)}}</p>
        <h2>${{escapeHtml(source.title)}}</h2>
        <p>${{escapeHtml(source.summary)}}</p>
        ${{points ? `<ul>${{points}}</ul>` : ''}}
        ${{source.url
          ? `<p><a href="${{escapeHtml(source.url)}}" target="_blank" rel="noopener">` +
            'Open original</a></p>'
          : ''}}
      `;
    }}

    // --- inline dig-deeper: select any phrase, then live web search + summary ---
    // The dig API runs on its own dedicated port so it never competes with a
    // plain static server that may squat on the page's port. Same-origin when
    // the page itself is served from the API port; absolute (CORS) otherwise.
    const DIG_API_PORT = '8790';
    const DIG_API = location.port === DIG_API_PORT
      ? ''
      : location.protocol + '//' + location.hostname + ':' + DIG_API_PORT;
    const DIG_CACHE = {{}};
    const OPEN_DIGS = {{}};

    async function digFetch(path, options) {{
      let lastError = null;
      for (let attempt = 0; attempt < 2; attempt++) {{
        try {{
          const response = await fetch(DIG_API + path, options);
          if (response.ok) return response;
          lastError = new Error(path.split('?')[0].replace('/api/', '') + ' ' + response.status);
        }} catch (error) {{
          lastError = error;
        }}
        await new Promise((resolve) => setTimeout(resolve, 350));
      }}
      throw lastError || new Error('request failed');
    }}

    function digPanelSkeleton(digId, fragment) {{
      const aside = document.createElement('aside');
      aside.className = 'dig';
      aside.dataset.digId = digId;
      aside.innerHTML = `
        <div class="dig-head">
          <span class="dig-kicker">Dig deeper — ${{escapeHtml(fragment.slice(0, 80))}}</span>
          <button class="dig-close" aria-label="Close">✕</button>
        </div>
        <div class="dig-sources"><div class="dig-shimmer" style="width:100%"></div></div>
        <div class="dig-body">
          <div class="dig-shimmer"></div>
          <div class="dig-shimmer"></div>
          <div class="dig-shimmer short"></div>
        </div>
        <p class="dig-meta">searching…</p>
      `;
      aside.querySelector('.dig-close').addEventListener('click', () => {{
        aside.remove();
        delete OPEN_DIGS[digId];
      }});
      return aside;
    }}

    function renderDigSources(panel, results) {{
      panel.querySelector('.dig-sources').innerHTML = results.length
        ? results.map((result) =>
            `<a href="${{escapeHtml(result.url)}}" target="_blank" rel="noopener">` +
            `${{escapeHtml((result.title || result.url).slice(0, 60))}}</a>`
          ).join('')
        : '<span class="dig-meta">no web results</span>';
    }}

    function renderDigSummary(panel, data) {{
      panel.querySelector('.dig-body').innerHTML =
        `<p class="dig-summary">${{escapeHtml(data.summary)}}</p>`;
      panel.querySelector('.dig-meta').textContent =
        `${{data.model || 'live'}} · search ${{data.searchMs}}ms · summary ${{data.summaryMs}}ms`;
    }}

    function renderDigFallback(panel, fallbackInsightId, reason) {{
      const insight = fallbackInsightId ? INSIGHTS[fallbackInsightId] : null;
      panel.querySelector('.dig-body').innerHTML = insight
        ? `<p class="dig-summary">${{escapeHtml(insight.learn_more)}}</p>`
        : '<p class="dig-summary">Couldn\\'t dig into that just now — try again.</p>';
      panel.querySelector('.dig-meta').textContent = insight
        ? `live dig failed (${{reason}}) — showing pre-generated note`
        : `live dig failed (${{reason}})`;
    }}

    async function runDig(panel, cacheKey, fragment, passage, fallbackInsightId) {{
      if (DIG_CACHE[cacheKey]) {{
        renderDigSources(panel, DIG_CACHE[cacheKey].results);
        renderDigSummary(panel, DIG_CACHE[cacheKey]);
        return;
      }}
      try {{
        const searchStart = performance.now();
        const searchResponse = await digFetch('/api/search?q=' + encodeURIComponent(fragment));
        const search = await searchResponse.json();
        const searchMs = Math.round(performance.now() - searchStart);
        renderDigSources(panel, search.results);
        panel.querySelector('.dig-meta').textContent = 'summarizing…';

        const summaryStart = performance.now();
        const summaryResponse = await digFetch('/api/summarize', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ fragment, passage, results: search.results }}),
        }});
        const summary = await summaryResponse.json();
        const data = {{
          results: search.results,
          summary: summary.summary,
          model: summary.model,
          searchMs,
          summaryMs: Math.round(performance.now() - summaryStart),
        }};
        DIG_CACHE[cacheKey] = data;
        renderDigSummary(panel, data);
      }} catch (error) {{
        renderDigFallback(panel, fallbackInsightId, error.message);
      }}
    }}

    function openDig(fragment, passageEl, fallbackInsightId) {{
      const digId = (fallbackInsightId || 'sel') + '::' + fragment.slice(0, 80);
      const existing = OPEN_DIGS[digId];
      if (existing && existing.isConnected) {{
        existing.remove();
        delete OPEN_DIGS[digId];
        return;
      }}
      const panel = digPanelSkeleton(digId, fragment);
      passageEl.after(panel);
      OPEN_DIGS[digId] = panel;
      runDig(panel, digId, fragment, passageEl.textContent.slice(0, 1500), fallbackInsightId);
      panel.scrollIntoView({{ block: 'nearest', behavior: 'smooth' }});
    }}

    // --- selection → floating "dig deeper" pill ---
    const digPill = document.createElement('button');
    digPill.className = 'dig-pill';
    digPill.hidden = true;
    digPill.textContent = 'Dig deeper ↗';
    document.body.appendChild(digPill);

    let pendingFragment = '';
    let pendingPassage = null;
    let pendingInsightId = null;

    function passageOf(node) {{
      const el = node && node.nodeType === 1 ? node : (node ? node.parentElement : null);
      return el ? el.closest('#briefing .chunk') : null;
    }}

    function refreshPill() {{
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || selection.rangeCount === 0) {{
        digPill.hidden = true;
        return;
      }}
      const range = selection.getRangeAt(0);
      const fragment = selection.toString().trim();
      const passage = passageOf(range.startContainer);
      if (fragment.length < 4 || !passage) {{
        digPill.hidden = true;
        return;
      }}
      const insightSlot = (range.startContainer.nodeType === 1
        ? range.startContainer : range.startContainer.parentElement);
      pendingFragment = fragment;
      pendingPassage = passage;
      pendingInsightId = insightSlot && insightSlot.closest('.slot-insight')
        ? insightSlot.closest('.slot-insight').dataset.insightId : null;
      const rect = range.getBoundingClientRect();
      digPill.style.top = Math.max(6, rect.top - 42) + 'px';
      digPill.style.left = (rect.left + rect.width / 2) + 'px';
      digPill.hidden = false;
    }}

    let pillTimer = null;
    document.addEventListener('selectionchange', () => {{
      clearTimeout(pillTimer);
      pillTimer = setTimeout(refreshPill, 130);
    }});
    window.addEventListener('scroll', () => {{ digPill.hidden = true; }}, {{ passive: true }});

    digPill.addEventListener('mousedown', (event) => event.preventDefault());
    digPill.addEventListener('click', () => {{
      const fragment = pendingFragment;
      const passage = pendingPassage;
      const insightId = pendingInsightId;
      digPill.hidden = true;
      window.getSelection().removeAllRanges();
      if (fragment && passage) openDig(fragment, passage, insightId);
    }});

    // Tapping a hinted phrase auto-selects it (guidance), then the pill offers dig.
    function autoSelectPhrase(slot) {{
      const fill = slot.querySelector('.slot-fill') || slot;
      const range = document.createRange();
      range.selectNodeContents(fill);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      refreshPill();
    }}

    document.getElementById('briefing').addEventListener('click', (event) => {{
      if (event.target.closest('.dig')) return;
      const insight = event.target.closest('.slot-insight');
      if (insight) {{
        event.preventDefault();
        autoSelectPhrase(insight);
        return;
      }}
      const target = event.target.closest('.slot-source, .cut');
      if (target && target.dataset.sourceKey) {{
        event.preventDefault();
        openSheet(sourceSheet(target.dataset.sourceKey), target);
      }}
    }});
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
