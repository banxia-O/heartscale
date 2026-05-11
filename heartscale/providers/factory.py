"""Factory: build providers from Config objects."""

from __future__ import annotations

from heartscale.config import ProviderConfig
from heartscale.providers.base import EmbeddingProvider, JudgeProvider
from heartscale.providers.openai_compat import (
    OpenAICompatEmbeddingProvider,
    OpenAICompatJudgeProvider,
)

# Providers that speak the OpenAI REST format
_OPENAI_COMPAT = {"openai", "siliconflow", "deepseek", "azure", "local", "ollama", "vllm"}


def make_embedding_provider(cfg: ProviderConfig) -> EmbeddingProvider:
    provider = cfg.provider.lower()
    if provider in _OPENAI_COMPAT:
        return OpenAICompatEmbeddingProvider(
            api_key=cfg.api_key(),
            model=cfg.model,
            base_url=cfg.resolved_base_url(),
            dimension=cfg.dimension,
        )
    raise ValueError(
        f"Unknown embedding provider: {cfg.provider!r}. "
        f"Supported: {sorted(_OPENAI_COMPAT)}"
    )


def make_judge_provider(cfg: ProviderConfig) -> JudgeProvider:
    provider = cfg.provider.lower()
    if provider in _OPENAI_COMPAT:
        return OpenAICompatJudgeProvider(
            api_key=cfg.api_key(),
            model=cfg.model,
            base_url=cfg.resolved_base_url(),
        )
    raise ValueError(
        f"Unknown judge provider: {cfg.provider!r}. "
        f"Supported: {sorted(_OPENAI_COMPAT)}"
    )
