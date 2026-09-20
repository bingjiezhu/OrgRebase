"""Identity of the bounded quote qualification suite, including retained v1 evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from orgrebase.digest import sha256_digest

REVISION = "orgrebase.quote-skill-qualification.v2"
CASES = (
    ("replay", "REPLAY", "APPLY_QUOTE"),
    ("held_out", "HELD_OUT", "APPLY_QUOTE"),
    ("negative_transfer", "NEGATIVE_TRANSFER", "KEEP_CURRENT"),
    ("permission", "PERMISSION", "DENY"),
    ("injection", "INJECTION", "ABSTAIN"),
    ("malformed", "MALFORMED", "ABSTAIN"),
    ("resource_or_deadline", "RESOURCE_OR_DEADLINE", "ABSTAIN"),
    ("canary", "CANARY", "CANARY"),
    ("domain_substitution", "MALFORMED", "ABSTAIN"),
)
SUITE_DIGEST = sha256_digest({"revision": REVISION, "cases": CASES})


def qualification_premise(package: Any) -> dict[str, str]:
    return {
        "package": package.package_digest,
        "runtime": "restricted-skill-registry@1.0.0",
        "dependencies": sha256_digest(package.manifest["dependencies"]),
        "gold_boundary": "evaluator-only:not-packaged",
        "qualification_suite_revision": REVISION,
        "qualification_suite_digest": SUITE_DIGEST,
    }


def has_complete_case_identity(evaluation: Mapping[str, Any], run_id: str) -> bool:
    """Accept exact legacy eight or explicitly bound current nine; never a subset."""
    premise = evaluation.get("premise_lock")
    results = evaluation.get("case_results")
    if not isinstance(premise, Mapping) or not isinstance(results, list):
        return False
    if not ({"qualification_suite_revision", "qualification_suite_digest"} & premise.keys()):
        expected = CASES[:-1]
    elif (premise.get("qualification_suite_revision") == REVISION
          and premise.get("qualification_suite_digest") == SUITE_DIGEST):
        expected = CASES
    else:
        return False
    identities = [(item.get("case_ref"), item.get("partition"))
                  for item in results if isinstance(item, Mapping)]
    return len(results) == len(expected) and sorted(identities, key=str) == sorted(
        [(f"{run_id}:quote-compose:{name}", partition) for name, partition, _ in expected], key=str)
