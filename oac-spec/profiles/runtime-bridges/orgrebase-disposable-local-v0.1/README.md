# OrgRebase disposable local bridge v0.1

This profile connects an accepted OAC plan to one bounded, disposable local state experiment in OrgRebase. The existing OAC compiler and verifier establish plan validity. The existing `OutcomeLab` controller grants and consumes a separate one-use authorization, observes effects and computes outcomes. OAC does not schedule tools or receive production credentials.

The supported public task projection is pinned by `orgrebase/scripts/public_outcome_lab/pins/tau-source.json`. The task mapping, environment build, seed, oracle policy and exact tool requests are immutable inputs. The published four-system comparison uses one controller and the same task/budgets. It measures this state projection with scripted confirmation; it is not a full conversational benchmark or a customer ROI result.

## Contract ownership

| Boundary | Owner and exact contract |
|---|---|
| Business change and accepted plan | Existing OAC public `compile` / `verify`; `oac.retail.cancellation-review/v0.1` is an explicit declarative profile of the same semantic kernel. |
| Source and Demand | `build_lab_lifecycle` retains source bytes, snapshot-qualified requester and locally declared authority evidence; public `oac validate-evolution` checks semantics. Scope is `CONTROLLED_LOCAL_SCRIPT_ADMISSION`. |
| Execution approval | `OutcomeLab.approve` resets the owned environment and returns a one-use token, immutable approval receipt and complete runtime package before any tool dispatch. Distinct controller, actor and oracle identities are host configuration, not verified human identity. |
| Execution | `OutcomeLab.run` requires the exact approved runtime package, consumes the token, and compares the supplied package to its privately saved copy before dispatch. Exact requests, roles, obligations, WorkUnits, required order, scope and budgets use the same execution loop. |
| Observation | `outcome_verification` recomputes six dimensions from retained state observations, calls, denials, timings and independent oracle inputs. |
| Portability | `outcome_portability` verifies every payload against separately trusted lifecycle/approval/receipt/oracle-build pins and reuses the original pre-execution runtime package, then invokes the public OAC validator. |
| Outcome wire | `oac.outcome.disposable-local/v0.1`, descriptor at `../../outcome-profiles/disposable-local-v0.1/profile.json`. Its separate execution-root domain binds a `DisposableExecutionGrant`, `DisposableRuntimeBinding`, `DisposableRuntimeBundle` and `ExecutionReceipt`. |
| Reuse | `outcome_pattern_bridge` stores each full portable receipt once through the scoped `StateStore`; the case holds exact artifact references and actual OutcomeCertificate dependencies. One fixed task remains one independent case across repeats and revisions. Existing `GovernedPatternService` owns corpus admission, evaluation and governance through `StateStore` / `SkillPackageRegistry`. |

The plan, its role instances and WorkUnits retain `zero_effect`. The original G1a `ZeroEffectRuntimeBundle` continues to describe its original contract. It cannot be relabelled to authorize sandbox writes. The new disposable kinds refer only to the separate local grant. No compatibility fallback converts one effect contract into the other.

## Required host sequence

1. Admit fixed source, dependency files, actual loaded seed, mapping, independent oracle policy and tool requests.
2. Verify the exact plan through the public CLI and create Source/Demand lifecycle evidence before execution. Persist its digest in trusted host storage.
3. Create a controller approval for one system/run. Under the same lifecycle lock, reset and snapshot the disposable environment and construct the exact mapping/grant/binding/bundle package. Retain that package before any dispatch.
4. Submit both the token and that complete package to the sole execution entrypoint. The attempt consumes the token, including on a rejected package. Compare with the controller-owned package before dispatching only the declared requests. Capture authoritative observations before and after each attempt, including denials and unresolved responses.
5. Verify the returned receipt against separately retained mapping, oracle, plan-certificate and authority pins. Persist the actual approval/receipt identity separately from exported data.
6. Reuse the original runtime package in the portable closure using an explicit oracle implementation-build digest; verify it independently and invoke `oac validate-evolution` for the resulting certificate.
7. Admit a case through the existing corpus interface only after complete receipt verification. Repeated systems, repeated runs and changed plans for the same fixed source task do not create independent cases.

An imported bundle cannot nominate its own trusted pins. Recomputing a JSON digest is neither an issuer signature nor proof that a real reviewer approved work. OAC's portable validator checks references, roots and verdict consistency; the product verifier additionally reconstructs the payload closure. Both checks are required for this bridge.

## Effect and failure policy

| Situation | Required behavior |
|---|---|
| Missing/invalid/non-accepted plan certificate | No approval or tool dispatch. |
| Reused, invalidated or unknown token | Reject before dispatch. A new reset invalidates prior approval. |
| Request outside the exact grant, missing role/order, or exhausted budget | Record denial; do not dispatch that request. |
| Read-only tool changes any state | Record the actual change and reject the read-only contract, even inside a generally writable path. |
| Forbidden change later restored | Retain intermediate observations and the violation; final-state equality cannot erase it. |
| Tool response lost, timeout or process loss after a possible effect | Preserve UNKNOWN observation and trace; do not blindly retry. Known failed obligations can still make the overall verdict REJECT while other dimensions remain UNKNOWN. |
| Cancellation | No in-flight cancellation contract is offered. Calls are bounded; closing/approving/running is serialized. A caller must wait for the bounded operation and retain uncertainty if it is lost. |
| Replay | Verify retained observations and reset identity. A new trial requires a fresh environment reset and fresh authorization; it is not a retry of an ambiguous effect. |
| Compensation | No remote compensation capability is offered. Reset restores only the owned disposable environment; it is not a production business rollback. |
| Concurrent operations | One controller serializes approval, run and close. There is no cross-host sandbox lease or multi-process shared environment claim. |
| Production writes | Unsupported. The sandbox process has no network and no file-write capability; only the disposable in-memory database may change. Production effects remain under the product's existing Commit Gateway and persistent effect-worker protocol. |

The six required dimensions are task goal, forbidden effects, evidence completion, scope, replayability and unresolved observations. Any known FAIL yields REJECT; otherwise any UNKNOWN yields UNKNOWN; only all PASS yields ACCEPT. ACCEPT does not promote a reusable Skill or release a production effect.

## Verification coordinates

Contract and adversarial tests live in `orgrebase/tests/workspace/test_outcome_lab*.py`, `test_outcome_portability.py`, `test_outcome_pattern_bridge.py` and `orgrebase/tests/test_public_outcome_lab_scripts.py`. Public script source and environment pins are distributed in the source archive; the upstream sandbox dependencies are not added to the production application dependency set.

Fresh actual public experiments, independent replay, PostgreSQL corpus persistence, full-suite and installed-package results are indexed by the parent completion record. Each record must retain its own exact source manifest, command exit code and claim boundary. Historical records are immutable and do not automatically qualify changed source.

This bridge implements the bounded local contract for T007-101–105 and the execution/outcome portions of T008-101–102. Its actual execution input is the explicit disposable-local RuntimeBundle and separate grant. It does not execute or relabel G1a ZeroEffectRuntimeBundle. Final qualification requires the exact installed artifact and linked execution evidence; implementation alone does not close that gate. Corpus/Pattern/Skill/retraction behavior belongs to Spec 006 and the existing governed Source path. Authenticated enterprise intake, actual reviewer independence, arbitrary enterprise workflows, customer deployments and cost savings require their own evidence.
