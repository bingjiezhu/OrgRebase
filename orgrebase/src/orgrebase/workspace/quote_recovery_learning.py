"""Reviewed content candidate for the Quote evidence-recovery profile.

This module authors bytes only.  It does not publish a Skill, call a model, or
grant runtime authority.  The restricted registry is the sole consumer.
"""

from __future__ import annotations

from typing import Any

from orgrebase.digest import canonical_json
from orgrebase.workspace.skill_packages import (
    QUOTE_RECOVERY_PROFILE,
    SKILL_CONTENT_CHECKLIST_SCHEMA,
    SkillContentBundle,
    SkillPackageRegistry,
)

TARGET_SKILL = "structured-domain-handoff"


def quote_recovery_content_bundle(
    registry: SkillPackageRegistry | None = None,
) -> SkillContentBundle:
    """Build the fixed allowlisted resources over the exact installed head."""

    selected = registry or SkillPackageRegistry()
    predecessor = selected.load(TARGET_SKILL)
    checklist = {
        "schema_version": SKILL_CONTENT_CHECKLIST_SCHEMA,
        "profile_id": QUOTE_RECOVERY_PROFILE,
        "rule_id": "require-verified-recovery-receipts",
        "applies_when_path": "/candidate_bundle/profile_id",
        "applies_when_equals": QUOTE_RECOVERY_PROFILE,
        "required_digest_paths": [
            "/candidate_bundle/evidence/outcome_artifact_digest",
            "/candidate_bundle/evidence/request_digest",
            "/candidate_bundle/evidence/resume_digest",
        ],
        "failure_action": "ABSTAIN",
        "failure_reason": "QUOTE_RECOVERY_EVIDENCE_INCOMPLETE",
        "allowed_tools": [],
        "target_writes_max": 0,
    }
    instructions = (
        b"# Quote evidence recovery\n\n"
        b"When a handoff declares the workspace Quote evidence-recovery profile, "
        b"apply the reviewed typed checklist before returning HANDOFF. The content "
        b"does not authorize source reads, tools, target writes, or approval.\n"
    )
    reference = (
        b"# Receipt contract\n\n"
        b"A complete recovery handoff binds the request, resume, and resulting "
        b"outcome artifact by non-zero sha256 digests. Missing receipts require "
        b"ABSTAIN; all other profiles keep the predecessor behavior.\n"
    )
    return SkillContentBundle.create(
        target_skill=TARGET_SKILL,
        predecessor_package_digest=predecessor.package_digest,
        instruction_bytes=instructions,
        reference_bytes=reference,
        checklist_bytes=canonical_json(checklist).encode("utf-8"),
    )


def recovery_handoff_input(
    *,
    run_id: str,
    task_id: str,
    delegation_id: str,
    delegation_task_digest: str,
    context_projection_digest: str,
    request_digest: str | None,
    resume_digest: str | None,
    outcome_artifact_digest: str | None,
    domain: str = "quote-operations",
) -> dict[str, Any]:
    """Create public input without copying business values into the learner."""

    evidence = {
        name: value
        for name, value in {
            "request_digest": request_digest,
            "resume_digest": resume_digest,
            "outcome_artifact_digest": outcome_artifact_digest,
        }.items()
        if value is not None
    }
    return {
        "run_id": run_id,
        "task_id": task_id,
        "delegation_id": delegation_id,
        "delegation_task_digest": delegation_task_digest,
        "context_projection_digest": context_projection_digest,
        "candidate_bundle": {
            "domain": domain,
            "profile_id": QUOTE_RECOVERY_PROFILE,
            "evidence": evidence,
        },
    }
