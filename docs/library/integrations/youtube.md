# YouTube Media Processing

Direct YouTube video URLs are classified by the Rust content worker and handed
to the native media worker as podcast-like content. The provider adapter runs
the installed `yt-dlp` executable, then the media worker uses the normal
transcription and summarization pipeline. Channel and playlist scraping are not
part of this path.

## Configuration

The media worker accepts:

| Variable | Default | Purpose |
| --- | --- | --- |
| `YT_DLP_BINARY` | `yt-dlp` | Explicit executable path |
| `NEWSLY_MEDIA_YT_DLP_TIMEOUT_SECONDS` | `600` | Download deadline |
| `YOUTUBE_COOKIES_PATH` | `secrets/youtube_cookies.txt` | Optional Netscape cookie file |
| `YOUTUBE_PLAYER_CLIENT` | `mweb` | yt-dlp YouTube player client |
| `YOUTUBE_PO_TOKEN_PROVIDER` | `bgutilhttp` | `bgutilhttp`, `webpoclient`, or `none` |
| `YOUTUBE_PO_TOKEN_BASE_URL` | `http://127.0.0.1:4416` | PO-token provider endpoint |

The Rust application image installs `yt-dlp` and `ffmpeg`. The repository
Compose topologies run the pinned provider as `bgutil-provider` and configure
the media worker to use `http://bgutil-provider:4416`. For native development,
run the same pinned helper container on loopback:

```bash
scripts/start_bgutil_provider.sh
```

If a deployment selects a different provider, its endpoint must remain
reachable from the media worker container; do not use a host-only loopback
address there.

## Troubleshooting

Run the same executable the worker uses and inspect its provider diagnostics:

```bash
yt-dlp --verbose --skip-download "https://www.youtube.com/watch?v=VIDEO_ID"
```

For 403 or BotGuard failures, verify the configured PO provider is reachable,
the cookie file exists inside the worker when configured, and the pinned image
contains a current `yt-dlp`. Private, deleted, age-gated, and region-restricted
videos may still be terminal failures.

Use `newsly-admin tasks failures` and the worker container logs to correlate a
failure with its task and provider error. Do not recreate a Python diagnostic
or media-processing path.
