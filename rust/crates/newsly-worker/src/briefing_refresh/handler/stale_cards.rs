//! Best-effort recomposition of live cards written by an older card prompt.

use futures_util::stream::{self, StreamExt};
use newsly_providers::BriefingCompositionGateway;

use super::{
    BriefingRefreshExecutionError, ComposedUnit, CompositionContext, CompositionUnit, compose_unit,
    composition_fingerprint, composition_request,
};

/// Recomposes up to `STALE_CARD_RECOMPOSE_LIMIT` cards written by an older card prompt,
/// newest first, skipping any whose unchanged input is cooling down so failing cards cannot
/// crowd out the rest. Best effort: a failure deselects only that card's batch, so it keeps
/// its previous text, and starts a cooldown even for transport failures so it is not retried
/// on every sweep.
pub(super) async fn recompose_stale_cards(
    gateway: &BriefingCompositionGateway,
    candidates: Vec<(usize, CompositionUnit)>,
    compaction_selected: &mut [bool],
    parallelism: usize,
    context: CompositionContext<'_>,
) -> Result<Vec<ComposedUnit>, BriefingRefreshExecutionError> {
    if candidates.is_empty() {
        return Ok(Vec::new());
    }
    let fingerprints = candidates
        .iter()
        .map(|(_, unit)| composition_fingerprint(gateway.model_spec(), &composition_request(unit)))
        .collect::<Vec<_>>();
    let cooling_down = newsly_db::briefing_attempts::cooling_down_fingerprints(
        context.pool,
        context.user_id,
        &fingerprints,
    )
    .await?;
    let selected = candidates
        .into_iter()
        .zip(fingerprints)
        .filter(|(_, fingerprint)| !cooling_down.contains(fingerprint))
        .take(newsly_db::STALE_CARD_RECOMPOSE_LIMIT);
    let max_attempts = super::super::config::MAX_COMPOSE_ATTEMPTS;
    let results = stream::iter(
        selected.map(|((batch_index, unit), fingerprint)| async move {
            let result = compose_unit(gateway, unit, max_attempts, context).await;
            (batch_index, fingerprint, result)
        }),
    )
    .buffer_unordered(parallelism)
    .collect::<Vec<_>>()
    .await;
    let mut composed = Vec::with_capacity(results.len());
    let mut skipped = 0_usize;
    for (batch_index, fingerprint, result) in results {
        match stale_card_outcome(result)? {
            StaleCardOutcome::Composed(unit) => {
                compaction_selected[batch_index] = true;
                composed.push(*unit);
            }
            StaleCardOutcome::Skipped { cool_down, error } => {
                skipped += 1;
                if cool_down {
                    newsly_db::briefing_attempts::record_attempt(
                        context.pool,
                        context.task_id,
                        context.user_id,
                        context.fence,
                        uuid::Uuid::new_v4(),
                        &fingerprint,
                        "stale_card_transport_cooldown",
                        None,
                        true,
                    )
                    .await?;
                }
                tracing::warn!(
                    task_id = context.task_id,
                    user_id = context.user_id,
                    code = "briefing_stale_card_recompose_skipped",
                    error = %error,
                    "Kept a card composed by an older prompt"
                );
            }
        }
    }
    if !composed.is_empty() || skipped > 0 {
        tracing::info!(
            task_id = context.task_id,
            user_id = context.user_id,
            recomposed = composed.len(),
            skipped,
            "Recomposed cards written by an older prompt"
        );
    }
    Ok(composed)
}

enum StaleCardOutcome {
    Composed(Box<ComposedUnit>),
    Skipped {
        /// Transport failures write no cooldown inside `compose_unit`; start one here.
        cool_down: bool,
        error: BriefingRefreshExecutionError,
    },
}

/// Classifies one stale-card composition. Composition and transport failures are skipped;
/// accounting failures and broken plans still fail the refresh.
fn stale_card_outcome(
    result: Result<ComposedUnit, BriefingRefreshExecutionError>,
) -> Result<StaleCardOutcome, BriefingRefreshExecutionError> {
    match result {
        Ok(unit) => Ok(StaleCardOutcome::Composed(Box::new(unit))),
        Err(error @ BriefingRefreshExecutionError::Transport(_)) => Ok(StaleCardOutcome::Skipped {
            cool_down: true,
            error,
        }),
        Err(error @ BriefingRefreshExecutionError::Composition { .. }) => {
            Ok(StaleCardOutcome::Skipped {
                cool_down: false,
                error,
            })
        }
        Err(error) => Err(error),
    }
}

#[cfg(test)]
mod tests {
    use newsly_db::{BriefingRefreshLens, ComposedBriefingSegment};

    use super::super::CompositionUnitKind;
    use super::*;

    fn composed_unit(ordinal: usize, batch_index: usize) -> ComposedUnit {
        ComposedUnit {
            ordinal,
            kind: CompositionUnitKind::Compaction { batch_index },
            segment: ComposedBriefingSegment {
                lens: BriefingRefreshLens {
                    id: 1,
                    key: "articles".to_owned(),
                    tier: "longform".to_owned(),
                    title: "Articles".to_owned(),
                    deck: String::new(),
                    position: 0,
                },
                blocks: serde_json::json!([]),
                markdown_raw: String::new(),
                narration_text: String::new(),
                source_keys: vec![format!("content:{batch_index}")],
                event_groups: Vec::new(),
                model: "test".to_owned(),
                prompt_version: newsly_db::BRIEFING_CARD_PROMPT_VERSION.to_owned(),
            },
        }
    }

    fn composition_failure() -> BriefingRefreshExecutionError {
        BriefingRefreshExecutionError::Composition {
            lens_key: "articles".to_owned(),
            attempts: 3,
            message: "rejected".to_owned(),
        }
    }

    #[test]
    fn stale_card_failures_are_skipped_and_transport_failures_cool_down() {
        assert!(matches!(
            stale_card_outcome(Ok(composed_unit(0, 0))),
            Ok(StaleCardOutcome::Composed(_))
        ));
        assert!(matches!(
            stale_card_outcome(Err(composition_failure())),
            Ok(StaleCardOutcome::Skipped {
                cool_down: false,
                ..
            })
        ));
        assert!(matches!(
            stale_card_outcome(Err(BriefingRefreshExecutionError::Transport(
                "timeout".to_owned()
            ))),
            Ok(StaleCardOutcome::Skipped {
                cool_down: true,
                ..
            })
        ));
    }

    #[test]
    fn broken_plans_and_accounting_failures_still_fail_the_refresh() {
        assert!(
            stale_card_outcome(Err(BriefingRefreshExecutionError::MissingPendingSource(
                "content:1".to_owned()
            )))
            .is_err()
        );
        assert!(
            stale_card_outcome(Err(BriefingRefreshExecutionError::Accounting(
                sqlx::Error::RowNotFound
            )))
            .is_err()
        );
    }
}
