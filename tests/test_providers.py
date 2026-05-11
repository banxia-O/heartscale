"""Tests for provider adapters — all network calls are mocked."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from heartscale.providers.base import EmbeddingProvider, JudgeProvider
from heartscale.providers.openai_compat import (
    OpenAICompatEmbeddingProvider,
    OpenAICompatJudgeProvider,
)
from heartscale.providers.factory import make_embedding_provider, make_judge_provider
from heartscale.config import ProviderConfig


# ---------------------------------------------------------------------------
# Helpers to build fake OpenAI responses
# ---------------------------------------------------------------------------

def _fake_embedding_response(vectors: list[list[float]]):
    items = [
        SimpleNamespace(embedding=vec, index=i)
        for i, vec in enumerate(vectors)
    ]
    return SimpleNamespace(data=items)


def _fake_chat_response(content: str):
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


# ---------------------------------------------------------------------------
# EmbeddingProvider interface contract
# ---------------------------------------------------------------------------

class TestEmbeddingInterface:
    def test_embed_one_delegates_to_embed(self):
        """embed_one() must return the first vector from embed()."""
        class FakeEmbed(EmbeddingProvider):
            def embed(self, texts):
                return [[0.1, 0.2, 0.3]] * len(texts)

        provider = FakeEmbed()
        result = provider.embed_one("hello")
        assert result == [0.1, 0.2, 0.3]

    def test_embed_empty_list_returns_empty(self):
        class FakeEmbed(EmbeddingProvider):
            def embed(self, texts):
                return []

        provider = FakeEmbed()
        assert provider.embed([]) == []


# ---------------------------------------------------------------------------
# JudgeProvider interface contract
# ---------------------------------------------------------------------------

class TestJudgeInterface:
    def test_ask_convenience_wrapper(self):
        """ask() must build [system, user] messages and call chat()."""
        class FakeJudge(JudgeProvider):
            def __init__(self):
                self.received = None

            def chat(self, messages, **kwargs):
                self.received = messages
                return "ok"

        judge = FakeJudge()
        result = judge.ask(system="sys", user="usr")
        assert result == "ok"
        assert judge.received[0] == {"role": "system", "content": "sys"}
        assert judge.received[1] == {"role": "user", "content": "usr"}


# ---------------------------------------------------------------------------
# OpenAICompatEmbeddingProvider
# ---------------------------------------------------------------------------

class TestOpenAICompatEmbedding:
    def _make_provider(self, mock_client):
        p = OpenAICompatEmbeddingProvider(
            api_key="sk-test",
            model="text-embedding-3-small",
            dimension=3,
        )
        p._client = mock_client
        return p

    def test_embed_returns_vectors(self):
        vectors = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        client = MagicMock()
        client.embeddings.create.return_value = _fake_embedding_response(vectors)
        provider = self._make_provider(client)

        result = provider.embed(["hello", "world"])

        assert len(result) == 2
        assert result[0] == pytest.approx([0.1, 0.2, 0.3])
        assert result[1] == pytest.approx([0.4, 0.5, 0.6])

    def test_embed_passes_dimension(self):
        client = MagicMock()
        client.embeddings.create.return_value = _fake_embedding_response([[0.0]])
        provider = self._make_provider(client)
        provider.embed(["text"])

        call_kwargs = client.embeddings.create.call_args.kwargs
        assert call_kwargs.get("dimensions") == 3

    def test_embed_empty_list_skips_api(self):
        client = MagicMock()
        provider = self._make_provider(client)
        result = provider.embed([])
        client.embeddings.create.assert_not_called()
        assert result == []

    def test_embed_one_returns_single_vector(self):
        client = MagicMock()
        client.embeddings.create.return_value = _fake_embedding_response([[1.0, 2.0]])
        provider = self._make_provider(client)
        result = provider.embed_one("single")
        assert result == pytest.approx([1.0, 2.0])

    def test_embed_result_sorted_by_index(self):
        """API may return items out of order — provider must sort by index."""
        items = [
            SimpleNamespace(embedding=[0.9, 0.9], index=1),
            SimpleNamespace(embedding=[0.1, 0.1], index=0),
        ]
        client = MagicMock()
        client.embeddings.create.return_value = SimpleNamespace(data=items)
        provider = self._make_provider(client)

        result = provider.embed(["first", "second"])
        assert result[0] == pytest.approx([0.1, 0.1])
        assert result[1] == pytest.approx([0.9, 0.9])

    def test_rate_limit_retry(self):
        from openai import RateLimitError
        client = MagicMock()
        client.embeddings.create.side_effect = [
            RateLimitError("rate limit", response=MagicMock(status_code=429), body={}),
            _fake_embedding_response([[0.5]]),
        ]
        provider = self._make_provider(client)
        with patch("time.sleep"):  # don't actually sleep in tests
            result = provider.embed(["retry me"])
        assert result[0] == pytest.approx([0.5])
        assert client.embeddings.create.call_count == 2


# ---------------------------------------------------------------------------
# OpenAICompatJudgeProvider
# ---------------------------------------------------------------------------

class TestOpenAICompatJudge:
    def _make_provider(self, mock_client):
        p = OpenAICompatJudgeProvider(
            api_key="sk-test",
            model="gpt-4o-mini",
        )
        p._client = mock_client
        return p

    def test_chat_returns_content(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _fake_chat_response("回复内容")
        provider = self._make_provider(client)

        result = provider.chat([{"role": "user", "content": "你好"}])
        assert result == "回复内容"

    def test_chat_passes_temperature(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _fake_chat_response("")
        provider = self._make_provider(client)
        provider.chat([{"role": "user", "content": "test"}], temperature=0.7)

        call_kwargs = client.chat.completions.create.call_args.kwargs
        assert call_kwargs["temperature"] == 0.7

    def test_chat_json_mode(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _fake_chat_response('{"key":"val"}')
        provider = self._make_provider(client)
        provider.chat([{"role": "user", "content": "give json"}], response_format="json_object")

        call_kwargs = client.chat.completions.create.call_args.kwargs
        assert call_kwargs["response_format"] == {"type": "json_object"}

    def test_ask_shorthand(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _fake_chat_response("shorthand reply")
        provider = self._make_provider(client)

        result = provider.ask(system="system prompt", user="user prompt")
        assert result == "shorthand reply"
        msgs = client.chat.completions.create.call_args.kwargs["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_rate_limit_retry(self):
        from openai import RateLimitError
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            RateLimitError("rate limit", response=MagicMock(status_code=429), body={}),
            _fake_chat_response("after retry"),
        ]
        provider = self._make_provider(client)
        with patch("time.sleep"):
            result = provider.chat([{"role": "user", "content": "go"}])
        assert result == "after retry"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestFactory:
    def _cfg(self, provider="openai", key_env="TEST_KEY", base_url=""):
        return ProviderConfig(
            provider=provider,
            model="test-model",
            api_key_env=key_env,
            base_url=base_url,
        )

    def test_make_embedding_openai(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "sk-123")
        p = make_embedding_provider(self._cfg())
        assert isinstance(p, OpenAICompatEmbeddingProvider)

    def test_make_embedding_siliconflow(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "sk-123")
        p = make_embedding_provider(self._cfg(provider="siliconflow"))
        assert isinstance(p, OpenAICompatEmbeddingProvider)

    def test_make_judge_openai(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "sk-123")
        p = make_judge_provider(self._cfg())
        assert isinstance(p, OpenAICompatJudgeProvider)

    def test_make_judge_deepseek(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "sk-123")
        p = make_judge_provider(self._cfg(provider="deepseek"))
        assert isinstance(p, OpenAICompatJudgeProvider)

    def test_make_embedding_unknown_raises(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "sk-123")
        with pytest.raises(ValueError, match="Unknown embedding provider"):
            make_embedding_provider(self._cfg(provider="unsupported_xyz"))

    def test_make_judge_unknown_raises(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "sk-123")
        with pytest.raises(ValueError, match="Unknown judge provider"):
            make_judge_provider(self._cfg(provider="unsupported_xyz"))
