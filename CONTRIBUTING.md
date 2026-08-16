# Contributing to OrgRebase

OrgRebase welcomes small, evidence-backed changes that preserve the control-plane
invariants.

Public homepage: English [`README.md`](README.md) and Chinese [`README.zh-CN.md`](README.zh-CN.md).
Keep product claims and status labels in both files in sync.

## Development

```bash
make setup
make check
make demo
```

Without `uv`:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Every behavior change needs a test for its invariant or failure boundary. New evidence
must declare one of the existing evidence classes; static assets, replay, and synthetic
fixtures must never be promoted to `LIVE_AGENTTEAMS`.

## Design rules

- Agents may propose; only the deterministic control plane writes canonical state.
- Absence of an admitted dependency is not proof of non-impact.
- Preserve immutable history; rollback is a compensating version, never deletion.
- Tool and Skill permissions must be explicit and default-deny.
- Do not add a service, datastore, framework, or cloud dependency without a measured need.

Open an issue with the problem, threatened invariant, smallest interface change, and the
evidence that will prove it. Security reports follow [SECURITY.md](SECURITY.md).

## License and copyright

OrgRebase is source-available under a dual-license model. See [LICENSE](LICENSE) and
[COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md).

By submitting a contribution, you confirm that you have the right to submit it, and you
license it to **Bingjie Zhu** under the same dual-license terms: PolyForm Noncommercial
License 1.0.0 for non-commercial use, plus the right to sublicense the contribution as
part of OrgRebase under a separate commercial license. You keep copyright in your
contribution unless a later CLA says otherwise.

This inbound grant is what keeps future commercial licensing possible as the project
takes outside patches.
