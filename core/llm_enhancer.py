"""Optional OpenAI-compatible structured suggestion layer.

The deterministic core never imports or calls this module. Suggestions are
always labelled unverified and must pass the normal compliance/evidence gate.
"""

import json
from typing import Any, Dict
from urllib.parse import urlparse

import requests

from core.config import settings
from core.schemas import ProductAnalysis


SUGGESTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "possible_audiences": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "possible_scenes": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "module_suggestions": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        "risk_notes": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
    },
    "required": ["possible_audiences", "possible_scenes", "module_suggestions", "risk_notes"],
}


class LLMEnhancer:
    @staticmethod
    def configured() -> bool:
        parsed = urlparse(settings.LLM_API_BASE_URL)
        return bool(settings.LLM_API_KEY and settings.LLM_MODEL and parsed.scheme in {"http", "https"})

    @staticmethod
    def _extract_responses(data: Dict[str, Any]) -> str:
        if isinstance(data.get("output_text"), str):
            return data["output_text"]
        for item in data.get("output", []):
            for content in item.get("content", []):
                if isinstance(content.get("text"), str):
                    return content["text"]
        raise ValueError("LLM Responses 返回中没有文本输出")

    @classmethod
    def suggest(cls, product: ProductAnalysis, instruction: str) -> Dict[str, Any]:
        if not cls.configured():
            raise RuntimeError("可选 LLM 增强层未配置；核心规则流程不受影响")

        facts = {
            "product_name": product.product_name,
            "confirmed_information": product.confirmed_information,
            "possible_information": product.possible_information,
            "risk_information": product.risk_information,
            "information_confidence": product.information_confidence,
        }
        developer = (
            "你是受约束的电商视频工作流建议器。只输出指定 JSON。不得把推测改写为事实，"
            "不得新增功效、规格、检测、医疗或收益承诺。所有新内容只能作为 possible 建议。"
        )
        user_text = f"用户指令：{instruction}\n商品档案：{json.dumps(facts, ensure_ascii=False)}"
        base = settings.LLM_API_BASE_URL.rstrip("/")
        headers = {"Authorization": f"Bearer {settings.LLM_API_KEY}", "Content-Type": "application/json"}

        if settings.LLM_API_STYLE == "chat_completions":
            url = f"{base}/chat/completions"
            payload = {
                "model": settings.LLM_MODEL,
                "messages": [
                    {"role": "developer", "content": developer},
                    {"role": "user", "content": user_text},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "product_suggestions", "strict": True, "schema": SUGGESTION_SCHEMA},
                },
            }
            response = requests.post(url, headers=headers, json=payload, timeout=45)
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
        else:
            url = f"{base}/responses"
            payload = {
                "model": settings.LLM_MODEL,
                "instructions": developer,
                "input": user_text,
                "store": False,
                "text": {
                    "format": {"type": "json_schema", "name": "product_suggestions", "strict": True, "schema": SUGGESTION_SCHEMA}
                },
            }
            response = requests.post(url, headers=headers, json=payload, timeout=45)
            response.raise_for_status()
            text = cls._extract_responses(response.json())

        parsed = json.loads(text)
        return {
            "source": "optional_llm",
            "verified": False,
            "may_be_used_as_confirmed_information": False,
            "suggestions": parsed,
        }
