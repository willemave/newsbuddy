use super::{EnqueueRequest, Map, SourcePlan, SourcePlanKind, TaskType, Value};

// All entrypoints, including old durable "all" payloads, converge here before HTTP work.
pub(super) fn isolated_scrape_requests(
    plans: &[SourcePlan],
    run_id: Option<i64>,
) -> Vec<EnqueueRequest> {
    let mut requests = Vec::new();
    for plan in plans {
        let owners: Vec<(Option<i64>, Option<i64>)> = match &plan.kind {
            SourcePlanKind::Feed(targets) => targets
                .iter()
                .map(|t| (Some(t.config_id), Some(t.user_id)))
                .collect(),
            SourcePlanKind::Reddit(targets) => targets
                .iter()
                .map(|t| (Some(t.config_id), Some(t.user_id)))
                .collect(),
            _ => vec![(None, None)],
        };
        for (config_id, user_id) in owners {
            let mut child = EnqueueRequest::new(TaskType::Scrape);
            let mut payload =
                Map::from_iter([("sources".to_owned(), serde_json::json!([plan.source]))]);
            if let Some(id) = config_id {
                payload.insert("config_id".to_owned(), Value::from(id));
            }
            if let Some(id) = run_id {
                payload.insert("first_edition_run_id".to_owned(), Value::from(id));
            }
            child.payload = Some(payload);
            child.owner_user_id = user_id;
            child.dedupe = Some(true);
            child.dedupe_key = Some(format!("scrape:{}:{config_id:?}:{run_id:?}", plan.source));
            requests.push(child);
        }
    }
    requests
}
