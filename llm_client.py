import logging
from typing import List, Optional

import requests

from config import (
    LLM_BASE_URL,
    LLM_API_KEY,
    LLM_MODEL,
    LLM_TEMPERATURA,
    LLM_TOP_P,
    LLM_MAX_TOKENS,
    LLM_THINKING
)

logger = logging.getLogger(__name__)


def _build_payload(messages: List[dict], model: Optional[str] = None) -> dict:
    payload = {
        "model": model or LLM_MODEL,
        "messages": messages,
        "temperature": LLM_TEMPERATURA,
        "top_p": LLM_TOP_P,
        "max_tokens": LLM_MAX_TOKENS
    }

    if LLM_THINKING:
        payload["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": True}
        }

    return payload


def chat_completion(messages: List[dict], model: Optional[str] = None) -> str:
    url = f"{LLM_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"}

    payload = _build_payload(messages, model=model)

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        content = message.get("content") or ""
        return content
    except Exception as exc:
        logger.error("LLM fallo: %s", exc)
        raise
