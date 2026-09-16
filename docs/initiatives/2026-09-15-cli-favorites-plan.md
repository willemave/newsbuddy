# CLI: last ten favorited articles

Status: implemented locally; authenticated verification completed against namespaced fixtures. See `test-results/cli-favorites/` and docs/log.md for validation evidence.

## User outcome

`newsbuddy content favorites --limit 10` returns the authenticated user's ten most recently saved articles, newest save first. The default limit is ten. "Favorites" maps to existing Knowledge saves; it does not introduce a second saved state. Publication/ingestion time does not determine order. Podcasts and other content types do not count toward the article limit. Fewer than ten is successful only when the saved feed is exhausted.

## Implementation

1. Add `content favorites` to the Rust CLI argument tree, with a validated limit of 1–100. Preserve the existing JSON/text envelope and identify this command as `content.favorites`. Include article IDs, titles, URLs, and `knowledge_saved_at` in a validated, typed result that preserves unknown fields and owns text rendering.
2. Add a client method for existing `GET /api/content/knowledge/list`. Keep credentials and requests in the current HTTP client. No database access, API change, or migration is needed.
3. Traverse the endpoint's cursor pages in server order, retaining articles until the requested number is collected. Request fixed pages of 100 saved items, retain only the requested number of articles, and expose no continuation cursor for this bounded collection. Follow continuation even when a page contains no articles. Reject malformed or repeated cursors; fail clearly on transport/authentication errors rather than return a misleading successful partial list. Keep the whole operation within a bounded deadline. Return a collection result without claiming the server's unfiltered pagination metadata describes the filtered articles.
4. Make this the first read-only quick-start example in the CLI README. Explain Knowledge terminology, save-time ordering, fewer-than-limit behavior, local authentication, and the distinction between the source-built Rust binary and the older installed Homebrew binary. Add the behavior to the Knowledge law and record implementation/validation in docs/log.md.

## Validation

- Argument defaults, valid limits, invalid limits, JSON/text output.
- Mock HTTP assertions for endpoint, bearer authentication, query parameters, and the result envelope.
- Mixed article/podcast pages, pages with no articles, multiple pages, fewer than ten articles, empty library, tied save timestamps preserving server order, missing/malformed continuation, repeated cursor, timeout, and HTTP 401.
- Retain and run the full existing CLI suite, formatting, CLI build, and warning-denied Clippy for all CLI targets.
- Start the local API, create a new key for the intended existing local user, verify an ordinary content read, then run the new command. Compare IDs and save timestamps with the Knowledge API, including type filtering. If the local account has fewer than ten saved articles, report the actual count and use fixtures to prove the ten-item case.
- Do not mutate saved/read state during the live read test. Provider-backed commands are outside this use case.

## Generate a local test key using existing functionality

1. Keep the API running in a terminal from the repository root:

   ```sh
   DEVELOPER_DIR=/Library/Developer/CommandLineTools scripts/start_services.sh server --env-file .env --port 8000 --skip-migrate
   ```

   This assumes the existing local database is migrated. The Command Line Tools override is the working build setup on this machine; it does not accept the pending Xcode license.

2. Open `http://127.0.0.1:8000/admin/api-keys`. It redirects to `/auth/admin/login` when needed. Use the `ADMIN_PASSWORD` configured in the local `.env`.
3. Select the existing local user whose Knowledge library should be tested. Click **Create API Key** and copy the displayed key; it is revealed once. Do not select the system administrator as a substitute for the intended reader.
4. Set the local server and supply the copied key through a hidden shell prompt, avoiding a literal key in shell history:

   ```zsh
   rust/target/debug/newsbuddy config set server http://127.0.0.1:8000
   read -rs 'NEWSBUDDY_API_KEY?Paste local API key: '
   export NEWSBUDDY_API_KEY
   rust/target/debug/newsbuddy content list --content-type article --limit 1
   # After implementation:
   rust/target/debug/newsbuddy content favorites --limit 10
   unset NEWSBUDDY_API_KEY
   ```

   The environment override avoids overwriting the saved API key. Existing `config set api-key` can persist credentials if desired, but is unnecessary for this test.
5. Revoke the temporary key on the same admin page when testing is finished.

Normal end-user alternative: `newsbuddy auth login` with the iOS app pointed at this same local server. Local admin creation avoids needing the iOS approval flow for this development test.

## Completion evidence

Record the tested source SHA, gate results, authenticated local result count, article IDs/save order, and any missing local data. Do not claim that the Homebrew binary includes this command until separately installed or released. No commit, push, deployment, or Homebrew update is part of this plan.
