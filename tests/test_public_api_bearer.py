from test_site_management import cloud  # noqa: F401


def test_public_api_requires_bearer_header_and_rejects_query_tokens(cloud):
    server, client = cloud
    server.CFG.setdefault("public_api", {})["token"] = "test-public-token"

    query_only = client.get("/api/public/overview", params={"token": "test-public-token"})
    assert query_only.status_code == 401

    bearer = client.get("/api/public/overview",
                        headers={"Authorization": "Bearer test-public-token"})
    assert bearer.status_code == 200

    # HTTP authentication scheme names are case-insensitive.
    lowercase = client.get("/api/public/overview",
                           headers={"Authorization": "bearer test-public-token"})
    assert lowercase.status_code == 200
