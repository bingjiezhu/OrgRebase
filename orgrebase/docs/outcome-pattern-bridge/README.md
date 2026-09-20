# Public outcome cases with retained evidence

`orgrebase.workspace.outcome_pattern_bridge` consumes the existing disposable-lab portable closure through the same OAC verifier. It creates a candidate case for `GovernedPatternService`; it does not execute tools or admit a skill.

The current profile is `orgrebase.tau2-retail-outcome-case/v2` and its case certificate declares `orgrebase.outcome-case-evidence.v2`. This unpublished bridge replaces the former inline receipt input. There is no fallback that admits an unverified raw receipt as a portable outcome.

## Retain, resolve, then govern

```python
from orgrebase.workspace.outcome_pattern_bridge import (
    OutcomeRunEvidence, build_retail_case_candidate, make_retail_case_resolver,
)
from orgrebase.workspace.pattern_evolution import GovernedPatternService

# store is the existing scoped StateStore; gate uses the admitted public OAC CLI.
# Each trust comes from independently retained controller records, not the bundle.
def runs():
    for receipt_digest in independently_admitted_receipts:
        yield OutcomeRunEvidence(
            load_portable(receipt_digest), trusted_contexts[receipt_digest]
        )

case = build_retail_case_candidate(
    complete_public_task, runs(), store=store, gate=gate,
    corpus_authority="scripted:corpus-controller",
)
resolver = make_retail_case_resolver(store, gate, trusted_contexts.__getitem__)
learning = GovernedPatternService(
    store, corpus_authority="scripted:corpus-controller",
    evaluator_authority="scripted:replay-controller",
    governance_authority="scripted:skill-governance",
    case_evidence_resolver=resolver,
)
# freeze_corpus / open_proposal / propose / evaluate / decide remain the one
# governed learning path. A returned case is not an admission or release.
```

The builder verifies and saves each complete portable bundle once in content-addressed StateStore artifacts, together with the full source task and exact context record. Case rows retain media type, artifact ID, payload digest, source coordinate, approval and receipt digests, and the actual OAC `OutcomeCertificate` resource reference. Repeating the same input creates no second evidence payload. An invalid member rolls the entire case write back. Pass a generator when reading large archives so the caller does not load every complete receipt at once.

The resolver reads those exact artifacts from the same scope. It obtains authorization context from the caller-owned callback, compares the retained context, reruns `verify_portable_lab_outcome`, and reconstructs the case. Missing artifacts, unsupported profiles, substituted references, changed public features or missing independent trust fail closed. The retained context is evidence to compare; it cannot grant authority to itself.

Every experiment context must include equally many runs of all four systems. Approvals cannot be reused. Repetitions, changed budgets and revised mappings do not turn one public source task into independent cases. Only declared public scenario text or null is exposed for clustering; evaluator-only criteria stay outside that feature projection. Actual OAC rejection or uncertainty remains a counterexample or unknown case.

On corpus freeze, the service records each verified OAC outcome certificate as a typed evidence provider. Retraction before proposal prevents later proposal or refreezing; retraction after proposal propagates through the existing certificate/case/pattern/skill dependency graph. Case-local invented digests cannot substitute for the real OutcomeCertificate reference.


## Inspect an evaluated candidate

The existing Python experiment controller can read a comparison after `freeze_corpus`, `freeze_replay`, `open_proposal` and `propose` have returned `candidate_ref`:

```python
evaluation_ref = learning.evaluate(
    candidate_ref, actor_id="scripted:replay-controller"
)
comparison = learning.evaluation_comparison(
    evaluation_ref, actor_id="scripted:skill-governance"
)
```

Evaluation still runs each frozen case once against the candidate and once against the prior package. The same transaction now retains both actual result bodies in a separately versioned observation artifact. Reading the comparison executes no skill and writes no artifact. Only the configured replay or governance controller can read it; this is a Python experiment interface, not an added ordinary-workbench function or a release permission.

Each row binds the same input digest to both observed actions. All frozen cases remain in the denominator, including `DENY` and `ABSTAIN`; a rejected evaluation is still inspectable. The view distinguishes program, resource and applicability changes. Current candidate applicability can change an action even when program and resource bytes are unchanged. `SKILL.md` and language references are currently validated package resources, not candidate instructions consumed by this executor.

`NO_BEHAVIOR_DELTA` means the retained results are identical for these frozen inputs after excluding only the package identity field. It does not prove general equivalence or business improvement. A changed action is not inherently a better action. Historical evaluations without the new observation artifact return `UNKNOWN`, null actions and no changed-case count; the reader neither guesses from old hashes nor reruns old packages. Existing evaluation receipt bytes and admission gates are unchanged.

## Evidence and limits

`tests/workspace/test_outcome_pattern_bridge.py` uses actual controller receipts, the public OAC CLI, SQLite reopen, and deliberate resealing, reference, trust, repetition and retraction attacks. Those small tests use a controlled state environment. Separately retained public-task experiments can be loaded through the same entry point without rerunning tools.

The ordinary scoped StateStore remains the only storage implementation. A complete portable bundle retains all raw state observations; this bridge reduces copies in cases and corpora, not evidence completeness. It does not assert that observations were independently produced by another organization, that the public benchmark is fully covered, or that one repeated task qualifies a general skill. The scripted authorities in this example are not human or enterprise IAM credentials.
