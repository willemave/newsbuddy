"""Integration tests for user data isolation."""


def test_users_see_only_their_own_knowledge_saves(
    auth_headers_factory,
    client_factory,
    content_factory,
    favorite_factory,
    status_entry_factory,
    user_factory,
):
    """Test that users only see their own saved knowledge."""
    user1 = user_factory(apple_id="user1", email="user1@example.com")
    user2 = user_factory(apple_id="user2", email="user2@example.com")
    content1 = content_factory(title="Article 1", url="https://example.com/article1")
    content2 = content_factory(title="Article 2", url="https://example.com/article2")

    favorite_factory(user=user1, content=content1)
    favorite_factory(user=user2, content=content2)
    for user in (user1, user2):
        for content in (content1, content2):
            status_entry_factory(user=user, content=content, status="inbox")

    with client_factory(user=user1) as client:
        headers = auth_headers_factory(user1)
        response = client.get(f"/api/content/{content1.id}", headers=headers)
        assert response.status_code == 200
        assert response.json()["is_saved_to_knowledge"] is True

        response = client.get(f"/api/content/{content2.id}", headers=headers)
        assert response.status_code == 200
        assert response.json()["is_saved_to_knowledge"] is False

    with client_factory(user=user2) as client:
        headers = auth_headers_factory(user2)
        response = client.get(f"/api/content/{content1.id}", headers=headers)
        assert response.status_code == 200
        assert response.json()["is_saved_to_knowledge"] is False

        response = client.get(f"/api/content/{content2.id}", headers=headers)
        assert response.status_code == 200
        assert response.json()["is_saved_to_knowledge"] is True


def test_users_see_only_their_own_read_status(
    auth_headers_factory,
    client_factory,
    content_factory,
    read_status_factory,
    status_entry_factory,
    user_factory,
):
    """Test that users only see their own read status."""
    user1 = user_factory(apple_id="user1_read", email="user1_read@example.com")
    user2 = user_factory(apple_id="user2_read", email="user2_read@example.com")
    content = content_factory(
        title="Article for Read Test",
        url="https://example.com/article_read",
    )

    read_status_factory(user=user1, content=content)
    status_entry_factory(user=user1, content=content, status="inbox")
    status_entry_factory(user=user2, content=content, status="inbox")

    with client_factory(user=user1) as client:
        response = client.get(
            f"/api/content/{content.id}",
            headers=auth_headers_factory(user1),
        )
        assert response.status_code == 200
        assert response.json()["is_read"] is True

    with client_factory(user=user2) as client:
        response = client.get(
            f"/api/content/{content.id}",
            headers=auth_headers_factory(user2),
        )
        assert response.status_code == 200
        assert response.json()["is_read"] is False


def test_knowledge_save_only_affects_current_user(
    auth_headers_factory,
    client_factory,
    content_factory,
    status_entry_factory,
    user_factory,
):
    """Test that saving content to knowledge only affects the current user."""
    user1 = user_factory(
        apple_id="user1_fav_action",
        email="user1_fav@example.com",
    )
    user2 = user_factory(
        apple_id="user2_fav_action",
        email="user2_fav@example.com",
    )
    content = content_factory(
        title="Knowledge Save Action Test",
        url="https://example.com/fav_action",
    )
    status_entry_factory(user=user1, content=content, status="inbox")
    status_entry_factory(user=user2, content=content, status="inbox")

    with client_factory(user=user1) as client:
        response = client.post(
            f"/api/content/{content.id}/knowledge",
            headers=auth_headers_factory(user1),
        )
        assert response.status_code == 200
        assert response.json()["is_saved_to_knowledge"] is True

    with client_factory(user=user2) as client:
        response = client.get(
            f"/api/content/{content.id}",
            headers=auth_headers_factory(user2),
        )
        assert response.status_code == 200
        assert response.json()["is_saved_to_knowledge"] is False
