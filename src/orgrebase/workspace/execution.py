"""Reference-monitored deterministic rendering and runtime dependency capture."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pydantic import JsonValue

from orgrebase.digest import sha256_digest
from orgrebase.domain import ManifestCompleteness
from orgrebase.workspace.models import (
    CoverageStatus,
    OutputFieldLineage,
    OutputProducedEvent,
    QuotePayload,
    QuoteTaskLiterals,
    ReferenceResolvedEvent,
    ResolvedQuoteInputs,
    RuntimeDependencyEntry,
    RuntimeDependencyManifest,
    StoredArtifact,
    TaskContextManifest,
    TaskRequest,
    TaskTemplateVersion,
    TraceCoverageReceipt,
    WorkTrace,
)


@dataclass(frozen=True)
class StaticClock:
    value: str = "2026-08-15T00:00:00Z"

    def now(self) -> str:
        return self.value


class ExecutionReferenceMonitor:
    """The only value-bearing input surface exposed to deterministic renderers."""

    def __init__(
        self,
        *,
        task: TaskRequest,
        template: TaskTemplateVersion,
        manifest: TaskContextManifest,
        artifact_reader: Callable[[str, str | None], StoredArtifact] | None,
        actor_id: str,
        run_id: str,
        clock: StaticClock,
        id_factory: Callable[[str], str],
        trace_version: str = "v1",
    ) -> None:
        if actor_id != "workspace-renderer":
            raise PermissionError("REFERENCE_MONITOR_ACTOR_DENIED")
        self.task = task
        self.template = template
        self.manifest = manifest
        self.artifact_reader = artifact_reader
        self.actor_id = actor_id
        self.run_id = run_id
        self.clock = clock
        self.id_factory = id_factory
        self.trace_version = trace_version
        self._bindings = {item.slot_id: item for item in manifest.slot_bindings}
        self._events: list[ReferenceResolvedEvent | OutputProducedEvent] = []
        self._resolved: set[str] = set()
        self._previous = "sha256:" + "0" * 64

    @property
    def capability_surface(self) -> tuple[str, ...]:
        return ("resolve", "finish")

    def resolve(self, slot_id: str) -> JsonValue:
        if slot_id in self._resolved:
            raise RuntimeError(f"REFERENCE_RESOLVED_MORE_THAN_ONCE:{slot_id}")
        try:
            binding = self._bindings[slot_id]
        except KeyError as exc:
            raise KeyError(f"UNADMITTED_REFERENCE:{slot_id}") from exc
        if binding.expires_at <= self.clock.now():
            raise RuntimeError(f"REFERENCE_EXPIRED:{slot_id}")
        event = ReferenceResolvedEvent(
            event_id=self.id_factory("reference-resolved"),
            task_ref=self.task.id,
            run_id=self.run_id,
            sequence=len(self._events) + 1,
            slot_id=slot_id,
            provider_ref=binding.object_ref,
            provider_digest=binding.object_digest,
            semantic_kind=binding.semantic_kind,
            relation=binding.relation,
            strength=binding.strength,
            projection_digest=binding.projection_digest,
            input_digest=sha256_digest(
                {
                    "task": self.task.digest,
                    "manifest": self.manifest.digest,
                    "slot": slot_id,
                    "projection": binding.projection_digest,
                }
            ),
            occurred_at=self.clock.now(),
            idempotency_key=f"{self.task.id}:{slot_id}:1",
            previous_event_digest=self._previous,
        )
        self._events.append(event)
        self._previous = event.digest
        self._resolved.add(slot_id)
        return binding.projection

    def finish(
        self,
        *,
        output_ref: str,
        output_payload: Mapping[str, JsonValue],
        output_field_lineage: tuple[OutputFieldLineage, ...],
    ) -> WorkTrace:
        output_digest = sha256_digest(dict(output_payload))
        event = OutputProducedEvent(
            event_id=self.id_factory("output-produced"),
            task_ref=self.task.id,
            run_id=self.run_id,
            sequence=len(self._events) + 1,
            output_ref=output_ref,
            output_digest=output_digest,
            occurred_at=self.clock.now(),
            idempotency_key=f"{self.task.id}:output:1",
            previous_event_digest=self._previous,
        )
        self._events.append(event)
        self._previous = event.digest
        return WorkTrace(
            id=f"work-trace:{self.task.id.split(':')[-1]}",
            version=self.trace_version,
            organization_id=self.task.organization_id,
            task_ref=self.task.id,
            run_id=self.run_id,
            template_ref=self.template.ref,
            coalition_plan_ref=self.manifest.coalition_plan_ref,
            context_manifest_ref=self.manifest.ref,
            events=tuple(self._events),
            head_event_digest=event.digest,
            renderer_ref=f"{self.template.renderer_id}@{self.template.renderer_version}",
            started_at=self.clock.now(),
            completed_at=self.clock.now(),
            output_ref=output_ref,
            output_digest=output_digest,
        )


class QuoteInputAssembler:
    SLOT_ORDER = (
        "product_plan",
        "launch_date",
        "data_residency",
        "notice_required",
        "price_band",
        "currency",
        "partner_terms",
        "quote_compose_skill",
    )

    def assemble(self, monitor: ExecutionReferenceMonitor) -> ResolvedQuoteInputs:
        values = {slot_id: monitor.resolve(slot_id) for slot_id in self.SLOT_ORDER}
        return ResolvedQuoteInputs(
            product_plan=str(values["product_plan"]),
            launch_date=str(values["launch_date"]),
            data_residency=str(values["data_residency"]),
            notice_required=bool(values["notice_required"]),
            price_band=str(values["price_band"]),
            currency=str(values["currency"]),
            partner_terms_code=str(values["partner_terms"]),
            quote_compose_skill_ref=str(values["quote_compose_skill"]),
        )


class QuoteRenderer:
    version = "workspace-quote-renderer@1.0.0"

    def render(
        self,
        *,
        task_literals: QuoteTaskLiterals,
        inputs: ResolvedQuoteInputs,
    ) -> QuotePayload:
        return QuotePayload(
            owner=task_literals.owner,
            customer_id=task_literals.customer_id,
            product_plan=inputs.product_plan,
            launch_date=inputs.launch_date,
            data_residency=inputs.data_residency,
            notice_required=inputs.notice_required,
            price_band=inputs.price_band,
            currency=inputs.currency,
            partner_terms_code=inputs.partner_terms_code,
        )


def quote_output_lineage() -> tuple[OutputFieldLineage, ...]:
    return (
        OutputFieldLineage(field_path="owner", source_kind="TASK_LITERAL"),
        OutputFieldLineage(field_path="deliverable_kind", source_kind="DETERMINISTIC_COMPUTED"),
        OutputFieldLineage(field_path="customer_id", source_kind="TASK_LITERAL"),
        OutputFieldLineage(field_path="product_plan", source_kind="SLOT", source_slot_ids=("product_plan",)),
        OutputFieldLineage(field_path="launch_date", source_kind="SLOT", source_slot_ids=("launch_date",)),
        OutputFieldLineage(
            field_path="data_residency", source_kind="SLOT", source_slot_ids=("data_residency",)
        ),
        OutputFieldLineage(
            field_path="notice_required", source_kind="SLOT", source_slot_ids=("notice_required",)
        ),
        OutputFieldLineage(field_path="price_band", source_kind="SLOT", source_slot_ids=("price_band",)),
        OutputFieldLineage(field_path="currency", source_kind="SLOT", source_slot_ids=("currency",)),
        OutputFieldLineage(
            field_path="partner_terms_code", source_kind="SLOT", source_slot_ids=("partner_terms",)
        ),
    )


class TraceCoverageVerifier:
    version = "workspace-trace-coverage@1.0.0"

    def verify(
        self,
        *,
        template: TaskTemplateVersion,
        trace: WorkTrace,
        output_payload: Mapping[str, JsonValue],
        observed_channels: tuple[str, ...],
    ) -> TraceCoverageReceipt:
        observed = tuple(
            event.slot_id for event in trace.events if isinstance(event, ReferenceResolvedEvent)
        )
        required = tuple(sorted(item.slot_id for item in template.slots if item.required))
        optional = tuple(sorted(item.slot_id for item in template.slots if not item.required))
        missing = tuple(sorted(set(required) - set(observed)))
        unexpected = tuple(sorted(set(observed) - set(required) - set(optional)))
        duplicates = len(observed) != len(set(observed))
        unmediated = tuple(sorted(set(observed_channels) - {"REFERENCE_MONITOR"}))
        output_digest_ok = trace.output_digest == sha256_digest(dict(output_payload))
        lineage = quote_output_lineage()
        business_fields = set(output_payload) - {"rebased_from", "rebase_change_set", "context_manifest"}
        lineage_fields = {item.field_path for item in lineage}
        lineage_ok = business_fields.issubset(lineage_fields)
        passed = not missing and not unexpected and not duplicates and not unmediated and output_digest_ok and lineage_ok
        reasons = []
        if duplicates:
            unexpected = tuple(sorted((*unexpected, "DUPLICATE_SLOT_ACCESS")))
        if not output_digest_ok:
            unmediated = tuple(sorted((*unmediated, "OUTPUT_DIGEST_MISMATCH")))
        if not lineage_ok:
            reasons.append("OUTPUT_FIELD_LINEAGE_INCOMPLETE")
            unmediated = tuple(sorted((*unmediated, *reasons)))
        return TraceCoverageReceipt(
            id=f"trace-coverage:{trace.id.split(':')[-1]}@{trace.version}",
            trace_ref=trace.ref,
            template_ref=template.ref,
            expected_required_slots=required,
            allowed_optional_slots=optional,
            observed_slots=tuple(sorted(observed)),
            missing_slots=missing,
            unexpected_slots=unexpected,
            unmediated_channels=unmediated,
            output_field_lineage=lineage,
            status=CoverageStatus.PASS if passed else CoverageStatus.FAIL,
            verifier_version=self.version,
        )


class RuntimeDependencyCompiler:
    version = "workspace-runtime-dependency@1.0.0"

    def compile(
        self,
        *,
        task: TaskRequest,
        template: TaskTemplateVersion,
        trace: WorkTrace,
        coverage: TraceCoverageReceipt,
        consumer_ref: str,
        consumer_domain: str,
        revision_lock: Mapping[str, str],
        now: str,
        version: str | None = None,
    ) -> RuntimeDependencyManifest:
        if coverage.status != CoverageStatus.PASS:
            raise RuntimeError("TRACE_COVERAGE_NOT_COMPLETE")
        slot_specs = {item.slot_id: item for item in template.slots}
        resolved = [item for item in trace.events if isinstance(item, ReferenceResolvedEvent)]
        entries = tuple(
            RuntimeDependencyEntry(
                consumer_ref=consumer_ref,
                provider_ref=event.provider_ref,
                provider_digest=event.provider_digest,
                relation=slot_specs[event.slot_id].relation,
                strength=slot_specs[event.slot_id].strength,
                source_event_ref=event.event_id,
                source_event_digest=event.digest,
                slot_id=event.slot_id,
                valid_from=now,
            )
            for event in resolved
        )
        if len(entries) != len(resolved):
            raise RuntimeError("TRACE_MANIFEST_BIJECTION_FAILED")
        consumer_version = consumer_ref.rsplit("@", 1)[1] if "@" in consumer_ref else "v1"
        return RuntimeDependencyManifest(
            id=f"runtime-dependency:{consumer_ref.split(':')[-1].split('@')[0]}",
            version=version or consumer_version,
            consumer_ref=consumer_ref,
            task_ref=task.id,
            trace_ref=trace.ref,
            coverage_receipt_ref=coverage.id,
            issuer_id="system:workspace-runtime-observer",
            authority_domain=consumer_domain,
            completeness=ManifestCompleteness.COMPLETE,
            entries=entries,
            provenance_refs=(trace.ref, coverage.id),
            revision_lock=dict(revision_lock),
            compiled_at=now,
            compiler_version=self.version,
        )
