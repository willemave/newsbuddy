CREATE TABLE operator_incident_batches (
    batch_id uuid PRIMARY KEY,
    operation text NOT NULL,
    actor text NOT NULL,
    reason text NOT NULL,
    targets jsonb NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
