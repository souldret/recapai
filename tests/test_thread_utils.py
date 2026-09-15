"""QThread yaşam döngüsü: abort sonrası referans tutulur."""

from ui.workers.thread_utils import abort_worker, prune_live_workers, remember_worker, start_worker


class _FakeThread:
    def __init__(self, running: bool = True) -> None:
        self._running = running
        self.cancelled = False

    def isRunning(self) -> bool:
        return self._running

    def cancel(self) -> None:
        self.cancelled = True

    def start(self) -> None:
        self._running = True


class _Owner:
    def __init__(self) -> None:
        self._live_workers = []


def test_abort_keeps_running_worker():
    owner = _Owner()
    worker = _FakeThread(running=True)
    abort_worker(owner, worker)
    assert worker.cancelled is True
    assert worker in owner._live_workers


def test_abort_drops_finished_worker():
    owner = _Owner()
    worker = _FakeThread(running=False)
    abort_worker(owner, worker)
    assert worker not in owner._live_workers


def test_prune_removes_stopped():
    owner = _Owner()
    live = _FakeThread(running=True)
    dead = _FakeThread(running=False)
    remember_worker(owner, live)
    remember_worker(owner, dead)
    prune_live_workers(owner)
    assert live in owner._live_workers
    assert dead not in owner._live_workers


def test_start_worker_remembers():
    owner = _Owner()
    worker = _FakeThread(running=False)
    start_worker(owner, worker)
    assert worker in owner._live_workers
    assert worker.isRunning() is True
