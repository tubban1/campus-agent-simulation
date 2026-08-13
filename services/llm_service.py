import requests
import os
from typing import Optional

from app.config import LLM_API_KEY, LLM_API_URL


def llm_request_timeout_seconds() -> int:
    try:
        configured = int(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "20"))
    except ValueError:
        configured = 20
    return max(5, min(configured, 120))


def is_llm_configured() -> bool:
    return bool(LLM_API_KEY.strip() and LLM_API_URL.strip())


def ask_llm(prompt: str, *, timeout_seconds: Optional[int] = None) -> str:
    if not LLM_API_KEY:
        raise RuntimeError("缺少 LLM_API_KEY，请检查 .env 文件")

    if not LLM_API_URL:
        raise RuntimeError("缺少 LLM_API_URL，请检查 .env 文件")

    headers = {
        "x-goog-api-key": LLM_API_KEY,
        "Content-Type": "application/json",
    }

    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": prompt
                    }
                ],
            }
        ]
    }

    response = requests.post(
        LLM_API_URL,
        headers=headers,
        json=body,
        timeout=(
            max(1, min(int(timeout_seconds), llm_request_timeout_seconds()))
            if timeout_seconds is not None
            else llm_request_timeout_seconds()
        ),
    )

    response.raise_for_status()
    data = response.json()

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        return str(data)
