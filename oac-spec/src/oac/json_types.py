"""Values accepted by the existing RFC 8785 serialization boundary."""

from collections.abc import Mapping, Sequence

type JsonValue = bool | int | float | str | Sequence[JsonValue] | Mapping[str, JsonValue] | None
