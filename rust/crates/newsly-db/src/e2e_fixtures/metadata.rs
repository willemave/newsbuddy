use serde_json::{Value, json};

use super::{DETAIL_TITLE, KNOWLEDGE_TITLE};

pub(super) fn fixture_url(namespace: &str, item: &str) -> String {
    format!("https://fixtures.newsly.invalid/{namespace}/{item}")
}

pub(super) fn detail_metadata(namespace: &str) -> Value {
    let title = DETAIL_TITLE;
    let one_line = "A small team can make model changes safer by treating contracts, evaluation, and release evidence as one loop.";
    let overview = "The article connects typed boundaries, representative evaluation data, and short feedback cycles. It argues that the useful unit of progress is not a model call but a validated product outcome.";
    let quote = "When evaluation becomes part of the product loop, each release leaves the next decision easier to make.";
    json!({
        "fixture_namespace": namespace,
        "source": "web",
        "content_type": "html",
        "final_url": fixture_url(namespace, "source"),
        "content": format!("# {title}\n\n{overview}\n\n## Evidence\n\n{one_line}"),
        "summary_kind": "longform_artifact",
        "summary_version": 1,
        "summary": {
            "title": title,
            "one_line": one_line,
            "ask": "understand",
            "artifact": {
                "type": "findings",
                "payload": {
                    "overview": overview,
                    "takeaway": "Keep contracts, evaluation, and release evidence in one loop.",
                    "key_points": [{"heading": "Typed boundaries", "content": "Use generated contracts as the only network boundary."}],
                    "quotes": [{"text": quote, "attribution": "Reliability field note"}],
                    "extras": {"evidence": ["Prepare immutable input and finalize through a fresh fenced transaction."]}
                }
            },
            "feed_preview": {
                "title": title,
                "one_line": one_line,
                "artifact_type": "findings",
                "preview_bullets": ["Keep contracts and evaluation together."],
                "reason_to_read": "Make model changes safer."
            }
        }
    })
}

pub(super) fn knowledge_metadata(namespace: &str) -> Value {
    let title = KNOWLEDGE_TITLE;
    let one_line = "Reliable processing starts with explicit ownership and keeps external work outside PostgreSQL transactions.";
    let overview = "These field notes cover immutable preparation DTOs, generated wire contracts, SQLx transaction boundaries, database-free extraction, and offline evaluation.";
    let quote = "Release database connections before waiting on the network.";
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
