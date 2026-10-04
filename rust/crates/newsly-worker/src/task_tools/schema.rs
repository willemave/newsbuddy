use newsly_agent_runtime::ToolDefinition;
use schemars::{JsonSchema, schema_for};
use serde::Deserialize;

use crate::knowledge_tools::KnowledgeReferenceInput;

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct ExecuteBashInput {
    pub(super) command: String,
    pub(super) timeout_seconds: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct WriteFileInput {
    pub(super) path: String,
    pub(super) text: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct EditFileInput {
    pub(super) path: String,
    pub(super) old_text: String,
    pub(super) new_text: String,
    #[serde(default)]
    pub(super) replace_all: bool,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct ReadFileInput {
    pub(super) path: String,
    pub(super) max_bytes: Option<usize>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct ListFilesInput {
    pub(super) path: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct WebSearchInput {
    pub(super) query: String,
    pub(super) num_results: Option<usize>,
    pub(super) category: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct SearchKnowledgeInput {
    pub(super) query: String,
    pub(super) limit: Option<usize>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct WriteKnowledgeInput {
    pub(super) references: Vec<KnowledgeReferenceInput>,
    pub(super) directory: Option<String>,
}

pub(super) fn tool_definition<T: JsonSchema>(name: &str, description: &str) -> ToolDefinition {
    ToolDefinition {
        name: name.to_owned(),
        description: description.to_owned(),
        input_schema: schema_for!(T),
    }
}
