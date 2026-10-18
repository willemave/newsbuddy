//! Persisted approval policy and action-state validation.
use super::ShareActionRepositoryError;
use serde_json::{Map, Value};

pub(super) fn approval_policy<'a>(
    policy: &'a Map<String, Value>,
    action_name: &str,
) -> Result<&'a str, ShareActionRepositoryError> {
    let value = policy
        .get("overrides")
        .and_then(Value::as_object)
        .and_then(|overrides| overrides.get(action_name))
        .or_else(|| policy.get(action_name))
        .or_else(|| policy.get("default"))
        .and_then(Value::as_str)
        .unwrap_or("approval_required");
    if matches!(value, "auto_apply" | "approval_required" | "dry_run") {
        Ok(value)
    } else {
        Err(ShareActionRepositoryError::UnsupportedApprovalPolicy(
            value.to_owned(),
        ))
    }
}

pub(super) fn requested_action_is_approved(
    value: &str,
) -> Result<bool, ShareActionRepositoryError> {
    match value {
        "approved" => Ok(true),
        "applying" | "applied" | "awaiting_approval" | "proposed" | "rejected" | "failed" => {
            Ok(false)
        }
        other => Err(ShareActionRepositoryError::InvalidActionStatus(
            other.to_owned(),
        )),
    }
}

#[cfg(test)]
mod policy_tests {
    use super::*;

    #[test]
    fn direct_action_policy_overrides_default_and_missing_policy_requires_approval() {
        for policy in ["approval_required", "dry_run"] {
            let values = Map::from_iter([
                ("default".to_owned(), Value::from("auto_apply")),
                ("save_to_knowledge".to_owned(), Value::from(policy)),
            ]);
            assert_eq!(
                approval_policy(&values, "save_to_knowledge").expect("policy"),
                policy
            );
        }
        assert_eq!(
            approval_policy(&Map::new(), "save_to_knowledge").expect("policy"),
            "approval_required"
        );
    }
}
