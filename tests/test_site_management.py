import importlib
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auth
import db


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({
        "db": {"path": str(tmp_path / "cloud.db")},
        "auth": {"enabled": True, "users": [
            {"username": "test-admin", "password": "admin-test-password", "role": "admin"},
            {"username": "test-viewer", "password": "viewer-test-password", "role": "viewer"},
        ]},
        "ingest": {"token": "reporting-test-token"},
    }))
    monkeypatch.setenv("CO_CONFIG", str(config))
    import appconfig
    monkeypatch.setattr(appconfig, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    sys.modules.pop("server", None)
    server = importlib.import_module("server")
    client = TestClient(server.app)  # no startup: never start network collectors in tests
    yield server, client
    server.CONN.close()
    sys.modules.pop("server", None)


def login(client, role="admin"):
    response = client.post("/api/login", headers={"X-CO-CSRF": "1"}, json={
        "username": "test-" + role, "password": role + "-test-password"})
    assert response.status_code == 200, response.text


def report(client, sid, name="云海二期", kind="summary"):
    return client.post("/api/ingest/" + kind,
                       headers={"Authorization": "Bearer reporting-test-token"},
                       json={"site_id": sid, "site": name, "type": "hydro",
                             "online": 1341, "total": 1341, "hashrate_ths": 416360,
                             "customers": [{"worker": "customer", "machines": 100}]})


def write(client, method, path, **kwargs):
    return client.request(method, path, headers={"X-CO-CSRF": "1"}, **kwargs)


def test_reinstallation_delete_only_old_id_and_no_resurrection(cloud):
    server, client = cloud
    login(client)
    for sid in ("st-old", "st-new"):
        assert report(client, sid).status_code == 200
        assert report(client, sid, kind="customers").status_code == 200
    overview = client.get("/api/overview").json()
    assert all(s["duplicate_name"] for s in overview["sites"])
    assert client.get("/api/site/云海二期").status_code == 409
    assert write(client, "DELETE", "/api/site/云海二期").status_code == 404
    assert write(client, "DELETE", "/api/site/st-old").status_code == 200
    sites = client.get("/api/overview").json()["sites"]
    assert [s["id"] for s in sites] == ["st-new"]
    assert sites[0]["duplicate_name"] is False
    assert client.get("/api/customers").json()["rows"][0]["site_id"] == "st-new"
    for kind in ("summary", "customers"):
        assert report(client, "st-old", kind=kind).status_code == 410
        assert report(client, "st-new", kind=kind).status_code == 200
    assert not db.site_exists(server.CONN, "st-old")
    assert server.CONN.execute("SELECT COUNT(*) FROM snapshots WHERE site_id='st-old'").fetchone()[0] == 0
    assert client.get("/api/sites/deleted").json()["sites"][0]["id"] == "st-old"


def test_tombstone_survives_restart_and_restore_accepts_new_reports(cloud):
    server, client = cloud
    login(client)
    report(client, "st-old")
    write(client, "DELETE", "/api/site/st-old")
    second = db.connect(server._db_path)
    with pytest.raises(db.SiteDeletedError):
        db.upsert_site(second, "st-old", "renamed", "air", int(time.time()))
    second.close()
    assert write(client, "POST", "/api/site/st-old/restore").status_code == 200
    assert client.get("/api/sites/deleted").json()["sites"] == []
    assert client.get("/api/overview").json()["sites"] == []
    assert report(client, "st-old").status_code == 200
    assert len(client.get("/api/site/st-old").json()["trend"]) == 1


def test_rename_persists_on_reports_and_customer_names_match(cloud):
    server, client = cloud
    login(client)
    report(client, "st-a")
    report(client, "st-b")
    report(client, "st-b", kind="customers")
    assert write(client, "PATCH", "/api/site/st-b", json={"name": "云海三期"}).status_code == 200
    for kind in ("summary", "customers"):
        assert report(client, "st-b", kind=kind).status_code == 200
    site = client.get("/api/site/st-b").json()["site"]
    assert site["name"] == "云海三期"
    assert site["reported_name"] == "云海二期"
    assert site["duplicate_name"] is False
    assert db.customers(server.CONN, "st-b")[0]["site_name"] == "云海三期"
    assert write(client, "PATCH", "/api/site/st-b", json={"name": "云海二期"}).status_code == 409
    assert write(client, "PATCH", "/api/site/st-b", json={"name": ""}).status_code == 409
    assert write(client, "DELETE", "/api/site/st-a").status_code == 200
    assert write(client, "PATCH", "/api/site/st-b", json={"name": ""}).status_code == 200
    assert client.get("/api/site/st-b").json()["site"]["name"] == "云海二期"


@pytest.mark.parametrize("method,path,body", [
    ("DELETE", "/api/site/st-a", None),
    ("PATCH", "/api/site/st-a", {"name": "new"}),
    ("POST", "/api/site/st-a/restore", None),
    ("GET", "/api/sites/deleted", None),
])
def test_management_requires_admin(cloud, method, path, body):
    server, client = cloud
    report(client, "st-a")
    assert write(client, method, path, json=body).status_code == 401
    login(client, "viewer")
    assert write(client, method, path, json=body).status_code == 403
    assert db.site_exists(server.CONN, "st-a")


@pytest.mark.parametrize("method,path", [
    ("DELETE", "/api/site/st-a"), ("PATCH", "/api/site/st-a"),
    ("POST", "/api/site/st-a/restore"),
])
def test_csrf_is_required(cloud, method, path):
    _, client = cloud
    login(client)
    report(client, "st-a")
    assert client.request(method, path, json={"name": "new"}).status_code == 403


@pytest.mark.parametrize("name", [None, 12, "x" * 65, "\n\t"])
def test_invalid_names_rejected(cloud, name):
    _, client = cloud
    login(client)
    report(client, "st-a")
    assert write(client, "PATCH", "/api/site/st-a", json={"name": name}).status_code == 400


def test_configured_pull_site_not_deleted(cloud):
    server, client = cloud
    login(client)
    server.CFG["sites"] = [{"id": "pull-id", "name": "pull", "type": "air"}]
    db.upsert_site(server.CONN, "pull-id", "pull", "air", int(time.time()))
    assert write(client, "DELETE", "/api/site/pull-id").status_code == 400


def test_inflight_report_cannot_recreate_deleted_site(cloud):
    server, client = cloud
    login(client)
    report(client, "st-a")
    authenticated = threading.Event()
    finish = threading.Event()
    original = server._check_ingest_token
    def pause(request, sid):
        original(request, sid)
        authenticated.set()
        assert finish.wait(5)
    server._check_ingest_token = pause
    responses = []
    thread = threading.Thread(target=lambda: responses.append(report(client, "st-a")))
    thread.start()
    try:
        assert authenticated.wait(5)
        assert write(client, "DELETE", "/api/site/st-a").status_code == 200
    finally:
        finish.set()
        thread.join(5)
        server._check_ingest_token = original
    assert not thread.is_alive()
    assert responses[0].status_code == 410
    assert not db.site_exists(server.CONN, "st-a")
