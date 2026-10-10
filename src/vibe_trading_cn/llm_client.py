"""v3.5 PR-12 llm_client — 多 provider LLM 客户端

设计：
- BaseLLMClient 抽象类（duck typing: call(messages) → obj.content）
- 3 provider: OpenAI / Kimi (Moonshot) / DeepSeek
- 统一 config：API key + base_url + model
- 失败降级：网络/API 错误 → 返回空 content
- 用 requests 调 HTTP API（不引入 openai SDK / langchain）

v3 简化：
- 不支持 stream
- 不支持 function calling
- 不支持多模态
- 单次重试（生产需加 retry/circuit breaker）
- OpenAI-compatible API（Kimi / DeepSeek 都用 OpenAI 格式）
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

import requests


logger = logging.getLogger(__name__)


SUPPORTED_PROVIDERS = ("openai", "kimi", "deepseek")


@dataclass(frozen=True)
class ProviderConfig:
    """LLM Provider 配置"""

    provider: str
    api_key: str
    base_url: str
    model: str

    def __post_init__(self) -> None:
        # frozen=True 不能直接赋值；用 object.__setattr__ 也不行（frozen）
        # 改用 raise
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"unknown provider: {self.provider!r}; "
                f"supported: {SUPPORTED_PROVIDERS}"
            )

    @classmethod
    def from_env(cls) -> "ProviderConfig":
        """从环境变量构造 config

        必需：
        - VIBE_LLM_PROVIDER（默认 openai）
        - VIBE_LLM_API_KEY
        - VIBE_LLM_BASE_URL
        - VIBE_LLM_MODEL
        """
        provider = os.getenv("VIBE_LLM_PROVIDER", "openai").lower()
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"unknown provider: {provider!r}; "
                f"supported: {SUPPORTED_PROVIDERS}"
            )
        return cls(
            provider=provider,
            api_key=os.getenv("VIBE_LLM_API_KEY", ""),
            base_url=os.getenv("VIBE_LLM_BASE_URL", _default_base_url(provider)),
            model=os.getenv("VIBE_LLM_MODEL", _default_model(provider)),
        )

    @staticmethod
    def supported_providers() -> tuple[str, ...]:
        return SUPPORTED_PROVIDERS


def _default_base_url(provider: str) -> str:
    return {
        "openai": "https://api.openai.com/v1",
        "kimi": "https://api.moonshot.cn/v1",
        "deepseek": "https://api.deepseek.com/v1",
    }[provider]


def _default_model(provider: str) -> str:
    return {
        "openai": "gpt-4o-mini",
        "kimi": "moonshot-v1-8k",
        "deepseek": "deepseek-chat",
    }[provider]


class _StubResult:
    """LLM 调用结果（duck typing：只要有 .content）"""

    def __init__(self, content: str = "") -> None:
        self.content = content


class BaseLLMClient(ABC):
    """LLM 客户端基类（duck typing）

    子类实现 _call(messages) → obj.content
    """

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def __call__(self, messages: list[tuple[str, str]]) -> _StubResult:
        """统一入口：调 _call，捕获异常返回空"""
        try:
            return self._call(messages)
        except Exception as e:
            logger.error(f"[{self.config.provider}] LLM call failed: {e}")
            return _StubResult(content="")

    @abstractmethod
    def _call(self, messages: list[tuple[str, str]]) -> _StubResult:
        """子类实现：实际 HTTP 调用"""
        raise NotImplementedError


class OpenAIClient(BaseLLMClient):
    """OpenAI provider（含 Kimi / DeepSeek — OpenAI-compatible API）"""

    def _call(self, messages: list[tuple[str, str]]) -> _StubResult:
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        # messages 格式：[(system, "..."), (human, "...")] → OpenAI [{role, content}, ...]
        oai_messages = []
        for role, content in messages:
            oai_role = "system" if role == "system" else "user"
            oai_messages.append({"role": oai_role, "content": content})
        body = {
            "model": self.config.model,
            "messages": oai_messages,
            "temperature": 0.3,
            "max_tokens": 1024,
        }
        resp = requests.post(url, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return _StubResult(content=content)


class KimiClient(OpenAIClient):
    """Kimi (Moonshot) — OpenAI-compatible"""

    pass


class DeepSeekClient(OpenAIClient):
    """DeepSeek — OpenAI-compatible"""

    pass


def get_client(config: ProviderConfig | None = None) -> BaseLLMClient:
    """Factory: ProviderConfig → 对应 client 实例"""
    if config is None:
        config = ProviderConfig.from_env()
    return {
        "openai": OpenAIClient,
        "kimi": KimiClient,
        "deepseek": DeepSeekClient,
    }[config.provider](config)


__all__ = [
    "BaseLLMClient",
    "OpenAIClient",
    "KimiClient",
    "DeepSeekClient",
    "ProviderConfig",
    "get_client",
    "SUPPORTED_PROVIDERS",
]
