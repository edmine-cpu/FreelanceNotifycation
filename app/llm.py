"""Provider contract: every request carries its verified owner and operation."""
from dataclasses import dataclass, field
from typing import Literal, Protocol


class LLMError(Exception):
    pass


class LLMResponseError(LLMError):
    pass


class UncertainRequestError(LLMError):
    """Provider may have processed the request; do not automatically repeat it."""


class QuotaExceededError(LLMError):
    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "model", "assistant"]
    text: str


@dataclass(frozen=True)
class AIRequest:
    user_id: int
    project_id: str
    purpose: Literal["screen", "bid", "regenerate"]
    operation_id: str


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str = ""
    request_id: str = ""
    stop_reason: str = "end_turn"
    usage: dict = field(default_factory=dict)


class LLMClient(Protocol):
    async def generate(
        self, *, system_instruction: str, messages: list[ChatMessage],
        context: AIRequest, schema: dict | None = None,
    ) -> LLMResult: ...
