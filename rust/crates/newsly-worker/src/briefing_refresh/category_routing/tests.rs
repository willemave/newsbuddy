use super::*;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

fn categories() -> Vec<NewsCategory> {
    vec![NewsCategory {
        key: "news-science".into(),
        title: "Science".into(),
        deck: "Research findings".into(),
        routing_rule: None,
    }]
}

fn decision(key: Option<&str>, probability: f64) -> NewsCategoryDecision {
    NewsCategoryDecision {
        selected_key: key.map(str::to_owned),
        selected_probability: probability,
        usage: ProviderUsage {
            request_count: 1,
            input_tokens: 100,
            ..ProviderUsage::default()
        },
        cost_usd: 0.000_004_2,
        generation_id: "gen-category".into(),
    }
}

#[tokio::test]
async fn phase_deadline_drains_dispatched_prefix_and_leaves_the_rest_unstarted() {
    let inputs: Vec<_> = (0..9)
        .map(|i| NewsCategoryInput {
            title: i.to_string(),
            summary: "Evidence".into(),
            key_points: vec![],
            categories: categories(),
        })
        .collect();
    let deadline = tokio::time::Instant::now() + std::time::Duration::from_millis(5);
    let calls = AtomicUsize::new(0);
    let routes = route_bounded(
        &inputs,
        || tokio::time::Instant::now() >= deadline,
        |input| {
            let calls = &calls;
            async move {
                calls.fetch_add(1, Ordering::SeqCst);
                tokio::time::sleep(std::time::Duration::from_millis(20)).await;
                CategoryRoute::Existing(input.title.clone())
            }
        },
    )
    .await;
    assert_eq!(calls.load(Ordering::SeqCst), 4);
    assert_eq!(
        routes,
        (0..4)
            .map(|i| CategoryRoute::Existing(i.to_string()))
            .collect::<Vec<_>>()
    );
}

#[test]
fn routing_uses_selected_probability_and_preserves_general_news() {
    let categories = categories();
    // The boundary and observed Mars-orbiter / observatory scores retain Science.
    for probability in [0.80, 0.84, 0.92] {
        assert_eq!(
            decision_route(Ok(decision(Some("news-science"), probability)), &categories),
            CategoryRoute::Existing("news-science".into())
        );
    }
    for probability in [0.79, 0.50, f64::NAN] {
        assert_eq!(
            decision_route(Ok(decision(Some("news-science"), probability)), &categories),
            CategoryRoute::General
        );
    }
    assert_eq!(
        decision_route(Ok(decision(None, 0.80)), &categories),
        CategoryRoute::Discover
    );
    // The ambiguous App Store story selected other at 0.50, below discovery.
    for probability in [0.79, 0.50] {
        assert_eq!(
            decision_route(Ok(decision(None, probability)), &categories),
            CategoryRoute::General
        );
    }
    assert_eq!(
        decision_route(
            Ok(decision(Some("another-users-category"), 1.0)),
            &categories
        ),
        CategoryRoute::General
    );
    assert_eq!(
        decision_route(Err(NewsCategoryGatewayError::Unavailable), &categories),
        CategoryRoute::General
    );
}

#[test]
fn metering_survives_rejected_answers_and_unknown_cost_is_not_zero() {
    let valid = observation(&Ok(decision(None, 0.95))).unwrap();
    assert_eq!(valid.provider_reported_cost_usd, Some(0.000_004_2));
    assert!(valid.usage_is_observed);
    let invalid = Err(NewsCategoryGatewayError::MeteredProtocol {
        code: "route_mismatch",
        usage: valid.usage.clone(),
        cost_usd: 0.001,
        generation_id: Some("billed-rejection".into()),
    });
    let metered = observation(&invalid).unwrap();
    assert_eq!(metered.provider_reported_cost_usd, Some(0.001));
    assert_eq!(metered.response_id.as_deref(), Some("billed-rejection"));
    let unknown = observation(&Err(NewsCategoryGatewayError::Provider(
        reqwest::StatusCode::TOO_MANY_REQUESTS,
    )))
    .unwrap();
    assert!(!unknown.usage_is_observed);
    assert_eq!(unknown.provider_reported_cost_usd, None);
    assert!(observation(&Err(NewsCategoryGatewayError::Unavailable)).is_none());
    assert!(
        observation(&Err(NewsCategoryGatewayError::InvalidInput(
            "evidence_length"
        )))
        .is_none()
    );
}

#[tokio::test]
async fn cancellation_drains_four_inflight_calls_without_starting_more() {
    let inputs: Vec<_> = (0..9)
        .map(|i| NewsCategoryInput {
            title: i.to_string(),
            summary: "Evidence".into(),
            key_points: vec![],
            categories: categories(),
        })
        .collect();
    let cancelled = AtomicBool::new(false);
    let calls = AtomicUsize::new(0);
    let completed = AtomicUsize::new(0);
    let barrier = tokio::sync::Barrier::new(PARALLELISM);
    let routes = tokio::time::timeout(
        std::time::Duration::from_secs(1),
        route_bounded(
            &inputs,
            || cancelled.load(Ordering::SeqCst),
            |input| {
                let cancelled = &cancelled;
                let calls = &calls;
                let completed = &completed;
                let barrier = &barrier;
                async move {
                    calls.fetch_add(1, Ordering::SeqCst);
                    barrier.wait().await;
                    cancelled.store(true, Ordering::SeqCst);
                    tokio::task::yield_now().await;
                    completed.fetch_add(1, Ordering::SeqCst);
                    CategoryRoute::Existing(input.title.clone())
                }
            },
        ),
    )
    .await
    .unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 4);
    assert_eq!(completed.load(Ordering::SeqCst), 4);
    assert_eq!(
        routes,
        (0..4)
            .map(|i| CategoryRoute::Existing(i.to_string()))
            .collect::<Vec<_>>()
    );
    let routes = route_bounded(
        &inputs,
        || true,
        |_| async { panic!("cancelled before dispatch") },
    )
    .await;
    assert!(routes.is_empty());
}

#[sqlx::test(migrations = false)]
async fn recorded_category_cost_survives_without_any_product_publication(pool: sqlx::PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('category-cost','category-cost@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
    let mut task = newsly_queue::EnqueueRequest::new(newsly_queue::TaskType::BriefingRefresh);
    task.owner_user_id = Some(user);
    task.payload = serde_json::json!({"user_id": user}).as_object().cloned();
    let task_id = newsly_queue::QueueKernel::new(pool.clone())
        .enqueue(task)
        .await
        .unwrap();
    let recorder = AgentUsageRecorder::new(
        pool.clone(),
        crate::agent_usage::AgentUsageAttribution {
            operation: "briefing.route_category".into(),
            source: "queue".into(),
            task_id,
            content_id: None,
            session_id: None,
            message_id: None,
            user_id: Some(user),
        },
    );
    let observed = observation(&Ok(decision(None, 0.95))).unwrap();
    recorder.record(observed.clone()).unwrap();
    recorder.record(observed).unwrap();
    recorder.finish().await;
    let rows: Vec<(f64,i32)> = sqlx::query_as("SELECT cost_usd,input_tokens FROM vendor_usage_records WHERE task_id::bigint=$1 AND feature='briefing_category_assignment'").bind(task_id).fetch_all(&pool).await.unwrap();
    assert_eq!(rows, vec![(0.000_004_2, 100)]);
    let lenses: i64 =
        sqlx::query_scalar("SELECT count(*) FROM briefing_lenses WHERE user_id::bigint=$1")
            .bind(user)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(lenses, 0);
}

#[tokio::test]
async fn freed_slots_refill_while_the_first_request_waits_and_results_keep_source_order() {
    let inputs: Vec<_> = (0..10)
        .map(|i| NewsCategoryInput {
            title: i.to_string(),
            summary: "Evidence".into(),
            key_points: vec![],
            categories: categories(),
        })
        .collect();
    let permits: Vec<_> = (0..inputs.len())
        .map(|_| tokio::sync::Semaphore::new(0))
        .collect();
    let active = AtomicUsize::new(0);
    let maximum = AtomicUsize::new(0);
    let (started, mut starts) = tokio::sync::mpsc::unbounded_channel();
    let work = route_bounded(
        &inputs,
        || false,
        |input| {
            let permits = &permits;
            let active = &active;
            let maximum = &maximum;
            let started = &started;
            async move {
                let index = input.title.parse::<usize>().unwrap();
                let count = active.fetch_add(1, Ordering::SeqCst) + 1;
                maximum.fetch_max(count, Ordering::SeqCst);
                started.send(index).unwrap();
                permits[index].acquire().await.unwrap().forget();
                active.fetch_sub(1, Ordering::SeqCst);
                CategoryRoute::Existing(input.title.clone())
            }
        },
    );
    let control = async {
        let mut first = Vec::new();
        for _ in 0..PARALLELISM {
            first.push(starts.recv().await.unwrap());
        }
        first.sort_unstable();
        assert_eq!(first, vec![0, 1, 2, 3]);
        assert_eq!(active.load(Ordering::SeqCst), PARALLELISM);
        // A hung first call must not hold a completed sibling's next slot hostage.
        permits[2].add_permits(1);
        assert_eq!(starts.recv().await, Some(4));
        assert_eq!(active.load(Ordering::SeqCst), PARALLELISM);
        for permit in &permits {
            permit.add_permits(1);
        }
    };
    let (routes, ()) = tokio::time::timeout(std::time::Duration::from_secs(2), async {
        tokio::join!(work, control)
    })
    .await
    .unwrap();
    assert_eq!(maximum.load(Ordering::SeqCst), PARALLELISM);
    assert_eq!(active.load(Ordering::SeqCst), 0);
    assert_eq!(
        routes,
        (0..10)
            .map(|i| CategoryRoute::Existing(i.to_string()))
            .collect::<Vec<_>>()
    );
}
