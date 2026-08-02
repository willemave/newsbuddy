import pytest


@pytest.mark.parametrize(
    ("path", "expected_text"),
    [
        ("/", "Newsbuddy"),
        ("/privacy", "Privacy Policy"),
        ("/support", "Support"),
        ("/terms", "Terms of Use"),
    ],
)
def test_public_release_pages(client_factory, path: str, expected_text: str) -> None:
    with client_factory(authenticate=False) as client:
        response = client.get(path)

    assert response.status_code == 200
    assert expected_text in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
