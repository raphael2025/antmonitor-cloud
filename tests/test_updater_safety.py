"""Update safety uses local temporary Git repos and never restarts a real service."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

import updater


SERVER = '''class Collector:
    def start(self):
        raise AssertionError("self-check must not start collection")
COLLECTOR = Collector()
class Router:
    on_startup = []
    on_shutdown = []
class App:
    router = Router()
app = App()
'''


def git(path, *args):
    return subprocess.check_output(["git", *args], cwd=str(path), stderr=subprocess.STDOUT).decode().strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    deploy = tmp_path / "deploy"
    subprocess.check_call(["git", "init", "--bare", "-q", str(origin)])
    subprocess.check_call(["git", "init", "-q", str(seed)])
    git(seed, "config", "user.name", "test")
    git(seed, "config", "user.email", "test@example.invalid")
    git(seed, "checkout", "-b", "main")
    (seed / "server.py").write_text(SERVER)
    (seed / "requirements.txt").write_text("")
    (seed / "updater.py").write_text("def start_auto(*a, **kw): pass\ndef start_health_timer(): pass\n")
    (seed / "cloud_launcher.py").write_text("# launcher\n")
    (seed / "site.css").write_text("original\n")
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "base")
    previous = git(seed, "rev-parse", "HEAD")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "main")
    git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    subprocess.check_call(["git", "clone", "-q", str(origin), str(deploy)])
    monkeypatch.setattr(updater, "BASE", str(deploy))
    monkeypatch.setattr(updater, "_apply_lock", threading.Lock(), raising=False)
    monkeypatch.setattr(updater, "_health_timer", None, raising=False)

    def push(files):
        for name, content in files.items():
            (seed / name).parent.mkdir(parents=True, exist_ok=True)
            (seed / name).write_text(content)
        git(seed, "add", ".")
        git(seed, "commit", "-qm", "candidate")
        git(seed, "push", "-q", "origin", "main")
        return git(seed, "rev-parse", "HEAD")

    return deploy, previous, push


@pytest.mark.parametrize("staged", [False, True])
def test_update_preserves_dirty_tracked_files(repo, staged):
    deploy, previous, push = repo
    push({"server.py": "def broken(:\n"})
    (deploy / "site.css").write_text("operator customization\n")
    if staged:
        git(deploy, "add", "site.css")
    result = updater.apply(restart=False)
    assert not result["ok"]
    assert (deploy / "site.css").read_text() == "operator customization\n"
    assert git(deploy, "rev-parse", "HEAD") == previous


@pytest.mark.parametrize("new_server", ["raise RuntimeError('missing runtime dependency')\n",
    SERVER + "\ndef startup(): raise RuntimeError('startup failed')\napp.router.on_startup=[startup]\n"])
def test_valid_syntax_but_failed_startup_never_promotes(repo, new_server):
    deploy, previous, push = repo
    target = push({"server.py": new_server})
    result = updater.apply(restart=False)
    assert not result["ok"]
    assert git(deploy, "rev-parse", "HEAD") == previous
    assert target in updater._load_state().get("bad", [])
    # A rejected candidate must not be installed automatically on the next poll.
    assert updater.check().get("bad")


def test_pending_update_is_not_reapplied_and_health_is_bound_to_target(repo, monkeypatch):
    deploy, previous, push = repo
    target = push({"site.css": "new\n"})
    result = updater.apply(restart=False)
    assert result["ok"], result
    assert updater._load_state()["pending"]["to"] == target
    assert git(deploy, "rev-parse", "HEAD") == target
    assert not updater.apply(restart=False)["ok"]
    updater.mark_healthy(expected_sha=previous)
    assert updater._load_state().get("pending")
    updater.mark_healthy(expected_sha=target)
    assert not updater._load_state().get("pending")


def test_failure_after_three_boot_attempts_rolls_back_and_suppresses_bad(repo):
    deploy, previous, push = repo
    target = push({"site.css": "new\n"})
    assert updater.apply(restart=False)["ok"]
    for _ in range(updater.MAX_BOOT_ATTEMPTS):
        assert updater.boot_guard()["status"] == "ready"
    assert updater.boot_guard()["status"] == "rolled_back"
    assert git(deploy, "rev-parse", "HEAD") == previous
    state = updater._load_state()
    assert state["pending"] is None
    assert target in state["bad"]
    assert not updater.apply(restart=False)["ok"]
    assert updater.apply(restart=False, force=True)["ok"]


def test_boot_rollback_refuses_to_destroy_new_operator_edits(repo):
    deploy, _previous, push = repo
    target = push({"site.css": "new\n"})
    assert updater.apply(restart=False)["ok"]
    (deploy / "site.css").write_text("emergency local fix\n")
    for _ in range(updater.MAX_BOOT_ATTEMPTS):
        updater.boot_guard()
    result = updater.boot_guard()
    assert result["status"] == "blocked"
    assert git(deploy, "rev-parse", "HEAD") == target
    assert (deploy / "site.css").read_text() == "emergency local fix\n"


def test_dirty_change_during_preflight_aborts_without_losing_edit(repo, monkeypatch):
    deploy, previous, push = repo
    push({"site.css": "new\n"})

    def preflight(*args, **kwargs):
        (deploy / "site.css").write_text("edit during checks\n")

    monkeypatch.setattr(updater, "_preflight", preflight, raising=False)
    result = updater.apply(restart=False)
    assert not result["ok"]
    assert git(deploy, "rev-parse", "HEAD") == previous
    assert (deploy / "site.css").read_text() == "edit during checks\n"


def test_dependency_failure_does_not_change_live_python_or_git(repo, monkeypatch):
    deploy, previous, push = repo
    push({"requirements.txt": "broken-dependency==0\n"})
    calls = []

    def install(candidate, target):
        calls.append((Path(candidate), target))
        assert Path(candidate) != deploy
        raise RuntimeError("dependency install failed")

    monkeypatch.setattr(updater, "_prepare_python", install, raising=False)
    result = updater.apply(restart=False)
    assert not result["ok"]
    assert calls
    assert git(deploy, "rev-parse", "HEAD") == previous
    assert not updater._load_state().get("active_python")


def test_concurrent_apply_does_not_enter_preflight_twice(repo, monkeypatch):
    _deploy, _previous, push = repo
    push({"site.css": "new\n"})
    entered, release = threading.Event(), threading.Event()
    original = updater._preflight

    def preflight(*a, **kw):
        entered.set()
        assert release.wait(5)
        return original(*a, **kw)

    monkeypatch.setattr(updater, "_preflight", preflight)
    result = []
    thread = threading.Thread(target=lambda: result.append(updater.apply(restart=False)))
    thread.start()
    try:
        assert entered.wait(5)
        second = updater.apply(restart=False)
        assert not second["ok"]
    finally:
        release.set()
        thread.join(10)
    assert result[0]["ok"], result


def test_archive_server_entry_does_not_require_git(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "BASE", str(tmp_path))
    monkeypatch.delenv("CO_LAUNCHER_CHILD", raising=False)
    monkeypatch.setattr(os, "execv", lambda *a: pytest.fail("archive server entered Git guardian"))
    updater.guard_server_entry()
    assert updater.boot_guard()["status"] == "ready"
    assert updater.runtime_python() == sys.executable


def test_explicit_launcher_runs_non_git_archive(tmp_path):
    import shutil
    source = Path(updater.__file__).resolve().parent
    for filename in ("updater.py", "cloud_launcher.py"):
        shutil.copyfile(str(source / filename), str(tmp_path / filename))
    (tmp_path / "server.py").write_text("print('archive ready')\n")
    result = subprocess.run([sys.executable, str(tmp_path / "cloud_launcher.py")],
                            cwd=str(tmp_path), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "archive ready" in result.stdout


@pytest.mark.parametrize("explicit", [False, True])
def test_candidate_dependencies_keep_offline_wheelhouse(repo, monkeypatch, explicit):
    deploy, _previous, _push = repo
    wheels = deploy.parent / "wheel cache" if explicit else deploy / "offline" / "wheels"
    wheels.mkdir(parents=True)
    if explicit:
        monkeypatch.setenv("ANTCLOUD_WHEELHOUSE", str(wheels))
    else:
        monkeypatch.delenv("ANTCLOUD_WHEELHOUSE", raising=False)
    calls = []
    monkeypatch.setattr(updater, "_run", lambda args, **kw: calls.append(args) or "")
    updater._prepare_python(str(deploy), "a" * 40)
    pip = next(args for args in calls if "pip" in args)
    assert "--no-index" in pip
    assert pip[pip.index("--find-links") + 1] == str(wheels)


def test_health_confirmation_requires_this_process_http_response(repo, monkeypatch):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    _deploy, _previous, push = repo
    target = push({"site.css": "new\n"})
    assert updater.apply(restart=False)["ok"]
    identity = "correct-instance"
    response = {"ok": True, "instance": "different-process"}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    http = HTTPServer(("127.0.0.1", 0), Handler)
    serving = threading.Thread(target=http.serve_forever, daemon=True)
    serving.start()
    monkeypatch.setenv("CO_BOOT_ID", identity)
    monkeypatch.setattr(updater, "_health_url", lambda: "http://127.0.0.1:%d/api/health" % http.server_port,
                        raising=False)
    monkeypatch.setattr(updater, "HEALTHY_AFTER_SEC", 0.01)
    monkeypatch.setattr(updater, "HEALTH_RETRY_SEC", 0.01, raising=False)
    confirmed = threading.Event()
    original = updater.mark_healthy
    def mark(*args, **kw):
        result = original(*args, **kw)
        confirmed.set()
        return result
    monkeypatch.setattr(updater, "mark_healthy", mark)
    try:
        updater.start_health_timer()
        assert not confirmed.wait(0.1), "elapsed uptime or another HTTP listener cannot confirm this boot"
        assert updater._load_state()["pending"]["to"] == target
        response["instance"] = identity
        assert confirmed.wait(3), "a live same-process HTTP health response should confirm the version"
        assert updater._load_state()["pending"] is None
    finally:
        updater.cancel_health_timer()
        http.shutdown()
        http.server_close()
        serving.join(2)


@pytest.mark.parametrize("behavior", ["exit_zero", "hang"])
def test_launcher_failed_candidate_is_bounded_and_rolls_back(repo, monkeypatch, behavior):
    import cloud_launcher
    import time
    deploy, previous, push = repo
    if behavior == "exit_zero":
        extra = "\nif __name__ == '__main__':\n    raise SystemExit(0)\n"
    else:
        extra = "\nif __name__ == '__main__':\n    import signal, time\n    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n    while True: time.sleep(0.05)\n"
    target = push({"server.py": SERVER + extra})
    assert updater.apply(restart=False)["ok"]
    monkeypatch.setattr(cloud_launcher, "BOOT_TIMEOUT_SEC", 0.1, raising=False)
    monkeypatch.setattr(cloud_launcher, "STOP_TIMEOUT_SEC", 0.1, raising=False)
    monkeypatch.setattr(cloud_launcher, "RETRY_DELAY_SEC", 0, raising=False)
    started = time.monotonic()
    # The previous server exits normally; the unconfirmed candidate must exhaust
    # its budget first, including a zero exit status and a SIGTERM-ignoring hang.
    assert cloud_launcher.main([]) == 0
    assert time.monotonic() - started < 8
    assert git(deploy, "rev-parse", "HEAD") == previous
    state = updater._load_state()
    assert not state.get("pending")
    assert target in state["bad"]


def test_parallel_checks_keep_their_own_fetched_branch(repo, monkeypatch):
    deploy, previous, push = repo
    main_target = push({"site.css": "main change\n"})
    seed = deploy.parent / "seed"
    git(seed, "checkout", "-b", "release", previous)
    (seed / "site.css").write_text("release change\n")
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "release")
    release_target = git(seed, "rev-parse", "HEAD")
    git(seed, "push", "-q", "origin", "release")
    fetched, resume = threading.Event(), threading.Event()
    original = updater._git
    def interleaved(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[0] == "fetch" and any(str(arg).split(":")[0] == "main" for arg in args[1:]):
            fetched.set()
            assert resume.wait(5)
        return result
    monkeypatch.setattr(updater, "_git", interleaved)
    main_results = []
    worker = threading.Thread(target=lambda: main_results.append(updater.check("main")))
    worker.start()
    try:
        assert fetched.wait(5)
        release = updater.check("release")
        assert release["target"] == release_target
    finally:
        resume.set()
        worker.join(5)
    assert main_results[0]["target"] == main_target


def test_installer_fallback_pins_checked_commit_without_changing_origin(repo):
    deploy, _previous, push = repo
    target = push({"site.css": "from available mirror\n"})
    mirror = git(deploy, "remote", "get-url", "origin")
    unavailable = str(deploy.parent / "missing-origin.git")
    git(deploy, "remote", "set-url", "origin", unavailable)
    result = updater.apply(restart=False, fallback_url=mirror)
    assert result["ok"], result
    assert git(deploy, "rev-parse", "HEAD") == target
    assert git(deploy, "remote", "get-url", "origin") == unavailable
    assert updater._load_state()["pending"]["to"] == target


def test_successful_dependency_update_uses_separate_venv_and_restores_previous(repo, monkeypatch):
    deploy, previous, push = repo
    wheels = deploy.parent / "empty-wheelhouse"
    wheels.mkdir()
    monkeypatch.setenv("ANTCLOUD_WHEELHOUSE", str(wheels))
    target = push({"requirements.txt": "# isolated empty requirements for offline test\n"})
    original_python = updater.runtime_python()
    result = updater.apply(restart=False)
    assert result["ok"], result
    selected = updater.runtime_python()
    assert selected != original_python
    assert Path(selected).is_file()
    assert Path(updater._state_dir()) / "envs" in Path(selected).parents
    assert updater._load_state()["pending"]["previous_python"] == original_python
    for _ in range(updater.MAX_BOOT_ATTEMPTS):
        updater.boot_guard()
    assert updater.boot_guard()["status"] == "rolled_back"
    assert updater.runtime_python() == original_python
    assert git(deploy, "rev-parse", "HEAD") == previous
    assert target in updater._load_state()["bad"]


@pytest.mark.parametrize("abort_at", ["preflight", "dirty", "head"])
def test_aborted_candidate_removes_only_its_unused_dependency_environment(repo, monkeypatch, abort_at):
    deploy, previous, push = repo
    push({"requirements.txt": "# dependency update\n"})
    environments = Path(updater._state_dir()) / "envs"
    old_environment = environments / "previous"
    old_python = old_environment / "bin" / "python"
    old_python.parent.mkdir(parents=True)
    old_python.write_text("old interpreter")
    updater._save_state({"active_python": str(old_python)})
    new_environment = environments / "candidate-this-attempt"
    new_python = new_environment / "bin" / "python"
    expected_head = [previous]
    def prepare(*args):
        new_python.parent.mkdir(parents=True)
        new_python.write_text("new interpreter")
        return str(new_python)
    def preflight(*args):
        if abort_at == "preflight":
            raise RuntimeError("candidate startup rejected")
        if abort_at == "dirty":
            (deploy / "site.css").write_text("operator change\n")
        else:
            git(deploy, "config", "user.name", "operator")
            git(deploy, "config", "user.email", "operator@example.invalid")
            git(deploy, "commit", "--allow-empty", "-qm", "operator commit")
            expected_head[0] = git(deploy, "rev-parse", "HEAD")
    monkeypatch.setattr(updater, "_prepare_python", prepare)
    monkeypatch.setattr(updater, "_preflight", preflight)
    result = updater.apply(restart=False)
    assert not result["ok"], result
    assert not new_environment.exists(), "unused candidate dependencies must not accumulate after retries"
    assert old_python.read_text() == "old interpreter"
    assert updater.runtime_python() == str(old_python)
    assert not updater._load_state().get("pending")
    assert git(deploy, "rev-parse", "HEAD") == expected_head[0]
    if abort_at == "dirty":
        assert (deploy / "site.css").read_text() == "operator change\n"


@pytest.mark.parametrize("location", ["outside", "symlink", "active"])
def test_aborted_candidate_cleanup_cannot_remove_external_or_active_environment(repo, monkeypatch, location):
    deploy, _previous, push = repo
    push({"requirements.txt": "# dependency update\n"})
    environments = Path(updater._state_dir()) / "envs"
    environments.mkdir(parents=True)
    directory = environments / "active" if location == "active" else deploy.parent / "external-env"
    interpreter = directory / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("must remain")
    supplied = interpreter
    if location == "symlink":
        link = environments / "candidate-link"
        link.symlink_to(directory, target_is_directory=True)
        supplied = link / "bin" / "python"
    if location == "active":
        updater._save_state({"active_python": str(interpreter)})
    monkeypatch.setattr(updater, "_prepare_python", lambda *a: str(supplied))
    def reject(*args):
        raise RuntimeError("preflight rejected")
    monkeypatch.setattr(updater, "_preflight", reject)
    assert not updater.apply(restart=False)["ok"]
    assert interpreter.read_text() == "must remain"
