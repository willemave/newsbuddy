CREATE TABLE agent_vm_namespace_leases (
    vm_namespace VARCHAR(255) PRIMARY KEY,
    ownership_resource_key VARCHAR(255) NOT NULL,
    runtime_owner VARCHAR(16) NOT NULL,
    ownership_version BIGINT NOT NULL,
    lease_token UUID NOT NULL UNIQUE,
    lease_holder VARCHAR(255) NOT NULL,
    task_id BIGINT,
    template_revision VARCHAR(255) NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    renewed_at TIMESTAMPTZ NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_agent_vm_namespace_leases_namespace_nonempty
        CHECK (BTRIM(vm_namespace) <> ''),
    CONSTRAINT ck_agent_vm_namespace_leases_ownership_key_nonempty
        CHECK (BTRIM(ownership_resource_key) <> ''),
    CONSTRAINT ck_agent_vm_namespace_leases_runtime_owner
        CHECK (runtime_owner = 'rust'),
    CONSTRAINT ck_agent_vm_namespace_leases_ownership_version
        CHECK (ownership_version > 0),
    CONSTRAINT ck_agent_vm_namespace_leases_holder_nonempty
        CHECK (BTRIM(lease_holder) <> ''),
    CONSTRAINT ck_agent_vm_namespace_leases_task_id
        CHECK (task_id IS NULL OR task_id > 0),
    CONSTRAINT ck_agent_vm_namespace_leases_template_revision_nonempty
        CHECK (BTRIM(template_revision) <> ''),
    CONSTRAINT ck_agent_vm_namespace_leases_timestamps
        CHECK (
            renewed_at >= acquired_at
            AND lease_expires_at > renewed_at
        )
);

CREATE INDEX ix_agent_vm_namespace_leases_expires_at
    ON agent_vm_namespace_leases (lease_expires_at);

COMMENT ON TABLE agent_vm_namespace_leases IS
    'Exclusive, expiring command leases fencing persistent E2B namespace use across runtimes and workers.';

COMMENT ON COLUMN agent_vm_namespace_leases.ownership_resource_key IS
    'Exact runtime_ownership key used for the grant, including the user:* fallback when no per-user override exists.';
