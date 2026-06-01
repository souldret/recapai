"""
RecapAI - Uygulama bağlamı (Dependency Injection).
"""

from dataclasses import dataclass
from typing import Any

@dataclass
class AppContext:
    """
    Tüm uygulamaya enjekte edilecek ortak bağımlılıklar.
    """
    settings_manager: Any
    app_state: Any
    open_router_client: Any
