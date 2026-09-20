"""Read-only decoding of completion metadata in retained Workspace evidence."""

from collections.abc import Mapping
from typing import Any


def business_is_complete(state: Mapping[str, Any]) -> bool:
    if "business_complete" in state:
        return state["business_complete"] is True
    # Older evidence used the final fixture stage instead of an explicit field.
    return state.get("schema_version") in {None, "orgrebase.workspace-state.v1"} and state.get("stage") == "QUOTE_V3"
