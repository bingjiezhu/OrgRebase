"""Thin coordinator for the Agent-assisted OAC product path.

The coordinator owns no business truth and introduces no second state machine.
It joins four already independent trust boundaries:

* a live, schema-constrained OAC mapping candidate;
* the existing four-second human source-admission gate;
* a least-authority task-agent context envelope compiled from Formation; and
* one OAC-bound, candidate-only shadow execution.

Only content-addressed receipts are persisted below ``runtime_root``.  Provider
credentials, raw prompts, local paths, and provider request identifiers never
enter the product view.  Interrupted evidence is held for investigation rather
than silently rerunning a provider or relabelling the deterministic baseline as
live Agent evidence.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from orgrebase.agentteams_source import load_agentteams_source
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.context_residency import (
    TaskAgentContextEnvelope,
    TaskAgentContextEnvelopeBuilder,
)
from orgrebase.workspace.demand_formation import (
    TaskFormationDecisionReceipt,
    build_task_formation_decision_receipt,
)
from orgrebase.workspace.formation import MEDIA
from orgrebase.workspace.model_provider import VERTEX_MODEL_ID
from orgrebase.workspace.models import (
    ActorContextProjection,
    CoalitionPlan,
    TaskContextManifest,
    TaskTemplateVersion,
)
from orgrebase.workspace.oac_agent_adaptation import (
    OACAgentMappingReceipt,
    require_verified_agent_mapping_receipt,
    run_oac_agent_adaptation,
)
from orgrebase.workspace.oac_quote_adaptation import (
    OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
    OACAdapterActivationBinding,
    OACAdapterCapsule,
    OACOwnerReviewGate,
    OACOwnerReviewSummary,
    OACQuoteAdaptationService,
    OACSourceAdmissionApproval,
    quote_adaptation_rule_set_digest,
)
from orgrebase.workspace.oac_shadow_execution import (
    OACBoundShadowExecutionReceipt,
    run_oac_bound_shadow_execution,
)
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.templates import default_capability_cards

_VIEW_SCHEMA_VERSION = "orgrebase.oac-agentic-runtime-view.v1"
_MAPPING_RECEIPT_NAME = "mapping-receipt.json"
_CONTEXT_ENVELOPE_NAME = "context-envelope.json"
_FORMATION_DECISION_NAME = "task-formation-decision-receipt.json"
_SHADOW_RECEIPT_NAME = "receipt.json"
_CONTEXT_BINDING_MEDIA = "application/vnd.orgrebase.oac-context-binding+json"
_EXECUTION_MODES = frozenset({"FROZEN_REPLAY", "OFFLINE_LOCAL", "LIVE_VERTEX"})
_ACTION_AGENT_PREPARE = "AGENT_PREPARE"
_ACTION_STRUCTURED_PREPARE = "STRUCTURED_PREPARE"
_ACTION_EXECUTE_SHADOW = "EXECUTE_SHADOW"
_SAME_RUN_LAYERS = (
    "SOURCE",
    "CONTEXT",
    "AGENTTEAMS",
    "TOOL",
    "SKILL",
    "OTLP",
    "CANDIDATE",
)


class OACAgenticRuntimeError(RuntimeError):
    """Stable fail-closed error raised at coordinator boundaries."""


def _read_object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OACAgenticRuntimeError(code) from exc
    if not isinstance(value, dict):
        raise OACAgenticRuntimeError(code)
    return value


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically retain one receipt; a crash can never expose half JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _directory_has_evidence(path: Path) -> bool:
    if not path.exists():
        return False
    return not path.is_dir() or any(path.iterdir())


class OACAgenticRuntime:
    """Coordinate the live mapping -> owner gate -> shadow proof journey.

    ``agent_runner`` and ``shadow_runner`` are injectable so contract tests do
    not require network access.  Production defaults retain the existing live
    Vertex/AgentTeams and controlled-local shadow implementations.
    """

    def __init__(
        self,
        *,
        runtime_root: str | Path,
        repo_root: str | Path,
        checkout: str | Path,
        lock_path: str | Path,
        pack_path: str | Path,
        frozen_golden_root: str | Path,
        adaptation_service: OACQuoteAdaptationService,
        workspace_service: WorkspaceService,
        agent_runner: Callable[..., OACAgentMappingReceipt] = run_oac_agent_adaptation,
        shadow_runner: Callable[..., OACBoundShadowExecutionReceipt] = run_oac_bound_shadow_execution,
        shadow_verifier: Callable[..., Mapping[str, Any]] | None = None,
        agent_provider: Any | None = None,
        shadow_competition_runner: Callable[..., dict[str, Any]] | None = None,
        shadow_model_provider: str = "vertex-ai",
        execution_mode: str = "LIVE_VERTEX",
        vertex_project: str | None = None,
        ollama_endpoint: str | None = None,
        late_attempt_fencing_receipt: str | Path | None = None,
    ) -> None:
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.checkout = Path(checkout).expanduser().resolve()
        self.lock_path = Path(lock_path).expanduser().resolve()
        self.pack_path = Path(pack_path).expanduser().resolve()
        self.frozen_golden_root = Path(frozen_golden_root).expanduser().resolve()
        self.adaptation_service = adaptation_service
        self.workspace_service = workspace_service
        self.agent_runner = agent_runner
        self.shadow_runner = shadow_runner
        self.shadow_verifier = shadow_verifier
        self.agent_provider = agent_provider
        self.shadow_competition_runner = shadow_competition_runner
        self.shadow_model_provider = str(shadow_model_provider).strip().lower()
        self.execution_mode = str(execution_mode).strip().upper()
        self.vertex_project = (
            str(vertex_project).strip()
            if vertex_project is not None and str(vertex_project).strip()
            else None
        )
        self.ollama_endpoint = ollama_endpoint
        self.late_attempt_fencing_receipt = late_attempt_fencing_receipt

        runtime = adaptation_service.runtime
        workspace_runtime = workspace_service.runtime_configuration
        if runtime is None:
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_EXACT_PACK_REQUIRED")
        if (
            workspace_runtime is None
            or runtime.pack_digest != workspace_runtime.pack_digest
            or adaptation_service.profile.digest != workspace_service.profile_digest
            or runtime.profile.digest != workspace_service.profile_digest
        ):
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_SERVICE_BINDING_MISMATCH")
        if not self.shadow_model_provider:
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_SHADOW_PROVIDER_REQUIRED")
        if self.execution_mode not in _EXECUTION_MODES:
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_EXECUTION_MODE_INVALID")
        if self.runtime_root.exists() and not self.runtime_root.is_dir():
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_ROOT_DIRECTORY_REQUIRED")

    @property
    def _mapping_dir(self) -> Path:
        return self.runtime_root / "agent-mapping"

    @property
    def _mapping_path(self) -> Path:
        return self._mapping_dir / _MAPPING_RECEIPT_NAME

    @property
    def _context_dir(self) -> Path:
        return self.runtime_root / "context-residency"

    @property
    def _context_path(self) -> Path:
        return self._context_dir / _CONTEXT_ENVELOPE_NAME

    @property
    def _formation_decision_path(self) -> Path:
        return self._context_dir / _FORMATION_DECISION_NAME

    @property
    def _shadow_dir(self) -> Path:
        return self.runtime_root / "shadow-execution"

    @property
    def _shadow_path(self) -> Path:
        return self._shadow_dir / _SHADOW_RECEIPT_NAME

    @property
    def _shadow_verification_path(self) -> Path:
        return self._shadow_dir / "verification.json"

    def _new_mapping_allowed(self) -> bool:
        """Only LIVE_VERTEX may start the mapper, which is Vertex-only."""

        return self.execution_mode == "LIVE_VERTEX"

    def _new_shadow_allowed(self) -> bool:
        """Frozen mode never starts work; offline mode may use only a local provider."""

        return self.execution_mode == "LIVE_VERTEX" or (
            self.execution_mode == "OFFLINE_LOCAL" and self.shadow_model_provider == "ollama-local"
        )

    def _new_shadow_blocked_reason(self) -> str:
        if self.execution_mode == "FROZEN_REPLAY":
            return "OAC_AGENTIC_RUNTIME_FROZEN_SHADOW_RECEIPT_REQUIRED"
        return (
            "OAC_AGENTIC_RUNTIME_NEW_VERTEX_SHADOW_BLOCKED"
            if self.shadow_model_provider == "vertex-ai"
            else "OAC_AGENTIC_RUNTIME_NEW_NONLOCAL_SHADOW_BLOCKED"
        )

    def _execution_policy(
        self,
        *,
        adaptation: Mapping[str, Any],
        mapping: OACAgentMappingReceipt | None,
        shadow: OACBoundShadowExecutionReceipt | None,
    ) -> dict[str, Any]:
        """Project truthful action capabilities without provider configuration secrets."""

        mapping_retained = mapping is not None
        mapping_incomplete = not mapping_retained and self._mapping_evidence_incomplete()
        shadow_retained = shadow is not None
        available_actions: list[str] = []
        blocked_reasons: dict[str, list[str]] = {}

        if mapping_incomplete:
            blocked_reasons[_ACTION_AGENT_PREPARE] = ["OAC_AGENTIC_RUNTIME_MAPPING_EVIDENCE_INCOMPLETE"]
        elif mapping_retained or self._new_mapping_allowed():
            available_actions.append(_ACTION_AGENT_PREPARE)
        elif self.execution_mode == "FROZEN_REPLAY":
            blocked_reasons[_ACTION_AGENT_PREPARE] = ["OAC_AGENTIC_RUNTIME_FROZEN_MAPPING_RECEIPT_REQUIRED"]
        else:
            blocked_reasons[_ACTION_AGENT_PREPARE] = ["OAC_AGENTIC_RUNTIME_NEW_VERTEX_MAPPING_BLOCKED"]

        if (
            self.execution_mode == "OFFLINE_LOCAL"
            and not mapping_retained
            and not mapping_incomplete
            and adaptation.get("status") == "PACK_OBSERVED"
        ):
            available_actions.append(_ACTION_STRUCTURED_PREPARE)

        if shadow_retained:
            available_actions.append(_ACTION_EXECUTE_SHADOW)
        elif not self._new_shadow_allowed():
            blocked_reasons[_ACTION_EXECUTE_SHADOW] = [self._new_shadow_blocked_reason()]
        elif mapping is None or mapping.status != "VALIDATED_CANDIDATE":
            blocked_reasons[_ACTION_EXECUTE_SHADOW] = ["OAC_AGENTIC_RUNTIME_LIVE_MAPPING_REQUIRED"]
        elif adaptation.get("status") != "READY_FOR_ORGREBASE":
            blocked_reasons[_ACTION_EXECUTE_SHADOW] = ["OAC_AGENTIC_RUNTIME_OWNER_APPROVAL_REQUIRED"]
        else:
            available_actions.append(_ACTION_EXECUTE_SHADOW)

        if mapping_retained:
            mapping_source = "RETAINED_VERTEX_RECEIPT"
        elif mapping_incomplete:
            mapping_source = "INCOMPLETE_MAPPING_EVIDENCE"
        elif self.execution_mode == "LIVE_VERTEX":
            mapping_source = "NEW_LIVE_VERTEX"
        elif self.execution_mode == "FROZEN_REPLAY":
            mapping_source = "FROZEN_RECEIPT_REQUIRED"
        else:
            mapping_source = "STRUCTURED_MATERIALS"

        mapping_provider = "vertex-ai"
        if mapping_incomplete:
            mapping_provider = None
        elif mapping_source == "STRUCTURED_MATERIALS":
            mapping_provider = "none"

        return {
            "mode": self.execution_mode,
            "mapping_source": mapping_source,
            "mapping_model_provider": mapping_provider,
            "model_provider": self.shadow_model_provider,
            "mapping_will_call_external_model": (
                not mapping_retained and not mapping_incomplete and self._new_mapping_allowed()
            ),
            "shadow_will_call_external_model": (
                not shadow_retained
                and self._new_shadow_allowed()
                and self.shadow_model_provider in {"vertex-ai", "deepseek"}
            ),
            "available_actions": available_actions,
            "blocked_reasons": blocked_reasons,
        }

    def _baseline_digest(self) -> tuple[tuple[Any, ...], str]:
        mappings, _gaps = self.adaptation_service.deterministic_mapping_baseline()
        return mappings, sha256_digest([item.digest for item in mappings])

    def _validate_mapping_receipt(
        self,
        value: OACAgentMappingReceipt | Mapping[str, Any],
    ) -> OACAgentMappingReceipt:
        try:
            selected = (
                value.revalidated()
                if isinstance(value, OACAgentMappingReceipt)
                else OACAgentMappingReceipt.model_validate(dict(value))
            )
        except (TypeError, ValueError) as exc:
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_MAPPING_RECEIPT_INVALID") from exc
        runtime = self.adaptation_service.runtime
        assert runtime is not None
        _baseline, baseline_digest = self._baseline_digest()
        if (
            selected.adaptation_run_id != self.adaptation_service.adaptation_run_id
            or selected.profile_digest != self.adaptation_service.profile.digest
            or selected.pack_digest != runtime.pack_digest
            or selected.baseline_mapping_set_digest != baseline_digest
            or selected.native_agentteams.run_id != self.adaptation_service.adaptation_run_id
            or selected.candidate_only is not True
            or selected.canonical_target_writes != 0
            or selected.effect_ceiling != "ZERO_EXTERNAL_EFFECTS"
            or selected.human_approval_granted is not False
            or selected.oac_source_admitted is not False
        ):
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_MAPPING_RECEIPT_BINDING_MISMATCH")
        if selected.status == "VALIDATED_CANDIDATE":
            try:
                return require_verified_agent_mapping_receipt(
                    selected,
                    adaptation_run_id=self.adaptation_service.adaptation_run_id,
                    profile_digest=self.adaptation_service.profile.digest,
                    pack_digest=runtime.pack_digest,
                    baseline_mapping_set_digest=baseline_digest,
                )
            except (RuntimeError, ValueError) as exc:
                raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_MAPPING_RECEIPT_INVALID") from exc
        if (
            selected.status != "HOLD"
            or selected.validation.verdict != "HOLD"
            or selected.accepted_mappings
            or selected.accepted_mapping_set_digest is not None
        ):
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_MAPPING_RECEIPT_INVALID")
        return selected

    def _load_mapping_receipt(self) -> OACAgentMappingReceipt | None:
        if not self._mapping_path.is_file():
            return None
        return self._validate_mapping_receipt(
            _read_object(
                self._mapping_path,
                "OAC_AGENTIC_RUNTIME_MAPPING_RECEIPT_LOAD_FAILED",
            )
        )

    def _mapping_evidence_incomplete(self) -> bool:
        if self._mapping_path.is_file():
            return False
        if _directory_has_evidence(self._mapping_dir):
            return True
        return self.adaptation_service.view().get("agent_mapping") is not None

    def _formation_decision_and_context_from_formation(
        self,
        *,
        organizational_intent_digest: str,
    ) -> tuple[TaskFormationDecisionReceipt, TaskAgentContextEnvelope]:
        formation = self.workspace_service.formation
        task = formation.default_request(self.workspace_service.profile)
        prepared = formation.prepare_quote(task)

        def one_payload(media_type: str, code: str) -> dict[str, Any]:
            matches = [item.payload for item in prepared.artifact_writes if item.media_type == media_type]
            if len(matches) != 1:
                raise OACAgenticRuntimeError(code)
            return matches[0]

        try:
            coalition = CoalitionPlan.model_validate(
                one_payload(
                    MEDIA["coalition"],
                    "OAC_AGENTIC_RUNTIME_FORMATION_COALITION_INVALID",
                )
            )
            template = TaskTemplateVersion.model_validate(
                one_payload(
                    MEDIA["template"],
                    "OAC_AGENTIC_RUNTIME_FORMATION_TEMPLATE_INVALID",
                )
            )
            task_context = TaskContextManifest.model_validate(
                one_payload(
                    MEDIA["context"],
                    "OAC_AGENTIC_RUNTIME_FORMATION_CONTEXT_INVALID",
                )
            )
            projections = tuple(
                ActorContextProjection.model_validate(item.payload)
                for item in prepared.artifact_writes
                if item.media_type == MEDIA["projection"]
                and item.payload.get("task_context_ref") == task_context.ref
            )
            adaptation = self.adaptation_service.view()
            organization_snapshot = adaptation.get("organization_snapshot")
            organizational_demand = adaptation.get("organizational_demand")
            if not isinstance(organization_snapshot, Mapping) or not isinstance(
                organizational_demand, Mapping
            ):
                raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_ORGANIZATIONAL_DEMAND_REQUIRED")
            receipt = build_task_formation_decision_receipt(
                organization_snapshot=organization_snapshot,
                organizational_demand=organizational_demand,
                task=task,
                template=template,
                coalition=coalition,
                capability_cards=default_capability_cards(template.ref),
                authority_policy_digests={
                    "oac_source_admission": quote_adaptation_rule_set_digest(),
                },
            )
            if receipt.organizational_demand_digest != organizational_intent_digest:
                raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_FORMATION_DEMAND_LINEAGE_MISMATCH")
            context = TaskAgentContextEnvelopeBuilder.build(
                task=task,
                coalition=coalition,
                capability_cards=default_capability_cards(template.ref),
                task_context=task_context,
                actor_projections=projections,
                admitted_organizational_intent_digest=(organizational_intent_digest),
                task_formation_decision_receipt_digest=receipt.digest,
                now=formation.clock.now(),
            )
            return receipt, context
        except (IntegrityError, TypeError, ValueError) as exc:
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_FORMATION_CONTEXT_INVALID") from exc

    def _context_envelope_from_formation(
        self,
        *,
        organizational_intent_digest: str,
    ) -> TaskAgentContextEnvelope:
        _receipt, context = self._formation_decision_and_context_from_formation(
            organizational_intent_digest=organizational_intent_digest
        )
        return context

    def _load_or_compile_context(
        self,
        *,
        organizational_intent_digest: str,
    ) -> TaskAgentContextEnvelope:
        expected_receipt, expected = self._formation_decision_and_context_from_formation(
            organizational_intent_digest=organizational_intent_digest
        )
        if self._context_path.is_file():
            if not self._formation_decision_path.is_file():
                raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_FORMATION_DECISION_EVIDENCE_INCOMPLETE")
            try:
                retained_receipt = TaskFormationDecisionReceipt.model_validate(
                    _read_object(
                        self._formation_decision_path,
                        "OAC_AGENTIC_RUNTIME_FORMATION_DECISION_EVIDENCE_INVALID",
                    )
                )
                retained = TaskAgentContextEnvelope.model_validate(
                    _read_object(
                        self._context_path,
                        "OAC_AGENTIC_RUNTIME_CONTEXT_EVIDENCE_INVALID",
                    )
                )
            except ValueError as exc:
                raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_CONTEXT_EVIDENCE_INVALID") from exc
            if (
                retained_receipt.digest != expected_receipt.digest
                or retained.digest != expected.digest
                or retained.task_formation_decision_receipt_digest != retained_receipt.digest
            ):
                raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_CONTEXT_LINEAGE_MISMATCH")
            return retained
        if _directory_has_evidence(self._context_dir):
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_CONTEXT_EVIDENCE_INCOMPLETE")
        _write_json_atomic(
            self._formation_decision_path,
            expected_receipt.model_dump(mode="json"),
        )
        _write_json_atomic(
            self._context_path,
            expected.model_dump(mode="json"),
        )
        return expected

    def _context_binding_payload(
        self, binding: OACAdapterActivationBinding, context: TaskAgentContextEnvelope,
    ) -> dict[str, Any]:
        store = self.adaptation_service.store
        return {
            "tenant_id": store.tenant_id,
            "workspace_id": store.workspace_id,
            "adaptation_run_id": binding.adaptation_run_id,
            "execution_run_id": binding.execution_run_id,
            "activation_binding_digest": binding.digest,
            "adapter_capsule_digest": binding.adapter_capsule_digest,
            "task_ref": context.task_ref,
            "task_digest": context.task_digest,
            "formation_decision_receipt_digest": context.task_formation_decision_receipt_digest,
            "context_envelope_digest": context.digest,
        }

    def _bind_compiled_context(
        self, binding: OACAdapterActivationBinding, context: TaskAgentContextEnvelope,
    ) -> None:
        """Anchor a freshly verified compilation in the existing immutable store."""

        store = self.adaptation_service.store
        with store.transaction() as connection:
            store.save_artifact(
                connection, f"oac-context-binding:{binding.digest}", _CONTEXT_BINDING_MEDIA,
                self._context_binding_payload(binding, context),
            )

    def _context_has_trusted_binding(
        self, binding: OACAdapterActivationBinding, context: TaskAgentContextEnvelope,
    ) -> bool:
        expected = self._context_binding_payload(binding, context)
        try:
            retained = self.adaptation_service.store.load_artifact(
                f"oac-context-binding:{binding.digest}", _CONTEXT_BINDING_MEDIA,
            ).payload
        except KeyError:
            # Older consumed runs already retain these roots in the control
            # plane's atomic Formation commit. Reading history must not migrate it.
            consumed = self.workspace_service.oac_activation_state()
            if (
                consumed.get("status") != "CONSUMED_BY_QUOTE_FORMATION"
                or consumed.get("execution_run_id") != binding.execution_run_id
                or consumed.get("activation_binding_digest") != binding.digest
            ):
                return False
            evidence = self.workspace_service._competition_evidence_record()
            if evidence is None or evidence.get("run_id") != binding.execution_run_id:
                return False
            lineage = evidence.get("oac_agentteams_lineage")
            if not isinstance(lineage, Mapping):
                return False
            if (
                lineage.get("activation_binding_digest") != binding.digest
                or lineage.get("task_formation_decision_receipt_digest")
                != context.task_formation_decision_receipt_digest
                or lineage.get("task_agent_context_envelope_digest") != context.digest
            ):
                raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_CONTEXT_LINEAGE_MISMATCH") from None
            return True
        if retained != expected:
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_CONTEXT_LINEAGE_MISMATCH")
        return True

    def _retained_context(
        self, capsule: OACAdapterCapsule, binding: OACAdapterActivationBinding,
    ) -> tuple[TaskFormationDecisionReceipt, TaskAgentContextEnvelope, bool]:
        """Read approved formation history without recompiling current sources."""

        if not self._formation_decision_path.is_file():
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_FORMATION_DECISION_EVIDENCE_INCOMPLETE")
        try:
            decision = TaskFormationDecisionReceipt.model_validate(_read_object(
                self._formation_decision_path,
                "OAC_AGENTIC_RUNTIME_FORMATION_DECISION_EVIDENCE_INVALID",
            ))
            context = TaskAgentContextEnvelope.model_validate(_read_object(
                self._context_path, "OAC_AGENTIC_RUNTIME_CONTEXT_EVIDENCE_INVALID",
            ))
        except ValueError as exc:
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_CONTEXT_EVIDENCE_INVALID") from exc
        task = self.workspace_service.profile.task_request()
        if (
            decision.organization_snapshot_digest != capsule.organization_snapshot_digest
            or decision.organizational_demand_digest != capsule.organizational_demand_digest
            or context.admitted_organizational_intent_digest != capsule.organizational_demand_digest
            or decision.task_ref != task.id or decision.task_digest != task.digest
            or context.task_ref != task.id or context.task_digest != task.digest
            or context.task_formation_decision_receipt_digest != decision.digest
            or context.coalition_plan_ref != decision.coalition_plan_ref
            or context.coalition_plan_digest != decision.coalition_plan_digest
            or tuple(item.domain_id for item in context.domain_bindings) != decision.selected_domain_ids
            or tuple(sorted(item.capability_card_ref for item in context.domain_bindings)) != decision.selected_card_refs
            or tuple(sorted(item.capability_card_digest for item in context.domain_bindings)) != decision.selected_card_digests
        ):
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_CONTEXT_LINEAGE_MISMATCH")
        return decision, context, self._context_has_trusted_binding(binding, context)

    @staticmethod
    def _ready_contracts(
        adaptation: Mapping[str, Any],
    ) -> tuple[
        OACSourceAdmissionApproval,
        OACAdapterCapsule,
        OACAdapterActivationBinding,
    ]:
        if adaptation.get("status") != "READY_FOR_ORGREBASE":
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_OWNER_APPROVAL_REQUIRED")
        try:
            approval = OACSourceAdmissionApproval.model_validate(adaptation["approval"])
            capsule = OACAdapterCapsule.model_validate(adaptation["adapter_capsule"])
            binding = OACAdapterActivationBinding.model_validate(adaptation["activation_binding"])
        except (KeyError, TypeError, ValueError) as exc:
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_APPROVED_CONTRACT_INVALID") from exc
        return approval, capsule, binding

    @staticmethod
    def _validate_approved_lineage(
        *,
        adaptation: Mapping[str, Any],
        mapping: OACAgentMappingReceipt,
        approval: OACSourceAdmissionApproval,
        capsule: OACAdapterCapsule,
        binding: OACAdapterActivationBinding,
    ) -> None:
        intent_digest = adaptation.get("organizational_demand_digest")
        snapshot_digest = adaptation.get("organization_snapshot_digest")
        try:
            review_summary = OACOwnerReviewSummary.model_validate(
                adaptation.get("owner_review_summary")
            )
            review_gate = OACOwnerReviewGate.model_validate(adaptation.get("review_gate"))
        except (TypeError, ValueError) as exc:
            raise OACAgenticRuntimeError(
                "OAC_AGENTIC_RUNTIME_OWNER_REVIEW_SUMMARY_INVALID"
            ) from exc
        approval_gate = approval.review_gate
        projected_mapping = adaptation.get("agent_mapping")
        projected_mappings = adaptation.get("candidate_mappings")
        expected_mappings = [
            item.model_dump(mode="json") for item in mapping.accepted_mappings
        ]
        if (
            mapping.status != "VALIDATED_CANDIDATE"
            or mapping.accepted_mapping_set_digest is None
            or adaptation.get("adaptation_run_id") != mapping.adaptation_run_id
            or adaptation.get("profile_digest") != mapping.profile_digest
            or adaptation.get("pack_digest") != mapping.pack_digest
            or adaptation.get("candidate_digest") != mapping.accepted_mapping_set_digest
            or projected_mappings != expected_mappings
            or not isinstance(projected_mapping, Mapping)
            or projected_mapping.get("status") != mapping.status
            or projected_mapping.get("receipt_digest") != mapping.digest
            or projected_mapping.get("accepted_mapping_set_digest")
            != mapping.accepted_mapping_set_digest
            or approval.adaptation_run_id != mapping.adaptation_run_id
            or approval.candidate_digest != mapping.accepted_mapping_set_digest
            or approval.actor_id != review_summary.decision_owner_ref
            or approval.actor_id != adaptation.get("human_authority_ref")
            or approval.acknowledgements != OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS
            or approval_gate is None
            or approval.review_gate_digest != review_gate.digest
            or approval_gate.digest != review_gate.digest
            or review_gate.adaptation_run_id != mapping.adaptation_run_id
            or review_gate.mapping_set_digest != mapping.accepted_mapping_set_digest
            or review_gate.profile_digest != mapping.profile_digest
            or review_gate.pack_digest != mapping.pack_digest
            or review_gate.owner_ref != approval.actor_id
            or review_gate.owner_review_summary_digest != review_summary.digest
            or capsule.adaptation_run_id != mapping.adaptation_run_id
            or capsule.profile_digest != mapping.profile_digest
            or capsule.pack_digest != mapping.pack_digest
            or capsule.mapping_set_digest != mapping.accepted_mapping_set_digest
            or capsule.approval_digest != approval.digest
            or review_summary.adaptation_run_id != mapping.adaptation_run_id
            or review_summary.profile_digest != mapping.profile_digest
            or review_summary.pack_digest != mapping.pack_digest
            or review_summary.candidate_mapping_set_digest
            != mapping.accepted_mapping_set_digest
            or approval.owner_review_summary_digest != review_summary.digest
            or capsule.owner_review_summary_digest != review_summary.digest
            or capsule.organization_snapshot_digest != snapshot_digest
            or capsule.organizational_demand_digest != intent_digest
            or binding.adaptation_run_id != mapping.adaptation_run_id
            or binding.adapter_capsule_digest != capsule.digest
            or binding.profile_digest != capsule.profile_digest
            or binding.pack_digest != capsule.pack_digest
            or binding.profile_digest != review_summary.profile_digest
            or binding.pack_digest != review_summary.pack_digest
            or binding.execution_run_id != adaptation.get("execution_run_id")
            or adaptation.get("approval_digest") != approval.digest
            or adaptation.get("adapter_capsule_digest") != capsule.digest
            or approval.canonical_target_writes != 0
            or capsule.canonical_target_writes != 0
            or binding.canonical_target_writes != 0
        ):
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_APPROVED_LINEAGE_MISMATCH")

    def _ready_formation_lineage(
        self,
        *,
        adaptation: Mapping[str, Any],
        mapping: OACAgentMappingReceipt | None,
        expected_execution_run_id: str | None = None,
        for_execution: bool = True,
    ) -> tuple[OACSourceAdmissionApproval, OACAdapterCapsule, OACAdapterActivationBinding]:
        """Revalidate the approved OAC contract used by task formation.

        Agent-assisted mapping is optional.  The activation binding, not the
        mapping technique, is the authority that allows a task to form.
        """

        approval, capsule, binding = self._ready_contracts(adaptation)
        runtime = self.adaptation_service.runtime
        if runtime is None:
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_EXACT_PACK_REQUIRED")
        try:
            retained = self.adaptation_service.require_activation_binding(
                profile_digest=self.adaptation_service.profile.digest,
                pack_digest=runtime.pack_digest,
                execution_run_id=(expected_execution_run_id or binding.execution_run_id),
                for_execution=for_execution,
            )
        except (IntegrityError, RuntimeError, TypeError, ValueError, KeyError) as exc:
            if str(exc) in {"OAC_ADAPTATION_IMPLEMENTATION_BINDING_MISSING", "OAC_ADAPTATION_IMPLEMENTATION_REPLAN_REQUIRED"}:
                raise OACAgenticRuntimeError(str(exc)) from exc
            raise OACAgenticRuntimeError(
                "OAC_AGENTIC_RUNTIME_APPROVED_LINEAGE_MISMATCH"
            ) from exc
        if retained.digest != binding.digest:
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_APPROVED_LINEAGE_MISMATCH")
        if mapping is not None:
            self._validate_approved_lineage(
                adaptation=adaptation,
                mapping=mapping,
                approval=approval,
                capsule=capsule,
                binding=binding,
            )
        return approval, capsule, binding

    @staticmethod
    def _validate_shadow_receipt(
        value: OACBoundShadowExecutionReceipt | Mapping[str, Any],
        *,
        mapping: OACAgentMappingReceipt,
        context: TaskAgentContextEnvelope,
        approval: OACSourceAdmissionApproval,
        capsule: OACAdapterCapsule,
        binding: OACAdapterActivationBinding,
    ) -> OACBoundShadowExecutionReceipt:
        try:
            selected = (
                value.revalidated()
                if isinstance(value, OACBoundShadowExecutionReceipt)
                else OACBoundShadowExecutionReceipt.model_validate(dict(value))
            )
        except (TypeError, ValueError) as exc:
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_SHADOW_RECEIPT_INVALID") from exc
        review_gate = approval.review_gate
        if (
            selected.status != "PASS"
            or selected.adaptation_run_id != mapping.adaptation_run_id
            or selected.adaptation_run_id != capsule.adaptation_run_id
            or selected.shadow_execution_run_id != binding.execution_run_id
            or selected.approval_digest != approval.digest
            or review_gate is None
            or selected.approval_review_gate_digest != approval.review_gate_digest
            or selected.review_gate_digest != review_gate.digest
            or selected.review_duration_ms != review_gate.review_duration_ms
            or selected.approval_elapsed_since_not_before_ms
            != approval.approved_at_epoch_ms - review_gate.not_before_epoch_ms
            or selected.adapter_capsule_digest != capsule.digest
            or selected.activation_binding_digest != binding.digest
            or selected.context_envelope_ref != context.ref
            or selected.context_envelope_digest != context.digest
            or selected.agent_mapping_receipt_digest != mapping.digest
            or selected.profile_digest != mapping.profile_digest
            or selected.profile_digest != capsule.profile_digest
            or selected.profile_digest != binding.profile_digest
            or selected.pack_digest != mapping.pack_digest
            or selected.pack_digest != capsule.pack_digest
            or selected.pack_digest != binding.pack_digest
            or selected.same_run_layers != _SAME_RUN_LAYERS
            or selected.shadow_terminal_status != "CANDIDATE_PREPARED_FOR_CONTROL_ADMISSION"
            or selected.candidate_only is not True
            or selected.canonical_target_writes != 0
            or selected.production_claimed is not False
        ):
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_SHADOW_LINEAGE_MISMATCH")
        return selected

    def _mapping_view(
        self,
        mapping: OACAgentMappingReceipt | None,
    ) -> dict[str, Any]:
        if mapping is None:
            incomplete = self._mapping_evidence_incomplete()
            return {
                "status": "EVIDENCE_INCOMPLETE" if incomplete else "NOT_RUN",
                "model_status": "NOT_RUN",
                "model_id": VERTEX_MODEL_ID,
                "provider_request_observed": False,
                "input_digest": None,
                "output_digest": None,
                "receipt_digest": None,
                "accepted_mapping_set_digest": None,
                "accepted_mappings": [],
                "validation_reason_codes": (["MAPPING_EVIDENCE_INCOMPLETE"] if incomplete else []),
                "native_lifecycle": {
                    "runtime": f"AgentTeams {load_agentteams_source().tag}",
                    "action_count": 0,
                    "actions": [],
                    "terminal_state": "NOT_RUN",
                },
                "candidate_only": True,
                "canonical_target_writes": 0,
            }
        observation = mapping.model_observation
        lifecycle = mapping.native_agentteams
        return {
            "status": mapping.status,
            "model_status": observation.status,
            "model_id": observation.model_id,
            "provider_request_observed": bool(observation.provider_request_id),
            "input_digest": mapping.input_digest,
            "output_digest": observation.output_digest,
            "receipt_digest": mapping.digest,
            "accepted_mapping_set_digest": mapping.accepted_mapping_set_digest,
            "accepted_mappings": [
                {
                    "component_kind": item.component_kind.value,
                    "source_root_ref": item.source_root_ref,
                    "target_oac_paths": list(item.target_oac_paths),
                    "declared_unknowns": list(item.declared_unknowns),
                    "reason_codes": list(item.reason_codes),
                    "output_digest": item.digest,
                }
                for item in mapping.accepted_mappings
            ],
            "validation_reason_codes": list(mapping.validation.reason_codes),
            "native_lifecycle": {
                "runtime": f"AgentTeams {lifecycle.agentteams_version}",
                "action_count": len(lifecycle.actions),
                "actions": list(lifecycle.action_sequence),
                "terminal_state": lifecycle.project_terminal_state,
            },
            "candidate_only": True,
            "canonical_target_writes": 0,
        }

    def _adaptation_view(self, adaptation: Mapping[str, Any]) -> dict[str, Any]:
        approval = adaptation.get("approval") or {}
        review_gate = adaptation.get("review_gate") or approval.get("review_gate") or {}
        capsule = adaptation.get("adapter_capsule") or {}
        binding = adaptation.get("activation_binding") or {}
        organizational_demand = adaptation.get("organizational_demand") or {}
        owner_review_summary = adaptation.get("owner_review_summary") or {}
        demand_spec = (
            organizational_demand.get("spec") if isinstance(organizational_demand, Mapping) else {}
        ) or {}
        approved_at_epoch_ms = approval.get("approved_at_epoch_ms")
        not_before_epoch_ms = review_gate.get("not_before_epoch_ms")
        approval_elapsed_ms = (
            approved_at_epoch_ms - not_before_epoch_ms
            if isinstance(approved_at_epoch_ms, int)
            and isinstance(not_before_epoch_ms, int)
            and approved_at_epoch_ms >= not_before_epoch_ms
            else None
        )
        lineage_proof = {
            "owner_review_summary_digest": owner_review_summary.get("digest"),
            "review_gate_owner_review_summary_digest": review_gate.get(
                "owner_review_summary_digest"
            ),
            "approval_owner_review_summary_digest": approval.get(
                "owner_review_summary_digest"
            ),
            "adapter_capsule_owner_review_summary_digest": capsule.get(
                "owner_review_summary_digest"
            ),
            "approval_digest": approval.get("digest"),
            "adapter_capsule_digest": capsule.get("digest"),
            "activation_binding_digest": binding.get("digest"),
        }
        return {
            "status": adaptation.get("status", "PACK_OBSERVED"),
            "adaptation_run_id": adaptation.get("adaptation_run_id"),
            "profile_digest": adaptation.get("profile_digest"),
            "pack_digest": adaptation.get("pack_digest"),
            "owner_ref": adaptation.get("human_authority_ref"),
            "candidate_digest": adaptation.get("candidate_digest"),
            "candidate_mappings": adaptation.get("candidate_mappings") or [],
            "gaps": adaptation.get("gaps") or [],
            "review_remaining_ms": adaptation.get("review_remaining_ms"),
            "review_not_before": adaptation.get("review_not_before"),
            "review_gate_digest": review_gate.get("digest"),
            "owner_review_summary_digest": owner_review_summary.get("digest"),
            "owner_review_summary": owner_review_summary,
            "review_duration_ms": review_gate.get("review_duration_ms"),
            "approval_digest": adaptation.get("approval_digest"),
            "approval_acknowledgements": approval.get("acknowledgements") or [],
            "approved_at": approval.get("approved_at"),
            "approval_elapsed_since_not_before_ms": approval_elapsed_ms,
            "adapter_capsule_digest": adaptation.get("adapter_capsule_digest"),
            "activation_binding_digest": binding.get("digest"),
            "lineage_proof": lineage_proof,
            "organizational_demand_digest": (
                capsule.get("organizational_demand_digest") or adaptation.get("organizational_demand_digest")
            ),
            "organizational_demand": {
                "objective": demand_spec.get("objective"),
                "accountable_role_ref": demand_spec.get("accountableRoleRef"),
                "effect_ceiling": demand_spec.get("effectCeiling"),
                "evidence_obligation_count": len(demand_spec.get("evidenceObligationRefs") or []),
            },
            "deterministic_fallback_used": False,
            "canonical_target_writes": 0,
        }

    def _context_view(
        self,
        context: TaskAgentContextEnvelope | None,
        *,
        adaptation_status: str,
    ) -> dict[str, Any]:
        if context is None:
            if _directory_has_evidence(self._context_dir):
                status = "EVIDENCE_INCOMPLETE"
            elif adaptation_status == "READY_FOR_ORGREBASE":
                status = "READY_TO_COMPILE"
            elif adaptation_status == "OWNER_REVIEW_PENDING":
                status = "WAITING_FOR_OWNER_APPROVAL"
            else:
                status = "NOT_RUN"
            return {
                "status": status,
                "envelope_digest": None,
                "organizational_intent_digest": None,
                "main_agent": None,
                "resident_domain_contracts": [],
                "task_projections": [],
                "return_to_control_plane": {
                    "authority": "ORGREBASE_CONTROL_PLANE",
                    "candidate_only": True,
                    "canonical_target_writes": 0,
                },
                "expires_at": None,
            }
        return {
            "status": "READY",
            "envelope_digest": context.digest,
            "organizational_intent_digest": (context.admitted_organizational_intent_digest),
            "main_agent": {
                "task_ref": context.task_ref,
                "task_digest": context.task_digest,
                "coalition_digest": context.coalition_plan_digest,
                "formation_decision_receipt_digest": (context.task_formation_decision_receipt_digest),
                "effect_ceiling": context.effect_ceiling,
            },
            "resident_domain_contracts": [
                {
                    "domain_id": item.domain_id,
                    "capability_card_ref": item.capability_card_ref,
                    "capability_card_digest": item.capability_card_digest,
                }
                for item in context.domain_bindings
            ],
            "task_projections": [
                {
                    "domain_id": item.domain_id,
                    "actor_projection_ref": item.actor_projection_ref,
                    "actor_projection_digest": item.actor_projection_digest,
                }
                for item in context.domain_bindings
            ],
            "return_to_control_plane": {
                "authority": "ORGREBASE_CONTROL_PLANE",
                "candidate_only": True,
                "canonical_target_writes": 0,
            },
            "expires_at": context.expires_at,
        }

    def _shadow_view(
        self,
        shadow: OACBoundShadowExecutionReceipt | None,
    ) -> dict[str, Any]:
        if shadow is None:
            incomplete = _directory_has_evidence(self._shadow_dir)
            return {
                "status": "EVIDENCE_INCOMPLETE" if incomplete else "NOT_RUN",
                "run_id": None,
                "model_provider": self.shadow_model_provider,
                "receipt_digest": None,
                "context_envelope_digest": None,
                "context_freshness_basis": None,
                "context_freshness_checked_at": None,
                "agent_mapping_receipt_digest": None,
                "same_run_layers": [],
                "agentteams_execution_plan_digest": None,
                "planned_domain_ids": [],
                "actual_agentteams_domain_ids": [],
                "topology_match": None,
                "digests": {},
                "terminal_status": "NOT_RUN",
                "canonical_target_writes": 0,
                "independent_verification": {
                    "status": "NOT_RUN",
                    "checked_output_file_count": 0,
                    "checked_pack_file_count": 0,
                    "failure_count": 0,
                    "digest": None,
                },
            }
        independent_verification = {
            "status": "NOT_RUN",
            "checked_output_file_count": 0,
            "checked_pack_file_count": 0,
            "failure_count": 0,
            "digest": None,
        }
        if self._shadow_verification_path.is_file():
            verification = self._validate_shadow_verification(
                _read_object(
                    self._shadow_verification_path,
                    "OAC_AGENTIC_RUNTIME_SHADOW_VERIFICATION_INVALID",
                ),
                shadow=shadow,
            )
            independent_verification = {
                "status": "PASS",
                "checked_output_file_count": verification["checked_output_file_count"],
                "checked_pack_file_count": verification["checked_pack_file_count"],
                "failure_count": 0,
                "digest": verification["digest"],
            }
        return {
            "status": "COMPLETED",
            "run_id": shadow.shadow_execution_run_id,
            "model_provider": self.shadow_model_provider,
            "receipt_digest": shadow.digest,
            "context_envelope_digest": shadow.context_envelope_digest,
            "context_freshness_basis": shadow.context_freshness_basis,
            "context_freshness_checked_at": shadow.context_freshness_checked_at,
            "agent_mapping_receipt_digest": shadow.agent_mapping_receipt_digest,
            "same_run_layers": list(shadow.same_run_layers),
            "agentteams_execution_plan_digest": (shadow.agentteams_execution_plan_digest),
            "planned_domain_ids": list(shadow.planned_domain_ids),
            "actual_agentteams_domain_ids": list(shadow.actual_agentteams_domain_ids),
            "topology_match": shadow.topology_match,
            "digests": {
                "execution": shadow.execution_envelope_digest,
                "competition": shadow.competition_summary_digest,
                "agentteams_plan": shadow.agentteams_execution_plan_digest,
                "tool": shadow.tool_receipt_digest,
                "skill": shadow.skill_invocation_receipt_digest,
                "formation": shadow.prepared_formation_digest,
                "telemetry_query": shadow.telemetry_query_receipt_digest,
                "telemetry_alert": shadow.telemetry_alert_receipt_digest,
            },
            "terminal_status": shadow.shadow_terminal_status,
            "canonical_target_writes": 0,
            "independent_verification": independent_verification,
        }

    def _validate_shadow_verification(
        self,
        value: Mapping[str, Any],
        *,
        shadow: OACBoundShadowExecutionReceipt,
    ) -> dict[str, Any]:
        verification = dict(value)
        verification_body = {key: item for key, item in verification.items() if key != "digest"}
        if (
            verification.get("digest") != sha256_digest(verification_body)
            or verification.get("status") != "PASS"
            or verification.get("shadow_execution_run_id") != shadow.shadow_execution_run_id
            or verification.get("failure_count") != 0
            or verification.get("failures") != []
            or verification.get("canonical_target_writes") != 0
            or not isinstance(verification.get("checked_output_file_count"), int)
            or verification.get("checked_output_file_count", 0) <= 0
            or not isinstance(verification.get("checked_pack_file_count"), int)
            or verification.get("checked_pack_file_count", 0) <= 0
        ):
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_SHADOW_VERIFICATION_INVALID")
        return verification

    def _ensure_shadow_verification(
        self,
        shadow: OACBoundShadowExecutionReceipt,
    ) -> None:
        if self._shadow_verification_path.is_file():
            self._validate_shadow_verification(
                _read_object(
                    self._shadow_verification_path,
                    "OAC_AGENTIC_RUNTIME_SHADOW_VERIFICATION_INVALID",
                ),
                shadow=shadow,
            )
            return
        if self.shadow_verifier is None:
            return
        try:
            result = self.shadow_verifier(
                root=self._shadow_dir,
                pack=self.pack_path,
                frozen_golden=self.frozen_golden_root,
                fencing_reference=self.late_attempt_fencing_receipt,
            )
        except Exception as exc:
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_SHADOW_VERIFICATION_FAILED") from exc
        verification = self._validate_shadow_verification(
            result,
            shadow=shadow,
        )
        _write_json_atomic(self._shadow_verification_path, verification)

    def view(self) -> dict[str, Any]:
        """Return a path- and credential-free product projection."""

        adaptation = self.adaptation_service.view()
        mapping = self._load_mapping_receipt()
        context: TaskAgentContextEnvelope | None = None
        context_anchored = False
        if self._context_path.is_file():
            if mapping is not None and mapping.status != "VALIDATED_CANDIDATE":
                raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_CONTEXT_WITH_INVALID_MAPPING")
            _approval, capsule, binding = self._ready_formation_lineage(
                adaptation=adaptation,
                mapping=mapping,
                for_execution=False,
            )
            _decision, context, context_anchored = self._retained_context(capsule, binding)
        shadow: OACBoundShadowExecutionReceipt | None = None
        if self._shadow_path.is_file() and context_anchored:
            if mapping is None or context is None:
                raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_SHADOW_LINEAGE_INCOMPLETE")
            approval, capsule, binding = self._ready_contracts(adaptation)
            shadow = self._validate_shadow_receipt(
                _read_object(
                    self._shadow_path,
                    "OAC_AGENTIC_RUNTIME_SHADOW_RECEIPT_INVALID",
                ),
                mapping=mapping,
                context=context,
                approval=approval,
                capsule=capsule,
                binding=binding,
            )

        if shadow is not None:
            status = "SHADOW_COMPLETED"
        elif (
            self._mapping_evidence_incomplete()
            or (mapping is not None and mapping.status == "HOLD")
            or adaptation.get("status") == "HOLD"
        ):
            status = "AGENT_MAPPING_HOLD"
        elif adaptation.get("status") == "READY_FOR_ORGREBASE":
            status = "READY_FOR_SHADOW"
        elif adaptation.get("status") == "OWNER_REVIEW_PENDING":
            status = "OWNER_REVIEW_PENDING"
        else:
            status = "NOT_STARTED"
        context_view = self._context_view(
            context if context_anchored else None,
            adaptation_status=str(adaptation.get("status") or ""),
        )
        if context is not None and not context_anchored:
            context_view["status"] = "EVIDENCE_UNANCHORED"
        policy = self._execution_policy(adaptation=adaptation, mapping=mapping, shadow=shadow)
        if self._shadow_path.is_file() and not context_anchored:
            policy["available_actions"] = [
                action for action in policy["available_actions"] if action != _ACTION_EXECUTE_SHADOW
            ]
            reasons = policy["blocked_reasons"].setdefault(_ACTION_EXECUTE_SHADOW, [])
            reason = "OAC_AGENTIC_RUNTIME_CONTEXT_EVIDENCE_UNANCHORED"
            if reason not in reasons:
                reasons.append(reason)
            policy["shadow_will_call_external_model"] = False
        return {
            "schema_version": _VIEW_SCHEMA_VERSION,
            "status": status,
            "execution_policy": policy,
            "adaptation": self._adaptation_view(adaptation),
            "agent_mapping": self._mapping_view(mapping),
            "context_residency": context_view,
            "shadow_execution": self._shadow_view(shadow),
        }

    def formation_roots_for_current_task(
        self,
        *,
        expected_activation_binding_digest: str,
    ) -> tuple[TaskFormationDecisionReceipt, TaskAgentContextEnvelope]:
        """Return the exact approved OAC roots for the current Workspace task.

        This is the production wiring boundary between organization admission
        and task execution.  It deliberately does not run the optional shadow
        proof: the current Golden run must consume these exact roots itself.
        """

        mapping = self._load_mapping_receipt()
        if mapping is not None and mapping.status != "VALIDATED_CANDIDATE":
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_CONTEXT_WITH_INVALID_MAPPING")
        adaptation = self.adaptation_service.view()
        _approval, capsule, binding = self._ready_formation_lineage(
            adaptation=adaptation,
            mapping=mapping,
            expected_execution_run_id=self.workspace_service.effective_workflow_run_id,
        )
        if (
            not expected_activation_binding_digest
            or binding.digest != expected_activation_binding_digest
        ):
            raise OACAgenticRuntimeError(
                "OAC_AGENTIC_RUNTIME_ACTIVATION_BINDING_CHANGED"
            )
        context = self._load_or_compile_context(
            organizational_intent_digest=capsule.organizational_demand_digest
        )
        try:
            receipt = TaskFormationDecisionReceipt.model_validate(
                _read_object(
                    self._formation_decision_path,
                    "OAC_AGENTIC_RUNTIME_FORMATION_DECISION_EVIDENCE_INVALID",
                )
            )
        except ValueError as exc:
            raise OACAgenticRuntimeError(
                "OAC_AGENTIC_RUNTIME_FORMATION_DECISION_EVIDENCE_INVALID"
            ) from exc
        if context.task_formation_decision_receipt_digest != receipt.digest:
            raise OACAgenticRuntimeError(
                "OAC_AGENTIC_RUNTIME_FORMATION_DECISION_LINEAGE_MISMATCH"
            )
        self._bind_compiled_context(binding, context)
        return receipt, context

    def agent_prepare(self, *, command_id: str) -> dict[str, Any]:
        """Run or replay one live mapping attempt, then open human review."""

        if not command_id:
            raise ValueError("OAC_AGENTIC_RUNTIME_COMMAND_ID_REQUIRED")
        mapping = self._load_mapping_receipt()
        if mapping is None:
            if self._mapping_evidence_incomplete():
                raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_MAPPING_EVIDENCE_INCOMPLETE")
            if not self._new_mapping_allowed():
                raise OACAgenticRuntimeError(
                    "OAC_AGENTIC_RUNTIME_FROZEN_MAPPING_RECEIPT_REQUIRED"
                    if self.execution_mode == "FROZEN_REPLAY"
                    else "OAC_AGENTIC_RUNTIME_NEW_VERTEX_MAPPING_BLOCKED"
                )
            adaptation = self.adaptation_service.view()
            if adaptation.get("status") != "PACK_OBSERVED":
                # Do not spend another provider call for a lineage which can no
                # longer become the exact approved draft.
                raise RuntimeError("OAC_ADAPTATION_AGENT_MAPPING_COMMAND_CONFLICT")
            baseline, _baseline_digest = self._baseline_digest()
            runtime = self.adaptation_service.runtime
            assert runtime is not None
            kwargs: dict[str, Any] = {
                "checkout": self.checkout,
                "lock_path": self.lock_path,
                "output_dir": self._mapping_dir,
                "profile": self.adaptation_service.profile,
                "runtime": runtime,
                "baseline_mappings": baseline,
                "adaptation_run_id": self.adaptation_service.adaptation_run_id,
                "vertex_project": self.vertex_project,
            }
            if self.agent_provider is not None:
                kwargs["provider"] = self.agent_provider
            from orgrebase.workspace.oac_agent_adaptation import (
                bind_oac_mapping_implementation,
                current_oac_admission_implementation,
            )

            implementation = current_oac_admission_implementation()
            mapping = self._validate_mapping_receipt(self.agent_runner(**kwargs))
            bind_oac_mapping_implementation(
                self.adaptation_service.store, mapping, implementation=implementation,
            )
            if self._mapping_path.is_file():
                retained = self._load_mapping_receipt()
                if retained is None or retained.digest != mapping.digest:
                    raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_MAPPING_RUNNER_RECEIPT_MISMATCH")
            else:
                _write_json_atomic(
                    self._mapping_path,
                    mapping.model_dump(mode="json"),
                )
        if mapping.status == "HOLD":
            return self.view()
        self.adaptation_service.prepare(
            command_id=command_id,
            agent_mapping_receipt=mapping.model_dump(mode="json"),
        )
        return self.view()

    def execute_shadow(self) -> dict[str, Any]:
        """Run or replay the exact live-approved, zero-write shadow proof."""

        if not self._shadow_path.is_file() and not self._new_shadow_allowed():
            raise OACAgenticRuntimeError(self._new_shadow_blocked_reason())
        mapping = self._load_mapping_receipt()
        if mapping is None or mapping.status != "VALIDATED_CANDIDATE":
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_LIVE_MAPPING_REQUIRED")
        adaptation = self.adaptation_service.view()
        approval, capsule, binding = self._ready_contracts(adaptation)
        self._validate_approved_lineage(
            adaptation=adaptation,
            mapping=mapping,
            approval=approval,
            capsule=capsule,
            binding=binding,
        )
        if not self._shadow_path.is_file():
            self.adaptation_service.require_activation_binding(
                profile_digest=capsule.profile_digest, pack_digest=capsule.pack_digest,
                execution_run_id=binding.execution_run_id,
            )
        if self._shadow_path.is_file():
            _decision, context, anchored = self._retained_context(capsule, binding)
            if not anchored:
                raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_CONTEXT_EVIDENCE_UNANCHORED")
        else:
            context = self._load_or_compile_context(
                organizational_intent_digest=capsule.organizational_demand_digest
            )
        try:
            formation_decision = TaskFormationDecisionReceipt.model_validate(
                _read_object(
                    self._formation_decision_path,
                    "OAC_AGENTIC_RUNTIME_FORMATION_DECISION_EVIDENCE_INVALID",
                )
            )
        except ValueError as exc:
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_FORMATION_DECISION_EVIDENCE_INVALID") from exc
        if context.task_formation_decision_receipt_digest != formation_decision.digest:
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_FORMATION_DECISION_LINEAGE_MISMATCH")

        if not self._shadow_path.is_file():
            self._bind_compiled_context(binding, context)

        if self._shadow_path.is_file():
            retained = self._validate_shadow_receipt(
                _read_object(
                    self._shadow_path,
                    "OAC_AGENTIC_RUNTIME_SHADOW_RECEIPT_INVALID",
                ),
                mapping=mapping,
                context=context,
                approval=approval,
                capsule=capsule,
                binding=binding,
            )
            self._ensure_shadow_verification(retained)
            return self.view()
        if _directory_has_evidence(self._shadow_dir):
            raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_SHADOW_EVIDENCE_INCOMPLETE")
        kwargs: dict[str, Any] = {
            "repo_root": self.repo_root,
            "output_dir": self._shadow_dir,
            "checkout": self.checkout,
            "lock_path": self.lock_path,
            "pack_path": self.pack_path,
            "frozen_golden_root": self.frozen_golden_root,
            "adapter_capsule": capsule.model_dump(mode="json"),
            "activation_binding": binding.model_dump(mode="json"),
            "approval": approval.model_dump(mode="json"),
            "context_envelope": context.model_dump(mode="json"),
            "task_formation_decision_receipt": formation_decision.model_dump(mode="json"),
            # The enterprise pilot is a frozen, deterministic business scenario.
            # Validate context freshness against that scenario's explicit event
            # clock; the approval gate above still uses real elapsed wall time.
            # The shadow receipt records LOGICAL_EVENT_TIME so this can never be
            # confused with live production freshness evidence.
            "context_validation_time": self.workspace_service.formation.clock.now(),
            "agent_mapping_receipt": mapping.model_dump(mode="json"),
            "late_attempt_fencing_receipt": self.late_attempt_fencing_receipt,
            "model_provider": self.shadow_model_provider,
            "ollama_endpoint": self.ollama_endpoint,
            "vertex_project": self.vertex_project,
        }
        if self.shadow_competition_runner is not None:
            kwargs["competition_runner"] = self.shadow_competition_runner
        shadow = self._validate_shadow_receipt(
            self.shadow_runner(**kwargs),
            mapping=mapping,
            context=context,
            approval=approval,
            capsule=capsule,
            binding=binding,
        )
        if self._shadow_path.is_file():
            retained = self._validate_shadow_receipt(
                _read_object(
                    self._shadow_path,
                    "OAC_AGENTIC_RUNTIME_SHADOW_RECEIPT_INVALID",
                ),
                mapping=mapping,
                context=context,
                approval=approval,
                capsule=capsule,
                binding=binding,
            )
            if retained.digest != shadow.digest:
                raise OACAgenticRuntimeError("OAC_AGENTIC_RUNTIME_SHADOW_RUNNER_RECEIPT_MISMATCH")
        else:
            _write_json_atomic(
                self._shadow_path,
                shadow.model_dump(mode="json"),
            )
        self._ensure_shadow_verification(shadow)
        return self.view()


__all__ = (
    "OACAgenticRuntime",
    "OACAgenticRuntimeError",
)
