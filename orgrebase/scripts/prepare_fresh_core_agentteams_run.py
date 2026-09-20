#!/usr/bin/env python3
"""Freeze answer-free, digest-bound tasks for four live Core AgentTeams Workers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

DEMO_SCHEMA = "orgrebase.demo.v1"
RUN_SCHEMA = "orgrebase.agentteams-live-run.v1"
PROJECTION_SCHEMA = "orgrebase.agentteams-task-projection.v1"
BUNDLE_SCHEMA = "orgrebase.agentteams-task-bundle.v1"
CANDIDATE_SCHEMA = "orgrebase.candidate-result.v1"
SPECIALISTS = (
    "product-steward",
    "legal-steward",
    "gtm-steward",
    "skill-curator",
)
ALL_WORKERS = ("change-coordinator", *SPECIALISTS)
GTM_TARGETS = (
    "work:sales_quote_a",
    "work:support_doc_b",
    "work:partner_brief_e",
)
PROHIBITIONS = (
    "no_canonical_write",
    "no_approval",
    "no_apply",
    "no_cross_domain_authority",
)
PRIMARY_OUTPUT = {
    "product-steward": "ClaimDeltaCandidate",
    "legal-steward": "MinimalClaimCandidate",
    "gtm-steward": "ImpactCandidate",
    "skill-curator": "SkillPatchCandidate",
}
FORBIDDEN_PROJECTION_KEYS = {
    "output",
    "candidate_result",
    "expected_answer",
    "expected_output",
    *PRIMARY_OUTPUT.values(),
}
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
FORBIDDEN_PROMPT_ANSWER_PATTERNS = (
    re.compile(r"\b20\d{2}-\d{2}-\d{2}\b"),
    re.compile(r"\bvalue\s*(?:=|:|->|\u2192)\s*(?:true|false)\b", re.IGNORECASE),
    re.compile(
        r"\bwork:[a-z0-9_.-]+\s*(?:=|:|->|\u2192).*\b"
        r"(?:AFFECTED_HARD|AFFECTED_REVIEW|AFFECTED_INFORMATIONAL|"
        r"UNAFFECTED_WITHIN_DECLARED_BOUNDARY|UNKNOWN)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:from_version|candidate_version)\s*(?:=|:|->|\u2192)\s*v?\d+\.\d+",
        re.IGNORECASE,
    ),
)


class PreparationError(RuntimeError):
    """An input is not the exact supported fresh-run contract."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _pretty_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _value_digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _bytes_digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationError(f"invalid JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise PreparationError(f"expected JSON object: {path}")
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PreparationError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise PreparationError(f"{label} must be an array")
    return value


def _verify_content_digest(value: dict[str, Any], label: str) -> None:
    declared = value.get("digest")
    payload = {key: item for key, item in value.items() if key != "digest"}
    if not DIGEST_PATTERN.fullmatch(str(declared)) or declared != _value_digest(payload):
        raise PreparationError(f"{label} content digest mismatch")


def _task_by_worker(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tasks = _sequence(plan.get("tasks"), "orchestration plan tasks")
    result: dict[str, dict[str, Any]] = {}
    for raw in tasks:
        task = _mapping(raw, "orchestration task")
        worker = task.get("agent_name")
        if isinstance(worker, str):
            if worker in result:
                raise PreparationError(f"duplicate orchestration task: {worker}")
            result[worker] = task
    if set(result) != set(ALL_WORKERS):
        raise PreparationError("orchestration plan does not bind the exact five-Worker set")
    return result


def _worker_by_name(envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    workers: dict[str, dict[str, Any]] = {}
    for raw in _sequence(envelope.get("workers"), "run workers"):
        worker = _mapping(raw, "run worker")
        name = worker.get("worker_name")
        if not isinstance(name, str) or name in workers:
            raise PreparationError("run has missing or duplicate Worker names")
        workers[name] = worker
    if set(workers) != set(ALL_WORKERS):
        raise PreparationError("run envelope does not bind the exact five-Worker set")
    if workers["change-coordinator"].get("role") != "team_leader" or any(
        workers[name].get("role") != "worker" for name in SPECIALISTS
    ):
        raise PreparationError("run Worker roles do not match the Core topology")
    return workers


def _validated_inputs(
    demo: dict[str, Any], envelope: dict[str, Any]
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    if demo.get("schema_version") != DEMO_SCHEMA:
        raise PreparationError("demo schema mismatch")
    if envelope.get("schema_version") != RUN_SCHEMA:
        raise PreparationError("fresh run envelope schema mismatch")
    if not isinstance(envelope.get("run_id"), str) or not str(envelope["run_id"]).startswith(
        "run:orgrebase:live:"
    ):
        raise PreparationError("fresh run id is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(envelope.get("nonce", ""))):
        raise PreparationError("fresh run nonce is invalid")
    if not isinstance(envelope.get("issued_at_ms"), int) or not isinstance(
        envelope.get("expires_at_ms"), int
    ) or envelope["expires_at_ms"] <= envelope["issued_at_ms"]:
        raise PreparationError("fresh run lifetime is invalid")

    change_set = _mapping(demo.get("change_set"), "demo change_set")
    preview = _mapping(demo.get("preview"), "demo preview")
    collaboration = _mapping(demo.get("collaboration"), "demo collaboration")
    plan = _mapping(collaboration.get("orchestration_plan"), "orchestration plan")
    for value, label in (
        (change_set, "ChangeSet"),
        (preview, "Preview"),
        (plan, "OrchestrationPlan"),
    ):
        _verify_content_digest(value, label)
    revision_lock = _mapping(preview.get("revision_lock"), "Preview revision lock")
    _verify_content_digest(revision_lock, "RevisionLock")
    if (
        plan.get("change_set_digest") != change_set.get("digest")
        or plan.get("preview_digest") != preview.get("digest")
        or plan.get("revision_lock_digest") != revision_lock.get("digest")
        or preview.get("change_set_ref")
        != f"{change_set.get('id')}@{change_set.get('revision')}"
    ):
        raise PreparationError("demo plan/preview bindings are inconsistent")

    deltas = _sequence(change_set.get("deltas"), "ChangeSet deltas")
    if len(deltas) != 1:
        raise PreparationError("fresh Core slice requires exactly one delta")
    delta = _mapping(deltas[0], "launch-date delta")
    _verify_content_digest(delta, "ChangeDelta")
    if (
        delta.get("object_id") != "claim:product.launch_date"
        or delta.get("semantic_classification") != "SEMANTIC_DELTA"
    ):
        raise PreparationError("fresh Core slice supports only the launch-date semantic delta")

    tasks = _task_by_worker(plan)
    workers = _worker_by_name(envelope)
    expected_refs = [preview["digest"], change_set["digest"], revision_lock["digest"]]
    for name in ALL_WORKERS:
        task = tasks[name]
        _verify_content_digest(task, f"delegation task {name}")
        if task.get("input_refs") != expected_refs or task.get("candidate_only") is not True:
            raise PreparationError(f"delegation task is not exactly bound: {name}")

    skill = _mapping(envelope.get("skill"), "run Skill")
    if (
        skill.get("name") != "enterprise-launch-readiness"
        or skill.get("assigned_worker") != "skill-curator"
        or not DIGEST_PATTERN.fullmatch(str(skill.get("digest", "")))
    ):
        raise PreparationError("run Skill binding is invalid")
    return change_set, preview, plan, tasks, workers


def _policy() -> dict[str, Any]:
    return {
        "decision_authority": "ORGREBASE_CONTROL_PLANE_AND_EXPLICIT_HUMAN_OWNER",
        "worker_authority": "ADVISORY_CANDIDATE_ONLY",
        "permitted_write": "DECLARED_RESULT_FILE_ONLY",
        "prohibited_actions": list(PROHIBITIONS),
        "on_missing_or_conflicting_evidence": "ABSTAIN_WITH_UNCERTAINTY",
        "prior_candidate_access": "PROHIBITED",
    }


def _source_without_classification(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "object_id": result["object_id"],
        "label": result["label"],
        "result_digest": result["digest"],
        "proof_path": result["proof_path"],
        "boundary": result["boundary"],
        "missing_evidence": result["missing_evidence"],
    }


def _domain_inputs(
    *,
    change_set: dict[str, Any],
    preview: dict[str, Any],
    envelope: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    delta = _mapping(_sequence(change_set["deltas"], "deltas")[0], "delta")
    results = {
        item.get("object_id"): item
        for item in (_mapping(raw, "ImpactResult") for raw in _sequence(preview["results"], "results"))
    }
    missing_gtm = [target for target in GTM_TARGETS if target not in results]
    if missing_gtm:
        raise PreparationError(f"GTM Preview targets are missing: {', '.join(missing_gtm)}")
    skill_results = [
        item for key, item in results.items() if isinstance(key, str) and key.startswith("skill:")
    ]
    if len(skill_results) != 1:
        raise PreparationError("fresh Core slice requires exactly one Skill impact result")
    skill_result = skill_results[0]
    version_refs = sorted(
        ref
        for step in skill_result.get("proof_path", [])
        for ref in step.get("provenance_refs", [])
        if isinstance(ref, str) and ref.startswith("skill-contract:")
    )
    if len(version_refs) != 1 or "@" not in version_refs[0]:
        raise PreparationError("Skill current version cannot be derived from Preview proof")
    current_version = version_refs[0].rsplit("@", 1)[1]
    return {
        "product-steward": {
            "source_type": "CHANGESET_DELTA_WITH_PREVIEW_LOCK",
            "change_set_ref": f"{change_set['id']}@{change_set['revision']}",
            "change_set_digest": change_set["digest"],
            "preview_digest": preview["digest"],
            "revision_lock_digest": preview["revision_lock"]["digest"],
            "delta": {
                "object_id": delta["object_id"],
                "base_version": delta["base_version"],
                "base_value": delta["base_value"],
                "proposed_version": delta["proposed_version"],
                "proposed_value": delta["proposed_value"],
                "changed_fields": delta["changed_fields"],
                "semantic_classification": delta["semantic_classification"],
                "source_delta_digest": delta["digest"],
            },
            "derivation_rule": "Compare the two admitted source values; preserve their direction and subject.",
        },
        "legal-steward": {
            "source_type": "PURPOSE_BOUND_POLICY_PREDICATE",
            "change_fact": {
                "object_id": delta["object_id"],
                "changed_fields": delta["changed_fields"],
                "semantic_classification": delta["semantic_classification"],
                "source_delta_digest": delta["digest"],
            },
            "policy_projection": {
                "policy_ref": "policy:launch-date-customer-notice@r1",
                "predicate": {
                    "all": [
                        {"field": "object_id", "equals": "claim:product.launch_date"},
                        {"field": "semantic_classification", "equals": "SEMANTIC_DELTA"},
                    ]
                },
                "consequence_claim_subject": "claim:legal.customer_notice_required",
                "consequence_value_semantics": "predicate_satisfaction_boolean",
            },
            "disclosure": {
                "restricted_source_access": "DENIED",
                "allowed": "MINIMUM_DERIVED_CLAIM_ONLY",
            },
        },
        "gtm-steward": {
            "source_type": "PREVIEW_PROOF_AND_COVERAGE_FACTS",
            "preview_digest": preview["digest"],
            "targets": [_source_without_classification(results[target]) for target in GTM_TARGETS],
            "classification_rules": [
                {"when": "admitted HARD proof path exists", "derive": "AFFECTED_HARD"},
                {"when": "coverage is incomplete or evidence is missing", "derive": "UNKNOWN"},
                {
                    "when": "complete declared coverage and no admitted path",
                    "derive": "UNAFFECTED_WITHIN_DECLARED_BOUNDARY",
                },
            ],
            "allowed_tool": {
                "name": "read-projection",
                "evidence_role": "DEPENDENCY_EVIDENCE_EQUIVALENT",
                "operation": "read_frozen_preview_proof_and_coverage_facts",
                "transport": "OPENCLAW_READ_EQUIVALENT_READ_ONLY_TOOL",
                "side_effects": "NONE",
                "same_run_receipt_required": True,
                "receipt_ref_convention": "tool:read-projection@{task_projection_digest}",
            },
        },
        "skill-curator": {
            "source_type": "SKILL_DEPENDENCY_PROOF",
            "impact_evidence": _source_without_classification(skill_result),
            "current_contract": {
                "target": skill_result["object_id"],
                "version": current_version,
                "contract_ref": version_refs[0],
            },
            "runtime_skill": {
                "name": envelope["skill"]["name"],
                "digest": envelope["skill"]["digest"],
                "assigned_worker": envelope["skill"]["assigned_worker"],
            },
            "version_policy": {
                "on_hard_dependency_semantic_change": "increment MINOR and reset PATCH",
                "release_authority": "NONE",
                "maximum_proposable_release_stage": "CANARY_ONLY",
            },
        },
    }


def _assert_answer_free(projection: dict[str, Any], prompt: str) -> None:
    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in FORBIDDEN_PROJECTION_KEYS:
                    raise PreparationError(f"prewritten answer structure at {path}.{key}")
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(projection, "projection")
    if '"output"' in prompt or any(f'"{kind}"' in prompt for kind in PRIMARY_OUTPUT.values()):
        raise PreparationError("prompt embeds candidate JSON instead of task instructions")
    if any(pattern.search(prompt) for pattern in FORBIDDEN_PROMPT_ANSWER_PATTERNS):
        raise PreparationError("prompt embeds a concrete business-answer value")


def _result_contract_guide(worker: str, primary: str) -> str:
    common = (
        "Result contract guide (ordinary prompt text; never load the JSON Schema through a tool):\n"
        "Required top-level fields:\n"
        "- schema_version: string literal orgrebase.candidate-result.v1.\n"
        "- run_id and nonce: strings copied exactly from the projection.\n"
        "- worker_name: this bound Worker name; candidate_only: boolean true.\n"
        "- run_envelope_digest and candidate_schema_digest: SHA-256 strings copied from projection.bindings.\n"
        "- task_projection_digest: SHA-256 string copied from projection.digest.\n"
        "- orchestration_plan_digest, delegation_task_digest, and input_refs: copied exactly from projection.bindings.\n"
        "- output: object that must contain exactly one key; that key's exact name is the Primary output kind stated below.\n"
        "- The Primary output kind key's value must be an object. Nest every role-specific required field inside that object; never place those fields directly under output.\n"
        "- uncertainty: array of unique non-empty strings; use an empty array when none.\n"
        "- prohibited_actions_respected: unique array containing exactly the four prohibitions declared by the projection.\n"
    )
    role_guides = {
        "product-steward": (
            f"Primary output kind: {primary}. Its object value has these required fields:\n"
            "- object_id: string beginning with claim:product. and derived from the source delta.\n"
            "- base_value and proposed_value: JSON scalar values preserving the source delta direction.\n"
            "- source_delta_digest: SHA-256 string copied from the source delta.\n"
        ),
        "legal-steward": (
            f"Primary output kind: {primary}. Its object value has these required fields:\n"
            "- subject: string beginning with claim:legal. and derived from the policy consequence.\n"
            "- value: JSON scalar obtained only by evaluating the declared policy predicate.\n"
            "- policy_ref: non-empty string copied from the policy projection.\n"
            "- restricted_source_text_disclosed: boolean false because source disclosure is forbidden.\n"
        ),
        "gtm-steward": (
            f"Primary output kind: {primary}. Its object value is a non-empty object keyed only by work identifiers present in the source projection.\n"
            "Each target value is an object with exactly these required fields:\n"
            "- classification: one of AFFECTED_HARD, AFFECTED_REVIEW, AFFECTED_INFORMATIONAL, UNAFFECTED_WITHIN_DECLARED_BOUNDARY, or UNKNOWN; derive it from proof and coverage rules.\n"
            "- source_result_digest: SHA-256 string copied from that target's source evidence.\n"
            "- tool_receipt_ref: string matching tool:read-projection@sha256:<64 lowercase hex characters>.\n"
        ),
        "skill-curator": (
            f"Primary output kind: {primary}. Its object value has these required fields:\n"
            "- target: string beginning with skill: and derived from current_contract.\n"
            "- from_version and candidate_version: semantic-version strings matching optional v prefix plus major.minor and optional patch; derive the transition from version_policy.\n"
            "- release_ceiling: one of DRAFT, SHADOW, or CANARY_ONLY, bounded by version_policy.\n"
            "- source_result_digest and runtime_skill_digest: SHA-256 strings copied from their named source fields.\n"
        ),
    }
    tool_rule = (
        "- tool_receipt_refs: exactly one entry, tool:read-projection@ followed by projection.digest; use the same exact ref in every impact entry.\n"
        if worker == "gtm-steward"
        else "- tool_receipt_refs: exactly an empty array.\n"
    )
    return common + tool_rule + role_guides[worker]


def _assert_result_contract_guide(prompt: str, primary: str) -> None:
    required_phrases = (
        "output: object that must contain exactly one key",
        "that key's exact name is the Primary output kind",
        "Nest every role-specific required field inside that object",
        "never place those fields directly under output",
        f"Primary output kind: {primary}. Its object value",
    )
    if any(phrase not in prompt for phrase in required_phrases):
        raise PreparationError("result contract guide is structurally ambiguous")


def _prompt(projection: dict[str, Any]) -> str:
    runtime = projection["runtime_paths"]
    worker = projection["worker_name"]
    primary = projection["task_contract"]["primary_output_kind"]
    tool_line = (
        "For GTM, the completed projection read is the declared dependency-evidence-equivalent read-only tool call. Bind its receipt exactly as tool:read-projection@ followed by this projection's digest.\n"
        if worker == "gtm-steward"
        else "Do not invoke any other read or external tool after the projection read.\n"
    )
    return (
        "Execute one fresh OrgRebase advisory task.\n"
        f"Worker: {worker}\n"
        "Tool-call serialization is mandatory: issue at most one tool call in each assistant turn and wait for its result before the next turn.\n"
        f"First, issue the only read tool call: read the frozen task projection at {runtime['projection']}; wait for its result.\n"
        f"The JSON Schema staged at {runtime['candidate_schema']} is an offline validation contract only. Never read it through a tool.\n"
        "Never issue parallel tool calls and never combine the projection read with write.\n"
        f"Independently derive the permitted {primary}; do not search for, request, or copy prior candidates.\n"
        + tool_line
        + _result_contract_guide(worker, primary)
        + "Use only facts and rules present in the projection. If they are insufficient, record uncertainty and abstain.\n"
        "Preserve every run, nonce, plan, task, input, projection, envelope, and Schema digest exactly.\n"
        "You have no approval, Apply, publication, or canonical-state authority.\n"
        f"Finally, issue write as a separate single-tool turn: write exactly one JSON result to {runtime['result']}; make no other write.\n"
        "Return only a short completion message after the file is written.\n"
    )


def build_bundle(
    *, demo: dict[str, Any], envelope: dict[str, Any], candidate_schema_bytes: bytes
) -> tuple[dict[str, bytes], dict[str, Any]]:
    try:
        candidate_schema = json.loads(candidate_schema_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationError("candidate Schema is invalid JSON") from exc
    if not isinstance(candidate_schema, dict) or candidate_schema.get("properties", {}).get(
        "schema_version", {}
    ).get("const") != CANDIDATE_SCHEMA:
        raise PreparationError("candidate Schema version mismatch")

    change_set, preview, plan, tasks, workers = _validated_inputs(demo, envelope)
    domain_inputs = _domain_inputs(change_set=change_set, preview=preview, envelope=envelope)
    run_digest = _value_digest(envelope)
    schema_digest = _bytes_digest(candidate_schema_bytes)
    run_slug = hashlib.sha256(
        f"{envelope['run_id']}:{envelope['nonce']}".encode()
    ).hexdigest()[:16]
    files: dict[str, bytes] = {
        "candidate-result.schema.json": candidate_schema_bytes,
        "run-envelope.json": _pretty_bytes(envelope),
    }
    worker_manifest: list[dict[str, Any]] = []
    for worker_name in SPECIALISTS:
        task = tasks[worker_name]
        # Keep ``<worker>/result.json`` as the suffix: the redacted provider-call
        # exporter proves that the model's write tool produced those exact bytes.
        runtime_root = (
            f"/root/agentteams-fs/agents/{worker_name}/runs/{run_slug}/{worker_name}"
        )
        projection: dict[str, Any] = {
            "schema_version": PROJECTION_SCHEMA,
            "run_id": envelope["run_id"],
            "nonce": envelope["nonce"],
            "issued_at_ms": envelope["issued_at_ms"],
            "expires_at_ms": envelope["expires_at_ms"],
            "worker_name": worker_name,
            "worker_identity": {
                "resource_name": workers[worker_name]["resource_name"],
                "uid": workers[worker_name]["uid"],
                "matrix_user_id": workers[worker_name]["matrix_user_id"],
            },
            "bindings": {
                "run_envelope_digest": run_digest,
                "candidate_schema_digest": schema_digest,
                "orchestration_plan_digest": plan["digest"],
                "delegation_task_digest": task["digest"],
                "input_refs": task["input_refs"],
            },
            "task_contract": {
                "task_id": task["id"],
                "purpose": task["purpose"],
                "authority_domain": task["authority_domain"],
                "required_capabilities": task["required_capabilities"],
                "allowed_output_kinds": task["allowed_output_kinds"],
                "primary_output_kind": PRIMARY_OUTPUT[worker_name],
                "failure_disposition": task["failure_disposition"],
                "candidate_only": True,
            },
            "source_projection": domain_inputs[worker_name],
            "authority_policy": _policy(),
            "runtime_paths": {
                "projection": f"{runtime_root}/projection.json",
                "candidate_schema": f"{runtime_root}/candidate-result.schema.json",
                "run_envelope": f"{runtime_root}/run-envelope.json",
                "prompt": f"{runtime_root}/prompt.txt",
                "result": f"{runtime_root}/result.json",
            },
        }
        projection["digest"] = _value_digest(projection)
        prompt = _prompt(projection)
        _assert_result_contract_guide(prompt, PRIMARY_OUTPUT[worker_name])
        _assert_answer_free(projection, prompt)
        projection_bytes = _pretty_bytes(projection)
        prompt_bytes = prompt.encode("utf-8")
        projection_path = f"workers/{worker_name}/projection.json"
        prompt_path = f"workers/{worker_name}/prompt.txt"
        files[projection_path] = projection_bytes
        files[prompt_path] = prompt_bytes
        worker_manifest.append(
            {
                "worker_name": worker_name,
                "matrix_user_id": workers[worker_name]["matrix_user_id"],
                "task_id": task["id"],
                "delegation_task_digest": task["digest"],
                "input_refs": task["input_refs"],
                "projection_path": projection_path,
                "projection_digest": projection["digest"],
                "projection_artifact_digest": _bytes_digest(projection_bytes),
                "prompt_path": prompt_path,
                "prompt_digest": _bytes_digest(prompt_bytes),
                "runtime_paths": projection["runtime_paths"],
                "result_bundle_path": f"workers/{worker_name}/result.json",
            }
        )

    manifest: dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA,
        "run_id": envelope["run_id"],
        "nonce": envelope["nonce"],
        "issued_at_ms": envelope["issued_at_ms"],
        "expires_at_ms": envelope["expires_at_ms"],
        "source_commitments": {
            "demo_digest": _value_digest(demo),
            "change_set_digest": change_set["digest"],
            "preview_digest": preview["digest"],
            "revision_lock_digest": preview["revision_lock"]["digest"],
            "orchestration_plan_digest": plan["digest"],
            "run_envelope_digest": run_digest,
            "candidate_schema_digest": schema_digest,
        },
        "workers": worker_manifest,
        "answer_free_attestation": {
            "scanner": "orgrebase.answer-free-task-projection@1.0.0",
            "scope": "worker projection and single-read prompt bytes, including the ordinary-text structural result guide",
            "prior_candidates_included": False,
            "prewritten_candidate_structures_found": 0,
            "canonical_write_authority_granted": False,
            "prompt_contract_guide": "STRUCTURE_ONLY_NO_EXPECTED_BUSINESS_VALUES",
            "schema_delivery": "STAGED_OFFLINE_VALIDATION_ONLY_NOT_A_TOOL_RESPONSE",
            "planned_read_tool_calls_per_worker": 1,
        },
    }
    manifest["digest"] = _value_digest(manifest)
    files["manifest.json"] = _pretty_bytes(manifest)
    return files, manifest


def write_bundle(files: dict[str, bytes], output_dir: Path) -> None:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise PreparationError(f"refusing to overwrite existing output: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        for relative, data in sorted(files.items()):
            path = temporary / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        os.replace(temporary, output_dir)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", type=Path, default=Path("evidence/latest/demo.json"))
    parser.add_argument("--run-envelope", type=Path, required=True)
    parser.add_argument(
        "--candidate-schema",
        type=Path,
        default=Path("schemas/agentteams-candidate-result.schema.json"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        files, manifest = build_bundle(
            demo=_object(args.demo),
            envelope=_object(args.run_envelope),
            candidate_schema_bytes=args.candidate_schema.read_bytes(),
        )
        write_bundle(files, args.output_dir)
    except (OSError, PreparationError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        json.dumps(
            {
                "status": "PREPARED",
                "run_id": manifest["run_id"],
                "workers": len(manifest["workers"]),
                "bundle_digest": manifest["digest"],
                "output_dir": str(args.output_dir.resolve()),
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
