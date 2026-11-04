"""Security overrides must fail closed; notification settings commit before activation."""
import copy
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

import appconfig
import auth
import notify
from test_site_management import cloud, login, write  # noqa: F401


def fresh_settings(path):
    spec = importlib.util.spec_from_file_location("isolated_appconfig", appconfig.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SETTINGS_FILE = str(path)
    return module


@pytest.mark.parametrize("content", ["{broken", "", "[]", "null", '{"passwords":[]}',
                                      '{"ingest":{"tokens":[]}}'])
def test_existing_invalid_settings_never_become_factory_defaults(tmp_path, content):
    path = tmp_path / "settings.json"
    path.write_text(content)
    value = fresh_settings(path)
    with pytest.raises(value.SettingsError):
        value.load_settings()
    with pytest.raises(value.SettingsError):
        value.update_settings({"alerts": {"enabled": False}})
    assert path.read_text() == content


@pytest.mark.skipif(sys.platform != "linux", reason="Linux symlink lifecycle")
def test_dangling_settings_link_is_existing_unreadable_configuration(tmp_path):
    path = tmp_path / "settings.json"
    path.symlink_to(tmp_path / "missing-target.json")
    value = fresh_settings(path)
    assert path.is_symlink() and not path.exists()
    with pytest.raises(value.SettingsError):
        value.load_settings()
    with pytest.raises(value.SettingsError):
        value.update_settings({"alerts": {"enabled": False}})
    assert path.is_symlink()
    path.unlink()
    with pytest.raises(value.SettingsError):
        value.password_overrides()
    path.write_text("{}")
    assert value.load_settings() == {}


def test_first_install_without_settings_can_use_yaml(tmp_path):
    value = fresh_settings(tmp_path / "settings.json")
    assert value.load_settings() == {}
    assert value.password_overrides() == {}


@pytest.mark.parametrize("content", ["{broken", '{"passwords": []}'])
def test_first_missing_settings_is_not_a_trusted_fallback_for_corrupt_file(tmp_path, content):
    path = tmp_path / "settings.json"
    value = fresh_settings(path)
    assert value.load_settings() == {}
    path.write_text(content)
    with pytest.raises(value.SettingsError):
        value.load_settings()
    with pytest.raises(value.SettingsError):
        value.password_overrides()
    with pytest.raises(value.SettingsError):
        value.update_settings({"alerts": {"enabled": False}})
    assert path.read_text() == content


def test_corrupt_first_settings_closes_authentication_with_recoverable_error(cloud):
    server, client = cloud
    login(client)
    path = Path(server.appconfig.SETTINGS_FILE)
    assert not path.exists()
    path.write_text("{broken")
    # No valid settings overlay was ever seen. Both an existing session and a
    # fresh login must stop before any protected operation uses YAML defaults.
    for response in (client.get("/api/me"), client.get("/api/settings"),
                     write(client, "POST", "/api/login", json={
                         "username": "test-admin", "password": "admin-test-password"})):
        assert response.status_code == 500
        assert "安全配置不可用" in response.json()["detail"]
        assert str(path) not in response.text
    assert path.read_text() == "{broken"
    # An operator can restore the file without restarting the service.
    path.write_text("{}")
    assert client.get("/api/me").json()["authenticated"] is True


@pytest.mark.parametrize("damage", ["invalid_json", "invalid_passwords", "removed", "unreadable"])
def test_runtime_keeps_last_trusted_credentials_on_config_error(tmp_path, monkeypatch, damage):
    path = tmp_path / "settings.json"
    value = fresh_settings(path)
    monkeypatch.setattr(auth, "appconfig", value)
    good = {"passwords": {"operator": "new-password"},
            "ingest": {"tokens": {"site-a": ""}}}
    path.write_text(json.dumps(good))
    cfg = {"auth": {"enabled": True, "users": [
        {"username": "operator", "password": "old-password", "role": "admin"}]}}
    assert value.load_settings() == good
    assert auth.login(cfg, "operator", "old-password")[0] is None
    assert auth.login(cfg, "operator", "new-password")[0]
    if damage == "removed":
        path.unlink()
    elif damage == "unreadable":
        monkeypatch.setattr(value, "open", Mock(side_effect=PermissionError("fixture")), raising=False)
    else:
        path.write_text("{bad" if damage == "invalid_json" else '{"passwords": []}')
    assert value.load_settings() == good
    assert auth.login(cfg, "operator", "old-password")[0] is None
    assert auth.login(cfg, "operator", "new-password")[0]
    runtime = {"ingest": {"tokens": {"site-a": "old-report-token"}}}
    value.apply_settings(runtime, value.load_settings())
    assert runtime["ingest"]["tokens"]["site-a"] == ""
    with pytest.raises(value.SettingsError):
        value.update_settings({"alerts": {"enabled": False}})


def test_password_update_refreshes_cache_and_external_repair_is_picked_up(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    value = fresh_settings(path)
    monkeypatch.setattr(auth, "appconfig", value)
    value.set_password_override("operator", "first-password")
    assert value.password_overrides() == {"operator": "first-password"}
    value.set_password_override("operator", "second-password")
    path.write_text("broken")
    assert value.password_overrides() == {"operator": "second-password"}
    path.write_text(json.dumps({"passwords": {"operator": "third-password"}}))
    assert value.password_overrides() == {"operator": "third-password"}


@pytest.mark.parametrize("error", [PermissionError("read only"), appconfig.SettingsError("invalid JSON")])
def test_failed_notification_save_does_not_change_runtime_or_disk(cloud, monkeypatch, error):
    server, client = cloud
    login(client)
    initial = {"enabled": True, "cooldown": 1800,
               "telegram": {"enabled": True, "bot_token": "existing-token", "chat_id": "chat"}}
    server.appconfig.update_settings({"alerts": initial})
    server.appconfig.apply_settings(server.CFG, {"alerts": initial})
    observer = notify.Notifier(server.CFG, server.CONN)
    before = copy.deepcopy(server.CFG["alerts"])
    disk = Path(server.appconfig.SETTINGS_FILE).read_bytes()
    monkeypatch.setattr(server.appconfig, "update_settings", Mock(side_effect=error))
    response = write(client, "POST", "/api/settings", json={"alerts": {
        "enabled": False, "telegram": {"bot_token": "replacement-token"}}})
    assert response.status_code == 500
    assert server.CFG["alerts"] == observer.cfg == before
    assert Path(server.appconfig.SETTINGS_FILE).read_bytes() == disk
    assert not any(row["action"] == "settings_save" for row in server.db.audit_recent(server.CONN, 20))


def test_notification_save_persists_before_inplace_activation(cloud, monkeypatch):
    server, client = cloud
    login(client)
    first = write(client, "POST", "/api/settings", json={"alerts": {
        "enabled": True, "telegram": {"bot_token": "existing-token", "chat_id": "chat"}}})
    assert first.status_code == 200
    observer = notify.Notifier(server.CFG, server.CONN)
    live = server.CFG["alerts"]
    telegram = live["telegram"]
    before = copy.deepcopy(live)
    save = server.appconfig.update_settings
    def checked(patch):
        assert live == before, "runtime was changed before persistence completed"
        return save(patch)
    monkeypatch.setattr(server.appconfig, "update_settings", checked)
    response = write(client, "POST", "/api/settings", json={"alerts": {
        "enabled": False, "telegram": {"chat_id": "next-chat", "bot_token": ""}}})
    assert response.status_code == 200
    assert live is server.CFG["alerts"] is observer.cfg
    assert telegram is live["telegram"]
    assert not live["enabled"] and telegram["chat_id"] == "next-chat"
    assert telegram["bot_token"] == "existing-token"
    assert server.appconfig.load_settings()["alerts"] == live


@pytest.mark.parametrize("initial_alerts", ["missing", "null", "empty"])
def test_notifier_created_with_empty_alerts_observes_first_saved_settings(cloud, initial_alerts):
    server, client = cloud
    if initial_alerts == "missing":
        server.CFG.pop("alerts", None)
    else:
        server.CFG["alerts"] = None if initial_alerts == "null" else {}
    server.COLLECTOR.notifier = notify.Notifier(server.CFG, server.CONN)
    login(client)
    response = write(client, "POST", "/api/settings", json={"alerts": {"enabled": False}})
    assert response.status_code == 200
    assert server.COLLECTOR.notifier.cfg is server.CFG["alerts"]
    assert server.COLLECTOR.notifier.cfg["enabled"] is False
