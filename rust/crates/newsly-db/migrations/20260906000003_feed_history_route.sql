-- Register the authenticated feed-history read before exposing it through the gateway.
INSERT INTO runtime_ownership (
    resource_kind, resource_key, active_owner, active_version, updated_by, reason
) VALUES (
    'route_group', 'getFeedHistory', 'rust', 1,
    'migration:20260906000003', 'Add user-scoped feed processing history'
);
