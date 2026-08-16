from __future__ import annotations

import os
import requests
from typing import Any, Dict, Optional, Tuple
from dotenv import load_dotenv

from app.config import ENV_PATH


def llm_request_timeout_seconds() -> int:
    try:
        configured = int(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "20"))
    except ValueError:
        configured = 20
    return max(5, min(configured, 120))


class BaseLLMProvider:
    """LLM 供应商抽象基类"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        model: Optional[str] = None,
        admin_token: Optional[str] = None,
    ):
        self.api_key = (api_key if api_key is not None else os.getenv("LLM_API_KEY", "")).strip()
        self.api_url = (api_url if api_url is not None else os.getenv("LLM_API_URL", "")).strip()
        self.model = (model if model is not None else os.getenv("LLM_MODEL", "")).strip()
        self.admin_token = (
            admin_token
            if admin_token is not None
            else (os.getenv("LLM_ADMIN_TOKEN") or os.getenv("ADMIN_TOKEN", ""))
        ).strip()

    def is_configured(self) -> bool:
        return bool(self.api_url and (self.api_key or self.admin_token))

    def prepare_request(self, prompt: str) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        raise NotImplementedError

    def parse_response(self, data: Dict[str, Any]) -> str:
        raise NotImplementedError

    def call(self, prompt: str, *, timeout_seconds: Optional[int] = None) -> str:
        if not self.is_configured():
            raise RuntimeError("缺少 LLM_API_KEY/ADMIN_TOKEN 或 LLM_API_URL，请检查 .env 配置")

        url, headers, body = self.prepare_request(prompt)
        actual_timeout = (
            max(1, min(int(timeout_seconds), llm_request_timeout_seconds()))
            if timeout_seconds is not None
            else llm_request_timeout_seconds()
        )

        response = requests.post(url, headers=headers, json=body, timeout=actual_timeout)
        response.raise_for_status()
        data = response.json()
        return self.parse_response(data)


class OpenAIProvider(BaseLLMProvider):
    """OpenAI 协议提供者 (兼容 OpenAI GPT, DeepSeek, Qwen, Moonshot, 硅基流动, vLLM 等)"""

    def prepare_request(self, prompt: str) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        url = self.api_url
        if not url:
            url = "https://api.openai.com/v1/chat/completions"
        elif not (
            url.endswith("/v1/chat/completions")
            or url.endswith("/chat/completions")
            or ":generateContent" in url
        ):
            url = url.rstrip("/") + "/v1/chat/completions"

        headers = {
            "Content-Type": "application/json",
        }
        token = self.api_key or self.admin_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self.admin_token:
            headers["x-admin-token"] = self.admin_token

        body: Dict[str, Any] = {
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.model:
            body["model"] = self.model
        else:
            body["model"] = "gpt-4o-mini"

        return url, headers, body

    def parse_response(self, data: Dict[str, Any]) -> str:
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return str(data)


class GeminiProvider(BaseLLMProvider):
    """Google Gemini 原生 REST API 提供者 (支持各种 Gemini proxy 网关)"""

    def prepare_request(self, prompt: str) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        url = self.api_url
        if not url:
            model_name = self.model or "gemini-1.5-flash"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"

        headers = {
            "Content-Type": "application/json",
        }
        token = self.api_key or self.admin_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        if self.admin_token:
            headers["x-admin-token"] = self.admin_token

        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ]
        }
        return url, headers, body

    def parse_response(self, data: Dict[str, Any]) -> str:
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return str(data)


class OllamaProvider(BaseLLMProvider):
    """本地 Ollama 提供者"""

    def prepare_request(self, prompt: str) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        url = self.api_url or "http://localhost:11434/api/chat"
        headers = {"Content-Type": "application/json"}
        body = {
            "model": self.model or "llama3",
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        return url, headers, body

    def parse_response(self, data: Dict[str, Any]) -> str:
        try:
            message = data.get("message")
            if isinstance(message, dict) and "content" in message:
                return message["content"]
            if "response" in data:
                return str(data["response"])
            return str(data)
        except Exception:
            return str(data)


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude API 提供者"""

    def prepare_request(self, prompt: str) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        url = self.api_url or "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key or self.admin_token,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": self.model or "claude-3-5-sonnet-20241022",
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}],
        }
        return url, headers, body

    def parse_response(self, data: Dict[str, Any]) -> str:
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return str(data)


class LLMFactory:
    """LLM 工厂类，负责根据配置和环境变量生产相应的 Provider"""

    _PROVIDERS = {
        "openai": OpenAIProvider,
        "deepseek": OpenAIProvider,
        "qwen": OpenAIProvider,
        "moonshot": OpenAIProvider,
        "siliconflow": OpenAIProvider,
        "gemini": GeminiProvider,
        "ollama": OllamaProvider,
        "anthropic": AnthropicProvider,
        "claude": AnthropicProvider,
    }

    @classmethod
    def get_provider(
        cls,
        provider_name: Optional[str] = None,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        model: Optional[str] = None,
        admin_token: Optional[str] = None,
    ) -> BaseLLMProvider:
        # 始终强行加载最新的 .env 文件，防止内存环境变量滞后
        load_dotenv(ENV_PATH, override=False)

        p_name = (
            provider_name
            if provider_name is not None
            else (os.getenv("LLM_PROVIDER") or os.getenv("DEFAULT_LLM_PROVIDER") or "").strip().lower()
        )

        # 尝试根据供应商前缀获取预设配置 (如 DEEPSEEK_API_KEY, GEMINI_API_KEY, VLLM_ADMIN_TOKEN 等)
        prefix = p_name.upper() if p_name else ""
        if prefix == "CLAUDE":
            prefix = "ANTHROPIC"

        env_key = (
            os.getenv(f"{prefix}_API_KEY")
            if prefix and os.getenv(f"{prefix}_API_KEY")
            else os.getenv("LLM_API_KEY", "")
        )

        env_url = (
            os.getenv(f"{prefix}_API_URL")
            if prefix and os.getenv(f"{prefix}_API_URL")
            else os.getenv("LLM_API_URL", "")
        )

        env_model = (
            os.getenv(f"{prefix}_MODEL")
            if prefix and os.getenv(f"{prefix}_MODEL")
            else os.getenv("LLM_MODEL", "")
        )

        env_admin_token = (
            os.getenv(f"{prefix}_ADMIN_TOKEN")
            if prefix and os.getenv(f"{prefix}_ADMIN_TOKEN")
            else (os.getenv("LLM_ADMIN_TOKEN") or os.getenv("ADMIN_TOKEN", ""))
        )

        target_key = api_key if api_key is not None else env_key
        target_url = api_url if api_url is not None else env_url
        target_model = model if model is not None else env_model
        target_admin_token = admin_token if admin_token is not None else env_admin_token

        # 自动识别协议与推断 provider 类型
        if p_name in cls._PROVIDERS:
            provider_cls = cls._PROVIDERS[p_name]
        elif ":generateContent" in target_url or "googleapis.com" in target_url or "gemini" in target_url:
            provider_cls = GeminiProvider
        elif "ollama" in target_url or "11434" in target_url:
            provider_cls = OllamaProvider
        elif "anthropic" in target_url or p_name in ("anthropic", "claude"):
            provider_cls = AnthropicProvider
        else:
            provider_cls = OpenAIProvider

        return provider_cls(
            api_key=target_key,
            api_url=target_url,
            model=target_model,
            admin_token=target_admin_token,
        )


def is_llm_configured() -> bool:
    provider = LLMFactory.get_provider()
    return provider.is_configured()


def ask_llm(prompt: str, *, timeout_seconds: Optional[int] = None) -> str:
    provider = LLMFactory.get_provider()
    return provider.call(prompt, timeout_seconds=timeout_seconds)


def get_llm_provider_info() -> Dict[str, str]:
    provider = LLMFactory.get_provider()
    return {
        "provider_class": provider.__class__.__name__,
        "model": provider.model,
        "api_url": provider.api_url,
        "is_configured": str(provider.is_configured()),
    }

