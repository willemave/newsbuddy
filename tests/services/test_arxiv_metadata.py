from __future__ import annotations

from types import SimpleNamespace

from app.services.arxiv_metadata import (
    extract_arxiv_id,
    fetch_arxiv_source_metadata,
    parse_arxiv_atom_source_metadata,
)

ARXIV_ATOM_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2509.15194v2</id>
    <updated>2026-01-02T00:00:00Z</updated>
    <published>2026-01-01T00:00:00Z</published>
    <title>Paper Title With Wrapped Whitespace</title>
    <summary>
      We introduce a new model for source metadata. It improves display grounding.
      The third sentence should not be needed in the short synopsis.
    </summary>
    <author>
      <name>Ada Lovelace</name>
      <arxiv:affiliation>Analytical Engines Lab</arxiv:affiliation>
    </author>
    <author>
      <name>Grace Hopper</name>
    </author>
    <arxiv:comment>12 pages</arxiv:comment>
    <arxiv:journal_ref>Proceedings of Example 2026</arxiv:journal_ref>
    <arxiv:doi>10.1234/example</arxiv:doi>
    <link title="pdf" href="http://arxiv.org/pdf/2509.15194v2"
          rel="related" type="application/pdf"/>
    <arxiv:primary_category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>
"""


def test_extract_arxiv_id_handles_abs_pdf_and_raw_ids() -> None:
    assert extract_arxiv_id("https://arxiv.org/abs/2509.15194v2?context=cs") == "2509.15194v2"
    assert extract_arxiv_id("https://www.arxiv.org/pdf/2509.15194.pdf") == "2509.15194"
    assert extract_arxiv_id("hep-th/9901001v1") == "hep-th/9901001v1"
    assert extract_arxiv_id("https://example.com/abs/2509.15194") is None


def test_parse_arxiv_atom_source_metadata_preserves_affiliations() -> None:
    metadata = parse_arxiv_atom_source_metadata(ARXIV_ATOM_RESPONSE, requested_id="2509.15194v2")

    assert metadata is not None
    assert metadata.source_id == "2509.15194v2"
    assert metadata.canonical_abs_url == "https://arxiv.org/abs/2509.15194v2"
    assert metadata.pdf_url == "https://arxiv.org/pdf/2509.15194v2"
    assert metadata.brief_synopsis == (
        "We introduce a new model for source metadata. It improves display grounding."
    )
    assert metadata.authors[0].name == "Ada Lovelace"
    assert metadata.authors[0].affiliation == "Analytical Engines Lab"
    assert metadata.authors[0].affiliation_source == "arxiv_api"
    assert metadata.authors[0].confidence == "direct"
    assert metadata.authors[1].name == "Grace Hopper"
    assert metadata.authors[1].affiliation is None
    assert metadata.authors[1].affiliation_source == "missing"
    assert [category.term for category in metadata.categories] == ["cs.AI", "cs.CL"]
    assert metadata.categories[0].primary is True
    assert metadata.doi == "10.1234/example"


def test_fetch_arxiv_source_metadata_uses_api_query() -> None:
    calls: list[str] = []

    class _HttpClient:
        def get(self, url: str, timeout: float | None = None):
            calls.append(url)
            assert timeout == 10
            return SimpleNamespace(text=ARXIV_ATOM_RESPONSE)

    metadata = fetch_arxiv_source_metadata(
        "https://arxiv.org/abs/2509.15194v2",
        http_client=_HttpClient(),
    )

    assert metadata is not None
    assert calls
    assert "id_list=2509.15194v2" in calls[0]
