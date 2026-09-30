//! Bounded batch review of category profiles, separate from story composition.
use super::{BriefingCompositionGateway, BriefingCompositionGatewayError, StructuredRunRequest};
use newsly_agent_runtime::ProviderUsage;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

const LENS_NAMING_BATCH_SYSTEM_PROMPT: &str = r"Review the complete set of active semantic news categories together.

Return JSON matching the supplied schema with exactly one result for every supplied stable_id and
no extra IDs. Preserve an existing title, deck, and routing_rule when they remain accurate. Revise
them only when the representative stories show a material change. Titles must be specific,
reader-facing, distinct from the other category titles, and at most 40 characters. Decks explain
what connects the stories in one concise sentence. routing_rule states what future stories belong
in the category and must not mention the sampling or review process. Ground every result only in
the supplied current profile, counts, and representative story summaries. Return structured output
only.";

pub const BRIEFING_LENS_NAMING_MAX_CATEGORIES: usize = 10;
pub const BRIEFING_LENS_NAMING_MAX_STORIES_PER_CATEGORY: usize = 25;
pub const BRIEFING_LENS_NAMING_MAX_INPUT_BYTES: usize = 250_000;
pub const BRIEFING_LENS_NAMING_MAX_OUTPUT_TOKENS: u64 = 2_400;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BriefingLensNamingStory {
    pub title: String,
    pub summary: String,
    pub source_name: Option<String>,
    pub published_at: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BriefingLensNamingCategory {
    pub stable_id: String,
    pub current_title: String,
    pub current_deck: String,
    pub current_routing_rule: String,
    pub story_count: usize,
    pub stories: Vec<BriefingLensNamingStory>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BriefingLensNamingBatchRequest {
    pub categories: Vec<BriefingLensNamingCategory>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BriefingLensNamingResult {
    pub stable_id: String,
    pub title: String,
    pub deck: String,
    pub routing_rule: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BriefingLensNamingBatch {
    pub categories: Vec<BriefingLensNamingResult>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GeneratedBriefingLensNamingBatch {
    pub batch: BriefingLensNamingBatch,
    pub model: String,
    pub usage: ProviderUsage,
    pub provider_response_id: Option<String>,
}

impl BriefingCompositionGateway {
    /// Reviews all active routing category names in one bounded structured Luna request.
    ///
    /// The caller owns retries; this method performs exactly one provider attempt and no
    /// structured-output validation retry.
    ///
    /// # Errors
    /// Returns an error for malformed or oversized input, provider failure, missing structured
    /// output, or a response that does not contain exactly the requested stable IDs.
    pub async fn review_lens_names(
        &self,
        request: &BriefingLensNamingBatchRequest,
    ) -> Result<GeneratedBriefingLensNamingBatch, BriefingCompositionGatewayError> {
        validate_naming_request(request)?;
        let user_prompt = serde_json::to_string_pretty(request)?;
        if user_prompt.len() > BRIEFING_LENS_NAMING_MAX_INPUT_BYTES {
            return Err(BriefingCompositionGatewayError::InvalidLensNamingBatch(
                "serialized naming input exceeds 250000 characters".to_owned(),
            ));
        }
        let outcome = self
            .run_structured_with_model(
                StructuredRunRequest {
                    feature: "briefing_lens_naming_batch",
                    system_prompt: LENS_NAMING_BATCH_SYSTEM_PROMPT,
                    user_prompt,
                    schema_name: "briefing_lens_naming_batch_v1",
                    schema: schemars::schema_for!(BriefingLensNamingBatch),
                    output_tokens: BRIEFING_LENS_NAMING_MAX_OUTPUT_TOKENS,
                    validation_retries: 0,
                },
                &self.naming_model_spec,
            )
            .await?;
        let batch = decode_naming_output(request, outcome.structured_output, &outcome.usage)?;
        Ok(GeneratedBriefingLensNamingBatch {
            batch,
            model: outcome.model_name,
            usage: outcome.usage,
            provider_response_id: outcome.provider_response_id,
        })
    }
}

fn decode_naming_output(
    request: &BriefingLensNamingBatchRequest,
    value: Option<serde_json::Value>,
    usage: &ProviderUsage,
) -> Result<BriefingLensNamingBatch, BriefingCompositionGatewayError> {
    value
        .ok_or(BriefingCompositionGatewayError::MissingStructuredOutput)
        .and_then(|value| serde_json::from_value(value).map_err(Into::into))
        .and_then(|batch| validate_naming_response(request, batch))
        .map_err(|error| BriefingCompositionGatewayError::ObservedNaming {
            reason: error.to_string(),
            usage: usage.clone(),
        })
}

fn validate_naming_request(
    request: &BriefingLensNamingBatchRequest,
) -> Result<(), BriefingCompositionGatewayError> {
    if request.categories.is_empty()
        || request.categories.len() > BRIEFING_LENS_NAMING_MAX_CATEGORIES
    {
        return Err(BriefingCompositionGatewayError::InvalidLensNamingBatch(
            "naming review requires 1..=10 categories".to_owned(),
        ));
    }
    let mut ids = BTreeSet::new();
    for category in &request.categories {
        if category.stable_id.trim().is_empty()
            || category.stable_id.chars().count() > 80
            || !ids.insert(category.stable_id.as_str())
            || category.current_title.chars().count() > 80
            || category.current_deck.chars().count() > 400
            || category.current_routing_rule.chars().count() > 400
            || category.story_count == 0
            || category.stories.is_empty()
            || category.stories.len() > BRIEFING_LENS_NAMING_MAX_STORIES_PER_CATEGORY
        {
            return Err(BriefingCompositionGatewayError::InvalidLensNamingBatch(
                "category IDs, current profiles, counts, and story samples must be unique and bounded"
                    .to_owned(),
            ));
        }
        if category.stories.iter().any(|story| {
            story.title.trim().is_empty()
                || story.title.chars().count() > 240
                || story.summary.chars().count() > 1_200
                || story
                    .source_name
                    .as_ref()
                    .is_some_and(|value| value.chars().count() > 120)
                || story
                    .published_at
                    .as_ref()
                    .is_some_and(|value| value.chars().count() > 64)
        }) {
            return Err(BriefingCompositionGatewayError::InvalidLensNamingBatch(
                "representative story fields exceed naming bounds".to_owned(),
            ));
        }
    }
    Ok(())
}

fn validate_naming_response(
    request: &BriefingLensNamingBatchRequest,
    batch: BriefingLensNamingBatch,
) -> Result<BriefingLensNamingBatch, BriefingCompositionGatewayError> {
    let expected = request
        .categories
        .iter()
        .map(|category| category.stable_id.as_str())
        .collect::<BTreeSet<_>>();
    let mut by_id = batch
        .categories
        .into_iter()
        .map(|mut result| {
            result.title = result
                .title
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ");
            result.deck = result.deck.split_whitespace().collect::<Vec<_>>().join(" ");
            result.routing_rule = result
                .routing_rule
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ");
            (result.stable_id.clone(), result)
        })
        .collect::<Vec<_>>();
    let actual = by_id
        .iter()
        .map(|(stable_id, _)| stable_id.as_str())
        .collect::<BTreeSet<_>>();
    if actual != expected || by_id.len() != expected.len() {
        return Err(BriefingCompositionGatewayError::InvalidLensNamingBatch(
            "response must contain every requested stable ID exactly once".to_owned(),
        ));
    }
    if by_id.iter().any(|(_, result)| {
        !(2..=40).contains(&result.title.chars().count())
            || !(8..=180).contains(&result.deck.chars().count())
            || !(8..=400).contains(&result.routing_rule.chars().count())
    }) {
        return Err(BriefingCompositionGatewayError::InvalidLensNamingBatch(
            "response title, deck, or routing rule is outside its bound".to_owned(),
        ));
    }
    by_id.sort_by_key(|(stable_id, _)| {
        request
            .categories
            .iter()
            .position(|category| category.stable_id == *stable_id)
            .expect("validated result ID exists")
    });
    Ok(BriefingLensNamingBatch {
        categories: by_id.into_iter().map(|(_, result)| result).collect(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejected_naming_output_preserves_observed_usage() {
        let request = BriefingLensNamingBatchRequest {
            categories: vec![naming_category("one")],
        };
        let usage = ProviderUsage {
            input_tokens: 125,
            output_tokens: 17,
            request_count: 1,
            ..Default::default()
        };
        for value in [
            None,
            Some(serde_json::json!({"categories":[]})),
            Some(serde_json::json!({"invalid":true})),
        ] {
            let error = decode_naming_output(&request, value, &usage).unwrap_err();
            assert_eq!(error.observed_usage(), Some(&usage));
        }
    }

    fn naming_category(stable_id: &str) -> BriefingLensNamingCategory {
        BriefingLensNamingCategory {
            stable_id: stable_id.to_owned(),
            current_title: "Infrastructure".to_owned(),
            current_deck: "Reporting on the systems that make public life work.".to_owned(),
            current_routing_rule: "Include reporting about public infrastructure and utilities."
                .to_owned(),
            story_count: 12,
            stories: vec![BriefingLensNamingStory {
                title: "Grid operator approves new transmission line".to_owned(),
                summary: "The project connects new generation to regional demand.".to_owned(),
                source_name: Some("Example News".to_owned()),
                published_at: Some("2026-09-27T12:00:00Z".to_owned()),
            }],
        }
    }

    #[test]
    fn naming_batch_requires_exact_ids_and_orders_results_like_input() {
        let request = BriefingLensNamingBatchRequest {
            categories: vec![naming_category("second"), naming_category("first")],
        };
        assert!(validate_naming_request(&request).is_ok());
        let batch = BriefingLensNamingBatch {
            categories: vec![
                BriefingLensNamingResult {
                    stable_id: "first".to_owned(),
                    title: "Public Systems".to_owned(),
                    deck: "Reporting on infrastructure that supports public life.".to_owned(),
                    routing_rule: "Include infrastructure, utilities, and public works reporting."
                        .to_owned(),
                },
                BriefingLensNamingResult {
                    stable_id: "second".to_owned(),
                    title: "Civic Infrastructure".to_owned(),
                    deck: "Reporting on the systems that keep cities operating.".to_owned(),
                    routing_rule: "Include reporting about civic systems and essential utilities."
                        .to_owned(),
                },
            ],
        };
        let validated = validate_naming_response(&request, batch).unwrap();
        assert_eq!(validated.categories[0].stable_id, "second");
        assert_eq!(validated.categories[1].stable_id, "first");
    }

    #[test]
    fn naming_batch_rejects_missing_and_oversized_categories() {
        let request = BriefingLensNamingBatchRequest {
            categories: vec![naming_category("one"), naming_category("two")],
        };
        let missing = BriefingLensNamingBatch {
            categories: vec![BriefingLensNamingResult {
                stable_id: "one".to_owned(),
                title: "Public Systems".to_owned(),
                deck: "Reporting on infrastructure that supports public life.".to_owned(),
                routing_rule: "Include infrastructure, utilities, and public works reporting."
                    .to_owned(),
            }],
        };
        assert!(validate_naming_response(&request, missing).is_err());
        let mut oversized = naming_category("one");
        oversized.stories = vec![oversized.stories[0].clone(); 26];
        assert!(
            validate_naming_request(&BriefingLensNamingBatchRequest {
                categories: vec![oversized],
            })
            .is_err()
        );
    }
}
