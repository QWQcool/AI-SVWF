"""
AI-SVWF 全局配置管理模块
支持从 .env 自动加载，并支持运行时动态修改单价、Mock开关等参数
"""

import os
from pathlib import Path
from typing import List, Literal
from dotenv import load_dotenv

# 加载 .env
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    # 基础服务
    # Local-first secure defaults. Cloud deployment must opt in explicitly.
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    ALLOWED_ORIGINS: List[str] = [
        item.strip()
        for item in os.getenv(
            "ALLOWED_ORIGINS",
            "http://127.0.0.1:8000,http://localhost:8000",
        ).split(",")
        if item.strip()
    ]
    BASE_DIR: Path = BASE_DIR

    # 运行模式
    MOCK_MODE: bool = os.getenv("MOCK_MODE", "true").lower() == "true"

    # 模型服务商与接入点配置 (Seedance / 即梦 / 可灵 / LLM)
    MODEL_PROVIDER: str = os.getenv("MODEL_PROVIDER", "mock")
    ARK_API_BASE_URL: str = os.getenv("ARK_API_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    ARK_API_KEY: str = os.getenv("ARK_API_KEY", "")
    VISION_MODEL: str = os.getenv("VISION_MODEL", "glm-5-3-flash-260828")
    IMAGE_MODEL_PRIMARY: str = os.getenv("IMAGE_MODEL_PRIMARY", "doubao-seedream-5-0-260128")
    IMAGE_MODEL_FALLBACK: str = os.getenv("IMAGE_MODEL_FALLBACK", "doubao-seedream-4-5-251128")
    VIDEO_MODEL: str = os.getenv("VIDEO_MODEL", "doubao-seedance-2-0-260128")
    VIDEO_GENERATE_AUDIO: bool = os.getenv("VIDEO_GENERATE_AUDIO", "false").lower() == "true"
    # No guessed vendor URL: fill this only from the supplier's actual API docs.
    JIMENG_API_BASE_URL: str = os.getenv("JIMENG_API_BASE_URL", "")
    JIMENG_API_KEY: str = os.getenv("JIMENG_API_KEY", "")
    JIMENG_API_SECRET: str = os.getenv("JIMENG_API_SECRET", "")
    JIMENG_DEFAULT_MODEL: str = os.getenv("JIMENG_DEFAULT_MODEL", "doubao-seedance-2-0-260128")

    # 火山引擎方舟 (Seedance 2.0 Ark)
    SEEDANCE_ARK_API_KEY: str = os.getenv("SEEDANCE_ARK_API_KEY", "")
    SEEDANCE_ENDPOINT_ID: str = os.getenv("SEEDANCE_ENDPOINT_ID", "")

    # 快手可灵 (Kling)
    KLING_API_KEY: str = os.getenv("KLING_API_KEY", "")

    # 可选 LLM 辅助层；核心 Prompt Builder 不依赖它。
    # responses 优先，chat_completions 用于兼容现有 OpenAI 格式网关。
    LLM_API_BASE_URL: str = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-5-mini")
    LLM_API_STYLE: str = os.getenv("LLM_API_STYLE", "responses")

    # 动态计费 (Section 13/26 要求)
    BILLING_MODE: Literal["CNY", "POINTS"] = os.getenv("BILLING_MODE", "CNY")  # type: ignore
    COST_PER_SECOND_CNY: float = float(os.getenv("COST_PER_SECOND_CNY", "0.05"))
    CREDITS_PER_SECOND: float = float(os.getenv("CREDITS_PER_SECOND", "4.0"))

    # 飞书多维表格 (Bitable)
    FEISHU_SYNC_MODE: str = os.getenv("FEISHU_SYNC_MODE", "dual")  # dual / local / cloud
    FEISHU_APP_ID: str = os.getenv("FEISHU_APP_ID", "")
    FEISHU_APP_SECRET: str = os.getenv("FEISHU_APP_SECRET", "")
    FEISHU_BITABLE_APP_TOKEN: str = os.getenv("FEISHU_BITABLE_APP_TOKEN", "")
    FEISHU_TABLE_PRODUCTS: str = os.getenv("FEISHU_TABLE_PRODUCTS", "")
    FEISHU_TABLE_TASKS: str = os.getenv("FEISHU_TABLE_TASKS", "")
    FEISHU_TABLE_QA: str = os.getenv("FEISHU_TABLE_QA", "")
    FEISHU_TABLE_DELIVERY: str = os.getenv("FEISHU_TABLE_DELIVERY", "")

    # 存储目录
    OUTPUT_DIR: Path = BASE_DIR / os.getenv("OUTPUT_DIR", "outputs")
    PRESET_DIR: Path = BASE_DIR / os.getenv("PRESET_DIR", "presets")
    DATABASE_PATH: Path = BASE_DIR / os.getenv("DATABASE_PATH", "data/ai_svwf.sqlite3")
    ASSET_DIR: Path = BASE_DIR / os.getenv("ASSET_DIR", "data/assets")
    MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "10"))
    MAX_ASSETS_PER_PRODUCT: int = int(os.getenv("MAX_ASSETS_PER_PRODUCT", "6"))
    MAX_UPLOAD_REQUEST_MB: int = int(os.getenv("MAX_UPLOAD_REQUEST_MB", "65"))
    MAX_REAL_VISION_TASKS_PER_DAY: int = int(os.getenv("MAX_REAL_VISION_TASKS_PER_DAY", "20"))
    MAX_REAL_IMAGE_TASKS_PER_DAY: int = int(os.getenv("MAX_REAL_IMAGE_TASKS_PER_DAY", "6"))
    MAX_REAL_VIDEO_TASKS_PER_DAY: int = int(os.getenv("MAX_REAL_VIDEO_TASKS_PER_DAY", "12"))

    @classmethod
    def calculate_cost(cls, duration_seconds: int = 5) -> dict:
        """动态计算单次视频生成预估成本"""
        cny = round(duration_seconds * cls.COST_PER_SECOND_CNY, 4)
        credits = round(duration_seconds * cls.CREDITS_PER_SECOND, 1)
        return {
            "duration_seconds": duration_seconds,
            "cost_cny": cny,
            "credits": credits,
            "display": f"¥{cny:.2f} ({credits}算力点)"
            if cls.BILLING_MODE == "CNY"
            else f"{credits}算力点 (折合¥{cny:.2f})",
        }


# 确保输出和预置目录存在
Settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
Settings.PRESET_DIR.mkdir(parents=True, exist_ok=True)
Settings.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
Settings.ASSET_DIR.mkdir(parents=True, exist_ok=True)

settings = Settings()
