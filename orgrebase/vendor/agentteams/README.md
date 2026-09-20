# Pinned AgentTeams source bundle

`AgentTeams-v1.2.3-223ddc2.bundle` is a complete Git bundle of the upstream
AgentTeams `v1.2.3` tag at commit
`223ddc2b8073e4c8b93bcbb15e1d717f196c04d9`.

It is included so an extracted competition source package can reconstruct the
native TeamHarness `projectflow/taskflow` checkout without network access. The
bundle digest and required MCP source-file digests are fixed in
`agentteams/teamharness-lock.json`; `scripts/fetch_pinned_agentteams.py` verifies
both before use and sets the recorded upstream URL before the runtime verifier
admits the checkout.

The upstream project is Apache-2.0 licensed. Its exact `LICENSE` file is inside
the bundle and can be inspected without checkout:

```bash
git --git-dir ./agentteams-license.git init --bare
git --git-dir ./agentteams-license.git fetch \
  vendor/agentteams/AgentTeams-v1.2.3-223ddc2.bundle \
  refs/tags/v1.2.3:refs/tags/v1.2.3
git --git-dir ./agentteams-license.git show refs/tags/v1.2.3:LICENSE
```

The bundle supplies upstream source only. It does not grant AgentTeams
canonical business-state authority and does not upgrade the controlled-local
evidence class to a distributed production claim.

The same complete bundle contains the historical v1.2.2 commit
`849182af8e017168a5a200a87b1062142caf462d`. After the fetch above, inspect it with
`git --git-dir ./agentteams-license.git show 849182af8e017168a5a200a87b1062142caf462d:LICENSE`.
For source replay, compare that commit's three MCP files with
`agentteams/historical/teamharness-v1.2.2.json`; do not replace the current runtime
lock or rewrite the historical lock. A second copy of the upstream history is not
needed merely to inspect those exact historical bytes.
