# Core approach: carry enterprise change into a verifiable result

Enterprise change can affect facts, policies, responsibilities or product plans. Suppose a company changes a quote discount rule from 5% to 10%: editing the number is easy. The hard part is finding work that still depends on the old rule, preserving work that does not, identifying who may decide, and ensuring the delivered result matches the exact approved change. These percentages illustrate the problem; they are not historical policies in the public sample or measured customer results.

OrgRebase represents enterprise change as source-bound work updates under explicit authority. **The organization supplies facts, rules, owners and targets. Agents, Tools and Skills prepare candidates and evidence. A deterministic control plane decides scope and admissibility. The exact owner approves; an authorized executor applies the change and records the successor and receipts.** Enterprise quotes are the runnable reference scenario; other business types require separate adaptation and acceptance.

## Supported change paths today

| Change | Current implementation scope |
|---|---|
| Launch date, currency, product plan | The quote reference path forms candidates and governed updates from admitted sources and dependencies |
| Basket items, order-level discounts and tax rules | Require the explicit pricing template v2 and its enterprise Pack; local amount calculation does not establish customer CRM write-back |
| Joint Quote and Discount Memo changes | Only the explicit two-deliverable profile, validated in a new isolated workspace with external effects disabled |
| Owner responsibility transfer | Only the opt-in two-party consent policy; not arbitrary delegation or automatic enterprise identity migration |

New deliverable types need their own handlers, source binding, owners and acceptance. This table lists supported paths, not coverage of every enterprise change.

## One enterprise change, end to end

1. **Establish the inputs.** Enterprise materials pass preflight, sealing, admission and activation. A change binds exact source versions, business objects and owners. Model text cannot promote itself into an enterprise fact.
2. **Organize only required work.** The control plane uses admitted capabilities, dependencies and observed reads to form domain tasks with bounded context. Teams may change across rounds; an admitted plan is not rewritten in place within a round. AgentTeams records execution and handoffs, not business approval.
3. **Verify the candidate and its impact separately.** Agents, Tools and Skills may propose candidates. The control plane independently checks provenance, authorization, dependencies and Preview results. It distinguishes required rebuilds, preservation within a declared boundary, and unknown impact. Missing coverage stays `UNKNOWN`.
4. **Approve an exact version, then apply it.** An owner reviews the digest and difference within their scope. Approval alone does not change the official result. An authorized executor submits Apply; the control plane rechecks version and authority at commit and updates only approved, affected objects.
5. **Keep a recoverable result.** Successor Quotes, dependencies, differences, approvals and Apply receipts have distinct identities. Failures or unknown outcomes are reconciled under the same operation identity before retry. External effects need separate authorization and read-back.

## Design choices and boundaries

| Design | Problem it addresses | Current boundary |
|---|---|---|
| **Organizational contracts and task-scoped teams** | Define facts, responsibility, capabilities and context before collaboration | OAC is a proposed draft; the quote reference path has controlled validation, not general enterprise certification |
| **Dependency-driven selective Rebase** | Avoid rebuilding every result when an upstream fact changes while refusing to treat uncovered dependencies as safe | Quotes and an explicit Quote + Discount Memo profile are supported in bounded runs; new deliverables still need handlers, source binding and acceptance |
| **Separate proposal, approval and effect** | Prevent model output or task completion from directly changing canonical business state | Approval and Apply bind exact objects; real customer identity and permissions require customer-environment validation |
| **Governed capability evolution** | Propose source-backed experience and Skill successors that can be rejected, withdrawn or restored | Controlled slices exist; cross-task quality, formal publication, adoption and business value remain open |

These decisions apply throughout one business flow. The primary user is a Quote Operations Owner. Product, Legal, Finance and GTM contribute domain candidates under their respective human owners. Participating domains, model calls and concurrency depend on the admitted task and execution mode.

## Verify it yourself

Start with the [no-model Quickstart](quickstart.en.md) over public historical product records; discount, tax and identities are controlled inputs. Then use the [browser walkthrough](demo.en.md) to inspect candidates, evidence recovery, approval and Apply. See [authority boundaries](architecture.en.md) for ownership and [deployment](deployment.en.md) for enterprise and external-effect prerequisites.

The `0.5.0b4` Beta evidence ceiling is **controlled-local validation**. Customer deployment, production capacity, cross-enterprise generalization, employee UAT and ROI still require separate acceptance. A website or presentation cannot supply that evidence.
