"""场地接入：网页查看/更换上报 token、接入码、ping、无域名自签证书指纹。"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auth
import connect
from test_site_management import cloud, login, report, write  # noqa: F401  (fixture 复用)

PW = {"password": "admin-test-password"}


@pytest.fixture(autouse=True)
def _clear_fails():
    auth._fails.clear()
    yield
    auth._fails.clear()


def test_connect_requires_admin_and_never_lists_plain_tokens(cloud):
    server, client = cloud
    assert client.get("/api/connect").status_code == 401
    login(client, "viewer")
    assert client.get("/api/connect").status_code == 403
    login(client)
    d = client.get("/api/connect", headers={"Host": "cloud.example.com"}).json()
    assert d["shared"]["set"] and d["shared"]["hint"] == "oken"
    assert "reporting-test-token" not in str(d)
    assert d["public_url"] == "http://cloud.example.com" and d["url_source"] == "guess"


def test_reveal_needs_password_and_is_audited(cloud):
    server, client = cloud
    login(client)
    r = write(client, "POST", "/api/connect/reveal", json={"target": "shared", "password": "wrong"})
    assert r.status_code == 403
    r = write(client, "POST", "/api/connect/reveal", json={"target": "shared", **PW})
    assert r.status_code == 200
    d = r.json()
    assert d["token"] == "reporting-test-token"
    assert connect.parse_code(d["code"])["t"] == "reporting-test-token"
    actions = [a["action"] for a in server.db.audit_recent(server.CONN, 20)]
    assert "token_reveal" in actions and "connect_pw_fail" in actions


def test_reveal_password_failures_lock_out(cloud):
    _server, client = cloud
    login(client)
    for _ in range(8):
        write(client, "POST", "/api/connect/reveal", json={"target": "shared", "password": "x"})
    r = write(client, "POST", "/api/connect/reveal", json={"target": "shared", **PW})
    assert r.status_code == 429


def test_rotate_shared_token_takes_effect_immediately(cloud):
    server, client = cloud
    login(client)
    assert report(client, "st-a").status_code == 200
    r = write(client, "POST", "/api/connect/token", json={"target": "shared", "action": "rotate", **PW})
    new = r.json()["token"]
    assert new and new != "reporting-test-token"
    assert report(client, "st-a").status_code == 401          # 旧 token 立即失效
    ok = client.post("/api/ingest/summary", headers={"Authorization": f"Bearer {new}"},
                     json={"site_id": "st-a", "site": "A"})
    assert ok.status_code == 200
    # 写进了覆盖层，重启后仍生效
    assert server.appconfig.load_settings()["ingest"]["token"] == new


def test_new_site_code_reports_even_with_auto_register_off(cloud):
    server, client = cloud
    login(client)
    write(client, "POST", "/api/connect/settings", json={"auto_register": False, **PW})
    r = write(client, "POST", "/api/connect/token", json={"action": "new_site", **PW}).json()
    sid, tok = r["target"], r["token"]
    assert connect.parse_code(r["code"])["s"] == sid
    # 共享 token 冒充新 ID：拒绝(自动注册已关)
    assert report(client, "st-other").status_code == 403
    hdr = {"Authorization": f"Bearer {tok}"}
    ping = client.post("/api/ingest/ping", headers=hdr, json={"site_id": sid, "site": "新场"})
    assert ping.status_code == 200 and ping.json()["registered"] is False
    assert client.post("/api/ingest/summary", headers=hdr,
                       json={"site_id": sid, "site": "新场"}).status_code == 200
    assert client.post("/api/ingest/ping", headers=hdr,
                       json={"site_id": sid, "site": "新场"}).json()["registered"] is True
    # 撤销后不能再报
    write(client, "POST", "/api/connect/token", json={"target": sid, "action": "revoke", **PW})
    assert client.post("/api/ingest/summary", headers=hdr,
                       json={"site_id": sid, "site": "新场"}).status_code == 401


def test_ping_writes_nothing(cloud):
    server, client = cloud
    r = client.post("/api/ingest/ping", headers={"Authorization": "Bearer reporting-test-token"},
                    json={"site_id": "st-ping", "site": "P"})
    assert r.status_code == 200 and r.json()["registered"] is False
    assert not server.db.site_exists(server.CONN, "st-ping")
    bad = client.post("/api/ingest/ping", headers={"Authorization": "Bearer nope"},
                      json={"site_id": "st-ping", "site": "P"})
    assert bad.status_code == 401


def test_public_url_validation(cloud):
    _server, client = cloud
    login(client)
    bad = write(client, "POST", "/api/connect/settings", json={"public_url": "ftp://x", **PW})
    assert bad.status_code == 400
    d = write(client, "POST", "/api/connect/settings",
              json={"public_url": "https://1.2.3.4:8900/", **PW}).json()
    assert d["public_url"] == "https://1.2.3.4:8900" and d["url_source"] == "config"
    assert d["mode"] == "ip"


def test_self_signed_fingerprint_goes_into_code(tmp_path):
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                    "-out", str(crt), "-days", "1", "-subj", "/CN=t"], check=True,
                   capture_output=True)
    want = subprocess.run(["openssl", "x509", "-in", str(crt), "-noout", "-fingerprint", "-sha256"],
                          check=True, capture_output=True, text=True).stdout.split("=", 1)[1].strip()
    cfg = {"server": {"tls_cert": str(crt), "tls_key": str(key), "tls_self_signed": True}}
    info = connect.server_info(cfg)
    assert info["fingerprint"] == want
    code = connect.make_code("https://1.2.3.4:8900", "tok", info["fingerprint"])
    assert connect.parse_code(code)["f"] == want
    assert connect.mode_of("https://1.2.3.4:8900", info) == "ip"
    assert connect.mode_of("https://a.example.com", connect.server_info({})) == "domain"


def test_concurrent_new_sites_keep_every_token(cloud):
    import threading
    server, client = cloud
    login(client)
    out = []

    def one():
        out.append(write(client, "POST", "/api/connect/token", json={"action": "new_site", **PW}).json())

    ts = [threading.Thread(target=one) for _ in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    toks = server.CFG["ingest"]["tokens"]
    disk = server.appconfig.load_settings()["ingest"]["tokens"]
    for d in out:
        assert toks[d["target"]] == d["token"] == disk[d["target"]]


def test_revoked_site_cannot_fall_back_to_shared_token(cloud):
    server, client = cloud
    login(client)
    d = write(client, "POST", "/api/connect/token", json={"action": "new_site", **PW}).json()
    sid = d["target"]
    assert client.post("/api/ingest/summary", headers={"Authorization": f"Bearer {d['token']}"},
                       json={"site_id": sid, "site": "S"}).status_code == 200
    write(client, "POST", "/api/connect/token", json={"target": sid, "action": "revoke", **PW})
    assert report(client, sid).status_code == 401           # 共享 token 也冒充不了它
    row = next(r for r in client.get("/api/connect").json()["sites"] if r["id"] == sid)
    assert row["revoked"] and not row["own_token"]


def test_legacy_name_as_site_id_is_manageable(cloud):
    _server, client = cloud
    login(client)
    assert report(client, "云海 二期").status_code == 200
    r = write(client, "POST", "/api/connect/token", json={"target": "云海 二期", "action": "rotate", **PW})
    assert r.status_code == 200 and r.json()["target"] == "云海 二期"


def test_unreadable_settings_file_is_never_overwritten(cloud, tmp_path):
    import os
    server, client = cloud
    login(client)
    p = server.appconfig.SETTINGS_FILE
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"passwords": {"x": "y"}, "broken": ')   # 半截 JSON
    r = write(client, "POST", "/api/connect/token", json={"target": "shared", "action": "rotate", **PW})
    assert r.status_code == 500
    assert open(p, encoding="utf-8").read().endswith('"broken": ')   # 原样保留
    os.remove(p)


def test_cgnat_metadata_address_is_internal():
    import notify
    assert notify._internal_target("100.100.100.200")
