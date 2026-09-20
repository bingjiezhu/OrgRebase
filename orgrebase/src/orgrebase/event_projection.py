"""Fixed-size projections derived from the canonical event chain."""

from __future__ import annotations

from typing import Any

ZERO_DIGEST = "sha256:" + "0" * 64
_PRELUDE_TYPES = frozenset({"FIXTURE_LOADED", "WORKSPACE_SEED_LOADED", "WORKSPACE_CHANGE_REGISTERED"})


def empty_scopes() -> dict[str, Any]:
    return {
        "prelude_open": True,
        "last_scope": None,
        "transitions": 0,
        **{name: {"events": 0, "first": None, "last": None} for name in ("prelude", "quote", "oac")},
    }


def append_scope(scopes: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """The same reducer serves transactional append and explicit migration replay."""
    result = dict(scopes)
    if result["prelude_open"] and record["event_type"] in _PRELUDE_TYPES:
        kind = "prelude"
    else:
        result["prelude_open"] = False
        kind = "oac" if record["event_type"].startswith("OAC_") else "quote"
        if result["last_scope"] is not None and result["last_scope"] != kind:
            result["transitions"] += 1
        result["last_scope"] = kind
    anchor = {key: record[key] for key in ("sequence_no", "previous_digest", "event_digest")}
    bucket = scopes[kind]
    result[kind] = {"events": bucket["events"] + 1, "first": bucket["first"] or anchor, "last": anchor}
    return result


def scope_view(
    scopes: dict[str, Any], *, events: int, head_digest: str, status: str = "PASS"
) -> dict[str, Any]:
    quote, oac, prelude = (scopes[name] for name in ("quote", "oac", "prelude"))
    if not oac["events"]:
        layout = "QUOTE_ONLY"
    elif not quote["events"]:
        layout = "OAC_PREFIX_QUOTE_SUFFIX"
    elif scopes["transitions"] == 1:
        layout = "QUOTE_PREFIX_OAC_SUFFIX" if scopes["last_scope"] == "oac" else "OAC_PREFIX_QUOTE_SUFFIX"
    else:
        layout = "INTERLEAVED"
    contiguous = layout != "INTERLEAVED"

    def anchor(bucket, position, key):
        return bucket[position][key] if bucket[position] is not None else None

    quote_definition = (
        "NO_QUOTE_BUSINESS_EVENTS_OBSERVED"
        if not quote["events"]
        else {
            "QUOTE_ONLY": "COMPLETE_NON_OAC_CHAIN",
            "QUOTE_PREFIX_OAC_SUFFIX": "CONTIGUOUS_NON_OAC_PREFIX",
            "OAC_PREFIX_QUOTE_SUFFIX": "CONTIGUOUS_NON_OAC_SUFFIX",
            "INTERLEAVED": "NON_OAC_RECORDS_NOT_A_CONTIGUOUS_WINDOW",
        }[layout]
    )
    return {
        "schema_version": "orgrebase.workspace-event-scopes.v2",
        "layout": layout,
        "quote_business": {
            "status": "NOT_OBSERVED" if not quote["events"] else status if contiguous else "NOT_CONTIGUOUS",
            "events": quote["events"],
            "head_digest": (anchor(quote, "last", "event_digest") or ZERO_DIGEST) if contiguous else None,
            "first_sequence_no": anchor(quote, "first", "sequence_no") if contiguous else None,
            "last_sequence_no": anchor(quote, "last", "sequence_no") if contiguous else None,
            "start_anchor_digest": (anchor(quote, "first", "previous_digest") or ZERO_DIGEST)
            if contiguous
            else None,
            "definition": quote_definition,
        },
        "workspace_global": {
            "status": status,
            "events": events,
            "head_digest": head_digest,
            "definition": "FULL_APPEND_ONLY_WORKSPACE_CHAIN",
        },
        "workspace_prelude": {
            "status": status,
            "events": prelude["events"],
            "first_sequence_no": anchor(prelude, "first", "sequence_no"),
            "last_sequence_no": anchor(prelude, "last", "sequence_no"),
            "head_digest": anchor(prelude, "last", "event_digest"),
            "definition": "LEADING_WORKSPACE_INITIALIZATION_NOT_QUOTE_BUSINESS",
        },
        "oac_adaptation": {
            "status": status if contiguous else "INTERLEAVED",
            "events": oac["events"],
            "first_sequence_no": anchor(oac, "first", "sequence_no") if contiguous else None,
            "last_sequence_no": anchor(oac, "last", "sequence_no") if contiguous else None,
            "start_anchor_digest": anchor(oac, "first", "previous_digest") if contiguous else None,
            "head_digest": anchor(oac, "last", "event_digest") if contiguous else None,
            "definition": "OAC_GOVERNANCE_EVENTS_ZERO_QUOTE_MUTATION_AUTHORITY",
        },
        "relationship": {
            "event_layout": layout,
            "honest_contiguous_windows_published": contiguous,
            "quote_is_contiguous_prefix": layout in {"QUOTE_ONLY", "QUOTE_PREFIX_OAC_SUFFIX"},
            "quote_is_contiguous_suffix": layout in {"QUOTE_ONLY", "OAC_PREFIX_QUOTE_SUFFIX"},
            "oac_events_are_prefix": layout == "OAC_PREFIX_QUOTE_SUFFIX",
            "oac_events_are_suffix": layout in {"QUOTE_ONLY", "QUOTE_PREFIX_OAC_SUFFIX"},
        },
    }


def event_subject(payload: dict[str, Any]) -> str | None:
    for key in ("kind", "event_id", "run_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def validate_scopes(scopes: dict[str, Any], *, events: int, head_digest: str) -> None:
    from orgrebase.domain import IntegrityError

    try:
        buckets = [scopes[name] for name in ("prelude", "quote", "oac")]
        if (
            set(scopes) != set(empty_scopes())
            or type(scopes["prelude_open"]) is not bool
            or scopes["last_scope"] not in {None, "quote", "oac"}
            or type(scopes["transitions"]) is not int
            or not 0 <= scopes["transitions"] <= events
            or any(type(bucket["events"]) is not int or bucket["events"] < 0 for bucket in buckets)
            or sum(bucket["events"] for bucket in buckets) != events
        ):
            raise ValueError
        for bucket in buckets:
            if bucket["events"] == 0:
                if bucket["first"] is not None or bucket["last"] is not None:
                    raise ValueError
            elif not 1 <= bucket["first"]["sequence_no"] <= bucket["last"]["sequence_no"] <= events:
                raise ValueError
        last = max(
            (bucket["last"] for bucket in buckets if bucket["events"]),
            key=lambda item: item["sequence_no"],
            default=None,
        )
        if (last is None and (events != 0 or head_digest != ZERO_DIGEST)) or (
            last is not None and (last["sequence_no"] != events or last["event_digest"] != head_digest)
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise IntegrityError("STATE_STORE_EVENT_PROJECTION_INVALID") from exc
