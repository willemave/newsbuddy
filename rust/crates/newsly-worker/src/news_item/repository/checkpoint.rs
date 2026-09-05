use super::{
    ModelUsageWrite, NewsRepositoryError, NewsSnapshot, QueueKernel, json, load_news_row,
    persist_model_usage, source_fingerprint,
};

/// Save the paid summary and its usage before optional relation work can fail.
pub(in crate::news_item) async fn checkpoint_summary(
    queue: &QueueKernel,
    claim: &newsly_queue::ClaimedTask,
    snapshot: &NewsSnapshot,
    summary: &newsly_providers::NewsSummary,
    usage: &[ModelUsageWrite],
) -> Result<bool, NewsRepositoryError> {
    let Some(mut fence) = queue
        .begin_fenced_finalization(claim, &newsly_queue::TaskResult::ok(), 0)
        .await?
    else {
        return Ok(false);
    };
    let tx = fence.transaction_mut();
    let Some(row) = load_news_row(tx, snapshot.id, true).await? else {
        return Ok(false);
    };
    if source_fingerprint(&row) != snapshot.fingerprint {
        return Ok(false);
    }
    let checkpoint = json!({"fingerprint": snapshot.fingerprint, "summary": summary});
    sqlx::query("UPDATE news_items SET raw_metadata = (COALESCE(raw_metadata, '{}'::json)::jsonb || jsonb_build_object('summary_checkpoint', $2::jsonb))::json WHERE id::bigint = $1")
        .bind(snapshot.id).bind(checkpoint).execute(&mut **tx).await?;
    for write in usage {
        persist_model_usage(tx, claim.id, snapshot.owner_user_id, write).await?;
    }
    Ok(fence.checkpoint().await?)
}
