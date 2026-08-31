WITH application_namespaces AS (
    SELECT oid, nspname, nspacl
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
explicit_grants AS (
    SELECT
        'schema'::text AS object_kind,
        namespace.nspname AS schema_name,
        namespace.nspname AS object_name,
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

    UNION ALL

    SELECT
        CASE relation.relkind
            WHEN 'S' THEN 'sequence'
            ELSE 'relation'
        END,
        namespace.nspname,
        relation.relname,
        CASE
            WHEN acl.grantee = 0 THEN 'PUBLIC'
            WHEN acl.grantee = identity.datdba THEN 'DATABASE_OWNER'
            WHEN acl.grantee = (SELECT usesysid FROM pg_catalog.pg_user WHERE usename = current_user)
                THEN 'CURRENT_ROLE'
            WHEN pg_catalog.pg_get_userbyid(acl.grantee) = 'pg_database_owner'
                THEN 'DATABASE_OWNER_VIRTUAL'
            ELSE 'EXTERNAL_ROLE'
        END,
        acl.privilege_type,
        acl.is_grantable
    FROM pg_catalog.pg_class AS relation
    JOIN application_namespaces AS namespace ON namespace.oid = relation.relnamespace
    CROSS JOIN database_identity AS identity
    CROSS JOIN LATERAL pg_catalog.aclexplode(relation.relacl) AS acl
    WHERE relation.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
      AND relation.relname <> '_sqlx_migrations'

    UNION ALL

    SELECT
        'routine',
        namespace.nspname,
        routine.proname || '(' || pg_catalog.pg_get_function_identity_arguments(routine.oid) || ')',
        CASE
            WHEN acl.grantee = 0 THEN 'PUBLIC'
            WHEN acl.grantee = identity.datdba THEN 'DATABASE_OWNER'
            WHEN acl.grantee = (SELECT usesysid FROM pg_catalog.pg_user WHERE usename = current_user)
                THEN 'CURRENT_ROLE'
            WHEN pg_catalog.pg_get_userbyid(acl.grantee) = 'pg_database_owner'
                THEN 'DATABASE_OWNER_VIRTUAL'
            ELSE 'EXTERNAL_ROLE'
        END,
        acl.privilege_type,
        acl.is_grantable
    FROM pg_catalog.pg_proc AS routine
    JOIN application_namespaces AS namespace ON namespace.oid = routine.pronamespace
    CROSS JOIN database_identity AS identity
    CROSS JOIN LATERAL pg_catalog.aclexplode(routine.proacl) AS acl
    LEFT JOIN extension_dependencies AS dependency
      ON dependency.classid = 'pg_proc'::regclass
     AND dependency.objid = routine.oid
     AND dependency.objsubid = 0
    WHERE dependency.objid IS NULL
)
SELECT pg_catalog.jsonb_build_object(
    'extensions', COALESCE((
        SELECT pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object(
                'name', extension.extname,
                'version', extension.extversion,
                'schema', namespace.nspname,
                'relocatable', extension.extrelocatable
            )
            ORDER BY extension.extname
        )
        FROM pg_catalog.pg_extension AS extension
        JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = extension.extnamespace
    ), '[]'::jsonb),
    'schemas', COALESCE((
        SELECT pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object('name', namespace.nspname)
            ORDER BY namespace.nspname
        )
        FROM application_namespaces AS namespace
    ), '[]'::jsonb),
    'relations', COALESCE((
        SELECT pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object(
                'schema', namespace.nspname,
                'name', relation.relname,
                'kind', relation.relkind,
                'persistence', relation.relpersistence,
                'access_method', access_method.amname,
                'row_security', relation.relrowsecurity,
                'force_row_security', relation.relforcerowsecurity,
                'replica_identity', relation.relreplident,
                'partition_bound', pg_catalog.pg_get_expr(relation.relpartbound, relation.oid)
            )
            ORDER BY namespace.nspname, relation.relname
        )
        FROM pg_catalog.pg_class AS relation
        JOIN application_namespaces AS namespace ON namespace.oid = relation.relnamespace
        LEFT JOIN pg_catalog.pg_am AS access_method ON access_method.oid = relation.relam
        WHERE relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND relation.relname <> '_sqlx_migrations'
    ), '[]'::jsonb),
    'columns', COALESCE((
        SELECT pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object(
                'schema', namespace.nspname,
                'relation', relation.relname,
                'position', attribute.logical_position,
                'name', attribute.attname,
                'type', pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
                'nullable', NOT attribute.attnotnull,
                'default', pg_catalog.pg_get_expr(default_value.adbin, default_value.adrelid),
                'identity', attribute.attidentity,
                'generated', attribute.attgenerated,
                'collation', CASE
                    WHEN attribute.attcollation = 0 THEN NULL
                    ELSE collation_namespace.nspname || '.' || collation_record.collname
                END
            )
            ORDER BY namespace.nspname, relation.relname, attribute.attnum
        )
        FROM (
            SELECT
                attribute_record.*,
                pg_catalog.row_number() OVER (
                    PARTITION BY attribute_record.attrelid
                    ORDER BY attribute_record.attnum
                ) AS logical_position
            FROM pg_catalog.pg_attribute AS attribute_record
            WHERE attribute_record.attnum > 0
              AND NOT attribute_record.attisdropped
        ) AS attribute
        JOIN pg_catalog.pg_class AS relation ON relation.oid = attribute.attrelid
        JOIN application_namespaces AS namespace ON namespace.oid = relation.relnamespace
        LEFT JOIN pg_catalog.pg_attrdef AS default_value
          ON default_value.adrelid = attribute.attrelid
         AND default_value.adnum = attribute.attnum
        LEFT JOIN pg_catalog.pg_collation AS collation_record
          ON collation_record.oid = attribute.attcollation
        LEFT JOIN pg_catalog.pg_namespace AS collation_namespace
          ON collation_namespace.oid = collation_record.collnamespace
        WHERE relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND relation.relname <> '_sqlx_migrations'
    ), '[]'::jsonb),
    'sequences', COALESCE((
        SELECT pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object(
                'schema', namespace.nspname,
                'name', sequence_relation.relname,
                'type', pg_catalog.format_type(sequence.seqtypid, NULL),
                'start', sequence.seqstart,
                'increment', sequence.seqincrement,
                'minimum', sequence.seqmin,
                'maximum', sequence.seqmax,
                'cache', sequence.seqcache,
                'cycle', sequence.seqcycle,
                'owned_by', CASE
                    WHEN owner_relation.oid IS NULL THEN NULL
                    ELSE owner_namespace.nspname || '.' || owner_relation.relname || '.' || owner_attribute.attname
                END
            )
            ORDER BY namespace.nspname, sequence_relation.relname
        )
        FROM pg_catalog.pg_sequence AS sequence
        JOIN pg_catalog.pg_class AS sequence_relation ON sequence_relation.oid = sequence.seqrelid
        JOIN application_namespaces AS namespace ON namespace.oid = sequence_relation.relnamespace
        LEFT JOIN pg_catalog.pg_depend AS ownership
          ON ownership.classid = 'pg_class'::regclass
         AND ownership.objid = sequence_relation.oid
         AND ownership.objsubid = 0
         AND ownership.refclassid = 'pg_class'::regclass
         AND ownership.deptype IN ('a', 'i')
        LEFT JOIN pg_catalog.pg_class AS owner_relation ON owner_relation.oid = ownership.refobjid
        LEFT JOIN pg_catalog.pg_namespace AS owner_namespace ON owner_namespace.oid = owner_relation.relnamespace
        LEFT JOIN pg_catalog.pg_attribute AS owner_attribute
          ON owner_attribute.attrelid = ownership.refobjid
         AND owner_attribute.attnum = ownership.refobjsubid
    ), '[]'::jsonb),
    'constraints', COALESCE((
        SELECT pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object(
                'schema', namespace.nspname,
                'relation', relation.relname,
                'name', constraint_record.conname,
                'type', constraint_record.contype,
                'definition', pg_catalog.pg_get_constraintdef(constraint_record.oid, true),
                'validated', constraint_record.convalidated,
                'deferrable', constraint_record.condeferrable,
                'initially_deferred', constraint_record.condeferred
            )
            ORDER BY namespace.nspname, relation.relname, constraint_record.conname
        )
        FROM pg_catalog.pg_constraint AS constraint_record
        JOIN pg_catalog.pg_class AS relation ON relation.oid = constraint_record.conrelid
        JOIN application_namespaces AS namespace ON namespace.oid = relation.relnamespace
        WHERE relation.relname <> '_sqlx_migrations'
    ), '[]'::jsonb),
    'indexes', COALESCE((
        SELECT pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object(
                'schema', namespace.nspname,
                'relation', relation.relname,
                'name', index_relation.relname,
                'definition', pg_catalog.pg_get_indexdef(index_record.indexrelid),
                'predicate', pg_catalog.pg_get_expr(index_record.indpred, index_record.indrelid),
                'unique', index_record.indisunique,
                'primary', index_record.indisprimary,
                'exclusion', index_record.indisexclusion,
                'valid', index_record.indisvalid,
                'ready', index_record.indisready,
                'live', index_record.indislive,
                'replica_identity', index_record.indisreplident
            )
            ORDER BY namespace.nspname, relation.relname, index_relation.relname
        )
        FROM pg_catalog.pg_index AS index_record
        JOIN pg_catalog.pg_class AS relation ON relation.oid = index_record.indrelid
        JOIN application_namespaces AS namespace ON namespace.oid = relation.relnamespace
        JOIN pg_catalog.pg_class AS index_relation ON index_relation.oid = index_record.indexrelid
        WHERE relation.relname <> '_sqlx_migrations'
    ), '[]'::jsonb),
    'types', COALESCE((
        SELECT pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object(
                'schema', namespace.nspname,
                'name', type_record.typname,
                'kind', type_record.typtype,
                'base_type', CASE
                    WHEN type_record.typbasetype = 0 THEN NULL
                    ELSE pg_catalog.format_type(type_record.typbasetype, type_record.typtypmod)
                END,
                'not_null', type_record.typnotnull,
                'default', type_record.typdefault,
                'enum_labels', COALESCE((
                    SELECT pg_catalog.jsonb_agg(enum_value.enumlabel ORDER BY enum_value.enumsortorder)
                    FROM pg_catalog.pg_enum AS enum_value
                    WHERE enum_value.enumtypid = type_record.oid
                ), '[]'::jsonb),
                'domain_constraints', COALESCE((
                    SELECT pg_catalog.jsonb_agg(
                        pg_catalog.pg_get_constraintdef(domain_constraint.oid, true)
                        ORDER BY domain_constraint.conname
                    )
                    FROM pg_catalog.pg_constraint AS domain_constraint
                    WHERE domain_constraint.contypid = type_record.oid
                ), '[]'::jsonb)
            )
            ORDER BY namespace.nspname, type_record.typname
        )
        FROM pg_catalog.pg_type AS type_record
        JOIN application_namespaces AS namespace ON namespace.oid = type_record.typnamespace
        LEFT JOIN extension_dependencies AS dependency
          ON dependency.classid = 'pg_type'::regclass
         AND dependency.objid = type_record.oid
         AND dependency.objsubid = 0
        WHERE type_record.typtype IN ('e', 'd', 'r', 'm')
          AND dependency.objid IS NULL
    ), '[]'::jsonb),
    'routines', COALESCE((
        SELECT pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object(
                'schema', namespace.nspname,
                'name', routine.proname,
                'kind', routine.prokind,
                'identity_arguments', pg_catalog.pg_get_function_identity_arguments(routine.oid),
                'result', pg_catalog.pg_get_function_result(routine.oid),
                'language', language.lanname,
                'volatility', routine.provolatile,
                'parallel', routine.proparallel,
                'security_definer', routine.prosecdef,
                'leakproof', routine.proleakproof,
                'strict', routine.proisstrict,
                'definition', pg_catalog.pg_get_functiondef(routine.oid)
            )
            ORDER BY namespace.nspname, routine.proname,
                     pg_catalog.pg_get_function_identity_arguments(routine.oid)
        )
        FROM pg_catalog.pg_proc AS routine
        JOIN application_namespaces AS namespace ON namespace.oid = routine.pronamespace
        JOIN pg_catalog.pg_language AS language ON language.oid = routine.prolang
        LEFT JOIN extension_dependencies AS dependency
          ON dependency.classid = 'pg_proc'::regclass
         AND dependency.objid = routine.oid
         AND dependency.objsubid = 0
        WHERE dependency.objid IS NULL
    ), '[]'::jsonb),
    'triggers', COALESCE((
        SELECT pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object(
                'schema', namespace.nspname,
                'relation', relation.relname,
                'name', trigger_record.tgname,
                'enabled', trigger_record.tgenabled,
                'definition', pg_catalog.pg_get_triggerdef(trigger_record.oid, true)
            )
            ORDER BY namespace.nspname, relation.relname, trigger_record.tgname
        )
        FROM pg_catalog.pg_trigger AS trigger_record
        JOIN pg_catalog.pg_class AS relation ON relation.oid = trigger_record.tgrelid
        JOIN application_namespaces AS namespace ON namespace.oid = relation.relnamespace
        LEFT JOIN extension_dependencies AS dependency
          ON dependency.classid = 'pg_trigger'::regclass
         AND dependency.objid = trigger_record.oid
         AND dependency.objsubid = 0
        WHERE NOT trigger_record.tgisinternal
          AND dependency.objid IS NULL
          AND relation.relname <> '_sqlx_migrations'
    ), '[]'::jsonb),
    'policies', COALESCE((
        SELECT pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object(
                'schema', namespace.nspname,
                'relation', relation.relname,
                'name', policy.polname,
                'permissive', policy.polpermissive,
                'command', policy.polcmd,
                'roles', COALESCE((
                    SELECT pg_catalog.jsonb_agg(
                        CASE
                            WHEN role_oid = 0 THEN 'PUBLIC'
                            WHEN role_oid = identity.datdba THEN 'DATABASE_OWNER'
                            WHEN role_oid = (SELECT usesysid FROM pg_catalog.pg_user WHERE usename = current_user)
                                THEN 'CURRENT_ROLE'
                            ELSE 'EXTERNAL_ROLE'
                        END
                        ORDER BY role_oid
                    )
                    FROM unnest(policy.polroles) AS role_oid
                ), '[]'::jsonb),
                'using', pg_catalog.pg_get_expr(policy.polqual, policy.polrelid),
                'check', pg_catalog.pg_get_expr(policy.polwithcheck, policy.polrelid)
            )
            ORDER BY namespace.nspname, relation.relname, policy.polname
        )
        FROM pg_catalog.pg_policy AS policy
        JOIN pg_catalog.pg_class AS relation ON relation.oid = policy.polrelid
        JOIN application_namespaces AS namespace ON namespace.oid = relation.relnamespace
        CROSS JOIN database_identity AS identity
        WHERE relation.relname <> '_sqlx_migrations'
    ), '[]'::jsonb),
    'explicit_grants', COALESCE((
        SELECT pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object(
                'object_kind', grant_record.object_kind,
                'schema', grant_record.schema_name,
                'object', grant_record.object_name,
                'grantee', grant_record.grantee_kind,
                'privilege', grant_record.privilege_type,
                'grantable', grant_record.is_grantable
            )
            ORDER BY grant_record.object_kind, grant_record.schema_name, grant_record.object_name,
                     grant_record.grantee_kind, grant_record.privilege_type
        )
        FROM explicit_grants AS grant_record
    ), '[]'::jsonb)
)
