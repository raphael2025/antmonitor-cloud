"""Independent configuration and notification lifecycle boundary regressions."""
from pathlib import Path

import pytest

from test_settings_integrity import fresh_settings
from test_site_management import cloud, login, write  # noqa: F401


def test_seen_invalid_overlay_cannot_become_first_install_when_removed(tmp_path):
    path = tmp_path / "settings.json"
    value = fresh_settings(path)
    assert value.load_settings() == {}
    path.write_text("{broken")
    with pytest.raises(value.SettingsError):
        value.load_settings()
    path.unlink()
    with pytest.raises(value.SettingsError):
        value.load_settings()
    with pytest.raises(value.SettingsError):
        value.password_overrides()
    with pytest.raises(value.SettingsError):
        value.update_settings({"alerts": {"enabled": False}})
    assert not path.exists()


def test_removing_a_seen_corrupt_overlay_cannot_reenable_yaml_authentication(cloud):
    server, client = cloud
    login(client)
    path = Path(server.appconfig.SETTINGS_FILE)
    assert not path.exists()
    path.write_text("{broken")
    assert client.get("/api/me").status_code == 500
    path.unlink()
    # An observed but unreadable security overlay is no longer a first install.
    # Until it is explicitly repaired, neither existing sessions nor factory
    # credentials should start working again just because the bad file vanished.
    assert client.get("/api/me").status_code == 500
    assert write(client, "POST", "/api/login", json={
        "username": "test-admin", "password": "admin-test-password"}).status_code == 500
    path.write_text("{}")
    assert client.get("/api/me").json()["authenticated"] is True
