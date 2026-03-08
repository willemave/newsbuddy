# YouTube Extraction Troubleshooting

## Common Issues

### 1. yt-dlp Returns None

**Symptoms:**
- Error: "Failed to extract video information from [url]"
- Video may be unavailable, private, region-restricted, or age-restricted

**Causes:**
- Video is private or deleted
- Video requires age verification (18+)
- Geographic restrictions
- YouTube rate limiting
- Outdated yt-dlp version

**Solutions:**

#### Update yt-dlp (Most Common Fix)
YouTube frequently changes their API. Update yt-dlp regularly:

```bash
# Using uv
uv add --upgrade yt-dlp

# Or in Docker
docker-compose build --no-cache workers
```

**Recommended:** Update yt-dlp at least monthly or whenever extraction errors increase.

#### Check Video Accessibility
Test the URL manually:

```bash
python scripts/diagnose_youtube.py "https://youtu.be/VIDEO_ID"
```

#### Add Cookie Support (For Age-Restricted Videos)
Some videos require authentication. Export cookies from your browser:

1. Install browser extension: "Get cookies.txt LOCALLY"
2. Visit YouTube and sign in
3. Export cookies to `cookies.txt`
4. Update `youtube_strategy.py`:

```python
self.ydl_opts = {
    ...
    "cookiefile": "/path/to/cookies.txt",
}
```

### 2. Rate Limiting

**Symptoms:**
- Multiple consecutive failures
- 429 HTTP errors in logs

**Solutions:**
- Add delays between requests
- Rotate user agents
- Use residential IP or VPN
- Implement exponential backoff

### 3. nsig Extraction Failed

**Symptoms:**
- Warning: "nsig extraction failed: Some formats may be missing"

**Impact:** Low - Usually doesn't prevent extraction, just limits available formats

**Solution:** Update yt-dlp to latest version

## Monitoring

### Check yt-dlp Version
```bash
python -c "import yt_dlp; print(yt_dlp.version.__version__)"
```

### Test Extraction
```bash
# Quick test
python scripts/diagnose_youtube.py "https://youtu.be/VIDEO_ID"

# Test with production config
python -c "
from app.processing_strategies.youtube_strategy import YouTubeProcessorStrategy
from app.http_client.robust_http_client import RobustHttpClient
import asyncio

async def test():
    client = RobustHttpClient(max_retries=3)
    strategy = YouTubeProcessorStrategy(client)
    data = await strategy.extract_data(b'', 'YOUR_URL_HERE')
    print(f'Title: {data[\"title\"]}')

asyncio.run(test())
"
```

## Maintenance Schedule

| Task | Frequency | Command |
|------|-----------|---------|
| Update yt-dlp | Weekly | `uv add --upgrade yt-dlp` |
| Check error logs | Daily | Review `/admin/logs` |
| Test popular videos | Weekly | Run diagnostic script |
| Rebuild Docker images | After yt-dlp update | `docker-compose build workers` |

## Recent Changes

### 2025-10-17: Improved Error Handling
- Changed `ignoreerrors: False` to get better error messages
- Added specific handling for `DownloadError`
- Include yt-dlp version in error logs
- Added user agent to reduce bot detection

### Error Log Analysis
Check for patterns in `/logs/errors/content_worker_*.jsonl`:

```bash
# Count YouTube errors by type
grep "youtube" logs/errors/content_worker_*.jsonl | \
  jq -r '.error_message' | \
  sort | uniq -c | sort -rn

# Find which videos fail most
grep "youtube" logs/errors/content_worker_*.jsonl | \
  jq -r '.context_data.url' | \
  sort | uniq -c | sort -rn | head -10
```

## References

- [yt-dlp GitHub](https://github.com/yt-dlp/yt-dlp)
- [yt-dlp Wiki - PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)
- [Supported Sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
