-- Register the owner-scoped full reprocess of one saved Knowledge item.
INSERT INTO runtime_ownership (
    resource_kind, resource_key, active_owner, active_version, updated_by, reason
) VALUES (
    'route_group', 'reprocessKnowledgeContent', 'rust', 1,
    'migration:20260927000002', 'Reprocess an unavailable saved Knowledge item'
);
