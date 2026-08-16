# AgentTeams deployment assets

`team.yaml` targets AgentTeams `v1.2.2` CRDs (`agentteams.io/v1beta1`), pinned by
`source-lock.json` to upstream commit `849182af8e017168a5a200a87b1062142caf462d`.
It declares five Workers and exactly one Team Leader. OrgRebase compiles their standalone
identity contracts into an authority-aware `OrchestrationPlan`; AgentTeams transports the
proposal work but cannot rewrite that DAG or write canonical state. These resources are
deliberately secret-free: model routing and credentials belong in the deployment environment.

The tested deployment model is `google/gemini-3.1-flash-lite` through Vertex AI's
OpenAI-compatible endpoint. Local deployment may use short-lived Vertex ADC access tokens;
the token is runtime configuration and never belongs in this repository.

The files alone prove **static compatibility**. The repository also carries one separately
frozen `LIVE_AGENTTEAMS` receipt from the current generation-2 Worker deployment. It binds
Kubernetes, Matrix identities, runtime Skill bytes, four Vertex provider calls, the
Leader-committed orchestration-plan digest, and each candidate's exact task/input bindings.
It proves one observed run, not production reliability. The deterministic OrgRebase control
plane admitted all four outputs as advisory-only and remained the sole state writer.

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
