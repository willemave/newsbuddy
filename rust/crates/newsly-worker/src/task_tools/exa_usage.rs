use serde::Deserialize;
use serde_json::Value;

#[derive(Debug, Deserialize)]
pub(super) struct ExaSearchResponse {
    #[serde(default)]
    pub(super) results: Vec<ExaSearchRow>,
    #[serde(rename = "costDollars")]
    pub(super) cost_dollars: Option<Value>,
}

pub(super) fn exa_estimated_cost_usd(cost_dollars: Option<&Value>) -> Option<f64> {
    cost_dollars
        .and_then(|value| value.get("total"))
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite() && *value >= 0.0)
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct ExaSearchRow {
    #[serde(default)]
    pub(super) title: String,
    #[serde(default)]
    pub(super) url: String,
    pub(super) summary: Option<String>,
    pub(super) text: Option<String>,
    pub(super) published_date: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::{ExaSearchResponse, exa_estimated_cost_usd};

    #[test]
    fn exa_cost_estimate_accepts_only_finite_nonnegative_total() {
        let valid: ExaSearchResponse = serde_json::from_value(serde_json::json!({
            "results": [],
            "costDollars": {"total": 0.0042}
        }))
        .expect("valid Exa response");
        assert_eq!(
            exa_estimated_cost_usd(valid.cost_dollars.as_ref()),
            Some(0.0042)
        );

        for value in [
            serde_json::json!({}),
            serde_json::json!({"costDollars": null}),
            serde_json::json!({"costDollars": []}),
            serde_json::json!({"costDollars": {"total": -1}}),
        ] {
            let response: ExaSearchResponse =
                serde_json::from_value(value).expect("malformed estimate must not reject response");
            assert_eq!(exa_estimated_cost_usd(response.cost_dollars.as_ref()), None);
        }
    }
}
