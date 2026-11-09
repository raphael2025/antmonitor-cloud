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
    class FastStop:
        def __init__(self):
            self.event = threading.Event()

        def is_set(self):
            return self.event.is_set()

        def set(self):
            self.event.set()

        def wait(self, timeout=None):
            return self.event.wait(min(timeout or 0, 0.05))

    collector._stop = FastStop()
    collector.conn = object()
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
        time.sleep(0.22)
        assert len(polls) >= 2
    finally:
        collector.stop()
        release.set()
