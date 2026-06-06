# app/scraping/aggregators/

Source folder: `app/scraping/aggregators`

## Purpose
News aggregator scraper package for global fast-news sources configured by `config/aggregators.yml`.

## Runtime behavior
- `config.py` validates the YAML shape for Hacker News, RSS-cluster aggregators, grouped HTML aggregators, and topic HTML aggregators.
- `registry.py` maps enabled YAML entries to scraper subclasses and exposes known aggregator keys.
- The default config currently includes Hacker News, Techmeme, Mediagazer, Memeorandum, SciURLs, FinURLs, and Brutalist Report.
- Aggregator metadata is persisted in `raw_metadata.aggregator`; per-user subscriptions use `scraper_type='aggregator'` and `feed_url='aggregator://<key>'`.
- Brutalist Report is topic-aware; topic selections are matched against `raw_metadata.aggregator.topic`.

## Important files
| File | Purpose |
|---|---|
| `base.py` | Shared aggregator item normalization helpers. |
| `config.py` | Pydantic config models and YAML loader. |
| `registry.py` | Scraper factory and known-key registry. |
| `hackernews.py` | Hacker News Firebase/API scraper. |
| `_rss_cluster.py` | Shared Techmeme-network RSS cluster parser. |
| `techmeme.py`, `mediagazer.py`, `memeorandum.py` | RSS-cluster subclasses. |
| `_html_grouped.py` | Shared SciURLs/FinURLs grouped HTML parser. |
| `sciurls.py`, `finurls.py` | Grouped HTML subclasses. |
| `brutalist.py` | Brutalist Report topic scraper. |

## Integration points
- `app/scraping/runner.py` loads these scrapers into scheduled runs.
- Visibility filtering for global aggregator rows lives in news feed services.
