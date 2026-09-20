"""Structured model provider contract used by workspace candidates."""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

from orgrebase.workspace.models import (
    ModelRequest,
    ModelRequestV2,
    ModelResponseReceipt,
    ModelResponseReceiptV2,
)

T = TypeVar("T", bound=BaseModel)


class ModelProvider[RequestT: ModelRequest | ModelRequestV2, ResponseT: ModelResponseReceipt | ModelResponseReceiptV2](Protocol):
    def generate_structured(self, *, request: RequestT, output_model: type[T]) -> ResponseT: ...
