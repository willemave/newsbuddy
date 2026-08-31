use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use utoipa::ToSchema;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum LlmTaskActionStatus {
    Proposed,
    AwaitingApproval,
    Approved,
    Applying,
    Applied,
    Rejected,
    Failed,
    Cancelled,
}

impl LlmTaskActionStatus {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Proposed => "proposed",
            Self::AwaitingApproval => "awaiting_approval",
            Self::Approved => "approved",
            Self::Applying => "applying",
            Self::Applied => "applied",
            Self::Rejected => "rejected",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        }
    }
}

impl TryFrom<&str> for LlmTaskActionStatus {
    type Error = String;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        match value {
            "proposed" => Ok(Self::Proposed),
            "awaiting_approval" => Ok(Self::AwaitingApproval),
            "approved" => Ok(Self::Approved),
            "applying" => Ok(Self::Applying),
            "applied" => Ok(Self::Applied),
            "rejected" => Ok(Self::Rejected),
            "failed" => Ok(Self::Failed),
            "cancelled" => Ok(Self::Cancelled),
            other => Err(format!("unsupported LLM task action status {other:?}")),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum LlmTaskApprovalPolicy {
    AutoApply,
    ApprovalRequired,
    DryRun,
}

impl TryFrom<&str> for LlmTaskApprovalPolicy {
    type Error = String;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        match value {
            "auto_apply" => Ok(Self::AutoApply),
            "approval_required" => Ok(Self::ApprovalRequired),
            "dry_run" => Ok(Self::DryRun),
            other => Err(format!("unsupported LLM task approval policy {other:?}")),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct LlmTaskActionResponse {
    pub id: i64,
    pub llm_task_id: i64,
    pub action_name: String,
    pub action_status: LlmTaskActionStatus,
    pub approval_policy: LlmTaskApprovalPolicy,
    pub approval_required: bool,
    #[serde(default)]
    pub action_input: Map<String, Value>,
    #[serde(default)]
    pub action_result: Map<String, Value>,
    pub rationale: Option<String>,
    pub idempotency_key: Option<String>,
    pub approved_by_user_id: Option<i64>,
    pub error_message: Option<String>,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub created_at: DateTime<Utc>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub approved_at: Option<DateTime<Utc>>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub started_at: Option<DateTime<Utc>>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub completed_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct LlmTaskActionListResponse {
    #[serde(default)]
    pub actions: Vec<LlmTaskActionResponse>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct LlmTaskActionRejectRequest {
    pub reason: Option<String>,
}
