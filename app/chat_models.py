"""Запросы/ответы для диалога с агентом."""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=32000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=16000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)
    """Контекст последней обработки файла с этой страницы (передаёт клиент после «Обработать»)."""
    processing_context: dict[str, Any] | None = None

    @field_validator("processing_context")
    @classmethod
    def limit_processing_context(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return None
        if len(json.dumps(v, ensure_ascii=False)) > 52000:
            raise ValueError("processing_context слишком большой")
        return v


class ChatResponse(BaseModel):
    reply: str
