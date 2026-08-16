# Examples

Workspace sample accepted by `POST /api/workspace/form` and used by `workspace-demo`:

- `input/enterprise-quote-task.json`: sales employee Quote task.
- `output/coalition-plan.json`: deterministic Product/Legal/Finance/GTM coalition.
- `output/quote-v1.json`: initial deliverable with execution-born dependencies.
- `output/quote-v2.json`: launch-date rebase result.
- `output/quote-v3.json`: currency rebase result after process restart.

These snapshots match the checked-in Workspace evidence under
`evidence/workspace/latest/formation/` and `evidence/workspace/latest/repeatability/final-quote.json`.

Core fixture input is `fixtures/canonical-enterprise.json`. Core run output is
`evidence/latest/demo.json`.

Run the complete Workspace example:

```bash
PYTHONPATH=src python -m orgrebase workspace-demo --output-dir evidence/workspace/latest
```
