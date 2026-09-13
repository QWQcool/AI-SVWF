"""
AI-SVWF 全局配置管理模块
支持从 .env 自动加载，并支持运行时动态修改单价、Mock开关等参数
"""

import os
from pathlib import Path
from typing import Literal
from dotenv import load_dotenv

# 加载 .env
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    # 基础服务
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"
    BASE_DIR: Path = BASE_DIR

    # 运行模式
    MOCK_MODE: bool = os.getenv("MOCK_MODE", "true").lower() == "true"

    # 模型服务商与接入点配置 (Seedance / 即梦 / 可灵 / LLM)
    MODEL_PROVIDER: str = os.getenv("MODEL_PROVIDER", "seedance")
    JIMENG_API_BASE_URL: str = os.getenv(
        "JIMENG_API_BASE_URL", "https://openspeech.bytedance.com/api/v1/video/generate"
    )
    JIMENG_API_KEY: str = os.getenv("JIMENG_API_KEY", "")
    JIMENG_API_SECRET: str = os.getenv("JIMENG_API_SECRET", "")
    JIMENG_DEFAULT_MODEL: str = os.getenv("JIMENG_DEFAULT_MODEL", "seedance-2.0-fast")

    # 火山引擎方舟 (Seedance 2.0 Ark)
    SEEDANCE_ARK_API_KEY: str = os.getenv("SEEDANCE_ARK_API_KEY", "")
    SEEDANCE_ENDPOINT_ID: str = os.getenv("SEEDANCE_ENDPOINT_ID", "")

    # 快手可灵 (Kling)
    KLING_API_KEY: str = os.getenv("KLING_API_KEY", "")

    # 可选 LLM 智能文案与卖点扩写 (兼容 OpenAI / DeepSeek / 豆包)
    LLM_API_BASE_URL: str = os.getenv("LLM_API_BASE_URL", "https://api.deepseek.com/v1")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")

    # 动态计费 (Section 13/26 要求)
    BILLING_MODE: Literal["CNY", "POINTS"] = os.getenv("BILLING_MODE", "CNY")  # type: ignore
    COST_PER_SECOND_CNY: float = float(os.getenv("COST_PER_SECOND_CNY", "0.05"))
    CREDITS_PER_SECOND: float = float(os.getenv("CREDITS_PER_SECOND", "4.0"))

    # 飞书多维表格 (Bitable)
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

settings = Settings()
