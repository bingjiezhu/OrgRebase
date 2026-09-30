from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from copy import deepcopy

import pytest

from orgrebase.auth import Principal, request_authorization, request_principal
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError, ObjectState, VersionedObject
from orgrebase.store import StateStore
from orgrebase.workspace import quote_pattern_bridge
from orgrebase.workspace.pattern_evolution import (
    CaseObservation,
    FeatureProfile,
    GovernedPatternService,
    ProposalBudget,
    ReplayCase,
    SkillBoundary,
    _record,
)
from orgrebase.workspace.pattern_governance import (
    PatternAdoptionPolicy,
    PatternAuthorityScope,
    PatternDecisionConfig,
    PrincipalPatternGovernance,
    load_pattern_decision_config,
)
from orgrebase.workspace.quote_recovery_learning import (
    TARGET_SKILL,
    quote_recovery_content_bundle,
    recovery_handoff_input,
)
from orgrebase.workspace.skill_packages import (
    PARTITIONS,
    InvocationContext,
    SkillContentBundle,
    SkillPackageRegistry,
)

TENANT = "org:quote-recovery"
WORKSPACE = "default"
CORPUS = "actor:corpus"
AUTHOR = "actor:skill-author"
EVALUATOR = "actor:independent-evaluator"
GOVERNOR = "actor:skill-governor"
RUNTIME = "actor:quote-operator"


def _digest(name: str) -> str:
    return sha256_digest({"receipt": name})


def _public(*, complete: bool = True, profile: str | None = None):
    value = recovery_handoff_input(
        run_id="run:quote-recovery",
        task_id="task:recovery-handoff",
        delegation_id="delegation:quote-operations",
        delegation_task_digest=_digest("delegation"),
        context_projection_digest=_digest("context"),
        request_digest=_digest("request"),
        resume_digest=_digest("resume"),
        outcome_artifact_digest=_digest("outcome") if complete else None,
        domain="product",
    )
    if profile is not None:
        value["candidate_bundle"]["profile_id"] = profile
    return value


def _observed(case_id: str, outcome: str) -> CaseObservation:
    value = _public()
    certificate = _record(
        "outcome-observation",
        case_id=case_id,
        case_revision="1",
        input_digest=sha256_digest(value),
        outcome=outcome,
        issuer=CORPUS,
        evidence_scope="CONTROLLED_LOCAL_QUOTE_RECOVERY_FIXTURE",
    )
    return CaseObservation(
        case_id=case_id,
        revision="1",
        public_input=value,
        outcome=outcome,
        certificate=certificate,
    )


def _replay(*, repair: bool = True) -> tuple[ReplayCase, ...]:
    cases = []
    for partition in PARTITIONS:
        value = _public()
        expected = "HANDOFF"
        role = "REGRESSION"
        pair = None
        if partition == "HELD_OUT":
            role = "HELD_OUT"
            value["task_id"] = "task:recovery-held-out"
            if repair:
                value = _public(complete=False)
                value["task_id"] = "task:recovery-held-out"
                expected = "ABSTAIN"
        elif partition == "NEGATIVE_TRANSFER":
            role = "COUNTERFACTUAL"
            pair = "quote:REPLAY"
            value = _public(profile="unrelated-profile-v1")
        elif partition == "PERMISSION":
            value["permission_expansion"] = True
            expected = "DENY"
        elif partition == "INJECTION":
            value["prompt_injection"] = True
            expected = "ABSTAIN"
        elif partition == "MALFORMED":
            value.pop("candidate_bundle")
            expected = "ABSTAIN"
        elif partition == "RESOURCE_OR_DEADLINE":
            value["deadline_expired"] = True
            expected = "ABSTAIN"
        elif partition == "CANARY":
            role = "PRIOR_VERSION"
        cases.append(
            ReplayCase(
                case_id=f"quote:{partition}",
                partition=partition,
                role=role,
                public_input=value,
                expected_action=expected,
                counterfactual_of=pair,
            )
        )
    return tuple(cases)


def _service(store: StateStore, *, case_resolver=None) -> GovernedPatternService:
    return GovernedPatternService(
        store,
        corpus_authority=CORPUS,
        evaluator_authority=EVALUATOR,
        governance_authority=GOVERNOR,
        prerequisite_resolver=lambda actor, ref: actor == RUNTIME,
        case_evidence_resolver=case_resolver,
    )


def _prepare(
    service: GovernedPatternService,
    *,
    repair: bool = True,
    cases: tuple[CaseObservation, ...] | None = None,
):
    corpus = service.freeze_corpus(
        FeatureProfile(
            profile_id="quote-evidence-recovery",
            revision="1",
            paths=("/candidate_bundle/domain",),
            transfer_scope="one quote evidence-recovery handoff profile",
        ),
        cases
        or tuple(
            _observed(f"quote-case:{index}", outcome)
            for index, outcome in enumerate(("SUPPORT", "SUPPORT", "COUNTEREXAMPLE"))
        ),
        actor_id=CORPUS,
    )
    replay = service.freeze_replay(_replay(repair=repair), actor_id=EVALUATOR)
    proposal = service.open_proposal(
        proposal_id=f"proposal:quote-recovery:{repair}",
        corpus_ref=corpus,
        replay_ref=replay,
        skill_name=TARGET_SKILL,
        author_id=AUTHOR,
        budget=ProposalBudget(max_cases=10, max_skill_invocations=100, max_seconds=300),
    )
    return proposal, _boundary(service)


def _boundary(service: GovernedPatternService) -> SkillBoundary:
    package = service.registry.load(TARGET_SKILL)
    return SkillBoundary(
        preconditions=("quote recovery handoff is explicitly declared",),
        required_knowledge=("knowledge:quote-recovery-v1",),
        required_qualifications=("qualification:quote-recovery-review",),
        evidence_duties=("request, resume, and outcome receipts",),
        rollback_package_digest=package.package_digest,
        known_failure_envelope=("missing receipt abstains", "permission expansion denies"),
    )


def _scope(service: GovernedPatternService) -> PatternAuthorityScope:
    bundle = quote_recovery_content_bundle(service.registry)
    predecessor = service.registry.load(TARGET_SKILL).package_digest
    return PatternAuthorityScope(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        corpus_actor_id=CORPUS,
        author_actor_id=AUTHOR,
        evaluator_actor_id=EVALUATOR,
        governor_actor_id=GOVERNOR,
        reviewed_bundle_digest=bundle.digest,
        reviewed_target_skill=TARGET_SKILL,
        reviewed_predecessor_package_digest=predecessor,
    )


@contextmanager
def _identity(
    actor: str,
    roles: frozenset[str],
    checks: list[str],
    *,
    expires_at: int = 4_000_000_000,
):
    principal = Principal(
        issuer="https://issuer.example",
        subject=f"subject:{actor}",
        tenant_id=TENANT,
        actor_id=actor,
        roles=roles,
        expires_at=expires_at,
    )

    def reauthorize() -> None:
        checks.append(actor)
        current = request_principal.get()
        if current is None or current.actor_id != actor:
            raise AuthorizationError("TEST_AUTHORIZATION_REVOKED")

    principal_token = request_principal.set(principal)
    authorization_token = request_authorization.set(reauthorize)
    try:
        yield
    finally:
        request_authorization.reset(authorization_token)
        request_principal.reset(principal_token)


def test_allowlisted_content_is_exact_and_rejects_path_or_byte_substitution():
    bundle = quote_recovery_content_bundle()
    assert SkillContentBundle.from_payload(bundle.payload).digest == bundle.digest
    changed = deepcopy(bundle.payload)
    changed["resources"][0]["path"] = "../program.py"
    changed["digest"] = sha256_digest(
        {key: value for key, value in changed.items() if key != "digest"}
    )
    with pytest.raises(IntegrityError, match="PATH_NOT_ALLOWED"):
        SkillContentBundle.from_payload(changed)
    changed = deepcopy(bundle.payload)
    changed["resources"][0]["content_base64"] = changed["resources"][1]["content_base64"]
    changed["digest"] = sha256_digest(
        {key: value for key, value in changed.items() if key != "digest"}
    )
    with pytest.raises(IntegrityError, match="BYTES_MISMATCH"):
        SkillContentBundle.from_payload(changed)


def test_steward_command_config_is_strict_private_and_disabled_by_default(tmp_path):
    registry = SkillPackageRegistry()
    reviewed = quote_recovery_content_bundle(registry)
    payload = {
        "schema_version": "orgrebase.pattern-governance-decision.v1",
        "workspace_id": WORKSPACE,
        "access_token_variable": "ORGREBASE_PATTERN_TOKEN",
        "corpus_actor_id": CORPUS,
        "author_actor_id": AUTHOR,
        "evaluator_actor_id": EVALUATOR,
        "governor_actor_id": GOVERNOR,
        "reviewed_bundle_digest": reviewed.digest,
        "reviewed_target_skill": TARGET_SKILL,
        "reviewed_predecessor_package_digest": registry.load(TARGET_SKILL).package_digest,
        "candidate_ref": "pattern-evolution:skill-candidate:test",
        "evaluation_ref": "pattern-evolution:evaluation:test",
        "verdict": "ADMIT",
        "expected_candidate_digest": _digest("candidate"),
        "expected_head_package_digest": _digest("head"),
        "idempotency_key": "quote-recovery-decision-001",
    }
    config_path = tmp_path / "decision.json"
    config_path.write_text(json.dumps(payload))
    config = load_pattern_decision_config(config_path)
    assert isinstance(config, PatternDecisionConfig)
    assert config.enabled is False
    config_path.chmod(0o666)
    with pytest.raises(ValueError, match="CONFIG_INVALID"):
        load_pattern_decision_config(config_path)
    config_path.chmod(0o600)
    link = tmp_path / "decision-link.json"
    os.symlink(config_path, link)
    with pytest.raises(ValueError, match="CONFIG_INVALID"):
        load_pattern_decision_config(link)


def test_real_workspace_recovery_receipts_enter_the_same_candidate_and_consumer(
    tmp_path, monkeypatch
):
    from tests.workspace.test_quote_pattern_bridge import workspace_case

    workspace, _ = workspace_case(tmp_path, monkeypatch)
    try:
        actual = quote_pattern_bridge.build_quote_recovery_case(
            workspace,
            "recovery-case",
            corpus_authority=CORPUS,
        )
        resolver = quote_pattern_bridge.make_quote_recovery_case_resolver(workspace)
        service = _service(workspace.store, case_resolver=resolver)
        proposal, boundary = _prepare(
            service,
            cases=(
                actual,
                _observed("quote-case:independent-support", "SUPPORT"),
                _observed("quote-case:counterexample", "COUNTEREXAMPLE"),
            ),
        )
        bundle = quote_recovery_content_bundle(service.registry)
        (candidate_ref,) = service.propose(
            proposal,
            actor_id=AUTHOR,
            boundary=boundary,
            content_bundle=bundle.payload,
        )
        candidate = service._load(candidate_ref, "skill-candidate")
        pattern = service._load(candidate["pattern_ref"], "pattern")
        assert actual.digest in pattern["case_refs"]
        assert set(resolver(actual)) < set(pattern["certificate_refs"])

        consumer_input = quote_pattern_bridge.quote_recovery_consumer_input(actual)
        overlay = service._overlay(candidate)
        package = overlay.load(TARGET_SKILL)
        invocation = overlay.invoke_for_evaluation(
            TARGET_SKILL,
            consumer_input,
            context=InvocationContext(
                consumer_input["run_id"],
                consumer_input["task_id"],
                consumer_input["delegation_id"],
                EVALUATOR,
            ),
            expected_package_digest=package.package_digest,
        )
        assert invocation.result["action"] == "HANDOFF"
        assert invocation.receipt["candidate_content"]["bundle_digest"] == bundle.digest
        evaluation = service._load(service.evaluate(candidate_ref, actor_id=EVALUATOR))
        assert evaluation["business_oracle"]["status"] == "IMPROVED"
        assert evaluation["verdict"] == "QUALIFIED"
    finally:
        workspace.store.close()


def test_independent_oracle_requires_real_behavior_improvement(tmp_path):
    with StateStore(
        tmp_path / "no-delta.sqlite3", tenant_id=TENANT, workspace_id=WORKSPACE
    ) as store:
        service = _service(store)
        proposal, boundary = _prepare(service, repair=False)
        bundle = quote_recovery_content_bundle(service.registry)
        (candidate,) = service.propose(
            proposal,
            actor_id=AUTHOR,
            boundary=boundary,
            content_bundle=bundle.payload,
        )
        evaluation_ref = service.evaluate(candidate, actor_id=EVALUATOR)
        evaluation = service._load(evaluation_ref)
        assert evaluation["business_oracle"]["status"] == "NO_BEHAVIOR_DELTA"
        assert evaluation["verdict"] == "REJECTED"
        with pytest.raises(IntegrityError, match="NOT_QUALIFIED"):
            service.decide(
                candidate,
                evaluation_ref,
                actor_id=GOVERNOR,
                verdict="ADMIT",
                expected_candidate_digest=service._load(candidate)["digest"],
                expected_head_package_digest=service._load(candidate)["base_package_digest"],
            )


def test_revocation_at_final_commit_does_not_publish_a_decision_or_source(tmp_path):
    with StateStore(
        tmp_path / "revoked-decision.sqlite3", tenant_id=TENANT, workspace_id=WORKSPACE
    ) as store:
        service = _service(store)
        proposal, boundary = _prepare(service)
        bundle = quote_recovery_content_bundle(service.registry)
        (candidate,) = service.propose(
            proposal,
            actor_id=AUTHOR,
            boundary=boundary,
            content_bundle=bundle.payload,
        )
        evaluation = service.evaluate(candidate, actor_id=EVALUATOR)
        checks = 0

        def reauthorize() -> None:
            nonlocal checks
            checks += 1
            if checks >= 3:
                raise AuthorizationError("PATTERN_TEST_AUTHORIZATION_REVOKED")

        with pytest.raises(AuthorizationError, match="AUTHORIZATION_REVOKED"):
            service.decide(
                candidate,
                evaluation,
                actor_id=GOVERNOR,
                verdict="ADMIT",
                expected_candidate_digest=service._load(candidate)["digest"],
                expected_head_package_digest=service.registry.load(TARGET_SKILL).package_digest,
                authorization_check=reauthorize,
                idempotency_key="revoked-at-commit",
            )
        assert checks == 3
        assert service._family("decision") == ()
        assert service._family("admission") == ()


def test_legacy_direct_candidate_evaluation_and_decision_are_not_production_adoptable(
    tmp_path,
):
    with StateStore(
        tmp_path / "legacy-not-adoptable.sqlite3",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    ) as store:
        checks: list[str] = []
        service = _service(store)
        proposal, boundary = _prepare(service)
        bundle = quote_recovery_content_bundle(service.registry)
        (candidate,) = service.propose(
            proposal,
            actor_id=AUTHOR,
            boundary=boundary,
            content_bundle=bundle.payload,
        )
        evaluation = service.evaluate(candidate, actor_id=EVALUATOR)
        controller = PrincipalPatternGovernance(
            service,
            _scope(service),
            adoption=PatternAdoptionPolicy(enabled=True, candidate_refs=(candidate,)),
        )
        base = service.registry.load(TARGET_SKILL).package_digest
        with (
            _identity(GOVERNOR, frozenset({"governor"}), checks),
            pytest.raises(AuthorizationError, match="PRODUCTION_CHAIN_REQUIRED"),
        ):
            controller.decide(
                candidate,
                evaluation,
                verdict="ADMIT",
                expected_candidate_digest=service._load(candidate)["digest"],
                expected_head_package_digest=base,
                idempotency_key="legacy-must-fail",
            )
        service.decide(
            candidate,
            evaluation,
            actor_id=GOVERNOR,
            verdict="ADMIT",
            expected_candidate_digest=service._load(candidate)["digest"],
            expected_head_package_digest=base,
        )
        value = _public()
        context = InvocationContext(
            value["run_id"], value["task_id"], value["delegation_id"], RUNTIME
        )
        with pytest.raises(
            AuthorizationError,
            match="PRODUCTION_INVOCATION_AUTHORITY_REQUIRED",
        ):
            service.invoke(
                candidate,
                value,
                context=context,
                knowledge_refs=("knowledge:quote-recovery-v1",),
                qualification_refs=("qualification:quote-recovery-review",),
                authorization_check=lambda: None,
            )
        with (
            _identity(RUNTIME, frozenset({"reader"}), checks),
            pytest.raises(AuthorizationError, match="PRODUCTION_CHAIN_REQUIRED"),
        ):
            controller.invoke(
                candidate,
                value,
                context=context,
                knowledge_refs=("knowledge:quote-recovery-v1",),
                qualification_refs=("qualification:quote-recovery-review",),
            )
        with _identity(RUNTIME, frozenset({"reader"}), checks):
            status = controller.status(candidate)
        assert status["production_chain_status"] == "LEGACY_OR_INCOMPLETE_NOT_ADOPTABLE"
        assert status["configured_adoption"] is True
        assert status["automatic_adoption"] is False


def test_principal_governed_content_release_restart_new_run_and_restoration(tmp_path):
    path = tmp_path / "quote-learning.sqlite3"
    checks: list[str] = []
    store = StateStore(path, tenant_id=TENANT, workspace_id=WORKSPACE)
    historical_quote = VersionedObject(
        id="quote:historical",
        version="v1",
        kind="Quote",
        label="Historical quote",
        domain="finance",
        state=ObjectState.CURRENT,
        payload={"status": "APPROVED", "amount": 12500},
    )
    external_effect = _record(
        "external-effect-history",
        effect_id="effect:historical",
        status="CONFIRMED",
    )
    with store.transaction() as connection:
        store.insert_version(connection, historical_quote, make_current=True)
        historical_effect = store.put_effect(
            connection,
            effect_id="effect:historical",
            target_key="target:historical",
            request_digest=sha256_digest({"effect": "historical"}),
            request={"operation": "PATCH", "status": "already-confirmed"},
            created_at="2026-09-25T00:00:00Z",
        )
        store.save_artifact(
            connection,
            "external-effect-history:effect:historical",
            "application/vnd.orgrebase.external-effect-history+json",
            external_effect,
        )
    service = _service(store)
    bundle = quote_recovery_content_bundle(service.registry)
    controller = PrincipalPatternGovernance(service, _scope(service))
    with _identity(CORPUS, frozenset({"governor"}), checks):
        corpus = controller.freeze_corpus(
            FeatureProfile(
                profile_id="quote-evidence-recovery",
                revision="1",
                paths=("/candidate_bundle/domain",),
                transfer_scope="one quote evidence-recovery handoff profile",
            ),
            tuple(
                _observed(f"verified-case:{index}", outcome)
                for index, outcome in enumerate(
                    ("SUPPORT", "SUPPORT", "COUNTEREXAMPLE")
                )
            ),
        )
    replay = service.freeze_replay(_replay(), actor_id=EVALUATOR)
    with _identity(AUTHOR, frozenset({"operator"}), checks):
        proposal = controller.open_proposal(
            proposal_id="proposal:verified-quote-recovery",
            corpus_ref=corpus,
            replay_ref=replay,
            skill_name=TARGET_SKILL,
            budget=ProposalBudget(
                max_cases=10, max_skill_invocations=100, max_seconds=300
            ),
        )
    boundary = _boundary(service)
    unreviewed = SkillContentBundle.create(
        target_skill=TARGET_SKILL,
        predecessor_package_digest=service.registry.load(TARGET_SKILL).package_digest,
        instruction_bytes=b"# Unreviewed instruction bytes\n",
        reference_bytes=bundle.resource_bytes(
            "references/quote-evidence-recovery.md"
        ),
        checklist_bytes=bundle.resource_bytes(
            "checklists/quote-evidence-recovery.v1.json"
        ),
    )
    with (
        _identity(AUTHOR, frozenset({"operator"}), checks),
        pytest.raises(AuthorizationError, match="REVIEWED_CONTENT_REQUIRED"),
    ):
        controller.propose(
            proposal, boundary=boundary, content_bundle=unreviewed.payload
        )
    assert service._family("skill-candidate") == ()
    with _identity(AUTHOR, frozenset({"operator"}), checks):
        (candidate,) = controller.propose(
            proposal, boundary=boundary, content_bundle=bundle.payload
        )
    with _identity(EVALUATOR, frozenset({"governor"}), checks):
        evaluation_ref = controller.evaluate(candidate)
    evaluation = service._load(evaluation_ref)
    assert evaluation["verdict"] == "QUALIFIED"
    oracle = evaluation["business_oracle"]
    assert oracle["status"] == "IMPROVED"
    assert oracle["repaired_case_count"] == 1
    assert oracle["regressed_case_count"] == 0
    assert oracle["model_invocations"] == oracle["model_cost_usd"] == 0
    comparison = service.evaluation_comparison(evaluation_ref, actor_id=GOVERNOR)
    assert comparison["behavior_delta"] == "OBSERVED_CHANGE"
    assert len(comparison["change_sources"]["changed_resources"]) == 3
    base_digest = service.registry.load(TARGET_SKILL).package_digest
    with (
        _identity(GOVERNOR, frozenset({"governor"}), checks),
        pytest.raises(AuthorizationError, match="REVIEWED_CONTENT_REQUIRED"),
    ):
        controller.decide(
            candidate,
            evaluation_ref,
            verdict="ADMIT",
            expected_candidate_digest=service._load(candidate)["digest"],
            expected_head_package_digest=_digest("stale-head"),
            idempotency_key="quote-recovery-stale-decision",
        )
    with _identity(GOVERNOR, frozenset({"governor"}), checks):
        decision_ref = controller.decide(
            candidate,
            evaluation_ref,
            verdict="ADMIT",
            expected_candidate_digest=service._load(candidate)["digest"],
            expected_head_package_digest=base_digest,
            idempotency_key="quote-recovery-decision-001",
        )
    with _identity(
        GOVERNOR,
        frozenset({"governor"}),
        checks,
        expires_at=3_999_999_999,
    ):
        assert (
            controller.decide(
                candidate,
                evaluation_ref,
                verdict="ADMIT",
                expected_candidate_digest=service._load(candidate)["digest"],
                expected_head_package_digest=base_digest,
                idempotency_key="quote-recovery-decision-001",
            )
            == decision_ref
        )
    decision = service._load(decision_ref)
    assert decision["authority_basis"] == "VERIFIED_CURRENT_PRINCIPAL"
    assert decision["human_review_verified"] is False
    assert decision["principal_binding"]["actor_id"] == GOVERNOR
    assert decision["principal_binding"]["role_phase"] == "RELEASE_GOVERNOR"
    candidate_record = service._load(candidate, "skill-candidate")
    proposal_record = service._load(candidate_record["proposal_ref"], "proposal")
    corpus_record = service._load(candidate_record["corpus_ref"], "corpus")
    assert {
        corpus_record["principal_binding"]["role_phase"],
        proposal_record["principal_binding"]["role_phase"],
        candidate_record["principal_binding"]["role_phase"],
        evaluation["principal_binding"]["role_phase"],
        decision["principal_binding"]["role_phase"],
    } == {
        "CORPUS_FREEZE",
        "PROPOSAL_OPEN",
        "CANDIDATE_AUTHOR",
        "INDEPENDENT_EVALUATOR",
        "RELEASE_GOVERNOR",
    }
    bindings = [
        corpus_record["principal_binding"],
        proposal_record["principal_binding"],
        candidate_record["principal_binding"],
        evaluation["principal_binding"],
        decision["principal_binding"],
    ]
    assert all(
        {
            "issuer",
            "subject",
            "tenant_id",
            "actor_id",
            "expires_at",
            "scope_digest",
            "role_phase",
        }
        <= set(binding)
        for binding in bindings
    )
    assert len({(item["issuer"], item["subject"]) for item in bindings}) == 4
    assert {item["scope_digest"] for item in bindings} == {_scope(service).digest}
    assert decision["production_chain_digest"]
    assert service.current_skill_head_package_digest(TARGET_SKILL) != base_digest
    store.close()

    process_value = _public()
    process_value["run_id"] = "run:subprocess-quote"
    script = """
import json
import sys
from orgrebase.store import StateStore
from orgrebase.auth import Principal, request_authorization, request_principal
from orgrebase.workspace.pattern_evolution import GovernedPatternService
from orgrebase.workspace.pattern_governance import (
    PatternAdoptionPolicy,
    PatternAuthorityScope,
    PrincipalPatternGovernance,
)
from orgrebase.workspace.quote_recovery_learning import TARGET_SKILL, quote_recovery_content_bundle
from orgrebase.workspace.skill_packages import InvocationContext

path, candidate, payload = sys.argv[1:]
value = json.loads(payload)
with StateStore(path, tenant_id='org:quote-recovery', workspace_id='default') as store:
    service = GovernedPatternService(
        store,
        corpus_authority='actor:corpus',
        evaluator_authority='actor:independent-evaluator',
        governance_authority='actor:skill-governor',
        prerequisite_resolver=lambda actor, ref: actor == 'actor:quote-operator',
    )
    bundle = quote_recovery_content_bundle(service.registry)
    controller = PrincipalPatternGovernance(
        service,
        PatternAuthorityScope(
            tenant_id='org:quote-recovery',
            workspace_id='default',
            corpus_actor_id='actor:corpus',
            author_actor_id='actor:skill-author',
            evaluator_actor_id='actor:independent-evaluator',
            governor_actor_id='actor:skill-governor',
            reviewed_bundle_digest=bundle.digest,
            reviewed_target_skill=TARGET_SKILL,
            reviewed_predecessor_package_digest=service.registry.load(TARGET_SKILL).package_digest,
        ),
        adoption=PatternAdoptionPolicy(enabled=True, candidate_refs=(candidate,)),
    )
    principal = Principal(
        'https://issuer.example', 'subject:actor:quote-operator', 'org:quote-recovery',
        'actor:quote-operator', frozenset({'reader'}), 4_000_000_000,
    )
    principal_token = request_principal.set(principal)
    authorization_token = request_authorization.set(lambda: None)
    try:
        result = controller.invoke(
            candidate,
            value,
            context=InvocationContext(
                value['run_id'], value['task_id'], value['delegation_id'], 'actor:quote-operator'
            ),
            knowledge_refs=('knowledge:quote-recovery-v1',),
            qualification_refs=('qualification:quote-recovery-review',),
        )
    finally:
        request_authorization.reset(authorization_token)
        request_principal.reset(principal_token)
    print(json.dumps(result.receipt, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(path), candidate, json.dumps(process_value)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess_receipt = json.loads(completed.stdout)
    assert subprocess_receipt["candidate_content"]["bundle_digest"] == bundle.digest

    reopened = StateStore(path, tenant_id=TENANT, workspace_id=WORKSPACE)
    resumed = _service(reopened)
    disabled = PrincipalPatternGovernance(resumed, _scope(resumed))
    value = _public()
    context = InvocationContext(
        "run:new-quote",
        value["task_id"],
        value["delegation_id"],
        RUNTIME,
    )
    value["run_id"] = context.run_id
    with _identity(RUNTIME, frozenset({"reader"}), checks):
        status = disabled.status(candidate)
    assert status["evidence_status"] == "NOT_RETRACTED"
    assert status["admission_count"] == status["decision_count"] == 1
    assert status["automatic_adoption"] is False
    assert status["production_chain_status"] == "VERIFIED"
    with (
        _identity(RUNTIME, frozenset({"reader"}), checks),
        pytest.raises(AuthorizationError, match="ADOPTION_DISABLED"),
    ):
        disabled.invoke(
            candidate,
            value,
            context=context,
            knowledge_refs=("knowledge:quote-recovery-v1",),
            qualification_refs=("qualification:quote-recovery-review",),
        )
    enabled = PrincipalPatternGovernance(
        resumed,
        _scope(resumed),
        adoption=PatternAdoptionPolicy(enabled=True, candidate_refs=(candidate,)),
    )
    with _identity(RUNTIME, frozenset({"reader"}), checks):
        invocation = enabled.invoke(
            candidate,
            value,
            context=context,
            knowledge_refs=("knowledge:quote-recovery-v1",),
            qualification_refs=("qualification:quote-recovery-review",),
        )
    trace = invocation.receipt["candidate_content"]
    assert invocation.result["action"] == "HANDOFF"
    assert trace["bundle_digest"] == bundle.digest
    assert len(trace["loaded_resource_digests"]) == 3
    assert len(trace["consumed_resource_digests"]) == 1
    historical = resumed._family("invocation-result")
    assert len(historical) == 2
    with _identity(GOVERNOR, frozenset({"governor"}), checks):
        _, restoration_ref = enabled.withdraw_and_restore_predecessor(
            candidate,
            expected_predecessor_digest=base_digest,
            reason="controlled canary regression rehearsal",
        )
    restoration = resumed._load(restoration_ref, "restoration")
    assert restoration["predecessor_package_digest"] == base_digest
    assert restoration["adoption_enabled"] is False
    admission = resumed._family("admission")[0]
    source_id, version = admission["source_ref"].rsplit("@", 1)
    assert reopened.get_object(source_id, version).state is ObjectState.REQUALIFICATION_REQUIRED
    assert resumed._family("invocation-result") == historical
    assert reopened.get_object("quote:historical", "v1").digest == historical_quote.digest
    assert reopened.get_effect("effect:historical") == historical_effect
    assert (
        reopened.load_artifact(
            "external-effect-history:effect:historical",
            "application/vnd.orgrebase.external-effect-history+json",
        ).payload
        == external_effect
    )
    with (
        _identity(RUNTIME, frozenset({"reader"}), checks),
        pytest.raises(IntegrityError, match="RESTORATION_REQUIRES_NEW_RUN"),
    ):
        enabled.invoke_restored_predecessor(
            restoration_ref,
            value,
            context=context,
        )
    restored_value = _public()
    restored_value["run_id"] = "run:restored-predecessor-new"
    restored_context = InvocationContext(
        restored_value["run_id"],
        restored_value["task_id"],
        restored_value["delegation_id"],
        RUNTIME,
    )
    with _identity(RUNTIME, frozenset({"reader"}), checks):
        restored_invocation = enabled.invoke_restored_predecessor(
            restoration_ref,
            restored_value,
            context=restored_context,
        )
    assert restored_invocation.result["action"] == "HANDOFF"
    assert (
        restored_invocation.receipt["authorization_mode"]
        == "EXACT_PREDECESSOR_RESTORATION"
    )
    assert len(resumed._family("predecessor-invocation-result")) == 1
    assert resumed._family("invocation-result") == historical
    base_result = SkillPackageRegistry().interpret_candidate(
        SkillPackageRegistry().load(TARGET_SKILL), value
    )
    assert base_result["action"] == "HANDOFF"
    with (
        _identity(RUNTIME, frozenset({"reader"}), checks),
        pytest.raises(AuthorizationError, match="PRODUCTION_RELEASE_CHAIN_INVALID"),
    ):
        enabled.invoke(
            candidate,
            value,
            context=context,
            knowledge_refs=("knowledge:quote-recovery-v1",),
            qualification_refs=("qualification:quote-recovery-review",),
        )
    reopened.close()
    assert checks.count(GOVERNOR) >= 4
