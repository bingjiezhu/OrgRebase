# OrgRebase documentation

OrgRebase manages updates to enterprise work through organizational contracts, candidate validation, exact approval and selective Rebase. Changes can affect facts, policies, responsibilities or product plans. The system identifies affected tasks and deliverables, preserves work supported by evidence, and binds application to the exact version approved by the responsible owner.

This documentation covers **OrgRebase `0.5.0b4` Beta**. Enterprise quotes are the current runnable reference scenario; the default single-Quote and explicit Quote + Discount Memo profiles have distinct controlled validation scopes. OAC supplies a proposed organizational contract, AgentTeams records execution, and Skills provide bounded capabilities. The [core approach](approach.en.md) explains how these pieces handle one enterprise change.

## Choose a starting point

| Goal | Guide |
|---|---|
| Understand enterprise change and the design | [Core approach](approach.en.md) |
| Verify installation and a pricing workflow | [Quickstart](quickstart.en.md), without a model or customer credentials |
| Configure a cloud-model reference journey | [Vertex](models-vertex.en.md); [DeepSeek](models-deepseek.en.md) is an optional Reviewer path with a different evidence level |
| Inspect business results before and after approval | [Demo and verification](demo.en.md) |
| Understand authority and state transitions | [Architecture](architecture.en.md), [AgentTeams](agentteams.en.md) |
| Adapt enterprise facts and rules or plan deployment | [Skills and reuse](skills.en.md), [Deployment](deployment.en.md) |
| Contribute, publish or evaluate commercial adoption | [Contributing](contributing.en.md), [Publishing](publishing.en.md), [Licensing](licensing.en.md) |

## What the evidence establishes

Candidates, review, approval and application are separate facts. The public-quote first run and local PostgreSQL/OAC checks establish behavior only within their declared boundaries. They do not establish customer deployment, production capacity or willingness to pay. Product records are historical public data; demo discounts, taxes and organizational identities are controlled inputs. Continuous Skill learning, customer adaptation and business value still have open acceptance work.

Core guides have paired Simplified Chinese and English pages. The language menu preserves the current page. [Detailed technical references](reference.en.md) retain their original language and are labeled accordingly. The “Site source and downloads” page in the navigation identifies the actual build bytes; until a new version is published, the live site may still show an earlier snapshot. Use documentation from the same source revision.
