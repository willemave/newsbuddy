"""
Shared LLM prompt generation for content summarization.
Used by both OpenAI and Anthropic LLM services to ensure consistency.
"""

from app.services.prompt_library import load_prompt, render_prompt

SPECIALIZED_EDITORIAL_PROMPT_TYPES = {
    "editorial_podcast",
    "editorial_substack",
    "editorial_twitter",
    "editorial_research",
    "editorial_github",
}


# ruff: noqa: E501
def generate_summary_prompt(
    content_type: str, max_bullet_points: int, max_quotes: int
) -> tuple[str, str]:
    """
    Generate optimized prompts for LLM summarization with caching support.

    This function creates prompts structured for efficient caching:
    - System message contains static instructions (cached by LLM providers)
    - User message template is for variable content (not cached)

    Args:
        content_type: Type of content ("article", "podcast", "news", "hackernews", "interleaved", "long_bullets", "editorial_narrative", "editorial_podcast", "editorial_substack", "editorial_twitter", "editorial_research", "editorial_github")
        max_bullet_points: Maximum number of bullet points to generate
        max_quotes: Maximum number of quotes to extract

    Returns:
        Tuple of (system_message, user_message_template)
        The user_message_template contains a {content} placeholder.
    """
    normalized_type = content_type.lower()
    if normalized_type == "article":
        normalized_type = "editorial_narrative"
    elif normalized_type == "podcast":
        normalized_type = "editorial_podcast"
    if normalized_type == "news":
        normalized_type = "news"
    content_type = normalized_type
    news_key_point_limit = max(1, min(max_bullet_points, 4))
    news_key_point_min = min(2, news_key_point_limit)
    editorial_key_point_limit = max(1, min(max_bullet_points, 6))
    editorial_key_point_min = min(4, editorial_key_point_limit)
    editorial_quote_limit = max(1, min(max_quotes, 2)) if max_quotes else 0

    if content_type == "hackernews":
        system_message = render_prompt(
            "summarization/hackernews#system",
            max_bullet_points=max_bullet_points,
            max_quotes=max_quotes,
        )
        user_message = load_prompt("summarization/hackernews#user")

    elif content_type == "news":
        system_message = render_prompt(
            "summarization/news#system",
            news_key_point_min=news_key_point_min,
            news_key_point_limit=news_key_point_limit,
        )
        user_message = load_prompt("summarization/news#user")

    elif content_type == "discussion_summary":
        system_message = load_prompt("summarization/discussion_summary#system")
        user_message = load_prompt("summarization/discussion_summary#user")

    elif content_type == "discussion_summary_merge":
        system_message = load_prompt("summarization/discussion_summary_merge#system")
        user_message = load_prompt("summarization/discussion_summary_merge#user")

    elif content_type in SPECIALIZED_EDITORIAL_PROMPT_TYPES:
        system_message = render_prompt(
            f"summarization/{content_type}#system",
            editorial_quote_limit=editorial_quote_limit,
            editorial_key_point_min=editorial_key_point_min,
            editorial_key_point_limit=editorial_key_point_limit,
        )
        user_message = load_prompt(f"summarization/{content_type}#user")

    elif content_type == "editorial_narrative":
        system_message = render_prompt(
            "summarization/editorial_narrative#system",
            editorial_quote_limit=editorial_quote_limit,
            editorial_key_point_min=editorial_key_point_min,
            editorial_key_point_limit=editorial_key_point_limit,
        )
        user_message = load_prompt("summarization/editorial_narrative#user")

    elif content_type == "long_bullets":
        system_message = render_prompt(
            "summarization/long_bullets#system",
            max_bullet_points=max_bullet_points,
        )
        user_message = load_prompt("summarization/long_bullets#user")

    elif content_type == "structured":
        system_message = render_prompt(
            "summarization/structured#system",
            max_bullet_points=max_bullet_points,
            max_quotes=max_quotes,
        )
        user_message = load_prompt("summarization/structured#user")

    else:
        # Interleaved format v2: key points, quotes list, topic bullets
        system_message = render_prompt(
            "summarization/interleaved#system",
            max_quotes=max_quotes,
        )
        user_message = load_prompt("summarization/interleaved#user")

    return system_message, user_message


def creativity_to_style_hints(creativity: int) -> str:
    """
    Map creativity level (1-10) to style guidance for tweet generation.

    Args:
        creativity: Integer 1-10 indicating desired creativity level

    Returns:
        String with style hints for the LLM prompt
    """
    if creativity <= 3:
        # Low creativity: Journalist/analyst voice
        return load_prompt("tweets/style_low")

    elif creativity <= 7:
        # Medium creativity: Thoughtful commentator
        return load_prompt("tweets/style_medium")

    else:
        # High creativity: Viral-worthy takes
        return load_prompt("tweets/style_high")


def length_to_char_range(length: str) -> tuple[int, int]:
    """
    Map length preference to character range.

    Args:
        length: "short", "medium", or "long"

    Returns:
        Tuple of (min_chars, max_chars)
    """
    ranges = {
        "short": (100, 180),
        "medium": (180, 280),
        "long": (280, 400),
    }
    return ranges.get(length, (180, 280))


def get_tweet_generation_prompt(
    creativity: int,
    user_message: str | None = None,
    length: str = "medium",
) -> tuple[str, str]:
    """
    Generate prompts for tweet generation from article/news content.

    Args:
        creativity: Integer 1-10 indicating desired creativity level
        user_message: Optional user guidance for tweet generation
        length: Tweet length preference ("short", "medium", "long")

    Returns:
        Tuple of (system_message, user_message_template)
        The user_message_template contains placeholders for content details.
    """
    style_hints = creativity_to_style_hints(creativity)
    min_chars, max_chars = length_to_char_range(length)

    system_message = render_prompt(
        "tweets/generation#system",
        min_chars=min_chars,
        max_chars=max_chars,
        creativity=creativity,
        style_hints=style_hints,
    )

    # Build user message with optional guidance
    user_guidance = ""
    if user_message:
        user_guidance = f"\n\nUser guidance: {user_message}"

    user_template = render_prompt("tweets/generation#user", user_guidance=user_guidance)

    return system_message, user_template
