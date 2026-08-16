# OrgRebase Workspace AgentTeams transport

This directory declares a **fixed pool** of Product, Legal, Finance, and GTM Workers.
The deterministic OrgRebase `CoalitionPlanner` selects the minimum required subset for
each `TaskTemplate`; AgentTeams does not choose canonical authority, admit Claims, or
write the Workspace graph.

Local conformance:

```bash
PYTHONPATH=src python scripts/workspace_agentteams_check.py
```

Without Kubernetes/Matrix credentials, the live result is deliberately `NOT_RUN`.
Use `--require-live` only in an environment capable of producing genuine K8s, Matrix,
artifact, Skill, and provider-request evidence.
