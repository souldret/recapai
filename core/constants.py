"""
RecapAI - Merkezi sabitler ve yollar.
"""

from pathlib import Path

# Base directory for the project
BASE_DIR = Path(__file__).resolve().parent.parent

# Paths
CONFIG_DIR = BASE_DIR / "config"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
THEME_PATH = CONFIG_DIR / "theme.qss"
MODELS_PATH = CONFIG_DIR / "models.json"

LOGS_DIR = BASE_DIR / "logs"
APP_LOG_PATH = LOGS_DIR / "app.log"
ERROR_LOG_PATH = LOGS_DIR / "error.log"

PROJECTS_DIR = BASE_DIR / "projects"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
