from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from pipecat.frames.frames import DataFrame  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    DataFrame = object  # type: ignore[assignment,misc]


@dataclass
class GMClientMessageFrame(DataFrame):  # type: ignore[misc]
    """Client -> server control messages (settings, uploads, etc.)."""

    message: dict[str, Any]

