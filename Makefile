PYTHON ?= python3
PYTHONPATH ?= src
PYRUN = PYTHONPATH=$(PYTHONPATH) $(PYTHON)
PYTEST = $(PYRUN) -m pytest

.PHONY: setup test check demo evidence-verify serve vertex-probe agentteams-preflight build

setup:
	uv sync --all-extras

test:
	uv run pytest -W error --cov=orgrebase --cov-report=term-missing

check:
	uv run ruff check src tests scripts
	uv run python scripts/validate_assets.py
	uv run python scripts/export_contract_schemas.py --check
	uv run python scripts/verify_evidence_manifest.py evidence/latest/manifest.json
	uv run python scripts/verify_proof_pack.py evidence/latest/proof-pack.json
	uv run pytest -W error --cov=orgrebase --cov-report=term-missing

demo:
	uv run orgrebase demo --output evidence/latest/demo.json


evidence-verify:
	uv run python scripts/verify_evidence_manifest.py evidence/latest/manifest.json
	uv run python scripts/verify_proof_pack.py evidence/latest/proof-pack.json

serve:
	uv run uvicorn orgrebase.api:app --host 127.0.0.1 --port 8000

agentteams-preflight:
	uv run python scripts/agentteams_preflight.py --output evidence/agentteams/preflight.json

vertex-probe:
	uv run python scripts/probe_vertex_openai.py --output evidence/agentteams/vertex-provider-probe.json


build:
	uv build

.PHONY: workspace-contract-check workspace-benchmark-check workspace-formation-check \
	workspace-rebase-check workspace-repeatability-check workspace-evidence-check \
	workspace-model-check workspace-skill-candidate-check workspace-skill-evaluation-check \
	workspace-skill-check workspace-user-validation-check workspace-review-readiness-check workspace-agentteams-static-check \
	workspace-agentteams-conformance-check workspace-agentteams-check workspace-package-check \
	workspace-check workspace-release-check workspace-demo workspace-clean build-offline

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

workspace-demo:
	$(PYRUN) -m orgrebase.cli workspace-demo --output-dir evidence/workspace/latest

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
	rm -rf evidence/workspace/latest .pytest_cache .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
