import threading
import time

from collector import Collector


def test_poll_summaries_never_exceeds_configured_worker_limit():
    collector = Collector.__new__(Collector)
    collector.max_workers = 2
    sites = [{"id": str(i), "name": f"site-{i}"} for i in range(8)]
    collector.pull_sites = lambda: sites

    lock = threading.Lock()
    active = 0
    peak = 0

    def poll(_site):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1

    collector._poll_site = poll
    collector.poll_summaries()

    assert peak == 2
    assert active == 0
