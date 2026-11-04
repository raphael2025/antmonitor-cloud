#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Supervise startup with stdlib and retain the recovery implementation in memory.

Both current systemd installs and legacy ``python server.py`` enter here. A
pending version must serve its own HTTP health response within the deadline.
Exit 42 requests an update restart. Exit 78 requests operator intervention.
"""
import os
import signal
import subprocess
import sys
import time
import uuid

import updater

BOOT_TIMEOUT_SEC = 180
STOP_TIMEOUT_SEC = 10
RETRY_DELAY_SEC = 1


def _pending():
    return updater._load_state().get("pending") if updater.is_git_deployment() else None


def _wait_child(child, guard, stopped):
    deadline = time.monotonic() + BOOT_TIMEOUT_SEC if guard.get("pending") else None
    stop_deadline = None
    while True:
        try:
            return child.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            now = time.monotonic()
            if stopped() and stop_deadline is None:
                stop_deadline = now + STOP_TIMEOUT_SEC
            if stop_deadline is not None and now >= stop_deadline:
                child.kill()
                return child.wait(timeout=STOP_TIMEOUT_SEC)
            if not stopped() and deadline is not None and now >= deadline:
                pending = _pending()
                if not pending or pending.get("to") != guard.get("target"):
                    # This child was confirmed, or a later update has just been queued.
                    deadline = None
                    continue
                print("[update] 新版本未在启动期限内通过 HTTP 健康确认，终止本次启动", flush=True)
                child.terminate()
                try:
                    child.wait(timeout=STOP_TIMEOUT_SEC)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=STOP_TIMEOUT_SEC)
                return 1


def main(args=None):
    args = list(sys.argv[1:] if args is None else args)
    child = None
    stopping = False
    handlers = {}

    def stop(signum, _frame):
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None:
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass

    for sig in (signal.SIGTERM, signal.SIGINT):
        handlers[sig] = signal.signal(sig, stop)
    try:
        while not stopping:
            guard = updater.boot_guard()
            if guard.get("status") == "blocked":
                print("[update] " + guard.get("msg", "恢复失败，需要人工检查"), file=sys.stderr)
                return 78
            if guard.get("status") == "rolled_back":
                print("[update] 新版本启动失败，已恢复上一版本和运行环境", flush=True)
            try:
                interpreter = updater.runtime_python()
                env = dict(os.environ, CO_LAUNCHER_CHILD="1", CO_BOOT_ID=uuid.uuid4().hex)
                child = subprocess.Popen([interpreter, os.path.join(updater.BASE, "server.py"), *args],
                                         cwd=updater.BASE, env=env)
                if stopping:
                    child.terminate()
                code = _wait_child(child, guard, lambda: stopping)
            except Exception as exc:
                print("[update] 无法启动/监督服务: %s" % exc, file=sys.stderr)
                if child is not None and child.poll() is None:
                    child.kill()
                    child.wait(timeout=STOP_TIMEOUT_SEC)
                code = 1
            if stopping:
                updater.cancel_boot_attempt(guard)
                return 0
            try:
                pending = _pending()
            except Exception as exc:
                print("[update] 恢复状态不可读: %s" % exc, file=sys.stderr)
                return 78
            if code == 42:
                continue
            if not pending:
                return code if code >= 0 else 1
            # Unexpected exit(0) before readiness is also a failed boot. Only a
            # deliberate signal to this guardian cancels the current attempt.
            time.sleep(RETRY_DELAY_SEC)
        return 0
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
