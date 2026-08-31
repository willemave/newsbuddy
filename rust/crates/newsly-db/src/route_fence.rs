use newsly_domain::RuntimeOwner;
use sqlx::{Postgres, Transaction};
use thiserror::Error;

/// Locks and validates the owner/version row inside the mutation transaction.
///
/// A request already counted by the gateway may finish while a transition is preparing. Promotion
/// cannot update the shared-locked row until this transaction commits, while the replica cannot
/// acknowledge its drained barrier until the request returns.
pub async fn verify_route_write_fence(
    transaction: &mut Transaction<'_, Postgres>,
    operation_id: &str,
    expected_owner: RuntimeOwner,
    expected_version: i64,
) -> Result<(), RouteWriteFenceError> {
    let actual = sqlx::query_as::<_, RouteFenceRow>(
        r#"
        SELECT active_owner, active_version
        FROM runtime_ownership
        WHERE resource_kind = 'route_group' AND resource_key = $1
        FOR SHARE
        "#,
    )
    .bind(operation_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some(actual) = actual else {
        return Err(RouteWriteFenceError::Missing(operation_id.to_owned()));
    };
    if actual.active_owner != expected_owner.as_str() || actual.active_version != expected_version {
        return Err(RouteWriteFenceError::Stale {
            operation_id: operation_id.to_owned(),
            expected_owner,
            expected_version,
            actual_owner: actual.active_owner,
            actual_version: actual.active_version,
        });
    }
    Ok(())
}

#[derive(Debug, sqlx::FromRow)]
struct RouteFenceRow {
    active_owner: String,
    active_version: i64,
}

#[derive(Debug, Error)]
pub enum RouteWriteFenceError {
    #[error("route ownership database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("route ownership is missing for {0}")]
    Missing(String),
    #[error(
        "stale route fence for {operation_id}: expected {expected_owner}@{expected_version}, found {actual_owner}@{actual_version}"
    )]
    Stale {
        operation_id: String,
        expected_owner: RuntimeOwner,
        expected_version: i64,
        actual_owner: String,
        actual_version: i64,
    },
}
