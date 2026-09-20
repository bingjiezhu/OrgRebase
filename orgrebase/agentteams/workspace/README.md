# OrgRebase Workspace AgentTeams transport

`source-lock.json` records the **historical v1.2.2 deployment qualification**,
including its observed Worker image digest. It is intentionally unchanged when
the active TeamHarness source in `../source-lock.json` is upgraded. A historical
image or transport receipt must not be relabelled as proof of the active source
release. New distributed execution requires a separately observed deployment
identity and matching end-to-end evidence.

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
