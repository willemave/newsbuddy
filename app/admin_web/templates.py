"""Jinja environment for the server-rendered admin web UI."""

import time
from pathlib import Path
from typing import Any

import markdown
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals["static_version"] = str(int(time.time()))


def markdown_filter(text: Any) -> Markup:
    """Convert markdown text to HTML for admin diagnostics."""
    if not text:
        return Markup("")
    md = markdown.Markdown(
        extensions=[
            "extra",
            "codehilite",
            "toc",
            "nl2br",
            "smarty",
        ]
    )
    html = md.convert(str(text))
    return Markup(html)


templates.env.filters["markdown"] = markdown_filter
