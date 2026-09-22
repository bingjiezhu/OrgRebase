# Third-party and evidence notice

Copyright 2026 Bingjie Zhu

Earlier public revisions, including tag `v0.4.0`, licensed project-owned source under
PolyForm Noncommercial 1.0.0, with commercial use reserved. This tree licenses
project-owned source, documentation, fixtures, and the bundled synthetic benchmark
under Apache License 2.0, including documentation. See [LICENSE](LICENSE),
[LICENSE.md](LICENSE.md), and [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md).
Content-addressed Skill and benchmark records may still contain the older SPDX
identifier so retained evidence stays verifiable.

Runtime dependencies are Authlib (BSD-3-Clause), HTTPX2 (BSD-3-Clause),
FastAPI (MIT), Pydantic (MIT), rfc8785.py (Apache-2.0),
Uvicorn (BSD-3-Clause), SQLAlchemy (MIT), Psycopg and psycopg-binary (LGPL-3.0-only),
PyJWT (MIT), cryptography (Apache-2.0 OR BSD-3-Clause), and their locked transitive dependencies.
Installed dependency distributions retain their respective license files; the project license does not replace them.
AgentTeams is an Apache-2.0 upstream
integration target. A complete Git bundle of the exact v1.2.3 upstream tag is
redistributed under `vendor/agentteams/` solely for offline source reconstruction;
its upstream `LICENSE` remains inside that bundle. OrgRebase's AgentTeams manifests,
locks and adapters remain project-authored integration code.
Historical v1.2.2 deployment receipts retain their original source identity and
do not qualify the current v1.2.3 source as a distributed deployment.

Reference organisations and the OWB mechanism cases are synthetic. The attributed
UCI Online Retail slice contains public historical transactions; its source manifest
and CC BY 4.0 notice are in `benchmark/public-retail-quote/v1/`. Discount, tax and
authority rules applied to that slice are controlled examples, not facts about the
original retailer. Other public inputs retain their own source and licence notices.
`LOCAL_DETERMINISTIC`, `SYNTHETIC_FIXTURE`, `PASS_STATIC`, `LIVE_AGENTTEAMS`, and
`NOT_RUN` describe different evidence scopes and must not be conflated.

Verification of frozen receipts makes no new model or cloud call. The current
local interactive journey uses the pinned Ollama model described in the README.
Explicit live mode can use the structured Vertex adapter; neither mode gives model
output approval or canonical-write authority. Retained Vertex and older Core
AgentTeams compatibility runs keep their original model and source identities.

Private credentials, deployment-specific settings and private customer data are
not part of the source distribution. Public fixtures and explicitly scoped
controlled-run evidence may contain example prompts and model responses; their
presence does not establish real-customer validation or permission to disclose
private inputs. Check each delivery's inventory and source notices for its scope.
