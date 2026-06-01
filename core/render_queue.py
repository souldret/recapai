"""
RecapAI - Render kuyruğu yönetimi.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class RenderStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class RenderJob:
    """Tek bir render işini temsil eder."""
    job_id: str
    chapter_id: str
    chapter_name: str
    settings: Dict[str, Any]
    output_path: str
    status: RenderStatus = RenderStatus.PENDING
    progress: int = 0
    eta: str = "—"
    error_message: str = ""
    output_file: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "chapter_id": self.chapter_id,
            "chapter_name": self.chapter_name,
            "output_path": self.output_path,
            "status": self.status.value,
            "progress": self.progress,
            "eta": self.eta,
            "error_message": self.error_message,
            "output_file": self.output_file,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class RenderQueue:
    """
    Birden fazla bölümü sırayla render etmek için kuyruk yöneticisi.
    Thread-safe tasarım.
    """

    def __init__(self) -> None:
        self._jobs: List[RenderJob] = []
        self._lock = threading.Lock()
        self._current_job: Optional[RenderJob] = None
        self._cancel_requested = threading.Event()

        # Callback'ler (UI thread'den bağlanır)
        self.on_job_started: Optional[Callable[[RenderJob], None]] = None
        self.on_job_progress: Optional[Callable[[RenderJob, int, str], None]] = None
        self.on_job_finished: Optional[Callable[[RenderJob], None]] = None
        self.on_job_error: Optional[Callable[[RenderJob, str], None]] = None
        self.on_queue_empty: Optional[Callable[[], None]] = None

    # ── Public API ────────────────────────────────────────────────

    def add(
        self,
        chapter,
        settings: Dict[str, Any],
        output_path: str,
    ) -> RenderJob:
        """
        Kuyruğa yeni bir render işi ekler.

        Args:
            chapter: Chapter nesnesi
            settings: Render ayarları sözlüğü
            output_path: Çıktı MP4 dosyasının tam yolu

        Returns:
            Oluşturulan RenderJob nesnesi
        """
        import uuid
        job_id = uuid.uuid4().hex[:8]
        job = RenderJob(
            job_id=job_id,
            chapter_id=chapter.id,
            chapter_name=chapter.name,
            settings=settings,
            output_path=output_path,
        )
        with self._lock:
            self._jobs.append(job)
        logger.info("Kuyruk: iş eklendi [%s] %s → %s", job_id, chapter.name, output_path)
        return job

    def process_next(self, chapter, composer_cls) -> Optional[RenderJob]:
        """
        Sıradaki PENDING işi çalıştırır (bloklayan çağrı).
        Bu metot genellikle RenderWorker'ın run() metodundan çağrılır.

        Args:
            chapter: Render edilecek Chapter nesnesi
            composer_cls: VideoComposer sınıfı (lazy import için)

        Returns:
            Tamamlanan RenderJob veya None
        """
        job = self._next_pending()
        if not job:
            return None

        self._current_job = job
        job.status = RenderStatus.RUNNING
        job.started_at = datetime.now().isoformat()
        self._cancel_requested.clear()

        if self.on_job_started:
            self.on_job_started(job)

        try:
            composer = composer_cls(job.settings)

            def progress_cb(percent: int, eta: str) -> None:
                job.progress = percent
                job.eta = eta
                if self.on_job_progress:
                    self.on_job_progress(job, percent, eta)

            def cancel_check() -> bool:
                return self._cancel_requested.is_set()

            output = composer.compose_chapter(
                chapter,
                job.output_path,
                progress_cb,
                cancel_check=cancel_check,
            )

            if self._cancel_requested.is_set():
                job.status = RenderStatus.CANCELLED
            else:
                job.status = RenderStatus.DONE
                job.output_file = output or job.output_path
                job.progress = 100

            job.finished_at = datetime.now().isoformat()
            if self.on_job_finished:
                self.on_job_finished(job)

        except Exception as exc:
            job.status = RenderStatus.ERROR
            job.error_message = str(exc)
            job.finished_at = datetime.now().isoformat()
            logger.error("Render hatası [%s]: %s", job.job_id, exc, exc_info=True)
            if self.on_job_error:
                self.on_job_error(job, str(exc))

        finally:
            self._current_job = None

        # Kuyruk bitti mi?
        if not self._has_pending() and self.on_queue_empty:
            self.on_queue_empty()

        return job

    def cancel_current(self) -> None:
        """Çalışmakta olan işi iptal etmek için sinyal gönderir."""
        self._cancel_requested.set()
        logger.info("Kuyruk: iptal sinyali gönderildi.")

    def clear(self) -> None:
        """Tüm PENDING işleri kaldırır."""
        with self._lock:
            self._jobs = [j for j in self._jobs if j.status != RenderStatus.PENDING]

    # ── Status ────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Kuyruk durum özetini döndürür."""
        with self._lock:
            pending = [j for j in self._jobs if j.status == RenderStatus.PENDING]
            running = [j for j in self._jobs if j.status == RenderStatus.RUNNING]
            done = [j for j in self._jobs if j.status == RenderStatus.DONE]
            error = [j for j in self._jobs if j.status == RenderStatus.ERROR]

        return {
            "total": len(self._jobs),
            "pending": len(pending),
            "running": len(running),
            "done": len(done),
            "error": len(error),
            "current": self._current_job.to_dict() if self._current_job else None,
            "jobs": [j.to_dict() for j in self._jobs],
        }

    def get_jobs(self) -> List[RenderJob]:
        """Tüm işlerin bir kopyasını döndürür."""
        with self._lock:
            return list(self._jobs)

    # ── Private ───────────────────────────────────────────────────

    def _next_pending(self) -> Optional[RenderJob]:
        with self._lock:
            for job in self._jobs:
                if job.status == RenderStatus.PENDING:
                    return job
        return None

    def _has_pending(self) -> bool:
        with self._lock:
            return any(j.status == RenderStatus.PENDING for j in self._jobs)


# Uygulama genelinde paylaşılan kuyruk örneği
_queue_instance: Optional[RenderQueue] = None


def get_render_queue() -> RenderQueue:
    """Singleton RenderQueue örneğini döndürür."""
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = RenderQueue()
    return _queue_instance