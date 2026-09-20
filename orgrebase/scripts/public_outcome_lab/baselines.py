"""The four published role selections for the single mapped public experiment."""

from .artifacts import encode


def baseline_roles(snapshot: dict, plan: dict) -> dict[str, list[str]]:
    spec = snapshot["spec"]
    # This mapping has no dependency edges; graph traversal reaches only its initiator.
    if spec["dependencyEdges"] or spec["completeness"]["discoveryRoleRef"] != "role:review":
        raise ValueError("PUBLIC_LAB_BASELINE_MAPPING_UNSUPPORTED")
    declared = sorted(role["roleId"] for role in spec["roleDefinitions"])
    if declared != ["role:cancel", "role:review"]:
        raise ValueError("PUBLIC_LAB_BASELINE_ROLES_UNSUPPORTED")
    return {
        "fixed-team": declared,
        "initiator-only": ["role:review"],
        "graph-only": ["role:review"],
        "oac": sorted({item["roleDefinitionRef"] for item in plan["spec"]["roleInstances"]}),
    }


def verify_baseline_roles(snapshot: dict, plan: dict, recorded: dict) -> dict[str, list[str]]:
    expected = baseline_roles(snapshot, plan)
    if encode(recorded) != encode(expected):
        raise ValueError("PUBLIC_LAB_BASELINE_ROLES_MISMATCH")
    return expected
