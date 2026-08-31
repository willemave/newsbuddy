WITH application_namespaces AS (
    SELECT oid, nspname, nspowner, nspacl
    FROM pg_catalog.pg_namespace
    WHERE nspname !~ '^pg_'
      AND nspname <> 'information_schema'
),
database_identity AS (
    SELECT datdba
    FROM pg_catalog.pg_database
    WHERE datname = current_database()
),
extension_dependencies AS (
    SELECT classid, objid, objsubid
    FROM pg_catalog.pg_depend
    WHERE deptype = 'e'
),
owned_objects AS (
    SELECT 'schema'::text AS object_kind, namespace.nspowner AS owner_oid
    FROM application_namespaces AS namespace

    UNION ALL

    SELECT
        CASE relation.relkind
            WHEN 'S' THEN 'sequence'
            ELSE 'relation'
        END,
        relation.relowner
    FROM pg_catalog.pg_class AS relation
    JOIN application_namespaces AS namespace ON namespace.oid = relation.relnamespace
    WHERE relation.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
      AND relation.relname <> '_sqlx_migrations'

    UNION ALL

    SELECT 'routine', routine.proowner
    FROM pg_catalog.pg_proc AS routine
    JOIN application_namespaces AS namespace ON namespace.oid = routine.pronamespace
    LEFT JOIN extension_dependencies AS dependency
      ON dependency.classid = 'pg_proc'::regclass
     AND dependency.objid = routine.oid
     AND dependency.objsubid = 0
    WHERE dependency.objid IS NULL

    UNION ALL

    SELECT 'type', type_record.typowner
    FROM pg_catalog.pg_type AS type_record
    JOIN application_namespaces AS namespace ON namespace.oid = type_record.typnamespace
    LEFT JOIN extension_dependencies AS dependency
      ON dependency.classid = 'pg_type'::regclass
     AND dependency.objid = type_record.oid
     AND dependency.objsubid = 0
    WHERE type_record.typtype IN ('e', 'd', 'r', 'm')
      AND dependency.objid IS NULL
),
normalized_owners AS (
    SELECT
        owned_objects.object_kind,
        CASE
            WHEN owned_objects.owner_oid = identity.datdba THEN 'DATABASE_OWNER'
            WHEN owned_objects.owner_oid = (SELECT usesysid FROM pg_catalog.pg_user WHERE usename = current_user)
                THEN 'CURRENT_ROLE'
            WHEN pg_catalog.pg_get_userbyid(owned_objects.owner_oid) = 'pg_database_owner'
                THEN 'DATABASE_OWNER_VIRTUAL'
            ELSE 'EXTERNAL_ROLE'
        END AS owner_kind,
        count(*) AS object_count
    FROM owned_objects
    CROSS JOIN database_identity AS identity
    GROUP BY owned_objects.object_kind, owner_kind
),
schema_grants AS (
    SELECT
        namespace.nspname AS schema_name,
        CASE
            WHEN acl.grantee = 0 THEN 'PUBLIC'
            WHEN acl.grantee = identity.datdba THEN 'DATABASE_OWNER'
            WHEN acl.grantee = (SELECT usesysid FROM pg_catalog.pg_user WHERE usename = current_user)
                THEN 'CURRENT_ROLE'
            WHEN pg_catalog.pg_get_userbyid(acl.grantee) = 'pg_database_owner'
                THEN 'DATABASE_OWNER_VIRTUAL'
            ELSE 'EXTERNAL_ROLE'
        END AS grantee_kind,
        acl.privilege_type,
        acl.is_grantable
    FROM application_namespaces AS namespace
    CROSS JOIN database_identity AS identity
    CROSS JOIN LATERAL pg_catalog.aclexplode(namespace.nspacl) AS acl
)
SELECT pg_catalog.jsonb_build_object(
    'database_owner_is_current_role', (
        SELECT identity.datdba = (SELECT usesysid FROM pg_catalog.pg_user WHERE usename = current_user)
        FROM database_identity AS identity
    ),
    'object_ownership', COALESCE((
        SELECT pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object(
                'object_kind', ownership.object_kind,
                'owner_kind', ownership.owner_kind,
                'count', ownership.object_count
            )
            ORDER BY ownership.object_kind, ownership.owner_kind
        )
        FROM normalized_owners AS ownership
    ), '[]'::jsonb),
    'schema_grants', COALESCE((
        SELECT pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object(
                'schema', grant_record.schema_name,
                'grantee', grant_record.grantee_kind,
                'privilege', grant_record.privilege_type,
                'grantable', grant_record.is_grantable
            )
            ORDER BY grant_record.schema_name, grant_record.grantee_kind, grant_record.privilege_type
        )
        FROM schema_grants AS grant_record
    ), '[]'::jsonb)
)
