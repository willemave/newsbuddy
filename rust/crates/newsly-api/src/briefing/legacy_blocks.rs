//! Stored Briefing blocks decoded for the public contract.

use newsly_contracts::BriefingBlockDto;

/// Decodes a stored segment's blocks, dropping only legacy figure and pullquote
/// blocks; any other unexpected block still fails loudly. Workers from the
/// release before source cards may publish such blocks after the strip
/// migration commits, during the online deploy overlap; dropping them keeps
/// every lens decodable for new and installed clients. Remove once a follow-up
/// migration has re-stripped stored blocks after that release's workers are gone.
pub(super) fn passage_blocks(
    blocks: &serde_json::Value,
) -> Result<Vec<BriefingBlockDto>, serde_json::Error> {
    let Some(blocks) = blocks.as_array() else {
        return serde_json::from_value(blocks.clone());
    };
    let passages = blocks
        .iter()
        .filter(|block| {
            !matches!(
                block.get("type").and_then(serde_json::Value::as_str),
                Some("figure" | "pullquote")
            )
        })
        .cloned()
        .collect();
    serde_json::from_value(serde_json::Value::Array(passages))
}

#[cfg(test)]
mod tests {
    use super::passage_blocks;

    #[test]
    fn legacy_figure_and_pullquote_blocks_are_dropped_in_order() {
        let stored = serde_json::json!([
            {"type": "figure", "source_key": "content:1", "placement": "inset"},
            {"type": "passage", "weight": "feature", "paragraphs": [], "source_key": null},
            {"type": "pullquote", "text": "Callout"},
            {"type": "passage", "weight": "brief", "paragraphs": [], "source_key": null}
        ]);

        let blocks = passage_blocks(&stored).unwrap();

        assert_eq!(blocks.len(), 2);
        assert_eq!(blocks[0].weight.as_deref(), Some("feature"));
        assert_eq!(blocks[1].weight.as_deref(), Some("brief"));
    }

    #[test]
    fn malformed_block_storage_still_fails() {
        assert!(passage_blocks(&serde_json::json!({"type": "passage"})).is_err());
        assert!(
            passage_blocks(&serde_json::json!([{"type": "passage", "paragraphs": "x"}])).is_err()
        );
        assert!(passage_blocks(&serde_json::json!([{"paragraphs": []}])).is_err());
        assert!(
            passage_blocks(&serde_json::json!([{"type": "future", "paragraphs": []}])).is_err()
        );
    }
}
