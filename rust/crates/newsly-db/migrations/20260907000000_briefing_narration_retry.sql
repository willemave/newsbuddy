-- Retry an owned immutable narration group without selecting a new unread edition.
INSERT INTO runtime_ownership (
    resource_kind, resource_key, active_owner, active_version, updated_by, reason
) VALUES (
    'route_group', 'retryBriefingNarration', 'rust', 1,
    'migration:20260907000000', 'Retry failed Briefing audio chapters by group identity'
);
