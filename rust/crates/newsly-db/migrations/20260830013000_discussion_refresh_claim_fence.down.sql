ALTER TABLE news_item_discussions
    ALTER COLUMN summary_incremental_update_count DROP DEFAULT;

ALTER TABLE news_item_discussions
    DROP COLUMN refresh_claim_token;
