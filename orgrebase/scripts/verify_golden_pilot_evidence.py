#!/usr/bin/env python3
"""Verify a frozen Golden Pilot evidence pack without importing OrgRebase."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from fractions import Fraction
from pathlib import Path
from typing import Any

EXCLUDED_INDEX_FILES = {"manifest.json", "verification.json"}
OLLAMA_MODEL_ID = "qwen2.5:3b"
OLLAMA_MODEL_DIGEST = (
    "357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b"
)
OLLAMA_RECEIPT_VERSION = f"ollama-manifest:{OLLAMA_MODEL_DIGEST}"
SUPPORTED_VERTEX_MODEL_IDS = frozenset(
    {"gemini-3.7-flash", "gemini-3.8-flash"}
)


def _configured_vertex_model_id() -> str | None:
    configured = os.environ.get("ORGREBASE_VERTEX_MODEL_ID")
    if configured is None:
        return None
    model_id = configured.strip()
    if model_id not in SUPPORTED_VERTEX_MODEL_IDS:
        supported = ",".join(sorted(SUPPORTED_VERTEX_MODEL_IDS))
        raise RuntimeError(
            f"UNSUPPORTED_VERTEX_MODEL_ID:{model_id or '<empty>'};supported={supported}"
        )
    return model_id


VERTEX_MODEL_ID = _configured_vertex_model_id()
MODEL_AUTHORITY = "ADVISORY_ONLY_DETERMINISTIC_REVIEWER_AUTHORITATIVE"
MODEL_ADVISORY_MATCHED = "MODEL_ADVISORY_MATCHED_DETERMINISTIC_VERIFIER"
MODEL_ADVISORY_OVERRIDDEN = "MODEL_ADVISORY_OVERRIDDEN_BY_DETERMINISTIC_VERIFIER"
EXPECTED_REVIEWER_DECISIONS = {
    1: {
        "verdict": "REPLAN",
        "missing_domains": ["finance"],
        "reason_codes": [
            "DOMAIN_NOT_PASS:finance",
            "DOMAIN_SLOT_SET_MISMATCH:finance",
            "SLOT_CARDINALITY_MISMATCH:finance:price_band",
        ],
    },
    2: {
        "verdict": "PASS",
        "missing_domains": [],
        "reason_codes": ["EXACT_SLOT_PROVENANCE_AND_EFFECT_BOUNDARY_PASS"],
    },
}
VERTEX_CLAIM_BOUNDARY = (
    "LIVE_VERTEX_STRUCTURED_ADVISORY_NOT_DETERMINISTIC_AUTHORITY_"
    "NOT_CANONICAL_WRITE"
)
EXPERIENCE_TARGET_SKILL = "structured-domain-handoff"
EXPERIENCE_STEWARD_ID = "human:skill-steward"
EXPERIENCE_PARTITIONS = {
    "REPLAY",
    "HELD_OUT",
    "NEGATIVE_TRANSFER",
    "PERMISSION",
    "INJECTION",
    "MALFORMED",
    "RESOURCE_OR_DEADLINE",
    "CANARY",
}
FORBIDDEN_RUNTIME_DATABASE_NAMES = {
    "workspace.sqlite3",
    "workspace.sqlite3-wal",
    "workspace.sqlite3-shm",
}
PRIVATE_TASK_INTAKE_MARKERS = (
    b'"work_description"',
    b"task-intake-private-work-description:",
    b"workspace-task-intake-work-description-record",
)
PRIVATE_JSON_FIELD_PATTERN = re.compile(
    rb'"(?:prompt|raw_prompt|task_text|customer_email|authorization_header|api_key|'
    rb'access_token|refresh_token|client_secret|password|secret)"\s*:'
)
MAX_PUBLIC_EVIDENCE_FILE_BYTES = 64 * 1024 * 1024
FORBIDDEN_PUBLIC_SECRET_PATTERNS = (
    re.compile(rb"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
)
LEGACY_PUBLIC_EVENT_TYPES = (
    "WORKSPACE_SEED_LOADED",
    "WORKSPACE_TASK_COMMITTED",
    "GOLDEN_COMPETITION_ACCEPTED",
    "TOOL_INVOKED",
    "WORKSPACE_CHANGE_PREVIEWED",
    "WORKSPACE_CHANGE_APPROVED",
    "REBASE_APPLIED",
    "WORKSPACE_CHANGE_OUTCOME_RECORDED",
    "WORKSPACE_CHANGE_PREVIEWED",
    "WORKSPACE_CHANGE_APPROVED",
    "REBASE_APPLIED",
    "WORKSPACE_CHANGE_OUTCOME_RECORDED",
)
OAC_BOUND_PUBLIC_EVENT_TYPES = (
    "WORKSPACE_SEED_LOADED",
    "OAC_QUOTE_ADAPTATION_PREPARED",
    "OAC_QUOTE_ADAPTER_ADMITTED",
    "OAC_ADAPTER_ACTIVATION_CONSUMED",
    "WORKSPACE_TASK_COMMITTED",
    "GOLDEN_COMPETITION_ACCEPTED",
    "WORKSPACE_TASK_INTAKE_BOUND",
    "TOOL_INVOKED",
    "WORKSPACE_CHANGE_PREVIEWED",
    "WORKSPACE_CHANGE_APPROVED",
    "REBASE_APPLIED",
    "WORKSPACE_CHANGE_OUTCOME_RECORDED",
    "WORKSPACE_CHANGE_PREVIEWED",
    "WORKSPACE_CHANGE_APPROVED",
    "REBASE_APPLIED",
    "WORKSPACE_CHANGE_OUTCOME_RECORDED",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _priced_quote_failures(quote_payload: Any, candidate_values: dict[str, Any]) -> list[str]:
    """Check the priced formation claim independently with rational arithmetic.

    This is an evidence oracle only: it never constructs or commits a business
    quote and deliberately imports neither the renderer nor the pricing engine.
    """
    if not isinstance(quote_payload, dict):
        return ["FORMATION_PRICING_INPUT_INVALID"]
    present = ("quote_basket" in candidate_values, "pricing_policy" in candidate_values,
               "pricing" in quote_payload)
    if not any(present):
        return []
    if not all(present):
        return ["FORMATION_PRICING_INPUT_INCOMPLETE"]

    def text(value: Any) -> bool:
        return isinstance(value, str) and 0 < len(value) <= 2000 and bool(value.strip())

    def integer(value: Any, lower: int, upper: int) -> bool:
        return type(value) is int and lower <= value <= upper

    def rounded(value: Fraction) -> int:
        whole, remainder = divmod(value.numerator, value.denominator)
        return whole + int(2 * remainder >= value.denominator)

    try:
        basket = candidate_values["quote_basket"]
        policy = candidate_values["pricing_policy"]
        pricing = quote_payload["pricing"]
        if not all(isinstance(item, dict) for item in (basket, policy, pricing)):
            raise ValueError("Pricing inputs and output must be objects")
        if any(len(_canonical(item)) > 65_536 for item in (basket, policy)):
            raise ValueError("Pricing input size limit")
        if set(basket) != {"currency", "items", "source_ref"}:
            raise ValueError("Basket fields")
        if set(policy) - {"discount_bps", "tax_bps", "tax_label", "tax_mode", "rounding", "source_ref"}:
            raise ValueError("Policy fields")
        policy = {"discount_bps": 0, "tax_bps": 0, "tax_mode": "EXCLUSIVE", "rounding": "HALF_UP", **policy}
        if (not integer(policy["discount_bps"], 0, 10000)
                or not integer(policy["tax_bps"], 0, 10000)
                or policy["tax_mode"] != "EXCLUSIVE" or policy["rounding"] != "HALF_UP"
                or not text(policy["tax_label"]) or not text(policy["source_ref"])
                or not text(basket["source_ref"])):
            raise ValueError("Policy values")
        currency = basket["currency"]
        units = {"GBP": 2, "USD": 2, "EUR": 2, "CNY": 2, "JPY": 0, "KWD": 3}
        if (not isinstance(currency, str) or currency not in units
                or quote_payload.get("currency") != currency or candidate_values.get("currency") != currency):
            raise ValueError("Currency binding")
        minor_units = units[currency]
        scale = 10 ** minor_units

        def amount(value: int) -> str:
            if minor_units == 0:
                return str(value)
            return f"{value // scale}.{value % scale:0{minor_units}d}"

        items = basket["items"]
        if not isinstance(items, list) or not 1 <= len(items) <= 200:
            raise ValueError("Basket items")
        line_ids: set[str] = set()
        expected_lines = []
        subtotal = 0
        for item in items:
            if (not isinstance(item, dict)
                    or set(item) != {"line_id", "sku", "description", "quantity", "unit_price"}
                    or not all(text(item[key]) for key in ("line_id", "sku", "description"))
                    or not integer(item["quantity"], 1, 1_000_000)
                    or not isinstance(item["unit_price"], str) or not 1 <= len(item["unit_price"]) <= 31
                    or re.fullmatch(r"(?:0|[0-9]{1,12})(?:\.[0-9]{1,18})?", item["unit_price"]) is None
                    or item["line_id"] in line_ids):
                raise ValueError("Line values")
            line_ids.add(item["line_id"])
            line_amount = rounded(Fraction(item["unit_price"]) * item["quantity"] * scale)
            subtotal += line_amount
            expected_lines.append({**item, "line_total": amount(line_amount)})
        discount = rounded(Fraction(subtotal * policy["discount_bps"], 10000))
        net = subtotal - discount
        tax = rounded(Fraction(net * policy["tax_bps"], 10000))
        expected = {
            "currency": currency, "minor_units": minor_units, "lines": expected_lines,
            "subtotal": amount(subtotal), "discount_rate_bps": policy["discount_bps"],
            "discount_amount": amount(discount), "net_amount": amount(net),
            "tax_rate_bps": policy["tax_bps"], "tax_label": policy["tax_label"],
            "tax_amount": amount(tax), "total": amount(net + tax),
            "basket_source_ref": basket["source_ref"], "policy_source_ref": policy["source_ref"],
            "basket_digest": _digest(basket), "policy_digest": _digest(policy),
            "rounding_description": (
                "HALF_UP to currency minor units: each quantity times unit price first; "
                "sum rounded lines; round subtotal times discount_bps / 10000 once; "
                "subtract discount; round net times tax_bps / 10000 once; add net and tax."
            ),
        }
        # Canonical bytes distinguish bool/float coercions as well as values.
        if _canonical(pricing) != _canonical(expected):
            return ["FORMATION_PRICING_CAUSALITY"]
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return ["FORMATION_PRICING_INPUT_INVALID"]
    return []


def _pack_tree_failures(root: Path) -> list[str]:
    candidate = root.absolute()
    if candidate.is_symlink():
        return ["PACK_ROOT_SYMLINK_FORBIDDEN"]
    try:
        root_stat = candidate.lstat()
    except OSError:
        return ["PACK_ROOT_MISSING"]
    if not stat.S_ISDIR(root_stat.st_mode):
        return ["PACK_ROOT_NOT_DIRECTORY"]
    resolved_root = candidate.resolve()
    failures: list[str] = []
    for current, directory_names, file_names in os.walk(candidate, followlinks=False):
        current_path = Path(current)
        for name in sorted((*directory_names, *file_names), key=str.encode):
            path = current_path / name
            relative = path.relative_to(candidate).as_posix()
            try:
                item_stat = path.lstat()
            except OSError:
                failures.append(f"PACK_PATH_UNREADABLE:{relative}")
                continue
            if stat.S_ISLNK(item_stat.st_mode):
                failures.append(f"PACK_SYMLINK_FORBIDDEN:{relative}")
                if name in directory_names:
                    directory_names.remove(name)
                continue
            if not path.resolve().is_relative_to(resolved_root):
                failures.append(f"PACK_PATH_ESCAPE:{relative}")
            if name in directory_names:
                if not stat.S_ISDIR(item_stat.st_mode):
                    failures.append(f"PACK_DIRECTORY_INVALID:{relative}")
            elif not stat.S_ISREG(item_stat.st_mode):
                failures.append(f"PACK_SPECIAL_FILE_FORBIDDEN:{relative}")
            elif item_stat.st_size > MAX_PUBLIC_EVIDENCE_FILE_BYTES:
                failures.append(f"PACK_FILE_TOO_LARGE:{relative}")
    return failures


def _read_regular_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        item_stat = os.fstat(descriptor)
        if not stat.S_ISREG(item_stat.st_mode):
            raise ValueError(f"PACK_SPECIAL_FILE_FORBIDDEN:{path.name}")
        if item_stat.st_size > MAX_PUBLIC_EVIDENCE_FILE_BYTES:
            raise ValueError(f"PACK_FILE_TOO_LARGE:{path.name}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(MAX_PUBLIC_EVIDENCE_FILE_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_PUBLIC_EVIDENCE_FILE_BYTES:
        raise ValueError(f"PACK_FILE_TOO_LARGE:{path.name}")
    return payload


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(_read_regular_bytes(path)).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(_read_regular_bytes(path).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(path.name)
    return value


def _load_json(path: Path) -> Any:
    return json.loads(_read_regular_bytes(path).decode("utf-8"))


def _quote_skill_case_identity(evaluation: dict[str, Any], run_id: str) -> bool:
    """Independently recognize exact retained v1 and explicitly bound v2 suites."""
    expected = [
        ("replay", "REPLAY"), ("held_out", "HELD_OUT"),
        ("negative_transfer", "NEGATIVE_TRANSFER"), ("permission", "PERMISSION"),
        ("injection", "INJECTION"), ("malformed", "MALFORMED"),
        ("resource_or_deadline", "RESOURCE_OR_DEADLINE"), ("canary", "CANARY"),
    ]
    premise, cases = evaluation.get("premise_lock"), evaluation.get("case_results")
    if not isinstance(premise, dict) or not isinstance(cases, list):
        return False
    markers = {"qualification_suite_revision", "qualification_suite_digest"} & premise.keys()
    if markers:
        if (premise.get("qualification_suite_revision") != "orgrebase.quote-skill-qualification.v2"
                or premise.get("qualification_suite_digest")
                != "sha256:7b272a0f3da13f2c8d7db1bd87afd797cbca28e4e3e5c569120a8e7f54e19ea7"):
            return False
        expected.append(("domain_substitution", "MALFORMED"))
    identities = [(item.get("case_ref"), item.get("partition"))
                  for item in cases if isinstance(item, dict)]
    return len(cases) == len(expected) and sorted(identities, key=str) == sorted(
        [(f"{run_id}:quote-compose:{name}", partition) for name, partition in expected], key=str)


def _sealed(value: dict[str, Any]) -> bool:
    return value.get("digest") == _digest({key: item for key, item in value.items() if key != "digest"})


def _valid_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _model_advisory_authority_valid(
    output: dict[str, Any],
    *,
    expected_decision: Any,
    phase: int,
) -> bool:
    """Accept a model match or an explicitly recorded deterministic override."""

    advisory = output.get("model_advisory")
    receipt = output.get("model_receipt")
    decision = output.get("decision")
    expected_for_phase = EXPECTED_REVIEWER_DECISIONS.get(phase)
    if (
        not isinstance(advisory, dict)
        or not isinstance(receipt, dict)
        or not isinstance(decision, dict)
        or decision != expected_decision
        or decision != expected_for_phase
        or advisory != receipt.get("value")
    ):
        return False
    advisory_missing = advisory.get("missing_domains")
    advisory_reasons = advisory.get("reason_codes")
    if (
        advisory.get("verdict") not in {"PASS", "REPLAN"}
        or not isinstance(advisory_missing, list)
        or any(not isinstance(item, str) for item in advisory_missing)
        or len(advisory_missing) != len(set(advisory_missing))
        or not isinstance(advisory_reasons, list)
        or any(not isinstance(item, str) for item in advisory_reasons)
    ):
        return False
    accepted = (
        advisory.get("verdict") == decision.get("verdict")
        and set(advisory_missing) == set(decision.get("missing_domains", []))
    )
    expected_disposition = (
        MODEL_ADVISORY_MATCHED if accepted else MODEL_ADVISORY_OVERRIDDEN
    )
    return (
        output.get("model_advisory_accepted") is accepted
        and output.get("model_advisory_disposition_reason") == expected_disposition
    )


def _manager_source_bindings(worker_input: dict[str, Any]) -> dict[str, Any]:
    sources = worker_input.get("source_values")
    if not isinstance(sources, dict):
        raise ValueError("WORKER_SOURCE_VALUES")
    bindings: dict[str, Any] = {}
    for slot, value in sorted(sources.items()):
        if not isinstance(value, dict):
            raise ValueError(f"WORKER_SOURCE_VALUE:{slot}")
        bindings[str(slot)] = {
            "object_ref": value["object_ref"],
            "value_digest": _digest(value["value"]),
            "semantic_kind": value["semantic_kind"],
            "authority_ref": value["authority_ref"],
            "source_id": value["source_id"],
            "source_version": value["source_version"],
            "source_digest": value["source_digest"],
            "sensitivity": value["sensitivity"],
        }
    return bindings


def _manager_expected_binding(
    *,
    worker_task_id: str,
    worker_binding: dict[str, Any],
    worker_input: dict[str, Any],
    run_id: str,
    correlation_id: str,
    oac_bound: bool,
) -> dict[str, Any]:
    """Recompute the exact Manager-side binding sent to the Reviewer."""

    assignee = str(worker_binding.get("assignee", ""))
    expected = {
        "task_id": worker_task_id,
        "worker_id": assignee.removeprefix("@").split(":", 1)[0],
        "run_id": run_id,
        "correlation_id": correlation_id,
        "attempt": worker_binding["attempt"],
        "input_digest": _digest(worker_input),
        "projection_digest": worker_input["projection"]["digest"],
        "task_purpose": worker_binding["task_purpose"],
        "source_bindings": _manager_source_bindings(worker_input),
        "tool_receipt_digest": worker_input.get(
            "supplemental_tool_receipt_digest"
        ),
        "tool_result_digest": worker_input.get(
            "supplemental_tool_result_digest"
        ),
    }
    if oac_bound:
        expected.update(
            {
                "agentteams_execution_plan_digest": worker_binding[
                    "agentteams_execution_plan_digest"
                ],
                "formation_receipt_digest": worker_binding[
                    "formation_receipt_digest"
                ],
                "formation_receipt_id": worker_binding[
                    "formation_receipt_id"
                ],
                "logical_plan_task_digest": worker_binding[
                    "logical_plan_task_digest"
                ],
                "logical_plan_task_id": worker_binding[
                    "logical_plan_task_id"
                ],
            }
        )
    return expected


def _contains_key(value: Any, names: set[str]) -> bool:
    if isinstance(value, dict):
        return any(str(key).lower() in names or _contains_key(item, names) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_key(item, names) for item in value)
    return False


def _vertex_model_id_from_summary(summary: dict[str, Any]) -> str | None:
    """Derive one allowed Vertex model from the sealed attempt evidence."""

    if summary.get("model_provider") != "vertex-ai":
        return None
    attempts = summary.get("reviewer_model_attempts")
    if (
        not isinstance(attempts, list)
        or len(attempts) != 2
        or not all(isinstance(item, dict) and _sealed(item) for item in attempts)
        or {item.get("phase") for item in attempts} != {1, 2}
    ):
        return None
    model_ids: set[str] = set()
    for attempt in attempts:
        requested = attempt.get("requested_model_id")
        observed = attempt.get("observed_model_version")
        if (
            attempt.get("provider") != "vertex-ai"
            or not isinstance(requested, str)
            or requested != observed
            or requested not in SUPPORTED_VERTEX_MODEL_IDS
        ):
            return None
        model_ids.add(requested)
    if len(model_ids) != 1:
        return None
    model_id = next(iter(model_ids))
    if VERTEX_MODEL_ID is not None and model_id != VERTEX_MODEL_ID:
        return None
    return model_id


def _verify_causal_evidence(
    root: Path,
    *,
    summary: dict[str, Any],
    run_id: str,
    correlation_id: str,
) -> list[str]:
    """Verify raw causal bindings instead of trusting the compact summary.

    This verifier intentionally uses only the Python standard library.  It does
    not re-run OrgRebase business code; it recomputes content digests and checks
    the independently persisted AgentTeams, process, Tool, Reviewer, Skill and
    Formation artifacts against one another.
    """

    failures: list[str] = []

    def fail(code: str) -> None:
        if code not in failures:
            failures.append(code)

    golden = root / "golden-run"
    try:
        actions = _load_json(golden / "agentteams" / "action-journal.json")
        process_receipts = _load_json(golden / "process-receipts.json")
        tool = _load(golden / "tool" / "invocation.json")
        skill_package = _load(golden / "skill" / "package.json")
        skill_evaluation = _load(golden / "skill" / "evaluation.json")
        skill_release_ledger = _load_json(
            golden / "skill" / "release-ledger.json"
        )
        skill_input = _load(golden / "skill" / "input.json")
        skill_result = _load(golden / "skill" / "result.json")
        skill_receipt = _load(golden / "skill" / "receipt.json")
        prepared = _load(golden / "prepared-formation-bundle.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"CAUSAL_EVIDENCE_LOAD:{type(exc).__name__}"]
    if (
        not isinstance(actions, list)
        or not isinstance(process_receipts, list)
        or not isinstance(skill_release_ledger, list)
    ):
        return ["CAUSAL_EVIDENCE_SHAPE"]

    # AgentTeams journal and raw MCP wrappers.
    action_by_digest: dict[str, dict[str, Any]] = {}
    raw_by_action_digest: dict[str, dict[str, Any]] = {}
    payload_by_action_digest: dict[str, dict[str, Any]] = {}
    action_digests: list[str] = []
    for sequence, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            fail("AGENTTEAMS_ACTION_SHAPE")
            continue
        if action.get("sequence") != sequence:
            fail("AGENTTEAMS_ACTION_SEQUENCE")
        if not _sealed(action):
            fail("AGENTTEAMS_ACTION_DIGEST")
        digest = action.get("digest")
        if not isinstance(digest, str) or digest in action_by_digest:
            fail("AGENTTEAMS_ACTION_DIGEST_SET")
            continue
        action_by_digest[digest] = action
        action_digests.append(digest)
        try:
            raw_ref = str(action["raw_ref"])
            raw_path = (golden / "agentteams" / raw_ref).resolve()
            raw_root = (golden / "agentteams" / "raw-mcp").resolve()
            if not raw_path.is_relative_to(raw_root):
                raise ValueError("RAW_REF_OUTSIDE_ROOT")
            raw = _load(raw_path)
            content = raw["response"]["content"]
            payload = json.loads(content[0]["text"])
            if not isinstance(payload, dict):
                raise ValueError("RAW_PAYLOAD_SHAPE")
            raw_by_action_digest[digest] = raw
            payload_by_action_digest[digest] = payload
            if (
                raw.get("sequence") != sequence
                or raw.get("action") != action.get("action")
                or raw.get("tool") != action.get("tool")
                or raw.get("key") != action.get("key")
                or action.get("request_digest") != _digest(raw["request"])
                or action.get("response_digest") != _digest(raw["response"])
                or action.get("payload_digest") != _digest(payload)
                or payload.get("action") != action.get("action")
                or payload.get("tool") != action.get("tool")
                or payload.get("ok") is not True
            ):
                fail("AGENTTEAMS_RAW_ACTION_BINDING")
        except (OSError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
            fail("AGENTTEAMS_RAW_ACTION_BINDING")
    if (
        summary.get("agentteams_action_count") != len(actions)
        or summary.get("agentteams_action_digests") != action_digests
        or not actions
        or actions[-1].get("action") != "complete_project"
        or actions[-1].get("status") != "completed"
    ):
        fail("AGENTTEAMS_SUMMARY_BINDING")

    # When the run declares OAC-bound formation, independently bind the two
    # admitted roots, the compiled execution plan and the runtime topology.
    oac_plan: dict[str, Any] | None = None
    oac_plan_task_by_id: dict[str, dict[str, Any]] = {}
    if summary.get("agentteams_execution_plan_digest") is not None:
        try:
            formation_root = _load(
                golden / "inputs" / "task-formation-decision-receipt.json"
            )
            context_root = _load(
                golden / "inputs" / "task-agent-context-envelope.json"
            )
            oac_plan = _load(golden / "agentteams" / "execution-plan.json")
            execution_envelope = _load(golden / "execution-envelope.json")
        except (OSError, ValueError, json.JSONDecodeError):
            fail("OAC_EXECUTION_ROOT_FILE")
        else:
            plan_tasks = oac_plan.get("tasks")
            if not isinstance(plan_tasks, list):
                fail("OAC_EXECUTION_PLAN_TASKS")
                plan_tasks = []
            for task in plan_tasks:
                if not isinstance(task, dict) or not _sealed(task):
                    fail("OAC_EXECUTION_PLAN_TASK_DIGEST")
                    continue
                task_id = task.get("task_id")
                if not isinstance(task_id, str) or task_id in oac_plan_task_by_id:
                    fail("OAC_EXECUTION_PLAN_TASK_SET")
                    continue
                oac_plan_task_by_id[task_id] = task
            formation_domains = formation_root.get("selected_domain_ids")
            context_bindings = context_root.get("domain_bindings")
            context_domains = (
                [item.get("domain_id") for item in context_bindings]
                if isinstance(context_bindings, list)
                and all(isinstance(item, dict) for item in context_bindings)
                else None
            )
            plan_domains = oac_plan.get("selected_domain_ids")
            if (
                not _sealed(formation_root)
                or not _sealed(context_root)
                or not _sealed(oac_plan)
                or formation_root.get("digest")
                != summary.get("task_formation_decision_receipt_digest")
                or context_root.get("digest") != summary.get("context_envelope_digest")
                or oac_plan.get("digest")
                != summary.get("agentteams_execution_plan_digest")
                or context_root.get("task_formation_decision_receipt_digest")
                != formation_root.get("digest")
                or context_root.get("admitted_organizational_intent_digest")
                != formation_root.get("organizational_demand_digest")
                or oac_plan.get("formation_receipt_digest")
                != formation_root.get("digest")
                or oac_plan.get("context_envelope_digest")
                != context_root.get("digest")
                or formation_root.get("task_ref") != context_root.get("task_ref")
                or formation_root.get("task_ref") != oac_plan.get("task_ref")
                or formation_root.get("task_digest") != context_root.get("task_digest")
                or formation_root.get("task_digest") != oac_plan.get("task_digest")
                or formation_root.get("coalition_plan_ref")
                != context_root.get("coalition_plan_ref")
                or formation_root.get("coalition_plan_ref")
                != oac_plan.get("coalition_plan_ref")
                or formation_root.get("coalition_plan_digest")
                != context_root.get("coalition_plan_digest")
                or formation_root.get("coalition_plan_digest")
                != oac_plan.get("coalition_plan_digest")
                or formation_domains != context_domains
                or formation_domains != plan_domains
                or summary.get("planned_domain_ids") != plan_domains
                or summary.get("actual_agentteams_domain_ids") != plan_domains
                or summary.get("topology_match") is not True
                or summary.get("context_freshness_basis") != "LOGICAL_EVENT_TIME"
                or execution_envelope.get("task_formation_decision_receipt_digest")
                != formation_root.get("digest")
                or execution_envelope.get("context_envelope_digest")
                != context_root.get("digest")
                or execution_envelope.get("agentteams_execution_plan_digest")
                != oac_plan.get("digest")
            ):
                fail("OAC_EXECUTION_ROOT_BINDING")

    # Manager-owned task bindings and the native lifecycle around every task.
    task_bindings = summary.get("task_bindings")
    binding_by_task: dict[str, dict[str, Any]] = {}
    if not isinstance(task_bindings, list):
        fail("TASK_BINDINGS_SHAPE")
        task_bindings = []
    lifecycle = (
        ("delegate_action_digest", "delegate_task"),
        ("ack_action_digest", "ack_task"),
        ("submit_action_digest", "submit_task"),
        ("check_action_digest", "check_task"),
        ("accept_action_digest", "accept_task_result"),
    )
    for binding in task_bindings:
        if not isinstance(binding, dict):
            fail("TASK_BINDING_SHAPE")
            continue
        task_id = binding.get("task_id")
        if not isinstance(task_id, str) or task_id in binding_by_task:
            fail("TASK_BINDING_CARDINALITY")
            continue
        binding_by_task[task_id] = binding
        try:
            frozen_binding = _load(golden / "agentteams" / "bindings" / f"{task_id}.json")
        except (OSError, ValueError, json.JSONDecodeError):
            fail("TASK_BINDING_FILE")
            continue
        if binding != frozen_binding or not _sealed(binding):
            fail("TASK_BINDING_FILE")
        if (
            binding.get("run_id") != run_id
            or binding.get("correlation_id") != correlation_id
            or binding.get("candidate_only") is not True
            or binding.get("target_writes") != 0
            or binding.get("expected_result_digest_present") is not False
        ):
            fail("TASK_BINDING_BOUNDARY")
        if oac_plan is not None:
            logical_task = oac_plan_task_by_id.get(
                str(binding.get("logical_plan_task_id"))
            )
            if (
                binding.get("agentteams_execution_plan_digest")
                != oac_plan.get("digest")
                or binding.get("formation_receipt_digest")
                != oac_plan.get("formation_receipt_digest")
                or binding.get("context_envelope_digest")
                != oac_plan.get("context_envelope_digest")
                or logical_task is None
                or binding.get("logical_plan_task_digest")
                != logical_task.get("digest")
                or binding.get("sealed_plan_task") != logical_task
            ):
                fail("TASK_OAC_PLAN_BINDING")
        observed_sequences: list[int] = []
        for field, expected_action in lifecycle:
            action = action_by_digest.get(binding.get(field))
            if (
                action is None
                or action.get("action") != expected_action
                or not str(action.get("key", "")).startswith(f"{task_id}:")
            ):
                fail("TASK_LIFECYCLE_BINDING")
                continue
            observed_sequences.append(int(action["sequence"]))
        if len(observed_sequences) != len(lifecycle) or observed_sequences != sorted(observed_sequences):
            fail("TASK_LIFECYCLE_ORDER")

    plan_actions = [item for item in actions if item.get("action") == "plan_dag"]
    plan_task_sets: list[set[str]] = []
    plan_cardinalities: list[int] = []
    for action in plan_actions:
        try:
            raw_tasks = raw_by_action_digest[str(action["digest"])]["request"]["payload"]["tasks"]
            if not isinstance(raw_tasks, list):
                raise ValueError("PLAN_TASKS_SHAPE")
            task_ids = {str(item["taskId"]) for item in raw_tasks}
            if len(task_ids) != len(raw_tasks):
                raise ValueError("PLAN_TASKS_DUPLICATE")
            plan_task_sets.append(task_ids)
            plan_cardinalities.append(len(task_ids))
        except (KeyError, TypeError, ValueError):
            fail("AGENTTEAMS_PLAN_CARDINALITY")
    if (
        plan_cardinalities != [5, 7]
        or len(plan_task_sets) != 2
        or plan_task_sets[-1] != set(binding_by_task)
    ):
        fail("AGENTTEAMS_PLAN_CARDINALITY")

    # Independent process receipts must bind exact input/output bytes and an
    # earlier ACK action for the same native task.
    inputs: dict[str, dict[str, Any]] = {}
    outputs: dict[str, dict[str, Any]] = {}
    receipt_by_task: dict[str, dict[str, Any]] = {}
    process_ids: set[int] = set()
    receipt_digests: list[str] = []
    for receipt in process_receipts:
        if not isinstance(receipt, dict):
            fail("PROCESS_RECEIPT_SHAPE")
            continue
        task_id = receipt.get("task_id")
        if not isinstance(task_id, str) or task_id in receipt_by_task:
            fail("PROCESS_RECEIPT_CARDINALITY")
            continue
        receipt_by_task[task_id] = receipt
        if not _sealed(receipt):
            fail("PROCESS_RECEIPT_DIGEST")
        receipt_digests.append(str(receipt.get("digest")))
        try:
            worker_input = _load(golden / "process-inputs" / f"{task_id}.json")
            worker_output = _load(golden / "process-outputs" / f"{task_id}.json")
        except (OSError, ValueError, json.JSONDecodeError):
            fail("PROCESS_ARTIFACT_FILE")
            continue
        inputs[task_id] = worker_input
        outputs[task_id] = worker_output
        binding = binding_by_task.get(task_id, {})
        ack = action_by_digest.get(receipt.get("started_after_ack_action_digest"))
        submit = action_by_digest.get(binding.get("submit_action_digest"))
        if (
            ack is None
            or ack.get("action") != "ack_task"
            or not str(ack.get("key", "")).startswith(f"{task_id}:")
            or submit is None
            or int(ack.get("sequence", 0)) >= int(submit.get("sequence", 0))
            or receipt.get("started_after_ack_action_digest") != binding.get("ack_action_digest")
        ):
            fail("PROCESS_ACK_BINDING")
        if (
            receipt.get("input_digest") != _digest(worker_input)
            or binding.get("input_digest") != _digest(worker_input)
            or receipt.get("output_digest") != _digest(worker_output)
            or binding.get("observed_result_digest") != _digest(worker_output)
            or worker_output.get("input_digest") != _digest(worker_input)
        ):
            fail("PROCESS_INPUT_OUTPUT_DIGEST")
        if oac_plan is not None and any(
            receipt.get(field) != binding.get(field)
            or worker_output.get(field) != binding.get(field)
            for field in (
                "agentteams_execution_plan_digest",
                "logical_plan_task_id",
                "logical_plan_task_digest",
                "formation_receipt_id",
                "formation_receipt_digest",
            )
        ):
            fail("PROCESS_OAC_PLAN_BINDING")

        # The native AgentTeams lifecycle must carry the exact process output,
        # not merely actions sharing the same task_id/run_id.  Bind the Worker
        # ACK, submit request/response, Manager check and Manager acceptance to
        # the independently persisted output bytes.
        try:
            canonical_output = _canonical(worker_output).decode("utf-8")
            ack_raw = raw_by_action_digest[binding["ack_action_digest"]]
            ack_payload = payload_by_action_digest[binding["ack_action_digest"]]
            submit_raw = raw_by_action_digest[binding["submit_action_digest"]]
            submit_payload = payload_by_action_digest[binding["submit_action_digest"]]
            check_raw = raw_by_action_digest[binding["check_action_digest"]]
            check_payload = payload_by_action_digest[binding["check_action_digest"]]
            accept_raw = raw_by_action_digest[binding["accept_action_digest"]]
            accept_payload = payload_by_action_digest[binding["accept_action_digest"]]
            ack_task = ack_payload["task"]
            submit_task = submit_payload["task"]
            check_task = check_payload["task"]
            check_result = check_payload["result"]
            if (
                ack_raw.get("request", {}).get("role") != "worker"
                or ack_raw.get("request", {}).get("payload", {}).get("taskId") != task_id
                or ack_task.get("task_id") != task_id
                or ack_task.get("status") != "in_progress"
                or ack_task.get("acknowledged_by_role") != "worker"
                or submit_raw.get("request", {}).get("role") != "worker"
                or submit_raw.get("request", {}).get("payload", {}).get("taskId") != task_id
                or submit_raw.get("request", {}).get("payload", {}).get("status") != "SUCCESS"
                or submit_raw.get("request", {}).get("payload", {}).get("summary") != canonical_output
                or submit_task.get("task_id") != task_id
                or submit_task.get("status") != "submitted"
                or submit_task.get("result_status") != "SUCCESS"
                or submit_task.get("submitted_by_role") != "worker"
                or submit_task.get("summary") != canonical_output
                or check_raw.get("request", {}).get("role") != "leader"
                or check_raw.get("request", {}).get("payload", {}).get("taskId") != task_id
                or check_payload.get("effective") is not True
                or check_task.get("task_id") != task_id
                or check_task.get("status") != "submitted"
                or check_task.get("result_status") != "SUCCESS"
                or check_task.get("summary") != canonical_output
                or check_result.get("status") != "SUCCESS"
                or check_result.get("summary") != canonical_output
                or accept_raw.get("request", {}).get("payload", {}).get("taskId") != task_id
                or accept_raw.get("request", {}).get("payload", {}).get("projectId")
                != binding.get("project_id")
                or accept_raw.get("request", {}).get("payload", {}).get("resultStatus") != "SUCCESS"
                or accept_raw.get("request", {}).get("payload", {}).get("accepted") is not True
                or accept_payload.get("taskId") != task_id
                or accept_payload.get("nodeStatus") != "completed"
                or accept_payload.get("accepted") is not True
            ):
                fail("AGENTTEAMS_RESULT_ROUNDTRIP")
        except (KeyError, TypeError, UnicodeDecodeError):
            fail("AGENTTEAMS_RESULT_ROUNDTRIP")
        process_id = receipt.get("process_id")
        if not isinstance(process_id, int):
            fail("PROCESS_ID")
        else:
            process_ids.add(process_id)
        if (
            receipt.get("run_id") != run_id
            or receipt.get("correlation_id") != correlation_id
            or worker_input.get("run_id") != run_id
            or worker_input.get("correlation_id") != correlation_id
            or worker_output.get("run_id") != run_id
            or worker_output.get("correlation_id") != correlation_id
            or receipt.get("exit_code") != 0
            or receipt.get("independent_process") is not True
            or receipt.get("canonical_target_writes") != 0
            or worker_input.get("candidate_only") is not True
            or worker_input.get("target_writes") != 0
            or worker_output.get("candidate_only") is not True
            or worker_output.get("target_writes") != 0
        ):
            fail("PROCESS_BOUNDARY")
        if oac_plan is not None and any(
            artifact.get(field) != binding.get(field)
            for artifact in (worker_input, worker_output, receipt)
            for field in (
                "agentteams_execution_plan_digest",
                "context_envelope_digest",
                "logical_plan_task_id",
                "logical_plan_task_digest",
                "formation_receipt_id",
                "formation_receipt_digest",
            )
        ):
            fail("PROCESS_OAC_PLAN_BINDING")
        if receipt.get("mode") == "worker":
            try:
                manager_bindings = _manager_source_bindings(worker_input)
                if binding.get("source_bindings") != manager_bindings:
                    fail("MANAGER_SOURCE_BINDING")
                projection = worker_input["projection"]
                if not _sealed(projection) or worker_output.get("projection_digest") != projection.get(
                    "digest"
                ):
                    fail("PROCESS_PROJECTION_BINDING")
                claims = worker_output["claim_candidates"]
                claim_digests: list[str] = []
                for claim in claims:
                    predicate = claim["predicate"]
                    source_binding = manager_bindings[predicate]
                    claim_digests.append(claim["digest"])
                    source_refs = claim.get("source_refs")
                    if (
                        not _sealed(claim)
                        or _digest(claim.get("value")) != source_binding["value_digest"]
                        or claim.get("subject_ref") != source_binding["object_ref"]
                        or claim.get("semantic_kind") != source_binding["semantic_kind"]
                        or claim.get("authority_ref") != source_binding["authority_ref"]
                        or claim.get("purpose") != binding.get("task_purpose")
                        or not isinstance(source_refs, list)
                        or len(source_refs) != 1
                        or source_refs[0].get("source_id") != source_binding["source_id"]
                        or source_refs[0].get("source_version") != source_binding["source_version"]
                        or source_refs[0].get("source_digest") != source_binding["source_digest"]
                    ):
                        fail("CANDIDATE_MANAGER_PROVENANCE")
                bundle = worker_output["candidate_bundle"]
                if (
                    not _sealed(bundle)
                    or bundle.get("delegation_task_ref") != task_id
                    or bundle.get("candidate_refs") != claim_digests
                    or bundle.get("candidate_set_digest") != _digest(sorted(claim_digests))
                ):
                    fail("CANDIDATE_BUNDLE_BINDING")
            except (KeyError, TypeError, ValueError):
                fail("CANDIDATE_MANAGER_PROVENANCE")
    if (
        summary.get("process_receipt_digests") != receipt_digests
        or len(process_receipts) != 7
        or len(process_ids) != 7
        or summary.get("manager_process_id") in process_ids
        or len([item for item in process_receipts if item.get("mode") == "worker"]) != 5
        or len([item for item in process_receipts if item.get("mode") == "reviewer"]) != 2
    ):
        fail("PROCESS_TOPOLOGY")

    # Finance attempt 1 must really lack the answer; attempt 2 must consume the
    # exact bytes returned by the bounded HTTP Tool.
    finance_a1 = next(
        (item for item in task_bindings if item.get("domain") == "finance" and item.get("attempt") == 1),
        None,
    )
    finance_a2 = next(
        (item for item in task_bindings if item.get("domain") == "finance" and item.get("attempt") == 2),
        None,
    )
    if finance_a1 is None or finance_a2 is None:
        fail("FINANCE_ATTEMPT_BINDINGS")
    else:
        a1_input = inputs.get(str(finance_a1["task_id"]), {})
        a1_output = outputs.get(str(finance_a1["task_id"]), {})
        serialized_a1 = json.dumps(a1_input, ensure_ascii=False, sort_keys=True)
        included_refs = a1_input.get("projection", {}).get("included_refs", [])
        if (
            set(a1_input.get("source_values", {})) != {"currency"}
            or any("price_band" in str(ref) for ref in included_refs)
            or a1_input.get("supplemental_tool_receipt_digest") is not None
            or a1_input.get("supplemental_tool_result_digest") is not None
            or "strategic" in serialized_a1
            or "85000" in serialized_a1
            or _contains_key(a1_input, {"raw_private", "raw_private_value", "private_floor_value"})
        ):
            fail("FINANCE_A1_SOURCE_SCOPE")
        if (
            a1_output.get("status") != "ABSTAIN"
            or a1_output.get("missing_fields") != ["price_band"]
            or [item.get("predicate") for item in a1_output.get("claim_candidates", [])] != ["currency"]
        ):
            fail("FINANCE_A1_ABSTAIN")
        receipt = tool.get("receipt", {})
        result = tool.get("result", {})
        a2_input = inputs.get(str(finance_a2["task_id"]), {})
        a2_output = outputs.get(str(finance_a2["task_id"]), {})
        if (
            not isinstance(receipt, dict)
            or not _sealed(receipt)
            or receipt.get("response_digest") != _digest(result)
            or receipt.get("status") != "SUCCEEDED"
            or receipt.get("evidence_class") != "CONTROLLED_LOCAL_REAL_HTTP"
            or receipt.get("run_id") != run_id
            or receipt.get("target_writes") != 0
            or tool.get("run_id") != run_id
            or tool.get("candidate_only") is not True
            or tool.get("target_writes") != 0
        ):
            fail("TOOL_RECEIPT_BINDING")
        tool_sources = result.get("source_values", {}) if isinstance(result, dict) else {}
        if (
            set(tool_sources) != {"price_band"}
            or a2_input.get("source_values", {}).get("price_band") != tool_sources.get("price_band")
            or a2_input.get("supplemental_tool_receipt_digest") != receipt.get("digest")
            or a2_input.get("supplemental_tool_result_digest") != _digest(result)
            or a2_output.get("supplemental_tool_receipt_digest") != receipt.get("digest")
            or a2_output.get("supplemental_tool_result_digest") != _digest(result)
            or finance_a2.get("tool_receipt_digest") != receipt.get("digest")
            or finance_a2.get("tool_result_digest") != _digest(result)
        ):
            fail("TOOL_FINANCE_A2_BYTES")

    # Reviewer inputs must contain exact process outputs, while expected
    # bindings are recomputed from Manager-side task/input artifacts.
    reviewer_inputs: dict[int, dict[str, Any]] = {}
    reviewer_outputs: dict[int, dict[str, Any]] = {}
    reviewer_providers: set[str] = set()
    vertex_response_ids: set[str] = set()
    vertex_response_schema_digests: set[str] = set()
    summary_model_attempts = summary.get("reviewer_model_attempts")
    current_model_evidence = isinstance(summary_model_attempts, list)
    vertex_model_id = _vertex_model_id_from_summary(summary)
    if current_model_evidence and len(summary_model_attempts) != 2:
        fail("REVIEWER_MODEL_ATTEMPT_COUNT")
    if summary.get("model_provider") == "vertex-ai" and vertex_model_id is None:
        fail("REVIEWER_VERTEX_MODEL_SELECTION")
    for binding in task_bindings:
        if binding.get("role") != "REVIEWER":
            continue
        phase = int(binding.get("attempt", 0))
        task_id = str(binding.get("task_id"))
        reviewer_input = inputs.get(task_id, {})
        reviewer_output = outputs.get(task_id, {})
        reviewer_inputs[phase] = reviewer_input
        reviewer_outputs[phase] = reviewer_output
        expected: dict[str, Any] = {}
        for result in reviewer_input.get("domain_results", []):
            domain = result.get("domain")
            worker_task_id = result.get("task_id")
            worker_binding = binding_by_task.get(worker_task_id, {})
            worker_input = inputs.get(worker_task_id, {})
            if not isinstance(domain, str) or not worker_binding or not worker_input:
                fail("REVIEWER_RESULT_BINDING")
                continue
            if result != outputs.get(worker_task_id):
                fail("REVIEWER_RESULT_BINDING")
            try:
                expected[domain] = _manager_expected_binding(
                    worker_task_id=worker_task_id,
                    worker_binding=worker_binding,
                    worker_input=worker_input,
                    run_id=run_id,
                    correlation_id=correlation_id,
                    oac_bound=oac_plan is not None,
                )
            except (KeyError, TypeError, ValueError):
                fail("REVIEWER_MANAGER_BINDING")
        if reviewer_input.get("expected_bindings") != expected:
            fail("REVIEWER_MANAGER_BINDING")
        model_receipt = reviewer_output.get("model_receipt", {})
        model_binding = reviewer_output.get("model_runtime_binding", {})
        provider = reviewer_output.get("model_provider") or model_receipt.get("provider")
        reviewer_providers.add(str(provider))
        if (
            not isinstance(model_receipt, dict)
            or not _sealed(model_receipt)
            or model_receipt.get("status") != "VALID"
            or model_receipt.get("schema_valid") is not True
            or model_receipt.get("output_digest") != _digest(model_receipt.get("value"))
            or model_receipt.get("provider") != provider
            or reviewer_output.get("decision")
            != summary.get(f"reviewer_attempt_{phase}")
        ):
            fail("REVIEWER_MODEL_BINDING")
        if (
            not _model_advisory_authority_valid(
                reviewer_output,
                expected_decision=summary.get(f"reviewer_attempt_{phase}"),
                phase=phase,
            )
            or summary.get(
                f"reviewer_attempt_{phase}_model_advisory_accepted"
            )
            != reviewer_output.get("model_advisory_accepted")
            or summary.get(
                f"reviewer_attempt_{phase}_model_advisory_disposition"
            )
            != reviewer_output.get("model_advisory_disposition_reason")
        ):
            fail("REVIEWER_MODEL_ADVISORY_AUTHORITY")
        if provider == "ollama-local":
            if (
                model_receipt.get("evidence_class") != "LOCAL_OLLAMA_MODEL"
                or model_receipt.get("model_id") != OLLAMA_MODEL_ID
                or model_receipt.get("model_version") != OLLAMA_RECEIPT_VERSION
                or model_receipt.get("provider_request_id") is not None
                or model_binding.get("status") != "BOUND"
                or model_binding.get("observed_model_digest")
                != model_binding.get("expected_model_digest")
                or model_binding.get("observed_model_digest") != OLLAMA_MODEL_DIGEST
                or model_receipt.get("model_version")
                != f"ollama-manifest:{model_binding.get('observed_model_digest')}"
                or model_binding.get("claim_boundary")
                != "LOCAL_LOOPBACK_INFERENCE_NOT_PRODUCTION_PROVIDER"
            ):
                fail("REVIEWER_MODEL_BINDING")
        elif provider == "vertex-ai":
            provider_request_id = model_receipt.get("provider_request_id")
            schema_digest = model_binding.get("response_schema_digest")
            if (
                model_receipt.get("evidence_class") != "LIVE_MODEL"
                or model_receipt.get("model_id") != vertex_model_id
                or model_receipt.get("model_version") != vertex_model_id
                or not isinstance(provider_request_id, str)
                or not provider_request_id.strip()
                or model_receipt.get("finish_reason") != "STOP"
                or model_binding.get("status") != "OBSERVED"
                or model_binding.get("provider") != "vertex-ai"
                or model_binding.get("provider_evidence_class") != "LIVE_VERTEX_MODEL"
                or model_binding.get("model_id") != vertex_model_id
                or model_binding.get("observed_model_version") != vertex_model_id
                or model_binding.get("location") != "global"
                or model_binding.get("thinking_level") != "LOW"
                or model_binding.get("max_output_tokens") != 2048
                or model_binding.get("provider_request_id_present") is not True
                or model_binding.get("credentials_disclosed") is not False
                or model_binding.get("request_or_response_content_disclosed") is not False
                or model_binding.get("claim_boundary") != VERTEX_CLAIM_BOUNDARY
                or not _valid_digest(model_binding.get("project_id_digest"))
                or not _valid_digest(model_binding.get("request_payload_digest"))
                or not _valid_digest(model_binding.get("response_observation_digest"))
                or not _valid_digest(schema_digest)
                or _contains_key(
                    model_binding,
                    {"api_key", "access_token", "authorization", "credential", "secret"},
                )
            ):
                fail("REVIEWER_VERTEX_MODEL_BINDING")
            if isinstance(provider_request_id, str):
                vertex_response_ids.add(provider_request_id)
            if isinstance(schema_digest, str):
                vertex_response_schema_digests.add(schema_digest)
        elif provider == "deepseek":
            deepseek_attempt = next((item for item in summary_model_attempts if isinstance(item, dict) and item.get("phase") == phase), {})
            if not _deepseek_binding_valid(model_receipt, model_binding, deepseek_attempt):
                fail("REVIEWER_DEEPSEEK_MODEL_BINDING")
            if isinstance(model_receipt.get("provider_request_id"), str):
                vertex_response_ids.add(model_receipt["provider_request_id"])
            if isinstance(model_binding.get("response_schema_digest"), str):
                vertex_response_schema_digests.add(model_binding["response_schema_digest"])
        else:
            fail("REVIEWER_MODEL_PROVIDER")

        if current_model_evidence:
            attempt = next(
                (
                    item
                    for item in summary_model_attempts
                    if isinstance(item, dict) and item.get("phase") == phase
                ),
                None,
            )
            if (
                not isinstance(attempt, dict)
                or not _sealed(attempt)
                or reviewer_output.get("model_provider") != provider
                or reviewer_output.get("model_authority") != MODEL_AUTHORITY
                or attempt.get("run_id") != run_id
                or attempt.get("task_id") != task_id
                or attempt.get("provider") != provider
                or attempt.get("reviewer_input_digest") != _digest(reviewer_input)
                or attempt.get("reviewer_prompt_payload_digest")
                != reviewer_output.get("prompt_payload_digest")
                or attempt.get("model_request_digest")
                != model_receipt.get("request_digest")
                or attempt.get("model_response_receipt_digest")
                != model_receipt.get("digest")
                or attempt.get("output_digest") != model_receipt.get("output_digest")
                or attempt.get("provider_request_id")
                != model_receipt.get("provider_request_id")
                or attempt.get("model_authority") != MODEL_AUTHORITY
                or attempt.get("advisory_accepted")
                != reviewer_output.get("model_advisory_accepted")
                or attempt.get("advisory_disposition")
                != reviewer_output.get("model_advisory_disposition_reason")
                or attempt.get("candidate_only") is not True
                or attempt.get("target_writes") != 0
            ):
                fail("REVIEWER_MODEL_ATTEMPT_BINDING")
            if provider == "vertex-ai" and isinstance(attempt, dict) and (
                attempt.get("requested_model_id") != vertex_model_id
                or attempt.get("observed_model_version") != vertex_model_id
                or attempt.get("finish_reason") != "STOP"
                or attempt.get("thinking_level") != "LOW"
                or attempt.get("max_output_tokens") != 2048
                or attempt.get("request_payload_digest")
                != model_binding.get("request_payload_digest")
                or attempt.get("response_observation_digest")
                != model_binding.get("response_observation_digest")
            ):
                fail("REVIEWER_VERTEX_MODEL_ATTEMPT")
            if provider == "vertex-ai" and not _vertex_transport_chain_valid(reviewer_output, attempt):
                fail("REVIEWER_VERTEX_TRANSPORT_RETRY_BINDING")
    if len(reviewer_providers) != 1:
        fail("REVIEWER_MODEL_PROVIDER_DRIFT")
    selected_provider = next(iter(reviewer_providers), None)
    if current_model_evidence and summary.get("model_provider") != selected_provider:
        fail("REVIEWER_MODEL_SUMMARY_PROVIDER")
    if selected_provider in {"vertex-ai", "deepseek"} and (
        len(vertex_response_ids) != 2 or len(vertex_response_schema_digests) != 1
    ):
        fail("REVIEWER_VERTEX_RESPONSE_ID_OR_SCHEMA")
    if (
        reviewer_outputs.get(1, {}).get("decision")
        != EXPECTED_REVIEWER_DECISIONS[1]
        or reviewer_outputs.get(2, {}).get("decision")
        != EXPECTED_REVIEWER_DECISIONS[2]
        or summary.get("reviewer_attempt_1") != reviewer_outputs.get(1, {}).get("decision")
        or summary.get("reviewer_attempt_2") != reviewer_outputs.get(2, {}).get("decision")
    ):
        fail("REVIEWER_TRANSITION")
    if finance_a2 is not None:
        finance_expected = reviewer_inputs.get(2, {}).get("expected_bindings", {}).get("finance", {})
        if finance_expected.get("tool_receipt_digest") != tool.get("receipt", {}).get(
            "digest"
        ) or finance_expected.get("tool_result_digest") != _digest(tool.get("result", {})):
            fail("REVIEWER_TOOL_BINDING")

    # Skill package, evaluation, release authority and exact-root invocation.
    final_results = {
        item.get("domain"): item
        for item in reviewer_inputs.get(2, {}).get("domain_results", [])
        if isinstance(item, dict) and isinstance(item.get("domain"), str)
    }
    final_result_digests = {domain: _digest(result) for domain, result in sorted(final_results.items())}
    skill_package_digest = _digest(
        {
            key: value
            for key, value in skill_package.items()
            if key != "manifest_digest"
        }
    )
    dependencies = skill_package.get("dependencies")
    release_artifact = skill_package.get("release_artifact")
    expected_partitions = {
        "REPLAY",
        "HELD_OUT",
        "NEGATIVE_TRANSFER",
        "PERMISSION",
        "INJECTION",
        "MALFORMED",
        "RESOURCE_OR_DEADLINE",
        "CANARY",
    }
    if (
        skill_package.get("schema_version")
        != "orgrebase.skill-package-manifest.v2"
        or skill_package.get("name") != "enterprise-quote-compose"
        or skill_package.get("entry_point") != "QUOTE_COMPOSE_V1"
        or skill_package.get("manifest_digest") != skill_package_digest
        or not isinstance(dependencies, dict)
        or not dependencies
        or not isinstance(release_artifact, dict)
        or release_artifact.get("source_candidate_executable") is not False
        or skill_package.get("permissions")
        != {
            "allowed_tools": [],
            "effect_ceiling": "CANDIDATE_ONLY",
            "side_effects": [],
        }
    ):
        fail("SKILL_PACKAGE_BINDING")

    evaluation_cases = skill_evaluation.get("case_results")
    evaluation_gates = skill_evaluation.get("gate_results")
    evaluation_premise = skill_evaluation.get("premise_lock")
    evaluation_partitions = {
        item.get("partition")
        for item in evaluation_cases
        if isinstance(item, dict)
    } if isinstance(evaluation_cases, list) else set()
    expected_gate_ids = {
        "exact_package_digest",
        "critical_security_failures",
        "target_writes",
        *(f"partition:{partition.lower()}" for partition in expected_partitions),
    }
    evaluation_gate_ids = {
        item.get("gate_id")
        for item in evaluation_gates
        if isinstance(item, dict)
    } if isinstance(evaluation_gates, list) else set()
    expected_candidate_digest = (
        release_artifact.get("source_candidate_digest")
        if isinstance(release_artifact, dict)
        else None
    ) or skill_package_digest
    if (
        not _sealed(skill_evaluation)
        or skill_evaluation.get("verdict") != "CANARY"
        or skill_evaluation.get("candidate_digest") != expected_candidate_digest
        or skill_evaluation.get("candidate_program_digest")
        != skill_package.get("program_content_digest")
        or not isinstance(evaluation_cases, list)
        or not _quote_skill_case_identity(skill_evaluation, run_id)
        or evaluation_partitions != expected_partitions
        or not all(
            isinstance(item, dict) and item.get("passed") is True
            for item in evaluation_cases
        )
        or not isinstance(evaluation_gates, list)
        or evaluation_gate_ids != expected_gate_ids
        or not all(
            isinstance(item, dict) and item.get("passed") is True
            for item in evaluation_gates
        )
        or not isinstance(evaluation_premise, dict)
        or evaluation_premise.get("package") != skill_package_digest
        or evaluation_premise.get("dependencies") != _digest(dependencies)
        or summary.get("skill_evaluation_receipt_digest")
        != skill_evaluation.get("digest")
        or summary.get("skill_evaluation_verdict") != "CANARY"
        or summary.get("skill_evaluation_partition_count") != len(expected_partitions)
        or summary.get("skill_evaluation_case_count", len(expected_partitions)) != len(evaluation_cases)
    ):
        fail("SKILL_EVALUATION_AUTHORITY")

    expected_release_transitions = (
        ("DRAFT", "EVALUATED"),
        ("EVALUATED", "SHADOW"),
        ("SHADOW", "CANARY"),
    )
    previous_release_digest: str | None = None
    evaluation_premise_digest = (
        _digest(evaluation_premise) if isinstance(evaluation_premise, dict) else None
    )
    release_chain_valid = len(skill_release_ledger) == 3
    for index, transition in enumerate(skill_release_ledger, start=1):
        if index > len(expected_release_transitions):
            release_chain_valid = False
            continue
        expected_from, expected_to = expected_release_transitions[index - 1]
        if (
            not isinstance(transition, dict)
            or not _sealed(transition)
            or transition.get("event_index") != index
            or transition.get("event_type") != "RELEASE"
            or transition.get("skill_name") != "enterprise-quote-compose"
            or transition.get("release_artifact_id")
            != (
                release_artifact.get("id")
                if isinstance(release_artifact, dict)
                else None
            )
            or transition.get("package_digest") != skill_package_digest
            or transition.get("effective_package_digest") != skill_package_digest
            or transition.get("predecessor_package_digest")
            != (
                release_artifact.get("predecessor_package_digest")
                if isinstance(release_artifact, dict)
                else None
            )
            or transition.get("predecessor_executable") is not False
            or transition.get("restoration_status") != "NOT_APPLICABLE"
            or transition.get("source_candidate_ref")
            != (
                release_artifact.get("source_candidate_ref")
                if isinstance(release_artifact, dict)
                else None
            )
            or transition.get("source_candidate_digest")
            != (
                release_artifact.get("source_candidate_digest")
                if isinstance(release_artifact, dict)
                else None
            )
            or transition.get("source_candidate_executable") is not False
            or transition.get("evaluation_receipt_digest")
            != skill_evaluation.get("digest")
            or transition.get("evaluation_premise_lock_digest")
            != evaluation_premise_digest
            or transition.get("previous_receipt_digest")
            != previous_release_digest
            or transition.get("from_state") != expected_from
            or transition.get("to_state") != expected_to
            or transition.get("actor_id") != "authority:skill-registry"
            or transition.get("reason_codes")
            != [f"GOLDEN_RUN_QUALIFIED_FOR_{expected_to}"]
            or transition.get("changed_dependency_refs") != []
        ):
            release_chain_valid = False
        if isinstance(transition, dict):
            previous_release_digest = transition.get("digest")
    if (
        not release_chain_valid
        or summary.get("skill_release_transition_count") != 3
        or summary.get("skill_release_state") != "CANARY"
        or summary.get("skill_release_receipt_digest")
        != previous_release_digest
    ):
        fail("SKILL_RELEASE_LEDGER")

    if (
        not _sealed(skill_receipt)
        or skill_receipt.get("input_digest") != _digest(skill_input)
        or skill_receipt.get("output_digest") != _digest(skill_result)
        or skill_receipt.get("run_id") != run_id
        or skill_receipt.get("outcome") != "SUCCESS"
        or skill_receipt.get("candidate_only") is not True
        or skill_receipt.get("target_writes") != 0
        or skill_receipt.get("authorization_mode") != "RELEASE"
        or skill_receipt.get("release_receipt_digest") is None
        or skill_receipt.get("release_receipt_digest")
        != previous_release_digest
        or skill_receipt.get("package_digest") != skill_package_digest
        or skill_receipt.get("manifest_digest") != skill_package_digest
        or skill_receipt.get("program_digest")
        != skill_package.get("program_content_digest")
        or skill_receipt.get("release_artifact_id")
        != (
            release_artifact.get("id")
            if isinstance(release_artifact, dict)
            else None
        )
        or skill_result.get("action") != "APPLY_QUOTE"
        or skill_result.get("candidate_only") is not True
        or skill_result.get("target_writes") != 0
        or skill_input.get("domain_result_digests") != final_result_digests
        or skill_result.get("domain_result_digests") != final_result_digests
        or skill_input.get("dependency_tool_receipt_digest") != tool.get("receipt", {}).get("digest")
        or skill_input.get("dependency_result_digest") != _digest(tool.get("result", {}))
        or summary.get("skill_invocation_receipt_digest") != skill_receipt.get("digest")
        or summary.get("skill_authorization_mode") != "RELEASE"
        or summary.get("skill_release_receipt_digest")
        != skill_receipt.get("release_receipt_digest")
        or summary.get("skill_action") != skill_result.get("action")
    ):
        fail("SKILL_RELEASE_AUTHORIZATION")

    # PreparedFormation must bind the exact Reviewer-2 bytes and derive the
    # Quote payload from those reviewed candidates.
    if not _sealed(prepared):
        fail("FORMATION_BUNDLE_DIGEST")
    artifact_writes = prepared.get("artifact_writes")
    if not isinstance(artifact_writes, list):
        artifact_writes = []
        fail("FORMATION_ARTIFACT_WRITES")
    controlled: list[dict[str, Any]] = []
    claim_payloads: dict[str, dict[str, Any]] = {}
    bundle_payloads: dict[str, dict[str, Any]] = {}
    for write in artifact_writes:
        if not isinstance(write, dict) or not isinstance(write.get("payload"), dict):
            fail("FORMATION_ARTIFACT_WRITES")
            continue
        payload = write["payload"]
        if write.get("payload_digest") != _digest(payload):
            fail("FORMATION_ARTIFACT_WRITES")
        if "digest" in payload and not _sealed(payload):
            fail("FORMATION_ARTIFACT_WRITES")
        media_type = write.get("media_type")
        if media_type == "application/vnd.orgrebase.controlled-agentteams-formation-receipt+json":
            controlled.append(payload)
        elif media_type == "application/vnd.orgrebase.claim-candidate+json":
            claim_payloads[str(payload.get("digest"))] = payload
        elif media_type == "application/vnd.orgrebase.domain-candidate-bundle+json":
            bundle_payloads[str(payload.get("digest"))] = payload
    if len(controlled) != 1:
        fail("FORMATION_CONTROL_RECEIPT")
    else:
        control = controlled[0]
        final_claims = [
            claim
            for domain in sorted(final_results)
            for claim in final_results[domain].get("claim_candidates", [])
        ]
        final_claim_digests = sorted(str(item.get("digest")) for item in final_claims)
        final_bundle_digests = sorted(
            str(final_results[domain].get("candidate_bundle", {}).get("digest")) for domain in final_results
        )
        expected_sources = (
            {
                domain: reviewer_inputs[2]["expected_bindings"][domain]["source_bindings"]
                for domain in sorted(final_results)
            }
            if 2 in reviewer_inputs and isinstance(reviewer_inputs[2].get("expected_bindings"), dict)
            else {}
        )
        if (
            not _sealed(control)
            or control.get("run_id") != run_id
            or control.get("correlation_id") != correlation_id
            or control.get("reviewer_input_digest") != _digest(reviewer_inputs.get(2, {}))
            or control.get("reviewer_result_digest") != _digest(reviewer_outputs.get(2, {}))
            or control.get("reviewer_verdict") != "PASS"
            or control.get("domain_result_digests") != final_result_digests
            or control.get("manager_source_bindings") != expected_sources
            or sorted(control.get("agentteams_action_digests", [])) != sorted(action_digests)
            or sorted(control.get("task_binding_digests", []))
            != sorted(str(item.get("digest")) for item in task_bindings)
            or sorted(control.get("claim_candidate_digests", [])) != final_claim_digests
            or sorted(control.get("domain_bundle_digests", [])) != final_bundle_digests
            or set(claim_payloads) != set(final_claim_digests)
            or set(bundle_payloads) != set(final_bundle_digests)
            or any(claim_payloads.get(str(claim.get("digest"))) != claim for claim in final_claims)
            or any(
                bundle_payloads.get(str(final_results[domain]["candidate_bundle"]["digest"]))
                != final_results[domain]["candidate_bundle"]
                for domain in final_results
            )
            or control.get("tool_receipt_digest") != tool.get("receipt", {}).get("digest")
            or control.get("tool_result_digest") != _digest(tool.get("result", {}))
            or control.get("skill_invocation_receipt_digest") != skill_receipt.get("digest")
            or control.get("candidate_target_writes") != 0
            or control.get("canonical_target_writes") != 0
        ):
            fail("FORMATION_EXACT_REVIEWED_BINDING")
        event_payload = prepared.get("event_payload", {})
        if (
            event_payload.get("controlled_agentteams_formation_receipt_digest") != control.get("digest")
            or event_payload.get("reviewer_result_digest") != _digest(reviewer_outputs.get(2, {}))
            or event_payload.get("tool_receipt_digest") != tool.get("receipt", {}).get("digest")
            or event_payload.get("skill_invocation_receipt_digest") != skill_receipt.get("digest")
            or event_payload.get("golden_competition_run_id") != run_id
            or event_payload.get("golden_competition_correlation_id") != correlation_id
            or event_payload.get("candidate_target_writes") != 0
            or event_payload.get("canonical_target_writes") != 0
            or summary.get("controlled_agentteams_formation_receipt_digest") != control.get("digest")
        ):
            fail("FORMATION_EVENT_BINDING")
        if oac_plan is not None and (
            event_payload.get("task_formation_decision_receipt_digest")
            != oac_plan.get("formation_receipt_digest")
            or event_payload.get("context_envelope_digest")
            != oac_plan.get("context_envelope_digest")
            or event_payload.get("agentteams_execution_plan_digest")
            != oac_plan.get("digest")
            or event_payload.get("planned_domain_ids")
            != oac_plan.get("selected_domain_ids")
            or event_payload.get("actual_agentteams_domain_ids")
            != oac_plan.get("selected_domain_ids")
            or event_payload.get("topology_match") is not True
            or event_payload.get("context_freshness_basis")
            != "LOGICAL_EVENT_TIME"
        ):
            fail("FORMATION_OAC_PLAN_BINDING")
    quote_payload = prepared.get("deliverable", {}).get("payload", {})
    candidate_values = {
        claim.get("predicate"): claim.get("value")
        for result in final_results.values()
        for claim in result.get("claim_candidates", [])
    }
    quote_fields = {
        "product_plan": "product_plan",
        "launch_date": "launch_date",
        "data_residency": "data_residency",
        "notice_required": "notice_required",
        "currency": "currency",
        "price_band": "price_band",
        "partner_terms_code": "partner_terms",
    }
    if (
        not isinstance(quote_payload, dict)
        or any(
            quote_payload.get(field) != candidate_values.get(predicate)
            for field, predicate in quote_fields.items()
        )
        or summary.get("prepared_quote_payload") != quote_payload
        or summary.get("prepared_quote_ref")
        != f"{prepared.get('deliverable', {}).get('id')}@{prepared.get('deliverable', {}).get('version')}"
        or summary.get("prepared_formation_digest") != prepared.get("digest")
    ):
        fail("FORMATION_QUOTE_CAUSALITY")
    for pricing_failure in _priced_quote_failures(quote_payload, candidate_values):
        fail(pricing_failure)

    return failures



def _deepseek_binding_valid(receipt, runtime, attempt):
    if not all(isinstance(value, dict) for value in (receipt, runtime, attempt)):
        return False
    request_id = receipt.get("provider_request_id")
    digest_fields = ("request_payload_digest", "response_observation_digest")
    return (
        receipt.get("provider") == "deepseek" and receipt.get("evidence_class") == "LIVE_MODEL"
        and receipt.get("model_id") == receipt.get("model_version") == "deepseek-flash"
        and receipt.get("finish_reason") == "stop"
        and isinstance(request_id, str) and bool(request_id.strip())
        and runtime.get("provider") == "deepseek" and runtime.get("status") == "OBSERVED"
        and runtime.get("model_binding") == "UNPINNED_PROVIDER_ALIAS"
        and runtime.get("provider_evidence_class") == "LIVE_DEEPSEEK_MODEL"
        and runtime.get("observed_model_version") == "deepseek-flash"
        and runtime.get("credentials_disclosed") is False
        and runtime.get("request_or_response_content_disclosed") is False
        and runtime.get("provider_request_id_present") is True
        and runtime.get("max_output_tokens") == 2048
        and _valid_digest(runtime.get("response_schema_digest"))
        and all(_valid_digest(runtime.get(key)) and attempt.get(key) == runtime[key] for key in digest_fields)
        and attempt.get("provider_request_id") == request_id
        and attempt.get("requested_model_id") == "deepseek-flash"
        and attempt.get("observed_model_version") == runtime["observed_model_version"]
        and attempt.get("finish_reason") == "stop" and attempt.get("max_output_tokens") == 2048
        and not _contains_key(runtime, {"api_key", "access_token", "authorization", "credential", "secret"})
    )

def _vertex_transport_chain_valid(output, attempt):
    runtime = output.get("model_runtime_binding", {})
    if not isinstance(runtime, dict) or not isinstance(attempt, dict):
        return False
    retry = runtime.get("transport_retry")
    legacy = "transport_retry" not in runtime
    # Only absence of the entire collection denotes the retained pre-observer
    # format. A present but malformed collection is never a legacy escape hatch.
    if "model_attempt_observations" not in output:
        return (legacy and attempt.get("provider_attempt_count", 1) == 1
                and attempt.get("failed_provider_attempt_count", 0) == 0)
    collection = output["model_attempt_observations"]
    if not isinstance(collection, dict):
        return False
    records = collection.get("records")
    receipt = output.get("model_receipt")
    if (not isinstance(records, list) or not 1 <= len(records) <= 3
            or not all(isinstance(record, dict) for record in records)
            or not isinstance(receipt, dict)):
        return False
    if legacy:
        if len(records) != 1:
            return False
    else:
        if not isinstance(retry, dict):
            return False
        dispatches = retry.get("attempts")
        if (not isinstance(dispatches, list)
                or not all(isinstance(item, dict) for item in dispatches)
                or retry.get("max_attempts") != 3
                or retry.get("provider_attempts") != len(records)
                or retry.get("successful_calls") != 1
                or [r.get("dispatch_id") for r in dispatches] != [r.get("dispatch_id") for r in records]):
            return False
    dispatch_ids = [r.get("dispatch_id") for r in records]
    if (not all(isinstance(value, str) and value for value in dispatch_ids)
            or len(set(dispatch_ids)) != len(records)):
        return False
    for index, record in enumerate(records):
        if (record.get("digest") != _digest({k: v for k, v in record.items() if k != "digest"}) or record.get("request_digest") != receipt.get("request_digest")
                or record.get("run_ref") != output.get("run_id") or record.get("task_ref") != output.get("task_id")
                or record.get("provider") != "vertex-ai" or record.get("requested_model") != receipt.get("model_id")
                or record.get("phase") != "RESULT" or record.get("dispatch_state") != "RESPONSE_RECEIVED"
                or record.get("persistence") != "DURABLE"
                or record.get("request_body_digest") != records[-1].get("request_body_digest")):
            return False
        if index < len(records) - 1:
            usage = record.get("usage")
            if (record.get("response_state") != "PROVIDER_ERROR"
                    or record.get("error_code") != "VERTEX_RATE_LIMITED"
                    or not isinstance(usage, dict)
                    or usage.get("status") not in {"UNAVAILABLE", "PARTIAL", "REPORTED"}):
                return False
    return (records[-1].get("response_receipt_digest") == receipt.get("digest")
            and records[-1].get("response_state") == "VALID"
            and records[-1].get("provider_request_id") == receipt.get("provider_request_id")
            and attempt.get("provider_attempt_count", 1 if legacy else None) == len(records)
            and attempt.get("failed_provider_attempt_count", 0 if legacy else None) == len(records) - 1)


def _expected_model_manifest(summary: dict[str, Any]) -> dict[str, Any] | None:
    provider = summary.get("model_provider")
    attempts = summary.get("reviewer_model_attempts")
    if provider not in {"ollama-local", "vertex-ai", "deepseek"} or not isinstance(attempts, list):
        return None
    if len(attempts) != 2 or not all(isinstance(item, dict) for item in attempts):
        return None
    vertex_model_id = _vertex_model_id_from_summary(summary)
    if provider == "vertex-ai" and vertex_model_id is None:
        return None
    return {
        "provider": provider,
        "model_id": "deepseek-flash" if provider == "deepseek" else vertex_model_id if provider == "vertex-ai" else OLLAMA_MODEL_ID,
        "model_version": (
            "deepseek-flash" if provider == "deepseek" else vertex_model_id if provider == "vertex-ai" else OLLAMA_RECEIPT_VERSION
        ),
        "attempt_count": 2,
        "evidence_class": (
            "LIVE_MODEL" if provider in {"vertex-ai", "deepseek"} else "LOCAL_OLLAMA_MODEL"
        ),
        "provider_evidence_class": (
            "LIVE_DEEPSEEK_MODEL" if provider == "deepseek" else "LIVE_VERTEX_MODEL" if provider == "vertex-ai" else "LOCAL_OLLAMA_MODEL"
        ),
        "finish_reasons": [item.get("finish_reason") for item in attempts],
        "provider_request_ids_distinct": (
            len({item.get("provider_request_id") for item in attempts}) == 2
            if provider in {"vertex-ai", "deepseek"}
            else None
        ),
        "thinking_level": "LOW" if provider == "vertex-ai" else None,
        "schema_valid": True,
        "model_authority": MODEL_AUTHORITY,
        "canonical_target_writes": 0,
        "advisory_dispositions": [
            {
                "phase": item.get("phase"),
                "accepted": item.get("advisory_accepted"),
                "disposition": item.get("advisory_disposition"),
            }
            for item in attempts
        ],
    }


def _verify_experience_evidence(
    root: Path,
    *,
    state: dict[str, Any],
    evidence: dict[str, Any],
    summary: dict[str, Any],
    run_id: str,
    correlation_id: str,
) -> tuple[list[str], dict[str, Any] | None]:
    failures: list[str] = []

    def fail(code: str) -> None:
        if code not in failures:
            failures.append(code)

    state_view = state.get("experience_governance")
    evidence_view = evidence.get("experience_governance")
    if (
        not isinstance(state_view, dict)
        or not isinstance(evidence_view, dict)
        or state_view != evidence_view
        or not _sealed(state_view)
    ):
        return ["EXPERIENCE_STATE_EVIDENCE_PROJECTION"], None
    if (
        state_view.get("run_id") != run_id
        or state_view.get("status") != "APPROVED_CANARY"
        or state_view.get("owner_id") != EXPERIENCE_STEWARD_ID
        or state_view.get("discoverable") is not True
        or state_view.get("loadable") is not True
        or state_view.get("callable") is not True
        or state_view.get("head_fresh") is not True
        or state_view.get("current_quote_consumed_candidate") is not False
        or state_view.get("candidate_only") is not True
        or state_view.get("target_writes") != 0
    ):
        fail("EXPERIENCE_APPROVED_VIEW")

    candidate = state_view.get("candidate")
    evaluation = state_view.get("evaluation")
    gate = state_view.get("review_gate")
    decision = state_view.get("decision")
    release = state_view.get("release")
    if not all(
        isinstance(item, dict) and _sealed(item)
        for item in (candidate, evaluation, gate, decision, release)
    ):
        return [*failures, "EXPERIENCE_PUBLIC_ARTIFACT_DIGEST"], None
    candidate_id = candidate.get("id")
    if (
        not isinstance(candidate_id, str)
        or not candidate_id.startswith("experience-candidate:")
        or not candidate_id.endswith("@v1")
    ):
        fail("EXPERIENCE_PUBLIC_ARTIFACT_ID")
        token = ""
    else:
        token = candidate_id.removeprefix("experience-candidate:").removesuffix(
            "@v1"
        )
    if (
        gate.get("id") != f"experience-review-gate:{token}@v1"
        or decision.get("id") != f"experience-decision:{token}@v1"
    ):
        fail("EXPERIENCE_PUBLIC_ARTIFACT_ID")

    source = candidate.get("source")
    required = candidate.get("required_evidence")
    if (
        candidate.get("outcome") != "IMPROVE"
        or candidate.get("maturity") != "SINGLE_RUN_SEED"
        or candidate.get("source_run_id") != run_id
        or candidate.get("target_skill_name") != EXPERIENCE_TARGET_SKILL
        or not _valid_digest(candidate.get("base_package_digest"))
        or not isinstance(source, dict)
        or source.get("run_id") != run_id
        or source.get("correlation_id") != correlation_id
        or source.get("summary_digest") != summary.get("digest")
        or source.get("competition_evidence_digest")
        != state.get("competition_evidence", {}).get("digest")
        or not isinstance(required, dict)
        or set(candidate.get("proposed_evaluation_partitions", []))
        != EXPERIENCE_PARTITIONS
        or candidate.get("permissions", {}).get("human_approval_required") is not True
        or candidate.get("permissions", {}).get("effect_ceiling") != "CANDIDATE_ONLY"
        or candidate.get("permissions", {}).get("target_writes") != 0
        or candidate.get("candidate_only") is not True
        or candidate.get("target_writes") != 0
    ):
        fail("EXPERIENCE_CANDIDATE")

    task_bindings = summary.get("task_bindings")
    if not isinstance(task_bindings, list):
        task_bindings = []
    finance_a1 = next(
        (
            item
            for item in task_bindings
            if isinstance(item, dict)
            and item.get("domain") == "finance"
            and item.get("attempt") == 1
        ),
        None,
    )
    finance_a2 = next(
        (
            item
            for item in task_bindings
            if isinstance(item, dict)
            and item.get("domain") == "finance"
            and item.get("attempt") == 2
        ),
        None,
    )
    reviewers = sorted(
        (
            item
            for item in task_bindings
            if isinstance(item, dict) and item.get("role") == "REVIEWER"
        ),
        key=lambda item: item.get("attempt", 0),
    )
    if not isinstance(finance_a1, dict) or not isinstance(finance_a2, dict) or len(reviewers) != 2:
        fail("EXPERIENCE_SOURCE_TASKS")
        expected_required: dict[str, Any] = {}
    else:
        try:
            tool = _load(root / "golden-run" / "tool" / "invocation.json")
        except (OSError, ValueError, json.JSONDecodeError):
            tool = {}
            fail("EXPERIENCE_SOURCE_TOOL")
        expected_required = {
            "finance_a1_output_digest": finance_a1.get("observed_result_digest"),
            "reviewer_a1_output_digest": reviewers[0].get("observed_result_digest"),
            "tool_receipt_digest": tool.get("receipt", {}).get("digest"),
            "finance_a2_output_digest": finance_a2.get("observed_result_digest"),
            "reviewer_a2_output_digest": reviewers[1].get("observed_result_digest"),
        }
        if required != expected_required or not all(
            _valid_digest(value) for value in expected_required.values()
        ):
            fail("EXPERIENCE_SOURCE_EVIDENCE_BINDING")
        for item in (finance_a1, finance_a2, *reviewers):
            try:
                output = _load(
                    root
                    / "golden-run"
                    / "process-outputs"
                    / f"{item['task_id']}.json"
                )
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                fail("EXPERIENCE_SOURCE_OUTPUT")
                continue
            if _digest(output) != item.get("observed_result_digest"):
                fail("EXPERIENCE_SOURCE_OUTPUT")

    cases = evaluation.get("case_results")
    evaluation_gates = evaluation.get("gate_results")
    premise = evaluation.get("premise_lock")
    if (
        evaluation.get("candidate_ref") != candidate.get("id")
        or evaluation.get("candidate_digest") != candidate.get("digest")
        or evaluation.get("verdict") != "CANARY"
        or not isinstance(cases, list)
        or len(cases) != 8
        or {item.get("partition") for item in cases if isinstance(item, dict)}
        != EXPERIENCE_PARTITIONS
        or not all(isinstance(item, dict) and item.get("passed") is True for item in cases)
        or not isinstance(evaluation_gates, list)
        or not all(
            isinstance(item, dict) and item.get("passed") is True
            for item in evaluation_gates
        )
        or not isinstance(premise, dict)
        or premise.get("candidate") != candidate.get("digest")
        or premise.get("source_run") != run_id
        or premise.get("gold_boundary") != "single-run-seed:evaluator-only"
    ):
        fail("EXPERIENCE_EVALUATION")
    if (
        gate.get("run_id") != run_id
        or gate.get("owner_id") != EXPERIENCE_STEWARD_ID
        or gate.get("candidate_digest") != candidate.get("digest")
        or gate.get("evaluation_digest") != evaluation.get("digest")
        or gate.get("evaluation_verdict") != "CANARY"
        or gate.get("evaluation_partition_count") != 8
        or gate.get("observed_skill_head_digest") != candidate.get("base_package_digest")
        or gate.get("review_duration_ms", 0) < 4_000
        or gate.get("not_before_epoch_ms", -1)
        - gate.get("review_started_at_epoch_ms", 0)
        < 4_000
        or gate.get("candidate_only") is not True
        or gate.get("target_writes") != 0
    ):
        fail("EXPERIENCE_REVIEW_GATE")
    if (
        decision.get("run_id") != run_id
        or decision.get("decision") != "APPROVE"
        or decision.get("actor_id") != EXPERIENCE_STEWARD_ID
        or decision.get("candidate_digest") != candidate.get("digest")
        or decision.get("evaluation_digest") != evaluation.get("digest")
        or decision.get("review_gate_digest") != gate.get("digest")
        or decision.get("observed_skill_head_digest")
        != candidate.get("base_package_digest")
        or decision.get("review_wait_satisfied") is not True
        or decision.get("decided_at_epoch_ms", -1)
        < gate.get("not_before_epoch_ms", 0)
        or decision.get("candidate_only") is not True
        or decision.get("target_writes") != 0
    ):
        fail("EXPERIENCE_DECISION")

    history = release.get("release_history")
    dry_call = release.get("dry_call")
    if (
        not isinstance(history, list)
        or len(history) != 3
        or [item.get("to_state") for item in history if isinstance(item, dict)]
        != ["EVALUATED", "SHADOW", "CANARY"]
        or not all(isinstance(item, dict) and _sealed(item) for item in history)
    ):
        fail("EXPERIENCE_RELEASE_HISTORY")
        history = []
    previous_digest: str | None = None
    for item in history:
        if (
            item.get("previous_receipt_digest") != previous_digest
            or item.get("skill_name") != EXPERIENCE_TARGET_SKILL
            or item.get("source_candidate_ref") != candidate.get("id")
            or item.get("source_candidate_digest") != candidate.get("digest")
            or item.get("evaluation_receipt_digest") != evaluation.get("digest")
            or item.get("package_digest") != release.get("package_digest")
            or item.get("effective_package_digest") != release.get("package_digest")
            or item.get("actor_id") != "authority:skill-registry"
            or item.get("source_candidate_executable") is not False
            or item.get("predecessor_package_digest")
            != candidate.get("base_package_digest")
        ):
            fail("EXPERIENCE_RELEASE_HISTORY_BINDING")
        previous_digest = item.get("digest")
    if (
        release.get("run_id") != run_id
        or release.get("candidate_digest") != candidate.get("digest")
        or release.get("evaluation_digest") != evaluation.get("digest")
        or release.get("approval_digest") != decision.get("digest")
        or release.get("skill_name") != EXPERIENCE_TARGET_SKILL
        or release.get("predecessor_package_digest")
        != candidate.get("base_package_digest")
        or release.get("release_state") != "CANARY"
        or release.get("release_head_digest") != previous_digest
        or release.get("authorization_mode") != "RELEASE"
        or release.get("claim_boundary")
        != "CONTROLLED_LOCAL_SINGLE_RUN_SEED_NOT_PRODUCTION_GENERALIZATION"
        or release.get("candidate_only") is not True
        or release.get("target_writes") != 0
        or not isinstance(dry_call, dict)
        or dry_call.get("purpose") != "CONTROLLED_LOCAL_RELEASE_AUTHORIZATION_PROOF"
        or dry_call.get("current_quote_consumed") is not False
    ):
        fail("EXPERIENCE_RELEASE")
    dry_receipt = dry_call.get("receipt", {}) if isinstance(dry_call, dict) else {}
    dry_result = dry_call.get("result", {}) if isinstance(dry_call, dict) else {}
    if (
        not isinstance(dry_receipt, dict)
        or not isinstance(dry_result, dict)
        or not _sealed(dry_receipt)
        or dry_receipt.get("run_id") != run_id
        or dry_receipt.get("authorization_mode") != "RELEASE"
        or dry_receipt.get("release_receipt_digest") != release.get("release_head_digest")
        or dry_receipt.get("package_digest") != release.get("package_digest")
        or dry_receipt.get("outcome") != "SUCCESS"
        or dry_receipt.get("candidate_only") is not True
        or dry_receipt.get("target_writes") != 0
        or dry_receipt.get("output_digest") != _digest(dry_result)
        or dry_result.get("run_id") != run_id
        or dry_result.get("action") != "HANDOFF"
        or dry_result.get("package_digest") != release.get("package_digest")
        or dry_result.get("candidate_only") is not True
        or dry_result.get("target_writes") != 0
    ):
        fail("EXPERIENCE_RELEASE_DRY_CALL")

    facts = {
        "status": "APPROVED_CANARY",
        "maturity": "SINGLE_RUN_SEED",
        "outcome": "IMPROVE",
        "target_skill": EXPERIENCE_TARGET_SKILL,
        "evaluation_partition_count": len(cases) if isinstance(cases, list) else 0,
        "evaluation_pass_count": (
            sum(item.get("passed") is True for item in cases)
            if isinstance(cases, list)
            else 0
        ),
        "review_owner": EXPERIENCE_STEWARD_ID,
        "review_duration_ms": gate.get("review_duration_ms"),
        "release_state": "CANARY",
        "authorization_mode": "RELEASE",
        "dry_call_outcome": dry_receipt.get("outcome"),
        "discoverable": True,
        "loadable": True,
        "callable": True,
        "current_quote_consumed_candidate": False,
        "candidate_only": True,
        "canonical_target_writes": 0,
        "candidate_digest": candidate.get("digest"),
        "evaluation_digest": evaluation.get("digest"),
        "decision_digest": decision.get("digest"),
        "release_digest": release.get("digest"),
    }
    return failures, facts


def _entries(root: Path) -> list[dict[str, Any]]:
    failures = _pack_tree_failures(root)
    if failures:
        raise ValueError(failures[0])
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.name in EXCLUDED_INDEX_FILES or not path.is_file():
            continue
        payload = _read_regular_bytes(path)
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        )
    return entries


def _runtime_database_paths(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.name in FORBIDDEN_RUNTIME_DATABASE_NAMES
        and (path.exists() or path.is_symlink())
    )


def _private_task_intake_paths(root: Path) -> list[str]:
    exposed: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in EXCLUDED_INDEX_FILES:
            continue
        payload = _read_regular_bytes(path)
        if (
            any(marker in payload for marker in PRIVATE_TASK_INTAKE_MARKERS)
            or PRIVATE_JSON_FIELD_PATTERN.search(payload)
            or any(pattern.search(payload) for pattern in FORBIDDEN_PUBLIC_SECRET_PATTERNS)
        ):
            exposed.append(path.relative_to(root).as_posix())
    return exposed


def _verify_public_state_closure(
    *,
    state: dict[str, Any],
    evidence: dict[str, Any],
    quote: dict[str, Any],
) -> tuple[list[str], dict[str, Any] | None]:
    failures: list[str] = []

    def fail(code: str) -> None:
        if code not in failures:
            failures.append(code)

    event_chain = state.get("event_chain")
    if (
        not isinstance(event_chain, dict)
        or event_chain != evidence.get("event_chain")
        or event_chain.get("status") != "PASS"
    ):
        return ["PUBLIC_EVENT_CHAIN_PROJECTION"], None
    records = event_chain.get("records")
    execution = state.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    oac_activation = execution.get("oac_activation")
    task_intake = state.get("task_intake")
    oac_bound = isinstance(oac_activation, dict) or isinstance(task_intake, dict)
    if oac_bound:
        run_id = execution.get("run_id")
        competition = state.get("competition_evidence")
        competition = competition if isinstance(competition, dict) else {}
        if (
            not isinstance(oac_activation, dict)
            or not isinstance(task_intake, dict)
            or oac_activation != evidence.get("oac_activation_consumption")
            or task_intake != evidence.get("task_intake")
            or oac_activation.get("status") != "CONSUMED_BY_QUOTE_FORMATION"
            or task_intake.get("status") != "FORMATION_COMPLETED"
            or not isinstance(run_id, str)
            or not run_id.startswith("run:golden-competition:")
            or competition.get("run_id") != run_id
            or oac_activation.get("execution_run_id") != run_id
            or task_intake.get("run_id") != run_id
            or not _valid_digest(oac_activation.get("activation_binding_digest"))
            or task_intake.get("oac_activation_binding_digest")
            != oac_activation.get("activation_binding_digest")
            or not _valid_digest(task_intake.get("event_digest"))
            or task_intake.get("intake_persisted") is not True
            or task_intake.get("intake_canonical_target_writes") != 0
        ):
            fail("PUBLIC_OAC_TASK_BINDING")
        expected_types = OAC_BOUND_PUBLIC_EVENT_TYPES
        approval_sequences = {"launch_date": 10, "currency": 14}
        event_profile = "OAC_BOUND_TASK_INTAKE_V1"
    else:
        expected_types = LEGACY_PUBLIC_EVENT_TYPES
        approval_sequences = {"launch_date": 6, "currency": 10}
        event_profile = "LEGACY_QUOTE_ONLY_V1"
    if (
        not isinstance(records, list)
        or event_chain.get("events") != len(expected_types)
        or len(records) != len(expected_types)
    ):
        return ["PUBLIC_EVENT_CHAIN_COUNT"], None
    previous = "sha256:" + "0" * 64
    for sequence_no, (record, event_type) in enumerate(
        zip(records, expected_types, strict=True), start=1
    ):
        if (
            not isinstance(record, dict)
            or record.get("sequence_no") != sequence_no
            or record.get("event_type") != event_type
            or record.get("previous_digest") != previous
            or not _valid_digest(record.get("event_digest"))
        ):
            fail("PUBLIC_EVENT_CHAIN_LINK")
            break
        previous = record["event_digest"]
    if event_chain.get("head_digest") != previous:
        fail("PUBLIC_EVENT_CHAIN_HEAD")
    if oac_bound and isinstance(records, list):
        scopes = state.get("event_scopes")
        by_sequence = {
            item.get("sequence_no"): item
            for item in records
            if isinstance(item, dict)
        }

        def event_digest_at(sequence_no: int) -> str | None:
            item = by_sequence.get(sequence_no)
            return item.get("event_digest") if isinstance(item, dict) else None

        assert isinstance(task_intake, dict)
        if (
            not isinstance(scopes, dict)
            or scopes != evidence.get("event_scopes")
            or scopes.get("schema_version")
            != "orgrebase.workspace-event-scopes.v2"
            or scopes.get("layout") != "OAC_PREFIX_QUOTE_SUFFIX"
            or scopes.get("workspace_global")
            != {
                "status": "PASS",
                "events": 16,
                "head_digest": previous,
                "definition": "FULL_APPEND_ONLY_WORKSPACE_CHAIN",
            }
            or scopes.get("workspace_prelude")
            != {
                "status": "PASS",
                "events": 1,
                "first_sequence_no": 1,
                "last_sequence_no": 1,
                "head_digest": event_digest_at(1),
                "definition": "LEADING_WORKSPACE_INITIALIZATION_NOT_QUOTE_BUSINESS",
            }
            or scopes.get("oac_adaptation")
            != {
                "status": "PASS",
                "events": 3,
                "first_sequence_no": 2,
                "last_sequence_no": 4,
                "start_anchor_digest": event_digest_at(1),
                "head_digest": event_digest_at(4),
                "definition": "OAC_GOVERNANCE_EVENTS_ZERO_QUOTE_MUTATION_AUTHORITY",
            }
            or scopes.get("quote_business")
            != {
                "status": "PASS",
                "events": 12,
                "head_digest": previous,
                "first_sequence_no": 5,
                "last_sequence_no": 16,
                "start_anchor_digest": event_digest_at(4),
                "definition": "CONTIGUOUS_NON_OAC_SUFFIX",
            }
            or scopes.get("relationship")
            != {
                "event_layout": "OAC_PREFIX_QUOTE_SUFFIX",
                "honest_contiguous_windows_published": True,
                "quote_is_contiguous_prefix": False,
                "quote_is_contiguous_suffix": True,
                "oac_events_are_prefix": True,
                "oac_events_are_suffix": False,
            }
            or task_intake.get("event_digest") != event_digest_at(7)
        ):
            fail("PUBLIC_OAC_EVENT_SCOPES")

    current_quote = state.get("quote")
    if (
        not isinstance(current_quote, dict)
        or current_quote != quote.get("quote")
        or current_quote.get("id") != "work:quote-blue-harbor"
        or current_quote.get("version") != "v3"
    ):
        fail("PUBLIC_CURRENT_QUOTE")
    changes = state.get("changes")
    if not isinstance(changes, dict) or set(changes) != {"launch_date", "currency"}:
        fail("PUBLIC_CHANGE_PROJECTION")
        changes = {}
    quote_versions = ["v1"]
    approval_records = {
        item.get("sequence_no"): item
        for item in records
        if isinstance(item, dict)
        and item.get("event_type") == "WORKSPACE_CHANGE_APPROVED"
    }
    for kind, expected_version, expected_sequence in (
        ("launch_date", "v2", approval_sequences["launch_date"]),
        ("currency", "v3", approval_sequences["currency"]),
    ):
        change = changes.get(kind)
        if not isinstance(change, dict):
            fail(f"PUBLIC_CHANGE:{kind}")
            continue
        projected_quote = (
            change.get("outcome", {}).get("outcome", {}).get("quote")
        )
        approval = change.get("approval", {}).get("approval_review_evidence")
        if (
            not isinstance(projected_quote, dict)
            or projected_quote.get("version") != expected_version
            or not isinstance(approval, dict)
            or approval.get("review_wait_satisfied") is not True
            or approval.get("review_duration_ms", 0) < 4_000
            or approval.get("approval_observed_at_epoch_ms", -1)
            < approval.get("review_not_before_epoch_ms", 0)
            or approval.get("event_sequence_no") != expected_sequence
            or approval.get("event_digest")
            != approval_records.get(expected_sequence, {}).get("event_digest")
        ):
            fail(f"PUBLIC_CHANGE_CLOSURE:{kind}")
        quote_versions.append(expected_version)
    if quote_versions != ["v1", "v2", "v3"]:
        fail("PUBLIC_QUOTE_LINEAGE")
    facts = {
        "source": "CONTENT_ADDRESSED_PUBLIC_JSON_PROJECTIONS",
        "event_profile": event_profile,
        "oac_bound": oac_bound,
        "task_intake_event_bound": oac_bound,
        "event_chain_verified": True,
        "event_count": len(records),
        "event_head_digest": previous,
        "quote_versions": quote_versions,
        "approval_count": len(approval_records),
        "runtime_database_included": False,
    }
    return failures, facts


def verify(root: Path) -> dict[str, Any]:
    unsafe_paths = _pack_tree_failures(root)
    if unsafe_paths:
        return {
            "schema_version": "orgrebase.golden-pilot-evidence-verification.v1",
            "status": "FAIL",
            "verification_mode": "STDLIB_ONLY_NO_PRODUCT_IMPORTS",
            "product_imports": 0,
            "run_id": None,
            "pack_digest": None,
            "entry_count": 0,
            "file_verification": "FAIL_CLOSED_UNSAFE_PACK_TREE",
            "public_state_verification": "FAIL",
            "causal_verification": "FAIL",
            "experience_verification": "FAIL",
            "failures": unsafe_paths,
        }
    root = root.resolve()
    failures: list[str] = []
    try:
        manifest = _load(root / "manifest.json")
        state = _load(root / "state.json")
        evidence = _load(root / "evidence-export.json")
        quote = _load(root / "quote-export.json")
        summary = _load(root / "golden-run" / "summary.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "FAIL", "failures": [f"LOAD:{exc}"], "product_imports": 0}

    manifest_schema = manifest.get("schema_version")
    current_manifest = manifest_schema == "orgrebase.golden-pilot-evidence-manifest.v3"
    exposed_databases = _runtime_database_paths(root)
    if exposed_databases:
        failures.append("RUNTIME_DATABASE_EXPOSED:" + ",".join(exposed_databases))
    exposed_private_intake = _private_task_intake_paths(root)
    if exposed_private_intake:
        failures.append(
            "PRIVATE_TASK_INTAKE_EXPOSED:" + ",".join(exposed_private_intake)
        )
    if manifest_schema in {
        "orgrebase.golden-pilot-evidence-manifest.v1",
        "orgrebase.golden-pilot-evidence-manifest.v2",
    }:
        failures.append("PUBLIC_MANIFEST_UPGRADE_REQUIRED")
    if manifest.get("digest") != _digest({k: v for k, v in manifest.items() if k != "digest"}):
        failures.append("MANIFEST_DIGEST")
    entries = _entries(root)
    declared_entries = manifest.get("files", {}).get("entries")
    if current_manifest:
        if declared_entries != entries:
            failures.append("FILE_INDEX")
        if manifest.get("files", {}).get("pack_digest") != _digest(entries):
            failures.append("PACK_DIGEST")
    elif declared_entries != entries:
        failures.append("LEGACY_FILE_INDEX")
    if manifest.get("files", {}).get("entry_count") != len(entries):
        failures.append("ENTRY_COUNT")
    workflow = manifest.get("workflow", {})
    if manifest_schema not in {
        "orgrebase.golden-pilot-evidence-manifest.v1",
        "orgrebase.golden-pilot-evidence-manifest.v2",
        "orgrebase.golden-pilot-evidence-manifest.v3",
    }:
        failures.append("MANIFEST_SCHEMA")
    if current_manifest and manifest.get("privacy") != {
        "runtime_database_included": False,
        "forbidden_runtime_database_names": sorted(
            FORBIDDEN_RUNTIME_DATABASE_NAMES
        ),
        "verification_source": "CONTENT_ADDRESSED_PUBLIC_JSON_PROJECTIONS",
    }:
        failures.append("PUBLIC_PRIVACY_CONTRACT")
    if (
        not isinstance(workflow, dict)
        or workflow.get("orchestration_node_count") != 8
        or workflow.get("native_task_binding_count") != 7
        or "task_count" in workflow
        or workflow.get("skill_evaluation_partition_count") != summary.get("skill_evaluation_partition_count")
        or workflow.get("skill_evaluation_case_count", 8) != summary.get("skill_evaluation_case_count", 8)
    ):
        failures.append("MANIFEST_WORKFLOW_CARDINALITY")
    for name, value in (("summary", summary), ("evidence", evidence), ("quote", quote)):
        if value.get("digest") != _digest({k: v for k, v in value.items() if k != "digest"}):
            failures.append(f"{name.upper()}_DIGEST")

    run_id = manifest.get("run_id")
    correlation_id = manifest.get("correlation_id")
    if not isinstance(run_id, str) or not run_id.startswith("run:golden-competition:"):
        failures.append("RUN_ID")
    if not isinstance(correlation_id, str) or correlation_id == run_id:
        failures.append("CORRELATION_ID")
    causal_failures: list[str] = []
    if isinstance(run_id, str) and isinstance(correlation_id, str):
        causal_failures = _verify_causal_evidence(
            root,
            summary=summary,
            run_id=run_id,
            correlation_id=correlation_id,
        )
        failures.extend(causal_failures)
    public_state_failures: list[str] = []
    experience_failures: list[str] = []
    if current_manifest and isinstance(run_id, str) and isinstance(correlation_id, str):
        public_state_failures, public_state_facts = _verify_public_state_closure(
            state=state,
            evidence=evidence,
            quote=quote,
        )
        failures.extend(public_state_failures)
        if (
            public_state_facts is None
            or workflow.get("public_state_closure") != public_state_facts
        ):
            failures.append("MANIFEST_PUBLIC_STATE_CLOSURE")
        expected_model = _expected_model_manifest(summary)
        if expected_model is None or workflow.get("model") != expected_model:
            failures.append("MANIFEST_MODEL_EVIDENCE")
        collaboration_model = (
            state.get("competition_evidence", {})
            .get("agent_collaboration", {})
            .get("reviewer", {})
        )
        if (
            state.get("competition_evidence") != evidence.get("competition_evidence")
            or state.get("competition_evidence", {}).get("model_provider")
            != summary.get("model_provider")
            or collaboration_model.get("model_provider") != summary.get("model_provider")
            or collaboration_model.get("model_attempts")
            != summary.get("reviewer_model_attempts")
            or collaboration_model.get("target_writes") != 0
        ):
            failures.append("MODEL_STATE_EVIDENCE_PROJECTION")
        experience_failures, experience_facts = _verify_experience_evidence(
            root,
            state=state,
            evidence=evidence,
            summary=summary,
            run_id=run_id,
            correlation_id=correlation_id,
        )
        failures.extend(experience_failures)
        if experience_facts is None or workflow.get("experience_governance") != experience_facts:
            failures.append("MANIFEST_EXPERIENCE_EVIDENCE")
    if {
        summary.get("run_id"),
        state.get("execution", {}).get("run_id"),
        evidence.get("competition_evidence", {}).get("run_id"),
    } != {run_id}:
        failures.append("RUN_BINDING")
    if state.get("stage") != "QUOTE_V3" or quote.get("quote", {}).get("version") != "v3":
        failures.append("QUOTE_V3")
    collaboration = state.get("competition_evidence", {}).get("agent_collaboration", {})
    if collaboration.get("reviewer", {}).get("attempt_1", {}).get("verdict") != "REPLAN":
        failures.append("REVIEWER_REPLAN")
    if collaboration.get("reviewer", {}).get("attempt_2", {}).get("verdict") != "PASS":
        failures.append("REVIEWER_PASS")
    if collaboration.get("tool", {}).get("status") != "SUCCEEDED":
        failures.append("TOOL")
    if collaboration.get("skill", {}).get("action") != "APPLY_QUOTE":
        failures.append("SKILL")
    if summary.get("canonical_target_writes") != 0:
        failures.append("CANDIDATE_WRITE")
    for kind in ("launch_date", "currency"):
        timing = (
            state.get("changes", {}).get(kind, {}).get("approval", {}).get("approval_review_evidence", {})
        )
        if (
            timing.get("review_wait_satisfied") is not True
            or timing.get("review_duration_ms", 0) < 4_000
            or timing.get("approval_observed_at_epoch_ms", -1) < timing.get("review_not_before_epoch_ms", 0)
        ):
            failures.append(f"APPROVAL_WAIT:{kind}")

    return {
        "schema_version": "orgrebase.golden-pilot-evidence-verification.v1",
        "status": "PASS" if not failures else "FAIL",
        "verification_mode": "STDLIB_ONLY_NO_PRODUCT_IMPORTS",
        "product_imports": 0,
        "run_id": run_id,
        "pack_digest": manifest.get("files", {}).get("pack_digest"),
        "entry_count": len(entries),
        "file_verification": (
            "FULL_CONTENT_ADDRESSED_PUBLIC_FILES_NO_RUNTIME_DATABASE"
            if current_manifest
            else "LEGACY_MANIFEST_REJECTED_FOR_PUBLIC_FREEZE"
        ),
        "public_state_verification": (
            "PASS"
            if current_manifest and not public_state_failures
            else ("UPGRADE_REQUIRED" if not current_manifest else "FAIL")
        ),
        "causal_verification": "PASS" if not causal_failures else "FAIL",
        "experience_verification": (
            "PASS"
            if current_manifest and not experience_failures
            else ("LEGACY_NOT_REQUIRED" if not current_manifest else "FAIL")
        ),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.root)
    if args.output is not None:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
