import threading
import time

import collector as collector_module
from collector import Collector


def test_slow_notification_does_not_block_collection(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    polls = []
    collector = Collector.__new__(Collector)
    collector.cfg = {"poll": {}, "db": {}}
    collector.interval = 0.05
    collector.customer_interval = 10_000
    collector._stop = threading.Event()
    collector._thread = None
    collector._notify_thread = None
    collector.last_poll_ts = 0
    collector.poll_summaries = lambda: polls.append(time.monotonic())
    collector.all_sites = lambda: []
    collector.site_state = lambda *_args, **_kwargs: "ok"

    class SlowNotifier:
        def tick(self, *_args):
            entered.set()
            release.wait(2)

    collector.notifier = SlowNotifier()
    monkeypatch.setattr(collector_module.db, "rollup_daily", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(collector_module.db, "cleanup", lambda *_args, **_kwargs: None)

    collector.start()
    try:
        assert entered.wait(1)
        time.sleep(0.18)
        assert len(polls) >= 2
    finally:
        collector.stop()
        release.set()
