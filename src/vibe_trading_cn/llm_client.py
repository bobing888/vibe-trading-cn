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
- 内置 retry：3 次（生产必须 — 第 6 轮 review CRITICAL 修复）
- 4xx 不重试（参数/凭据错，重试无意义）；5xx/timeout/connection 触发重试
- OpenAI-compatible API（Kimi / DeepSeek 都用 OpenAI 格式）
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import requests


logger = logging.getLogger(__name__)


# Retry 配置（生产第 6 轮 review CRITICAL 修复）
RETRY_MAX_ATTEMPTS = 3
RETRY_BACKOFF_BASE_SEC = 1.0
RETRY_BACKOFF_FACTOR = 2.0  # 1s, 2s, 4s
# 哪些 HTTP 状态码触发 retry
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


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
    """OpenAI provider（含 Kimi / DeepSeek — OpenAI-compatible API）

    重试策略（第 6 轮 review CRITICAL）：
    - 重试 3 次，指数退避 1s/2s/4s
    - 重试触发条件：网络异常（timeout/connection/SSLError）或 5xx/429 响应
    - 4xx（除 429）不重试（凭据/参数错，重试无意义）
    - 全重试耗尽后由 BaseLLMClient.__call__ 兜底返回空 content
    """

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

        # retry loop
        last_err: Exception | None = None
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                resp = requests.post(url, json=body, headers=headers, timeout=30)
                if resp.status_code in RETRYABLE_STATUS_CODES:
                    raise RuntimeError(
                        f"HTTP {resp.status_code} from {self.config.provider} (attempt {attempt}/{RETRY_MAX_ATTEMPTS})"
                    )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return _StubResult(content=content)
            except requests.exceptions.Timeout as e:
                last_err = e
                logger.warning(
                    f"[{self.config.provider}] timeout (attempt {attempt}/{RETRY_MAX_ATTEMPTS})"
                )
            except requests.exceptions.ConnectionError as e:
                last_err = e
                logger.warning(
                    f"[{self.config.provider}] connection error (attempt {attempt}/{RETRY_MAX_ATTEMPTS}): {e}"
                )
            except requests.exceptions.HTTPError as e:
                # 4xx（非 retryable）直接抛出 → BaseLLMClient.__call__ 兜底
                if resp is not None and resp.status_code not in RETRYABLE_STATUS_CODES:
                    logger.error(
                        f"[{self.config.provider}] non-retryable HTTP {resp.status_code}: {e}"
                    )
                    raise
                last_err = e
                logger.warning(
                    f"[{self.config.provider}] HTTP error (attempt {attempt}/{RETRY_MAX_ATTEMPTS}): {e}"
                )
            except Exception as e:
                # 其它异常（RuntimeError 含 5xx）也走重试
                last_err = e
                logger.warning(
                    f"[{self.config.provider}] call failed (attempt {attempt}/{RETRY_MAX_ATTEMPTS}): {e}"
                )

            if attempt < RETRY_MAX_ATTEMPTS:
                backoff = RETRY_BACKOFF_BASE_SEC * (RETRY_BACKOFF_FACTOR ** (attempt - 1))
                time.sleep(backoff)

        # 重试全部失败
        logger.error(
            f"[{self.config.provider}] all {RETRY_MAX_ATTEMPTS} attempts failed: {last_err}"
        )
        raise RuntimeError(
            f"LLM call failed after {RETRY_MAX_ATTEMPTS} attempts: {last_err}"
        )


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
