PYTHON ?= uv run python
PYTHONPATH ?= src
OAC_ROOT ?=
OAC_QUOTE_ADAPTATION_OUTPUT ?= evidence/oac-quote-adaptation/latest
AGENTTEAMS_CHECKOUT ?= .tmp/agentteams
SEMIFINAL_CLOSURE_OUTPUT ?= evidence/semifinal-closure/latest
SEMIFINAL_GOVERNED_OUTPUT ?= evidence/semifinal-governed/latest
PUBLIC_PROCESS_OUTPUT ?= evidence/public-process/latest
SKILL_ROLLBACK_OUTPUT ?= evidence/skill-predecessor-rollback/latest
SEMIFINAL_MVP_OUTPUT ?= evidence/semifinal-mvp/latest
ENTERPRISE_PILOT_PACK ?= examples/enterprise-quote-pilot/evergreen
ENTERPRISE_PILOT_WORK_DIR ?=
# The snapshot builder deliberately rejects outputs inside either included
# source tree.  Keep the default beside the repositories so the documented
# one-command path is valid without weakening that fail-closed rule.
SOURCE_SNAPSHOT_OUTPUT ?= ../goai-source-snapshot
GOAI_REPO_DEMO_MEDIA = $(firstword $(wildcard submission/demo.mp4 submission/demo.mov submission/demo.webm))
PYRUN = PYTHONPATH=$(PYTHONPATH) $(PYTHON)
PYTEST = $(PYRUN) -m pytest
CORE_TESTS = tests/test_architecture_boundaries.py tests/test_impact.py \
	tests/test_workflow_and_store.py tests/test_commit_gateway.py tests/test_skills.py \
	tests/workspace/test_pricing.py tests/test_core_check.py
OAC_ROOT_ARG = $(if $(strip $(OAC_ROOT)),--oac-root "$(OAC_ROOT)",)

.PHONY: help setup test check check-core release-hygiene demo evidence-verify serve serve-demo vertex-probe agentteams-preflight build \
	goai-agentteams-demo goai-agentteams-verify goai-agentteams-check goai-semifinal-check \
	goai-source-snapshot goai-source-snapshot-verify \
	workspace-oac-admission-demo workspace-oac-admission-verify workspace-oac-admission-check \
	workspace-oac-evolution-demo workspace-oac-evolution-verify \
	workspace-oac-evolution-local-check workspace-oac-evolution-wheel-check \
	workspace-oac-evolution-check oac-quote-adaptation-run \
	oac-quote-adaptation-verify oac-quote-adaptation-check \
	semifinal-closure-run semifinal-closure-verify \
	semifinal-closure-mutation-check semifinal-closure-check quote-value-run \
	quote-value-verify quote-value-check public-process-bridge-run \
	public-process-bridge-verify public-process-bridge-check \
	skill-predecessor-rollback-run skill-predecessor-rollback-verify \
	skill-predecessor-rollback-check semifinal-governed-run \
	semifinal-governed-verify semifinal-governed-mutation-check \
	semifinal-governed-check semifinal-mvp-build semifinal-mvp-verify \
	semifinal-mvp-check semifinal-demo enterprise-pilot-preflight \
	enterprise-pilot-check enterprise-pilot-start enterprise-pilot-init \
	enterprise-pilot-seal

help:
	@printf '%s\n' 'Core contributor checks without OAC or service credentials: make check-core'
	@printf '%s\n' 'Full validation with the admitted OAC checkout and PostgreSQL tools: make check'
	@printf '%s\n' 'Deployable single-enterprise Quote Pilot: make enterprise-pilot-check'
	@printf '%s\n' 'Interactive localhost Pilot: make enterprise-pilot-start'
	@printf '%s\n' 'Final governed semifinal MVP: make semifinal-mvp-check'
	@printf '%s\n' 'Clickable evaluator demo: ./run-semifinal-demo.sh interactive'
	@printf '%s\n' 'Semifinal controlled-local closure: make semifinal-closure-check'
	@printf '%s\n' 'Canonical product journey: make workspace-oac-evolution-check'
	@printf '%s\n' 'Enterprise adaptation proof: make oac-quote-adaptation-check OAC_ROOT=/path/to/oac-spec'
	@printf '%s\n' 'OrgRebase preliminary regression: make workspace-demo | make workspace-check'
	@printf '%s\n' 'OAC admission requires the final two-directory snapshot: orgrebase/ + sibling oac-spec/'
	@printf '%s\n' 'One command in that snapshot: make workspace-oac-admission-demo workspace-oac-admission-verify'
	@printf '%s\n' 'Override discovery: make workspace-oac-admission-check OAC_ROOT=/path/to/oac-spec'
	@printf '%s\n' 'Equivalent CLI override: --oac-root PATH or ORGREBASE_OAC_ROOT=/path/to/oac-spec'
	@printf '%s\n' 'Source archive: make goai-source-snapshot OAC_ROOT=/path/to/oac-spec SOURCE_SNAPSHOT_OUTPUT=/path/outside/both/repos'
	@printf '%s\n' 'Assembled developer checkout only: make goai-semifinal-check (requires repo-local submission media)'

setup:
	uv sync --all-extras

release-hygiene:
	$(PYRUN) scripts/verify_review_readiness.py --hygiene-only

test: release-hygiene
	$(PYTEST) -W error --cov=orgrebase --cov-report=term-missing

check: release-hygiene
	$(PYRUN) -m ruff check src tests scripts
	$(PYRUN) scripts/validate_assets.py
	$(PYRUN) scripts/export_contract_schemas.py --check
	$(PYRUN) scripts/verify_evidence_manifest.py evidence/latest/manifest.json
	$(PYRUN) scripts/verify_proof_pack.py evidence/latest/proof-pack.json
	$(PYRUN) scripts/generate_release_facts.py --check
	$(PYRUN) scripts/fetch_pinned_agentteams.py
	ORGREBASE_REQUIRE_POSTGRES_TESTS=1 $(PYTEST) -W error --cov=orgrebase --cov-report=term-missing

# A bounded contributor gate, not a substitute for the complete OAC/PostgreSQL suite.
check-core: release-hygiene
	$(PYRUN) -m ruff check src tests scripts
	$(PYRUN) scripts/validate_assets.py
	$(PYRUN) scripts/export_contract_schemas.py --check
	$(PYRUN) scripts/verify_evidence_manifest.py evidence/latest/manifest.json
	$(PYRUN) scripts/verify_proof_pack.py evidence/latest/proof-pack.json
	$(PYRUN) scripts/verify_packaged_runtime_assets.py
	$(PYTEST) -W error $(CORE_TESTS)

demo:
	uv run orgrebase demo --output evidence/latest/demo.json


evidence-verify:
	uv run python scripts/verify_evidence_manifest.py evidence/latest/manifest.json
	uv run python scripts/verify_proof_pack.py evidence/latest/proof-pack.json

serve:
	uv run orgrebase serve --host 127.0.0.1 --port 8000

serve-demo:
	uv run orgrebase serve --local-demo --host 127.0.0.1 --port 8000

agentteams-preflight:
	uv run python scripts/agentteams_preflight.py --output evidence/agentteams/preflight.json

vertex-probe:
	uv run python scripts/probe_vertex_openai.py --output evidence/agentteams/vertex-provider-probe.json

goai-agentteams-demo:
	./run-agentteams-demo.sh

goai-agentteams-verify:
	./verify-agentteams-demo.sh

goai-agentteams-check: goai-agentteams-demo goai-agentteams-verify

semifinal-closure-run:
	$(PYRUN) scripts/run_semifinal_closure.py \
		--output-dir "$(SEMIFINAL_CLOSURE_OUTPUT)" \
		--checkout "$(AGENTTEAMS_CHECKOUT)"

semifinal-closure-verify:
	$(PYRUN) scripts/verify_semifinal_closure.py \
		--evidence "$(SEMIFINAL_CLOSURE_OUTPUT)" \
		--checkout "$(AGENTTEAMS_CHECKOUT)"

semifinal-closure-mutation-check:
	$(PYRUN) scripts/verify_semifinal_mutations.py \
		--evidence "$(SEMIFINAL_CLOSURE_OUTPUT)" \
		--checkout "$(AGENTTEAMS_CHECKOUT)"

semifinal-closure-check: semifinal-closure-run semifinal-closure-verify semifinal-closure-mutation-check

public-process-bridge-run:
	$(PYRUN) scripts/run_public_process_bridge.py \
		--output "$(PUBLIC_PROCESS_OUTPUT)/public-process-bridge-receipt.json"

public-process-bridge-verify:
	$(PYRUN) scripts/verify_public_process_bridge.py \
		--receipt "$(PUBLIC_PROCESS_OUTPUT)/public-process-bridge-receipt.json"

public-process-bridge-check: public-process-bridge-run public-process-bridge-verify

skill-predecessor-rollback-run:
	$(PYRUN) scripts/run_skill_predecessor_rollback.py \
		--run-id "run:orgrebase:semifinal-closure:quote-001" \
		--semifinal-root "$(SEMIFINAL_CLOSURE_OUTPUT)" \
		--output "$(SKILL_ROLLBACK_OUTPUT)"

skill-predecessor-rollback-verify:
	$(PYRUN) scripts/verify_skill_predecessor_rollback.py \
		"$(SKILL_ROLLBACK_OUTPUT)" \
		--semifinal-root "$(SEMIFINAL_CLOSURE_OUTPUT)"

skill-predecessor-rollback-check: skill-predecessor-rollback-run skill-predecessor-rollback-verify

semifinal-governed-run:
	$(PYRUN) scripts/run_governed_semifinal_apply.py \
		--output-dir "$(SEMIFINAL_GOVERNED_OUTPUT)" \
		--parent-pack "$(SEMIFINAL_CLOSURE_OUTPUT)"

semifinal-governed-verify:
	$(PYRUN) scripts/verify_governed_semifinal_apply.py \
		--evidence "$(SEMIFINAL_GOVERNED_OUTPUT)" \
		--parent-pack "$(SEMIFINAL_CLOSURE_OUTPUT)"

semifinal-governed-mutation-check:
	$(PYRUN) scripts/verify_governed_semifinal_mutations.py \
		--evidence "$(SEMIFINAL_GOVERNED_OUTPUT)" \
		--parent-pack "$(SEMIFINAL_CLOSURE_OUTPUT)"

semifinal-governed-check: semifinal-governed-run semifinal-governed-verify \
	semifinal-governed-mutation-check

semifinal-mvp-build:
	$(PYRUN) scripts/build_semifinal_mvp_manifest.py \
		--output "$(SEMIFINAL_MVP_OUTPUT)/summary.json" \
		--closure-root "$(SEMIFINAL_CLOSURE_OUTPUT)" \
		--governed-root "$(SEMIFINAL_GOVERNED_OUTPUT)" \
		--public-receipt "$(PUBLIC_PROCESS_OUTPUT)/public-process-bridge-receipt.json" \
		--rollback-root "$(SKILL_ROLLBACK_OUTPUT)"

semifinal-mvp-verify:
	$(PYRUN) scripts/verify_semifinal_mvp_manifest.py \
		"$(SEMIFINAL_MVP_OUTPUT)" \
		--closure-root "$(SEMIFINAL_CLOSURE_OUTPUT)" \
		--governed-root "$(SEMIFINAL_GOVERNED_OUTPUT)" \
		--public-receipt "$(PUBLIC_PROCESS_OUTPUT)/public-process-bridge-receipt.json" \
		--rollback-root "$(SKILL_ROLLBACK_OUTPUT)"

# The evaluator-facing entrypoint keeps one causal order: refresh the
# candidate-only parent, prove the public mechanism lane and executable Skill
# rollback, then perform governed writes under that exact parent run id.
semifinal-mvp-check: semifinal-closure-check public-process-bridge-check \
	skill-predecessor-rollback-check semifinal-governed-check \
	semifinal-mvp-build semifinal-mvp-verify

semifinal-demo:
	./run-semifinal-demo.sh interactive

enterprise-pilot-preflight:
	./run-enterprise-pilot.sh preflight --pack "$(ENTERPRISE_PILOT_PACK)" $(if $(strip $(ENTERPRISE_PILOT_WORK_DIR)),--work-dir "$(ENTERPRISE_PILOT_WORK_DIR)",)

enterprise-pilot-check:
	./run-enterprise-pilot.sh check --pack "$(ENTERPRISE_PILOT_PACK)" $(if $(strip $(ENTERPRISE_PILOT_WORK_DIR)),--work-dir "$(ENTERPRISE_PILOT_WORK_DIR)",)

enterprise-pilot-start:
	./run-enterprise-pilot.sh start --pack "$(ENTERPRISE_PILOT_PACK)" $(if $(strip $(ENTERPRISE_PILOT_WORK_DIR)),--work-dir "$(ENTERPRISE_PILOT_WORK_DIR)",)

enterprise-pilot-init:
	@test -n "$(strip $(ENTERPRISE_PILOT_OUTPUT))" || { printf '%s\n' 'ENTERPRISE_PILOT_OUTPUT is required'; exit 2; }
	./run-enterprise-pilot.sh init --output "$(ENTERPRISE_PILOT_OUTPUT)"

enterprise-pilot-seal:
	@test -n "$(strip $(ENTERPRISE_PILOT_DRAFT))" || { printf '%s\n' 'ENTERPRISE_PILOT_DRAFT is required'; exit 2; }
	@test -n "$(strip $(ENTERPRISE_PILOT_OUTPUT))" || { printf '%s\n' 'ENTERPRISE_PILOT_OUTPUT is required'; exit 2; }
	./run-enterprise-pilot.sh seal \
		--draft "$(ENTERPRISE_PILOT_DRAFT)" \
		--output "$(ENTERPRISE_PILOT_OUTPUT)"

quote-value-run:
	$(PYRUN) scripts/run_quote_value_benchmark.py \
		--output-dir evidence/quote-value/latest

quote-value-verify:
	$(PYRUN) scripts/verify_quote_value_evidence.py \
		--receipt evidence/quote-value/latest/quote-value-receipt.json \
		--project-root .

quote-value-check: quote-value-run quote-value-verify

goai-source-snapshot:
	@test -n "$(strip $(OAC_ROOT))" || { printf '%s\n' 'OAC_ROOT is required'; exit 2; }
	$(PYRUN) scripts/build_source_snapshot.py build --orgrebase-root . --oac-root "$(OAC_ROOT)" --output-dir "$(SOURCE_SNAPSHOT_OUTPUT)"

goai-source-snapshot-verify:
	@test -n "$(strip $(OAC_ROOT))" || { printf '%s\n' 'OAC_ROOT is required'; exit 2; }
	$(PYRUN) scripts/build_source_snapshot.py verify --orgrebase-root . --oac-root "$(OAC_ROOT)" --output-dir "$(SOURCE_SNAPSHOT_OUTPUT)"

# Developer-only aggregate gate for an assembled checkout that contains the
# repo-local submission media. It is not a source-snapshot verification entry:
# source snapshots deliberately exclude submission/, while the formal outer
# package is verified by its own verify-submission.py.
goai-semifinal-check:
	@test -n "$(GOAI_REPO_DEMO_MEDIA)" || { \
		printf '%s\n' 'goai-semifinal-check requires repo-local submission/demo.{mp4,mov,webm} in an assembled developer checkout.'; \
		printf '%s\n' 'For this source snapshot, run: make semifinal-mvp-check'; \
		printf '%s\n' 'For the formal PPT/PDF/code/video package, run verify-submission.py --deep from the outer submission root.'; \
		exit 2; \
	}
	$(PYRUN) -m orgrebase.cli workspace-demo --output-dir evidence/workspace/latest
	$(PYRUN) scripts/workspace_evidence.py --verify evidence/workspace/latest
	# The first Evolution check makes the command self-seeding when retained
	# wheel evidence is absent or stale. A final rebuild below binds fixed facts.
	$(MAKE) workspace-oac-evolution-check OAC_ROOT="$(OAC_ROOT)"
	./verify-agentteams-demo.sh
	# release-facts.json is intentionally packaged in the wheel. Two passes make
	# ProductPath converge from a stale checked-in fact surface. QuoteValue binds
	# the exact ProductPath observation, so each pass closes Spec 045 before facts.
	$(PYRUN) scripts/workspace_product_path_blackbox.py --output evidence/workspace/latest/product-path-blackbox.json
	$(MAKE) semifinal-closure-check
	$(PYRUN) scripts/generate_release_facts.py
	$(PYRUN) scripts/workspace_product_path_blackbox.py --output evidence/workspace/latest/product-path-blackbox.json
	$(MAKE) semifinal-closure-check
	$(PYRUN) scripts/generate_release_facts.py
	$(PYRUN) scripts/generate_release_facts.py --check
	# Rebuild both retained Evolution wheels from the converged fact bytes, then
	# prove that the wheel report did not change the non-self-referential facts.
	$(MAKE) workspace-oac-evolution-wheel-check OAC_ROOT="$(OAC_ROOT)"
	$(PYRUN) scripts/generate_release_facts.py --check
	# Spec 041 owns a separate public QuoteValue coordinate. Refresh it only
	# after ProductPath/release-facts reach their fixed point so the aggregate
	# gate cannot accept a current Spec 045 child beside a stale top-level copy.
	$(MAKE) quote-value-check
	# The Spec 045 parent pack is the final candidate-only publication boundary.
	# It must bind the converged source facts and survive semantic mutations
	# before the aggregate GOAI gate is allowed to return GO.
	$(MAKE) semifinal-closure-check
	$(PYRUN) scripts/goai_gate.py --stage semifinal --require-go
	# The evaluator-facing manifest is the final claim ceiling: all validated
	# rows and every explicit NOT_RUN blocker must still bind to fresh sources.
	$(MAKE) semifinal-mvp-check


build:
	uv build

.PHONY: workspace-contract-check workspace-benchmark-check workspace-formation-check \
	workspace-rebase-check workspace-repeatability-check workspace-evidence-check \
	workspace-model-check workspace-skill-candidate-check workspace-skill-evaluation-check \
	workspace-skill-check workspace-user-validation-check workspace-review-readiness-check workspace-agentteams-static-check \
	workspace-agentteams-conformance-check workspace-agentteams-check workspace-package-check \
	workspace-product-path-blackbox-check workspace-check workspace-release-check workspace-demo \
	workspace-clean build-offline

workspace-contract-check:
	$(PYRUN) scripts/export_contract_schemas.py --check
	$(PYTEST) -q tests/workspace/test_contracts_and_store.py

workspace-benchmark-check:
	$(PYRUN) scripts/workspace_evaluate.py --output evidence/workspace/latest/evaluation-suite.json
	$(PYTEST) -q tests/workspace/test_benchmark.py

workspace-formation-check:
	$(PYTEST) -q tests/workspace/test_formation.py tests/workspace/test_planner_and_governance.py

workspace-rebase-check:
	$(PYTEST) -q tests/workspace/test_rebase.py tests/workspace/test_failures.py tests/workspace/test_rebase_repeatability.py

workspace-repeatability-check:
	$(PYTEST) -q tests/workspace/test_repeatability.py tests/workspace/test_rebase_repeatability.py

workspace-evidence-check:
	$(PYRUN) scripts/workspace_evidence.py evidence/workspace/latest
	$(PYRUN) scripts/workspace_evidence.py --verify evidence/workspace/latest
	$(PYRUN) scripts/verify_review_readiness.py --output evidence/workspace/latest/review-readiness.json
	$(PYTEST) -q tests/workspace/test_evidence.py tests/workspace/test_evidence_user_api.py

workspace-model-check:
	$(PYTEST) -q tests/workspace/test_model_provider.py tests/workspace/test_model_and_transport.py

workspace-skill-candidate-check:
	$(PYTEST) -q tests/workspace/test_skill_foundry.py -k 'candidate or mutated'

workspace-skill-evaluation-check:
	$(PYRUN) scripts/workspace_skill_check.py --output evidence/workspace/latest/skill-foundry.json
	$(PYTEST) -q tests/workspace/test_skill_foundry.py

workspace-skill-check: workspace-skill-candidate-check workspace-skill-evaluation-check

workspace-user-validation-check:
	$(PYRUN) scripts/workspace_user_validation.py --output evidence/workspace/latest/user-validation.json
	$(PYTEST) -q tests/workspace/test_user_validation.py tests/workspace/test_evidence_user_api.py -k user_validation

workspace-review-readiness-check:
	$(PYRUN) scripts/verify_review_readiness.py --output evidence/workspace/latest/review-readiness.json
	$(PYTEST) -q tests/workspace/test_review_readiness.py

workspace-agentteams-static-check:
	$(PYRUN) scripts/workspace_agentteams_check.py --output evidence/workspace/latest/agentteams-check.json
	$(PYRUN) scripts/collect_workspace_agentteams_evidence.py --output evidence/workspace/agentteams/live-evidence.json

workspace-agentteams-conformance-check:
	$(PYTEST) -q tests/workspace/test_transport.py tests/workspace/test_model_and_transport.py tests/workspace/test_agentteams_live_evidence.py -k "transport or live_evidence"

workspace-agentteams-check: workspace-agentteams-static-check workspace-agentteams-conformance-check
	$(PYRUN) scripts/workspace_agentteams_check.py --output evidence/workspace/latest/agentteams-check.json $(if $(filter 1,$(REQUIRE_LIVE)),--require-live,)

workspace-package-check:
	$(PYRUN) scripts/verify_packaged_runtime_assets.py

workspace-product-path-blackbox-check:
	# Fixed point: each ProductPath observation refresh is followed by the Spec
	# 045 parent closure because QuoteValue content-addresses that observation.
	# Pass two then retains the wheel built from the converged fact bytes.
	$(PYRUN) scripts/workspace_product_path_blackbox.py --output evidence/workspace/latest/product-path-blackbox.json
	$(MAKE) semifinal-closure-check
	$(PYRUN) scripts/generate_release_facts.py
	$(PYRUN) scripts/workspace_product_path_blackbox.py --output evidence/workspace/latest/product-path-blackbox.json
	$(MAKE) semifinal-closure-check
	$(PYRUN) scripts/generate_release_facts.py
	$(MAKE) quote-value-check
	$(PYRUN) scripts/goai_gate.py --verify-product-path-only

workspace-demo:
	$(PYRUN) -m orgrebase.cli workspace-demo --output-dir evidence/workspace/latest

workspace-oac-admission-demo:
	$(PYRUN) -m orgrebase.cli workspace-oac-admission-demo --output-dir evidence/oac-bridge/latest $(OAC_ROOT_ARG)

workspace-oac-admission-verify:
	$(PYRUN) -m orgrebase.cli workspace-oac-admission-demo --output-dir evidence/oac-bridge/latest --verify

workspace-oac-admission-check: workspace-oac-admission-demo workspace-oac-admission-verify
	$(PYTEST) -q tests/workspace/test_oac_bridge.py tests/test_architecture_boundaries.py

workspace-oac-evolution-demo:
	$(PYRUN) -m orgrebase.cli workspace-oac-evolution-demo \
		--output-dir evidence/oac-evolution/latest $(OAC_ROOT_ARG)

workspace-oac-evolution-verify:
	$(PYRUN) scripts/verify_oac_evolution_evidence.py evidence/oac-evolution/latest

workspace-oac-evolution-wheel-check:
	$(PYRUN) scripts/run_oac_evolution_wheel_check.py --output-root evidence/oac-evolution/wheel-check $(if $(strip $(OAC_ROOT)),--oac-root "$(OAC_ROOT)",)

workspace-oac-evolution-local-check: workspace-oac-evolution-demo workspace-oac-evolution-verify
	$(PYTEST) -q \
		tests/workspace/test_oac_evolution_demo.py \
		tests/workspace/test_oac_evolution_evaluator.py \
		tests/workspace/test_oac_evolution_wire.py \
		tests/workspace/test_oac_governed_evolution.py \
		tests/workspace/test_oac_governance_lineage_attacks.py \
		tests/workspace/test_oac_governance_resource_semantics.py \
		tests/workspace/test_oac_governance_successors.py \
		tests/test_architecture_boundaries.py

workspace-oac-evolution-check: workspace-oac-evolution-local-check
	$(MAKE) workspace-oac-evolution-wheel-check OAC_ROOT="$(OAC_ROOT)"

oac-quote-adaptation-run:
	$(PYRUN) scripts/run_oac_quote_adaptation.py \
		--output-root "$(OAC_QUOTE_ADAPTATION_OUTPUT)" $(OAC_ROOT_ARG)

oac-quote-adaptation-verify:
	$(PYRUN) scripts/verify_oac_quote_adaptation.py \
		--root "$(OAC_QUOTE_ADAPTATION_OUTPUT)" $(OAC_ROOT_ARG)

oac-quote-adaptation-check: oac-quote-adaptation-run oac-quote-adaptation-verify
	$(PYTEST) -q \
		tests/workspace/test_oac_quote_adaptation.py \
		tests/workspace/test_oac_quote_parity.py \
		tests/workspace/test_oac_quote_adaptation_evidence.py

workspace-check:
	$(PYRUN) scripts/export_contract_schemas.py --check
	$(PYRUN) scripts/verify_packaged_runtime_assets.py
	$(PYTEST) -q tests/workspace
	$(PYRUN) scripts/workspace_evaluate.py --output evidence/workspace/latest/evaluation-suite.json
	$(PYRUN) scripts/workspace_skill_check.py --output evidence/workspace/latest/skill-foundry.json
	$(PYRUN) scripts/workspace_user_validation.py --output evidence/workspace/latest/user-validation.json
	$(PYRUN) scripts/workspace_agentteams_check.py --output evidence/workspace/latest/agentteams-check.json
	$(PYRUN) scripts/collect_workspace_agentteams_evidence.py --output evidence/workspace/agentteams/live-evidence.json
	$(PYRUN) scripts/workspace_evidence.py evidence/workspace/latest
	$(PYRUN) scripts/workspace_evidence.py --verify evidence/workspace/latest
	$(PYRUN) scripts/verify_review_readiness.py --output evidence/workspace/latest/review-readiness.json

build-offline:
	$(PYRUN) scripts/build_offline_release.py

workspace-release-check: workspace-check
	$(PYRUN) scripts/validate_assets.py
	$(PYRUN) scripts/verify_evidence_manifest.py evidence/latest/manifest.json
	$(PYRUN) scripts/verify_proof_pack.py evidence/latest/proof-pack.json
	$(PYRUN) scripts/generate_release_facts.py --check
	$(PYRUN) -m compileall -q src scripts tests
	$(PYTEST) -W error --cov=orgrebase --cov-report=term-missing
	$(PYRUN) scripts/build_offline_release.py

workspace-clean:
	@printf '%s\n' 'workspace-clean is intentionally disabled: evidence and caches must be moved to a dated recovery archive, never deleted implicitly.'
	@printf '%s\n' 'Create a scoped archive with checksums and restore instructions before removing any current path.'
