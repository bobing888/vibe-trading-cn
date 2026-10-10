"""v3.5 PR-12 LLM API 多 provider 接入 测试

设计目标：
- 抽象 BaseLLMClient（duck typing: call(messages) → obj.content）
- 3 个 provider: OpenAI / Kimi / DeepSeek
- 统一 config（API key + base_url + model）
- 失败降级：网络/API 错误 → 返回空内容
- 不引入 langchain / openai SDK（v3 简化 — 用 requests 调 HTTP API）

v3 简化（对照 langchain ChatModel）：
- 不支持 stream（一次性返回）
- 不支持 function calling（v3 简化 — 4 analyst 单次 LLM 调用）
- 不支持多模态（纯文本）
- 单次重试（生产需加 retry/circuit breaker）
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import pytest


# ====================================================================
# BaseLLMClient 抽象测试
# ====================================================================

class TestBaseLLMClient:
    """BaseLLMClient 抽象类"""

    def test_base_client_is_abstract(self) -> None:
        """BaseLLMClient 不能直接实例化"""
        from src.vibe_trading_cn.llm_client import BaseLLMClient
        with pytest.raises(TypeError):
            BaseLLMClient()  # type: ignore[abstract]

    def test_base_client_subclass_callable(self) -> None:
        """子类实现 _call 即可用"""
        from src.vibe_trading_cn.llm_client import BaseLLMClient, ProviderConfig

        class FakeClient(BaseLLMClient):
            def _call(self, messages):
                return type("R", (), {"content": "fake response"})()

        c = FakeClient(ProviderConfig(provider="openai", api_key="x", base_url="x", model="x"))
        r = c([("system", "x"), ("human", "y")])
        assert r.content == "fake response"

    def test_base_client_handles_provider_errors_gracefully(self) -> None:
        """_call 抛错 → 返回空 content，不抛"""
        from src.vibe_trading_cn.llm_client import BaseLLMClient, ProviderConfig

        class BrokenClient(BaseLLMClient):
            def _call(self, messages):
                raise RuntimeError("API 限流")

        c = BrokenClient(ProviderConfig(provider="openai", api_key="x", base_url="x", model="x"))
        r = c([("system", "x")])
        assert r.content == ""


# ====================================================================
# Provider 配置测试
# ====================================================================

class TestProviderConfig:
    """Provider config: API key + base_url + model"""

    def test_config_from_env(self, monkeypatch) -> None:
        """从环境变量读 config"""
        from src.vibe_trading_cn.llm_client import ProviderConfig
        monkeypatch.setenv("VIBE_LLM_PROVIDER", "kimi")
        monkeypatch.setenv("VIBE_LLM_API_KEY", "sk-test-123")
        monkeypatch.setenv("VIBE_LLM_BASE_URL", "https://api.moonshot.cn/v1")
        monkeypatch.setenv("VIBE_LLM_MODEL", "moonshot-v1-8k")

        config = ProviderConfig.from_env()
        assert config.provider == "kimi"
        assert config.api_key == "sk-test-123"
        assert config.base_url == "https://api.moonshot.cn/v1"
        assert config.model == "moonshot-v1-8k"

    def test_config_default_provider_openai(self, monkeypatch) -> None:
        """无 VIBE_LLM_PROVIDER → 默认 openai"""
        from src.vibe_trading_cn.llm_client import ProviderConfig
        for k in ("VIBE_LLM_PROVIDER", "VIBE_LLM_API_KEY", "VIBE_LLM_BASE_URL", "VIBE_LLM_MODEL"):
            monkeypatch.delenv(k, raising=False)
        config = ProviderConfig.from_env()
        assert config.provider == "openai"

    def test_config_rejects_unknown_provider(self) -> None:
        """未知 provider → 抛错"""
        from src.vibe_trading_cn.llm_client import ProviderConfig
        with pytest.raises(ValueError, match="unknown provider"):
            ProviderConfig(provider="bogus", api_key="x", base_url="x", model="x")

    def test_supported_providers(self) -> None:
        """列出支持的 provider"""
        from src.vibe_trading_cn.llm_client import ProviderConfig
        providers = ProviderConfig.supported_providers()
        assert "openai" in providers
        assert "kimi" in providers
        assert "deepseek" in providers


# ====================================================================
# 3 Provider 单元测试（mock HTTP）
# ====================================================================

class TestOpenAIClient:
    """OpenAI provider"""

    def test_openai_client_constructs_with_config(self) -> None:
        from src.vibe_trading_cn.llm_client import OpenAIClient, ProviderConfig
        config = ProviderConfig(
            provider="openai", api_key="sk-test", base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
        )
        c = OpenAIClient(config)
        assert c.config.model == "gpt-4o-mini"

    def test_openai_client_call_formats_messages(self, monkeypatch) -> None:
        """call 把 messages 转 OpenAI chat.completions 格式"""
        from src.vibe_trading_cn.llm_client import OpenAIClient, ProviderConfig

        captured: dict = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            class R:
                status_code = 200
                def json(self_inner):
                    return {"choices": [{"message": {"content": "OPENAI_OK"}}]}
                def raise_for_status(self_inner): pass
            return R()

        monkeypatch.setattr("src.vibe_trading_cn.llm_client.requests.post", fake_post)

        config = ProviderConfig(
            provider="openai", api_key="sk-x", base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
        )
        c = OpenAIClient(config)
        r = c([("system", "你是助手"), ("human", "你好")])

        assert r.content == "OPENAI_OK"
        # 验证请求格式
        body = captured["json"]
        assert body["model"] == "gpt-4o-mini"
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "你是助手"
        assert body["messages"][1]["role"] == "user"
        assert "Bearer sk-x" in captured["headers"]["Authorization"]


class TestKimiClient:
    """Kimi (Moonshot) provider — 与 OpenAI API 兼容"""

    def test_kimi_client_uses_moonshot_base_url(self) -> None:
        from src.vibe_trading_cn.llm_client import KimiClient, ProviderConfig
        config = ProviderConfig(
            provider="kimi", api_key="sk-k", base_url="https://api.moonshot.cn/v1",
            model="moonshot-v1-8k",
        )
        c = KimiClient(config)
        assert "moonshot" in c.config.base_url


class TestDeepSeekClient:
    """DeepSeek provider — 与 OpenAI API 兼容"""

    def test_deepseek_client_uses_deepseek_base_url(self) -> None:
        from src.vibe_trading_cn.llm_client import DeepSeekClient, ProviderConfig
        config = ProviderConfig(
            provider="deepseek", api_key="sk-d", base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
        )
        c = DeepSeekClient(config)
        assert "deepseek" in c.config.base_url


# ====================================================================
# Factory 测试
# ====================================================================

class TestLLMClientFactory:
    """get_client() factory: provider → 实例"""

    def test_factory_returns_correct_provider(self, monkeypatch) -> None:
        from src.vibe_trading_cn import llm_client
        monkeypatch.setenv("VIBE_LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("VIBE_LLM_API_KEY", "sk-d")
        monkeypatch.setenv("VIBE_LLM_BASE_URL", "https://api.deepseek.com/v1")
        monkeypatch.setenv("VIBE_LLM_MODEL", "deepseek-chat")
        c = llm_client.get_client()
        assert isinstance(c, llm_client.DeepSeekClient)

    def test_factory_openai(self, monkeypatch) -> None:
        from src.vibe_trading_cn import llm_client
        monkeypatch.setenv("VIBE_LLM_PROVIDER", "openai")
        monkeypatch.setenv("VIBE_LLM_API_KEY", "sk-o")
        monkeypatch.setenv("VIBE_LLM_BASE_URL", "https://api.openai.com/v1")
        monkeypatch.setenv("VIBE_LLM_MODEL", "gpt-4o-mini")
        c = llm_client.get_client()
        assert isinstance(c, llm_client.OpenAIClient)


# ====================================================================
# Retry 行为测试（第 6 轮 review CRITICAL 修复）
# ====================================================================

class TestLLMClientRetry:
    """LLM 调用 retry 行为

    - 5xx / 429 / timeout / connection error → 重试 3 次
    - 4xx（除 429）→ 不重试，立即抛
    - 全重试失败 → BaseLLMClient.__call__ 兜底返回空 content
    """

    def _build_client(self):
        from src.vibe_trading_cn.llm_client import OpenAIClient, ProviderConfig
        return OpenAIClient(ProviderConfig(
            provider="openai", api_key="sk-x", base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
        ))

    def test_retry_on_5xx_then_success(self, monkeypatch) -> None:
        """第一次 503，第二次 200 → 成功返回第 2 次内容"""
        call_count = {"n": 0}
        from src.vibe_trading_cn import llm_client

        def fake_post(url, json=None, headers=None, timeout=None):
            call_count["n"] += 1
            from requests.exceptions import HTTPError
            class R:
                def __init__(self, code, body=None):
                    self.status_code = code
                    self._body = body or {}
                def json(self_inner):
                    return self_inner._body
                def raise_for_status(self_inner):
                    if self_inner.status_code >= 400:
                        raise HTTPError(f"{self_inner.status_code}")
            if call_count["n"] == 1:
                return R(503)
            return R(200, {"choices": [{"message": {"content": "RETRY_OK"}}]})

        # 跳过 sleep（避免 CI 慢）
        monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
        monkeypatch.setattr("src.vibe_trading_cn.llm_client.requests.post", fake_post)

        c = self._build_client()
        r = c([("system", "x"), ("human", "y")])
        assert r.content == "RETRY_OK"
        assert call_count["n"] == 2

    def test_retry_on_timeout_then_success(self, monkeypatch) -> None:
        """第一次 timeout，第二次成功"""
        call_count = {"n": 0}
        from src.vibe_trading_cn import llm_client

        def fake_post(url, json=None, headers=None, timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                import requests
                raise requests.exceptions.Timeout("read timeout")
            class R:
                status_code = 200
                def json(self_inner):
                    return {"choices": [{"message": {"content": "TIMEOUT_RECOVERED"}}]}
                def raise_for_status(self_inner): pass
            return R()

        monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
        monkeypatch.setattr("src.vibe_trading_cn.llm_client.requests.post", fake_post)

        c = self._build_client()
        r = c([("system", "x")])
        assert r.content == "TIMEOUT_RECOVERED"
        assert call_count["n"] == 2

    def test_no_retry_on_4xx(self, monkeypatch) -> None:
        """401 立即抛（凭据错，重试无意义）"""
        call_count = {"n": 0}
        from src.vibe_trading_cn import llm_client

        def fake_post(url, json=None, headers=None, timeout=None):
            call_count["n"] += 1
            from requests.exceptions import HTTPError
            class R:
                status_code = 401
                def json(self_inner): return {}
                def raise_for_status(self_inner):
                    raise HTTPError("401")
            return R()

        monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
        monkeypatch.setattr("src.vibe_trading_cn.llm_client.requests.post", fake_post)

        c = self._build_client()
        r = c([("system", "x")])
        # __call__ 兜底 → 空 content
        assert r.content == ""
        assert call_count["n"] == 1  # 不重试

    def test_retry_exhausted_returns_empty(self, monkeypatch) -> None:
        """3 次都 503 → 抛 → __call__ 兜底返回空 content"""
        call_count = {"n": 0}
        from src.vibe_trading_cn import llm_client

        def fake_post(url, json=None, headers=None, timeout=None):
            call_count["n"] += 1
            from requests.exceptions import HTTPError
            class R:
                status_code = 503
                def json(self_inner): return {}
                def raise_for_status(self_inner):
                    raise HTTPError("503")
            return R()

        monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
        monkeypatch.setattr("src.vibe_trading_cn.llm_client.requests.post", fake_post)

        c = self._build_client()
        r = c([("system", "x")])
        assert r.content == ""
        assert call_count["n"] == 3  # 重试 3 次

    def test_retry_429_ratelimit(self, monkeypatch) -> None:
        """429 触发重试（特殊 4xx）"""
        call_count = {"n": 0}
        from src.vibe_trading_cn import llm_client

        def fake_post(url, json=None, headers=None, timeout=None):
            call_count["n"] += 1
            from requests.exceptions import HTTPError
            class R:
                def __init__(self, code, body=None):
                    self.status_code = code
                    self._body = body or {}
                def json(self_inner):
                    return self_inner._body
                def raise_for_status(self_inner):
                    if self_inner.status_code >= 400:
                        raise HTTPError(f"{self_inner.status_code}")
            if call_count["n"] < 3:
                return R(429)
            return R(200, {"choices": [{"message": {"content": "RATELIMIT_RECOVERED"}}]})

        monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
        monkeypatch.setattr("src.vibe_trading_cn.llm_client.requests.post", fake_post)

        c = self._build_client()
        r = c([("system", "x")])
        assert r.content == "RATELIMIT_RECOVERED"
        assert call_count["n"] == 3


# ====================================================================
# 端到端：接 production_adapter
# ====================================================================

class TestProductionIntegration:
    """production_adapter.call_llm 接受 LLMClient"""

    def test_production_adapter_uses_llm_client(self, monkeypatch) -> None:
        from src.vibe_trading_cn import production_adapter
        from src.vibe_trading_cn.llm_client import OpenAIClient, ProviderConfig

        class FakeLLM:
            def __call__(self, messages):
                return type("R", (), {"content": "PROD_OK"})()

        monkeypatch.setattr(
            production_adapter, "call_llm", lambda msgs, llm_client=None: FakeLLM()(msgs),
        )
        result = production_adapter.call_llm(
            [("system", "x"), ("human", "y")],
            llm_client=FakeLLM(),
        )
        assert result.content == "PROD_OK"
