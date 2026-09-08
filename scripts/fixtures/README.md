# Launch visual fixtures

`launch_briefing.sql` adds a richer, provider-free visual edition after the normal
Rust `newsly-admin e2e seed --namespace appstore-launch --confirm-local` seed.
Use a migrated disposable local database named `newsbuddy_launch_*`.
Load and normalize its database environment with `scripts/lib/rust_runtime.sh`,
then run:

```sh
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/fixtures/launch_briefing.sql
```

The overlay replaces the seeded user's Briefing with eight source-linked articles:
four Space, two Earth, and two Energy. Each has an introduction, context, and a
takeaway, with matching article summaries. Source URLs are in the dataset. These
are curated evergreen explainers, not claims of breaking news or provider output.
Publication dates are not invented. An existing NASA image in the launch fixture
is retained when available; a fresh standard seed works without it.

The transaction rejects other database names and missing fixture namespaces.
Rerunning replaces the edition without duplicate stories. Read state resets for
the demo user. Normal small E2E fixtures and personal data are not used.
Restart the locally configured Simulator app after applying to refresh its cache.
No workers or paid model calls are required. The overlay does not create audio.
