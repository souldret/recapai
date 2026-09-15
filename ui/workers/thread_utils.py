"""
RecapAI - QThread yaşam döngüsü.

Worker iptal edilince Python referansı düşünce Qt native crash verir
(QThread: Destroyed while thread is still running). Bu yardımcı, iptal
edilen thread'i parent + _live_workers listesinde tutar.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional


def prune_live_workers(owner: Any) -> None:
    bag = getattr(owner, "_live_workers", None)
    if not bag:
        return
    owner._live_workers = [w for w in bag if w is not None and getattr(w, "isRunning", lambda: False)()]


def remember_worker(owner: Any, worker: Any) -> None:
    if worker is None:
        return
    prune_live_workers(owner)
    bag = getattr(owner, "_live_workers", None)
    if bag is None:
        owner._live_workers = []
        bag = owner._live_workers
    if worker not in bag:
        bag.append(worker)


def abort_worker(owner: Any, worker: Any) -> None:
    """İptal et; çalışan thread'in son referansını düşürme."""
    if worker is None or not hasattr(worker, "isRunning"):
        return
    for method in ("cancel", "stop"):
        if hasattr(worker, method):
            try:
                getattr(worker, method)()
            except Exception:
                pass
    if worker.isRunning():
        remember_worker(owner, worker)


def start_worker(owner: Any, worker: Any) -> Any:
    """Parent varsa bağla, listeye al, start et."""
    if worker is None:
        return None
    remember_worker(owner, worker)
    worker.start()
    return worker


def collect_page_workers(page: Any) -> List[Any]:
    workers: List[Any] = []
    for attr in (
        "_worker", "_pipeline_worker", "_render_worker",
        "_preview_worker", "_regen_worker",
        "_test_worker", "_refresh_worker", "_model_test_worker",
        "_api_test_worker", "_kokoro_dl_worker", "_fix_worker",
    ):
        workers.append(getattr(page, attr, None))
    workers.extend(getattr(page, "_regen_workers", None) or [])
    workers.extend(getattr(page, "_live_workers", None) or [])
    for nested_name in ("manga_panel_editor", "webtoon_editor"):
        nested = getattr(page, nested_name, None)
        if nested is None:
            continue
        workers.extend(getattr(nested, "_live_workers", None) or [])
        for attr in ("_load_worker", "_detect_worker", "_batch_worker", "_stitch_worker"):
            workers.append(getattr(nested, attr, None))
    return workers


def stop_qthread(worker: Any, timeout_ms: int = 3000) -> None:
    if worker is None or not hasattr(worker, "isRunning"):
        return
    if not worker.isRunning():
        return
    for method in ("stop", "cancel"):
        if hasattr(worker, method):
            try:
                getattr(worker, method)()
            except Exception:
                pass
    if not worker.wait(timeout_ms):
        try:
            worker.terminate()
            worker.wait(1000)
        except Exception:
            pass


def shutdown_page(page: Any, timeout_ms: int = 3000) -> None:
    seen = set()
    for w in collect_page_workers(page):
        ident = id(w)
        if w is None or ident in seen:
            continue
        seen.add(ident)
        stop_qthread(w, timeout_ms=timeout_ms)


def iter_live(owner: Any) -> Iterable[Any]:
    prune_live_workers(owner)
    return list(getattr(owner, "_live_workers", None) or [])
