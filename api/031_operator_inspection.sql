CREATE TABLE IF NOT EXISTS operator_inspection (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    grant_id uuid NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '25 seconds',
    claim_id uuid,
    claimed_until timestamptz,
    receipt jsonb
);
CREATE INDEX IF NOT EXISTS operator_inspection_pending ON operator_inspection(project_id, requested_at)
    WHERE receipt IS NULL;

DO $$
DECLARE
    inspection_table regclass := 'operator_inspection'::regclass;
    columns text[];
    constraints text[];
BEGIN
    SELECT array_agg(
        format('%s %s %s %s', a.attname, format_type(a.atttypid, a.atttypmod),
            CASE WHEN a.attnotnull THEN 'required' ELSE 'nullable' END,
            coalesce(pg_get_expr(d.adbin, d.adrelid), ''))
        ORDER BY a.attnum)
    INTO columns
    FROM pg_attribute a
    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
    WHERE a.attrelid = inspection_table AND a.attnum > 0 AND NOT a.attisdropped;

    SELECT array_agg(pg_get_constraintdef(oid) ORDER BY contype)
    INTO constraints
    FROM pg_constraint WHERE conrelid = inspection_table;

    IF columns IS DISTINCT FROM ARRAY[
        'id uuid required ', 'project_id uuid required ', 'grant_id uuid required ',
        'requested_at timestamp with time zone required now()',
        'expires_at timestamp with time zone required (now() + ''00:00:25''::interval)',
        'claim_id uuid nullable ', 'claimed_until timestamp with time zone nullable ',
        'receipt jsonb nullable '
    ] OR constraints IS DISTINCT FROM ARRAY[
        format('FOREIGN KEY (project_id) REFERENCES %s(id)', 'projects'::regclass),
        'PRIMARY KEY (id)'
    ] OR NOT EXISTS (
        SELECT 1 FROM pg_index
        WHERE indrelid = inspection_table
          AND indexrelid = to_regclass('operator_inspection_pending')
          AND indisvalid AND indisready
          AND pg_get_indexdef(indexrelid) = format(
            'CREATE INDEX operator_inspection_pending ON %I.operator_inspection USING btree (project_id, requested_at) WHERE (receipt IS NULL)',
            (SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.oid = inspection_table))
    ) THEN
        RAISE EXCEPTION 'Migration 031: incompatible operator_inspection schema';
    END IF;
END
$$;
