from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol


class Embedder(Protocol):
    async def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class OpenAIEmbedder(Embedder):
    model: str = "text-embedding-3-small"
    api_key: str | None = None
    base_url: str | None = None

    def __post_init__(self) -> None:
        try:
            from openai import AsyncOpenAI  # type: ignore
        except ModuleNotFoundError as e:  # pragma: no cover
            raise RuntimeError("OpenAI SDK not installed. Install with: pip install -e '.[voice]'") from e

        key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("Missing OPENAI_API_KEY (required for embeddings).")

        base_url = self.base_url or os.environ.get("OPENAI_BASE_URL")
        if base_url is not None and not str(base_url).strip():
            base_url = None

        self._client = AsyncOpenAI(api_key=key, base_url=base_url)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # OpenAI supports batching.
        resp = await self._client.embeddings.create(model=self.model, input=texts)
        # Keep order stable.
        data = list(resp.data or [])
        data.sort(key=lambda d: d.index)
        return [list(d.embedding) for d in data]

