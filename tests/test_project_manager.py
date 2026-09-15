"""
RecapAI - ProjectManager testleri.
Çalıştırma: pytest tests/test_project_manager.py -v
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

# Gerçek PROJECTS_ROOT'u test için geçici dizine yönlendir
import core.project_manager as pm_mod


@pytest.fixture(autouse=True)
def temp_projects_dir(tmp_path, monkeypatch):
    """Her test için izole geçici projects/ dizini kullanır."""
    test_root = tmp_path / "projects"
    test_root.mkdir()
    monkeypatch.setattr(pm_mod, "PROJECTS_ROOT", test_root)
    # ProjectManager singleton'ını sıfırla
    pm_mod.ProjectManager._instance = None
    yield test_root
    pm_mod.ProjectManager._instance = None


# ── create_project ─────────────────────────────────────────────────────────────

class TestCreateProject:
    def test_creates_project_json(self, temp_projects_dir):
        p = pm_mod.create_project("Test Serisi", "manga")
        assert p.id
        assert p.name == "Test Serisi"
        assert p.series_type == "manga"
        # Disk üzerinde project.json var mı?
        dirs = list(temp_projects_dir.iterdir())
        assert len(dirs) == 1
        assert (dirs[0] / "project.json").exists()

    def test_creates_sub_dirs(self, temp_projects_dir):
        pm_mod.create_project("Alt Klasörler", "webtoon")
        proj_dir = list(temp_projects_dir.iterdir())[0]
        for sub in ["images", "audio", "output", "thumbnails"]:
            assert (proj_dir / sub).is_dir(), f"Alt klasör eksik: {sub}"

    def test_short_name_raises(self, temp_projects_dir):
        with pytest.raises(ValueError, match="en az 3 karakter"):
            pm_mod.create_project("ab")

    def test_invalid_series_type_raises(self, temp_projects_dir):
        with pytest.raises(ValueError, match="manga.*webtoon"):
            pm_mod.create_project("Geçerli İsim", "comic")

    def test_project_in_cache_after_create(self, temp_projects_dir):
        p = pm_mod.create_project("Önbellek Testi", "manga")
        manager = pm_mod.ProjectManager.instance()
        assert p.id in manager._cache


# ── load_project ───────────────────────────────────────────────────────────────

class TestLoadProject:
    def test_load_returns_same_project(self, temp_projects_dir):
        original = pm_mod.create_project("Yükle Testi", "manga")
        # Klasör yolunu bul
        manager = pm_mod.ProjectManager.instance()
        proj_dir = manager.find_project_dir(original.id)
        loaded = pm_mod.load_project(proj_dir)
        assert loaded.id == original.id
        assert loaded.name == original.name

    def test_load_missing_raises(self, temp_projects_dir):
        with pytest.raises(FileNotFoundError):
            pm_mod.load_project(temp_projects_dir / "yok_olan_klasor")


# ── save_project ───────────────────────────────────────────────────────────────

class TestSaveProject:
    def test_save_updates_json(self, temp_projects_dir):
        p = pm_mod.create_project("Kayıt Testi", "manga")
        p.name = "Değiştirilmiş İsim"
        pm_mod.save_project(p)

        manager = pm_mod.ProjectManager.instance()
        proj_dir = manager.find_project_dir(p.id)
        data = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
        assert data["name"] == "Değiştirilmiş İsim"

    def test_save_updates_updated_at(self, temp_projects_dir):
        from datetime import datetime
        p = pm_mod.create_project("Zaman Damgası", "webtoon")
        original_ts = datetime.fromisoformat(p.updated_at)
        import time; time.sleep(1.1)
        pm_mod.save_project(p)
        assert datetime.fromisoformat(p.updated_at) > original_ts

    def test_save_unknown_id_raises(self, temp_projects_dir):
        from core.models import Project
        p = Project(id="deadbeef", name="Yok", created_at="t", updated_at="t")
        with pytest.raises(FileNotFoundError, match="bulunamadı"):
            pm_mod.save_project(p)

    def test_save_is_atomic(self, temp_projects_dir):
        p = pm_mod.create_project("Atomik Kayit", "manga")
        manager = pm_mod.ProjectManager.instance()
        proj_dir = manager.find_project_dir(p.id)
        p.name = "Yeni Isim"
        pm_mod.save_project(p)
        assert not (proj_dir / "project.json.tmp").exists()
        data = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
        assert data["name"] == "Yeni Isim"


# ── delete_project ─────────────────────────────────────────────────────────────

class TestDeleteProject:
    def test_delete_removes_dir(self, temp_projects_dir):
        p = pm_mod.create_project("Silinecek", "manga")
        manager = pm_mod.ProjectManager.instance()
        proj_dir = manager.find_project_dir(p.id)
        assert proj_dir.exists()
        pm_mod.delete_project(p.id)
        assert not proj_dir.exists()

    def test_delete_removes_from_cache(self, temp_projects_dir):
        p = pm_mod.create_project("Önbellekten Sil", "manga")
        pm_mod.delete_project(p.id)
        manager = pm_mod.ProjectManager.instance()
        assert p.id not in manager._cache

    def test_delete_missing_raises(self, temp_projects_dir):
        with pytest.raises(FileNotFoundError):
            pm_mod.delete_project("olmayan_id")


# ── list_projects ──────────────────────────────────────────────────────────────

class TestListProjects:
    def test_lists_all_projects(self, temp_projects_dir):
        pm_mod.create_project("Proje A", "manga")
        pm_mod.create_project("Proje B", "webtoon")
        items = pm_mod.list_projects()
        assert len(items) == 2
        names = {i["name"] for i in items}
        assert "Proje A" in names
        assert "Proje B" in names
        for item in items:
            assert "image_count" in item
            assert "duration_sec" in item
            assert item["image_count"] == 0
            assert item["duration_sec"] == 0.0

    def test_empty_directory(self, temp_projects_dir):
        assert pm_mod.list_projects() == []

    def test_warms_cache(self, temp_projects_dir):
        pm_mod.create_project("Önbellek Isıt", "manga")
        # Önbelleği sıfırla
        manager = pm_mod.ProjectManager.instance()
        manager.invalidate_cache()
        assert not manager._cache_loaded
        pm_mod.list_projects()
        assert manager._cache_loaded


# ── find_project_dir / cache ───────────────────────────────────────────────────

class TestFindProjectDir:
    def test_finds_existing_project(self, temp_projects_dir):
        p = pm_mod.create_project("Bul Testi", "manga")
        manager = pm_mod.ProjectManager.instance()
        result = manager.find_project_dir(p.id)
        assert result is not None
        assert result.is_dir()

    def test_returns_none_for_missing(self, temp_projects_dir):
        manager = pm_mod.ProjectManager.instance()
        result = manager.find_project_dir("00000000")
        assert result is None

    def test_cache_hit_avoids_disk_rescan(self, temp_projects_dir):
        p = pm_mod.create_project("Önbellek Hit", "manga")
        manager = pm_mod.ProjectManager.instance()
        # Cache ısınmış olmalı
        assert p.id in manager._cache
        # Disk taraması olmadan bulunmalı (warm_cache çağrılmaz)
        manager._cache_loaded = True
        result = manager.find_project_dir(p.id)
        assert result is not None