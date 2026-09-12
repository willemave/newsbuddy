-- Register owner-scoped observation of one durable Briefing refresh task.
INSERT INTO runtime_ownership (
    resource_kind, resource_key, active_owner, active_version, updated_by, reason
) VALUES (
    'route_group', 'getBriefingRefreshStatus', 'rust', 1,
    'migration:20260912000000', 'Observe exact durable Briefing refresh completion'
);
