"""One declared public task mapping; discovery never consumes evaluator labels."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from orgrebase.workspace.outcome_lab import LabToolRequest

TASK_ID = "113"
SCOPE = "PUBLIC_TASK_DB_PROJECTION_WITH_SCRIPTED_CONFIRMATION"
UNSUPPORTED = (
    "interactive-user-simulation",
    "natural-language-assertion-grading",
    "real-user-identity",
    "real-refund-effects",
    "other-retail-tasks",
    "organization-ground-truth",
    "enterprise-roi",
)


def public_candidate(scenario: dict[str, Any], environment) -> dict[str, Any]:
    """The only planner input is public user instructions and permitted read results."""
    instructions = scenario.get("instructions", {})
    if (
        instructions.get("domain") != "retail"
        or instructions.get("reason_for_call") != "You want to cancel all pending orders."
        or instructions.get("known_info") != "You name is Yara Muller and your zip code is 85041."
        or instructions.get("task_instructions")
        != "You are mysterious and  don't want to reveal the reason for cancellation until the agent asks. If asked for reason, say you ordered the items by mistake."
    ):
        raise ValueError("PUBLIC_LAB_TASK_MAPPING_UNSUPPORTED")
    requests = []
    discovery = []

    def read(tool, arguments):
        if environment.tool_mutates_state(tool):
            raise ValueError("PUBLIC_LAB_DISCOVERY_MUST_BE_READ_ONLY")
        request = LabToolRequest(tool=tool, arguments=arguments)
        result = environment.call(request, timeout=10)
        requests.append(request.model_dump(mode="json"))
        discovery.append({"request": request.model_dump(mode="json"), "result": deepcopy(result)})
        return result

    user_id = read("find_user_id_by_name_zip", {"first_name": "Yara", "last_name": "Muller", "zip": "85041"})
    details = read("get_user_details", {"user_id": user_id})
    if not isinstance(details.get("orders"), list) or len(details["orders"]) > 8:
        raise ValueError("PUBLIC_LAB_ORDER_DISCOVERY_BOUND_EXCEEDED")
    pending = []
    for order_id in details["orders"]:
        order = read("get_order_details", {"order_id": order_id})
        if order["status"] == "pending":
            pending.append(order_id)
    if not pending or len(pending) > 2:
        raise ValueError("PUBLIC_LAB_PENDING_ORDER_PROFILE_MISMATCH")
    for order_id in pending:
        requests.append(
            {
                "tool": "cancel_pending_order",
                "arguments": {"order_id": order_id, "reason": "ordered by mistake"},
            }
        )
    return {
        "requests": requests,
        "discovery": discovery,
        "pending_orders": pending,
        "user_id": user_id,
        "confirmation": "SCRIPTED_FROM_PUBLIC_USER_INSTRUCTIONS",
    }


def oracle_expectations(task: dict[str, Any], seed: dict[str, Any]) -> dict[str, Any]:
    """Evaluator-only expected values are derived before any candidate execution."""
    if task["id"] != TASK_ID:
        raise ValueError("PUBLIC_LAB_TASK_ID_UNSUPPORTED")
    expected = {}
    for action in task["evaluation_criteria"]["actions"]:
        if action["name"] != "cancel_pending_order":
            raise ValueError("PUBLIC_LAB_ORACLE_ACTION_UNSUPPORTED")
        order_id = action["arguments"]["order_id"]
        order = seed["orders"][order_id]
        if order["status"] != "pending" or not all(
            seed["users"][order["user_id"]]["payment_methods"][payment["payment_method_id"]]["source"]
            == "credit_card"
            for payment in order["payment_history"]
        ):
            raise ValueError("PUBLIC_LAB_ORACLE_PAYMENT_MAPPING_UNSUPPORTED")
        escaped = order_id.replace("~", "~0").replace("/", "~1")
        expected[f"/orders/{escaped}/status"] = "cancelled"
        expected[f"/orders/{escaped}/cancel_reason"] = action["arguments"]["reason"]
        expected[f"/orders/{escaped}/payment_history"] = deepcopy(order["payment_history"]) + [
            {**payment, "transaction_type": "refund"} for payment in order["payment_history"]
        ]
    if len(expected) != 6:
        raise ValueError("PUBLIC_LAB_ORACLE_SCOPE_UNSUPPORTED")
    return expected


class DiscoveryReplay:
    """Recheck deterministic candidate construction from committed public read results."""

    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.index = 0

    def tool_mutates_state(self, tool):
        if tool not in {"find_user_id_by_name_zip", "get_user_details", "get_order_details"}:
            raise ValueError("PUBLIC_LAB_DISCOVERY_TOOL_INVALID")
        return False

    def call(self, request, *, timeout):
        if self.index >= len(self.rows):
            raise ValueError("PUBLIC_LAB_DISCOVERY_TRACE_INCOMPLETE")
        row = self.rows[self.index]
        self.index += 1
        if row["request"] != request.model_dump(mode="json"):
            raise ValueError("PUBLIC_LAB_DISCOVERY_REQUEST_MISMATCH")
        return row["result"]
