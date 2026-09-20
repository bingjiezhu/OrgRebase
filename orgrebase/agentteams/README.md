# AgentTeams deployment assets

`team.yaml` targets the AgentTeams source release in `source-lock.json`
(`v1.2.3`, commit `223ddc2b8073e4c8b93bcbb15e1d717f196c04d9`), using
`agentteams.io/v1beta1` CRDs. Runtime consumers read this lock through
`orgrebase.agentteams_source`; the TeamHarness lock additionally binds exact MCP
source bytes and the offline Git bundle.
It declares five Workers and exactly one Team Leader. OrgRebase compiles their standalone
identity contracts into an authority-aware `OrchestrationPlan`; AgentTeams transports the
proposal work but cannot rewrite that DAG or write canonical state. These resources are
deliberately secret-free: model routing and credentials belong in the deployment environment.

The historical deployment model is `google/gemini-3.1-flash-lite` through Vertex AI's
OpenAI-compatible endpoint. Local deployment may use short-lived Vertex ADC access tokens;
the token is runtime configuration and never belongs in this repository.

The files alone prove **static compatibility**. The repository also carries one separately
frozen `LIVE_AGENTTEAMS` receipt from the earlier v1.2.2 generation-2 Worker deployment. It binds
Kubernetes, Matrix identities, runtime Skill bytes, four Vertex provider calls, the
Leader-committed orchestration-plan digest, and each candidate's exact task/input bindings.
It proves one observed run, not production reliability. The deterministic OrgRebase control
plane admitted all four outputs as advisory-only and remained the sole state writer.

That receipt and `workspace/source-lock.json` retain their original v1.2.2 identity
and image digest. They do not qualify a v1.2.3 deployment. The active source upgrade
requires fresh runtime evidence before any distributed execution claim is updated.

`scripts/freeze_agentteams_run.py` compares the active source identity with the
reviewed Core deployment configuration and its live-run schema. While those
still qualify v1.2.2, a v1.2.3 source checkout fails with
`DEPLOYMENT_SOURCE_NOT_QUALIFIED` before reading Kubernetes or writing an envelope.
Preflight reports source and dependency readiness; it does not grant deployment
qualification.

Validate the source lock and static assets locally:

```bash
uv run python scripts/validate_assets.py
```

Probe the exact provider route without disclosing credentials, prompts, or completions:

```bash
make vertex-probe
```

`LIVE_PROVIDER_PROBE` proves only one Vertex request. It cannot satisfy the stricter
`LIVE_AGENTTEAMS` evidence predicate.
