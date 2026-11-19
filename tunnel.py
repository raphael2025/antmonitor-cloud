# -*- coding: utf-8 -*-
"""场地面板反向转发：让云端管理员能点开 NAT 后面的场地本地面板。

方向仍然是"场地主动连云端"：场地端开一条长轮询 /api/ingest/tunnel/poll 等活，
云端管理员打开 /p/<site_id>/ 时，请求被放进该场地的队列，场地端取走后在本机
127.0.0.1 上执行，再把响应回传 /api/ingest/tunnel/respond。场地不需要开放任何入站端口。

纯内存、单进程：重启后排队中的请求丢失(浏览器刷新即可)，不落盘任何场地面板内容。
"""
import asyncio
import base64
import re
import secrets
import time

POLL_SECONDS = 20          # 长轮询最长挂多久
ONLINE_WINDOW = 45         # 距上次 poll 多久内算"转发在线"
REQUEST_TIMEOUT = 30       # 管理员一次请求最多等多久
MAX_QUEUE = 20             # 每场地最多排队请求数，防把场地端淹没
MAX_REQ_BODY = 2 * 1024 * 1024
MAX_RESP_BODY = 8 * 1024 * 1024

# 不原样转发给场地端的请求头：云端自己的会话/鉴权，以及逐跳头
_DROP_REQ = {"host", "cookie", "authorization", "content-length", "connection", "keep-alive",
             "transfer-encoding", "upgrade", "te", "trailer", "proxy-authorization",
             "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-real-ip", "forwarded",
             "origin", "referer", "x-co-csrf", "accept-encoding"}
# 不原样回给浏览器的响应头
_DROP_RESP = {"content-length", "connection", "keep-alive", "transfer-encoding", "content-encoding",
              "upgrade", "te", "trailer", "content-security-policy", "strict-transport-security",
              "x-frame-options", "set-cookie", "content-security-policy-report-only"}

def valid_site(sid):
    sid = str(sid or "")
    return 1 <= len(sid) <= 64 and not any(c in sid for c in '/?#\\') and sid.isprintable()


def forward_cookies(cookie_header, drop_names):
    """只转发场地面板自己的 Cookie，云端会话 Cookie(co_token)绝不能发到场地端。"""
    keep = []
    for part in (cookie_header or "").split(";"):
        name = part.split("=", 1)[0].strip()
        if name and name not in drop_names:
            keep.append(part.strip())
    return "; ".join(keep)


def clean_request_headers(headers):
    return {k.lower(): v for k, v in headers.items() if k.lower() not in _DROP_REQ}


def clean_response_headers(pairs):
    out = []
    for k, v in pairs:
        if str(k).lower() in _DROP_RESP:
            continue
        out.append([str(k)[:100], str(v)[:2000]])
    return out


def rewrite_cookie(value, prefix):
    """场地面板的 Set-Cookie：Path 限定到 /p/<id>/，去掉 Domain，各场地之间互不串号。"""
    parts = [p.strip() for p in str(value).split(";") if p.strip()]
    if not parts or "=" not in parts[0]:
        return None
    kept = [parts[0]]
    for p in parts[1:]:
        low = p.lower()
        if low.startswith("path=") or low.startswith("domain="):
            continue
        kept.append(p)
    kept.append(f"Path={prefix}")
    return "; ".join(kept)


_ATTR = re.compile(r"""(\b(?:src|href|action)\s*=\s*)(["'])/(?!/)""", re.IGNORECASE)
_HEAD = re.compile(r"<head[^>]*>", re.IGNORECASE)


def rewrite_html(text, prefix):
    """HTML：绝对路径资源加上 /p/<id> 前缀，并在 <head> 最前面挂一个外链 shim 脚本
    (CSP 不允许内联脚本)，由它接管 fetch/XHR/WebSocket 的绝对路径。"""
    text = _ATTR.sub(lambda m: f"{m.group(1)}{m.group(2)}{prefix}", text)
    tag = f'<script src="{prefix}__co_shim.js"></script>'
    if _HEAD.search(text):
        return _HEAD.sub(lambda m: m.group(0) + tag, text, count=1)
    return tag + text


def shim_js(prefix):
    """浏览器里跑的小脚本：把页面发出的 "/xxx" 绝对路径请求改写到 /p/<id>/xxx。
    WebSocket 在转发模式下不可用——换成永不连接的占位，前端靠定时轮询刷新。"""
    p = prefix.rstrip("/")
    return f"""(function(){{
var P={p!r};
function fix(u){{
  if(typeof u!=="string")return u;
  if(u.charAt(0)==="/"&&u.charAt(1)!=="/"&&u.indexOf(P+"/")!==0)return P+u;
  try{{var a=new URL(u,location.href);
    if(a.origin===location.origin&&a.pathname.indexOf(P+"/")!==0&&a.pathname.charAt(0)==="/")
      return P+a.pathname+a.search+a.hash;}}catch(e){{}}
  return u;
}}
var f=window.fetch;
if(f)window.fetch=function(i,o){{
  if(typeof i==="string")i=fix(i);
  else if(i&&i.url)i=new Request(fix(i.url),i);
  return f.call(this,i,o);
}};
var xo=XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open=function(m,u){{
  arguments[1]=fix(String(u));return xo.apply(this,arguments);
}};
var wo=window.open;
window.open=function(u,a,b){{return wo.call(window,u?fix(String(u)):u,a,b);}};
function FakeWS(){{this.readyState=0;this.onopen=this.onclose=this.onerror=this.onmessage=null;}}
FakeWS.prototype.close=function(){{}};FakeWS.prototype.send=function(){{}};
FakeWS.prototype.addEventListener=function(){{}};FakeWS.prototype.removeEventListener=function(){{}};
FakeWS.CONNECTING=0;FakeWS.OPEN=1;FakeWS.CLOSING=2;FakeWS.CLOSED=3;
window.WebSocket=FakeWS;
}})();
"""


class Broker:
    """每场地一个队列 + 每个在途请求一个 Future。只在事件循环线程里用。"""

    def __init__(self):
        self.queues = {}
        self.pending = {}      # job_id -> (site_id, Future)
        self.last_poll = {}    # site_id -> ts

    def online(self, sid, now=None):
        return ((now or time.time()) - self.last_poll.get(sid, 0)) < ONLINE_WINDOW

    def online_ids(self, now=None):
        now = now or time.time()
        return {s for s, t in self.last_poll.items() if now - t < ONLINE_WINDOW}

    def _q(self, sid):
        q = self.queues.get(sid)
        if q is None:
            q = self.queues[sid] = asyncio.Queue()
        return q

    async def next_job(self, sid, timeout=None):
        timeout = POLL_SECONDS if timeout is None else timeout
        self.last_poll[sid] = time.time()
        try:
            job = await asyncio.wait_for(self._q(sid).get(), timeout)
        except asyncio.TimeoutError:
            self.last_poll[sid] = time.time()
            return None
        self.last_poll[sid] = time.time()
        return job

    async def submit(self, sid, method, path, headers, body, timeout=REQUEST_TIMEOUT):
        q = self._q(sid)
        if q.qsize() >= MAX_QUEUE:
            raise OverflowError("队列已满")
        jid = secrets.token_hex(8)
        fut = asyncio.get_running_loop().create_future()
        self.pending[jid] = (sid, fut)
        q.put_nowait({"id": jid, "method": method, "path": path, "headers": headers,
                      "body_b64": base64.b64encode(body).decode("ascii") if body else ""})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(jid, None)

    def respond(self, sid, jid, resp):
        ent = self.pending.get(jid)
        if not ent or ent[0] != sid:      # 只能回自己场地的请求，不能替别的场地答
            return False
        if not ent[1].done():
            ent[1].set_result(resp)
        return True
