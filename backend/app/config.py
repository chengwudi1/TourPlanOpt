from __future__ import annotations

import time
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"

PROBE_ORIGIN = (116.45925, 39.910031)
PROBE_DESTINATION = (116.587922, 39.928548)

# Lives here rather than in app.main so route modules can report uptime without
# importing app.main, which imports the routers.
START_TIME = time.monotonic()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "TourPlanOpt"
    version: str = "0.1.0"
    db_path: Path = DATA_DIR / "tourplan.db"

    # --- Amap: Web服务 key (backend REST). Never sent to the browser. ---
    amap_web_key: str = ""
    # --- Amap: Web端(JS API) key + security code (frontend map) ---
    amap_js_key: str = ""
    amap_js_scode: str = ""
    amap_jsapi_version: str = "2.0"

    amap_base_url: str = "https://restapi.amap.com"
    # Self-imposed ceiling, deliberately well under the account cap.
    amap_qps_limit: int = 3
    amap_timeout_s: float = 8.0

    # WebSocket hygiene
    ws_max_payload_bytes: int = 64 * 1024
    ws_silence_timeout_s: float = 60.0
    ws_rate_limit_messages: int = 30
    ws_rate_limit_window_s: float = 10.0
    ws_rate_limit_frames: int = 200

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # A1: when frontend/dist exists it is served from this port (single-origin deploy).
    frontend_dist: Path = FRONTEND_DIST


settings = Settings()
