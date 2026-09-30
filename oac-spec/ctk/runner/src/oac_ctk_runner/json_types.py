"""JSON value types for the code-independent runner's existing JCS boundary."""

from collections.abc import Mapping, Sequence

type JsonValue = bool | int | float | str | Sequence[JsonValue] | Mapping[str, JsonValue] | None
