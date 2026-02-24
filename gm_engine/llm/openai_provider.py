from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class OpenAIChatLLM:
    """Minimal OpenAI chat completion wrapper implementing gm_engine.rlm.controller.LLMProvider."""

    model: str = "gpt-4o-mini"
    api_key: str | None = None
    base_url: str | None = None

    def __post_init__(self) -> None:
        try:
            from openai import AsyncOpenAI  # type: ignore
        except ModuleNotFoundError as e:  # pragma: no cover
            raise RuntimeError(
                "OpenAI SDK not installed. Install with: pip install -e '.[voice]'"
            ) from e

        key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not key:  # pragma: no cover
            raise RuntimeError("Missing OPENAI_API_KEY.")

        env_base_url = os.environ.get("OPENAI_BASE_URL")
        if env_base_url is not None and not env_base_url.strip():
            # Treat empty as unset to avoid httpx UnsupportedProtocol errors.
            os.environ.pop("OPENAI_BASE_URL", None)

        base_url = self.base_url or os.environ.get("OPENAI_BASE_URL")
        if base_url is not None and not str(base_url).strip():
            base_url = None

        self._client = AsyncOpenAI(api_key=key, base_url=base_url)

    async def complete(self, *, system: str, user: str, temperature: float = 0.2) -> str:
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
        msg = resp.choices[0].message.content if resp.choices else None
        return (msg or "").strip()
