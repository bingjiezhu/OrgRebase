"""Governed promotion seam from a candidate-only OAC run to canonical Workspace writes.

AgentTeams, Tools, and Skills remain proposal-plane components.  This module
binds their exact retained roots to a deterministic Workspace change, persists
the candidate-to-preview relation, and requires a domain-owner approval before
delegating the only canonical write to :class:`WorkspaceService`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError, RebaseReceipt, VersionedObject
from orgrebase.workspace.models import WorkspaceRebaseReceipt
from orgrebase.workspace.service import WorkspaceService

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
ChangeKind = Literal["launch_date", "currency"]

PROMOTION_ARTIFACT_ID = "candidate-promotion:enterprise-quote@v1"
PROMOTION_MEDIA_TYPE = "application/vnd.orgrebase.candidate-promotion+json"
PREVIEW_MEDIA_TYPE = "application/vnd.orgrebase.promotion-preview-binding+json"
APPROVAL_MEDIA_TYPE = "application/vnd.orgrebase.promotion-approval+json"
APPLY_MEDIA_TYPE = "application/vnd.orgrebase.promotion-apply-receipt+json"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _AddressedModel(_StrictModel):
    digest: Digest

    @model_validator(mode="after")
    def _verify_digest(self):
        payload = self.model_dump(mode="json", exclude={"digest"})
        if self.digest != sha256_digest(payload):
            raise ValueError("content digest mismatch")
        return self

    @classmethod
    def seal(cls, **values: Any):
        return cls.model_validate({**values, "digest": sha256_digest(values)})


class CandidateChainRoots(_StrictModel):
    source_receipt_digest: Digest
    source_payload_digest: Digest
    native_receipt_digest: Digest
    coalition_result_binding_digest: Digest
    domain_result_digests: dict[str, Digest]
    tool_receipt_digest: Digest
    tool_result_digest: Digest
    skill_package_digest: Digest
    skill_invocation_receipt_digest: Digest
    skill_result_digest: Digest

    @model_validator(mode="after")
    def _exact_domains(self):
        if set(self.domain_result_digests) != {"product", "legal", "finance", "gtm"}:
            raise ValueError("exact four-domain roots required")
        if len(set(self.domain_result_digests.values())) != 4:
            raise ValueError("four-domain roots must be distinct")
        return self


class ProposedChange(_StrictModel):
    kind: ChangeKind
    owner_id: str = Field(min_length=1)
    change_id: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    base_version: str = Field(min_length=1)
    proposed_version: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    profile_binding_digest: Digest


class CandidatePromotionBundle(_AddressedModel):
    schema_version: Literal["orgrebase.candidate-promotion-bundle.v1"]
    evidence_class: Literal["CONTROLLED_LOCAL_CANDIDATE_TO_GOVERNED_APPLY"]
    run_id: str = Field(min_length=1)
    candidate_terminal_state: Literal["CANDIDATE_ACCEPTED"]
    candidate_action: Literal["APPLY_QUOTE"]
    roots: CandidateChainRoots
    base_quote_ref: str = Field(min_length=1)
    base_quote_digest: Digest
    base_graph_ref: str = Field(min_length=1)
    base_graph_digest: Digest
    proposed_changes: tuple[ProposedChange, ProposedChange]
    proposal_plane_target_writes: Literal[0]
    canonical_writer: Literal["STATESTORE_REBASE_WORKFLOW"]
    approval_policy: Literal["EXACT_PREVIEW_AND_PROMOTION_DIGEST"]
    claim_boundary: Literal[
        "AGENT_SKILL_TOOL_RECOMMEND;DETERMINISTIC_CONTROL_PREVIEWS;OWNER_APPROVES;STATESTORE_WRITES"
    ]

    @model_validator(mode="after")
    def _change_order(self):
        if tuple(item.kind for item in self.proposed_changes) != (
            "launch_date",
            "currency",
        ):
            raise ValueError("canonical change order required")
        return self


class PromotionPreviewBinding(_AddressedModel):
    schema_version: Literal["orgrebase.promotion-preview-binding.v1"]
    run_id: str = Field(min_length=1)
    kind: ChangeKind
    promotion_digest: Digest
    workspace_preview_artifact_digest: Digest
    workspace_preview_digest: Digest
    snapshot_digest: Digest
    owner_id: str = Field(min_length=1)
    canonical_target_writes: Literal[0]


class PromotionApproval(_AddressedModel):
    schema_version: Literal["orgrebase.promotion-approval.v1"]
    run_id: str = Field(min_length=1)
    kind: ChangeKind
    promotion_digest: Digest
    preview_binding_digest: Digest
    workspace_preview_digest: Digest
    workspace_approval_digest: Digest
    actor_id: str = Field(min_length=1)
    input_mode: Literal["CONTROLLED_LOCAL_SCRIPTED_COMMAND", "EXTERNAL_HUMAN_COMMAND"]
    canonical_target_writes: Literal[0]


class PromotionApplyReceipt(_AddressedModel):
    schema_version: Literal["orgrebase.promotion-apply-receipt.v1"]
    status: Literal["APPLIED"]
    run_id: str = Field(min_length=1)
    kind: ChangeKind
    promotion_digest: Digest
    promotion_approval_digest: Digest
    workspace_approval_digest: Digest
    workspace_rebase_receipt_digest: Digest
    workspace_outcome_artifact_digest: Digest
    before_quote_ref: str = Field(min_length=1)
    before_quote_digest: Digest
    after_quote_ref: str = Field(min_length=1)
    after_quote_digest: Digest
    before_graph_ref: str = Field(min_length=1)
    before_graph_digest: Digest
    after_graph_ref: str = Field(min_length=1)
    after_graph_digest: Digest
    canonical_write_set: tuple[Literal["work:quote_acme", "graph:workspace"], ...]
    canonical_target_writes: Literal[2]
    proposal_plane_target_writes: Literal[0]
    event_chain_head_digest: Digest

    @model_validator(mode="after")
    def _write_set(self):
        if self.canonical_write_set != ("work:quote_acme", "graph:workspace"):
            raise ValueError("exact canonical write set required")
        return self


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"PROMOTION_EVIDENCE_JSON_INVALID:{path.name}") from exc
    if not isinstance(value, dict):
        raise IntegrityError(f"PROMOTION_EVIDENCE_OBJECT_REQUIRED:{path.name}")
    return value


def _require_addressed(value: Mapping[str, Any], field: str, error: str) -> str:
    declared = value.get(field)
    body = {key: item for key, item in value.items() if key != field}
    if not isinstance(declared, str) or declared != sha256_digest(body):
        raise IntegrityError(error)
    return declared


def _candidate_chain(pack: Path, run_id: str) -> CandidateChainRoots:
    summary = _load_json(pack / "summary.json")
    native = _load_json(pack / "agentteams/lifecycle-receipt.json")
    coalition = _load_json(pack / "agentteams/coalition-result-binding.json")
    invocation = _load_json(pack / "skills/quote-compose/invocation-receipt.json")
    result = _load_json(pack / "skills/quote-compose/result.json")
    operations = _load_json(pack / "operations/summary.json")

    _require_addressed(summary, "digest", "PROMOTION_PARENT_SUMMARY_DIGEST_MISMATCH")
    _require_addressed(native, "receipt_digest", "PROMOTION_NATIVE_RECEIPT_DIGEST_MISMATCH")
    _require_addressed(
        coalition,
        "coalition_digest",
        "PROMOTION_COALITION_BINDING_DIGEST_MISMATCH",
    )
    _require_addressed(invocation, "digest", "PROMOTION_SKILL_RECEIPT_DIGEST_MISMATCH")
    _require_addressed(operations, "digest", "PROMOTION_OPERATIONS_DIGEST_MISMATCH")
    if not all(
        item.get("run_id") == run_id
        for item in (summary, native, coalition, invocation, operations)
    ):
        raise IntegrityError("PROMOTION_RUN_ID_MISMATCH")
    if (
        summary.get("status") != "PASS"
        or summary.get("terminal_state") != "CANDIDATE_ACCEPTED"
        or summary.get("canonical_target_writes") != 0
        or native.get("canonical_target_writes") != 0
        or coalition.get("candidate_only") is not True
        or coalition.get("target_writes") != 0
        or invocation.get("outcome") != "SUCCESS"
        or invocation.get("candidate_only") is not True
        or invocation.get("target_writes") != 0
        or result.get("action") != "APPLY_QUOTE"
        or result.get("candidate_only") is not True
        or result.get("target_writes") != 0
    ):
        raise IntegrityError("PROMOTION_CANDIDATE_CHAIN_NOT_ADMISSIBLE")
    result_digest = sha256_digest(result)
    if result_digest != invocation.get("output_digest"):
        raise IntegrityError("PROMOTION_SKILL_RESULT_DIGEST_MISMATCH")
    members = coalition.get("members")
    if not isinstance(members, list) or len(members) != 4:
        raise IntegrityError("PROMOTION_EXACT_FOUR_DOMAIN_MEMBERS_REQUIRED")
    domain_roots = {
        str(item.get("domain")): str(item.get("observed_result_digest"))
        for item in members
        if isinstance(item, Mapping)
    }
    if result.get("domain_result_digests") != domain_roots:
        raise IntegrityError("PROMOTION_DOMAIN_ROOTS_MISMATCH")
    equality_pairs = (
        (summary.get("native_receipt_digest"), native.get("receipt_digest")),
        (summary.get("coalition_result_binding_digest"), coalition.get("coalition_digest")),
        (coalition.get("native_receipt_digest"), native.get("receipt_digest")),
        (summary.get("skill_invocation_receipt_digest"), invocation.get("digest")),
        (summary.get("skill_package_digest"), invocation.get("package_digest")),
        (summary.get("skill_package_digest"), result.get("package_digest")),
        (summary.get("tool_receipt_digest"), result.get("dependency_tool_receipt_digest")),
        (summary.get("dependency_result_digest"), result.get("dependency_result_digest")),
        (operations.get("native_receipt_digest"), native.get("receipt_digest")),
        (operations.get("coalition_result_binding_digest"), coalition.get("coalition_digest")),
        (operations.get("skill_invocation_receipt_digest"), invocation.get("digest")),
        (operations.get("tool_receipt_digest"), result.get("dependency_tool_receipt_digest")),
        (operations.get("tool_result_digest"), result.get("dependency_result_digest")),
    )
    if any(left != right for left, right in equality_pairs):
        raise IntegrityError("PROMOTION_CANDIDATE_ROOT_SUBSTITUTION")
    return CandidateChainRoots(
        source_receipt_digest=str(summary["source_receipt_digest"]),
        source_payload_digest=str(summary["source_payload_digest"]),
        native_receipt_digest=str(native["receipt_digest"]),
        coalition_result_binding_digest=str(coalition["coalition_digest"]),
        domain_result_digests=domain_roots,
        tool_receipt_digest=str(result["dependency_tool_receipt_digest"]),
        tool_result_digest=str(result["dependency_result_digest"]),
        skill_package_digest=str(invocation["package_digest"]),
        skill_invocation_receipt_digest=str(invocation["digest"]),
        skill_result_digest=result_digest,
    )


def build_candidate_promotion(
    service: WorkspaceService,
    *,
    evidence_pack: str | Path,
    run_id: str,
) -> CandidatePromotionBundle:
    if service.effective_workflow_run_id != run_id:
        raise IntegrityError("PROMOTION_SERVICE_RUN_ID_MISMATCH")
    quote = service.current_quote()
    graph = service.current_graph_pointer()
    if quote.version != "v1" or graph.version != "v1":
        raise IntegrityError("PROMOTION_BASE_QUOTE_V1_REQUIRED")
    roots = _candidate_chain(Path(evidence_pack).resolve(), run_id)
    changes = []
    for expected_kind in ("launch_date", "currency"):
        binding = next(
            (item for item in service.profile.change_family if item.kind == expected_kind),
            None,
        )
        if binding is None:
            raise IntegrityError(f"PROMOTION_PROFILE_CHANGE_MISSING:{expected_kind}")
        profile_body = binding.model_dump(mode="json")
        changes.append(
            ProposedChange(
                kind=expected_kind,
                owner_id=binding.owner_id,
                change_id=binding.change_id,
                object_id=binding.object_id,
                base_version=binding.base_version,
                proposed_version=binding.proposed_version,
                purpose=binding.purpose,
                profile_binding_digest=sha256_digest(profile_body),
            )
        )
    return CandidatePromotionBundle.seal(
        schema_version="orgrebase.candidate-promotion-bundle.v1",
        evidence_class="CONTROLLED_LOCAL_CANDIDATE_TO_GOVERNED_APPLY",
        run_id=run_id,
        candidate_terminal_state="CANDIDATE_ACCEPTED",
        candidate_action="APPLY_QUOTE",
        roots=roots.model_dump(mode="json"),
        base_quote_ref=quote.ref,
        base_quote_digest=quote.digest,
        base_graph_ref=graph.ref,
        base_graph_digest=graph.digest,
        proposed_changes=tuple(item.model_dump(mode="json") for item in changes),
        proposal_plane_target_writes=0,
        canonical_writer="STATESTORE_REBASE_WORKFLOW",
        approval_policy="EXACT_PREVIEW_AND_PROMOTION_DIGEST",
        claim_boundary=(
            "AGENT_SKILL_TOOL_RECOMMEND;DETERMINISTIC_CONTROL_PREVIEWS;"
            "OWNER_APPROVES;STATESTORE_WRITES"
        ),
    )


def persist_candidate_promotion(
    service: WorkspaceService,
    bundle: CandidatePromotionBundle,
) -> str:
    bundle = CandidatePromotionBundle.model_validate(bundle.model_dump(mode="json"))
    if bundle.run_id != service.effective_workflow_run_id:
        raise IntegrityError("PROMOTION_SERVICE_RUN_ID_MISMATCH")
    if service.store.artifact_exists(PROMOTION_ARTIFACT_ID):
        stored = service.store.load_artifact(PROMOTION_ARTIFACT_ID, PROMOTION_MEDIA_TYPE)
        existing = CandidatePromotionBundle.model_validate(stored.payload)
        if existing.digest != bundle.digest:
            raise IntegrityError("PROMOTION_ARTIFACT_CONFLICT")
        return existing.digest
    with service.store.transaction() as connection:
        service.store.save_artifact(
            connection,
            PROMOTION_ARTIFACT_ID,
            PROMOTION_MEDIA_TYPE,
            bundle.model_dump(mode="json"),
        )
        service.store.append_event(
            connection,
            "CANDIDATE_PROMOTION_ADMITTED",
            {
                "run_id": bundle.run_id,
                "promotion_digest": bundle.digest,
                "coalition_result_binding_digest": (
                    bundle.roots.coalition_result_binding_digest
                ),
                "skill_result_digest": bundle.roots.skill_result_digest,
                "proposal_plane_target_writes": 0,
            },
        )
    return bundle.digest


def load_candidate_promotion(service: WorkspaceService) -> CandidatePromotionBundle:
    stored = service.store.load_artifact(PROMOTION_ARTIFACT_ID, PROMOTION_MEDIA_TYPE)
    bundle = CandidatePromotionBundle.model_validate(stored.payload)
    return bundle


def _change(bundle: CandidatePromotionBundle, kind: ChangeKind) -> ProposedChange:
    matches = [item for item in bundle.proposed_changes if item.kind == kind]
    if len(matches) != 1:
        raise IntegrityError(f"PROMOTION_EXACT_CHANGE_REQUIRED:{kind}")
    return matches[0]


def prepare_promoted_change(
    service: WorkspaceService,
    kind: ChangeKind,
    *,
    promotion_digest: str,
) -> PromotionPreviewBinding:
    bundle = load_candidate_promotion(service)
    if bundle.digest != promotion_digest:
        raise IntegrityError("PROMOTION_DIGEST_MISMATCH")
    change = _change(bundle, kind)
    workspace = service.preview_command(kind)
    payload = PromotionPreviewBinding.seal(
        schema_version="orgrebase.promotion-preview-binding.v1",
        run_id=bundle.run_id,
        kind=kind,
        promotion_digest=bundle.digest,
        workspace_preview_artifact_digest=workspace["artifact_digest"],
        workspace_preview_digest=workspace["preview_digest"],
        snapshot_digest=workspace["bundle"]["snapshot_digest"],
        owner_id=change.owner_id,
        canonical_target_writes=0,
    )
    artifact_id = f"promotion-preview:{kind.replace('_', '-')}@r1"
    if service.store.artifact_exists(artifact_id):
        stored = service.store.load_artifact(artifact_id, PREVIEW_MEDIA_TYPE)
        return PromotionPreviewBinding.model_validate(stored.payload)
    with service.store.transaction() as connection:
        service.store.save_artifact(
            connection,
            artifact_id,
            PREVIEW_MEDIA_TYPE,
            payload.model_dump(mode="json"),
        )
        service.store.append_event(
            connection,
            "PROMOTION_CHANGE_PREVIEW_BOUND",
            {
                "run_id": bundle.run_id,
                "kind": kind,
                "promotion_digest": bundle.digest,
                "preview_binding_digest": payload.digest,
                "workspace_preview_digest": payload.workspace_preview_digest,
            },
        )
    return payload


def approve_promoted_change(
    service: WorkspaceService,
    kind: ChangeKind,
    *,
    actor_id: str,
    preview_binding_digest: str,
    input_mode: Literal[
        "CONTROLLED_LOCAL_SCRIPTED_COMMAND", "EXTERNAL_HUMAN_COMMAND"
    ] = "CONTROLLED_LOCAL_SCRIPTED_COMMAND",
) -> PromotionApproval:
    bundle = load_candidate_promotion(service)
    artifact_id = f"promotion-preview:{kind.replace('_', '-')}@r1"
    preview = PromotionPreviewBinding.model_validate(
        service.store.load_artifact(artifact_id, PREVIEW_MEDIA_TYPE).payload
    )
    change = _change(bundle, kind)
    if (
        preview.digest != preview_binding_digest
        or preview.promotion_digest != bundle.digest
        or actor_id != change.owner_id
        or preview.owner_id != change.owner_id
    ):
        raise IntegrityError("PROMOTION_APPROVAL_BINDING_INVALID")
    workspace = service.approve_change(
        kind,
        actor_id=actor_id,
        preview_digest=preview.workspace_preview_digest,
    )
    approval = PromotionApproval.seal(
        schema_version="orgrebase.promotion-approval.v1",
        run_id=bundle.run_id,
        kind=kind,
        promotion_digest=bundle.digest,
        preview_binding_digest=preview.digest,
        workspace_preview_digest=preview.workspace_preview_digest,
        workspace_approval_digest=workspace["approval_digest"],
        actor_id=actor_id,
        input_mode=input_mode,
        canonical_target_writes=0,
    )
    approval_artifact_id = f"promotion-approval:{kind.replace('_', '-')}@r1"
    if service.store.artifact_exists(approval_artifact_id):
        stored = service.store.load_artifact(approval_artifact_id, APPROVAL_MEDIA_TYPE)
        existing = PromotionApproval.model_validate(stored.payload)
        if existing.digest != approval.digest:
            raise IntegrityError("PROMOTION_APPROVAL_CONFLICT")
        return existing
    with service.store.transaction() as connection:
        service.store.save_artifact(
            connection,
            approval_artifact_id,
            APPROVAL_MEDIA_TYPE,
            approval.model_dump(mode="json"),
        )
        service.store.append_event(
            connection,
            "PROMOTION_CHANGE_APPROVED",
            {
                "run_id": bundle.run_id,
                "kind": kind,
                "promotion_digest": bundle.digest,
                "promotion_approval_digest": approval.digest,
                "workspace_approval_digest": approval.workspace_approval_digest,
                "actor_id": actor_id,
                "input_mode": input_mode,
            },
        )
    return approval


def _workspace_outcome_event_digest(
    service: WorkspaceService,
    *,
    kind: ChangeKind,
    artifact_id: str,
    artifact_digest: str,
) -> str:
    """Return the immutable event that committed the exact Workspace outcome.

    A later retry may observe more events than the original apply call did.  The
    promotion receipt therefore binds the historical outcome commit, not the
    mutable current event-chain head.
    """

    matches = [
        event
        for event in service.store.event_envelopes()
        if event["event_type"] == "WORKSPACE_CHANGE_OUTCOME_RECORDED"
        and event["payload"].get("kind") == kind
        and event["payload"].get("artifact_id") == artifact_id
        and event["payload"].get("artifact_digest") == artifact_digest
    ]
    if len(matches) != 1:
        raise IntegrityError("PROMOTION_WORKSPACE_OUTCOME_EVENT_INVALID")
    return str(matches[0]["event_digest"])


def _promotion_predecessors(
    service: WorkspaceService,
    *,
    bundle: CandidatePromotionBundle,
    kind: ChangeKind,
    predecessor_quote_ref: str,
    predecessor_quote_digest: str,
) -> tuple[str, str, str, str]:
    """Recover exact pre-apply Quote and graph roots from durable artifacts."""

    if kind == "launch_date":
        values = (
            bundle.base_quote_ref,
            bundle.base_quote_digest,
            bundle.base_graph_ref,
            bundle.base_graph_digest,
        )
    else:
        try:
            launch = PromotionApplyReceipt.model_validate(
                service.store.load_artifact(
                    "promotion-apply:launch-date@r1",
                    APPLY_MEDIA_TYPE,
                ).payload
            )
        except KeyError as exc:
            raise IntegrityError("PROMOTION_PREDECESSOR_RECEIPT_REQUIRED") from exc
        values = (
            launch.after_quote_ref,
            launch.after_quote_digest,
            launch.after_graph_ref,
            launch.after_graph_digest,
        )
    if values[:2] != (predecessor_quote_ref, predecessor_quote_digest):
        raise IntegrityError("PROMOTION_WORKSPACE_PREDECESSOR_CONFLICT")
    return values


def _reconstruct_apply_receipt(
    service: WorkspaceService,
    *,
    bundle: CandidatePromotionBundle,
    approval: PromotionApproval,
    kind: ChangeKind,
) -> PromotionApplyReceipt:
    """Reconstruct a promotion outcome after the canonical Workspace commit.

    The persisted promotion approval is the durable intent.  Workspace's
    outcome artifact and event are the durable canonical result.  Rebuilding
    from those two records closes the crash window between canonical Apply and
    saving this projection receipt without adding another state machine.
    """

    outcome_record = service._outcome_record(kind)
    approval_record = service._approval_record(kind)
    if outcome_record is None or approval_record is None:
        raise IntegrityError("PROMOTION_WORKSPACE_OUTCOME_REQUIRED")
    binding = approval_record.get("binding")
    if not isinstance(binding, Mapping):
        raise IntegrityError("PROMOTION_WORKSPACE_APPROVAL_BINDING_REQUIRED")
    if (
        approval_record.get("approval_digest") != approval.workspace_approval_digest
        or binding.get("approval_digest") != approval.workspace_approval_digest
        or binding.get("workflow_run_id") != bundle.run_id
        or binding.get("change_kind") != kind
    ):
        raise IntegrityError("PROMOTION_WORKSPACE_APPROVAL_OUTCOME_CONFLICT")

    outcome = outcome_record["outcome"]
    if (
        outcome.get("kind") != kind
        or outcome.get("approval_digest") != approval.workspace_approval_digest
    ):
        raise IntegrityError("PROMOTION_WORKSPACE_OUTCOME_BINDING_INVALID")
    try:
        rebase = RebaseReceipt.model_validate(outcome["rebase_receipt"])
        workspace_rebase = WorkspaceRebaseReceipt.model_validate(
            outcome["workspace_rebase_receipt"]
        )
        after_quote = VersionedObject.model_validate(outcome["quote"])
        after_graph = VersionedObject.model_validate(outcome["graph_pointer"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IntegrityError("PROMOTION_WORKSPACE_OUTCOME_INVALID") from exc
    if (
        rebase.workflow_run_id != bundle.run_id
        or rebase.status != "COMPLETED"
        or rebase.approval_digest != approval.workspace_approval_digest
        or workspace_rebase.status != "COMPLETED"
        or workspace_rebase.base_rebase_receipt_digest != rebase.digest
        or workspace_rebase.graph_pointer_ref != after_graph.ref
    ):
        raise IntegrityError("PROMOTION_WORKSPACE_APPLY_RUN_BINDING_INVALID")

    before_quote_ref = binding.get("predecessor_ref")
    before_quote_digest = binding.get("predecessor_digest")
    if not isinstance(before_quote_ref, str) or not isinstance(before_quote_digest, str):
        raise IntegrityError("PROMOTION_WORKSPACE_PREDECESSOR_INVALID")
    (
        before_quote_ref,
        before_quote_digest,
        before_graph_ref,
        before_graph_digest,
    ) = _promotion_predecessors(
        service,
        bundle=bundle,
        kind=kind,
        predecessor_quote_ref=before_quote_ref,
        predecessor_quote_digest=before_quote_digest,
    )
    expected_versions = {
        "launch_date": ("v1", "v2"),
        "currency": ("v2", "v3"),
    }[kind]
    if (
        not before_quote_ref.endswith(f"@{expected_versions[0]}")
        or after_quote.version != expected_versions[1]
        or not before_graph_ref.endswith(f"@{expected_versions[0]}")
        or after_graph.version != expected_versions[1]
    ):
        raise IntegrityError("PROMOTION_WORKSPACE_SUCCESSOR_SEQUENCE_INVALID")

    event_digest = _workspace_outcome_event_digest(
        service,
        kind=kind,
        artifact_id=str(outcome_record["artifact_id"]),
        artifact_digest=str(outcome_record["artifact_digest"]),
    )
    return PromotionApplyReceipt.seal(
        schema_version="orgrebase.promotion-apply-receipt.v1",
        status="APPLIED",
        run_id=bundle.run_id,
        kind=kind,
        promotion_digest=bundle.digest,
        promotion_approval_digest=approval.digest,
        workspace_approval_digest=approval.workspace_approval_digest,
        workspace_rebase_receipt_digest=rebase.digest,
        workspace_outcome_artifact_digest=str(outcome_record["artifact_digest"]),
        before_quote_ref=before_quote_ref,
        before_quote_digest=before_quote_digest,
        after_quote_ref=after_quote.ref,
        after_quote_digest=after_quote.digest,
        before_graph_ref=before_graph_ref,
        before_graph_digest=before_graph_digest,
        after_graph_ref=after_graph.ref,
        after_graph_digest=after_graph.digest,
        canonical_write_set=("work:quote_acme", "graph:workspace"),
        canonical_target_writes=2,
        proposal_plane_target_writes=0,
        event_chain_head_digest=event_digest,
    )


def apply_promoted_change(
    service: WorkspaceService,
    kind: ChangeKind,
    *,
    promotion_approval_digest: str,
) -> PromotionApplyReceipt:
    bundle = load_candidate_promotion(service)
    approval_id = f"promotion-approval:{kind.replace('_', '-')}@r1"
    approval = PromotionApproval.model_validate(
        service.store.load_artifact(approval_id, APPROVAL_MEDIA_TYPE).payload
    )
    if (
        approval.digest != promotion_approval_digest
        or approval.promotion_digest != bundle.digest
        or approval.run_id != bundle.run_id
    ):
        raise IntegrityError("PROMOTION_APPLY_BINDING_INVALID")
    receipt_id = f"promotion-apply:{kind.replace('_', '-')}@r1"
    if service.store.artifact_exists(receipt_id):
        stored = service.store.load_artifact(receipt_id, APPLY_MEDIA_TYPE)
        return PromotionApplyReceipt.model_validate(stored.payload)
    service.apply_approved_change(
        kind,
        approval_digest=approval.workspace_approval_digest,
    )
    receipt = _reconstruct_apply_receipt(
        service,
        bundle=bundle,
        approval=approval,
        kind=kind,
    )
    with service.store.transaction() as connection:
        service.store.save_artifact(
            connection,
            receipt_id,
            APPLY_MEDIA_TYPE,
            receipt.model_dump(mode="json"),
        )
        service.store.append_event(
            connection,
            "PROMOTION_CHANGE_APPLIED",
            {
                "run_id": bundle.run_id,
                "kind": kind,
                "promotion_digest": bundle.digest,
                "promotion_approval_digest": approval.digest,
                "promotion_apply_receipt_digest": receipt.digest,
                "canonical_write_set": list(receipt.canonical_write_set),
            },
        )
    return receipt
