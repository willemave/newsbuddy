"""
Shared LLM prompt generation for content summarization.
Used by both OpenAI and Anthropic LLM services to ensure consistency.
"""

from typing import TypedDict


class SpecializedEditorialTemplateConfig(TypedDict):
    source_name: str
    template: str
    source_fields: list[str]
    source_guidelines: list[str]
    user_message: str


SPECIALIZED_EDITORIAL_TEMPLATE_CONFIGS: dict[str, SpecializedEditorialTemplateConfig] = {
    "editorial_podcast": {
        "source_name": "a podcast transcript or episode",
        "template": "podcast",
        "source_fields": [
            "thesis: the central claim or frame of the episode",
            "speakers: named speakers or roles",
            "notable_arguments: important arguments or perspectives",
            "practical_takeaways: operational or practical takeaways",
        ],
        "source_guidelines": [
            "Capture the guest or host thesis, not just the topic area.",
            "Use speakers to distinguish viewpoints when the conversation includes disagreement or contrast.",
            "Prefer practical takeaways, execution advice, and concrete examples over banter or scene-setting.",
        ],
        "user_message": "Podcast Transcript:\n\n{content}",
    },
    "editorial_substack": {
        "source_name": "a newsletter or essay",
        "template": "substack",
        "source_fields": [
            "thesis: the author's central thesis",
            "supporting_arguments: major supporting arguments",
            "evidence: evidence, examples, or references the author uses",
            "implications: what follows if the thesis is right",
        ],
        "source_guidelines": [
            "Treat the piece as an argument: identify the thesis, the support, and the implications.",
            "Separate the author's framing from the strongest evidence they actually provide.",
            "Call out omissions or weak support in key_points when relevant.",
        ],
        "user_message": "Essay Content:\n\n{content}",
    },
    "editorial_twitter": {
        "source_name": "an X/Twitter post or linked thread",
        "template": "twitter",
        "source_fields": [
            "primary_claim: the main claim or assertion being made",
            "evidence: evidence directly supplied in the post or linked context",
            "caveats: important missing context, uncertainty, or caveats",
            "linked_context: key context from links, screenshots, or embedded references",
        ],
        "source_guidelines": [
            "Distinguish clearly between what is asserted, what is evidenced, and what remains uncertain.",
            "If the post links to a richer source, prioritize the linked source over the rhetoric of the post.",
            "Keep the narrative tighter and less essay-like than for a long article.",
        ],
        "user_message": "Post and Linked Context:\n\n{content}",
    },
    "editorial_research": {
        "source_name": "a research paper or technical PDF",
        "template": "research",
        "source_fields": [
            "hypothesis: the central research question, thesis, or hypothesis",
            "methods: method, dataset, experiment, or evidence base",
            "arguments: main claims or results supported by the work",
            "limitations: important limitations, confounds, or scope boundaries",
            "implications: practical or research implications",
        ],
        "source_guidelines": [
            "Prioritize hypothesis, methods, results, and limitations over rhetorical framing.",
            "Do not overstate conclusions beyond what the evidence supports.",
            "When possible, preserve quantitative findings, evaluation conditions, and important caveats.",
        ],
        "user_message": "Research Content:\n\n{content}",
    },
    "editorial_github": {
        "source_name": "a GitHub repository or technical project documentation",
        "template": "github",
        "source_fields": [
            "overview: what the project is for and what problem it solves",
            "architecture: core subsystems, design choices, or structural patterns",
            "interfaces: CLI, API, SDK, workflow, or integration surface",
            "setup_constraints: important dependency, setup, or environment constraints",
            "maturity_signals: maintenance, documentation, tests, adoption, or stability signals",
            "best_fit_use_cases: who should use it and for what",
        ],
        "source_guidelines": [
            "Summarize the repo like a technical product: purpose, architecture, interfaces, and adoption signals.",
            "Call out setup friction, hidden dependencies, or maturity limits instead of treating the README as marketing.",
            "Prefer what a developer needs to know before using or extending the project.",
        ],
        "user_message": "Repository or Documentation Content:\n\n{content}",
    },
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
    news_key_point_limit = max(1, min(max_bullet_points, 3))
    news_key_point_min = min(2, news_key_point_limit)
    editorial_key_point_limit = max(1, min(max_bullet_points, 6))
    editorial_key_point_min = min(4, editorial_key_point_limit)
    editorial_quote_limit = max(1, min(max_quotes, 2)) if max_quotes else 0

    if content_type == "hackernews":
        system_message = f"""You are an expert content analyst. Analyze HackerNews discussions, which
include linked article content (if any) and community comments. Provide a structured
summary that captures both the main content and key insights from the discussion.

Important:
- Generate a descriptive title that describes the article in detail.
- There may be technical terms in the content, please don't make any spelling errors.
- Extract actual quotes from both the article and notable comments
- Make bullet points capture insights from BOTH content and discussion
- Include {max_bullet_points} bullet points that blend article + comment insights
- Include up to {max_quotes} notable quotes (can be from article or comments)
- IMPORTANT: Each quote must be at least 10 characters long - do not include short snippets
- For quotes from comments, use format "HN user [username]" as context
- Include 3-8 relevant topic tags
- Generate 3-5 thought-provoking questions that help readers think critically about the content
- Identify 2-4 counter-arguments or alternative perspectives mentioned in comments or implied by the content
- Add a "classification" field with either "to_read" or "skip"
- Add a special section in the overview about the HN community response
- Set "full_markdown" to include the article content AND the comments


Questions Guidelines:
- Questions should prompt critical thinking about implications, limitations, or applications
- Draw from both the article content and HN discussion
- Focus on "what if", "how might", "what are the implications" style questions

Counter Arguments Guidelines:
- Look for dissenting opinions or skeptical viewpoints in HN comments
- Identify assumptions that could be challenged
- Include technical critiques or alternative approaches mentioned
- If no strong counter-arguments exist, you may leave this list empty

Classification Guidelines:
- Consider both article quality AND discussion quality
- High-quality technical discussions should be "to_read" even if article is average
- Set to "skip" if both article and comments lack substance"""

        user_message = "Analyze this content and discussion:\n\n{content}"

    elif content_type == "news":
        system_message = f"""You are an expert news editor. Read provided article content and any additional
aggregator context, then produce a concise summary matching the provided structured output schema.

Field guidance:
- title: direct factual headline, <=95 characters; rewrite weak, generic, or source-label headlines.
- article_url: canonical article URL when available.
- key_points: include {news_key_point_min}-{news_key_point_limit} self-contained bullets, <=120 characters each.
- summary: optional one-sentence overview, <=180 characters; use null if key_points cover it.
- classification: use "to_read" for substantial signal and "skip" for low-value or promotional content.

Rules:
- Focus on why the story matters, not just what happened.
- Prefer omission over padding. Do not add background, caveats, or second-order implications unless the source states them directly.
- There may be technical terms in the content, please don't make any spelling errors.
- Keep each key point self-contained, concrete, and free of markdown or numbering.
- Prefer action verbs, quantitative figures, and clear implications.
- If the content is low-value or promotional, set classification to "skip" but still
  surface truthful key points.
- Never include markdown, topics, quotes, or any extra fields.
"""

        user_message = "Article & Aggregator Context:\n\n{content}"

    elif content_type == "daily_news_rollup":
        system_message = """You are an expert news editor preparing a single daily rollup from many source stories.

Produce a rollup matching the provided structured output schema.

Field guidance:
- title: descriptive headline, <=110 characters, capturing the day's main themes.
- summary: required two-sentence overview explaining the day at a glance, <=500 characters.
- key_points: one bullet per distinct major topic, story cluster, or consequential development.

Guidelines:
- Cover the major themes of the day, not just the single top story.
- Emit as many bullets as needed to cover the important distinct topics.
- Merge near-duplicate stories into one broader bullet when they tell the same story.
- Stop adding bullets when additional bullets would be redundant.
- Prefer concrete entities, numbers, dates, and implications over vague phrasing.
- Most bullets should stay as a single sentence.
- When a provided comment quote materially sharpens a point, you may append one extra line inside that same key_points string.
- Use at most one quote per bullet, and only on a few bullets where it clearly improves the summary.
- Keep quote lines short, high-signal, and verbatim to the provided comment context; do not invent commenters or wording.
- Treat minor promotional, meta, or duplicative items as supporting context unless they materially change the day.
- Keep each bullet self-contained, concrete, and free of markdown or numbering.
- Never include article URLs, classifications, topics, or extra fields. Do not emit a separate quotes field.
"""

        user_message = "Daily News Rollup Context:\n\n{content}"

    elif content_type in SPECIALIZED_EDITORIAL_TEMPLATE_CONFIGS:
        config = SPECIALIZED_EDITORIAL_TEMPLATE_CONFIGS[content_type]
        source_fields_text = "\n".join(
            f"- source_details.{field}" for field in config["source_fields"]
        )
        source_guidelines_text = "\n".join(
            f"- {guideline}" for guideline in config["source_guidelines"]
        )
        system_message = f"""You are an expert editor writing an information-dense narrative summary for {config["source_name"]}.

Produce a summary matching the provided structured output schema.

Field guidance:
- title: descriptive title, <=110 characters.
- editorial_narrative: one compact thesis-led paragraph, 90-150 words.
- quotes: include exactly {editorial_quote_limit} direct quotes when the source contains usable quotes; otherwise include the strongest available quote.
- key_points: include {editorial_key_point_min}-{editorial_key_point_limit} non-overlapping points, each <=22 words.
- source_details: use template "{config["template"]}" and keep every value short, accurate, and source-grounded.
{source_fields_text}
- classification: use "to_read" for substantial insight or original reporting; otherwise use "skip".

Rules:
- Start the first paragraph with the core thesis or the most consequential takeaway.
- Use a second paragraph only when the source has two distinct claims.
- Keep every sentence concrete: named entities, numbers, dates, constraints, implications.
- Avoid filler, repetition, and generic framing.
- Each key point must be specific and evidence-oriented, not vague advice.
- There may be technical terms in the content; preserve exact spelling.
{source_guidelines_text}
- Never include markdown or any fields outside this schema.

Classification Guidelines:
- Set classification to "skip" if the content lacks depth, evidence, or practical signal.
- Set classification to "to_read" if the content delivers substantial insight, original reporting, or high-signal analysis.
"""
        user_message = config["user_message"]

    elif content_type == "editorial_narrative":
        system_message = f"""You are an expert editor writing an information-dense narrative summary.

Produce a summary matching the provided structured output schema.

Field guidance:
- title: descriptive title, <=110 characters.
- editorial_narrative: one compact thesis-led paragraph, 90-150 words.
- quotes: include exactly {editorial_quote_limit} direct quotes when the source contains usable quotes; otherwise include the strongest available quote.
- key_points: include {editorial_key_point_min}-{editorial_key_point_limit} non-overlapping points, each <=22 words.
- classification: use "to_read" for substantial insight or high-signal analysis; otherwise use "skip".

Rules:
- Start the first paragraph with the core thesis or the most consequential takeaway.
- Use a second paragraph only when the source has two distinct claims.
- Keep every sentence concrete: named entities, numbers, dates, constraints, implications.
- Avoid filler, repetition, and generic framing.
- Each key point must be specific and evidence-oriented, not vague advice.
- There may be technical terms in the content; preserve exact spelling.
- Never include markdown or any fields outside this schema.

Classification Guidelines:
- Set classification to "skip" if the content lacks depth, evidence, or practical signal.
- Set classification to "to_read" if the content delivers substantial insight, original reporting, or high-signal analysis.
"""

        user_message = "Content:\n\n{content}"

    elif content_type == "long_bullets":
        system_message = f"""You are an expert content analyst. Produce an exhaustive bullet-first summary
where each bullet can expand into a brief detail and supporting quotes. Match the
provided structured output schema.

Field guidance:
- title: descriptive title, <=110 characters.
- points: target 10-20 bullets; include up to {max_bullet_points} when needed for completeness.
- point text: one concrete sentence.
- point detail: 2-3 sentences with evidence, numbers, names, and implications.
- quotes: 1-3 verbatim quotes per point that support the claim.
- classification: use "to_read" for substantive information or analysis; otherwise use "skip".

Guidelines:
- Quotes must be verbatim from the content; avoid duplication across points.
- There may be technical terms in the content, please don't make any spelling errors.
- Never include markdown or extra fields.

Classification Guidelines:
- Set classification to "skip" if the content:
  * Is light on content or seems like marketing/promotional material
  * Is general mainstream news without depth or unique insights
  * Lacks substantive information or analysis
  * Appears to be clickbait or sensationalized
- Set classification to "to_read" if the content:
  * Contains in-depth analysis or unique insights
  * Provides technical or specialized knowledge
  * Offers original research or investigation
  * Has educational or informative value"""

        user_message = "Content:\n\n{content}"

    elif content_type == "structured":
        system_message = f"""You are an expert content analyst. Return a structured summary matching
the provided output schema.

Field guidance:
- title: descriptive title, <=110 characters.
- overview: brief paragraph with the main argument or finding.
- bullet_points: include 6-{max_bullet_points} high-signal points with optional category labels.
- quotes: include up to {max_quotes} non-trivial direct quotes.
- topics: concise topic labels.
- questions: critical questions prompted by the content.
- counter_arguments: credible objections or alternative perspectives.
- classification: use "to_read" for substantive information or analysis; otherwise use "skip".
- full_markdown: readable markdown form of the source when available.

Guidelines:
- Keep details specific with names, numbers, and implications.
- There may be technical terms in the content, please don't make any spelling errors.
- Never include markdown outside schema fields or any extra fields.
"""

        user_message = "Content:\n\n{content}"

    else:
        # Interleaved format v2: key points, quotes list, topic bullets
        system_message = f"""You are an expert content analyst creating interleaved summaries that
surface top key points first, then expand each topic with focused bullets, and
separate longer quotes into their own list. Match the provided structured output schema.

Field guidance:
- title: descriptive title, <=110 characters.
- hook: 2-3 sentence opening with the main story.
- key_points: 3-5 highest-signal items only; no quotes inside key_points.
- topics: cover all major themes; each topic must have 2-3 focused bullets.
- quotes: include up to {max_quotes} longer quotes that add signal.
- takeaway: 2-3 sentence final synthesis.
- classification: use "to_read" for substantive information or analysis; otherwise use "skip".

Guidelines:
- Avoid quote duplication.
- Use concrete numbers, names, and data points when available.
- There may be technical terms in the content, please don't make any spelling errors.
- Never include markdown or extra fields.

Classification Guidelines:
- Set classification to "skip" if the content:
  * Is light on content or seems like marketing/promotional material
  * Is general mainstream news without depth or unique insights
  * Lacks substantive information or analysis
  * Appears to be clickbait or sensationalized
- Set classification to "to_read" if the content:
  * Contains in-depth analysis or unique insights
  * Provides technical or specialized knowledge
  * Offers original research or investigation
  * Has educational or informative value"""

        user_message = "Content:\n\n{content}"

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
        return """Write like a tech journalist reporting facts.
- Lead with the most important data point or finding
- No emojis, no rhetorical questions
- Stick to verified claims from the article
- Neutral tone - let the facts speak"""

    elif creativity <= 7:
        # Medium creativity: Thoughtful commentator
        return """Write like a thoughtful industry insider sharing an interesting find.
- Can add one opinion or insight beyond the facts
- Sparing emoji use (max 1, only if natural)"""

    else:
        # High creativity: Viral-worthy takes
        return """Write like a respected thought leader with a strong point of view.
- Lead with a surprising angle, contrarian take, or pattern interrupt
- Use tension and curiosity
- Wit and memorable phrasing encouraged - make it quotable
- Can be provocative but substantive - no empty controversy
- Emojis allowed (max 2) only if they add punch"""


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

    system_message = f"""You are an expert social media writer for a tech/AI/startup audience.
Your task is to generate exactly 3 tweet suggestions with their corresponding probabilities about the provided content.

Core Guidelines:
- Each tweet must be {min_chars}-{max_chars} characters (strict limit: {max_chars} max)
- Be concise and impactful with one main idea per tweet
- Start with a strong hook that grabs attention
- Conversational tone
- ALWAYS avoid colons (:) and emdashs (—)
- No clickbait. Provide genuine value and insight
- Frame as "great article, this stood out to me" style when appropriate
- Include the article URL when provided
- Self-contained. Tweet should be understandable even without clicking the link
- No markdown, no multi-tweet threads, no numbered lists
- Focus on a single key insight, not a summary
- Avoid rhetorical questions

Style for this creativity level ({creativity}/10):
{style_hints}

Output Format:
Return ONLY valid JSON matching this exact structure:
{{
  "suggestions": [
    {{"id": 1, "text": "tweet text here", "style_label": "descriptive label like 'insightful' or 'provocative'"}},
    {{"id": 2, "text": "tweet text here", "style_label": "label"}},
    {{"id": 3, "text": "tweet text here", "style_label": "label"}}
  ]
}}

Do not include markdown code fences, commentary, or any text outside the JSON."""

    # Build user message with optional guidance
    user_guidance = ""
    if user_message:
        user_guidance = f"\n\nUser guidance: {user_message}"

    user_template = (
        """Content to tweet about:

Title: {title}
Source: {source} ({platform})
URL: {url}

Summary:
{summary}

Key Points:
{key_points}

Notable Quotes:
{quotes}

Thought-Provoking Questions:
{questions}

Counter-Arguments/Alternative Perspectives:
{counter_arguments}"""
        + user_guidance
    )

    return system_message, user_template
