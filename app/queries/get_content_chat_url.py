"""Application query for content ChatGPT URLs."""

from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.api.common import ChatGPTUrlResponse
from app.models.content_mapper import content_to_domain
from app.repositories.content_detail_repository import get_visible_content
from app.services.content_bodies import ContentBodyVariant, get_content_body_resolver


def execute(
    db: Session,
    *,
    user_id: int,
    content_id: int,
    user_prompt: str | None,
) -> ChatGPTUrlResponse:
    """Generate a ChatGPT URL for one visible content item."""
    content = get_visible_content(db, user_id=user_id, content_id=content_id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    try:
        domain_content = content_to_domain(content)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process content metadata: {exc!s}",
        ) from exc

    resolved_body = get_content_body_resolver().resolve(
        db,
        content=content,
        variant=ContentBodyVariant.SOURCE,
    )

    prompt_parts: list[str] = []
    if user_prompt:
        prompt_parts.append("USER PROMPT:")
        prompt_parts.append(user_prompt.strip())
        prompt_parts.append("")

    prompt_parts.append(f"I'd like to discuss this {domain_content.content_type.value}:")
    prompt_parts.append(f"Title: {domain_content.display_title}")

    if domain_content.source:
        prompt_parts.append(f"Source: {domain_content.source}")
    if domain_content.publication_date:
        prompt_parts.append(f"Published: {domain_content.publication_date.strftime('%B %d, %Y')}")

    prompt_parts.append("")
    if resolved_body and resolved_body.text.strip():
        label = "Transcript" if resolved_body.kind == "transcript" else "Full Content"
        prompt_parts.append(f"{label}:")
        prompt_parts.append(resolved_body.text.strip())

    if resolved_body and resolved_body.text.strip():
        content_text = resolved_body.text.strip()
    elif domain_content.content_type.value == "podcast" and domain_content.transcript:
        prompt_parts.append("TRANSCRIPT:")
        content_text = domain_content.transcript
    elif domain_content.full_markdown:
        prompt_parts.append("ARTICLE:")
        content_text = domain_content.full_markdown
    elif domain_content.summary:
        prompt_parts.append("SUMMARY:")
        content_text = domain_content.summary
    else:
        if domain_content.structured_summary:
            prompt_parts.append("KEY POINTS:")
            if domain_content.bullet_points:
                for bullet in domain_content.bullet_points:
                    prompt_parts.append(f"• {bullet.get('text', '')}")
            if domain_content.quotes:
                prompt_parts.append("\nQUOTES:")
                for quote in domain_content.quotes:
                    prompt_parts.append(f'"{quote.get("text", "")}"')
                    if quote.get("context"):
                        prompt_parts.append(f"  - {quote['context']}")
        content_text = ""

    full_prompt = "\n".join(prompt_parts)
    if content_text:
        full_prompt += "\n" + content_text

    max_url_length = 8000
    base_url = "https://chat.openai.com/?q="
    truncated = False
    encoded_prompt = quote_plus(full_prompt)
    full_url = base_url + encoded_prompt

    if len(full_url) > max_url_length:
        truncated = True
        available_space = max_url_length - len(base_url) - 100
        context_part = "\n".join(prompt_parts)
        encoded_context = quote_plus(context_part)

        if len(encoded_context) < available_space:
            remaining_space = available_space - len(encoded_context)
            truncated_content = content_text[: remaining_space // 3]
            truncated_prompt = (
                context_part
                + "\n"
                + truncated_content
                + "\n\n[Content truncated for URL length...]"
            )
        else:
            truncated_prompt = f"Chat about: {domain_content.display_title}"
            if domain_content.source:
                truncated_prompt += f" from {domain_content.source}"

        encoded_prompt = quote_plus(truncated_prompt)
        full_url = base_url + encoded_prompt

    return ChatGPTUrlResponse(chat_url=full_url, truncated=truncated)
