"""
RecapAI - Proje yöneticisi.
"""

import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from core.models import Project, Chapter, ImageData

logger = logging.getLogger(__name__)

PROJECTS_ROOT = Path("projects")
PROJECT_FILE = "project.json"
SUB_DIRS = ["images", "audio", "output", "thumbnails"]


def _now() -> str:
    """ISO 8601 formatında şu anki zamanı döndürür."""
    return datetime.now().isoformat(timespec="seconds")


def _safe_name(name: str) -> str:
    """Dosya sistemi için güvenli klasör adı üretir."""
    import re
    return re.sub(r"[^\w\-]", "_", name.strip())


# ── CRUD ───────────────────────────────────────────────────────────────────────

def create_project(name: str, series_type: str = "manga") -> Project:
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

    project_id = uuid.uuid4().hex[:8]
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
    logger.info("Proje oluşturuldu: %s [%s] (%s)", name, series_type, project_dir)
    return project


def load_project(path: str | Path) -> Project:
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
        logger.debug("Proje yüklendi: %s", project.name)
        return project
    except (json.JSONDecodeError, KeyError) as exc:
        raise ValueError(f"Proje dosyası bozuk: {exc}") from exc


def save_project(project: Project) -> None:
    """Projeyi uygun klasöre kaydeder."""
    project_dir = _find_project_dir(project.id)
    if project_dir is None:
        logger.error("Proje klasörü bulunamadı: %s", project.id)
        return
    project.updated_at = _now()
    _write_project_json(project_dir, project)
    logger.debug("Proje kaydedildi: %s", project.name)


def delete_project(project_id: str) -> None:
    """Proje klasörünü ve tüm içeriğini siler."""
    project_dir = _find_project_dir(project_id)
    if project_dir is None:
        raise FileNotFoundError(f"Silinecek proje bulunamadı: {project_id}")
    shutil.rmtree(project_dir)
    logger.info("Proje silindi: %s", project_dir.name)


def list_projects() -> List[Dict]:
    """
    projects/ altındaki tüm projelerin özet bilgilerini döndürür.

    Returns:
        [{"id", "name", "created_at", "updated_at", "chapter_count", "path"}, ...]
    """
    PROJECTS_ROOT.mkdir(exist_ok=True)
    result: List[Dict] = []
    for item in sorted(PROJECTS_ROOT.iterdir()):
        if not item.is_dir():
            continue
        json_path = item / PROJECT_FILE
        if not json_path.exists():
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            result.append({
                "id": data.get("id", ""),
                "name": data.get("name", item.name),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "chapter_count": len(data.get("chapters", [])),
                "series_type": data.get("series_type", "manga"),
                "path": str(item),
            })
        except Exception as exc:
            logger.warning("Proje okunamadı (%s): %s", item.name, exc)
    return result


# ── Chapter CRUD ───────────────────────────────────────────────────────────────

def add_chapter(project: Project, name: str, images: List[ImageData]) -> Chapter:
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
    save_project(project)
    logger.info("Bölüm eklendi: %s (%d görsel)", name, len(images))
    return chapter


def delete_chapter(project: Project, chapter_id: str) -> None:
    """Projeden bölümü kaldırır ve kaydeder."""
    before = len(project.chapters)
    project.chapters = [c for c in project.chapters if c.id != chapter_id]
    if len(project.chapters) == before:
        raise ValueError(f"Bölüm bulunamadı: {chapter_id}")
    save_project(project)
    logger.info("Bölüm silindi: %s", chapter_id)


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_project_dir(project: Project) -> Optional[Path]:
    """Projenin klasör yolunu döndürür."""
    return _find_project_dir(project.id)


def _find_project_dir(project_id: str) -> Optional[Path]:
    """projects/ altında verilen id'ye sahip klasörü bulur."""
    PROJECTS_ROOT.mkdir(exist_ok=True)
    for item in PROJECTS_ROOT.iterdir():
        if not item.is_dir():
            continue
        json_path = item / PROJECT_FILE
        if not json_path.exists():
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if data.get("id") == project_id:
                return item
        except Exception:
            continue
    return None


def _write_project_json(project_dir: Path, project: Project) -> None:
    """project.json'u yazar."""
    json_path = project_dir / PROJECT_FILE
    json_path.write_text(
        json.dumps(project.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )