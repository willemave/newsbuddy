use newsly_providers::ScrapedItem;
use serde_json::Value;

pub(super) fn brutalist_budget_keys(item: &ScrapedItem) -> Vec<(String, String)> {
    let ScrapedItem::News(news) = item else {
        return Vec::new();
    };
    if news.platform != "brutalist" {
        return Vec::new();
    }
    let metadata = &news.raw_metadata["aggregator"];
    let section = metadata["metadata"]["source_name"]
        .as_str()
        .unwrap_or_default()
        .to_owned();
    let topics = metadata["topics"].as_array().map_or_else(
        || metadata["topic"].as_str().into_iter().collect(),
        |topics| topics.iter().filter_map(Value::as_str).collect::<Vec<_>>(),
    );
    topics
        .into_iter()
        .map(|topic| (topic.to_owned(), section.clone()))
        .collect()
}
