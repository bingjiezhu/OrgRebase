"""Candidate-only OAC adaptation over the retained BPI Challenge 2019 projection.

This add-on is deliberately isolated from the enterprise-quote product path.  A
pinned AgentTeams task may ask Vertex for a structured mapping candidate, but a
deterministic validator owns admission.  Anonymous process data can establish
field and typed-scope bindings; it cannot establish an organization's values,
real owners, permissions, or approval authorities.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from orgrebase.digest import sha256_digest
from orgrebase.domain import ContentAddressedModel
from orgrebase.workspace.model_observations import ModelAttemptObserver, provider_observations
from orgrebase.workspace.model_provider import VERTEX_MODEL_ID, VertexAIStructuredProvider
from orgrebase.workspace.models import ModelRequest, ModelResponseReceipt
from orgrebase.workspace.native_taskflow import (
    ControlledLocalMatrix,
    LifecycleJournal,
    NativeTaskflowError,
    load_pinned_teamharness,
)
from orgrebase.workspace.oac_quote_adaptation import (
    OACAdaptationReviewGatePending,
    OACOwnerReviewGate,
)

CLAIM_CEILING = "PUBLIC_REAL_DATA_OAC_MAPPING_AND_TYPED_SCOPE_EXECUTION"
SOURCE_SHA256 = "sha256:af63bc687fc4152f2123b05c3af7772b37ef3fce2d3f67f812666c9e356baae7"
PROJECTION_SCHEMA = "orgrebase.workspace-public-real-process-projection.v1"
DATASET_SCHEMA = "orgrebase.workspace-public-real-process-dataset-manifest.v1"
ADAPTATION_SCHEMA = "orgrebase.workspace-oac-public-real-process-binding.v1"
CANDIDATE_SCHEMA = "orgrebase.workspace-oac-public-real-process-mapping-candidate.v1"
CONFIG_SCHEMA = "orgrebase.oac-public-real-process-adaptation-config.v1"
UNKNOWN_DIMENSIONS = (
    "ORGANIZATION_VALUES",
    "REAL_RESPONSIBLE_OWNERS",
    "PERMISSIONS",
    "APPROVAL_AUTHORITIES",
)
CLAIM_LIMITATIONS = (
    "TASK_DEFINED_GROUND_TRUTH_NOT_HUMAN_CAUSAL_ANNOTATION",
    "NOT_ENTERPRISE_QUOTE_DATA_OR_QUOTE_ROI",
    "NOT_ARBITRARY_ENTERPRISE_ADAPTATION",
    "NOT_PRODUCTION_DEPLOYMENT",
    "NOT_EXTERNAL_HUMAN_ACCEPTANCE",
)

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PublicProcessFieldMapping(_StrictModel):
    source_path: str = Field(min_length=1)
    target_oac_path: str = Field(min_length=1)
    semantic: str = Field(min_length=1)


class BPIOACMappingCandidate(_StrictModel):
    schema_version: Literal["orgrebase.workspace-oac-public-real-process-mapping-candidate.v1"] = (
        CANDIDATE_SCHEMA
    )
    adaptation_run_id: str = Field(min_length=1)
    projection_digest: Digest
    mappings: tuple[PublicProcessFieldMapping, ...] = Field(min_length=1)
    typed_scope_keys: tuple[str, ...] = Field(min_length=1)
    organization_values: tuple[str, ...] = ()
    real_responsible_owners: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    approval_authorities: tuple[str, ...] = ()
    unresolved_dimensions: tuple[str, ...] = Field(min_length=4, max_length=4)
    candidate_only: Literal[True] = True
    canonical_target_writes: Literal[0] = 0


class _StructuredProvider(Protocol):
    def generate_structured(
        self, *, request: ModelRequest, output_model: type[BPIOACMappingCandidate]
    ) -> ModelResponseReceipt: ...


class BPIOACAdaptationReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.workspace-oac-public-real-process-binding.v1"] = ADAPTATION_SCHEMA
    status: Literal["PASS", "HOLD"]
    claim_ceiling: Literal["PUBLIC_REAL_DATA_OAC_MAPPING_AND_TYPED_SCOPE_EXECUTION"] = CLAIM_CEILING
    adaptation_run_id: str
    execution_run_id: str | None = None
    source_sha256: Literal["sha256:af63bc687fc4152f2123b05c3af7772b37ef3fce2d3f67f812666c9e356baae7"] = (
        SOURCE_SHA256
    )
    dataset_manifest_digest: Digest
    projection_digest: Digest
    config_digest: Digest
    mapping_candidate_digest: Digest
    mapping_set_digest: Digest
    organizational_demand_digest: Digest
    context_capsule_digest: Digest
    agentteams: dict[str, Any]
    model_attempt: dict[str, Any]
    selected_mapping_lane: Literal["LIVE_VERTEX_AGENT_CANDIDATE", "CONTROLLED_LOCAL_DETERMINISTIC_FALLBACK"]
    deterministic_validation: dict[str, Any]
    unknown_dimensions: dict[str, Any]
    review_gate: OACOwnerReviewGate
    human_admission: dict[str, Any]
    typed_scope_execution: dict[str, Any] | None = None
    limitations: tuple[str, ...] = CLAIM_LIMITATIONS
    candidate_only: Literal[True] = True
    canonical_target_writes: Literal[0] = 0


class BPIOACAdaptationError(RuntimeError):
    """Stable fail-closed public-process adaptation error."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BPIOACAdaptationError(f"INVALID_JSON:{path.name}") from exc
    if not isinstance(value, dict):
        raise BPIOACAdaptationError(f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _verify_sealed(value: Mapping[str, Any], code: str) -> str:
    claimed = value.get("digest")
    payload = dict(value)
    payload.pop("digest", None)
    if not isinstance(claimed, str) or claimed != sha256_digest(payload):
        raise BPIOACAdaptationError(f"{code}_DIGEST_MISMATCH")
    return claimed


def _verify_benchmark_closure(benchmark_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    dataset = _load_object(benchmark_root / "dataset-manifest.json")
    projection = _load_object(benchmark_root / "projection/bpi2019-real-process-projection.json")
    dataset_digest = _verify_sealed(dataset, "DATASET_MANIFEST")
    projection_digest = _verify_sealed(projection, "PROJECTION")
    expected = {
        "LICENSES.json",
        "dataset-manifest.json",
        "projection/bpi2019-real-process-projection.json",
    }
    observed: set[str] = set()
    for line in (benchmark_root / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        if relative not in expected or _file_sha256(benchmark_root / relative) != digest:
            raise BPIOACAdaptationError(f"BENCHMARK_MANIFEST_DRIFT:{relative}")
        observed.add(relative)
    if observed != expected:
        raise BPIOACAdaptationError("BENCHMARK_MANIFEST_CLOSURE_MISMATCH")
    if (
        dataset.get("schema_version") != DATASET_SCHEMA
        or projection.get("schema_version") != PROJECTION_SCHEMA
        or dataset.get("projection_digest") != projection_digest
        or projection.get("upstream", {}).get("sha256") != SOURCE_SHA256
        or projection.get("data_domain") != "PURCHASE_TO_PAY"
        or len(projection.get("queries", [])) != 128
    ):
        raise BPIOACAdaptationError("BPI_INPUT_CONTRACT_MISMATCH")
    if dataset_digest != dataset["digest"]:
        raise BPIOACAdaptationError("DATASET_DIGEST_INVALID")
    return dataset, projection


def _load_config(path: Path, dataset: Mapping[str, Any], projection: Mapping[str, Any]) -> dict[str, Any]:
    config = _load_object(path)
    config_digest = _verify_sealed(config, "ADAPTATION_CONFIG")
    if (
        config.get("schema_version") != CONFIG_SCHEMA
        or config.get("dataset_manifest_digest") != dataset.get("digest")
        or config.get("projection_digest") != projection.get("digest")
        or config.get("unknown_dimensions") != list(UNKNOWN_DIMENSIONS)
        or config.get("claim_ceiling") != CLAIM_CEILING
    ):
        raise BPIOACAdaptationError("ADAPTATION_CONFIG_BINDING_MISMATCH")
    if config_digest != config["digest"]:
        raise BPIOACAdaptationError("ADAPTATION_CONFIG_DIGEST_INVALID")
    return config


def expected_mapping_candidate(
    *, config: Mapping[str, Any], adaptation_run_id: str
) -> BPIOACMappingCandidate:
    return BPIOACMappingCandidate(
        adaptation_run_id=adaptation_run_id,
        projection_digest=str(config["projection_digest"]),
        mappings=tuple(PublicProcessFieldMapping.model_validate(item) for item in config["mappings"]),
        typed_scope_keys=tuple(config["typed_scope_keys"]),
        unresolved_dimensions=UNKNOWN_DIMENSIONS,
    )


def validate_mapping_candidate(
    *, candidate: BPIOACMappingCandidate, expected: BPIOACMappingCandidate
) -> dict[str, Any]:
    checks = {
        "run_bound": candidate.adaptation_run_id == expected.adaptation_run_id,
        "projection_bound": candidate.projection_digest == expected.projection_digest,
        "mapping_allowlist_exact": candidate.mappings == expected.mappings,
        "typed_scope_exact": candidate.typed_scope_keys == expected.typed_scope_keys,
        "organization_values_unknown": not candidate.organization_values,
        "real_responsible_owners_unknown": not candidate.real_responsible_owners,
        "permissions_unknown": not candidate.permissions,
        "approval_authorities_unknown": not candidate.approval_authorities,
        "unknowns_preserved": candidate.unresolved_dimensions == UNKNOWN_DIMENSIONS,
        "candidate_only": candidate.candidate_only and candidate.canonical_target_writes == 0,
    }
    failures = [key for key, passed in checks.items() if not passed]
    return {
        "verdict": "PASS" if not failures else "HOLD",
        "checks": checks,
        "failures": failures,
        "authority_rule": "UNKNOWN_ORGANIZATIONAL_FACTS_REQUIRE_HUMAN_SUPPLEMENT",
        "canonical_target_writes": 0,
        "digest": sha256_digest(
            {
                "verdict": "PASS" if not failures else "HOLD",
                "checks": checks,
                "failures": failures,
                "authority_rule": "UNKNOWN_ORGANIZATIONAL_FACTS_REQUIRE_HUMAN_SUPPLEMENT",
                "canonical_target_writes": 0,
            }
        ),
    }


def _request(*, adaptation_run_id: str, projection_digest: str, config_digest: str) -> ModelRequest:
    schema_digest = sha256_digest(BPIOACMappingCandidate.model_json_schema(mode="validation"))
    return ModelRequest(
        request_id=f"model-request:{adaptation_run_id}:bpi-oac-mapper",
        run_id=adaptation_run_id,
        task_ref=f"task:{adaptation_run_id}:mapper",
        actor_id="agent:oac-public-process-mapper",
        purpose="candidate_only_public_process_to_oac_mapping",
        schema_name=CANDIDATE_SCHEMA,
        schema_digest=schema_digest,
        context_refs=(f"projection-digest:{projection_digest}", f"config-digest:{config_digest}"),
        input_refs=("bpi2019:retained-public-real-process-projection",),
        allowed_tool_ids=(),
        provider="vertex-ai",
        model_id=VERTEX_MODEL_ID,
        model_version=VERTEX_MODEL_ID,
        prompt_template_ref="orgrebase://prompts/bpi-oac-mapper/v1",
        prompt_template_digest=sha256_digest("bpi-oac-mapper-v1"),
        temperature=0.0,
        seed=None,
        max_output_tokens=4096,
        attempt=0,
    )


def _unwrap(response: Mapping[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(str(response["content"][0]["text"]))
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise NativeTaskflowError("BPI_OAC_AGENTTEAMS_RESPONSE_INVALID") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise NativeTaskflowError("BPI_OAC_AGENTTEAMS_ACTION_FAILED")
    return payload


def _invoke(
    module: Any,
    journal: LifecycleJournal,
    *,
    key: str,
    tool: str,
    action: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = {"action": action, **arguments}
    wrapper = module.call_tool(tool, request)
    payload = _unwrap(wrapper)
    return payload, journal.record(
        key=key,
        tool=tool,
        action=action,
        arguments=request,
        response=wrapper,
        payload=payload,
    )


@contextlib.contextmanager
def _environment(overrides: Mapping[str, str]) -> Iterator[None]:
    old = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _mc_adapter(root: Path) -> tuple[Path, Path]:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    log = root / "mc.jsonl"
    executable = bin_dir / "mc"
    executable.write_text(
        "#!/usr/bin/env python3\nimport json,os,sys\n"
        "p=os.environ.get('ORGREBASE_BPI_OAC_MC_LOG')\n"
        "open(p,'a').write(json.dumps(sys.argv[1:])+'\\n') if p else None\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return bin_dir, log


def _run_agentteams_mapper(
    *,
    checkout: Path,
    lock_path: Path,
    output_dir: Path,
    adaptation_run_id: str,
    expected: BPIOACMappingCandidate,
    config: Mapping[str, Any],
    provider: _StructuredProvider | None,
    vertex_project: str | None,
) -> tuple[BPIOACMappingCandidate, dict[str, Any], dict[str, Any]]:
    module, source = load_pinned_teamharness(checkout, lock_path)
    suffix = hashlib.sha256(adaptation_run_id.encode()).hexdigest()[:16]
    project_id = f"bpi-oac-{suffix}"
    task_id = f"{project_id}-mapper-a1"
    worker = "@bpi-oac-mapper:controlled.local"
    room = "!bpi-oac:controlled.local"
    context = {
        "adaptation_run_id": adaptation_run_id,
        "projection_digest": expected.projection_digest,
        "config_digest": config["digest"],
        "unknown_dimensions": list(UNKNOWN_DIMENSIONS),
        "candidate_only": True,
        "canonical_target_writes": 0,
    }
    spec = json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True)
    request = _request(
        adaptation_run_id=adaptation_run_id,
        projection_digest=expected.projection_digest,
        config_digest=str(config["digest"]),
    )
    prompt = {
        "task": "Map only observed BPI 2019 P2P fields to the allowlisted OAC paths.",
        "non_inference_rule": (
            "Return empty values and preserve UNKNOWN for organization values, real owners, "
            "permissions, and approval authorities."
        ),
        "output_identity": {
            "schema_version": CANDIDATE_SCHEMA,
            "adaptation_run_id": adaptation_run_id,
            "projection_digest": expected.projection_digest,
            "candidate_only": True,
            "canonical_target_writes": 0,
        },
        "observed_source_contract": {
            "data_domain": "PURCHASE_TO_PAY",
            "query_fields": [
                "source_system_ref",
                "change_event.event_ref",
                "vendor_ref",
                "purchase_document_ref",
                "item_ref",
                "window_end",
            ],
            "task_contract_ref": "projection.task_contract",
        },
        "allowlisted_mapping_contract": config["mappings"],
        "required_typed_scope_keys": config["typed_scope_keys"],
        "required_unresolved_dimensions": list(UNKNOWN_DIMENSIONS),
    }
    selected_provider = provider or VertexAIStructuredProvider(
        prompt_payload=prompt,
        project_id=vertex_project,
        model_id=VERTEX_MODEL_ID,
        observer=ModelAttemptObserver(output_dir / "model-attempts"),
    )
    actions: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="orgrebase-bpi-oac-at-") as raw:
        root = Path(raw)
        workspace = root / "workspace"
        workspace.mkdir()
        mc_bin, mc_log = _mc_adapter(root)
        journal = LifecycleJournal(
            output_dir / "action-journal.json",
            output_dir / "raw-mcp",
            public_path_replacements={
                str(checkout.resolve()): "agentteams://pinned-checkout",
                str(root): "orgrebase://bpi-oac-runtime",
                str(output_dir): "orgrebase://bpi-oac-evidence",
            },
        )
        with (
            ControlledLocalMatrix((worker,)) as matrix,
            _environment(
                {
                    "PATH": str(mc_bin) + os.pathsep + os.environ.get("PATH", ""),
                    "ORGREBASE_BPI_OAC_MC_LOG": str(mc_log),
                    "AGENTTEAMS_MATRIX_URL": matrix.url,
                    "AGENTTEAMS_WORKER_MATRIX_TOKEN": "controlled-local-token",
                    "AGENTTEAMS_SHARED_STORAGE_PREFIX": "agentteams/shared",
                    "MC_HOST_agentteams": "http://controlled:local@127.0.0.1:9000",
                    "AGENTTEAMS_AGENT_ROLE": "leader",
                }
            ),
        ):
            _payload, entry = _invoke(
                module,
                journal,
                key="project:create",
                tool="projectflow",
                action="create_project",
                arguments={
                    "workspaceDir": str(workspace),
                    "payload": {"projectId": project_id, "title": "BPI 2019 to OAC candidate"},
                },
            )
            actions.append(entry)
            _payload, entry = _invoke(
                module,
                journal,
                key="project:plan",
                tool="projectflow",
                action="plan_dag",
                arguments={
                    "workspaceDir": str(workspace),
                    "payload": {
                        "projectId": project_id,
                        "tasks": [
                            {
                                "taskId": task_id,
                                "title": "Produce candidate-only public-process OAC mapping",
                                "assignedTo": worker,
                                "dependsOn": [],
                            }
                        ],
                    },
                },
            )
            actions.append(entry)
            for key, tool, action, arguments in (
                (
                    "project:ready",
                    "projectflow",
                    "ready_nodes",
                    {"workspaceDir": str(workspace), "payload": {"projectId": project_id}},
                ),
                (
                    "task:delegate",
                    "taskflow",
                    "delegate_task",
                    {
                        "role": "leader",
                        "workspaceDir": str(workspace),
                        "payload": {
                            "projectId": project_id,
                            "taskId": task_id,
                            "assignedTo": worker,
                            "roomId": room,
                            "spec": spec,
                        },
                    },
                ),
                (
                    "task:ack",
                    "taskflow",
                    "ack_task",
                    {"role": "worker", "workspaceDir": str(workspace), "payload": {"taskId": task_id}},
                ),
            ):
                _payload, entry = _invoke(
                    module, journal, key=key, tool=tool, action=action, arguments=arguments
                )
                actions.append(entry)
            response = selected_provider.generate_structured(
                request=request, output_model=BPIOACMappingCandidate
            )
            live_candidate: BPIOACMappingCandidate | None = None
            if (
                response.status == "VALID"
                and response.schema_valid
                and response.value is not None
                and str(response.evidence_class) == "LIVE_MODEL"
                and response.provider_request_id
            ):
                try:
                    proposed = BPIOACMappingCandidate.model_validate(response.value)
                    if validate_mapping_candidate(candidate=proposed, expected=expected)["verdict"] == "PASS":
                        live_candidate = proposed
                except ValueError:
                    live_candidate = None
            candidate = live_candidate or expected
            lane = (
                "LIVE_VERTEX_AGENT_CANDIDATE"
                if live_candidate is not None
                else "CONTROLLED_LOCAL_DETERMINISTIC_FALLBACK"
            )
            result = json.dumps(
                {
                    "candidate_digest": sha256_digest(candidate.model_dump(mode="json")),
                    "lane": lane,
                    "candidate_only": True,
                    "canonical_target_writes": 0,
                },
                sort_keys=True,
            )
            for key, tool, action, arguments in (
                (
                    "task:submit",
                    "taskflow",
                    "submit_task",
                    {
                        "role": "worker",
                        "workspaceDir": str(workspace),
                        "payload": {
                            "taskId": task_id,
                            "status": "SUCCESS",
                            "summary": result,
                            "deliverables": [],
                        },
                    },
                ),
                (
                    "task:check",
                    "taskflow",
                    "check_task",
                    {"role": "leader", "workspaceDir": str(workspace), "payload": {"taskId": task_id}},
                ),
                (
                    "task:accept",
                    "projectflow",
                    "accept_task_result",
                    {
                        "workspaceDir": str(workspace),
                        "payload": {
                            "projectId": project_id,
                            "taskId": task_id,
                            "resultStatus": "SUCCESS",
                            "accepted": True,
                        },
                    },
                ),
                (
                    "project:complete",
                    "projectflow",
                    "complete_project",
                    {"workspaceDir": str(workspace), "payload": {"projectId": project_id}},
                ),
            ):
                _payload, entry = _invoke(
                    module, journal, key=key, tool=tool, action=action, arguments=arguments
                )
                actions.append(entry)
            matrix_requests = matrix.state.request_count
    model_attempt = {
        "model_attempt_observations": provider_observations(selected_provider, run_ref=adaptation_run_id),
        "status": response.status,
        "provider": response.provider,
        "model_id": response.model_id,
        "model_version": response.model_version,
        "evidence_class": str(response.evidence_class),
        "provider_request_id": response.provider_request_id,
        "provider_request_id_present": bool(response.provider_request_id),
        "response_receipt_digest": response.digest,
        "error_code": response.error_code,
        "selected": live_candidate is not None,
        "fallback_is_separate": live_candidate is None,
    }
    lifecycle = {
        "evidence_class": "CONTROLLED_LOCAL_NATIVE_TASKFLOW",
        "claim_boundary": "PINNED_IN_PROCESS_AGENTTEAMS_NOT_LIVE_DISTRIBUTED_WORKER",
        "source_verification": source,
        "run_id": adaptation_run_id,
        "project_id": project_id,
        "task_id": task_id,
        "action_sequence": [item["action"] for item in actions],
        "action_receipt_digests": [item["digest"] for item in actions],
        "matrix_request_count": matrix_requests,
        "terminal_state": "completed",
        "candidate_only": True,
        "canonical_target_writes": 0,
    }
    return candidate, lifecycle, model_attempt


def _epoch_ms_timestamp(value: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _admit_gate(gate: OACOwnerReviewGate, *, now_epoch_ms: int) -> dict[str, Any]:
    if now_epoch_ms < gate.not_before_epoch_ms:
        raise OACAdaptationReviewGatePending(
            remaining_ms=gate.not_before_epoch_ms - now_epoch_ms,
            not_before=gate.not_before,
        )
    payload = {
        "status": "ADMITTED",
        "actor_id": gate.owner_ref,
        "interaction_evidence": "CONTROLLED_LOCAL_SCRIPTED_OWNER_COMMAND_NOT_EXTERNAL_HUMAN",
        "gate_digest": gate.digest,
        "approved_at_epoch_ms": now_epoch_ms,
        "approved_at": _epoch_ms_timestamp(now_epoch_ms),
        "elapsed_ms": now_epoch_ms - gate.prepared_at_epoch_ms,
        "candidate_only": True,
        "canonical_target_writes": 0,
    }
    return {**payload, "digest": sha256_digest(payload)}


def prepare_bpi_oac_adaptation(
    *,
    benchmark_root: str | Path,
    config_path: str | Path,
    checkout: str | Path,
    lock_path: str | Path,
    output_dir: str | Path,
    provider: _StructuredProvider | None = None,
    vertex_project: str | None = None,
    now_ms: int | None = None,
) -> BPIOACAdaptationReceipt:
    """Produce and admit one AgentTeams mapping candidate after the four-second gate."""

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise BPIOACAdaptationError("OUTPUT_EVIDENCE_DIR_NOT_EMPTY")
    dataset, projection = _verify_benchmark_closure(Path(benchmark_root).resolve())
    config = _load_config(Path(config_path).resolve(), dataset, projection)
    adaptation_run_id = f"run:bpi2019-oac-adaptation:{str(projection['digest'])[7:23]}"
    execution_run_id = f"run:bpi2019-oac-execution:{str(projection['digest'])[7:23]}"
    expected = expected_mapping_candidate(config=config, adaptation_run_id=adaptation_run_id)
    candidate, lifecycle, model_attempt = _run_agentteams_mapper(
        checkout=Path(checkout).resolve(),
        lock_path=Path(lock_path).resolve(),
        output_dir=output,
        adaptation_run_id=adaptation_run_id,
        expected=expected,
        config=config,
        provider=provider,
        vertex_project=vertex_project,
    )
    fallback_payload = {
        "schema_version": "orgrebase.bpi-oac-deterministic-fallback-contract.v1",
        "status": ("SELECTED" if model_attempt["fallback_is_separate"] else "AVAILABLE_NOT_SELECTED"),
        "activation_conditions": [
            "VERTEX_NOT_RUN",
            "VERTEX_PROVIDER_ERROR",
            "VERTEX_SCHEMA_ERROR",
            "LIVE_CANDIDATE_DETERMINISTIC_VALIDATION_HOLD",
        ],
        "candidate_digest": sha256_digest(expected.model_dump(mode="json")),
        "source": "SEALED_CONFIG_ALLOWLIST_AND_PROJECTION_DIGESTS",
        "evidence_class": "LOCAL_DETERMINISTIC",
        "candidate_only": True,
        "canonical_target_writes": 0,
    }
    fallback_contract = {**fallback_payload, "digest": sha256_digest(fallback_payload)}
    model_attempt = {
        **model_attempt,
        "fallback_contract_digest": fallback_contract["digest"],
    }
    validation = validate_mapping_candidate(candidate=candidate, expected=expected)
    mapping_payload = candidate.model_dump(mode="json")
    mapping_candidate_digest = sha256_digest(mapping_payload)
    mapping_set_digest = sha256_digest([mapping_candidate_digest])
    demand = {
        "schema_version": "orgrebase.public-real-process-organizational-demand.v1",
        "purpose": "Evaluate typed P2P downstream scope for a Change Price event",
        "task_contract": projection["task_contract"],
        "projection_digest": projection["digest"],
        "unknown_dimensions": list(UNKNOWN_DIMENSIONS),
        "effect_ceiling": "ZERO_EXTERNAL_EFFECTS",
    }
    demand_digest = sha256_digest(demand)
    capsule = {
        "schema_version": "orgrebase.public-real-process-context-capsule.v1",
        "adaptation_run_id": adaptation_run_id,
        "execution_run_id": execution_run_id,
        "dataset_manifest_digest": dataset["digest"],
        "projection_digest": projection["digest"],
        "config_digest": config["digest"],
        "mapping_set_digest": mapping_set_digest,
        "organizational_demand_digest": demand_digest,
        "claim_ceiling": CLAIM_CEILING,
        "canonical_target_writes": 0,
    }
    capsule_digest = sha256_digest(capsule)
    owner_ref = "human:public-process-oac-admission-owner"
    owner_review_summary_payload = {
        "schema_version": "orgrebase.public-real-process-owner-review-summary.v1",
        "adaptation_run_id": adaptation_run_id,
        "data_class": "PUBLIC_ANONYMIZED_ENTERPRISE_EVENT_LOG",
        "source_ref": "BPI_CHALLENGE_2019_PURCHASE_TO_PAY",
        "dataset_manifest_digest": dataset["digest"],
        "projection_digest": projection["digest"],
        "task_scope": projection["task_contract"],
        "mapping_count": len(candidate.mappings),
        "typed_scope_keys": list(candidate.typed_scope_keys),
        "declared_unknowns": list(UNKNOWN_DIMENSIONS),
        "decision_owner_ref": owner_ref,
        "approval_effects": ["ADMIT_EXACT_PUBLIC_PROCESS_MAPPING_FOR_OFFLINE_VALIDATION"],
        "non_effects": [
            "NO_ENTERPRISE_QUOTE_DATA_CLAIM",
            "NO_REAL_ORGANIZATION_OWNER_INFERENCE",
            "NO_EXTERNAL_SYSTEM_WRITE",
        ],
        "claim_ceiling": CLAIM_CEILING,
        "canonical_target_writes": 0,
    }
    owner_review_summary = {
        **owner_review_summary_payload,
        "digest": sha256_digest(owner_review_summary_payload),
    }
    prepared_ms = int(time.time() * 1000) if now_ms is None else now_ms
    gate = OACOwnerReviewGate(
        adaptation_run_id=adaptation_run_id,
        mapping_set_digest=mapping_set_digest,
        organization_snapshot_digest=sha256_digest(
            {"status": "PARTIAL", "projection_digest": projection["digest"]}
        ),
        organizational_demand_digest=demand_digest,
        owner_review_summary_digest=owner_review_summary["digest"],
        profile_digest=config["digest"],
        pack_digest=dataset["digest"],
        owner_ref=owner_ref,
        review_duration_ms=4000,
        prepared_at_epoch_ms=prepared_ms,
        not_before_epoch_ms=prepared_ms + 4000,
        prepared_at=_epoch_ms_timestamp(prepared_ms),
        not_before=_epoch_ms_timestamp(prepared_ms + 4000),
    )
    early_rejection: dict[str, Any]
    try:
        _admit_gate(gate, now_epoch_ms=prepared_ms)
    except OACAdaptationReviewGatePending as exc:
        early_rejection = {
            "status": "REJECTED_TOO_EARLY",
            "error_code": exc.code,
            "remaining_ms": exc.remaining_ms,
        }
    else:  # pragma: no cover - the four-second contract makes this unreachable
        raise BPIOACAdaptationError("EARLY_ADMISSION_NOT_REJECTED")
    if now_ms is None:
        time.sleep(max(0.0, (gate.not_before_epoch_ms - int(time.time() * 1000)) / 1000))
        admitted_ms = int(time.time() * 1000)
        if admitted_ms < gate.not_before_epoch_ms:
            time.sleep((gate.not_before_epoch_ms - admitted_ms) / 1000)
            admitted_ms = int(time.time() * 1000)
    else:
        admitted_ms = gate.not_before_epoch_ms
    admission = _admit_gate(gate, now_epoch_ms=admitted_ms)
    human_admission = {"early_attempt": early_rejection, "admission": admission}
    unknowns = {
        key: {
            "status": "UNKNOWN",
            "execution_disposition": "HOLD",
            "human_supplement_required": True,
            "inferred_from_anonymous_log": False,
        }
        for key in UNKNOWN_DIMENSIONS
    }
    receipt = BPIOACAdaptationReceipt(
        status="PASS" if validation["verdict"] == "PASS" else "HOLD",
        adaptation_run_id=adaptation_run_id,
        execution_run_id=execution_run_id,
        dataset_manifest_digest=str(dataset["digest"]),
        projection_digest=str(projection["digest"]),
        config_digest=str(config["digest"]),
        mapping_candidate_digest=mapping_candidate_digest,
        mapping_set_digest=mapping_set_digest,
        organizational_demand_digest=demand_digest,
        context_capsule_digest=capsule_digest,
        agentteams=lifecycle,
        model_attempt=model_attempt,
        selected_mapping_lane=(
            "LIVE_VERTEX_AGENT_CANDIDATE"
            if model_attempt["selected"]
            else "CONTROLLED_LOCAL_DETERMINISTIC_FALLBACK"
        ),
        deterministic_validation=validation,
        unknown_dimensions=unknowns,
        review_gate=gate,
        human_admission=human_admission,
    )
    _write(output / "mapping-candidate.json", mapping_payload)
    _write(output / "deterministic-fallback-contract.json", fallback_contract)
    _write(output / "organizational-demand.json", {**demand, "digest": demand_digest})
    _write(output / "context-capsule.json", {**capsule, "digest": capsule_digest})
    _write(output / "owner-review-summary.json", owner_review_summary)
    _write(output / "adaptation-receipt.json", receipt.model_dump(mode="json"))
    return receipt


def finalize_bpi_oac_execution(
    *,
    adaptation_receipt: Mapping[str, Any] | BPIOACAdaptationReceipt,
    execution_receipt_path: str | Path,
    independent_verification_path: str | Path,
    output_path: str | Path,
) -> BPIOACAdaptationReceipt:
    selected = (
        adaptation_receipt.revalidated()
        if isinstance(adaptation_receipt, BPIOACAdaptationReceipt)
        else BPIOACAdaptationReceipt.model_validate(dict(adaptation_receipt))
    )
    if selected.status != "PASS" or selected.typed_scope_execution is not None:
        raise BPIOACAdaptationError("ADAPTATION_NOT_FINALIZABLE")
    execution = _load_object(Path(execution_receipt_path))
    verification = _load_object(Path(independent_verification_path))
    _verify_sealed(execution, "TYPED_SCOPE_EXECUTION")
    _verify_sealed(verification, "TYPED_SCOPE_VERIFICATION")
    typed = next(
        (row for row in execution.get("strategies", []) if row.get("strategy_id") == "OAC_TYPED_ITEM_SCOPE"),
        None,
    )
    if (
        execution.get("status") != "PASS"
        or execution.get("input_closure", {}).get("projection_digest") != selected.projection_digest
        or execution.get("scenario", {}).get("query_count") != 128
        or verification.get("status") != "PASS"
        or verification.get("receipt_digest") != execution.get("digest")
        or verification.get("queries_replayed") != 128
        or not isinstance(typed, dict)
        or typed.get("metrics", {}).get("recall", {}).get("value") != 1.0
        or typed.get("metrics", {}).get("unsafe_false_unaffected_rate", {}).get("value") != 0.0
    ):
        raise BPIOACAdaptationError("TYPED_SCOPE_EXECUTION_VERIFICATION_FAILED")
    execution_binding = {
        "execution_run_id": selected.execution_run_id,
        "adaptation_run_id": selected.adaptation_run_id,
        "mapping_set_digest": selected.mapping_set_digest,
        "context_capsule_digest": selected.context_capsule_digest,
        "organizational_demand_digest": selected.organizational_demand_digest,
        "execution_receipt_digest": execution["digest"],
        "independent_verification_digest": verification["digest"],
        "query_count": 128,
        "ground_truth_class": "TASK_DEFINED_QUERY_GROUND_TRUTH",
        "recall": 1.0,
        "unsafe_false_unaffected_rate": 0.0,
        "canonical_target_writes": 0,
    }
    execution_binding["digest"] = sha256_digest(execution_binding)
    result = BPIOACAdaptationReceipt.model_validate(
        {**selected.model_dump(mode="json", exclude={"digest"}), "typed_scope_execution": execution_binding}
    )
    _write(Path(output_path), result.model_dump(mode="json"))
    return result


__all__ = (
    "CLAIM_CEILING",
    "UNKNOWN_DIMENSIONS",
    "BPIOACAdaptationError",
    "BPIOACAdaptationReceipt",
    "BPIOACMappingCandidate",
    "expected_mapping_candidate",
    "finalize_bpi_oac_execution",
    "prepare_bpi_oac_adaptation",
    "validate_mapping_candidate",
)
