import threading
import base64

from fastapi.testclient import TestClient

from test_site_management import cloud, login, report  # noqa: F401
import tunnel

SITE = "st-tunnel01"
TOK = {"Authorization": "Bearer reporting-test-token"}


def _agent(client, handler, stop):
    """模拟场地端：长轮询取活 → 本机处理 → 回传。"""
    while not stop.is_set():
        r = client.post("/api/ingest/tunnel/poll", headers=TOK, json={"site_id": SITE})
        job = r.json().get("job") if r.status_code == 200 else None
        if not job:
            continue
        status, headers, body = handler(job)
        client.post("/api/ingest/tunnel/respond", headers=TOK, json={
            "site_id": SITE, "id": job["id"], "status": status, "headers": headers,
            "body_b64": base64.b64encode(body).decode()})


def _page(job):
    p = job["path"]
    if p == "/":
        return 200, [["content-type", "text/html; charset=utf-8"],
                     ["set-cookie", "mm_token=abc; Path=/; HttpOnly; Domain=x.com"]], \
            b'<html><head></head><body><script src="/web/app.js"></script></body></html>'
    if p == "/redir":
        return 302, [["location", "/login"]], b""
    return 200, [["content-type", "application/json"]], \
        ('{"echo":"%s","cookie":"%s","origin":"%s","ip":"%s"}' % (
            job["method"], job["headers"].get("cookie", ""), job["headers"].get("origin", ""),
            job["headers"].get("x-co-client-ip", ""))).encode()


def _run(cloud, fn):
    server, _ = cloud
    tunnel.POLL_SECONDS = 0.3
    with TestClient(server.app) as client:      # 共享一个事件循环(队列/Future 绑定在它上面)
        assert report(client, SITE).status_code == 200
        stop = threading.Event()
        t = threading.Thread(target=_agent, args=(client, _page, stop), daemon=True)
        t.start()
        try:
            fn(server, client)
        finally:
            stop.set()
            t.join(5)


def test_requires_admin_login_and_site_online(cloud):
    def body(server, client):
        r = client.get(f"/p/{SITE}/")
        assert r.status_code == 401                      # 没登录
        login(client, "viewer")
        assert client.get(f"/p/{SITE}/").status_code == 403   # viewer 不行
        login(client, "admin")
        assert client.get("/p/nope/").status_code == 404      # 场地不存在
    _run(cloud, body)


def test_offline_site_gets_clear_503(cloud):
    server, client = cloud
    tunnel.POLL_SECONDS = 0.3
    with TestClient(server.app) as c:
        assert report(c, SITE).status_code == 200
        login(c, "admin")
        assert c.get(f"/p/{SITE}/").status_code == 503


def test_proxy_roundtrip_rewrites_html_cookie_and_strips_cloud_session(cloud):
    def body(server, client):
        login(client, "admin")
        import time
        for _ in range(50):                    # 等 agent 首次 poll 上线
            if server.BROKER.online(SITE):
                break
            time.sleep(0.1)
        r = client.get(f"/p/{SITE}/")
        assert r.status_code == 200
        assert f'src="/p/{SITE}/web/app.js"' in r.text
        assert f'/p/{SITE}/__co_shim.js' in r.text
        sc = r.headers["set-cookie"]
        assert f"Path=/p/{SITE}/" in sc and "Domain" not in sc
        assert "script-src 'self'" in r.headers["content-security-policy"]
        # 云端会话 cookie 不能发给场地；场地自己的 cookie 可以
        client.cookies.set("mm_token", "abc", path="/")
        r = client.get(f"/p/{SITE}/api/x")
        data = r.json()
        assert "co_token" not in data["cookie"] and "mm_token=abc" in data["cookie"]
        # 重定向被改写进前缀
        r = client.get(f"/p/{SITE}/redir", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == f"/p/{SITE}/login"
        # 写请求：跨站被拒；同源通过且不把 Origin 带给场地
        r = client.post(f"/p/{SITE}/api/login", json={}, headers={"Origin": "https://evil.com"})
        assert r.status_code == 403
        r = client.post(f"/p/{SITE}/api/login", json={}, headers={"Origin": "http://testserver"})
        assert r.status_code == 200 and r.json()["origin"] == ""
        # shim 脚本
        r = client.get(f"/p/{SITE}/__co_shim.js")
        assert r.status_code == 200 and "FakeWS" in r.text
    _run(cloud, body)


def test_poll_requires_valid_token_and_other_site_cannot_answer(cloud):
    def body(server, client):
        bad = client.post("/api/ingest/tunnel/poll", headers={"Authorization": "Bearer nope"},
                          json={"site_id": SITE})
        assert bad.status_code == 401
        assert client.post("/api/ingest/tunnel/poll", headers=TOK,
                           json={"site_id": "st-unknown"}).status_code == 404
        assert server.BROKER.respond("other", "deadbeef", {}) is False
    _run(cloud, body)


def test_cookie_and_header_helpers():
    assert tunnel.forward_cookies("co_token=1; mm_token=2", {"co_token"}) == "mm_token=2"
    assert "origin" not in tunnel.clean_request_headers({"Origin": "x", "Accept": "y"})
    assert tunnel.rewrite_cookie("a=b; Path=/; Secure; Domain=z", "/p/s/") == "a=b; Secure; Path=/p/s/"
    out = tunnel.rewrite_html('<head><link href="/web/a.css"><a href="//x">', "/p/s/")
    assert 'href="/p/s/web/a.css"' in out and 'href="//x"' in out
