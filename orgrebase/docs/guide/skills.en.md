# Skills and enterprise Pack reuse

## Three existing Skills

| Skill | Purpose | Boundary |
|---|---|---|
| structured-domain-handoff | Shared task/candidate handoff contract across four domains | Rejects or abstains on missing bindings, excess authority or schema errors |
| enterprise-quote-compose | Binds the same run's Tool and four-domain results into a Quote candidate | Cannot self-approve or write canonical state |
| enterprise-launch-readiness | Maps verified impact classifications to bounded action suggestions | Cannot turn Unknown into a positive conclusion |

Packages contain SKILL.md, a contract, program, schemas and a version manifest. The registry binds exact versions and package digests. The interpreter and concrete adapter validate inputs, execute supported programs and record output receipts under that contract. Uploading an arbitrary Python file does not make it an executable Skill.

Compatibility and recovery validation runs the following checks in an isolated ledger, bound to the complete three-Skill catalog digest:

| Skill | Versions and validation scope |
|---|---|
| enterprise-quote-compose | `1.3.0 → 1.3.1 → 1.3.0`: qualification, trial invocation and actual predecessor recovery invocation; 12 calls |
| enterprise-launch-readiness | `1.4.1 / 1.4.2`: six controlled cases against both versions, followed by one predecessor replay; 13 calls |
| structured-domain-handoff | `1.1.1 / 1.1.2`: the same six paired cases and one predecessor replay; 13 calls |

A complete successful run makes 38 calls. The latter two use scope `ISOLATED_COMPATIBILITY_AND_PREDECESSOR_INVOCATION`: they verify compatibility and actual invocation of retained version bytes without switching a production Skill. Each pair uses the same executable program, so these checks do not establish an algorithm upgrade. Validation changes neither the workspace Quote nor the active capability registry. Consult the [Skill registry](../../configs/workspace/skill-registry.json) and receipts from the actual validation run.

The current suite, `orgrebase.quote-skill-qualification.v2`, contains nine cases across eight partitions. Its added negative case substitutes the product result digest for the legal result while requesting normal composition; the Skill must abstain. Cached validation binds the suite revision and digest, so an earlier eight-case pass cannot satisfy current qualification. Archived records remain verifiable under their original suite.

Eight-partition checks are constructive qualification, not sampled generalization estimates. HELD_OUT and CANARY labels in the current case set are not statistical holdouts or production traffic rollout. New enterprises or deliverable types require their own handlers, domain tests and business acceptance.

## Change rules without changing the engine

Follow the [Pack exercise](../REUSE-AND-LICENSING.md#try-a-rule-pack-without-changing-the-engine): initialize a draft, edit supported knowledge fields, seal into a new directory and run the check. Do not edit digests manually or mark incomplete sources COMPLETE.

Keep the governance path, authority checks and receipts; replace enterprise facts, owners, source mappings and target adapters. Each workspace currently manages one Quote. Four Workers sharing the handoff package is implemented reuse, not evidence of support for arbitrary business workflows.

See the original-language [Skill inventory](../SKILL-LIST.md) for full interfaces and qualification boundaries.
