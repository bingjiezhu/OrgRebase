# Third-party and evidence notice

Copyright 2026 Bingjie Zhu

Project-owned source, documentation, fixtures, and the bundled synthetic benchmark
are licensed under the PolyForm Noncommercial License 1.0.0. Commercial use requires
a separate written license. See [LICENSE](LICENSE) and
[COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md). This is source-available. It is not
OSI Open Source.

Runtime dependencies are FastAPI (MIT), Pydantic (MIT), Uvicorn (BSD-3-Clause),
and their locked transitive dependencies. AgentTeams is an Apache-2.0 upstream
integration target; its source is not vendored. The AgentTeams assets in this
repository are application manifests and adapters authored for OrgRebase.

All bundled business data and evaluation cases are synthetic. `LOCAL_DETERMINISTIC`,
`SYNTHETIC_FIXTURE`, `PASS_STATIC`, `LIVE_AGENTTEAMS`, and `NOT_RUN` are deliberately
different evidence classes and must not be conflated.

The default local demo calls no commercial API or closed model. Optional live
AgentTeams execution may use Vertex AI's OpenAI-compatible endpoint with
`google/gemini-3.1-flash-lite`. Deployment credentials, project identifiers,
project-scoped endpoints, raw prompts, and raw model output are excluded from the
public release. Reproducible scoring uses the deterministic profile.
