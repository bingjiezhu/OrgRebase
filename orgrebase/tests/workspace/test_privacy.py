from __future__ import annotations

import json

import pytest

from orgrebase.domain import IntegrityError
from orgrebase.workspace.benchmark import OWBBenchmarkRepository
from orgrebase.workspace.context import exclusion_reason_counts
from orgrebase.workspace.evidence import WorkspaceEvidenceExporter
from orgrebase.workspace.models import ActorContextProjection


def test_context_exclusion_summary_redacts_unknown_reason_and_source_text() -> None:
    projection = ActorContextProjection.model_construct(excluded=(
        {"object_ref": "private:contract", "reason": "OTHER_AUTHORITY_DOMAIN"},
        {"object_ref": "private:cost", "reason": "private policy explanation"},
        {"object_ref": "private:unclassified"},
    ))
    assert exclusion_reason_counts(projection) == {"OTHER": 2, "OTHER_AUTHORITY_DOMAIN": 1}


def test_benchmark_public_cases_do_not_expose_private_sources_or_gold() -> None:
    repository = OWBBenchmarkRepository()
    rendered = json.dumps(
        [item.model_dump(mode="json") for item in repository.cases()],
        ensure_ascii=False,
    )
    assert "expected_output" not in rendered
    assert "expected_error_code" not in rendered
    assert "raw_text" not in rendered
    assert "SYNTHETIC RESTRICTED SOURCE" not in rendered


def test_organization_reads_are_copy_isolated() -> None:
    repository = OWBBenchmarkRepository()
    organization_id = repository.cases()[0].organization_id
    first = repository.organization(organization_id)
    first["objects"].clear()
    second = repository.organization(organization_id)
    assert second["objects"]


def test_evidence_exporter_rejects_privacy_canary(tmp_path) -> None:
    exporter = WorkspaceEvidenceExporter(tmp_path)
    with pytest.raises(IntegrityError, match="EVIDENCE_PRIVACY_CANARY_LEAK"):
        exporter.write_json(
            relative_path="bad.json",
            value={"secret": "ORGREBASE_CANARY_SECRET_case-001"},
            evidence_class="LOCAL_DETERMINISTIC",
            claim_supported="negative privacy test",
            verifier_command="false",
        )
