use super::super::normalize::BriefingNormalizationError;

pub(super) const fn normalization_rejection_code(
    error: &BriefingNormalizationError,
) -> &'static str {
    match error {
        BriefingNormalizationError::UnknownSuggestion(_) => "unknown_suggestion",
        BriefingNormalizationError::NoPassage => "no_usable_passage",
        BriefingNormalizationError::MissingCoverage(_) => "missing_source_coverage",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalization_codes_do_not_expose_dynamic_details() {
        assert_eq!(
            normalization_rejection_code(&BriefingNormalizationError::UnknownSuggestion(
                "private quote".to_owned()
            )),
            "unknown_suggestion"
        );
        assert_eq!(
            normalization_rejection_code(&BriefingNormalizationError::MissingCoverage(vec![
                "private source".to_owned()
            ])),
            "missing_source_coverage"
        );
        assert_eq!(
            normalization_rejection_code(&BriefingNormalizationError::NoPassage),
            "no_usable_passage"
        );
    }
}
