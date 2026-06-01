"""
RecapAI - Veri modelleri (dataclass'lar).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any


# ── ImageData ──────────────────────────────────────────────────────────────────

@dataclass
class ImageData:
    """Tek bir görsel dosyasını temsil eder."""
    path: str
    filename: str
    order: int
    thumbnail_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ImageData":
        return cls(
            path=data["path"],
            filename=data["filename"],
            order=data["order"],
            thumbnail_path=data.get("thumbnail_path"),
        )


# ── SegmentData ────────────────────────────────────────────────────────────────

@dataclass
class SegmentData:
    """Bir görsel kareye karşılık gelen script + ses segmenti."""
    image_index: int
    text: str = ""
    audio_path: Optional[str] = None
    duration: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SegmentData":
        return cls(
            image_index=data["image_index"],
            text=data.get("text", ""),
            audio_path=data.get("audio_path"),
            duration=data.get("duration", 0.0),
        )


# ── Chapter ────────────────────────────────────────────────────────────────────

# Chapter durum sabitleri
CHAPTER_STATUS_RAW       = "ham"        # Görsel yüklendi, işlem yok
CHAPTER_STATUS_DETECTED  = "detected"  # Panel tespiti yapıldı
CHAPTER_STATUS_COMPLETED = "completed" # Tamamlandı


@dataclass
class PanelInfo:
    """Tek bir panel hakkında metadata: kaynak sayfa + koordinat + sıra."""
    page_filename: str
    page_index: int
    x: int
    y: int
    w: int
    h: int
    order: int   # okuma sırası (0-tabanlı)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PanelInfo":
        return cls(**data)


@dataclass
class Chapter:
    """Bir manga/manhwa bölümünü temsil eder."""
    id: str
    name: str
    images: List[ImageData] = field(default_factory=list)
    analysis_data: Dict[str, Any] = field(default_factory=dict)
    segments: List[SegmentData] = field(default_factory=list)
    # v2 alanları
    status: str = CHAPTER_STATUS_RAW          # ham / detected / completed
    panel_data: Dict[str, Any] = field(default_factory=dict)
    # panel_data örnek yapısı:
    # {
    #   "<image_filename>": [{"x":0,"y":0,"w":100,"h":100,"order":0}, ...],
    #   ...
    # }

    @property
    def image_count(self) -> int:
        return len(self.images)

    @property
    def total_panels(self) -> int:
        """Tüm sayfalardaki toplam panel sayısı."""
        return sum(len(v) for v in self.panel_data.values())

    def get_panels_for_image(self, filename: str) -> List[Dict[str, Any]]:
        """Belirtilen görsel dosyasının panel listesini döndürür."""
        return self.panel_data.get(filename, [])

    def set_panels_for_image(
        self,
        filename: str,
        boxes: List[tuple],   # [(x,y,w,h), ...]
        reading_order: str = "ltr",
    ) -> None:
        """Panel listesini günceller ve status'u 'detected' yapar."""
        self.panel_data[filename] = [
            {"x": x, "y": y, "w": w, "h": h, "order": i}
            for i, (x, y, w, h) in enumerate(boxes)
        ]
        if self.status == CHAPTER_STATUS_RAW:
            self.status = CHAPTER_STATUS_DETECTED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "images": [img.to_dict() for img in self.images],
            "analysis_data": self.analysis_data,
            "segments": [seg.to_dict() for seg in self.segments],
            "status": self.status,
            "panel_data": self.panel_data,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Chapter":
        return cls(
            id=data["id"],
            name=data["name"],
            images=[ImageData.from_dict(i) for i in data.get("images", [])],
            analysis_data=data.get("analysis_data", {}),
            segments=[SegmentData.from_dict(s) for s in data.get("segments", [])],
            status=data.get("status", CHAPTER_STATUS_RAW),
            panel_data=data.get("panel_data", {}),
        )


# ── Project ────────────────────────────────────────────────────────────────────

@dataclass
class Project:
    """Bir recap projesini temsil eder."""
    id: str
    name: str
    created_at: str
    updated_at: str
    series_type: str = "manga"          # "manga" veya "webtoon"
    chapters: List[Chapter] = field(default_factory=list)
    settings: Dict[str, Any] = field(default_factory=dict)

    @property
    def chapter_count(self) -> int:
        return len(self.chapters)

    @property
    def total_images(self) -> int:
        return sum(ch.image_count for ch in self.chapters)

    def get_chapter(self, chapter_id: str) -> Optional[Chapter]:
        """ID ile bölüm getirir."""
        return next((c for c in self.chapters if c.id == chapter_id), None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "series_type": self.series_type,
            "chapters": [ch.to_dict() for ch in self.chapters],
            "settings": self.settings,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Project":
        return cls(
            id=data["id"],
            name=data["name"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            series_type=data.get("series_type", "manga"),
            chapters=[Chapter.from_dict(c) for c in data.get("chapters", [])],
            settings=data.get("settings", {}),
        )