use super::{
    AggregatorKey, MAX_SOURCE_RESPONSE_BYTES, NewsItemInput, ScrapeGateway, ScrapeGatewayError,
    ScrapeProviderOutcome, ScrapedItem, clean, domain_of, host_matches, news_item,
    next_element_matching, normalize_http_url, selector,
};
use chrono::Utc;
use reqwest::Url;
use scraper::Html;
use serde_json::{Value, json};
use std::collections::BTreeMap;

impl ScrapeGateway {
    pub(super) async fn fetch_brutalist(
        &self,
        demanded_topics: &[String],
    ) -> Result<ScrapeProviderOutcome, ScrapeGatewayError> {
        let mut items = Vec::new();
        let mut errors = Vec::new();
        let mut retryable_failure = false;
        let mut seen = BTreeMap::<String, usize>::new();
        let heading_selector = selector("h3, h4")?;
        let item_selector = selector("li a[href]")?;
        for topic in AggregatorKey::Brutalist
            .topics()
            .iter()
            .copied()
            .filter(|topic| demanded_topics.iter().any(|demanded| demanded == topic))
        {
            let topic_url = format!("https://brutalist.report/topic/{topic}?limit=25&hours=24");
            let bytes = match self
                .fetch_public_bytes(&topic_url, Some(MAX_SOURCE_RESPONSE_BYTES))
                .await
            {
                Ok(bytes) => bytes,
                Err(error) => {
                    retryable_failure |= error.retryable();
                    errors.push(format!("{topic}: {error}"));
                    continue;
                }
            };
            let html = match std::str::from_utf8(&bytes) {
                Ok(html) => html,
                Err(error) => {
                    errors.push(format!("{topic}: non-UTF-8 HTML: {error}"));
                    continue;
                }
            };
            append_topic_items(
                topic,
                &topic_url,
                html,
                (&heading_selector, &item_selector),
                &mut items,
                &mut seen,
            );
        }
        Ok(ScrapeProviderOutcome {
            items,
            item_errors: errors,
            retryable_failure,
        })
    }
}

fn append_topic_items(
    topic: &str,
    topic_url: &str,
    html: &str,
    selectors: (&scraper::Selector, &scraper::Selector),
    items: &mut Vec<ScrapedItem>,
    seen: &mut BTreeMap<String, usize>,
) {
    let (heading_selector, item_selector) = selectors;
    let topic_base = Url::parse(topic_url).ok();
    let document = Html::parse_document(html);
    for heading in document.select(heading_selector) {
        let source_name = clean(Some(heading.text().collect::<Vec<_>>().join(" ")))
            .map(|value| value.replace("[rss]", "").trim().to_owned());
        let Some(list) = next_element_matching(heading, "ul") else {
            continue;
        };
        for anchor in list.select(item_selector) {
            let Some(href) = anchor.value().attr("href") else {
                continue;
            };
            let Some(article_url) = topic_base
                .as_ref()
                .and_then(|base| base.join(href).ok())
                .and_then(|url| normalize_http_url(url.as_str()))
            else {
                continue;
            };
            if host_matches(&article_url, "brutalist.report") {
                continue;
            }
            if let Some(index) = seen.get(&article_url).copied() {
                if let Some(ScrapedItem::News(existing)) = items.get_mut(index)
                    && let Some(topics) =
                        existing.raw_metadata["aggregator"]["topics"].as_array_mut()
                    && !topics.iter().any(|value| value.as_str() == Some(topic))
                {
                    topics.push(Value::String(topic.to_owned()));
                }
                continue;
            }
            let Some(title) = clean(Some(anchor.text().collect::<Vec<_>>().join(" "))) else {
                continue;
            };
            let domain = domain_of(&article_url);
            let display_source = source_name.clone().or_else(|| domain.clone());
            let metadata = json!({
                "platform": "brutalist",
                "source": display_source,
                "article": {"url": article_url, "title": title, "source_domain": domain},
                "aggregator": {
                    "key": "brutalist",
                    "name": "Brutalist Report",
                    "topic": topic,
                    "topics": [topic],
                    "metadata": {"source_name": source_name, "topic_url": topic_url},
                },
                "discussion_url": null,
                "excerpt": null,
                "discovery_time": Utc::now().to_rfc3339(),
            });
            seen.insert(article_url.clone(), items.len());
            items.push(ScrapedItem::News(Box::new(news_item(NewsItemInput {
                key: AggregatorKey::Brutalist,
                article_url,
                title: Some(title),
                external_id: None,
                discussion_url: None,
                owner_user_id: None,
                published_at: None,
                raw_metadata: metadata,
            }))));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cross_listed_urls_keep_each_topic_once() {
        let heading = selector("h3, h4").unwrap();
        let item = selector("li a[href]").unwrap();
        let html = "<h3>Example Source</h3><ul><li><a href='https://example.com/story'>Headline</a></li><li><a href='https://example.com/story'>Repeated</a></li></ul>";
        let mut items = Vec::new();
        let mut seen = BTreeMap::new();
        for topic in ["science", "politics"] {
            append_topic_items(
                topic,
                &format!("https://brutalist.report/topic/{topic}"),
                html,
                (&heading, &item),
                &mut items,
                &mut seen,
            );
        }
        assert_eq!(items.len(), 1);
        let ScrapedItem::News(story) = &items[0] else {
            panic!("news expected")
        };
        assert_eq!(
            story.raw_metadata["aggregator"]["topics"],
            json!(["science", "politics"])
        );
        assert_eq!(story.raw_metadata["aggregator"]["topic"], "science");
    }
}
