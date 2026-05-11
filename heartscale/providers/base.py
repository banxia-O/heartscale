"""Abstract interfaces for embedding and judge providers."""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns one vector per text."""
        ...

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


class JudgeProvider(ABC):
    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        response_format: str = "text",  # "text" or "json_object"
    ) -> str:
        """Send a message list to the LLM, return the response text."""
        ...

    def ask(self, system: str, user: str, **kwargs) -> str:
        """Convenience wrapper: single system + user turn."""
        return self.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            **kwargs,
        )
