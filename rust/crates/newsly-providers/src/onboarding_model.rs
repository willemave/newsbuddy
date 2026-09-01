use serde_json::{Map, Value, json};

pub(super) const ONBOARDING_MODEL: &str = "openai:gpt-5.6-luna";

pub(super) const AUDIO_PLAN_SYSTEM_PROMPT: &str = concat!(
    "You design onboarding discovery lanes based on a user's spoken interests. Return a concise ",
    "topic_summary, 3-6 inferred_topics, and 3-5 lanes. Each lane must include name, goal, target ",
    "(feeds, podcasts, reddit), and 2-4 web search queries. Queries must be varied and specific: ",
    "each query should be a compact search phrase (5-10 words) with concrete keywords tied to the ",
    "lane goal, and avoid repeating the same wording pattern. Include at least one reddit lane. ",
    "Return structured output only. Infer only topics directly supported by the narration, plus at ",
    "most 1-2 clearly adjacent concepts; do not add unsupported niches. Feed queries must seek ",
    "durable, recurring sources such as RSS feeds, newsletters, publications, journals, research ",
    "institutes, think tanks, or recurring analysis—not one-off articles. Podcast queries must seek ",
    "shows, series, or feeds—not individual episodes or 'best podcast' listicles. For Reddit, name ",
    "a specific subreddit only when confident it is real, using site:reddit.com/r/<community>; ",
    "otherwise use a broad Reddit topic-discovery query. Keep lanes non-overlapping and give each ",
    "lane a distinct discovery purpose. Across the whole plan, deliberately cover different source ",
    "archetypes and viewpoints where applicable: practitioner, academic/research, institutional, ",
    "independent, and contrasting perspectives. Encode that source archetype in the query; format ",
    "differences alone do not count as diversity. Every lane must add distinct discovery value."
);

pub(super) fn onboarding_provider_parameters() -> Map<String, Value> {
    Map::from_iter([
        ("reasoning".to_owned(), json!({"effort": "low"})),
        (
            "service_tier".to_owned(),
            Value::String("priority".to_owned()),
        ),
        ("store".to_owned(), Value::Bool(false)),
    ])
}
