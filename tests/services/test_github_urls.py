from app.services.github_urls import normalize_github_file_url_to_raw, parse_github_file_url


def test_parse_github_blob_file_url() -> None:
    url = "https://github.com/deepseek-ai/DeepSpec/blob/main/DSpark_paper.pdf"

    parsed = parse_github_file_url(url)

    assert parsed is not None
    assert parsed.owner == "deepseek-ai"
    assert parsed.repo == "DeepSpec"
    assert parsed.ref == "main"
    assert parsed.path == "DSpark_paper.pdf"
    assert parsed.filename == "DSpark_paper.pdf"
    assert parsed.repo_full_name == "deepseek-ai/DeepSpec"
    assert parsed.repo_url == "https://github.com/deepseek-ai/DeepSpec"
    assert parsed.canonical_blob_url == url
    assert parsed.raw_url == (
        "https://raw.githubusercontent.com/deepseek-ai/DeepSpec/main/DSpark_paper.pdf"
    )
    assert parsed.is_pdf is True


def test_normalize_github_raw_file_url_keeps_raw_url() -> None:
    raw_url = "https://raw.githubusercontent.com/deepseek-ai/DeepSpec/main/DSpark_paper.pdf"

    assert normalize_github_file_url_to_raw(raw_url) == raw_url


def test_parse_github_tree_url_is_not_a_file_url() -> None:
    assert parse_github_file_url("https://github.com/deepseek-ai/DeepSpec/tree/main/docs") is None
