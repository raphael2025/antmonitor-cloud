# -*- coding: utf-8 -*-
"""云端更新源探测：GitHub/Gitee 谁通用谁；都通保持当前来源；全不通保留原顺序。"""
import update_source as us
import updater

GH = "https://github.com/raphael2025/antmonitor-cloud.git"
GE = "https://gitee.com/raphael01/antmonitor-cloud.git"


def _prober(latencies):
    return lambda url, timeout: latencies.get(url)


def setup_function(_):
    us.reset_cache()


def test_unreachable_source_goes_last_and_reachable_first():
    assert us.ordered([GH, GE], preferred=GH, prober=_prober({GH: None, GE: 0.5}), cache_ttl=0) == [GE, GH]


def test_both_reachable_keeps_preferred_even_if_slower():
    assert us.ordered([GH, GE], preferred=GH, prober=_prober({GH: 2.0, GE: 0.1}), cache_ttl=0) == [GH, GE]


def test_all_unreachable_keeps_original_order():
    assert us.ordered([GH, GE], preferred=GH, prober=_prober({}), cache_ttl=0) == [GH, GE]


def test_probe_git_accepts_only_real_git_answer():
    ok = lambda u, t: (200, "application/x-git-upload-pack-advertisement", b"")  # noqa: E731
    assert us.probe_git(GH, fetcher=ok) is not None
    page = lambda u, t: (200, "text/html", b"<html>login")  # noqa: E731
    assert us.probe_git(GH, fetcher=page) is None


def test_non_http_sources_keep_their_order():
    assert us.ordered(["/srv/mirror.git", GH], preferred=GH, prober=_prober({})) == ["/srv/mirror.git", GH]


def test_fetch_sources_use_working_mirror_first(monkeypatch):
    monkeypatch.setattr(updater, "_git", lambda *a, **k: GH)
    monkeypatch.setattr(updater.update_source, "ordered", lambda urls, preferred=None, **k: [GE, GH])
    assert updater._fetch_sources() == [GE, GH]          # GitHub 不通 → Gitee 在前
    monkeypatch.setattr(updater.update_source, "ordered", lambda urls, preferred=None, **k: list(urls))
    assert updater._fetch_sources() == [GH, GE]


def test_probe_error_falls_back_to_default_order(monkeypatch):
    monkeypatch.setattr(updater, "_git", lambda *a, **k: GH)

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(updater.update_source, "ordered", boom)
    assert updater._fetch_sources() == [GH, GE]
