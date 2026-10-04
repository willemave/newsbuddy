use serde_json::{Map, Value, json};

/// Fixed `OpenRouter` routing policy for requests containing user data.
pub(crate) fn openrouter_provider_routing() -> Value {
    json!({
        "allow_fallbacks": false,
        "require_parameters": true,
        "data_collection": "deny",
        "zdr": true,
        "sort": "throughput",
    })
}

/// Fixed `OpenRouter` routing and reasoning parameters for agent requests.
pub(crate) fn openrouter_request_parameters() -> Map<String, Value> {
    json!({
        "provider": openrouter_provider_routing(),
        "reasoning": {
            "enabled": false,
            "exclude": true,
        },
    })
    .as_object()
    .expect("fixed OpenRouter request parameters are an object")
    .clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixed_request_parameters_match_the_previous_rig_serialization() {
        assert_eq!(
            Value::Object(openrouter_request_parameters()),
            json!({
                "provider": {
                    "allow_fallbacks": false,
                    "require_parameters": true,
                    "data_collection": "deny",
                    "zdr": true,
                    "sort": "throughput",
                },
                "reasoning": {
                    "enabled": false,
                    "exclude": true,
                },
            })
        );
    }

    #[test]
    fn embedding_routing_contains_only_provider_parameters() {
        let provider = openrouter_provider_routing();
        assert_eq!(provider["zdr"], json!(true));
        assert_eq!(provider["data_collection"], json!("deny"));
        assert!(provider.get("provider").is_none());
        assert!(provider.get("reasoning").is_none());
    }
}
