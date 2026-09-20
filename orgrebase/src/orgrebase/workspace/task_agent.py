"""Task-template matching and template-bounded requirement candidates."""

from __future__ import annotations

from orgrebase.digest import sha256_digest
from orgrebase.domain import EvidenceClass
from orgrebase.workspace.models import (
    CandidateSource,
    TaskInterpretationReceipt,
    TaskRequest,
    TaskRequirementCandidate,
    TaskTemplateVersion,
    TemplateCandidate,
)
from orgrebase.workspace.templates import TemplateRegistry


class TemplateBoundTaskInterpreter:
    version = "workspace-template-bound-interpreter@1.0.0"

    def propose(
        self,
        *,
        task: TaskRequest,
        template: TaskTemplateVersion,
    ) -> tuple[tuple[TaskRequirementCandidate, ...], TaskInterpretationReceipt]:
        optional = task.input_values.get("optional_slots", [])
        if not isinstance(optional, list):
            raise ValueError("INVALID_OPTIONAL_SLOT_LIST")
        allowed = {item.slot_id: item for item in template.slots}
        selected = [item.slot_id for item in template.slots if item.required]
        for slot_id in optional:
            if not isinstance(slot_id, str) or slot_id not in allowed:
                raise ValueError(f"EXTRA_REQUIREMENT_NOT_ALLOWED:{slot_id}")
            if slot_id not in selected:
                selected.append(slot_id)
        candidates = tuple(
            TaskRequirementCandidate(
                task_ref=task.id,
                slot_id=slot_id,
                proposed_domain_id=allowed[slot_id].domain_id,
                rationale="template-required" if allowed[slot_id].required else "task-selected-optional",
                source=CandidateSource.TEMPLATE,
                candidate_sequence=index,
            )
            for index, slot_id in enumerate(selected)
        )
        refs = tuple(item.digest for item in candidates)
        receipt = TaskInterpretationReceipt(
            id=f"interpretation:{task.id.split(':')[-1]}@v1",
            task_ref=task.id,
            template_ref=template.ref,
            candidate_refs=refs,
            candidate_set_digest=sha256_digest(sorted(refs)),
            interpreter_version=self.version,
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        )
        return candidates, receipt


class TaskTemplateMatcher:
    """Deterministic matcher used for local and open benchmark execution."""

    version = "workspace-template-matcher@1.0.0"

    def __init__(self, registry: TemplateRegistry) -> None:
        self.registry = registry

    def match(self, task: TaskRequest) -> TemplateCandidate:
        if task.template_ref:
            template = self.registry.get(task.template_ref)
            confidence = 1.0
            reasons = ("EXPLICIT_TEMPLATE_REF",)
        else:
            template = self.registry.by_deliverable_kind(task.deliverable_kind)
            confidence = 1.0
            reasons = ("DELIVERABLE_KIND_MATCH",)
        return TemplateCandidate(
            task_ref=task.id,
            template_ref=template.ref,
            confidence=confidence,
            reason_codes=reasons,
        )
