"""Explicit test-only compatibility calls for legacy Pattern fixtures."""

from __future__ import annotations

from typing import Any


def invoke_pattern_fixture(service: Any, *args: Any, **kwargs: Any):
    """Call the private retained-fixture path; never imported by product code."""

    return service._invoke_admitted(*args, **kwargs)
