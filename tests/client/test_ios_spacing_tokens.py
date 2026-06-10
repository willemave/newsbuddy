import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VIEWS_ROOT = REPO_ROOT / "client/newsly/newsly/Views"
DESIGN_TOKENS = VIEWS_ROOT / "Shared/DesignTokens.swift"


def test_ios_screen_margin_has_single_source_of_truth() -> None:
    source = DESIGN_TOKENS.read_text()

    assert "static let appHorizontalMargin: CGFloat = 20" in source
    for alias in (
        "screenHorizontal",
        "fastReadHorizontal",
        "readerHorizontal",
        "chatHorizontal",
    ):
        assert f"static let {alias}: CGFloat = appHorizontalMargin" in source


def test_ios_views_use_shared_margin_token_for_screen_gutters() -> None:
    old_aliases = re.compile(
        r"Spacing\.(screenHorizontal|fastReadHorizontal|readerHorizontal|chatHorizontal)"
    )
    offenders: list[str] = []

    for path in sorted(VIEWS_ROOT.rglob("*.swift")):
        if path == DESIGN_TOKENS:
            continue
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if old_aliases.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_primary_screens_do_not_hardcode_large_horizontal_gutters() -> None:
    checked_paths = [
        *sorted(VIEWS_ROOT.glob("*.swift")),
        *sorted((VIEWS_ROOT / "Onboarding").glob("*.swift")),
        VIEWS_ROOT / "Components/ArticleCardView.swift",
        VIEWS_ROOT / "Components/CardStackView.swift",
        VIEWS_ROOT / "Components/ChatStatusBanner.swift",
        VIEWS_ROOT / "Components/SuggestionDetailSheet.swift",
    ]
    hardcoded_outer_gutter = re.compile(r"\.padding\(\.horizontal,\s*(20|24|28|40)\)")
    offenders: list[str] = []

    for path in checked_paths:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if hardcoded_outer_gutter.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []
