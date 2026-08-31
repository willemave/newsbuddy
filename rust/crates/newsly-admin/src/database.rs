//! Bounded, database-enforced read-only `PostgreSQL` inspection for operators.

use std::collections::BTreeMap;
use std::fmt::Write as _;

use chrono::{DateTime, NaiveDate, NaiveDateTime, NaiveTime, Utc};
use futures_util::TryStreamExt as _;
use serde::Serialize;
use serde_json::{Value, json};
use sqlx::postgres::PgRow;
use sqlx::types::Json;
use sqlx::{AssertSqlSafe, Column, Executor, FromRow, PgPool, Row, SqlSafeStr, TypeInfo, ValueRef};
use thiserror::Error;
use uuid::Uuid;

const MAX_SCHEMA_TABLES: i64 = 1_000;
const MAX_QUERY_ROWS: i64 = 1_000;
const MAX_QUERY_COLUMNS: usize = 100;
const MAX_SCHEMA_COLUMNS: usize = 10_000;
const MAX_SCHEMA_COLUMN_QUERY_LIMIT: i64 = 10_001;
const MAX_SQL_BYTES: usize = 100_000;
const MAX_CELL_BYTES: usize = 32 * 1_024;
const MAX_RESULT_BYTES: usize = 2 * 1_024 * 1_024;
const REDACTED: &str = "<redacted>";
const TRUNCATED_CELL: &str = "<cell omitted: exceeds 32768-byte operator limit>";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, FromRow)]
struct TableCatalogRow {
    table_name: String,
    table_kind: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TableSummary {
    pub name: String,
    pub kind: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TableList {
    pub generated_at: DateTime<Utc>,
    pub schema: String,
    pub limit: i64,
    pub count: usize,
    pub truncated: bool,
    pub tables: Vec<TableSummary>,
}

impl TableList {
    pub fn render_text(&self) -> String {
        let mut text = format!(
            "PostgreSQL schema {}: {} table(s){}",
            self.schema,
            self.count,
            if self.truncated { " (truncated)" } else { "" }
        );
        for table in &self.tables {
            let _ = write!(text, "\n- {} ({})", table.name, table.kind);
        }
        text
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, FromRow)]
struct SchemaColumnRow {
    table_name: String,
    ordinal_position: i32,
    column_name: String,
    data_type: String,
    udt_name: String,
    is_nullable: bool,
    column_default: Option<String>,
    is_primary_key: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SchemaColumn {
    pub ordinal_position: i32,
    pub name: String,
    pub data_type: String,
    pub postgres_type: String,
    pub nullable: bool,
    pub default: Option<String>,
    pub primary_key: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TableSchema {
    pub table: String,
    pub columns: Vec<SchemaColumn>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SchemaDescription {
    pub generated_at: DateTime<Utc>,
    pub schema: String,
    pub requested_table: Option<String>,
    pub limit: i64,
    pub count: usize,
    pub truncated: bool,
    pub tables: Vec<TableSchema>,
}

impl SchemaDescription {
    pub fn render_text(&self) -> String {
        let mut text = format!(
            "PostgreSQL schema {}: {} described table(s){}",
            self.schema,
            self.count,
            if self.truncated { " (truncated)" } else { "" }
        );
        for table in &self.tables {
            let _ = write!(text, "\n{}:", table.table);
            for column in &table.columns {
                let nullable = if column.nullable { " nullable" } else { "" };
                let primary_key = if column.primary_key {
                    " primary-key"
                } else {
                    ""
                };
                let _ = write!(
                    text,
                    "\n- {}: {}{}{}",
                    column.name, column.postgres_type, nullable, primary_key
                );
            }
        }
        text
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct QueryColumn {
    pub name: String,
    pub postgres_type: String,
    pub redacted: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct QueryResult {
    pub sql: String,
    pub limit: i64,
    pub byte_limit: usize,
    pub columns: Vec<String>,
    pub column_metadata: Vec<QueryColumn>,
    pub row_count: usize,
    pub rows: Vec<BTreeMap<String, Value>>,
    pub redacted: bool,
    pub truncated: bool,
    pub truncation_reason: Option<String>,
}

impl QueryResult {
    pub fn render_text(&self) -> String {
        let mut text = format!(
            "{} row(s){}; columns: {}",
            self.row_count,
            if self.truncated { " (truncated)" } else { "" },
            self.column_metadata
                .iter()
                .map(|column| format!("{}:{}", column.name, column.postgres_type))
                .collect::<Vec<_>>()
                .join(", ")
        );
        for row in &self.rows {
            let rendered = serde_json::to_string(row).unwrap_or_else(|_| "{}".to_owned());
            let _ = write!(text, "\n{rendered}");
        }
        text
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ExplainResult {
    pub sql: String,
    pub format: &'static str,
    pub analyzed: bool,
    pub plan: Value,
}

impl ExplainResult {
    pub fn render_text(&self) -> String {
        serde_json::to_string_pretty(&self.plan).unwrap_or_else(|_| "<invalid plan>".to_owned())
    }
}

/// Lists ordinary tables and views from one `PostgreSQL` schema.
///
/// # Errors
///
/// Returns a validation error for an invalid schema or limit and a database error when the
/// catalog query fails.
pub async fn list_tables(
    pool: &PgPool,
    schema: &str,
    limit: i64,
) -> Result<TableList, DatabaseOperatorError> {
    let schema = validate_catalog_name(schema, "schema")?;
    let limit_usize = validate_limit(limit, MAX_SCHEMA_TABLES)?;
    let mut rows = sqlx::query_as::<_, TableCatalogRow>(
        r"
        SELECT
            table_name,
            CASE table_type
                WHEN 'BASE TABLE' THEN 'table'
                WHEN 'VIEW' THEN 'view'
                ELSE LOWER(table_type)
            END AS table_kind
        FROM information_schema.tables
        WHERE table_schema = $1
        ORDER BY table_name
        LIMIT $2
        ",
    )
    .bind(schema)
    .bind(limit + 1)
    .fetch_all(pool)
    .await?;
    let truncated = rows.len() > limit_usize;
    rows.truncate(limit_usize);
    let tables = rows
        .into_iter()
        .map(|row| TableSummary {
            name: row.table_name,
            kind: row.table_kind,
        })
        .collect::<Vec<_>>();
    Ok(TableList {
        generated_at: Utc::now(),
        schema: schema.to_owned(),
        limit,
        count: tables.len(),
        truncated,
        tables,
    })
}

/// Describes table columns and primary-key membership from `PostgreSQL` catalogs.
///
/// # Errors
///
/// Returns a validation error for invalid names or limits, [`DatabaseOperatorError::TableNotFound`]
/// for a requested missing table, and a database error when catalog inspection fails.
pub async fn describe_schema(
    pool: &PgPool,
    schema: &str,
    table: Option<&str>,
    limit: i64,
) -> Result<SchemaDescription, DatabaseOperatorError> {
    let schema = validate_catalog_name(schema, "schema")?;
    let table = table
        .map(|value| validate_catalog_name(value, "table"))
        .transpose()?;
    let limit_usize = validate_limit(limit, MAX_SCHEMA_TABLES)?;
    let mut rows = sqlx::query_as::<_, SchemaColumnRow>(
        r"
        WITH selected_tables AS (
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = $1
              AND ($2::text IS NULL OR table_name = $2)
            ORDER BY table_name
            LIMIT $3
        )
        SELECT
            columns.table_name,
            columns.ordinal_position,
            columns.column_name,
            columns.data_type,
            columns.udt_name,
            columns.is_nullable = 'YES' AS is_nullable,
            columns.column_default,
            EXISTS (
                SELECT 1
                FROM information_schema.table_constraints AS constraints
                JOIN information_schema.key_column_usage AS key_columns
                  ON key_columns.constraint_catalog = constraints.constraint_catalog
                 AND key_columns.constraint_schema = constraints.constraint_schema
                 AND key_columns.constraint_name = constraints.constraint_name
                 AND key_columns.table_schema = constraints.table_schema
                 AND key_columns.table_name = constraints.table_name
                WHERE constraints.constraint_type = 'PRIMARY KEY'
                  AND constraints.table_schema = columns.table_schema
                  AND constraints.table_name = columns.table_name
                  AND key_columns.column_name = columns.column_name
            ) AS is_primary_key
        FROM information_schema.columns AS columns
        JOIN selected_tables USING (table_name)
        WHERE columns.table_schema = $1
        ORDER BY columns.table_name, columns.ordinal_position
        LIMIT $4
        ",
    )
    .bind(schema)
    .bind(table)
    .bind(limit + 1)
    .bind(MAX_SCHEMA_COLUMN_QUERY_LIMIT)
    .fetch_all(pool)
    .await?;
    if let Some(table) = table
        && rows.is_empty()
    {
        return Err(DatabaseOperatorError::TableNotFound {
            schema: schema.to_owned(),
            table: table.to_owned(),
        });
    }

    let mut truncated = rows.len() > MAX_SCHEMA_COLUMNS;
    rows.truncate(MAX_SCHEMA_COLUMNS);
    let mut grouped = BTreeMap::<String, Vec<SchemaColumn>>::new();
    let mut result_bytes = 0_usize;
    for row in rows {
        let column = SchemaColumn {
            ordinal_position: row.ordinal_position,
            name: row.column_name,
            data_type: row.data_type,
            postgres_type: row.udt_name,
            nullable: row.is_nullable,
            default: row.column_default.map(bound_text),
            primary_key: row.is_primary_key,
        };
        let column_bytes = serde_json::to_vec(&column)?.len();
        if result_bytes.saturating_add(column_bytes) > MAX_RESULT_BYTES {
            truncated = true;
            break;
        }
        result_bytes += column_bytes;
        grouped.entry(row.table_name).or_default().push(column);
    }
    truncated |= table.is_none() && grouped.len() > limit_usize;
    let tables = grouped
        .into_iter()
        .take(limit_usize)
        .map(|(table, columns)| TableSchema { table, columns })
        .collect::<Vec<_>>();
    Ok(SchemaDescription {
        generated_at: Utc::now(),
        schema: schema.to_owned(),
        requested_table: table.map(str::to_owned),
        limit,
        count: tables.len(),
        truncated,
        tables,
    })
}

/// Executes one SELECT/WITH statement with row, column, cell, byte, and time bounds.
///
/// `PostgreSQL` also enforces `READ ONLY` for the transaction, so a mutating function cannot bypass
/// the lexical statement guard.
///
/// # Errors
///
/// Returns a validation error for non-query SQL or invalid bounds and a database error when
/// `PostgreSQL` rejects or cannot decode the query.
pub async fn run_query(
    pool: &PgPool,
    sql: &str,
    limit: i64,
    unsafe_raw: bool,
) -> Result<QueryResult, DatabaseOperatorError> {
    let sql = validate_read_only_query(sql)?;
    let limit_usize = validate_limit(limit, MAX_QUERY_ROWS)?;
    let mut transaction = begin_operator_transaction(pool).await?;
    let description = (&mut *transaction)
        .describe(AssertSqlSafe(sql.clone()).into_sql_str())
        .await?;
    let columns = description
        .columns()
        .iter()
        .map(|column| QueryColumn {
            name: column.name().to_owned(),
            postgres_type: column.type_info().name().to_owned(),
            redacted: !unsafe_raw && is_sensitive_key(column.name()),
        })
        .collect::<Vec<_>>();
    if columns.len() > MAX_QUERY_COLUMNS {
        return Err(DatabaseOperatorError::TooManyColumns {
            count: columns.len(),
            maximum: MAX_QUERY_COLUMNS,
        });
    }

    let mut rows = Vec::new();
    let mut result_bytes = 0_usize;
    let mut truncated = false;
    let mut truncation_reason = None;
    {
        // SAFETY: `validate_read_only_query` lexes the complete operator-supplied statement,
        // rejects statement separators and every PostgreSQL write/control statement class, and
        // the database transaction independently enforces READ ONLY.
        let mut stream = sqlx::query(AssertSqlSafe(sql.clone())).fetch(&mut *transaction);
        while let Some(row) = stream.try_next().await? {
            if rows.len() == limit_usize {
                truncated = true;
                truncation_reason = Some("row_limit".to_owned());
                break;
            }
            let rendered = render_row(&row, &columns, unsafe_raw)?;
            let row_bytes = serde_json::to_vec(&rendered)?.len();
            if result_bytes.saturating_add(row_bytes) > MAX_RESULT_BYTES {
                truncated = true;
                truncation_reason = Some("byte_limit".to_owned());
                break;
            }
            result_bytes += row_bytes;
            rows.push(rendered);
        }
    }
    transaction.rollback().await?;

    Ok(QueryResult {
        sql,
        limit,
        byte_limit: MAX_RESULT_BYTES,
        columns: columns.iter().map(|column| column.name.clone()).collect(),
        column_metadata: columns,
        row_count: rows.len(),
        rows,
        redacted: !unsafe_raw,
        truncated,
        truncation_reason,
    })
}

/// Returns `PostgreSQL`'s JSON plan for one SELECT/WITH statement without executing it.
///
/// # Errors
///
/// Returns a validation error for non-query SQL and a database error when `PostgreSQL` cannot plan
/// the query.
pub async fn explain_query(
    pool: &PgPool,
    sql: &str,
) -> Result<ExplainResult, DatabaseOperatorError> {
    let sql = validate_read_only_query(sql)?;
    let explain_sql = format!("EXPLAIN (FORMAT JSON, ANALYZE FALSE, VERBOSE FALSE) {sql}");
    let mut transaction = begin_operator_transaction(pool).await?;
    // SAFETY: the dynamic suffix passed the same complete lexical validation used by `run_query`;
    // this function prepends a fixed non-ANALYZE EXPLAIN clause and uses a READ ONLY transaction.
    let Json(plan) = sqlx::query_scalar::<_, Json<Value>>(AssertSqlSafe(explain_sql))
        .fetch_one(&mut *transaction)
        .await?;
    transaction.rollback().await?;
    Ok(ExplainResult {
        sql,
        format: "postgresql-json",
        analyzed: false,
        plan: bound_value(redact_value(plan)),
    })
}

async fn begin_operator_transaction(
    pool: &PgPool,
) -> Result<sqlx::Transaction<'_, sqlx::Postgres>, DatabaseOperatorError> {
    let mut transaction = pool.begin().await?;
    sqlx::query("SET TRANSACTION READ ONLY")
        .execute(&mut *transaction)
        .await?;
    sqlx::query("SET LOCAL statement_timeout = '10s'")
        .execute(&mut *transaction)
        .await?;
    sqlx::query("SET LOCAL lock_timeout = '2s'")
        .execute(&mut *transaction)
        .await?;
    sqlx::query("SET LOCAL idle_in_transaction_session_timeout = '15s'")
        .execute(&mut *transaction)
        .await?;
    Ok(transaction)
}

fn render_row(
    row: &PgRow,
    columns: &[QueryColumn],
    unsafe_raw: bool,
) -> Result<BTreeMap<String, Value>, DatabaseOperatorError> {
    columns
        .iter()
        .enumerate()
        .map(|(index, column)| {
            if column.redacted {
                return Ok((column.name.clone(), Value::String(REDACTED.to_owned())));
            }
            let value = decode_value(row, index, &column.postgres_type)?;
            let value = if unsafe_raw {
                value
            } else {
                redact_value(value)
            };
            Ok((column.name.clone(), bound_value(value)))
        })
        .collect()
}

fn decode_value(
    row: &PgRow,
    index: usize,
    postgres_type: &str,
) -> Result<Value, DatabaseOperatorError> {
    if row.try_get_raw(index)?.is_null() {
        return Ok(Value::Null);
    }
    let value = match postgres_type {
        "BOOL" => json!(row.try_get::<bool, _>(index)?),
        "INT2" => json!(row.try_get::<i16, _>(index)?),
        "INT4" => json!(row.try_get::<i32, _>(index)?),
        "INT8" => json!(row.try_get::<i64, _>(index)?),
        "FLOAT4" => finite_float_json(f64::from(row.try_get::<f32, _>(index)?)),
        "FLOAT8" => finite_float_json(row.try_get::<f64, _>(index)?),
        "TEXT" | "VARCHAR" | "BPCHAR" | "NAME" | "\"CHAR\"" => {
            Value::String(row.try_get::<String, _>(index)?)
        }
        "JSON" | "JSONB" => row.try_get::<Json<Value>, _>(index)?.0,
        "UUID" => Value::String(row.try_get::<Uuid, _>(index)?.to_string()),
        "DATE" => Value::String(row.try_get::<NaiveDate, _>(index)?.to_string()),
        "TIME" => Value::String(row.try_get::<NaiveTime, _>(index)?.to_string()),
        "TIMESTAMP" => Value::String(row.try_get::<NaiveDateTime, _>(index)?.to_string()),
        "TIMESTAMPTZ" => Value::String(row.try_get::<DateTime<Utc>, _>(index)?.to_rfc3339()),
        "BOOL[]" => serde_json::to_value(row.try_get::<Vec<bool>, _>(index)?)?,
        "INT2[]" => serde_json::to_value(row.try_get::<Vec<i16>, _>(index)?)?,
        "INT4[]" => serde_json::to_value(row.try_get::<Vec<i32>, _>(index)?)?,
        "INT8[]" => serde_json::to_value(row.try_get::<Vec<i64>, _>(index)?)?,
        "TEXT[]" | "VARCHAR[]" | "BPCHAR[]" | "NAME[]" => {
            serde_json::to_value(row.try_get::<Vec<String>, _>(index)?)?
        }
        "UUID[]" => serde_json::to_value(row.try_get::<Vec<Uuid>, _>(index)?)?,
        "JSON[]" | "JSONB[]" => serde_json::to_value(row.try_get::<Vec<Json<Value>>, _>(index)?)?,
        "BYTEA" => json!({
            "omitted": "binary",
            "byte_length": row.try_get::<Vec<u8>, _>(index)?.len(),
        }),
        unsupported => json!({
            "unsupported_postgres_type": unsupported,
            "hint": "cast this column to text in the operator query",
        }),
    };
    Ok(value)
}

fn finite_float_json(value: f64) -> Value {
    if value.is_finite() {
        json!(value)
    } else {
        Value::String(value.to_string())
    }
}

fn validate_read_only_query(sql: &str) -> Result<String, DatabaseOperatorError> {
    if sql.len() > MAX_SQL_BYTES {
        return Err(DatabaseOperatorError::SqlTooLong {
            maximum_bytes: MAX_SQL_BYTES,
        });
    }
    if sql.contains('\0') {
        return Err(DatabaseOperatorError::InvalidSql(
            "SQL must not contain NUL bytes".to_owned(),
        ));
    }
    let normalized = sql.trim();
    if normalized.is_empty() {
        return Err(DatabaseOperatorError::InvalidSql(
            "SQL must not be empty".to_owned(),
        ));
    }
    let scan = scan_sql(normalized)?;
    if scan.statement_delimiters > 1
        || (scan.statement_delimiters == 1 && scan.tokens_after_delimiter)
    {
        return Err(DatabaseOperatorError::InvalidSql(
            "only one SQL statement is allowed".to_owned(),
        ));
    }
    let first = scan.tokens.first().map(String::as_str).unwrap_or_default();
    if first != "SELECT" && first != "WITH" {
        return Err(DatabaseOperatorError::InvalidSql(
            "only SELECT or WITH queries are allowed".to_owned(),
        ));
    }
    if let Some(keyword) = scan.tokens.iter().find(|token| is_forbidden_token(token)) {
        return Err(DatabaseOperatorError::InvalidSql(format!(
            "forbidden SQL keyword {keyword}"
        )));
    }
    Ok(normalized.to_owned())
}

#[derive(Debug, Default, PartialEq, Eq)]
struct SqlScan {
    tokens: Vec<String>,
    statement_delimiters: usize,
    tokens_after_delimiter: bool,
}

fn scan_sql(sql: &str) -> Result<SqlScan, DatabaseOperatorError> {
    let bytes = sql.as_bytes();
    let mut scan = SqlScan::default();
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'\'' => {
                index = skip_quoted(bytes, index, b'\'', is_escape_string_prefix(bytes, index))?;
            }
            b'"' => {
                let (next_index, identifier) = read_quoted_identifier(bytes, index)?;
                if is_dangerous_function(&identifier) {
                    scan.tokens.push(identifier);
                }
                index = next_index;
            }
            b'-' if bytes.get(index + 1) == Some(&b'-') => {
                index = skip_line_comment(bytes, index + 2);
            }
            b'/' if bytes.get(index + 1) == Some(&b'*') => {
                index = skip_block_comment(bytes, index)?;
            }
            b'$' => {
                if let Some(delimiter) = dollar_quote_delimiter(&sql[index..]) {
                    let content_start = index + delimiter.len();
                    let remainder = &sql[content_start..];
                    let Some(end) = remainder.find(delimiter) else {
                        return Err(DatabaseOperatorError::InvalidSql(
                            "unterminated dollar-quoted string".to_owned(),
                        ));
                    };
                    index = content_start + end + delimiter.len();
                } else {
                    index += 1;
                }
            }
            b';' => {
                scan.statement_delimiters += 1;
                index += 1;
            }
            byte if byte.is_ascii_alphabetic() || byte == b'_' => {
                let start = index;
                index += 1;
                while bytes.get(index).is_some_and(|byte| {
                    byte.is_ascii_alphanumeric() || *byte == b'_' || *byte == b'$'
                }) {
                    index += 1;
                }
                if scan.statement_delimiters > 0 {
                    scan.tokens_after_delimiter = true;
                }
                scan.tokens.push(sql[start..index].to_ascii_uppercase());
            }
            _ => index += 1,
        }
    }
    Ok(scan)
}

fn skip_quoted(
    bytes: &[u8],
    start: usize,
    quote: u8,
    allow_backslash_escape: bool,
) -> Result<usize, DatabaseOperatorError> {
    let mut index = start + 1;
    while index < bytes.len() {
        if allow_backslash_escape && bytes[index] == b'\\' {
            index = (index + 2).min(bytes.len());
            continue;
        }
        if bytes[index] == quote {
            if bytes.get(index + 1) == Some(&quote) {
                index += 2;
                continue;
            }
            return Ok(index + 1);
        }
        index += 1;
    }
    Err(DatabaseOperatorError::InvalidSql(
        "unterminated quoted SQL value or identifier".to_owned(),
    ))
}

fn is_escape_string_prefix(bytes: &[u8], quote_index: usize) -> bool {
    let Some(prefix_index) = quote_index.checked_sub(1) else {
        return false;
    };
    if !matches!(bytes[prefix_index], b'e' | b'E') {
        return false;
    }
    prefix_index == 0
        || !bytes[prefix_index - 1].is_ascii_alphanumeric()
            && bytes[prefix_index - 1] != b'_'
            && bytes[prefix_index - 1] != b'$'
}

fn read_quoted_identifier(
    bytes: &[u8],
    start: usize,
) -> Result<(usize, String), DatabaseOperatorError> {
    let mut identifier = Vec::new();
    let mut index = start + 1;
    while index < bytes.len() {
        if bytes[index] == b'"' {
            if bytes.get(index + 1) == Some(&b'"') {
                identifier.push(b'"');
                index += 2;
                continue;
            }
            return Ok((
                index + 1,
                String::from_utf8_lossy(&identifier).to_ascii_uppercase(),
            ));
        }
        identifier.push(bytes[index]);
        index += 1;
    }
    Err(DatabaseOperatorError::InvalidSql(
        "unterminated quoted SQL identifier".to_owned(),
    ))
}

fn skip_line_comment(bytes: &[u8], start: usize) -> usize {
    bytes[start..]
        .iter()
        .position(|byte| *byte == b'\n')
        .map_or(bytes.len(), |offset| start + offset + 1)
}

fn skip_block_comment(bytes: &[u8], start: usize) -> Result<usize, DatabaseOperatorError> {
    let mut index = start + 2;
    let mut depth = 1_usize;
    while index < bytes.len() {
        if bytes.get(index..index + 2) == Some(b"/*") {
            depth += 1;
            index += 2;
        } else if bytes.get(index..index + 2) == Some(b"*/") {
            depth -= 1;
            index += 2;
            if depth == 0 {
                return Ok(index);
            }
        } else {
            index += 1;
        }
    }
    Err(DatabaseOperatorError::InvalidSql(
        "unterminated block comment".to_owned(),
    ))
}

fn dollar_quote_delimiter(sql: &str) -> Option<&str> {
    let bytes = sql.as_bytes();
    if bytes.first() != Some(&b'$') {
        return None;
    }
    let end = bytes[1..].iter().position(|byte| *byte == b'$')? + 1;
    let tag = &bytes[1..end];
    if (tag.is_empty() || tag[0].is_ascii_alphabetic() || tag[0] == b'_')
        && tag
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || *byte == b'_')
    {
        Some(&sql[..=end])
    } else {
        None
    }
}

fn is_forbidden_token(token: &str) -> bool {
    matches!(
        token,
        "INSERT"
            | "UPDATE"
            | "DELETE"
            | "MERGE"
            | "INTO"
            | "CALL"
            | "DO"
            | "COPY"
            | "CREATE"
            | "ALTER"
            | "DROP"
            | "TRUNCATE"
            | "COMMENT"
            | "GRANT"
            | "REVOKE"
            | "VACUUM"
            | "ANALYZE"
            | "CLUSTER"
            | "REINDEX"
            | "REFRESH"
            | "LOCK"
            | "SET"
            | "RESET"
            | "BEGIN"
            | "START"
            | "COMMIT"
            | "ROLLBACK"
            | "SAVEPOINT"
            | "RELEASE"
            | "PREPARE"
            | "EXECUTE"
            | "DEALLOCATE"
            | "DISCARD"
            | "LISTEN"
            | "UNLISTEN"
            | "NOTIFY"
            | "LOAD"
            | "SECURITY"
            | "REASSIGN"
            | "OWNED"
    ) || is_dangerous_function(token)
}

fn is_dangerous_function(token: &str) -> bool {
    matches!(
        token,
        "SET_CONFIG"
            | "PG_CANCEL_BACKEND"
            | "PG_TERMINATE_BACKEND"
            | "PG_RELOAD_CONF"
            | "PG_ROTATE_LOGFILE"
            | "PG_LOG_BACKEND_MEMORY_CONTEXTS"
            | "PG_CREATE_RESTORE_POINT"
            | "PG_SWITCH_WAL"
            | "PG_WAL_REPLAY_PAUSE"
            | "PG_WAL_REPLAY_RESUME"
            | "PG_PROMOTE"
            | "PG_READ_FILE"
            | "PG_READ_BINARY_FILE"
            | "PG_LS_DIR"
            | "PG_STAT_FILE"
            | "DBLINK_EXEC"
            | "LO_IMPORT"
            | "LO_EXPORT"
    ) || (token.contains("ADVISORY") && token.contains("LOCK"))
}

fn validate_catalog_name<'a>(
    value: &'a str,
    kind: &'static str,
) -> Result<&'a str, DatabaseOperatorError> {
    let value = value.trim();
    if value.is_empty() || value.len() > 63 || value.contains('\0') {
        return Err(DatabaseOperatorError::InvalidCatalogName { kind });
    }
    Ok(value)
}

fn validate_limit(limit: i64, maximum: i64) -> Result<usize, DatabaseOperatorError> {
    if !(1..=maximum).contains(&limit) {
        return Err(DatabaseOperatorError::InvalidLimit { maximum });
    }
    usize::try_from(limit).map_err(|_| DatabaseOperatorError::InvalidLimit { maximum })
}

fn is_sensitive_key(key: &str) -> bool {
    let key = key.to_ascii_lowercase();
    [
        "authorization",
        "cookie",
        "api-key",
        "api_key",
        "apikey",
        "token",
        "password",
        "passcode",
        "secret",
        "jwt",
    ]
    .iter()
    .any(|part| key.contains(part))
}

fn redact_value(value: Value) -> Value {
    match value {
        Value::Object(values) => Value::Object(
            values
                .into_iter()
                .map(|(key, value)| {
                    if is_sensitive_key(&key) {
                        (key, Value::String(REDACTED.to_owned()))
                    } else {
                        (key, redact_value(value))
                    }
                })
                .collect(),
        ),
        Value::Array(values) => Value::Array(values.into_iter().map(redact_value).collect()),
        Value::String(value) if contains_sensitive_string(&value) => {
            Value::String(REDACTED.to_owned())
        }
        value => value,
    }
}

fn contains_sensitive_string(value: &str) -> bool {
    let value = value.to_ascii_lowercase();
    value.contains("bearer ")
        || value.contains("authorization=")
        || value.contains("authorization:")
        || value.contains("cookie=")
        || value.contains("cookie:")
}

fn bound_value(value: Value) -> Value {
    match serde_json::to_vec(&value) {
        Ok(encoded) if encoded.len() <= MAX_CELL_BYTES => value,
        _ => Value::String(TRUNCATED_CELL.to_owned()),
    }
}

fn bound_text(value: String) -> String {
    if value.len() <= MAX_CELL_BYTES {
        value
    } else {
        TRUNCATED_CELL.to_owned()
    }
}

#[derive(Debug, Error)]
pub enum DatabaseOperatorError {
    #[error("{kind} name must contain between 1 and 63 bytes and no NUL byte")]
    InvalidCatalogName { kind: &'static str },
    #[error("limit must be between 1 and {maximum}")]
    InvalidLimit { maximum: i64 },
    #[error("invalid operator SQL: {0}")]
    InvalidSql(String),
    #[error("operator SQL may not exceed {maximum_bytes} bytes")]
    SqlTooLong { maximum_bytes: usize },
    #[error("operator query returned {count} columns; maximum is {maximum}")]
    TooManyColumns { count: usize, maximum: usize },
    #[error("table {schema}.{table} does not exist or has no visible columns")]
    TableNotFound { schema: String, table: String },
    #[error("PostgreSQL operator query failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("operator result could not be serialized")]
    Json(#[from] serde_json::Error),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sql_guard_accepts_one_select_or_with_statement() {
        assert_eq!(
            validate_read_only_query(" SELECT 1; ").expect("select should pass"),
            "SELECT 1;"
        );
        assert_eq!(
            validate_read_only_query("WITH value AS (SELECT 1) SELECT * FROM value")
                .expect("with query should pass"),
            "WITH value AS (SELECT 1) SELECT * FROM value"
        );
    }

    #[test]
    fn sql_guard_rejects_every_non_query_statement_class() {
        for sql in [
            "INSERT INTO users(id) VALUES (1)",
            "UPDATE users SET email = NULL",
            "DELETE FROM users",
            "MERGE INTO users USING other ON false WHEN NOT MATCHED THEN INSERT DEFAULT VALUES",
            "CREATE TABLE surprise(id int)",
            "ALTER TABLE users ADD COLUMN surprise int",
            "DROP TABLE users",
            "TRUNCATE users",
            "COPY users TO STDOUT",
            "CALL refresh_everything()",
            "BEGIN",
            "COMMIT",
            "ROLLBACK",
            "EXPLAIN SELECT * FROM users",
        ] {
            assert!(validate_read_only_query(sql).is_err(), "accepted {sql}");
        }
    }

    #[test]
    fn sql_guard_rejects_writable_ctes_and_select_into() {
        for sql in [
            "WITH doomed AS (DELETE FROM users RETURNING id) SELECT * FROM doomed",
            "WITH changed AS (UPDATE users SET email = NULL RETURNING id) SELECT * FROM changed",
            "WITH added AS (INSERT INTO users DEFAULT VALUES RETURNING id) SELECT * FROM added",
            "SELECT * INTO copied_users FROM users",
        ] {
            assert!(validate_read_only_query(sql).is_err(), "accepted {sql}");
        }
    }

    #[test]
    fn sql_guard_rejects_side_effecting_or_host_inspection_functions() {
        for sql in [
            "SELECT set_config('statement_timeout', '0', false)",
            "SELECT pg_advisory_lock(42)",
            "SELECT pg_terminate_backend(42)",
            "SELECT pg_read_file('/etc/passwd')",
            "SELECT dblink_exec('remote', 'DELETE FROM users')",
            "SELECT pg_catalog.\"set_config\"('statement_timeout', '0', false)",
            "SELECT lo_export(42, '/tmp/export')",
        ] {
            assert!(validate_read_only_query(sql).is_err(), "accepted {sql}");
        }
    }

    #[test]
    fn scanner_ignores_keywords_and_semicolons_in_literals_identifiers_and_comments() {
        let sql = r#"
            /* DELETE; /* nested UPDATE */ */
            SELECT
                'INSERT; DROP' AS "delete",
                $$COPY users; UPDATE users$$ AS text,
                $tag$COMMIT;$tag$ AS other
            -- TRUNCATE users;
        "#;
        let scan = scan_sql(sql).expect("quoted input should scan");
        assert_eq!(scan.statement_delimiters, 0);
        assert_eq!(scan.tokens.first().map(String::as_str), Some("SELECT"));
        assert!(!scan.tokens.iter().any(|token| is_forbidden_token(token)));
        assert!(validate_read_only_query(sql).is_ok());
    }

    #[test]
    fn scanner_distinguishes_standard_and_escape_strings() {
        assert!(validate_read_only_query(r"SELECT E'quote\' ; still text' AS value").is_ok());
        let scan = scan_sql(r"SELECT '\'; DELETE FROM users")
            .expect("standard string should close after the quote");
        assert!(
            scan.tokens.iter().any(|token| token == "DELETE"),
            "a backslash in a standard string must not hide statement separators"
        );
    }

    #[test]
    fn sql_guard_rejects_a_second_statement_after_comments_or_literals() {
        for sql in [
            "SELECT ';' AS punctuation; SELECT 2",
            "SELECT 1; /* comment */ DELETE FROM users",
            "SELECT 1;;",
        ] {
            assert!(validate_read_only_query(sql).is_err(), "accepted {sql}");
        }
    }

    #[test]
    fn redaction_is_recursive_and_cells_are_bounded() {
        let value = json!({
            "nested": {"access_token": "secret-token"},
            "message": "Authorization: Bearer actual-secret",
            "safe": "visible",
        });
        assert_eq!(
            redact_value(value),
            json!({
                "nested": {"access_token": REDACTED},
                "message": REDACTED,
                "safe": "visible",
            })
        );
        assert_eq!(
            bound_value(Value::String("x".repeat(MAX_CELL_BYTES))),
            Value::String(TRUNCATED_CELL.to_owned())
        );
    }

    #[sqlx::test(migrations = false)]
    async fn query_results_are_bounded_and_sensitive_columns_are_redacted(pool: PgPool) {
        let result = run_query(
            &pool,
            "SELECT value, 'secret'::text AS access_token FROM generate_series(1, 4) AS value",
            2,
            false,
        )
        .await
        .expect("bounded query should succeed");

        assert_eq!(result.row_count, 2);
        assert!(result.truncated);
        assert_eq!(result.truncation_reason.as_deref(), Some("row_limit"));
        assert_eq!(
            result.rows[0]["access_token"],
            Value::String(REDACTED.to_owned())
        );
    }

    #[sqlx::test(migrations = false)]
    async fn catalog_and_explain_commands_return_typed_bounded_results(pool: PgPool) {
        sqlx::query(
            r"
            CREATE TABLE operator_catalog_fixture (
                id bigint PRIMARY KEY,
                payload text NOT NULL DEFAULT 'fixture',
                optional boolean
            )
            ",
        )
        .execute(&pool)
        .await
        .expect("fixture table should be created");
        sqlx::query(
            "CREATE VIEW operator_catalog_view AS SELECT id, payload FROM operator_catalog_fixture",
        )
        .execute(&pool)
        .await
        .expect("fixture view should be created");

        let tables = list_tables(&pool, "public", 1)
            .await
            .expect("table catalog should load");
        assert_eq!(tables.count, 1);
        assert!(tables.truncated);
        assert_eq!(tables.tables[0].name, "operator_catalog_fixture");

        let schema = describe_schema(&pool, "public", Some("operator_catalog_fixture"), 10)
            .await
            .expect("table schema should load");
        assert_eq!(schema.count, 1);
        assert_eq!(schema.tables[0].columns.len(), 3);
        assert!(schema.tables[0].columns[0].primary_key);
        assert!(!schema.tables[0].columns[1].nullable);

        let explained = explain_query(&pool, "SELECT id FROM operator_catalog_fixture")
            .await
            .expect("read-only plan should load");
        assert_eq!(explained.format, "postgresql-json");
        assert!(!explained.analyzed);
        assert!(explained.plan.is_array());
    }

    #[sqlx::test(migrations = false)]
    async fn postgres_read_only_transaction_blocks_mutating_functions(pool: PgPool) {
        sqlx::query("CREATE SEQUENCE operator_guard_fixture")
            .execute(&pool)
            .await
            .expect("fixture sequence should be created");

        let error = run_query(&pool, "SELECT nextval('operator_guard_fixture')", 10, false)
            .await
            .expect_err("mutating function must fail inside a read-only transaction");
        assert!(matches!(error, DatabaseOperatorError::Sqlx(_)));
        let value = sqlx::query_scalar::<_, i64>("SELECT last_value FROM operator_guard_fixture")
            .fetch_one(&pool)
            .await
            .expect("fixture should remain readable");
        assert_eq!(value, 1);
    }
}
