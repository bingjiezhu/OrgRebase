# Licensing

Project-owned OrgRebase material in this tree is Apache License 2.0, including code, tests, schemas, Skills, fixtures, documentation and other project prose. The grant text is [LICENSE](LICENSE). Copyright 2026 Bingjie Zhu.

## 原先

Public revisions through tag `v0.4.0` used PolyForm Noncommercial 1.0.0. Commercial use of those revisions needed a separate written grant.

A later `main` commit used the OAC path split: Apache-2.0 for executable and machine-readable assets, CC BY 4.0 for normative prose.

## 现状

This tree offers all project-owned OrgRebase files under Apache-2.0. Commercial use is included. [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md) records that change.

## 为什么会有这样的更新

The copyright holder chose one OSI license for the repository's own material. `LICENSES/CC-BY-4.0.txt` remains so upstream CC BY datasets can be read with their own terms. It is not a grant for OrgRebase project-owned files.

`vendor/agentteams/` keeps the upstream Apache-2.0 terms in `vendor/agentteams/README.md`. `benchmark/public-retail-quote/` keeps its upstream CC BY 4.0 notice. Dependencies, PostgreSQL and optional models keep their own terms. Model weights are not included.

Skill manifests and the OrgWorkBench dataset manifest still contain the sealed identifier `PolyForm-Noncommercial-1.0.0`. That string is part of the content address used by retained evidence. It does not narrow the Apache-2.0 grant for project-owned files in the current tree.
