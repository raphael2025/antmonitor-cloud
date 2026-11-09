import auth


def test_unknown_roles_do_not_fall_back_to_global_viewer(monkeypatch):
    monkeypatch.setattr(auth.appconfig, "password_overrides", lambda: {})
    cfg = {"auth": {"enabled": True, "users": [
        {"username": "future-customer", "password": "long-test-password", "role": "customer"},
        {"username": "staff-viewer", "password": "long-test-password", "role": "viewer"},
        {"username": "staff-admin", "password": "long-test-password", "role": "admin"},
    ]}}

    users = auth._users(cfg)
    assert users["future-customer"]["role"] == "disabled"
    assert users["staff-viewer"]["role"] == "viewer"
    assert users["staff-admin"]["role"] == "admin"

    token, reason = auth.login(cfg, "future-customer", "long-test-password")
    assert token is None
    assert reason == "role_disabled"

    viewer_token, viewer_reason = auth.login(cfg, "staff-viewer", "long-test-password")
    assert viewer_reason is None and viewer_token
    auth.logout(viewer_token)
