"""Provider-agnostic LLM interface.

All model-specific code lives in a single client implementation (currently
``app.ai.GroqClient``). To switch providers or models, write a new class
that satisfies ``LLMClient`` and swap the one instantiation in ``app.app.run`` —
prompt-building code (e.g. ``BidGenerator``) depends only on the types here, not
on any SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

Role = Literal["user", "model"]


class LLMError(Exception):
    """Base class for failures coming from an LLM client."""


class QuotaExceededError(LLMError):
    """The model rejected the request because a quota or rate limit is exhausted
    (HTTP 429). Distinct from transient errors: the caller should back off / wait,
    not just retry immediately."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class ChatMessage:
    """One turn in a chat exchange, independent of any vendor SDK."""

    role: Role
    text: str


@runtime_checkable
class LLMClient(Protocol):
    """The single contract every model client must satisfy.

    Implementations own *all* vendor-specific details (SDK calls, request/response
    shapes, retry policy). Callers pass plain ``ChatMessage`` objects and get back
    text, so they never import a provider SDK.
    """

    async def generate(
        self,
        *,
        system_instruction: str,
        messages: list[ChatMessage],
        temperature: float = 0.8,
    ) -> str:
        ...
