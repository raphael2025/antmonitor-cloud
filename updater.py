#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloud updates: validate a detached candidate, promote it, then confirm startup.

Only clean Git deployments are updated. Candidate dependencies live in a separate
venv, so a failed update cannot damage the running environment. A stable launcher
supervises startup and restores the previous commit/interpreter after repeated
failures. State and venvs live inside Git's private metadata directory.
"""
import contextlib
import errno
import io
import json
import os
import py_compile
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

BASE = os.path.dirname(os.path.abspath(__file__))
MAX_BOOT_ATTEMPTS = 3
HEALTHY_AFTER_SEC = 60
HEALTH_RETRY_SEC = 5
SELFCHECK_TIMEOUT = 60
PIP_TIMEOUT = 600
_apply_lock = threading.Lock()
_health_timer = None
_health_stop = None
_process_identity = uuid.uuid4().hex
_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


def _run(args, cwd=None, timeout=60, env=None):
    result = subprocess.run(args, cwd=cwd or BASE, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, encoding="utf-8", errors="replace",
                            env=env)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "command failed")[-1500:].strip())
    return result.stdout.strip()


def _git(*args, timeout=60):
    env = dict(os.environ, **_GIT_ENV)
    return _run(["git", *args], timeout=timeout, env=env)


def _state_dir():
    path = _git("rev-parse", "--git-path", "antcloud-update", timeout=15)
    return os.path.abspath(os.path.join(BASE, path))


def _load_state():
    path = os.path.join(_state_dir(), "state.json")
    try:
        with open(path, encoding="utf-8") as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return {}
    except (ValueError, OSError) as exc:
        raise RuntimeError("更新状态损坏或不可读，请先检查 state.json: %s" % exc)
    if not isinstance(state, dict):
        raise RuntimeError("更新状态格式错误")
    return state


def _save_state(state):
    directory = _state_dir()
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, os.path.join(directory, "state.json"))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _lock_file():
    """Serialize API/automatic/CLI/launcher writers, including different processes."""
    directory = _state_dir()
    os.makedirs(directory, exist_ok=True)
    stream = open(os.path.join(directory, "lock"), "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if not stream.tell():
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError as exc:
        stream.close()
        if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            return None
        raise


def _unlock_file(stream):
    if stream is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        stream.close()  # POSIX flock is released on close.


@contextlib.contextmanager
def _state_transaction():
    if not _apply_lock.acquire(blocking=False):
        raise RuntimeError("正在更新，请稍候")
    stream = None
    try:
        stream = _lock_file()
        if stream is None:
            raise RuntimeError("其他进程正在更新，请稍候")
        yield
    finally:
        _unlock_file(stream)
        _apply_lock.release()


def _dirty():
    # porcelain includes staged, unstaged and non-ignored untracked files.
    return _git("status", "--porcelain", "--untracked-files=normal")


def _mark_bad(state, sha, reason):
    state["bad"] = ([s for s in state.get("bad", []) if s != sha] + [sha])[-20:]
    state["last_error"] = str(reason)[-1000:]
    _save_state(state)


def is_git_deployment():
    # A parent repository must not turn an archive/Docker installation into a Git deployment.
    # A .git file is also valid (git worktree); a broken existing Git directory fails closed.
    return os.path.exists(os.path.join(BASE, ".git"))


def runtime_python():
    if not is_git_deployment():
        return sys.executable
    interpreter = _load_state().get("active_python") or sys.executable
    if not os.path.isfile(interpreter):
        raise RuntimeError("更新运行环境不存在: %s" % interpreter)
    return interpreter


def check(branch=None, fallback_url=None):
    """Read update availability. A rejected version is visible but not auto-applied."""
    if not is_git_deployment():
        return {"git": False, "error": "本目录不是 git 仓库(未用 git 部署)"}
    try:
        _git("rev-parse", "--git-dir", timeout=15)
    except Exception:
        return {"git": False, "error": "本目录不是 git 仓库(未用 git 部署)"}
    try:
        branch = branch or _git("rev-parse", "--abbrev-ref", "HEAD")
        if branch == "HEAD":
            raise RuntimeError("当前为游离提交，请先切换到部署分支")
        _git("check-ref-format", "--branch", branch)
        # Read-only API checks can overlap each other or an apply. FETCH_HEAD is
        # shared by all fetches, so each check owns an independent temporary ref.
        fetched_ref = "refs/antcloud/check/" + uuid.uuid4().hex
        try:
            try:
                _git("fetch", "--quiet", "origin", branch + ":" + fetched_ref, timeout=120)
            except Exception:
                if not fallback_url:
                    raise
                _git("fetch", "--quiet", fallback_url, branch + ":" + fetched_ref, timeout=120)
            remote = _git("rev-parse", fetched_ref)
        finally:
            _git("update-ref", "-d", fetched_ref)
        local = _git("rev-parse", "HEAD")
        behind = int(_git("rev-list", "--count", local + ".." + remote) or 0)
        state = _load_state()
        changes = _git("log", "--oneline", local + ".." + remote, "-10").splitlines() if behind else []
        return {"git": True, "branch": branch, "local": local[:10], "remote": remote[:10],
                "target": remote, "behind": behind, "changes": changes, "checked_ts": int(time.time()),
                "bad": remote in state.get("bad", []), "pending": bool(state.get("pending"))}
    except Exception as exc:
        return {"git": True, "error": str(exc)[:1000]}


def _extract_candidate(sha, destination):
    data = subprocess.run(["git", "archive", "--format=tar", sha], cwd=BASE,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=60)
    if data.returncode:
        raise RuntimeError(data.stderr.decode("utf-8", "replace")[-1000:])
    with tarfile.open(fileobj=io.BytesIO(data.stdout)) as archive:
        # Python 3.8 has no tarfile extraction filters. Reject links and path escapes.
        root = os.path.realpath(destination)
        for member in archive.getmembers():
            target = os.path.realpath(os.path.join(root, member.name))
            if (not target.startswith(root + os.sep) or
                    not (member.isfile() or member.isdir())):
                raise RuntimeError("候选版本包含不安全的归档路径: %s" % member.name)
        archive.extractall(destination)


def _prepare_python(candidate, target):
    """Never pip-install into the live interpreter; retain the previous venv for rollback."""
    parent = os.path.join(_state_dir(), "envs")
    os.makedirs(parent, exist_ok=True)
    environment = tempfile.mkdtemp(prefix=target[:12] + "-", dir=parent)
    interpreter = os.path.join(environment, "Scripts" if os.name == "nt" else "bin",
                               "python.exe" if os.name == "nt" else "python")
    try:
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        _run([sys.executable, "-m", "venv", environment], cwd=candidate, timeout=120, env=env)
        wheels = os.environ.get("ANTCLOUD_WHEELHOUSE", "")
        if not wheels and os.path.isdir(os.path.join(BASE, "offline", "wheels")):
            wheels = os.path.join(BASE, "offline", "wheels")
        options = []
        if wheels:
            wheels = os.path.abspath(os.path.join(BASE, wheels))
            if not os.path.isdir(wheels):
                raise RuntimeError("离线依赖目录不存在: %s" % wheels)
            options = ["--no-index", "--find-links", wheels]
        _run([interpreter, "-m", "pip", "install", "--disable-pip-version-check", *options,
              "-r", os.path.join(candidate, "requirements.txt")],
             cwd=candidate, timeout=PIP_TIMEOUT, env=env)
        return interpreter
    except Exception:
        shutil.rmtree(environment, ignore_errors=True)
        raise


def _discard_unused_python(interpreter, previous_python):
    """Delete only this attempt's unused child of envs, never an active/recovery venv."""
    try:
        interpreter = os.path.abspath(interpreter)
        environment = os.path.dirname(os.path.dirname(interpreter))
        parent = os.path.abspath(os.path.join(_state_dir(), "envs"))
        expected = os.path.join(environment, "Scripts" if os.name == "nt" else "bin",
                                "python.exe" if os.name == "nt" else "python")
        if (interpreter != expected or os.path.dirname(environment) != parent or
                os.path.islink(environment) or
                os.path.realpath(environment) != os.path.join(os.path.realpath(parent),
                                                              os.path.basename(environment))):
            return
        state = _load_state()
        pending = state.get("pending") or {}
        preserved = (previous_python, state.get("active_python"), pending.get("python"),
                     pending.get("previous_python"))
        if any(value and os.path.dirname(os.path.dirname(os.path.abspath(value))) == environment
               for value in preserved):
            return
        shutil.rmtree(environment)
    except Exception as exc:
        # Failed/uncertain state reads must never turn cleanup into removal of a live venv.
        print("未能清理未使用的候选依赖环境: %s" % str(exc)[-500:], file=sys.stderr)


_SELFCHECK = r'''
import asyncio, inspect, os, sys
sys.path.insert(0, os.getcwd())
import updater
updater.start_auto = lambda *a, **kw: None
updater.start_health_timer = lambda *a, **kw: None
import server
# Startup handlers still execute, but no collection, notification, update or real DB runs.
server.COLLECTOR.start = lambda: None
async def check():
    for callback in server.app.router.on_startup:
        result = callback()
        if inspect.isawaitable(result):
            await result
    for callback in server.app.router.on_shutdown:
        result = callback()
        if inspect.isawaitable(result):
            await result
asyncio.run(check())
conn = getattr(server, "CONN", None)
if conn is not None:
    conn.close()
'''


def _preflight(candidate, interpreter):
    for directory, _subdirs, filenames in os.walk(candidate):
        for filename in filenames:
            if filename.endswith(".py"):
                py_compile.compile(os.path.join(directory, filename), doraise=True)
    with tempfile.TemporaryDirectory(prefix="antcloud-check-") as sandbox:
        config = os.path.join(sandbox, "config.yaml")
        database = os.path.join(sandbox, "check.db")
        with open(config, "w", encoding="utf-8") as stream:
            stream.write("db: {path: " + json.dumps(database) + "}\nsites: []\nalerts: {}\n"
                         "update: {auto: false}\nserver: {host: 127.0.0.1, port: 0}\n"
                         "auth: {enabled: true, users: []}\ningest: {enabled: false}\n")
        env = dict(os.environ, CO_CONFIG=config,
                   CO_SETTINGS=os.path.join(sandbox, "settings.json"), CO_SELF_CHECK="1",
                   CO_LAUNCHER_CHILD="1", PYTHONDONTWRITEBYTECODE="1")
        # A newly installed environment must prove it contains its own dependencies.
        if os.path.realpath(interpreter) != os.path.realpath(sys.executable) or (
                os.path.dirname(os.path.dirname(interpreter)).startswith(_state_dir() + os.sep)):
            env.pop("PYTHONPATH", None)
            env.pop("PYTHONHOME", None)
        _run([interpreter, "-c", _SELFCHECK], cwd=candidate, timeout=SELFCHECK_TIMEOUT, env=env)


def is_applying():
    return _apply_lock.locked()


def _restart():
    os._exit(42)


def apply(branch=None, restart=True, force=False, fallback_url=None):
    """Prepare -> recheck cleanliness -> persist recovery -> promote -> supervised restart."""
    if not is_git_deployment():
        return {"ok": False, "msg": "本目录不是 git 仓库(未用 git 部署)"}
    if not _apply_lock.acquire(blocking=False):
        return {"ok": False, "msg": "正在更新或等待重启，请稍候"}
    lock = None
    keep_lock = False
    unused_python = None
    previous_python = None
    retain_candidate = False
    try:
        lock = _lock_file()
        if lock is None:
            return {"ok": False, "msg": "其他进程正在更新，请稍候"}
        state = _load_state()
        if state.get("pending"):
            return {"ok": False, "msg": "已有更新等待启动健康确认，请先重启并确认服务"}
        if _dirty():
            return {"ok": False, "msg": "工作区有未提交或暂存改动，请先提交/保存现场改动后再更新"}
        if restart and os.environ.get("CO_LAUNCHER_CHILD") != "1":
            return {"ok": False, "msg": "请先使用 python cloud_launcher.py 启动服务，再执行在线更新"}
        status = check(branch, fallback_url=fallback_url) if fallback_url else check(branch)
        if status.get("error") or not status.get("behind"):
            return {"ok": False, "unchanged": not bool(status.get("error")),
                    "msg": status.get("error") or "已是最新版本", "check": status}
        previous = _git("rev-parse", "HEAD")
        target = status["target"]
        if target in state.get("bad", []) and not force:
            return {"ok": False, "msg": "该版本上次检查/启动失败，自动更新已跳过；修复后可手动重试"}
        _git("merge-base", "--is-ancestor", previous, target)
        previous_python = runtime_python()
        candidate_python = previous_python
        with tempfile.TemporaryDirectory(prefix="antcloud-candidate-") as candidate:
            try:
                _extract_candidate(target, candidate)
                changed = _git("diff", "--name-only", previous, target, "--", "requirements.txt")
                if changed:
                    candidate_python = _prepare_python(candidate, target)
                    unused_python = candidate_python
                _preflight(candidate, candidate_python)
            except Exception as exc:
                _mark_bad(state, target, "候选版本检查失败: %s" % exc)
                return {"ok": False, "msg": "候选版本检查失败，运行代码及依赖未改变: %s" % str(exc)[-1000:]}
        if _git("rev-parse", "HEAD") != previous or _dirty():
            return {"ok": False, "msg": "检查期间工作区或 HEAD 发生变化，已停止更新并保留现场改动"}
        state["pending"] = {"from": previous, "to": target, "branch": status["branch"],
                            "previous_python": previous_python, "python": candidate_python,
                            "attempts": 0, "phase": "prepared"}
        # Must be durable before the live checkout changes. From this point a
        # failed save may have an uncertain outcome, so retain its recovery venv.
        retain_candidate = True
        _save_state(state)
        try:
            _git("merge", "--ff-only", target)
        except Exception:
            if _git("rev-parse", "HEAD") == previous:
                state["pending"] = None
                _save_state(state)
            raise
        state["active_python"] = candidate_python
        state["pending"]["phase"] = "startup"
        state["bad"] = [sha for sha in state.get("bad", []) if sha != target]
        _save_state(state)
        if restart:
            # Hold both locks until process exit, including the HTTP-response delay.
            timer = threading.Timer(1.5, _restart)
            timer.daemon = True
            timer.start()
            keep_lock = True
        return {"ok": True, "from": previous[:10], "to": target[:10],
                "changes": status.get("changes"), "restarting": restart, "pending": True}
    except Exception as exc:
        return {"ok": False, "msg": "更新未完成，已保留现场/恢复状态: %s" % str(exc)[-1000:]}
    finally:
        if unused_python and not retain_candidate:
            _discard_unused_python(unused_python, previous_python)
        if not keep_lock:
            _unlock_file(lock)
            _apply_lock.release()
        else:
            # Keep the descriptor reachable until exit: GC must not release flock early.
            globals()["_restart_file_lock"] = lock


def boot_guard():
    """Called by the launcher before each child. Never reset over operator changes."""
    if not is_git_deployment():
        return {"status": "ready"}
    try:
        with _state_transaction():
            state = _load_state()
            pending = state.get("pending")
            if not pending:
                return {"status": "ready"}
            current = _git("rev-parse", "HEAD")
            if current == pending["from"]:
                # Interrupted before promotion (or restored before the state write).
                state["active_python"] = pending["previous_python"]
                state["pending"] = None
                _save_state(state)
                return {"status": "ready"}
            if current != pending["to"]:
                return {"status": "blocked", "msg": "更新后 HEAD 被其他操作改变，请手动检查恢复状态"}
            attempts = int(pending.get("attempts", 0))
            if attempts < MAX_BOOT_ATTEMPTS:
                pending["attempts"] = attempts + 1
                pending["phase"] = "startup"
                state["active_python"] = pending["python"]
                _save_state(state)
                return {"status": "ready", "attempt": attempts + 1, "pending": True, "target": pending["to"]}
            if _dirty():
                return {"status": "blocked", "msg": "新版本连续启动失败，但工作区有现场改动；已停止自动回退，请人工检查"}
            _git("reset", "--keep", pending["from"])
            state["active_python"] = pending["previous_python"]
            state["pending"] = None
            _mark_bad(state, pending["to"], "新版本启动健康确认失败，已恢复上一版本和依赖环境")
            return {"status": "rolled_back", "from": pending["to"], "to": pending["from"]}
    except Exception as exc:
        return {"status": "blocked", "msg": "恢复检查失败: %s" % str(exc)[-1000:]}


def cancel_boot_attempt(guard):
    """A deliberate/graceful stop before the timer fires is not a failed boot."""
    if not guard.get("pending"):
        return
    try:
        with _state_transaction():
            state = _load_state()
            pending = state.get("pending")
            if (pending and pending["to"] == _git("rev-parse", "HEAD")
                    and pending.get("attempts") == guard.get("attempt")):
                pending["attempts"] = max(0, int(pending["attempts"]) - 1)
                _save_state(state)
    except Exception as exc:
        print("[update] 停止服务时更新状态未保存: %s" % exc)


def mark_healthy(expected_sha=None):
    # A timer from the old process must never confirm a candidate it has not run.
    with _state_transaction():
        state = _load_state()
        pending = state.get("pending")
        if not pending or not expected_sha or pending["to"] != expected_sha:
            return False
        if _git("rev-parse", "HEAD") != expected_sha:
            return False
        state["pending"] = None
        state["healthy"] = expected_sha
        state["last_error"] = ""
        _save_state(state)
        return True


def health_identity():
    """Bind the HTTP response to the exact child the guardian started."""
    return os.environ.get("CO_BOOT_ID") or _process_identity


def _health_url():
    # Reuse the configuration of this running server, without importing/starting another app.
    config = {}
    for name in ("__main__", "server"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "app") and hasattr(module, "CFG"):
            config = module.CFG
            break
    server = config.get("server") or {}
    host = str(server.get("host") or "0.0.0.0")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host and not host.startswith("["):
        host = "[" + host + "]"
    scheme = "https" if server.get("tls_cert") and server.get("tls_key") else "http"
    return "%s://%s:%s/api/health" % (scheme, host, int(server.get("port", 8900)))


def _http_healthy(url, identity):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    try:
        # Local deployments may use self-signed TLS. This is a loopback readiness check,
        # not a credential-bearing request; the unpredictable instance ID prevents a stale
        # listener on the same port from confirming the new child.
        context = ssl._create_unverified_context()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                             urllib.request.HTTPSHandler(context=context))
        with opener.open(url, timeout=2) as response:
            data = response.read(8193)
            if response.status != 200 or len(data) > 8192:
                return False
            result = json.loads(data.decode("utf-8"))
            return result.get("ok") is True and result.get("instance") == identity
    except Exception:
        return False


def start_health_timer():
    global _health_timer, _health_stop
    cancel_health_timer()
    try:
        if not is_git_deployment() or os.environ.get("CO_SELF_CHECK") == "1":
            return None
        pending = _load_state().get("pending")
        expected = _git("rev-parse", "HEAD")
        if not pending or pending["to"] != expected:
            return None
        url, identity = _health_url(), health_identity()
    except Exception:
        return None
    stop = threading.Event()
    _health_stop = stop

    def confirm():
        if stop.wait(HEALTHY_AFTER_SEC):
            return
        while not stop.is_set():
            if _http_healthy(url, identity) and not stop.is_set():
                try:
                    if mark_healthy(expected_sha=expected):
                        return
                except Exception as exc:
                    print("[update] 健康确认未保存: %s" % exc)
            if stop.wait(HEALTH_RETRY_SEC):
                return

    _health_timer = threading.Thread(target=confirm, name="update-health", daemon=True)
    _health_timer.start()
    return _health_timer


def cancel_health_timer():
    global _health_timer, _health_stop
    if _health_stop is not None:
        _health_stop.set()
        _health_stop = None
    _health_timer = None


def guard_server_entry():
    """Legacy systemd/python server.py enters the guardian before third-party imports."""
    if os.environ.get("CO_LAUNCHER_CHILD") == "1" or not is_git_deployment():
        return
    launcher = os.path.join(BASE, "cloud_launcher.py")
    os.execv(sys.executable, [sys.executable, launcher, *sys.argv[1:]])


def start_auto(ucfg, on_event=None):
    if not (ucfg or {}).get("auto"):
        return None
    interval = max(300, int(ucfg.get("check_interval", 3600)))
    branch = ucfg.get("branch") or None

    def say(message):
        try:
            (on_event or print)(message)
        except Exception:
            pass

    def loop():
        while True:
            time.sleep(interval)
            try:
                status = check(branch)
                if status.get("behind") and not status.get("bad") and not status.get("pending"):
                    result = apply(branch)
                    say("自动更新已提交，等待重启健康确认" if result.get("ok")
                        else "自动更新推迟/失败: " + result.get("msg", ""))
            except Exception as exc:
                say("自动检测异常: %s" % exc)

    worker = threading.Thread(target=loop, name="auto-update", daemon=True)
    worker.start()
    return worker


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    if command == "check":
        print(json.dumps(check(), ensure_ascii=False, indent=2))
    elif command == "apply":
        result = apply(restart=False, force=True)
        print(result.get("msg") or "已更新 %s → %s，请重启服务并等待健康确认" % (result["from"], result["to"]))
        sys.exit(0 if result.get("ok") else 1)
    else:
        print("用法: python updater.py check|apply")
        sys.exit(2)
