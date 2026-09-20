#!/usr/bin/env python3
# ruff: noqa: E731, SIM905
"""Verify one OAC evolution pack without importing either implementation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import rfc8785

ZERO = "sha256:" + "0" * 64
QUOTE = "sha256:0212b4cfc2a86552beb1aea4aa35e5771e7c6204a4c45f0d7f9dc59379953b44"
QUOTE_SEMANTICS = {
    "customer_id": "customer:acme", "currency": "EUR", "data_residency": "US region supported",
    "deliverable_kind": "QUOTE", "launch_date": "2026-09-15", "notice_required": True,
    "owner": "sales-owner", "partner_terms_code": "legal-review", "price_band": "strategic",
    "product_plan": "enterprise-plan-v4",
}
RUNS = {
    "base-accepted": ("BASE", "ACCEPT"),
    "split-accepted": ("SPLIT", "ACCEPT"),
    "split-rejected": ("SPLIT", "REJECT"),
}
FACTS = {
    "assess_dependency_impact": ("dependency_impact_satisfied", "impact_within_declared_scope"),
    "commercial-switch-review": ("commercial_switch_satisfied", "commercial_switch_approved"),
    "continuity-option-selection": ("continuity_option_satisfied", "continuity_option_available"),
    "qualification-evidence-check": ("qualification_evidence_satisfied", "qualification_record_approved"),
}
SOURCE = "change demand profile-migration profile-r1 profile-r2 snapshot-r2 source-admission-local source-admission-oac".split()
PLANS = "capsule runtime-admission runtime-approval runtime-preview".split()
RUN_FILES = "evidence execution-approval execution-receipt outcome-certificate outcome-decision outcome-observation".split()
EVOLUTION = "governance-decision pointer-transition portable-source-admission procedure-candidate profile-successor profile-successor-lineage promotion-receipt proposal regression-evidence replay-evidence rollback-receipt snapshot-successor snapshot-successor-lineage source-predecessor source-successor".split()
PATHS = tuple(
    sorted(
        (
            "event-chain.json",
            "summary.json",
            *(f"source/{name}.json" for name in SOURCE),
            *(f"plans/{case}/{name}.json" for case in ("BASE", "SPLIT") for name in PLANS),
            *(f"runs/{run}/{name}.json" for run in RUNS for name in RUN_FILES),
            *(f"evolution/{name}.json" for name in EVOLUTION),
        )
    )
)
EVENTS = (
    ("OAC_RUNTIME_FORMATION_ACTIVATED",) * 2
    + ("OAC_EXECUTION_COMPLETED",) * 3
    + tuple(
        event
        for _ in RUNS
        for event in ("OAC_CONTROLLED_OBSERVATION_RECORDED", "OAC_CONTROLLED_OUTCOME_ISSUED")
    )
    + ("OAC_PROCEDURE_SOURCE_PROMOTED", "OAC_PROCEDURE_SOURCE_ROLLED_BACK")
)


def _product(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(data.encode()).hexdigest()


def _jcs(value: Any) -> str:
    return "sha256:" + hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _strict(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict)


def _maps(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _maps(child)
    elif isinstance(value, list):
        for child in value:
            yield from _maps(child)


def _digest_ok(value: dict[str, Any]) -> bool:
    claimed = value.get("digest")
    if not isinstance(claimed, str):
        return True
    if {"apiVersion", "kind", "metadata", "spec"} <= value.keys():
        projection = deepcopy(value)
        projection.pop("digest")
        metadata = projection["metadata"]
        for name in "ownerRef governanceRef effectiveFrom effectiveTo".split():
            metadata.setdefault(name, None)
        metadata.setdefault("sourceRefs", [])
        if projection["kind"] == "SemanticChangeSet":
            for delta in projection["spec"].get("deltas", []):
                delta.setdefault("beforeVersion", None)
                delta.setdefault("afterVersion", None)
        return claimed == _jcs(projection)
    if ("resourceId" in value and "metadata" not in value) or set(value) == {"ref", "digest"}:
        return True
    projection = {key: item for key, item in value.items() if key != "digest"}
    if {"id", "version", "kind", "state", "payload", "label", "domain"} <= value.keys():
        projection.pop("state")
    return claimed == _product(projection)


def _quote_regression_ok(summary: dict[str, Any]) -> bool:
    regression = summary["preliminary_regression"]
    if summary.get("schema_version") == "orgrebase.workspace-oac-evolution-demo.v1":
        return regression == {"status": "PASS", "expected_quote_digest": QUOTE, "actual_quote_digest": QUOTE}
    if (summary.get("schema_version") != "orgrebase.workspace-oac-evolution-demo.v2"
            or regression.get("schema_version") != "orgrebase.preliminary-quote-regression.v2"):
        return False
    quote, restart, chain = regression["final_quote"], regression["restart"], regression["event_chain"]
    return (
        regression["status"] == "PASS" and _digest_ok(regression) and _digest_ok(quote)
        and _product(regression["expected_quote_semantics"]) == _product(regression["actual_quote_semantics"]) == _product(QUOTE_SEMANTICS)
        and _product({field: quote["payload"].get(field) for field in QUOTE_SEMANTICS}) == _product(QUOTE_SEMANTICS)
        and (quote["id"], quote["version"], quote["state"]) == ("work:quote_acme", "v3", "CURRENT")
        and regression["actual_quote_digest"] == quote["digest"]
        and restart["state_digest_before_close"] == restart["state_digest_after_reopen"]
        and re.fullmatch(r"sha256:[0-9a-f]{64}", str(restart["state_digest_before_close"])) is not None
        and chain["status"] == "PASS" and isinstance(chain["events"], int) and chain["events"] > 0
        and re.fullmatch(r"sha256:[0-9a-f]{64}", str(chain["head_digest"])) is not None
    )


def _ref(value: dict[str, Any]) -> dict[str, Any]:
    metadata = value["metadata"]
    return dict(
        apiVersion="oac.dev/v0alpha1",
        kind=value["kind"],
        namespace=metadata["namespace"],
        resourceId=metadata["id"],
        revision=metadata["revision"],
        digest=value["digest"],
    )


def _eref(kind: str, resource_id: str, digest: str) -> dict[str, Any]:
    return dict(
        apiVersion="oac.dev/v0alpha1",
        kind=kind,
        namespace="oac.examples.supplier",
        resourceId=resource_id,
        revision=1,
        digest=digest,
    )


def _check(failures: list[str], condition: Any, code: str) -> None:
    if not condition:
        failures.append(code)


def _read(root: Path, failures: list[str]) -> dict[str, Any]:
    found = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    _check(failures, not any(path.is_symlink() for path in root.rglob("*")), "PACK_SYMLINK")
    _check(failures, found == {*PATHS, "evidence-index.json"}, "PACK_PATH_SET_INVALID")
    docs = {name: _load(root / name) for name in (*PATHS, "evidence-index.json")}
    entries = docs["evidence-index.json"]["entries"]
    indexed = tuple(item["artifact_ref"] for item in entries) == PATHS
    hashes = all(
        item["sha256"] == "sha256:" + hashlib.sha256((root / item["artifact_ref"]).read_bytes()).hexdigest()
        for item in entries
    )
    _check(failures, len(entries) == 51 and indexed and hashes, "INDEX_CLOSURE_INVALID")
    _check(failures, docs["evidence-index.json"]["pack_digest"] == _product(entries), "PACK_DIGEST_INVALID")
    content = all(
        _digest_ok(item) for name, doc in docs.items() if name != "evidence-index.json" for item in _maps(doc)
    )
    _check(failures, content, "CONTENT_DIGEST_INVALID")
    return docs


def _events(docs: dict[str, Any], failures: list[str]) -> None:
    chain, previous = docs["event-chain.json"], ZERO
    records = chain["records"]
    _check(failures, chain["status"] == "PASS" and chain["events"] == 13, "EVENT_COUNT_INVALID")
    _check(failures, tuple(item["event_type"] for item in records) == EVENTS, "EVENT_ORDER_INVALID")
    for number, event in enumerate(records, 1):
        envelope = dict(
            sequence_no=number,
            event_type=event["event_type"],
            payload=event["payload"],
            previous_digest=previous,
        )
        linked = event["sequence_no"] == number and event["previous_digest"] == previous
        previous = _product(envelope)
        _check(failures, linked and event["event_digest"] == previous, "EVENT_CHAIN_INVALID")
    _check(failures, chain["head_digest"] == previous, "EVENT_HEAD_INVALID")


def _source_plans(docs: dict[str, Any], failures: list[str]) -> None:
    p2, migration = docs["source/profile-r2.json"], docs["source/profile-migration.json"]
    source = docs["source/source-admission-oac.json"]
    demand = docs["source/demand.json"]
    migrated = migration["successor_profile_digest"] == p2["digest"]
    authority = source["spec"]["decisionAuthorityRef"]["resourceId"]
    admitted = source["spec"]["verdict"] == "ADMITTED"
    admitted &= authority in p2["governance"]["admission_authority_refs"]
    _check(failures, migrated and admitted, "SOURCE_ADMISSION_INVALID")
    _check(
        failures,
        demand["metadata"]["id"] == "demand:SC-008" and demand["spec"]["effectCeiling"] == "zero_effect",
        "DEMAND_INVALID",
    )
    base, split = (docs[f"plans/{case}/capsule.json"] for case in ("BASE", "SPLIT"))
    same = base["snapshot"] == split["snapshot"] == docs["source/snapshot-r2.json"]
    same &= base["change"] == split["change"] == docs["source/change.json"]
    bundles = [capsule["runtime_bundle"]["spec"] for capsule in (base, split)]
    plural = (
        len({item["topologyDigest"] for item in bundles}) == 2
        and len({item["obligationContractDigest"] for item in bundles}) == 1
    )
    shapes = [len(capsule["plan"]["spec"]["workUnits"]) for capsule in (base, split)]
    _check(failures, same and plural and shapes == [3, 4], "PLAN_PLURALITY_INVALID")
    for case, capsule in (("BASE", base), ("SPLIT", split)):
        admission = docs[f"plans/{case}/runtime-admission.json"]
        valid = capsule["plan_certificate"]["spec"]["verdict"] == "ACCEPT"
        valid &= admission["status"] == "LOCAL_RUNTIME_ADMISSION_PASS"
        _check(failures, valid, f"PLAN_ADMISSION_INVALID:{case}")


def _roots(capsule, source, demand, change, receipt, evidence):
    evidence_refs = [
        _eref("ExecutionEvidence", item["evidence_id"], item["digest"])
        for item in sorted(evidence, key=lambda item: item["evidence_id"].encode())
    ]
    jcs = lambda domain, **parts: _jcs({"domain": domain, **parts})
    return {
        "sourceRoot": jcs(
            "oac.root/source/v0.1",
            snapshot=[_ref(capsule["snapshot"])],
            sourceAdmissionReceipts=[_ref(source)],
        ),
        "demandRoot": jcs("oac.root/demand/v0.1", demand=[_ref(demand)], changes=[_ref(change)]),
        "planRoot": jcs(
            "oac.root/plan/v0.1",
            plan=[_ref(capsule["plan"])],
            planCertificate=[_ref(capsule["plan_certificate"])],
        ),
        "executionRoot": jcs(
            "oac.state/execution-evidence/v0.1",
            runtimeBinding=[_ref(capsule["runtime_binding"])],
            runtimeBundle=[_ref(capsule["runtime_bundle"])],
            executionReceipt=[_eref("ExecutionReceipt", receipt["receipt_id"], receipt["digest"])],
            executionEvidence=evidence_refs,
        ),
    }


def _runs(docs: dict[str, Any], failures: list[str]) -> dict[str, Any]:
    source, demand, change = (
        docs[f"source/{name}.json"] for name in ("source-admission-oac", "demand", "change")
    )
    events, outcomes = docs["event-chain.json"]["records"], {}
    for offset, (run, (case, wanted)) in enumerate(RUNS.items()):
        prefix, capsule = f"runs/{run}", docs[f"plans/{case}/capsule.json"]
        approval, receipt = (
            docs[f"{prefix}/{name}.json"] for name in ("execution-approval", "execution-receipt")
        )
        evidence, certificate, decision, observation = (
            docs[f"{prefix}/{name}.json"]
            for name in ("evidence", "outcome-certificate", "outcome-decision", "outcome-observation")
        )
        outcomes[run] = certificate
        bound = (
            approval["source_admission_digest"] == receipt["source_admission_digest"] == source["digest"]
            and approval["demand_digest"] == receipt["demand_digest"] == demand["digest"]
        )
        executed = (
            receipt["status"] == "COMPLETED"
            and receipt["plan_digest"] == capsule["plan"]["digest"]
            and receipt["target_writes"] == 0
            and receipt["external_effects"] == "NONE"
        )
        pairs = [
            {"ref": item["evidence_id"], "digest": item["digest"]}
            for item in sorted(evidence, key=lambda item: item["evidence_id"].encode())
        ]
        neutral = {item["observed_value"] for item in evidence} == {
            "zero_effect_handler_completed"
        } and receipt["evidence_projection_digest"] == _product(pairs)
        producer = {observation["producer_id"], observation["producer_authority_ref"]}
        authorities = {
            receipt["executor_id"],
            receipt["runtime_owner_id"],
            decision["oracle_id"],
            decision["issuer_authority_ref"],
        }
        independent = (
            producer.isdisjoint(authorities) and observation["execution_receipt_digest"] == receipt["digest"]
        )
        _check(failures, bound and executed and neutral, f"EXECUTION_INVALID:{run}")
        _check(failures, independent, f"OBSERVATION_AUTHORITY_INVALID:{run}")
        values = {name: value for name, (_, value) in FACTS.items()}
        if run == "split-rejected":
            values["qualification-evidence-check"] = "qualification_record_expired"
        facts = {item["name"]: item for item in observation["facts"]}
        fact_ok = set(facts) == set(values) and all(
            facts[name]["value"] == value
            and facts[name]["state"] == "KNOWN"
            and facts[name]["evidence_refs"] == [observation["observation_id"]]
            for name, value in values.items()
        )
        dimensions = {item["name"]: item for item in decision["dimensions"]}
        dimensions_ok = all(
            dimensions[dimension]["verdict"] == ("PASS" if values[name] == expected else "FAIL")
            and dimensions[dimension]["evidence_refs"] == [observation["observation_id"]]
            for name, (dimension, expected) in FACTS.items()
        )
        verdict_ok = (
            decision["observation_digest"] == observation["digest"]
            and decision["verdict"] == certificate["spec"]["verdict"] == wanted
        )
        event_ok = (
            events[5 + offset * 2]["payload"]["observation_digest"] == observation["digest"]
            and events[6 + offset * 2]["payload"]["decision_digest"] == decision["digest"]
        )
        roots_ok = all(
            certificate["spec"][key] == value
            for key, value in _roots(capsule, source, demand, change, receipt, evidence).items()
        )
        _check(failures, fact_ok and dimensions_ok and verdict_ok, f"OUTCOME_INVALID:{run}")
        _check(failures, event_ok and roots_ok, f"OUTCOME_CLOSURE_INVALID:{run}")
    return outcomes


def _evolution(docs: dict[str, Any], outcomes: dict[str, Any], failures: list[str]) -> None:
    get = lambda name: docs[f"evolution/{name}.json"]
    candidate, proposal, governance = (
        get(name) for name in ("procedure-candidate", "proposal", "governance-decision")
    )
    replay, regression, promotion, rollback = (
        get(name)
        for name in ("replay-evidence", "regression-evidence", "promotion-receipt", "rollback-receipt")
    )
    profile2, profile3, snapshot3 = (
        docs["source/profile-r2.json"],
        get("profile-successor"),
        get("snapshot-successor"),
    )
    prepared, sl, pl = (
        proposal["prepared_successors"],
        get("snapshot-successor-lineage"),
        get("profile-successor-lineage"),
    )
    predecessor, successor, pointer = (
        get("source-predecessor"),
        get("source-successor"),
        get("pointer-transition"),
    )
    forbidden = "plan_digest topology_digest work_unit fixed_dag case_id".split()
    topology_free = not any(token in json.dumps(candidate, sort_keys=True).lower() for token in forbidden)
    outcome_bound = proposal["supporting_outcome_digests"] == [
        outcomes[run]["digest"] for run in ("base-accepted", "split-accepted")
    ] and proposal["counterexample_outcome_digests"] == [outcomes["split-rejected"]["digest"]]
    replay_ok = set(replay["assertions"]) == {
        "DISTINCT_TOPOLOGIES_SAME_OBLIGATION_CONTRACT",
        "TWO_ACCEPTED_EXECUTIONS_REPLAYED_AFTER_SQLITE_REOPEN",
    }
    summary = docs["summary.json"]
    current = summary.get("schema_version") == "orgrebase.workspace-oac-evolution-demo.v2"
    quote_digest = summary["preliminary_regression"]["actual_quote_digest"]
    quote_assertion = "PRELIMINARY_QUOTE_SEMANTICS_AND_RESTART_VERIFIED" if current else "PRELIMINARY_QUOTE_DIGEST_FIXED"
    quote_ok = (_quote_regression_ok(summary) and f"quote-digest:{quote_digest}" in regression["source_refs"]
                and quote_assertion in regression["assertions"])
    _check(failures, topology_free and outcome_bound and replay_ok, "PROPOSAL_PROOF_INVALID")
    _check(failures, quote_ok, "QUOTE_REGRESSION_INVALID")
    snapshot_prepared = json.loads(prepared["snapshot_payload_jcs"])
    node = dict(
        nodeId=candidate["candidate_id"],
        nodeType="procedure-contract",
        domainRef="domain:quality",
        ownerRoleRef="role:quality-qualification",
        admissionStatus="admitted",
    )
    prepared_ok = (
        prepared["candidate_digest"] == candidate["digest"]
        and prepared["profile_payload"] == profile3
        and snapshot_prepared == snapshot3
        and snapshot3["spec"]["nodes"].count(node) == 1
        and candidate["candidate_id"] in snapshot3["spec"]["completeness"]["coveredNodeRefs"]
    )
    profile_ok = profile3["revision"] == "r3" and profile3["runtime_compatibility"] == {
        "mode": "REFERENCE_HANDLER",
        "handler_profile": f"{candidate['candidate_id']}@{candidate['digest']}",
    }
    lineage_ok = (
        pl["profile_payload"] == profile3
        and sl["snapshot_payload_jcs"] == prepared["snapshot_payload_jcs"]
        and sl["candidate_digest"] == candidate["digest"]
    )
    _check(failures, prepared_ok and profile_ok and lineage_ok, "PREPARED_SUCCESSOR_INVALID")
    allowed = set(profile2["governance"]["admission_authority_refs"])
    governed = governance["actor_authority_ref"] in allowed and {
        governance["actor_id"],
        governance["actor_authority_ref"],
    }.isdisjoint({governance["proposal_author_id"], governance["runtime_owner_id"]})
    rolled_back = (
        rollback["actor_authority_ref"] in allowed
        and rollback["actor_id"] not in {rollback["proposal_author_id"], rollback["runtime_owner_id"]}
        and rollback["human_review_status"] == promotion["human_review_status"] == "NOT_RUN"
    )
    _check(failures, governance["verdict"] == "ADMIT" and governed, "GOVERNANCE_AUTHORITY_INVALID")
    _check(failures, rolled_back, "ROLLBACK_AUTHORITY_INVALID")
    lineage = (
        promotion["predecessor_digest"] == rollback["restored_digest"] == predecessor["digest"]
        and promotion["successor_digest"] == rollback["from_digest"] == successor["digest"]
    )
    versions = (
        pointer["after_promotion"]["version"] == "r2"
        and pointer["after_rollback"]["version"] == "r1"
        and pointer["history_retained"] == {"r1": True, "r2": True}
    )
    objects = (
        pointer["active_object_after_promotion"]["payload"]["source_revision"] == successor
        and pointer["active_object_after_rollback"]["payload"]["source_revision"] == predecessor
    )
    _check(failures, lineage and versions and objects, "POINTER_LINEAGE_INVALID")


def _summary(docs: dict[str, Any], failures: list[str]) -> None:
    summary, chain = docs["summary.json"], docs["event-chain.json"]
    boundaries = dict(
        data="SYNTHETIC_FIXTURE",
        assurance="SYNTHETIC_CONTROLLED_PROCESS_ASSURANCE_ONLY",
        observation="SYNTHETIC_CONTROLLED_OBSERVATION_NOT_EXTERNAL_GROUND_TRUTH",
        runtime_evidence="ZERO_EFFECT_HANDLER_COMPLETION_NOT_BUSINESS_TRUTH",
        demand_admission="NO_INDEPENDENT_KIND_SCHEMA_VALIDATED_AND_EXECUTION_APPROVAL_BOUND",
        source_admission_authority="human:veracier-shadow-owner",
        authority_assurance="DECLARED_NOT_AUTHENTICATED",
        human_review="NOT_RUN",
        real_enterprise="NOT_RUN",
        external_effects="NONE",
        target_writes=0,
        production_ready=False,
    )
    restart = summary["restart"]
    valid = (
        summary["status"] == "PASS"
        and summary["maturity"] == "SYNTHETIC_CONTROLLED_REFERENCE_MVP"
        and summary["boundaries"] == boundaries
        and _quote_regression_ok(summary)
    )
    closure = (
        summary["event_chain"] == {key: chain[key] for key in ("status", "events", "head_digest")}
        and restart["idempotent_execution_replay"] == "PASS"
        and restart["replay_event_delta"] == restart["replay_artifact_delta"] == 0
    )
    _check(failures, valid and closure, "SUMMARY_INVALID")


def evaluate(root: str | Path) -> dict[str, Any]:
    failures: list[str] = []
    docs: dict[str, Any] = {}
    try:
        docs = _read(Path(root), failures)
        _events(docs, failures)
        _source_plans(docs, failures)
        outcomes = _runs(docs, failures)
        _evolution(docs, outcomes, failures)
        _summary(docs, failures)
    except (KeyError, TypeError, ValueError, IndexError, OSError, json.JSONDecodeError) as exc:
        failures.append(f"EVIDENCE_SHAPE_INVALID:{type(exc).__name__}:{exc}")
    failures = sorted(set(failures))
    return {
        "schema_version": "orgrebase.oac-evolution-independent-verification.v1",
        "status": "PASS" if not failures else "FAIL",
        "artifact_count": 51 if len(docs) == 52 else 0,
        "event_count": docs.get("event-chain.json", {}).get("events"),
        "pack_digest": docs.get("evidence-index.json", {}).get("pack_digest"),
        "product_imports": 0,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_root", nargs="?", type=Path, default=Path("evidence/oac-evolution/latest"))
    result = evaluate(parser.parse_args().evidence_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
