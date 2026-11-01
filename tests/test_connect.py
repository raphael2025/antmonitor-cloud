"""场地接入：网页查看/更换上报 token、接入码、ping、无域名自签证书指纹。"""
import subprocess
import sys
import copy
import importlib
import shutil
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auth
import connect
from test_site_management import cloud as cloud_base, login, report, write  # noqa: F401

PW = {"password": "admin-test-password"}
NEW_SITE = {"action": "new_site", "site_name": "新场", "site_type": "hydro"}


@pytest.fixture
def cloud(cloud_base):
    from fastapi.testclient import TestClient
    server, _client = cloud_base
    # Real operators open the public HTTPS page; no separate public_url save is
    # needed before generating connection information from that address.
    client = TestClient(server.app, base_url="https://cloud.example.com")
    yield server, client
    client.close()


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
    assert d["public_url"] == "https://cloud.example.com" and d["url_source"] == "guess"
    assert d["address_ready"] and not d["address_error"]


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


def test_existing_shared_site_copies_full_packet_without_rotating_credentials(cloud):
    server, client = cloud
    sid = "云海 二期"
    assert report(client, sid).status_code == 200
    login(client)
    assert write(client, "PATCH", f"/api/site/{sid}", json={"name": "管理界面新名"}).status_code == 200
    server.appconfig.update_settings({"alerts": {"retained": True}})
    before = copy.deepcopy(server.CFG["ingest"])
    path = Path(server.appconfig.SETTINGS_FILE)
    disk = path.read_bytes()
    expected = {"u": "https://cloud.example.com", "t": "reporting-test-token",
                "s": sid, "n": "管理界面新名", "y": "hydro"}
    row = next(x for x in client.get("/api/connect").json()["sites"] if x["id"] == sid)
    assert row["can_reveal"] and not row["own_token"] and not row["revoked"]
    for _ in range(2):
        response = write(client, "POST", "/api/connect/reveal", json={"target": sid, **PW})
        assert response.status_code == 200
        assert connect.parse_code(response.json()["code"]) == expected
        assert response.json()["token"] == expected["t"]
        assert server.CFG["ingest"] == before
        assert path.read_bytes() == disk
    assert report(client, sid).status_code == 200, "copying must leave the installed reporter working"
    audits = server.db.audit_recent(server.CONN, 20)
    assert sum(a["action"] == "token_reveal" for a in audits) == 2
    assert "reporting-test-token" not in str(audits)


@pytest.mark.parametrize("own_token", ["dedicated-test-token", "", None, "CHANGE_ME_site_token"])
def test_site_copy_uses_the_same_dedicated_or_revoked_credential_as_ingest(cloud, own_token):
    server, client = cloud
    sid = "st-own"
    assert report(client, sid).status_code == 200
    server.CFG["ingest"].setdefault("tokens", {})[sid] = own_token
    before = copy.deepcopy(server.CFG["ingest"])
    login(client)
    row = next(x for x in client.get("/api/connect").json()["sites"] if x["id"] == sid)
    response = write(client, "POST", "/api/connect/reveal", json={"target": sid, **PW})
    assert report(client, sid).status_code == 401, "a per-site entry must never fall back to shared"
    if own_token == "dedicated-test-token":
        assert row["can_reveal"] and row["own_token"] and not row["revoked"]
        assert response.status_code == 200
        packet = connect.parse_code(response.json()["code"])
        assert packet["t"] == own_token and packet["s"] == sid
        assert client.post("/api/ingest/ping", headers={"Authorization": f"Bearer {packet['t']}"},
                           json={"site_id": sid}).status_code == 200
    else:
        assert not row["can_reveal"] and row["revoked"]
        assert response.status_code == 404
        assert "reporting-test-token" not in response.text
    assert server.CFG["ingest"] == before
    assert not Path(server.appconfig.SETTINGS_FILE).exists()


def test_shared_site_copy_keeps_admin_password_and_csrf_checks(cloud):
    server, client = cloud
    assert report(client, "st-shared").status_code == 200
    before = copy.deepcopy(server.CFG["ingest"])
    body = {"target": "st-shared", **PW}
    assert write(client, "POST", "/api/connect/reveal", json=body).status_code == 401
    login(client, "viewer")
    assert write(client, "POST", "/api/connect/reveal", json=body).status_code == 403
    login(client)
    assert client.post("/api/connect/reveal", json=body).status_code == 403
    assert write(client, "POST", "/api/connect/reveal", json={**body, "password": "wrong"}).status_code == 403
    assert write(client, "POST", "/api/connect/reveal", json=body).status_code == 200
    assert server.CFG["ingest"] == before
    assert not Path(server.appconfig.SETTINGS_FILE).exists()


def test_shared_site_copy_does_not_create_new_or_pull_bindings(cloud):
    server, client = cloud
    server.CFG["sites"] = [{"id": "st-pull", "name": "拉取场地", "url": "http://127.0.0.1:8800"}]
    login(client)
    row = next(x for x in client.get("/api/connect").json()["sites"] if x["id"] == "st-pull")
    assert row["mode"] == "pull" and not row["can_reveal"]
    for sid in ("st-pull", "st-unknown"):
        response = write(client, "POST", "/api/connect/reveal", json={"target": sid, **PW})
        assert response.status_code == 404
        assert "reporting-test-token" not in response.text
    assert not Path(server.appconfig.SETTINGS_FILE).exists()


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
    r = write(client, "POST", "/api/connect/token", json={**NEW_SITE, **PW}).json()
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


def test_https_page_generates_one_complete_packet_without_saving_address_first(cloud):
    server, client = cloud
    login(client)
    before = client.get("/api/connect").json()
    assert before["url_source"] == "guess" and before["address_ready"]
    response = write(client, "POST", "/api/connect/token",
                     json={**NEW_SITE, "site_name": "北场 / 一期", **PW})
    assert response.status_code == 200
    d = response.json()
    packet = connect.parse_code(d["code"])
    assert packet == {"u": "https://cloud.example.com", "t": d["token"],
                      "s": d["target"], "n": "北场 / 一期", "y": "hydro"}
    assert d["public_url"] == packet["u"]
    assert "connect" not in server.appconfig.load_settings(), "no extra address configuration step"
    for _ in range(2):
        copied = write(client, "POST", "/api/connect/reveal",
                       json={"target": d["target"], **PW}).json()
        assert copied["token"] == d["token"] and copied["code"] == d["code"]
        assert copied["public_url"] == packet["u"]
    assert client.post("/api/ingest/ping", headers={"Authorization": "Bearer " + packet["t"]},
                       json={"site_id": packet["s"]}).status_code == 200


@pytest.mark.parametrize("url", ["http://cloud.example.com", "http://8.8.8.8:8900", "HTTP://cloud.example.com"])
def test_public_http_cannot_issue_new_or_rotated_credentials(cloud, url):
    server, client = cloud
    login(client)
    created = write(client, "POST", "/api/connect/token", json={**NEW_SITE, **PW}).json()
    server.CFG.setdefault("server", {})["public_url"] = url
    before = copy.deepcopy(server.CFG["ingest"])
    disk = Path(server.appconfig.SETTINGS_FILE).read_bytes()
    state = client.get("/api/connect").json()
    assert not state["address_ready"] and "HTTPS" in state["address_error"]
    for body in ({**NEW_SITE, **PW}, {"action": "rotate", "target": created["target"], **PW}):
        denied = write(client, "POST", "/api/connect/token", json=body)
        assert denied.status_code == 400 and "HTTPS" in denied.json()["detail"]
        assert server.CFG["ingest"] == before
        assert Path(server.appconfig.SETTINGS_FILE).read_bytes() == disk


def test_inferred_loopback_needs_a_site_reachable_address(cloud):
    from fastapi.testclient import TestClient
    server, _client = cloud
    local = TestClient(server.app, base_url="http://127.0.0.1:8900")
    login(local)
    state = local.get("/api/connect").json()
    assert not state["address_ready"] and "SSH" in state["address_error"]
    assert write(local, "POST", "/api/connect/token", json={**NEW_SITE, **PW}).status_code == 400
    assert not Path(server.appconfig.SETTINGS_FILE).exists()
    # An explicit private URL remains usable for installations on the same network.
    server.CFG.setdefault("server", {})["public_url"] = "http://192.168.1.10:8900"
    assert local.get("/api/connect").json()["address_ready"]
    made = write(local, "POST", "/api/connect/token", json={**NEW_SITE, **PW})
    assert made.status_code == 200
    assert connect.parse_code(made.json()["code"])["u"] == "http://192.168.1.10:8900"
    local.close()


def test_forwarded_headers_only_help_when_the_proxy_is_trusted(cloud, monkeypatch):
    import ipaddress
    from starlette.requests import Request
    server, _client = cloud
    monkeypatch.setattr(server, "_TRUST_PROXY", True)
    monkeypatch.setattr(server, "_TRUSTED_NETS", [ipaddress.ip_network("127.0.0.0/8")])

    def request(client_ip):
        return Request({"type": "http", "method": "POST", "scheme": "http", "path": "/api/connect/token",
                        "server": ("cloud.example.com", 80), "client": (client_ip, 1234),
                        "query_string": b"", "headers": [(b"host", b"cloud.example.com"),
                            (b"x-forwarded-proto", b"https"), (b"x-forwarded-host", b"attacker.invalid")]})

    trusted = request("127.0.0.1")
    packet = connect.parse_code(server._code_for(trusted, "test-token", "st-proxy",
                                               {"name": "代理场地", "type": "air"}))
    assert packet["u"] == "https://cloud.example.com"
    with pytest.raises(server.HTTPException) as exc:
        server._code_for(request("8.8.8.8"), "test-token")
    assert exc.value.status_code == 400 and "HTTPS" in exc.value.detail
    server.CFG.setdefault("server", {})["public_url"] = "https://configured.example.com/prefix"
    packet = connect.parse_code(server._code_for(request("8.8.8.8"), "test-token"))
    assert packet["u"] == "https://configured.example.com/prefix"


@pytest.mark.parametrize("url", ["https://cloud.example.com/\nextra", "https://cloud .example.com", "https://cloud.example.com/\x00x"])
def test_control_characters_and_spaces_do_not_become_connection_addresses(url):
    assert connect.normalize_url(url) is None


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
    from concurrent.futures import ThreadPoolExecutor
    import threading
    server, client = cloud
    login(client)
    count = 12
    gate = threading.Barrier(count)

    def one(index):
        gate.wait(timeout=10)
        response = write(client, "POST", "/api/connect/token",
                         json={**NEW_SITE, "site_name": f"并发场地 {index}", **PW})
        assert response.status_code == 200, response.text
        return response.json()

    # Future.result 把工作线程异常带回主线程，不能只警告后拿不完整 out 判通过。
    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(one, i) for i in range(count)]
        out = [future.result(timeout=20) for future in futures]
    assert len(out) == count
    assert len({d["target"] for d in out}) == count
    toks = server.CFG["ingest"]["tokens"]
    saved = server.appconfig.load_settings()["ingest"]
    disk = saved["tokens"]
    assert len(toks) == len(disk) == len(saved["site_meta"]) == count
    for d in out:
        assert toks[d["target"]] == d["token"] == disk[d["target"]]
        meta = saved["site_meta"][d["target"]]
        assert meta["name"] == d["site_name"] == connect.parse_code(d["code"])["n"]


def test_site_count_stays_correct_during_concurrent_reads_and_audits(cloud):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    server, _client = cloud
    server.db.upsert_site(server.CONN, "st-count", "计数测试", "air", 1)
    readers, iterations = 8, 300
    gate = threading.Barrier(readers + 1)

    def read_counts():
        gate.wait(timeout=10)
        for _ in range(iterations):
            assert server.db.site_count(server.CONN) == 1

    def write_audits():
        gate.wait(timeout=10)
        for index in range(iterations):
            server.db.audit_add(server.CONN, "test-admin", "local", "count_test", str(index))

    with ThreadPoolExecutor(max_workers=readers + 1) as pool:
        futures = [pool.submit(read_counts) for _ in range(readers)] + [pool.submit(write_audits)]
        for future in futures:
            future.result(timeout=20)
    assert server.db.site_count(server.CONN) == 1


def test_revoked_site_cannot_fall_back_to_shared_token(cloud):
    server, client = cloud
    login(client)
    d = write(client, "POST", "/api/connect/token", json={**NEW_SITE, **PW}).json()
    sid = d["target"]
    assert client.post("/api/ingest/summary", headers={"Authorization": f"Bearer {d['token']}"},
                       json={"site_id": sid, "site": "S"}).status_code == 200
    write(client, "POST", "/api/connect/token", json={"target": sid, "action": "revoke", **PW})
    assert report(client, sid).status_code == 401           # 共享 token 也冒充不了它
    row = next(r for r in client.get("/api/connect").json()["sites"] if r["id"] == sid)
    assert row["revoked"] and not row["own_token"] and not row["can_reveal"]
    assert write(client, "POST", "/api/connect/reveal", json={"target": sid, **PW}).status_code == 404


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


def test_connect_code_old_and_named_formats_are_compatible():
    old = connect.make_code("https://cloud.example.com", "test-token", site_id="st-old")
    assert connect.parse_code(old) == {"u": "https://cloud.example.com", "t": "test-token", "s": "st-old"}
    named = connect.make_code("https://cloud.example.com", "test-token", "AA:BB", "st-new", "北场 水冷", "hydro")
    assert connect.parse_code(named) == {"u": "https://cloud.example.com", "t": "test-token", "f": "AA:BB",
                                         "s": "st-new", "n": "北场 水冷", "y": "hydro"}


def test_named_pending_site_survives_restart_reveal_and_rotation(cloud):
    from fastapi.testclient import TestClient
    server, client = cloud
    login(client)
    r = write(client, "POST", "/api/connect/token",
              json={**NEW_SITE, "site_name": "  华北 / 一场  ", **PW})
    assert r.status_code == 200
    created = r.json()
    sid = created["target"]
    meta = {"name": "华北 / 一场", "type": "hydro"}
    saved = server.appconfig.load_settings()["ingest"]
    assert saved["tokens"][sid] == created["token"]
    assert saved["site_meta"][sid] == meta
    row = next(x for x in client.get("/api/connect").json()["sites"] if x["id"] == sid)
    assert row["name"] == meta["name"] and row["type"] == meta["type"]
    assert not row["registered"] and not server.db.site_exists(server.CONN, sid)

    # 真正重新导入服务：从磁盘重新叠加 token 与元数据，不依赖旧 CFG。
    server.CONN.close()
    server = importlib.reload(server)
    client = TestClient(server.app, base_url="https://cloud.example.com")
    login(client)
    for action in ("reveal", "rotate"):
        endpoint = "/api/connect/reveal" if action == "reveal" else "/api/connect/token"
        response = write(client, "POST", endpoint, json={"target": sid, "action": action, **PW})
        assert response.status_code == 200
        result = response.json()
        decoded = connect.parse_code(result["code"])
        assert (decoded["s"], decoded["n"], decoded["y"]) == (sid, meta["name"], meta["type"])
        assert result["site_name"] == meta["name"] and result["site_type"] == meta["type"]
        if action == "rotate":
            assert result["token"] != created["token"]
            stored = server.appconfig.load_settings()["ingest"]
            assert stored["tokens"][sid] == result["token"]
            assert stored["site_meta"][sid] == meta
    assert client.post("/api/ingest/ping", headers={"Authorization": f"Bearer {created['token']}"},
                       json={"site_id": sid}).status_code == 401


def test_registered_site_reissued_code_uses_current_display_name_and_type(cloud):
    server, client = cloud
    login(client)
    created = write(client, "POST", "/api/connect/token",
                    json={**NEW_SITE, "site_name": "最初名称", **PW}).json()
    sid = created["target"]
    assert client.post("/api/ingest/summary",
                       headers={"Authorization": f"Bearer {created['token']}"},
                       json={"site_id": sid, "site": "现场改名", "type": "air"}).status_code == 200
    # 当前显示名还可能由云端管理员覆盖，重发接入码应与列表一致。
    assert write(client, "PATCH", f"/api/site/{sid}", json={"name": "管理界面新名"}).status_code == 200
    row = next(x for x in client.get("/api/connect").json()["sites"] if x["id"] == sid)
    assert (row["name"], row["type"]) == ("管理界面新名", "air")
    for action in ("reveal", "rotate"):
        path = "/api/connect/reveal" if action == "reveal" else "/api/connect/token"
        response = write(client, "POST", path, json={"target": sid, "action": action, **PW})
        assert response.status_code == 200
        result = response.json()
        decoded = connect.parse_code(result["code"])
        assert (decoded["s"], decoded["n"], decoded["y"]) == (sid, row["name"], row["type"])
        assert (result["site_name"], result["site_type"]) == (row["name"], row["type"])
    assert server.appconfig.load_settings()["ingest"]["site_meta"][sid] == {
        "name": "管理界面新名", "type": "air"}


@pytest.mark.parametrize("patch", [
    {"site_name": ""}, {"site_name": " "}, {"site_name": "名" * 65},
    {"site_name": "场地\n一"}, {"site_name": "\x00场地"}, {"site_name": "场地\x7f"},
    {"site_name": None}, {"site_name": 123}, {"site_name": ["场地"]},
    {"site_type": "water"}, {"site_type": ""}, {"site_type": None}, {"site_type": ["air"]},
])
def test_invalid_new_site_description_creates_nothing(cloud, patch):
    server, client = cloud
    login(client)
    before = copy.deepcopy(server.CFG["ingest"])
    r = write(client, "POST", "/api/connect/token", json={**NEW_SITE, **patch, **PW})
    assert r.status_code == 400
    assert server.CFG["ingest"] == before
    assert not Path(server.appconfig.SETTINGS_FILE).exists()


def test_new_site_requires_name_and_type_but_old_pending_codes_still_work(cloud):
    server, client = cloud
    login(client)
    for body in ({"action": "new_site"}, {"action": "new_site", "site_name": "有名"}):
        assert write(client, "POST", "/api/connect/token", json={**body, **PW}).status_code == 400
    # 模拟升级前已存在、没有名称元数据的专属凭据。
    server.appconfig.update_settings({"ingest": {"tokens": {"st-legacy": "legacy-dedicated-token"}}})
    server.appconfig.apply_settings(server.CFG, server.appconfig.load_settings())
    for action, path in (("reveal", "/api/connect/reveal"), ("rotate", "/api/connect/token")):
        r = write(client, "POST", path, json={"target": "st-legacy", "action": action, **PW})
        assert r.status_code == 200
        code = connect.parse_code(r.json()["code"])
        assert code["s"] == "st-legacy" and "n" not in code and "y" not in code


@pytest.mark.parametrize("action", ["new_site", "rotate"])
def test_failed_atomic_write_keeps_both_token_and_description(cloud, monkeypatch, action):
    server, client = cloud
    login(client)
    old = write(client, "POST", "/api/connect/token", json={**NEW_SITE, **PW}).json()
    path = Path(server.appconfig.SETTINGS_FILE)
    disk, runtime = path.read_bytes(), copy.deepcopy(server.CFG["ingest"])

    def fail_write(*args):
        raise OSError("test disk full")

    monkeypatch.setattr(server.appconfig, "_atomic_json", fail_write)
    r = write(client, "POST", "/api/connect/token", json={**NEW_SITE, "action": action,
                                                           "target": old["target"], **PW})
    assert r.status_code == 500
    assert path.read_bytes() == disk and server.CFG["ingest"] == runtime
    assert client.post("/api/ingest/ping", headers={"Authorization": f"Bearer {old['token']}"},
                       json={"site_id": old["target"]}).status_code == 200


def test_code_generation_failure_does_not_issue_credentials(cloud, monkeypatch):
    server, client = cloud
    login(client)
    before = copy.deepcopy(server.CFG["ingest"])

    def fail_code(*args):
        raise ValueError("test encoder failure")

    monkeypatch.setattr(connect, "make_code", fail_code)
    with pytest.raises(ValueError, match="test encoder failure"):
        write(client, "POST", "/api/connect/token", json={**NEW_SITE, **PW})
    assert server.CFG["ingest"] == before
    assert not Path(server.appconfig.SETTINGS_FILE).exists()


def test_new_site_permissions_password_and_csrf_remain_required(cloud):
    server, client = cloud
    body = {**NEW_SITE, **PW}
    assert write(client, "POST", "/api/connect/token", json=body).status_code == 401
    login(client, "viewer")
    assert write(client, "POST", "/api/connect/token", json=body).status_code == 403
    login(client)
    assert client.post("/api/connect/token", json=body).status_code == 403  # 缺 CSRF 头
    assert write(client, "POST", "/api/connect/token", json={**body, "password": "wrong"}).status_code == 403
    assert not Path(server.appconfig.SETTINGS_FILE).exists()


def test_onboarding_ui_workflow():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for the browser-handler regression test")
    subprocess.run([node, str(Path(__file__).with_name("test_connect_ui.js"))], check=True,
                   capture_output=True, text=True, timeout=20)
