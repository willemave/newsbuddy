use serde_json::{Value, json};

use super::{DETAIL_TITLE, KNOWLEDGE_TITLE};

pub(super) fn fixture_url(namespace: &str, item: &str) -> String {
    format!("https://fixtures.newsly.invalid/{namespace}/{item}")
}

pub(super) fn detail_metadata(namespace: &str) -> Value {
    longform_metadata(
        namespace,
        DETAIL_TITLE,
        "A small team can make model changes safer by treating contracts, evaluation, and release evidence as one loop.",
        "The article connects typed boundaries, representative evaluation data, and short feedback cycles. It argues that the useful unit of progress is not a model call but a validated product outcome.",
        "When evaluation becomes part of the product loop, each release leaves the next decision easier to make.",
    )
}

pub(super) fn knowledge_metadata(namespace: &str) -> Value {
    longform_metadata(
        namespace,
        KNOWLEDGE_TITLE,
        "Reliable processing starts with explicit ownership and keeps external work outside PostgreSQL transactions.",
        "These field notes cover immutable preparation DTOs, generated wire contracts, SQLx transaction boundaries, database-free extraction, and offline evaluation.",
        "Release database connections before waiting on the network.",
    )
}

fn longform_metadata(
    namespace: &str,
    title: &str,
    one_line: &str,
    overview: &str,
    quote: &str,
) -> Value {
    json!({
        "fixture_namespace": namespace,
        "source": "web",
        "content_type": "html",
        "final_url": fixture_url(namespace, "source"),
        "content": format!("# {title}\n\n{overview}\n\n## Evidence\n\n{one_line}"),
        "summary_kind": "long_structured",
        "summary_version": 1,
        "summary": {
            "title": title,
            "one_line": one_line,
            "overview": overview,
            "bullet_points": [
                {
                    "text": "Use generated contracts as the only network boundary.",
                    "category": "architecture"
                },
                {
                    "text": "Prepare immutable input, release the transaction, then finalize with a fresh fenced transaction.",
                    "category": "reliability"
                },
                {
                    "text": "Keep extraction and offline evaluation behind narrow, database-free boundaries.",
                    "category": "scope"
                }
            ],
            "quotes": [{"text": quote, "context": "Reliability field note"}],
            "topics": ["Rust", "evaluation", "typed contracts", "PostgreSQL"],
            "classification": "to_read",
            "full_markdown": format!("# {title}\n\n{overview}\n\n## Key Points\n\n- Use generated contracts.\n- Keep transactions short.\n- Keep auxiliary runtimes database-free.\n\n## Notable Quotes\n\n> {quote}")
        }
    })
}
