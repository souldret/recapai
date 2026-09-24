"""
RecapAI - Proje yöneticisi.
ProjectManager sınıfı: id→path önbelleği ile O(1) arama.
Modül-seviyesi fonksiyonlar geriye dönük uyumluluk için korunuyor.
"""

import json
import logging
import os
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from core.models import Project, Chapter, ImageData

logger = logging.getLogger(__name__)

PROJECTS_ROOT = Path(__file__).resolve().parent.parent / "projects"
PROJECT_FILE  = "project.json"
SUB_DIRS      = ["images", "audio", "output", "thumbnails"]


def _now() -> str:
    """ISO 8601 formatında şu anki zamanı döndürür."""
    return datetime.now().isoformat(timespec="seconds")


def _safe_name(name: str) -> str:
    """Dosya sistemi için güvenli klasör adı üretir."""
    import re
    return re.sub(r"[^\w\-]", "_", name.strip())


def _write_project_json(project_dir: Path, project: Project) -> None:
    """project.json'u atomik yazar (tmp + replace). Yarı yazılmış dosya bırakmaz."""
    json_path = project_dir / PROJECT_FILE
    payload = json.dumps(project.to_dict(), indent=2, ensure_ascii=False)
    tmp_path = json_path.with_name(
        f"{json_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(json_path)


# ── ProjectManager (önbellekli) ────────────────────────────────────────────────

class ProjectManager:
    """
    Proje CRUD işlemlerini yöneten singleton sınıf.
    id → Path önbelleği ile tekrarlayan disk taramalarını önler.
    """

    _instance: Optional["ProjectManager"] = None

    def __init__(self) -> None:
        self._cache: Dict[str, Path] = {}   # project_id → proje klasörü
        self._cache_loaded = False
        ProjectManager._instance = self

    @classmethod
    def instance(cls) -> "ProjectManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Önbellek yönetimi ──────────────────────────────────────────

    def _warm_cache(self) -> None:
        """Önbellek boşsa projects/ altını tarayarak doldurur."""
        if self._cache_loaded:
            return
        PROJECTS_ROOT.mkdir(exist_ok=True)
        for item in PROJECTS_ROOT.iterdir():
            if not item.is_dir():
                continue
            json_path = item / PROJECT_FILE
            if not json_path.exists():
                continue
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                pid = data.get("id")
                if pid:
                    self._cache[pid] = item
            except Exception as exc:
                logger.warning("Önbellek ısıtma hatası (%s): %s", item.name, exc)
        self._cache_loaded = True
        logger.debug("ProjectManager önbelleği ısıtıldı: %d proje", len(self._cache))

    def invalidate_cache(self) -> None:
        """Önbelleği temizler; bir sonraki işlemde yeniden taranır."""
        self._cache.clear()
        self._cache_loaded = False

    # ── CRUD ──────────────────────────────────────────────────────

    def create_project(self, name: str, series_type: str = "manga") -> Project:
        """
        Yeni proje oluşturur; klasör yapısını ve project.json'u yazar.

        Args:
            name: Proje adı (min 3 karakter).
            series_type: Seri türü — "manga" veya "webtoon".

        Returns:
            Oluşturulan Project nesnesi.

        Raises:
            ValueError: Geçersiz isim veya tür.
            FileExistsError: Aynı isimde proje zaten var.
        """
        name = name.strip()
        if len(name) < 3:
            raise ValueError("Proje adı en az 3 karakter olmalıdır.")
        if series_type not in ("manga", "webtoon"):
            raise ValueError("Seri türü 'manga' veya 'webtoon' olmalıdır.")

        project_id  = uuid.uuid4().hex[:8]
        folder_name = f"{_safe_name(name)}_{project_id}"
        project_dir = PROJECTS_ROOT / folder_name

        if project_dir.exists():
            raise FileExistsError(f"Proje klasörü zaten mevcut: {project_dir}")

        project_dir.mkdir(parents=True)
        for sub in SUB_DIRS:
            (project_dir / sub).mkdir()

        project = Project(
            id=project_id,
            name=name,
            created_at=_now(),
            updated_at=_now(),
            series_type=series_type,
        )
        _write_project_json(project_dir, project)
        self._warm_cache()
        self._cache[project_id] = project_dir
        logger.info("Proje oluşturuldu: %s [%s] (%s)", name, series_type, project_dir)
        return project

    def load_project(self, path: "str | Path") -> Project:
        """
        Belirtilen yoldan project.json okuyarak Project nesnesi döndürür.

        Raises:
            FileNotFoundError: Dosya bulunamadı.
            ValueError: JSON ayrıştırma hatası.
        """
        json_path = Path(path) / PROJECT_FILE
        if not json_path.exists():
            raise FileNotFoundError(f"project.json bulunamadı: {json_path}")
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            project = Project.from_dict(data)
            # Önbelleğe ekle
            self._cache[project.id] = Path(path)
            logger.debug("Proje yüklendi: %s", project.name)
            return project
        except (json.JSONDecodeError, KeyError) as exc:
            raise ValueError(f"Proje dosyası bozuk: {exc}") from exc

    def save_project(self, project: Project) -> None:
        """Projeyi uygun klasöre kaydeder.

        Raises:
            FileNotFoundError: Proje klasörü bulunamazsa.
        """
        project_dir = self.find_project_dir(project.id)
        if project_dir is None:
            raise FileNotFoundError(f"Proje klasörü bulunamadı: {project.id}")
        project.updated_at = _now()
        _write_project_json(project_dir, project)
        logger.debug("Proje kaydedildi: %s", project.name)

    def delete_project(self, project_id: str) -> None:
        """Proje klasörünü ve tüm içeriğini siler."""
        project_dir = self.find_project_dir(project_id)
        if project_dir is None:
            raise FileNotFoundError(f"Silinecek proje bulunamadı: {project_id}")
        shutil.rmtree(project_dir)
        self._cache.pop(project_id, None)
        logger.info("Proje silindi: %s", project_dir.name)

    def list_projects(self) -> List[Dict]:
        """
        projects/ altındaki tüm projelerin özet bilgilerini döndürür.
        Tam disk taraması yapar; önbelleği sıfırlayıp yeniden inşa eder
        böylece silinmiş projelere ait stale girişler temizlenir.

        Returns:
            [{"id", "name", "created_at", "updated_at", "chapter_count", "path"}, ...]
        """
        PROJECTS_ROOT.mkdir(exist_ok=True)
        # Tam tarama yapıyoruz — önceki stale girişleri temizle
        self._cache.clear()
        result: List[Dict] = []
        for item in sorted(PROJECTS_ROOT.iterdir()):
            if not item.is_dir():
                continue
            json_path = item / PROJECT_FILE
            if not json_path.exists():
                continue
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                pid = data.get("id", "")
                if pid:
                    self._cache[pid] = item  # güncel önbelleği doldur
                chapters = data.get("chapters", []) or []
                image_count = 0
                duration_sec = 0.0
                for ch in chapters:
                    image_count += len(ch.get("images") or [])
                    for seg in ch.get("segments") or []:
                        try:
                            duration_sec += float(seg.get("duration") or 0.0)
                        except (TypeError, ValueError):
                            continue
                result.append({
                    "id":            pid,
                    "name":          data.get("name", item.name),
                    "created_at":    data.get("created_at", ""),
                    "updated_at":    data.get("updated_at", ""),
                    "chapter_count": len(chapters),
                    "image_count":   image_count,
                    "duration_sec":  duration_sec,
                    "series_type":   data.get("series_type", "manga"),
                    "path":          str(item),
                })
            except Exception as exc:
                logger.warning("Proje okunamadı (%s): %s", item.name, exc)
        self._cache_loaded = True
        return result

    # ── Chapter CRUD ──────────────────────────────────────────────

    def add_chapter(
        self, project: Project, name: str, images: List[ImageData]
    ) -> Chapter:
        """
        Projeye yeni bölüm ekler ve kaydeder.

        Args:
            project: Hedef proje.
            name: Bölüm adı.
            images: ImageData listesi.

        Returns:
            Oluşturulan Chapter.
        """
        chapter = Chapter(
            id=uuid.uuid4().hex[:8],
            name=name.strip(),
            images=images,
        )
        project.chapters.append(chapter)
        self.save_project(project)
        logger.info("Bölüm eklendi: %s (%d görsel)", name, len(images))
        return chapter

    def delete_chapter(self, project: Project, chapter_id: str) -> None:
        """Projeden bölümü kaldırır ve kaydeder."""
        before = len(project.chapters)
        project.chapters = [c for c in project.chapters if c.id != chapter_id]
        if len(project.chapters) == before:
            raise ValueError(f"Bölüm bulunamadı: {chapter_id}")
        self.save_project(project)
        logger.info("Bölüm silindi: %s", chapter_id)

    # ── Yardımcılar ───────────────────────────────────────────────

    def get_project_dir(self, project: Project) -> Optional[Path]:
        """Projenin klasör yolunu döndürür."""
        return self.find_project_dir(project.id)

    def find_project_dir(self, project_id: str) -> Optional[Path]:
        """
        Önbellekten O(1) ile proje klasörünü bulur.
        Önbellek soğuksa diskten ısıtır.
        """
        self._warm_cache()
        path = self._cache.get(project_id)
        if path and path.exists():
            return path
        # Önbellekte yoksa veya klasör silinmişse önbelleği yenile ve tekrar dene
        self.invalidate_cache()
        self._warm_cache()
        path = self._cache.get(project_id)
        return path if (path and path.exists()) else None


# ── Modül-seviyesi yardımcılar (geriye dönük uyumluluk) ───────────────────────

def _get_pm() -> ProjectManager:
    return ProjectManager.instance()


def create_project(name: str, series_type: str = "manga") -> Project:
    return _get_pm().create_project(name, series_type)


def load_project(path: "str | Path") -> Project:
    return _get_pm().load_project(path)


def save_project(project: Project) -> None:
    _get_pm().save_project(project)


def persist_project(project: Project) -> bool:
    """Ara kayıt. Klasör yoksa veya yazma hatasında analizi durdurmaz."""
    if project is None:
        return False
    try:
        save_project(project)
        return True
    except Exception as exc:
        logger.warning("Proje ara kaydı yazılamadı: %s", exc)
        return False


def delete_project(project_id: str) -> None:
    _get_pm().delete_project(project_id)


def list_projects() -> List[Dict]:
    return _get_pm().list_projects()


def add_chapter(project: Project, name: str, images: List[ImageData]) -> Chapter:
    return _get_pm().add_chapter(project, name, images)


def delete_chapter(project: Project, chapter_id: str) -> None:
    _get_pm().delete_chapter(project, chapter_id)


def get_project_dir(project: Project) -> Optional[Path]:
    return _get_pm().get_project_dir(project)


def _find_project_dir(project_id: str) -> Optional[Path]:
    """Geriye dönük uyumluluk — find_project_dir'e yönlendirir."""
    return _get_pm().find_project_dir(project_id)