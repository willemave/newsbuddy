use newsly_queue::{OwnedWorkPlan, TaskResult};

use crate::{HandlerExecution, LeaseHealth};

use super::super::extraction::ExtractionAttempt;
use super::super::model::{
    ContentFinalizationPlan, ContentMutation, ContentSnapshot, ExtractedArticle,
};
use super::super::storage::ContentBodyStoreError;
use super::ContentWorkerServices;
use super::support::{
    content_id, extraction_deadline, extraction_failure, feed_candidates_from_metadata, request_id,
    resolve_article_url, runtime_bool, runtime_string, storage_failure, terminal_failure,
    with_finalizer,
};
use crate::content::repository::load_content_snapshot;

#[allow(clippy::too_many_lines)]
pub(super) async fn execute_process_content(
    services: &ContentWorkerServices,
    plan: &OwnedWorkPlan,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let Some(content_id) = content_id(plan) else {
        return HandlerExecution::from_result(TaskResult::fail(
            Some("process_content requires a positive content_id".to_owned()),
            false,
        ));
    };
    let snapshot = match load_content_snapshot(&services.pool, content_id).await {
        Ok(Some(snapshot)) => snapshot,
        Ok(None) => {
            return HandlerExecution::from_result(TaskResult::fail(
                Some(format!("content {content_id} does not exist")),
                false,
            ));
        }
        Err(error) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(error.to_string()), true));
        }
    };
    if snapshot.is_terminal() {
        return HandlerExecution::from_result(TaskResult::ok());
    }

    if snapshot.content_type == "podcast" {
        let finalization = ContentFinalizationPlan {
            task_id: plan.task_id,
            content_id,
            mutation: ContentMutation::PodcastHandoff,
            usage: Vec::new(),
        };
        return with_finalizer(services, TaskResult::ok(), finalization);
    }
    if !matches!(snapshot.content_type.as_str(), "article" | "news") {
        return terminal_failure(
            services,
            plan,
            content_id,
            "process_content",
            &format!(
                "Rust process_content does not support content type {:?}",
                snapshot.content_type
            ),
            "unsupported_content_type",
            Vec::new(),
        );
    }

    let target_url = resolve_article_url(&snapshot);
    let subscribe_to_feed = runtime_bool(&snapshot.content_metadata, "subscribe_to_feed");
    let preextracted = match load_preextracted_article(services, &snapshot, &target_url).await {
        Ok(article) => article,
        Err(error) => {
            return storage_failure(
                services,
                plan,
                content_id,
                "process_content",
                &error,
                Vec::new(),
            );
        }
    };

    let (article, usage) = if let Some(article) = preextracted {
        (article, Vec::new())
    } else {
        let deadline = extraction_deadline(services.extraction_timeout);
        let request_id = request_id(plan);
        let extraction = services.extraction.process_article(
            &target_url,
            &snapshot.content_type,
            &request_id,
            deadline,
        );
        tokio::pin!(extraction);
        let attempt = tokio::select! {
            attempt = &mut extraction => attempt,
            () = lease.wait_for_ownership_loss() => {
                return HandlerExecution::from_result(TaskResult::fail(
                    Some("lease ownership was lost during content extraction".to_owned()),
                    true,
                ));
            }
        };
        match attempt {
            ExtractionAttempt::Success { article, usage } => (article, usage),
            ExtractionAttempt::Failure {
                reason,
                code,
                retryable,
                usage,
            } => {
                return extraction_failure(
                    services,
                    plan,
                    content_id,
                    "process_content",
                    reason,
                    code,
                    retryable,
                    usage,
                );
            }
        }
    };

    let body = match services
        .body_store
        .stage_source(content_id, &article.body)
        .await
    {
        Ok(body) => body,
        Err(error) => {
            return storage_failure(services, plan, content_id, "process_content", &error, usage);
        }
    };
    let finalization = ContentFinalizationPlan {
        task_id: plan.task_id,
        content_id,
        mutation: ContentMutation::ProcessArticle {
            article,
            body,
            subscribe_to_feed,
        },
        usage,
    };
    with_finalizer(services, TaskResult::ok(), finalization)
}

async fn load_preextracted_article(
    services: &ContentWorkerServices,
    snapshot: &ContentSnapshot,
    target_url: &str,
) -> Result<Option<ExtractedArticle>, ContentBodyStoreError> {
    if !runtime_bool(&snapshot.content_metadata, "analyze_url_source_body_ready") {
        return Ok(None);
    }
    let pointer_body = match snapshot.source_body_pointer() {
        Some(pointer) => services.body_store.read_source(&pointer).await?,
        None => None,
    };
    let body = pointer_body.or_else(|| {
        runtime_string(&snapshot.content_metadata, "content_to_summarize")
            .or_else(|| runtime_string(&snapshot.content_metadata, "content"))
    });
    let Some(body) = body.filter(|body| !body.trim().is_empty()) else {
        return Ok(None);
    };
    Ok(Some(ExtractedArticle {
        original_url: snapshot.url.clone(),
        final_url: target_url.to_owned(),
        title: snapshot
            .title
            .clone()
            .filter(|title| !title.trim().is_empty())
            .unwrap_or_else(|| "Untitled".to_owned()),
        author: runtime_string(&snapshot.content_metadata, "author"),
        published_at: None,
        body,
        feed_candidates: feed_candidates_from_metadata(&snapshot.content_metadata),
        extraction_method: "analyze_url_source_body".to_owned(),
        warnings: Vec::new(),
        timings: Vec::new(),
        used_firecrawl: false,
    }))
}
