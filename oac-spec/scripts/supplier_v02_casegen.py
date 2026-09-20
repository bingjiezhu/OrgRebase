#!/usr/bin/env python3
"""Deterministic JSON-recipe generator for the Supplier v0.2 seed-2 matrix.

The generator deliberately knows nothing about Supplier business semantics.  It
applies a small, closed JSON Pointer mutation language to admitted raw roots and
re-seals each result using the SealedResource/v1 raw-map digest rule.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

import rfc8785

RECIPE_API_VERSION = "oac.ctk.mutation-recipes/v0alpha1"
RECIPE_KIND = "DeterministicMutationRecipeSet"
GENERATOR_ID = "oac.supplier-v02.recipe-generator"
GENERATOR_VERSION = "v2"

_RECIPE_SET_FIELDS = {
    "apiVersion",
    "kind",
    "recipeSetId",
    "generatorId",
    "generatorVersion",
    "profileId",
    "profileVersion",
    "digestAlgorithm",
    "cases",
    "recipeSetDigest",
}
_CASE_FIELDS = {
    "caseId",
    "semanticClass",
    "publicSeed",
    "baseCaseId",
    "mutations",
}
_MUTATION_FIELDS = {
    "replace": {"operation", "target", "path", "value"},
    "remove": {"operation", "target", "path"},
    "reverse": {"operation", "target", "path"},
    "append": {"operation", "target", "path", "value"},
}


class CaseGenerationError(ValueError):
    """A recipe or base artifact is outside the deterministic generator contract."""


def artifact_digest(raw: bytes) -> str:
    """Return the repository-wide SHA-256 wire spelling."""

    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CaseGenerationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise CaseGenerationError(f"non-JSON numeric constant: {value}")


def _loads(raw: bytes, label: str) -> Any:
    try:
        return json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CaseGenerationError(f"{label} is not valid JSON: {exc}") from exc


def _closed(value: Mapping[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise CaseGenerationError(f"{label} is not closed")


def _non_blank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaseGenerationError(f"{label} must be a non-blank string")
    return value


def _binary64_projection(value: object) -> object:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise CaseGenerationError("generated JSON number is outside finite binary64")
        return number
    if isinstance(value, list):
        return [_binary64_projection(item) for item in value]
    if isinstance(value, dict):
        return {key: _binary64_projection(item) for key, item in value.items()}
    raise CaseGenerationError(f"generated value is outside JSON: {type(value).__name__}")


def _decode_pointer(pointer: object) -> tuple[str, ...]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise CaseGenerationError("mutation path must be a non-root JSON Pointer")
    tokens: list[str] = []
    for encoded in pointer[1:].split("/"):
        decoded: list[str] = []
        index = 0
        while index < len(encoded):
            character = encoded[index]
            if character != "~":
                decoded.append(character)
                index += 1
                continue
            if index + 1 == len(encoded) or encoded[index + 1] not in {"0", "1"}:
                raise CaseGenerationError(f"illegal JSON Pointer escape in {pointer}")
            decoded.append("~" if encoded[index + 1] == "0" else "/")
            index += 2
        tokens.append("".join(decoded))
    return tuple(tokens)


def _array_index(token: str, length: int, pointer: str) -> int:
    if token == "-" or not token.isascii() or not token.isdigit():
        raise CaseGenerationError(f"JSON Pointer array index is invalid: {pointer}")
    if len(token) > 1 and token.startswith("0"):
        raise CaseGenerationError(f"JSON Pointer array index has a leading zero: {pointer}")
    index = int(token)
    if index >= length:
        raise CaseGenerationError(f"JSON Pointer target is missing: {pointer}")
    return index


def _lookup(document: object, tokens: Sequence[str], pointer: str) -> object:
    current = document
    for token in tokens:
        if isinstance(current, dict):
            if token not in current:
                raise CaseGenerationError(f"JSON Pointer target is missing: {pointer}")
            current = current[token]
        elif isinstance(current, list):
            current = current[_array_index(token, len(current), pointer)]
        else:
            raise CaseGenerationError(f"JSON Pointer traverses a scalar: {pointer}")
    return current


def _parent(document: object, tokens: Sequence[str], pointer: str) -> tuple[object, str]:
    if not tokens:
        raise CaseGenerationError("mutation cannot replace or remove the document root")
    return _lookup(document, tokens[:-1], pointer), tokens[-1]


def _assign(parent: object, token: str, value: object, pointer: str) -> None:
    if isinstance(parent, dict):
        if token not in parent:
            raise CaseGenerationError(f"JSON Pointer target is missing: {pointer}")
        parent[token] = copy.deepcopy(value)
        return
    if isinstance(parent, list):
        parent[_array_index(token, len(parent), pointer)] = copy.deepcopy(value)
        return
    raise CaseGenerationError(f"JSON Pointer parent is a scalar: {pointer}")


def _remove(parent: object, token: str, pointer: str) -> None:
    if isinstance(parent, dict):
        if token not in parent:
            raise CaseGenerationError(f"JSON Pointer target is missing: {pointer}")
        del parent[token]
        return
    if isinstance(parent, list):
        del parent[_array_index(token, len(parent), pointer)]
        return
    raise CaseGenerationError(f"JSON Pointer parent is a scalar: {pointer}")


def _apply_mutation(
    snapshot: dict[str, Any],
    change: dict[str, Any],
    mutation: Mapping[str, Any],
    label: str,
) -> None:
    operation = mutation.get("operation")
    if not isinstance(operation, str) or operation not in _MUTATION_FIELDS:
        raise CaseGenerationError(f"{label} has an unsupported operation")
    _closed(mutation, _MUTATION_FIELDS[operation], label)
    target = mutation["target"]
    if target not in {"snapshot", "change"}:
        raise CaseGenerationError(f"{label} target must be snapshot or change")
    document = snapshot if target == "snapshot" else change
    pointer = mutation["path"]
    tokens = _decode_pointer(pointer)
    if tokens == ("digest",):
        raise CaseGenerationError(f"{label} cannot mutate the derived root digest")

    if operation == "replace":
        parent, token = _parent(document, tokens, pointer)
        _assign(parent, token, mutation["value"], pointer)
    elif operation == "remove":
        parent, token = _parent(document, tokens, pointer)
        _remove(parent, token, pointer)
    elif operation == "reverse":
        selected = _lookup(document, tokens, pointer)
        if not isinstance(selected, list):
            raise CaseGenerationError(f"reverse target is not an array: {pointer}")
        selected.reverse()
    else:
        selected = _lookup(document, tokens, pointer)
        if not isinstance(selected, list):
            raise CaseGenerationError(f"append target is not an array: {pointer}")
        selected.append(copy.deepcopy(mutation["value"]))


def _reseal(document: dict[str, Any], expected_kind: str) -> bytes:
    if document.get("kind") != expected_kind:
        raise CaseGenerationError(f"mutation changed the {expected_kind} resource kind")
    projection = {key: item for key, item in document.items() if key != "digest"}
    document["digest"] = artifact_digest(rfc8785.dumps(_binary64_projection(projection)))
    return rfc8785.dumps(_binary64_projection(document))


def admit_recipe_set(raw: bytes) -> dict[str, Any]:
    """Admit exact-JCS recipe bytes and verify the detached content digest."""

    value = _loads(raw, "recipe set")
    if not isinstance(value, dict):
        raise CaseGenerationError("recipe set must be an object")
    _closed(value, _RECIPE_SET_FIELDS, "recipe set")
    if (
        value["apiVersion"] != RECIPE_API_VERSION
        or value["kind"] != RECIPE_KIND
        or value["generatorId"] != GENERATOR_ID
        or value["generatorVersion"] != GENERATOR_VERSION
        or value["profileId"] != "oac.supplier.transfer"
        or value["profileVersion"] != "v0.2"
        or value["digestAlgorithm"] != "sha256-jcs-detached/v1"
    ):
        raise CaseGenerationError("recipe set coordinate is unsupported")
    _non_blank(value["recipeSetId"], "recipeSetId")
    projection = {key: item for key, item in value.items() if key != "recipeSetDigest"}
    if value["recipeSetDigest"] != artifact_digest(rfc8785.dumps(projection)):
        raise CaseGenerationError("recipeSetDigest mismatch")
    if raw != rfc8785.dumps(value) + b"\n":
        raise CaseGenerationError("recipe set must be exact JCS plus newline")

    cases = value["cases"]
    if not isinstance(cases, list) or not cases:
        raise CaseGenerationError("recipe set cases must be a non-empty array")
    case_ids: set[str] = set()
    public_seeds: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise CaseGenerationError(f"recipe case {index} must be an object")
        _closed(case, _CASE_FIELDS, f"recipe case {index}")
        case_id = _non_blank(case["caseId"], f"recipe case {index} caseId")
        public_seed = _non_blank(case["publicSeed"], f"recipe case {index} publicSeed")
        _non_blank(case["semanticClass"], f"recipe case {index} semanticClass")
        _non_blank(case["baseCaseId"], f"recipe case {index} baseCaseId")
        if case_id in case_ids:
            raise CaseGenerationError(f"duplicate generated caseId: {case_id}")
        if public_seed in public_seeds:
            raise CaseGenerationError(f"duplicate publicSeed: {public_seed}")
        case_ids.add(case_id)
        public_seeds.add(public_seed)
        mutations = case["mutations"]
        if not isinstance(mutations, list) or not mutations:
            raise CaseGenerationError(f"{case_id} mutations must be a non-empty array")
        for mutation_index, mutation in enumerate(mutations):
            if not isinstance(mutation, dict):
                raise CaseGenerationError(
                    f"{case_id} mutation {mutation_index} must be an object"
                )
            operation = mutation.get("operation")
            if not isinstance(operation, str) or operation not in _MUTATION_FIELDS:
                raise CaseGenerationError(
                    f"{case_id} mutation {mutation_index} has an unsupported operation"
                )
            _closed(
                mutation,
                _MUTATION_FIELDS[operation],
                f"{case_id} mutation {mutation_index}",
            )
            _decode_pointer(mutation.get("path"))
    return value


def generate_cases(
    recipe_set: Mapping[str, Any],
    frozen_cases: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Apply an admitted recipe set to frozen raw roots in declared order."""

    try:
        recipe_set = admit_recipe_set(rfc8785.dumps(dict(recipe_set)) + b"\n")
    except (CaseGenerationError, TypeError) as exc:
        raise CaseGenerationError(f"recipe set is not digest-bound: {exc}") from exc
    base_by_id: dict[str, Mapping[str, Any]] = {}
    for base in frozen_cases:
        base_id = base.get("caseId")
        if not isinstance(base_id, str) or not base_id or base_id in base_by_id:
            raise CaseGenerationError("base caseId values must be unique non-empty strings")
        base_by_id[base_id] = base

    generated: list[dict[str, Any]] = []
    output_ids: set[str] = set()
    for case_index, recipe in enumerate(recipe_set["cases"]):
        case_id = recipe["caseId"]
        if case_id in output_ids:
            raise CaseGenerationError(f"duplicate generated caseId: {case_id}")
        output_ids.add(case_id)
        base_id = recipe["baseCaseId"]
        if base_id not in base_by_id:
            raise CaseGenerationError(f"base case is missing: {base_id}")
        base = base_by_id[base_id]
        snapshot_raw = base.get("snapshot")
        change_raw = base.get("change")
        if not isinstance(snapshot_raw, bytes) or not isinstance(change_raw, bytes):
            raise CaseGenerationError(f"base case {base_id} does not contain raw roots")
        snapshot = _loads(snapshot_raw, f"{base_id} snapshot")
        change = _loads(change_raw, f"{base_id} change")
        if not isinstance(snapshot, dict) or not isinstance(change, dict):
            raise CaseGenerationError(f"base case {base_id} roots must be objects")
        for mutation_index, mutation in enumerate(recipe["mutations"]):
            _apply_mutation(
                snapshot,
                change,
                mutation,
                f"{case_id} mutation {mutation_index}",
            )
        generated_snapshot = _reseal(snapshot, "OrganizationSnapshot")
        generated_change = _reseal(change, "SemanticChangeSet")
        generated.append(
            {
                "caseId": case_id,
                "semanticClass": recipe["semanticClass"],
                "publicSeed": recipe["publicSeed"],
                "baseCaseId": base_id,
                "snapshot": generated_snapshot,
                "change": generated_change,
                "inputDigest": artifact_digest(
                    generated_snapshot + b"\x00" + generated_change
                ),
                "recipeIndex": case_index,
            }
        )
    return generated


__all__ = [
    "GENERATOR_ID",
    "GENERATOR_VERSION",
    "CaseGenerationError",
    "admit_recipe_set",
    "artifact_digest",
    "generate_cases",
]
