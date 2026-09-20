from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from io import BytesIO
from pathlib import Path

import pytest
from pydantic import BaseModel

from orgrebase.business_evaluation import COST_CATEGORIES, PairedObservation, business_report
from orgrebase.cli import main
from orgrebase.cost_evidence import qualify_cost_evidence
from orgrebase.digest import sha256_digest
from orgrebase.workspace.model_observations import (
    ModelAttemptObservation,
    ModelAttemptObserver,
    ModelUsage,
    read_model_attempts,
)
from orgrebase.workspace.model_provider import LiveHTTPModelProvider
from orgrebase.workspace.models import ModelRequest


class EvidenceCase:
    def __init__(self, directory: Path):
        self.directory = directory
        self.manifest = {"schema_version": "orgrebase.cost-evidence-manifest.v1", "files": [], "runs": []}
        self.payload = [{"case_id": "one", "organization_id": "org", "cluster_id": "one", "period": "2026-09",
                         "currency": "USD", "evidence_class": "SYNTHETIC"}]
        self.sources = {}
        for side in ("baseline", "product"):
            ref = f"run-{side}"
            costs = []
            (directory / ref).mkdir()
            self.manifest["runs"].append({"run_ref": ref, "attempt_directory": ref, "dispatch_ids": [],
                                          "declared_complete": True, "no_dispatch_reason": "Synthetic manual-only run"})
            for category in sorted(COST_CATEGORIES):
                cost_ref = f"{side}-{category}"
                amount = "10" if category == "human_review" else "0"
                costs.append({"observation_ref": cost_ref, "category": category, "amount": amount})
                self.source(cost_ref, {
                    "schema_version": "orgrebase.cost-source.v1", "issuer": "synthetic-lab",
                    "document_id": cost_ref, "line_item_id": "1", "category": category,
                    "basis": "HUMAN_ESTIMATE" if category == "human_review" else "MEASURED_ZERO",
                    "recorded_at": "2026-09-12T10:00:00Z", "evidence_class": "SYNTHETIC",
                    "currency": "USD", "amount": amount, "measurement_note": "Explicit synthetic measurement",
                    "allocations": [{"run_ref": ref, "amount": amount, "dispatch_ids": []}],
                })
            self.payload[0][side] = {"run_ref": ref, "outcome": "QUALIFIED", "result_evidence_ref": f"{side}-result",
                                     "costs": costs}

    def source(self, ref: str, payload: dict) -> None:
        self.sources[ref] = deepcopy(payload)
        raw = json.dumps(payload).encode()
        filename = f"{ref}.json"
        (self.directory / filename).write_bytes(raw)
        item = {"ref": ref, "path": filename, "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
        self.manifest["files"] = [entry for entry in self.manifest["files"] if entry["ref"] != ref] + [item]

    def cost(self, side: str, category: str) -> dict:
        return next(item for item in self.payload[0][side]["costs"] if item["category"] == category)

    def attempt(self, side="product", *, dispatch="a" * 32, phase="RESULT", known_usage=False):
        run_ref = f"run-{side}"
        usage = ModelUsage(status="REPORTED", basis="provider_response", input_tokens=10, output_tokens=5,
                           reported_fields=("input_tokens", "output_tokens")) if known_usage else ModelUsage(
                               status="UNAVAILABLE", basis="unavailable")
        record = ModelAttemptObservation(
            dispatch_id=dispatch, phase=phase, run_ref=run_ref, task_ref="task", request_digest=sha256_digest("request"),
            request_attempt=0, provider="example", requested_model="model-1", observed_model="model-1" if phase == "RESULT" else None,
            provider_request_id=None, dispatch_state="RESPONSE_RECEIVED" if phase == "RESULT" else "DISPATCH_MAY_HAVE_OCCURRED",
            response_state="VALID" if phase == "RESULT" else "UNKNOWN", request_body_digest=sha256_digest("body"),
            request_body_bytes=100, response_body_bytes=100 if phase == "RESULT" else None,
            response_body_digest=sha256_digest("response") if phase == "RESULT" else None,
            response_receipt_digest=sha256_digest("receipt") if phase == "RESULT" else None,
            usage=usage, duration_ms=1, observed_at="2026-09-12T09:00:00Z", persistence="DURABLE", error_code=None,
        )
        if phase == "RESULT":
            intent_payload = record.model_dump(mode="json", exclude={"digest"})
            intent_payload.update(phase="INTENT", observed_model=None, dispatch_state="DISPATCH_MAY_HAVE_OCCURRED",
                                  response_state="UNKNOWN", response_body_bytes=None, response_body_digest=None,
                                  response_receipt_digest=None, usage=ModelUsage(status="UNAVAILABLE", basis="unavailable"))
            intent = ModelAttemptObservation.model_validate(intent_payload)
            (self.directory / run_ref / f"{dispatch}.intent.json").write_text(intent.model_dump_json())
        (self.directory / run_ref / f"{dispatch}.{phase.lower()}.json").write_text(record.model_dump_json())
        coverage = next(item for item in self.manifest["runs"] if item["run_ref"] == run_ref)
        if dispatch not in coverage["dispatch_ids"]:
            coverage["dispatch_ids"].append(dispatch)
        ref = f"{side}-model"
        source = self.sources[ref]
        source["basis"] = "BILL"
        source["amount"] = "3"
        source["allocations"][0]["amount"] = "3"
        if dispatch not in source["allocations"][0]["dispatch_ids"]:
            source["allocations"][0]["dispatch_ids"].append(dispatch)
        self.source(ref, source)
        self.cost(side, "model")["amount"] = "3"
        return record

    def run(self):
        path = self.directory / "manifest.json"
        path.write_text(json.dumps(self.manifest))
        return qualify_cost_evidence(self.payload, path)


def codes(result):
    return {item["code"] for item in result["diagnostics"]}


def test_complete_synthetic_measurements_preserve_legacy_report(tmp_path):
    case = EvidenceCase(tmp_path)
    report = case.run()
    assert report["status"] == "COMPLETE_COST"
    assert report["report"] == business_report(tuple(PairedObservation.model_validate(item) for item in case.payload))
    assert report["cost_basis_counts"] == {"HUMAN_ESTIMATE": 2, "MEASURED_ZERO": 10}
    assert report["enterprise_roi_proven"] is report["unrecorded_dispatches_ruled_out"] is False
    assert report["cost_source_authenticity"] == "REQUIRES_INDEPENDENT_REVIEW"


@pytest.mark.parametrize("fault", ("missing_category", "unknown_amount", "unmeasured_zero", "missing_ref", "wrong_digest"))
def test_unknown_or_unresolvable_expense_never_becomes_zero(tmp_path, fault):
    case = EvidenceCase(tmp_path)
    if fault == "missing_category":
        case.payload[0]["product"]["costs"].pop()
    elif fault == "unknown_amount":
        case.cost("product", "model")["amount"] = None
    elif fault == "unmeasured_zero":
        source = case.sources["product-model"]
        source["basis"] = "UNMEASURED"
        case.source("product-model", source)
    elif fault == "missing_ref":
        case.manifest["files"] = case.manifest["files"][:-1]
    else:
        (tmp_path / case.manifest["files"][0]["path"]).write_text("{}")
    report = case.run()
    assert report["status"] == "INCOMPLETE_COST"
    assert report["diagnostics"]
    assert "report" not in report and "total_cost" not in report


@pytest.mark.parametrize("field,value", (("currency", "EUR"), ("category", "tools"), ("amount", "12")))
def test_source_amount_currency_and_category_must_match(tmp_path, field, value):
    case = EvidenceCase(tmp_path)
    source = case.sources["product-human_review"]
    source[field] = value
    case.source("product-human_review", source)
    assert case.run()["status"] == "INCOMPLETE_COST"


def test_alias_refs_cannot_double_count_one_underlying_charge(tmp_path):
    case = EvidenceCase(tmp_path)
    source = case.sources["product-human_review"]
    case.source("alias", source)
    case.payload[0]["product"]["costs"].append({"observation_ref": "alias", "category": "human_review", "amount": "10"})
    assert "COST_UNDERLYING_CHARGE_COUNTED_MORE_THAN_ONCE" in codes(case.run())


def test_shared_invoice_requires_exact_explicit_allocations(tmp_path):
    case = EvidenceCase(tmp_path)
    source = case.sources["product-human_review"]
    source["amount"] = "20"
    source["allocations"] = [{"run_ref": "run-baseline", "amount": "10"}, {"run_ref": "run-product", "amount": "10"}]
    case.source("baseline-human_review", source)
    case.source("product-human_review", source)
    assert case.run()["status"] == "COMPLETE_COST"
    source["allocations"][1]["amount"] = "9"
    case.source("baseline-human_review", source)
    case.source("product-human_review", source)
    assert "COST_ALLOCATION_TOTAL_MISMATCH" in codes(case.run())


@pytest.mark.parametrize("phase", ("INTENT", "RESULT"))
def test_bill_can_price_an_attempt_without_inventing_unknown_tokens(tmp_path, phase):
    case = EvidenceCase(tmp_path)
    case.attempt(phase=phase)
    result = case.run()
    assert result["status"] == "COMPLETE_COST"
    assert result["coverage"][1]["usage_unknown_dispatches"] == ["a" * 32]
    assert result["report"]["product"]["cost_by_category"]["model"] == "3"


@pytest.mark.parametrize("fault", ("undeclared_attempt", "unpriced_attempt", "missing_observation", "declared_incomplete"))
def test_all_declared_and_enumerated_dispatches_must_be_covered(tmp_path, fault):
    case = EvidenceCase(tmp_path)
    case.attempt()
    case.attempt(dispatch="b" * 32)
    coverage = case.manifest["runs"][1]
    if fault == "undeclared_attempt":
        coverage["dispatch_ids"].pop()
    elif fault == "unpriced_attempt":
        source = case.sources["product-model"]
        source["allocations"][0]["dispatch_ids"].pop()
        case.source("product-model", source)
    elif fault == "missing_observation":
        (tmp_path / "run-product" / ("b" * 32 + ".result.json")).unlink()
        (tmp_path / "run-product" / ("b" * 32 + ".intent.json")).unlink()
    else:
        coverage["declared_complete"] = False
    assert case.run()["status"] == "INCOMPLETE_COST"


def test_intent_and_result_are_one_dispatch_but_retries_are_not(tmp_path):
    case = EvidenceCase(tmp_path)
    case.attempt(phase="INTENT")
    case.attempt(phase="RESULT")
    case.attempt(dispatch="b" * 32)
    result = case.run()
    assert result["status"] == "COMPLETE_COST"
    assert result["coverage"][1]["dispatches_observed"] == 2


def test_tampered_observation_is_not_accepted_by_declared_inventory(tmp_path):
    case = EvidenceCase(tmp_path)
    case.attempt()
    path = tmp_path / "run-product" / ("a" * 32 + ".result.json")
    payload = json.loads(path.read_text())
    payload["request_attempt"] += 1
    path.write_text(json.dumps(payload))
    assert "COST_ATTEMPT_DIGEST_MISMATCH" in codes(case.run())


@pytest.mark.parametrize("escape", ("absolute", "parent", "symlink", "oversize"))
def test_sources_cannot_escape_manifest_directory_or_read_unbounded_files(tmp_path, escape):
    case = EvidenceCase(tmp_path)
    source = case.manifest["files"][0]
    original = tmp_path / source["path"]
    if escape == "absolute":
        source["path"] = str(original)
    elif escape == "parent":
        source["path"] = "../outside.json"
    elif escape == "symlink":
        link = tmp_path / "link.json"
        link.symlink_to(original)
        source["path"] = "link.json"
    else:
        original.write_bytes(b" " * 1_048_577)
    assert case.run()["status"] == "INCOMPLETE_COST"


@pytest.mark.parametrize("fault,expected", (
    ("approval", "COST_RATE_REQUEST_APPLICABILITY_UNVERIFIED"),
    ("usage", "COST_RATE_USAGE_UNKNOWN"),
    ("model", "COST_RATE_MODEL_MISMATCH"),
    ("period", "COST_RATE_OUTSIDE_EFFECTIVE_PERIOD"),
    ("amount", "COST_RATE_AMOUNT_MISMATCH"),
))
def test_approved_rate_requires_known_usage_exact_model_and_request_applicability(tmp_path, fault, expected):
    case = EvidenceCase(tmp_path)
    record = case.attempt(known_usage=True)
    schedule = {"schema_version": "orgrebase.uniform-token-schedule.v1", "provider": "example", "model_id": "model-1",
                "region": "synthetic-region", "currency": "USD", "input_per_token": "0.2", "output_per_token": "0.2",
                "effective_from": "2026-09-01T00:00:00Z", "effective_until": "2026-10-01T00:00:00Z",
                "discount_treatment": "RATES_ARE_NET", "cache_treatment": "UNIFORM_INPUT_RATE",
                "thinking_treatment": "INCLUDED_IN_OUTPUT"}
    case.source("schedule", schedule)
    digest = next(item["digest"] for item in case.manifest["files"] if item["ref"] == "schedule")
    case.source("rate-approval", {"approved": True, "schedule_digest": digest, "request_body_digests": [record.request_body_digest]})
    source = case.sources["product-model"]
    source["basis"] = "APPROVED_RATE"
    source["rate_calculation"] = {"schedule_ref": "schedule", "approval_ref": "rate-approval"}
    case.source("product-model", source)
    assert case.run()["status"] == "COMPLETE_COST"
    if fault == "approval":
        case.source("rate-approval", {"approved": True, "schedule_digest": digest, "request_body_digests": []})
    elif fault in {"usage", "model", "period"}:
        payload = record.model_dump(mode="json", exclude={"digest"})
        if fault == "usage":
            payload["usage"] = ModelUsage(status="UNAVAILABLE", basis="unavailable")
        elif fault == "model":
            payload["observed_model"] = "different-model"
        else:
            payload["observed_at"] = "2026-10-02T00:00:00Z"
        changed = ModelAttemptObservation.model_validate(payload)
        (tmp_path / "run-product" / (record.dispatch_id + ".result.json")).write_text(changed.model_dump_json())
    else:
        source["amount"] = source["allocations"][0]["amount"] = "3.1"
        case.cost("product", "model")["amount"] = "3.1"
        case.source("product-model", source)
    assert expected in codes(case.run())


def test_rate_cannot_relabel_unavailable_usage_as_free(tmp_path):
    case = EvidenceCase(tmp_path)
    case.attempt()
    source = case.sources["product-model"]
    source["basis"] = "APPROVED_RATE"
    case.source("product-model", source)
    assert "COST_RATE_CALCULATION_REQUIRED" in codes(case.run())


def test_possible_dispatch_cannot_use_a_zero_measurement_note_as_a_free_contract(tmp_path):
    case = EvidenceCase(tmp_path)
    case.attempt(phase="INTENT")
    source = case.sources["product-model"]
    source["basis"] = "MEASURED_ZERO"
    source["amount"] = source["allocations"][0]["amount"] = "0"
    case.source("product-model", source)
    case.cost("product", "model")["amount"] = "0"
    assert "COST_MODEL_ZERO_REQUIRES_PRICED_BASIS" in codes(case.run())


@pytest.mark.parametrize("fault", ("missing_intent", "different_bytes"))
def test_paired_dispatch_evidence_cannot_lose_its_intent_or_change_identity(tmp_path, fault):
    case = EvidenceCase(tmp_path)
    record = case.attempt()
    path = tmp_path / "run-product" / (record.dispatch_id + ".intent.json")
    if fault == "missing_intent":
        path.unlink()
    else:
        payload = json.loads(path.read_text())
        payload.pop("digest")
        payload["request_body_bytes"] += 1
        path.write_text(ModelAttemptObservation.model_validate(payload).model_dump_json())
    expected = "COST_ATTEMPT_INTENT_MISSING" if fault == "missing_intent" else "COST_ATTEMPT_IDENTITY_MISMATCH"
    assert expected in codes(case.run())


def test_synthetic_cost_evidence_cannot_support_a_real_enterprise_label(tmp_path):
    case = EvidenceCase(tmp_path)
    case.payload[0]["evidence_class"] = "REAL_ENTERPRISE"
    assert "COST_SOURCE_EVIDENCE_CLASS_MISMATCH" in codes(case.run())


def test_manifest_symlink_parent_is_not_silently_trusted(tmp_path):
    root = tmp_path / "real"
    root.mkdir()
    case = EvidenceCase(root)
    case.run()
    link = tmp_path / "linked"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="COST_EVIDENCE_SYMLINK_DENIED"):
        qualify_cost_evidence(case.payload, link / "manifest.json")


def test_duplicate_json_fields_are_rejected_even_with_matching_file_digest(tmp_path):
    case = EvidenceCase(tmp_path)
    item = case.manifest["files"][0]
    path = tmp_path / item["path"]
    original = path.read_bytes()
    raw = b'{"amount":"999",' + original[1:]
    path.write_bytes(raw)
    item["digest"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    assert "COST_EVIDENCE_DUPLICATE_JSON_FIELD" in codes(case.run())


def test_cli_preserves_legacy_output_and_incomplete_gate_exits_nonzero(tmp_path, monkeypatch, capsys):
    case = EvidenceCase(tmp_path)
    source = tmp_path / "observations.json"
    source.write_text(json.dumps(case.payload))
    monkeypatch.setattr(sys, "argv", ["orgrebase", "business-report", str(source)])
    main()
    assert json.loads(capsys.readouterr().out) == business_report(tuple(PairedObservation.model_validate(item) for item in case.payload))
    case.manifest["runs"][1]["declared_complete"] = False
    case.run()
    output = tmp_path / "qualified.json"
    monkeypatch.setattr(sys, "argv", ["orgrebase", "business-report", str(source), "--evidence", str(tmp_path / "manifest.json"),
                                     "--output", str(output)])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 2
    result = json.loads(capsys.readouterr().out)
    assert result == json.loads(output.read_text())
    assert result["status"] == "INCOMPLETE_COST" and "report" not in result


@pytest.mark.parametrize("response_mode", ("valid", "schema_error", "timeout"))
def test_provider_persisted_attempts_feed_the_offline_gate(tmp_path, monkeypatch, response_mode):
    class Output(BaseModel):
        value: str

    class Response(BytesIO):
        def __init__(self, raw):
            super().__init__(raw)
            self.headers = {"x-request-id": "synthetic-request"}

    def transport(*args, **kwargs):
        if response_mode == "timeout":
            raise TimeoutError("synthetic timeout")
        content = {"value": "candidate"} if response_mode == "valid" else {"other": "invalid"}
        return Response(json.dumps({"id": "synthetic-request", "model": "model-1",
                                    "choices": [{"message": {"content": json.dumps(content)}}]}).encode())

    monkeypatch.setenv("ORGREBASE_MODEL_ENDPOINT", "https://example.invalid/model")
    monkeypatch.setenv("ORGREBASE_MODEL_API_KEY", "synthetic-not-a-secret")
    monkeypatch.setattr("orgrebase.workspace.model_provider.urllib.request.urlopen", transport)
    case = EvidenceCase(tmp_path)
    observer = ModelAttemptObserver(tmp_path / "run-product")
    request = ModelRequest(
        request_id="synthetic", run_id="run-product", task_ref="task", actor_id="agent", purpose="candidate_generation",
        schema_name="Output", schema_digest=sha256_digest(Output.model_json_schema()), context_refs=(), input_refs=(),
        allowed_tool_ids=(), provider="example", model_id="model-1", model_version="v1", prompt_template_ref="synthetic",
        prompt_template_digest=sha256_digest("synthetic"), temperature=0.0, seed=0, max_output_tokens=128, attempt=0,
    )
    receipt = LiveHTTPModelProvider(observer=observer).generate_structured(request=request, output_model=Output)
    assert receipt.status == {"valid": "VALID", "schema_error": "SCHEMA_ERROR", "timeout": "PROVIDER_ERROR"}[response_mode]
    persisted = read_model_attempts((tmp_path / "run-product").resolve())
    assert persisted["coverage"] == "DURABLE"
    dispatches = [item["dispatch_id"] for item in persisted["records"]]
    assert len(dispatches) == 1 and len(list((tmp_path / "run-product").glob("*.json"))) == 2
    case.manifest["runs"][1]["dispatch_ids"] = dispatches
    source = case.sources["product-model"]
    source["basis"] = "BILL"
    source["amount"] = source["allocations"][0]["amount"] = "3"
    source["allocations"][0]["dispatch_ids"] = dispatches
    case.source("product-model", source)
    case.cost("product", "model")["amount"] = "3"
    if response_mode != "valid":
        case.payload[0]["product"]["outcome"] = "FAILED"
        case.payload[0]["product"]["result_evidence_ref"] = None
    result = case.run()
    assert result["status"] == "COMPLETE_COST"
    assert result["coverage"][1]["usage_unknown_dispatches"] == dispatches
    assert result["report"]["product"]["cost_by_category"]["model"] == "3"
    if response_mode != "valid":
        assert result["report"]["product"]["cost_per_qualified_result"] is None
