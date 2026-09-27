const PRICING_VERSION: &str = "official-standard-2026-09-27";
const COST_BASIS: &str = "public_list_estimate";
const OPENAI_PRICING_SOURCE: &str = "https://developers.openai.com/api/docs/models/gpt-transcribe";
const ELEVENLABS_PRICING_SOURCE: &str = "https://elevenlabs.io/pricing/api";

#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct CalculatedVendorCost {
    pub(crate) cost_usd: f64,
    pub(crate) pricing_version: &'static str,
    pub(crate) cost_basis: &'static str,
    pub(crate) rate_usd: f64,
    pub(crate) rate_unit: &'static str,
    pub(crate) source_url: &'static str,
}

pub(crate) fn openai_transcription_cost(
    model: &str,
    measured_duration_ms: Option<u64>,
    duration_is_measured: bool,
    standard_direct_pricing: bool,
) -> Option<CalculatedVendorCost> {
    if model != "gpt-transcribe" || !duration_is_measured || !standard_direct_pricing {
        return None;
    }
    let duration_ms = u32::try_from(measured_duration_ms?).ok()?;
    let rate_usd_per_minute = 0.0045;
    Some(CalculatedVendorCost {
        cost_usd: f64::from(duration_ms) / 60_000.0 * rate_usd_per_minute,
        pricing_version: PRICING_VERSION,
        cost_basis: COST_BASIS,
        rate_usd: rate_usd_per_minute,
        rate_unit: "audio_minute",
        source_url: OPENAI_PRICING_SOURCE,
    })
}

pub(crate) fn elevenlabs_tts_cost(
    model: &str,
    text_chars: u64,
    standard_direct_pricing: bool,
) -> Option<CalculatedVendorCost> {
    if !standard_direct_pricing {
        return None;
    }
    let text_chars = u32::try_from(text_chars).ok()?;
    let rate_usd_per_thousand_characters = match model {
        "eleven_flash_v2"
        | "eleven_flash_v2_5"
        | "eleven_turbo_v2"
        | "eleven_turbo_v2_5"
        | "eleven_v3_conversational" => 0.05,
        "eleven_multilingual_v2" | "eleven_v3" => 0.10,
        _ => return None,
    };
    Some(CalculatedVendorCost {
        cost_usd: f64::from(text_chars) / 1_000.0 * rate_usd_per_thousand_characters,
        pricing_version: PRICING_VERSION,
        cost_basis: COST_BASIS,
        rate_usd: rate_usd_per_thousand_characters,
        rate_unit: "thousand_characters",
        source_url: ELEVENLABS_PRICING_SOURCE,
    })
}

#[cfg(test)]
mod tests {
    use super::{elevenlabs_tts_cost, openai_transcription_cost};

    #[test]
    fn prices_only_measured_direct_gpt_transcribe_audio() {
        let priced = openai_transcription_cost("gpt-transcribe", Some(120_000), true, true)
            .expect("known direct model and measured duration should be priced");
        assert!((priced.cost_usd - 0.009).abs() < f64::EPSILON);
        assert!(openai_transcription_cost("gpt-transcribe", Some(120_000), false, true).is_none());
        assert!(openai_transcription_cost("gpt-transcribe", Some(120_000), true, false).is_none());
        assert!(
            openai_transcription_cost("custom-transcribe", Some(120_000), true, true).is_none()
        );
    }

    #[test]
    fn prices_known_elevenlabs_models_only_on_standard_endpoint() {
        let flash = elevenlabs_tts_cost("eleven_flash_v2_5", 2_000, true)
            .expect("known Flash model should be priced");
        assert!((flash.cost_usd - 0.10).abs() < f64::EPSILON);
        let multilingual = elevenlabs_tts_cost("eleven_multilingual_v2", 2_000, true)
            .expect("known multilingual model should be priced");
        assert!((multilingual.cost_usd - 0.20).abs() < f64::EPSILON);
        assert!(elevenlabs_tts_cost("custom-model", 2_000, true).is_none());
        assert!(elevenlabs_tts_cost("eleven_flash_v2_5", 2_000, false).is_none());
    }
}
