"""An actual author identity must own the exact unreviewed Finance bytes."""

from __future__ import annotations

import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from orgrebase.auth import AuthenticationError, Principal, request_principal
from orgrebase.clock import FrozenClock
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.private_records import PrivateRecordStore
from orgrebase.store import StateStore
from orgrebase.workspace.finance_skill_candidates import (
    CANDIDATE_MEDIA,
    FinanceSkillCandidateService,
)
from orgrebase.workspace.skill_evolution_v2 import FinanceSkillHeadService

TENANT = "tenant:finance-candidate"


@contextmanager
def _as(actor_id: str, role: str):
    token = request_principal.set(Principal(
        issuer="https://issuer.example.test", subject=actor_id,
        tenant_id=TENANT, actor_id=actor_id, roles=frozenset({role}),
        expires_at=int(time.time()) + 120,
    ))
    try:
        yield
    finally:
        request_principal.reset(token)


def test_candidate_proposal_binds_real_author_exact_private_bytes_and_event(tmp_path):
    with StateStore(tmp_path / "candidate.sqlite", tenant_id=TENANT) as store:
        head = FinanceSkillHeadService(store)
        with _as("actor:governor", "governor"):
            parent = head.bootstrap()
        workspace = SimpleNamespace(
            store=store, profile=SimpleNamespace(organization_id=TENANT),
            clock=FrozenClock("2026-09-28T00:00:00Z"), private_retention_seconds=86_400,
        )
        service = FinanceSkillCandidateService(workspace, head)
        instruction = parent.bundle.instruction_text + "Verify current Finance evidence.\n"
        expected = head.prepare_instruction_patch(
            instruction, expected_head_ref=parent.head_ref,
            expected_head_digest=parent.head_digest,
            expected_generation=parent.generation,
            expected_package_digest=parent.package_digest,
        )
        with _as("actor:evaluator", "governor"), pytest.raises(
            Exception, match="AUTH_ACTION_DENIED",
        ):
            service.propose_instruction(operation_id="candidate:one", instruction_text=instruction)
        with _as("actor:author", "operator"):
            ref = service.propose_instruction(
                operation_id="candidate:one", instruction_text=instruction,
            )
            assert service.propose_instruction(
                operation_id="candidate:one", instruction_text=instruction,
            ) == ref
            with pytest.raises(IntegrityError, match="FINANCE_CANDIDATE_OPERATION_CONFLICT"):
                service.propose_instruction(
                    operation_id="candidate:one",
                    instruction_text=instruction + "Different bytes.\n",
                )
        with _as("actor:evaluator", "governor"):
            proof = service.verify_for_qualification(ref, candidate=expected)
            assert proof["author_actor_id"] == "actor:author"
            assert proof["candidate_bundle_digest"] == expected.digest
            wrong = head.prepare_instruction_patch(
                instruction + "Different bytes.\n",
                expected_head_ref=parent.head_ref,
                expected_head_digest=parent.head_digest,
                expected_generation=parent.generation,
                expected_package_digest=parent.package_digest,
            )
            with pytest.raises(IntegrityError, match="FINANCE_CANDIDATE_PROPOSAL_INVALID"):
                service.verify_for_qualification(ref, candidate=wrong)
        late_instruction = instruction + "This appeared after a model intent.\n"
        late_bundle = head.prepare_instruction_patch(
            late_instruction, expected_head_ref=parent.head_ref,
            expected_head_digest=parent.head_digest,
            expected_generation=parent.generation,
            expected_package_digest=parent.package_digest,
        )
        with store.transaction() as connection:
            store.save_artifact(
                connection, "finance-evaluation-intent:prior-exposure",
                "application/vnd.orgrebase.finance-evaluation-intent.v1+json",
                {"candidate_bundle_digest": late_bundle.digest},
            )
            store.append_event(connection, "FINANCE_EVALUATION_INTENT_RESERVED", {
                "intent_ref": "finance-evaluation-intent:prior-exposure",
            })
        with _as("actor:author", "operator"):
            late_ref = service.propose_instruction(
                operation_id="candidate:late", instruction_text=late_instruction,
            )
        with _as("actor:evaluator", "governor"), pytest.raises(
            IntegrityError, match="FINANCE_CANDIDATE_PROPOSAL_AFTER_EXPOSURE",
        ):
            service.verify_for_qualification(late_ref, candidate=late_bundle)
        private_ref = "finance-skill-candidate-private:" + ref.rsplit(":", 1)[1]
        assert PrivateRecordStore(store, workspace.clock).erase(
            private_ref, actor_id="actor:author",
        )
        with _as("actor:evaluator", "governor"), pytest.raises(
            IntegrityError, match="FINANCE_CANDIDATE_PRIVATE_CONTENT_UNAVAILABLE",
        ):
            service.verify_for_qualification(ref, candidate=expected)
        assert store.verify_event_chain()["status"] == "PASS"


def test_candidate_cannot_be_authored_by_governor_or_reattributed_by_operation(tmp_path):
    with StateStore(tmp_path / "authors.sqlite", tenant_id=TENANT) as store:
        head = FinanceSkillHeadService(store)
        with _as("actor:governor", "governor"):
            parent = head.bootstrap()
        workspace = SimpleNamespace(
            store=store, profile=SimpleNamespace(organization_id=TENANT),
            clock=FrozenClock("2026-09-28T00:00:00Z"), private_retention_seconds=86_400,
        )
        service = FinanceSkillCandidateService(workspace, head)
        instruction = parent.bundle.instruction_text + "Check exact approved Finance sources.\n"

        with pytest.raises(AuthenticationError, match="FINANCE_CANDIDATE_PRINCIPAL_REQUIRED"):
            service.propose_instruction(operation_id="authorship", instruction_text=instruction)
        with _as("actor:governor", "operator"), pytest.raises(
            AuthorizationError, match="FINANCE_CANDIDATE_GOVERNOR_CANNOT_AUTHOR",
        ):
            service.propose_instruction(operation_id="authorship", instruction_text=instruction)

        with _as("actor:author", "operator"):
            ref = service.propose_instruction(
                operation_id="authorship", instruction_text=instruction,
            )
        body = store.load_artifact(ref, CANDIDATE_MEDIA).payload
        assert body["author_actor_id"] == "actor:author"
        assert instruction not in str(body)
        assert body["content_status"] == "PRIVATE_NOT_RELEASED"
        with _as("actor:second-author", "operator"), pytest.raises(
            IntegrityError, match="FINANCE_CANDIDATE_OPERATION_CONFLICT",
        ):
            service.propose_instruction(operation_id="authorship", instruction_text=instruction)
        with _as("actor:author", "operator"), pytest.raises(
            AuthenticationError, match="AUTH_ACTION_DENIED",
        ):
            service.verify_for_qualification(
                ref, candidate=head.prepare_instruction_patch(
                    instruction,
                    expected_head_ref=parent.head_ref,
                    expected_head_digest=parent.head_digest,
                    expected_generation=parent.generation,
                    expected_package_digest=parent.package_digest,
                ),
            )
        assert head.resolve().generation == 0


@pytest.mark.parametrize("exposure", ["missing-ref", "missing-artifact"])
def test_candidate_review_fails_closed_on_unresolved_exposure_history(tmp_path, exposure):
    with StateStore(tmp_path / f"{exposure}.sqlite", tenant_id=TENANT) as store:
        head = FinanceSkillHeadService(store)
        with _as("actor:governor", "governor"):
            parent = head.bootstrap()
        workspace = SimpleNamespace(
            store=store, profile=SimpleNamespace(organization_id=TENANT),
            clock=FrozenClock("2026-09-28T00:00:00Z"), private_retention_seconds=86_400,
        )
        service = FinanceSkillCandidateService(workspace, head)
        instruction = parent.bundle.instruction_text + "Verify source coverage.\n"
        candidate = head.prepare_instruction_patch(
            instruction,
            expected_head_ref=parent.head_ref,
            expected_head_digest=parent.head_digest,
            expected_generation=parent.generation,
            expected_package_digest=parent.package_digest,
        )
        with _as("actor:author", "operator"):
            ref = service.propose_instruction(
                operation_id="unknown-exposure", instruction_text=instruction,
            )
        with store.transaction() as connection:
            store.append_event(connection, "FINANCE_EVALUATION_INTENT_RESERVED", (
                {} if exposure == "missing-ref" else {"intent_ref": "absent:intent"}
            ))
        with _as("actor:evaluator", "governor"), pytest.raises(
            IntegrityError, match="FINANCE_CANDIDATE_EXPOSURE_COVERAGE_INCOMPLETE",
        ):
            service.verify_for_qualification(ref, candidate=candidate)
        assert head.resolve().qualification_status == "UNQUALIFIED"
