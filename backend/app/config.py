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

    # --- 对话式助手：OpenAI 兼容的 chat/completions ------------------------------------
    # 默认是智谱的免费模型 GLM-4-Flash。指向本地 Ollama 时（LLM_BASE_URL=
    # http://127.0.0.1:11434/v1）连密钥都不需要——localhost 视为免鉴权，零成本零外传。
    # 与 amap_web_key 同一条纪律：这把密钥只在服务端用，绝不出现在任何响应里。
    # 模型别选思考型：硅基流动实测 Qwen2.5-14B-Instruct 1–4 秒回，冷启一次 14 秒；而
    # Qwen3.5-4B/9B 这类带 reasoning 的同一句话 90 秒不返回，同步解析等不起。
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_api_key: str = ""
    llm_model: str = "glm-4-flash"
    llm_timeout_s: float = 20.0
    # 一次解析允许的最长用户输入。挡的是把整段聊天记录灌进 prompt，不是挡正常说话。
    assistant_max_chars: int = 600
    # 单条消息最多解析出几条指令，以及最多调用几次 POI 搜索（每次都是真配额）。
    assistant_max_actions: int = 20
    assistant_max_poi_lookups: int = 5

    # --- 语音输入：OpenAI 兼容的 /audio/transcriptions ---------------------------------
    # 浏览器自带的识别（Chrome Web Speech）没有可配置的服务端点，音频只能发到境外那台
    # 服务，网络到不了就永远报 network，用户端无从补救。这一条路把转写搬到后端：
    # ASR_BASE_URL 指向本地的 whisper 类服务（http://127.0.0.1:8100/v1，不出本机、零成本）
    # 或国内云的 OpenAI 兼容端点。留空即不启用，话筒退回浏览器自带识别。
    # 密钥与 amap_web_key / llm_api_key 同一条纪律：只在进程内用，绝不进响应。
    asr_base_url: str = ""
    asr_api_key: str = ""
    asr_model: str = "whisper-1"
    # 空串让服务端自己判语种；已知只说中文时填 zh 能少一批同音字。
    asr_language: str = ""
    # 25 秒不够：云端语音是排队实例，冷启一次实测 48–54 秒（热实例 2–7 秒）。卡在 25 秒
    # 会把「闲置之后的第一句」准时打成失败，而那恰好是用户第一次试着开口的哪一句。
    asr_timeout_s: float = 60.0
    # 一段话的天花板：60 秒 opus 约 60 KB，给到 2 MB 已经能挡下「误开十分钟录音」和恶意灌包。
    asr_max_bytes: int = 2 * 1024 * 1024
    asr_max_seconds: int = 90

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
