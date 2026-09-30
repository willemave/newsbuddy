use chrono::{DateTime, Utc};
use serde_json::{Value, json};
use sqlx::{FromRow, PgConnection};

/// An observed billing quantity, not a count inferred from product state.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct VendorResourceMeter<'a> {
    pub unit: &'a str,
    pub quantity: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CalculatedVendorCost {
    pub cost_usd: f64,
    pub pricing_version: String,
    pub metadata: Value,
}

#[derive(Debug, FromRow)]
struct ResourceRate {
    unit: String,
    rate_usd: f64,
    source_url: String,
    pricing_version: String,
    effective_at: DateTime<Utc>,
}

/// Prices complete, measured units against the catalog applicable at the observation timestamp.
/// Missing rates or invalid quantities leave cost unknown. The snapshot preserves the
/// applied rates after later catalog updates. Use a short accounting transaction.
pub async fn calculate_vendor_resource_cost(
    connection: &mut PgConnection,
    provider: &str,
    model: &str,
    meters: &[VendorResourceMeter<'_>],
    used_at: DateTime<Utc>,
) -> Result<Option<CalculatedVendorCost>, sqlx::Error> {
    if meters.is_empty()
        || meters
            .iter()
            .any(|meter| !meter.quantity.is_finite() || meter.quantity < 0.0)
        || meters.iter().enumerate().any(|(index, meter)| {
            meters[..index]
                .iter()
                .any(|previous| previous.unit == meter.unit)
        })
    {
        return Ok(None);
    }
    let units = meters.iter().map(|meter| meter.unit).collect::<Vec<_>>();
    let rates = sqlx::query_as::<_, ResourceRate>(
        r"
        SELECT DISTINCT ON (unit) unit, rate_usd::double precision AS rate_usd,
               source_url, pricing_version, effective_at
        FROM vendor_resource_price_rates
        WHERE provider = $1 AND model = $2 AND unit = ANY($3) AND effective_at <= $4
        ORDER BY unit, effective_at DESC
        ",
    )
    .bind(provider)
    .bind(model)
    .bind(&units)
    .bind(used_at)
    .fetch_all(connection)
    .await?;
    if rates.len() != meters.len() {
        return Ok(None);
    }
    let mut cost_usd = 0.0;
    let mut components = Vec::with_capacity(meters.len());
    for meter in meters {
        let Some(rate) = rates.iter().find(|rate| rate.unit == meter.unit) else {
            return Ok(None);
        };
        cost_usd += meter.quantity * rate.rate_usd;
        components.push(json!({
            "unit": meter.unit, "quantity": meter.quantity, "rate_usd": rate.rate_usd,
            "source_url": rate.source_url, "version": rate.pricing_version,
            "effective_at": rate.effective_at,
        }));
    }
    if !cost_usd.is_finite() {
        return Ok(None);
    }
    Ok(Some(CalculatedVendorCost {
        cost_usd,
        pricing_version: if rates
            .iter()
            .all(|rate| rate.pricing_version == rates[0].pricing_version)
        {
            rates[0].pricing_version.clone()
        } else {
            "mixed-resource-rates".to_owned()
        },
        metadata: json!({"basis": "public_list_estimate", "used_at": used_at,
                         "provider": provider, "model": model, "components": components}),
    }))
}

pub(crate) async fn openai_transcription_cost(
    connection: &mut PgConnection,
    model: &str,
    measured_duration_ms: Option<u64>,
    duration_is_measured: bool,
    standard_direct_pricing: bool,
) -> Result<Option<CalculatedVendorCost>, sqlx::Error> {
    if !duration_is_measured || !standard_direct_pricing {
        return Ok(None);
    }
    let Some(duration_ms) = measured_duration_ms.and_then(|value| u32::try_from(value).ok()) else {
        return Ok(None);
    };
    calculate_vendor_resource_cost(
        connection,
        "openai",
        model,
        &[VendorResourceMeter {
            unit: "audio_minute",
            quantity: f64::from(duration_ms) / 60_000.0,
        }],
        Utc::now(),
    )
    .await
}

pub(crate) async fn elevenlabs_tts_cost(
    connection: &mut PgConnection,
    model: &str,
    text_chars: u64,
    standard_direct_pricing: bool,
) -> Result<Option<CalculatedVendorCost>, sqlx::Error> {
    if !standard_direct_pricing {
        return Ok(None);
    }
    let Ok(text_chars) = u32::try_from(text_chars) else {
        return Ok(None);
    };
    calculate_vendor_resource_cost(
        connection,
        "elevenlabs",
        model,
        &[VendorResourceMeter {
            unit: "character",
            quantity: f64::from(text_chars),
        }],
        Utc::now(),
    )
    .await
}

#[cfg(test)]
mod tests;
