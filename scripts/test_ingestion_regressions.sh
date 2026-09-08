#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${DATABASE_URL:?Use the local runtime environment or the release gate disposable database}"
authority="${DATABASE_URL#*://}"
authority="${authority##*@}"
case "${authority%%[:/]*}" in
  localhost|127.0.0.1) ;;
  *) echo "Ingestion regressions require local PostgreSQL" >&2; exit 2 ;;
esac

cases=(
  'newsly-worker scrape::tests::scheduled_feed_cycles_never_cross_the_persisted_frontier'
  'newsly-providers briefing_links::tests::citation_corpus_preserves_labels_and_exact_identity'
  'newsly-providers briefing_composition::tests::citation_corpus_validates_brackets_but_rejects_bare_and_code_uris'
  'newsly-worker briefing_refresh::normalize::tests::citation_corpus_survives_coverage_runs_and_narration'
  'newsly-db briefing_attempts::tests::observed_attempts_survive_rejection_deduplicate_and_respect_lease'
  'newsly-worker briefing_refresh::handler::retry_tests::correction_budget_survives_new_jobs_and_changed_input_recovers'
  'newsly-db pipeline_monitoring::tests::durable_alert_deduplicates_retries_and_recovers'
  'newsly-db pipeline_monitoring::tests::growing_ready_media_is_detected_before_two_hours'
  'newsly-db incident_batches::tests::incident_inventory_preserves_reads_and_replays_are_noops'
  'newsly-db incident_batches::tests::artwork_migration_never_enqueues_and_inventory_excludes_exhausted_or_archived'
  'newsly-worker image_generation::finalizer::tests::artwork_publication_commits_files_readiness_and_briefing_fanout'
  'newsly-worker image_generation::finalizer::tests::changed_summary_cannot_publish_artwork_or_briefing_fanout'
  'newsly-scheduler runner::delivery_tests::alert_receiver_success_throttle_failure_and_timeout'
)
for entry in "${cases[@]}"; do
  read -r package test_name <<<"$entry"
  # A removed or renamed test must fail the gate, not silently pass with zero tests.
  listing="$(cargo test --manifest-path "$repo_root/rust/Cargo.toml" --locked -p "$package" --lib "$test_name" -- --list)"
  rg --fixed-strings --line-regexp "$test_name: test" <<<"$listing" >/dev/null
  cargo test --manifest-path "$repo_root/rust/Cargo.toml" --locked -p "$package" --lib "$test_name" -- --exact
done
