from pathlib import Path

from app.models.domain.scraper_runs import ScraperStats
from scripts import run_scrapers


def test_scraper_cli_returns_nonzero_when_source_reports_errors(monkeypatch) -> None:
    class FakeRunner:
        def list_scrapers(self) -> list[str]:
            return ["SciURLs"]

        def run_scraper_with_stats(self, name: str) -> ScraperStats:
            assert name == "SciURLs"
            return ScraperStats(errors=1, error_details=["E2B unavailable"])

    monkeypatch.setattr(run_scrapers, "ScraperRunner", FakeRunner)
    monkeypatch.setattr(run_scrapers, "init_db", lambda: None)
    monkeypatch.setattr(
        run_scrapers,
        "_get_backpressure_status",
        lambda: {"should_throttle": False},
    )
    monkeypatch.setattr(
        run_scrapers.sys,
        "argv",
        ["run_scrapers.py", "--scrapers", "SciURLs"],
    )

    assert run_scrapers.main() == 1


def test_scraper_cli_returns_zero_when_source_preserves_partial_progress(monkeypatch) -> None:
    class FakeRunner:
        def list_scrapers(self) -> list[str]:
            return ["Techmeme"]

        def run_scraper_with_stats(self, name: str) -> ScraperStats:
            assert name == "Techmeme"
            return ScraperStats(saved=1, errors=1)

    monkeypatch.setattr(run_scrapers, "ScraperRunner", FakeRunner)
    monkeypatch.setattr(run_scrapers, "init_db", lambda: None)
    monkeypatch.setattr(
        run_scrapers,
        "_get_backpressure_status",
        lambda: {"should_throttle": False},
    )
    monkeypatch.setattr(
        run_scrapers.sys,
        "argv",
        ["run_scrapers.py", "--scrapers", "Techmeme"],
    )

    assert run_scrapers.main() == 0


def test_scraper_cli_fails_aggregate_run_when_any_source_has_zero_progress(
    monkeypatch,
) -> None:
    class FakeRunner:
        def list_scrapers(self) -> list[str]:
            return ["Healthy", "Broken"]

        def run_scraper_with_stats(self, name: str) -> ScraperStats:
            if name == "Healthy":
                return ScraperStats(saved=2)
            assert name == "Broken"
            return ScraperStats(errors=1, error_details=["E2B unavailable"])

    monkeypatch.setattr(run_scrapers, "ScraperRunner", FakeRunner)
    monkeypatch.setattr(run_scrapers, "init_db", lambda: None)
    monkeypatch.setattr(
        run_scrapers,
        "_get_backpressure_status",
        lambda: {"should_throttle": False},
    )
    monkeypatch.setattr(run_scrapers.sys, "argv", ["run_scrapers.py"])

    assert run_scrapers.main() == 1


def test_recurring_scraper_cron_lets_runner_derive_enabled_aggregators() -> None:
    cron_line = Path("docker/crontab").read_text().splitlines()[0]

    assert "scripts/run_scrapers.py --show-stats" in cron_line
    assert "--scrapers" not in cron_line
