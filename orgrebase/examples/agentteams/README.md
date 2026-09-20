# AgentTeams sample input and output

- `change-request.json` is the exact frozen input accepted by the GOAI reference runner.
- `run-summary.example.json` is an illustrative projection of the output shape. Its
  `ILLUSTRATIVE_PROJECTION_NOT_RUNTIME_EVIDENCE` label is intentional; use the generated
  evidence for verification.

The runner deliberately fails closed for a different scenario, object, version, value, owner,
or team roster instead of pretending to support an unvalidated enterprise universe.

Run from the repository root:

```bash
./run-agentteams-demo.sh
./verify-agentteams-demo.sh
```

The generated output is written to `evidence/goai-agentteams/latest/`. Start with
`run-summary.json`; it indexes the five reference Agent runs, structured handoffs, tool
invocation, result, exception drills, rollback, OTLP telemetry, the scoped historical transport
record, Fresh Core live proposal-plane evidence, and every generated artifact digest.

Read `run_classification` before interpreting the result. A passing local run means the frozen
reference fixture passed. It does not mean autonomous collaboration occurred, and it does not
promote the current Workspace AgentTeams status beyond `NOT_RUN`.
