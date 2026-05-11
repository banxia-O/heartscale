"""OpenAI-compatible provider implementation.

Works with any API that speaks the OpenAI REST format:
  - OpenAI
  - SiliconFlow (Qwen3-Embedding, DeepSeek-V3 Flash, etc.)
  - DeepSeek
  - Any local server (ollama, LM Studio, vllm) with an OpenAI-compatible endpoint
"""

from __future__ import annotations

import time
from typing import Optional

from openai import OpenAI, APIError, RateLimitError

from heartscale.providers.base import EmbeddingProvider, JudgeProvider


class OpenAICompatEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        dimension: Optional[int] = None,
        max_retries: int = 3,
    ):
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url or None,
        )
        self._model = model
        self._dimension = dimension
        self._max_retries = max_retries

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        kwargs: dict = {"input": texts, "model": self._model}
        if self._dimension:
            kwargs["dimensions"] = self._dimension

        for attempt in range(self._max_retries):
            try:
                response = self._client.embeddings.create(**kwargs)
                # API returns items sorted by index
                return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
            except RateLimitError:
                if attempt == self._max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
            except APIError as e:
                if attempt == self._max_retries - 1:
                    raise
                if e.status_code and e.status_code >= 500:
                    time.sleep(2 ** attempt)
                else:
                    raise
        return []  # unreachable, satisfies type checker


class OpenAICompatJudgeProvider(JudgeProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        max_retries: int = 3,
    ):
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url or None,
        )
        self._model = model
        self._max_retries = max_retries

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        response_format: str = "text",
    ) -> str:
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format == "json_object":
            kwargs["response_format"] = {"type": "json_object"}

        for attempt in range(self._max_retries):
            try:
                response = self._client.chat.completions.create(**kwargs)
                return response.choices[0].message.content or ""
            except RateLimitError:
                if attempt == self._max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
            except APIError as e:
                if attempt == self._max_retries - 1:
                    raise
                if e.status_code and e.status_code >= 500:
                    time.sleep(2 ** attempt)
                else:
                    raise
        return ""  # unreachable
