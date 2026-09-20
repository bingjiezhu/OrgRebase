from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from orgrebase.domain import EvidenceClass
from orgrebase.workspace import competition_worker
from orgrebase.workspace.model_provider import DeterministicModelProvider, _receipt

ROOT = Path(__file__).resolve().parents[2]
PROCESS_ROOT = (
    ROOT
    / "evidence"
    / "golden-competition"
    / "latest"
    / "pilot"
    / "golden-run"
)


def _review_input(attempt: int) -> dict[str, Any]:
    name = f"{_review_task_id(attempt)}.json"
    payload = json.loads((PROCESS_ROOT / "process-inputs" / name).read_text(encoding="utf-8"))
    payload["model_provider"] = "ollama-local"
    payload["model_id"] = "qwen2.5:3b"
    return payload


def _review_output(attempt: int) -> dict[str, Any]:
    name = f"{_review_task_id(attempt)}.json"
    return json.loads((PROCESS_ROOT / "process-outputs" / name).read_text(encoding="utf-8"))


def _review_task_id(attempt: int) -> str:
    summary = json.loads((PROCESS_ROOT / "summary.json").read_text(encoding="utf-8"))
    matches = [
        item.get("task_id")
        for item in summary.get("task_bindings", [])
        if item.get("role") == "REVIEWER" and item.get("attempt") == attempt
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise AssertionError(f"expected one reviewer binding for attempt {attempt}")
    return matches[0]


def _provider_class(value: dict[str, Any] | None):
    class Provider:
        def __init__(self, **_kwargs: Any) -> None:
            self.runtime_binding = {
                "status": "BOUND" if value is not None else "NOT_RUN",
                "claim_boundary": "TEST_DOUBLE_NO_NETWORK",
            }

        def generate_structured(self, *, request, output_model):
            if value is None:
                return _receipt(
                    request,
                    status="NOT_RUN",
                    value=None,
                    provider_request_id=None,
                    schema_valid=False,
                    error_code="MODEL_REVIEW_NOT_RUN",
                    evidence_class=EvidenceClass.NOT_RUN,
                )
            return DeterministicModelProvider(lambda _request: value).generate_structured(
                request=request,
                output_model=output_model,
            )

    return Provider


def test_model_advisory_cannot_override_a_deterministic_replan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _review_input(1)
    model_pass = {
        "verdict": "PASS",
        "missing_domains": [],
        "reason_codes": ["TEST_ADVISORY_PASS"],
    }
    monkeypatch.setattr(
        competition_worker,
        "LocalOllamaStructuredProvider",
        _provider_class(model_pass),
    )

    result = competition_worker.review_domain_results(payload)

    assert result["status"] == "PASS"
    assert result["decision"]["verdict"] == "REPLAN"
    assert result["model_advisory"]["verdict"] == "PASS"
    assert result["model_advisory_accepted"] is False
    assert result["model_advisory_disposition_reason"] == (
        "MODEL_ADVISORY_OVERRIDDEN_BY_DETERMINISTIC_VERIFIER"
    )
    assert result["candidate_only"] is True
    assert result["target_writes"] == 0


def test_matching_model_advisory_is_retained_but_never_becomes_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _review_input(2)
    expected = _review_output(2)["decision"]
    monkeypatch.setattr(
        competition_worker,
        "LocalOllamaStructuredProvider",
        _provider_class(expected),
    )

    result = competition_worker.review_domain_results(payload)

    assert result["decision"] == expected
    assert result["model_advisory"] == expected
    assert result["model_advisory_accepted"] is True
    assert result["model_advisory_disposition_reason"] == (
        "MODEL_ADVISORY_MATCHED_DETERMINISTIC_VERIFIER"
    )
    assert result["model_authority"] == (
        "ADVISORY_ONLY_DETERMINISTIC_REVIEWER_AUTHORITATIVE"
    )


def test_unavailable_model_stays_not_run_without_a_fallback_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        competition_worker,
        "LocalOllamaStructuredProvider",
        _provider_class(None),
    )

    result = competition_worker.review_domain_results(_review_input(2))

    assert result["status"] == "NOT_RUN"
    assert result["decision"] is None
    assert result["reason_codes"] == ["MODEL_REVIEW_NOT_RUN"]
    assert result["candidate_only"] is True
    assert result["target_writes"] == 0


def test_reviewer_rejects_an_unregistered_model_provider() -> None:
    payload = deepcopy(_review_input(2))
    payload["model_provider"] = "unregistered-provider"

    with pytest.raises(ValueError, match="COMPETITION_REVIEWER_MODEL_PROVIDER_INVALID"):
        competition_worker.review_domain_results(payload)


@pytest.mark.parametrize("mode", ("worker", "reviewer"))
def test_worker_entrypoint_writes_the_selected_candidate_only_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
) -> None:
    payload = {"mode": mode}
    input_path = tmp_path / f"{mode}-input.json"
    output_path = tmp_path / f"{mode}-output.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    expected = {"status": "PASS", "candidate_only": True, "target_writes": 0}
    monkeypatch.setattr(
        competition_worker,
        "produce_domain_result",
        lambda value: {**expected, "received": value},
    )
    monkeypatch.setattr(
        competition_worker,
        "review_domain_results",
        lambda value, **_kwargs: {**expected, "received": value},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "competition-worker",
            mode,
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
    )

    assert competition_worker.main() == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        **expected,
        "received": payload,
    }


def test_reviewer_cost_projection_uses_observation_and_never_v1_defaults(monkeypatch, tmp_path):
    import urllib.request

    from orgrebase.workspace.competition_run import GoldenCompetitionError, _reviewer_model_attempt_evidence
    from orgrebase.workspace.model_observations import ModelAttemptObservation

    payload = _review_input(2)
    decision = competition_worker.deterministic_review(payload).model_dump(mode="json")

    class Response:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self):
            return json.dumps(self.value).encode()

    def urlopen(request, **_kwargs):
        if request.get_method() == "GET":
            return Response(
                {"models": [{"name": "qwen2.5:3b", "digest": competition_worker.OLLAMA_MODEL_DIGEST}]}
            )
        return Response(
            {"model": "qwen2.5:3b", "message": {"content": json.dumps(decision)}, "prompt_eval_count": 17}
        )

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    result = competition_worker.review_domain_results(payload, observation_directory=tmp_path / "attempts")
    evidence = _reviewer_model_attempt_evidence(
        model_provider="ollama-local", reviewer_input=payload, reviewer_result=result
    )
    assert evidence["schema_version"] == "orgrebase.golden-model-attempt-evidence.v2"
    assert evidence["input_tokens"] == 17 and evidence["output_tokens"] is None
    assert evidence["usage"]["status"] == "PARTIAL"
    assert evidence["observation_persistence"] == "DURABLE"
    assert (
        evidence["model_attempt_observation_digest"]
        == result["model_attempt_observations"]["records"][0]["digest"]
    )
    legacy = {key: value for key, value in result.items() if key != "model_attempt_observations"}
    legacy_evidence = _reviewer_model_attempt_evidence(
        model_provider="ollama-local", reviewer_input=payload, reviewer_result=legacy
    )
    assert legacy_evidence["input_tokens"] is None and legacy_evidence["output_tokens"] is None
    assert legacy_evidence["legacy_receipt_usage_is_cost_evidence"] is False
    altered = deepcopy(result)
    record = altered["model_attempt_observations"]["records"][0]
    record.pop("digest")
    record["requested_model"] = "other-model"
    altered["model_attempt_observations"]["records"][0] = ModelAttemptObservation.model_validate(
        record
    ).model_dump(mode="json")
    with pytest.raises(GoldenCompetitionError, match="GOLDEN_REVIEWER_USAGE_BINDING_INVALID"):
        _reviewer_model_attempt_evidence(
            model_provider="ollama-local", reviewer_input=payload, reviewer_result=altered
        )
