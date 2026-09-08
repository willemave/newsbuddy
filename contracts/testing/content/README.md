# Content artifact response fixtures

`artifact_responses.json` is shared by Rust presenter regression tests and native
`ArtifactResponseFixtureTests`. Each case contains normalized source metadata,
the expected list and current detail responses, and the previous duplicated detail
response for decoding/sharing comparisons.

The four envelopes cover findings, argument, mental model and playbook. Rust
checks the production envelope schema and exact API projections. Swift decodes
these same response objects through the generated API model and handwritten
reader model, then compares medium/full sharing before and after duplicate removal.
Keep required preview and selection-trace fields complete when changing a case.

Legacy, malformed and summary-kind mismatch baselines remain separate in
`rust/crates/newsly-api/src/content_read/presentation/baseline.json`.
These compact synthetic fixtures contain no production source bodies.
