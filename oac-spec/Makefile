PYTHON ?= python3

.PHONY: setup test lint schemas-check benchmark-check ctk-successor-check plan-disagreement-reproductions-check type-check ctk-bundle-check independent-ctk go-phase-a-check go-supplier-v02-check go-plan-verifier-check supplier-capsule-check supplier-parity-check supplier-parity-exact-check supplier-disagreement-check supplier-evidence-check plan-verification-capsule-check plan-verification-parity-check plan-verification-rule-mutants-check plan-verification-evidence-check plan-verification-installed-replay evolution-evidence-check archive-replay-check runtime-lowering-check tck demo check

setup:
	uv sync --all-extras

test:
	uv run pytest --cov=oac --cov-report=term-missing

lint:
	uv run ruff check src tests scripts tck ctk implementations/python-plan-verifier-reference

type-check:
	uv run mypy

ctk-successor-check:
	uv run python ctk/build_successor.py --check
	uv run python scripts/check_ctk_successor.py $(CTK_SUCCESSOR_ARGS)

plan-disagreement-reproductions-check:
	uv run python scripts/minimize_plan_verification_disagreements.py --check

schemas-check:
	uv run python scripts/export_schemas.py --check

benchmark-check:
	uv run python scripts/run_benchmark.py --check

ctk-bundle-check:
	uv run python ctk/build_bundle.py --check

independent-ctk:
	uv run --project ctk/runner oac-ctk --bundle ctk/bundles/phase-a-v0.1 --adapter-command-json '["$(CURDIR)/.venv/bin/python","-m","oac.ctk_adapter"]' >/dev/null

go-phase-a-check:
	uv run python scripts/check_go_phase_a.py

go-supplier-v02-check:
	go -C implementations/go-supplier-v02-internal test ./...
	go -C implementations/go-supplier-v02-internal vet ./...

go-plan-verifier-check:
	go -C implementations/go-plan-verifier-v01-internal test ./...
	go -C implementations/go-plan-verifier-v01-internal vet ./...

supplier-capsule-check:
	uv run python scripts/build_supplier_v02_capsule.py --check

supplier-parity-check: supplier-capsule-check
	uv run python scripts/check_supplier_v02_parity.py --check-summary-portable experiments/supplier-v02-portability/v0.2-seed-2/parity-summary.json

supplier-parity-exact-check: supplier-capsule-check
	uv run python scripts/check_supplier_v02_parity.py --check-summary experiments/supplier-v02-portability/v0.2-seed-2/parity-summary.json

supplier-disagreement-check:
	uv run python scripts/capture_supplier_v02_disagreements.py --check

supplier-evidence-check: supplier-capsule-check supplier-disagreement-check
	uv run python scripts/check_supplier_v02_evidence_manifest.py

plan-verification-capsule-check:
	uv run python scripts/build_plan_verification_capsule.py --check >/dev/null

plan-verification-parity-check: plan-verification-capsule-check
	uv run python scripts/check_plan_verification_parity.py --skip-source-drift --check-summary-portable experiments/plan-verification-portability/v0.1-seed-1/parity-summary.json >/dev/null

plan-verification-rule-mutants-check: plan-verification-parity-check
	uv run python scripts/check_plan_verification_rule_mutants.py >/dev/null

plan-verification-evidence-check: plan-verification-parity-check plan-verification-rule-mutants-check
	uv run python scripts/build_plan_verification_evidence.py

plan-verification-installed-replay: plan-verification-parity-check plan-verification-rule-mutants-check
	uv run python scripts/build_plan_verification_evidence.py
	@echo "Frozen seed-1 evidence is read-only; publish a new coordinate for a fresh installed replay."

evolution-evidence-check:
	uv run python scripts/check_evolution_minimum_evidence.py

# Run the ordinary composite gate from an extracted copy without rewriting any
# published historical evidence coordinate.
archive-replay-check: plan-verification-installed-replay check

runtime-lowering-check: schemas-check
	uv run pytest tests/test_runtime_lowering.py -q

tck:
	uv run oac tck

demo:
	uv run oac demo

check: lint schemas-check benchmark-check ctk-successor-check type-check ctk-bundle-check independent-ctk go-phase-a-check go-supplier-v02-check go-plan-verifier-check supplier-evidence-check plan-verification-evidence-check evolution-evidence-check runtime-lowering-check tck test plan-disagreement-reproductions-check
