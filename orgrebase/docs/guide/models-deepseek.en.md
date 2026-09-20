# DeepSeek integration

The client defaults to the API model ID `deepseek-flash`. It is a provider alias, not an immutable model-artifact digest. A fixed alias does not replace service-version and observed-receipt records.

## Interface and credentials

| Setting | Value |
|---|---|
| provider | `deepseek` |
| endpoint | `https://api.deepseek.com/chat/completions` |
| model | `deepseek-flash` |
| credential variable | `DEEPSEEK_API_KEY` |

Supply the key through a secret facility or the current process environment, not source files, screenshots or receipts. OrgRebase validates structured candidates and records status, digests and usage. Model output still cannot admit itself, approve a change or invoke canonical Apply.

## Execution scope

DeepSeek joins the existing native Reviewer candidate path. This journey requires explicit `ORGREBASE_OAC_EXECUTION_MODE=LIVE_VERTEX`: Vertex performs OAC mapping, and DeepSeek performs Reviewer work. Configure both the Vertex project and credentials described in the [Vertex guide](models-vertex.en.md), and `DEEPSEEK_API_KEY`. The change-round ModelRequestV2/Responses interface remains outside this provider's scope.

After installation and secure external injection of both providers' credentials, run from the product directory with a new working directory and replace `YOUR_GCP_PROJECT` with the authorized Vertex project:

```bash
export ORGREBASE_OAC_ROOT="$(pwd)/../oac-spec"
export ORGREBASE_VERTEX_MODEL_ID=gemini-3.8-flash
export ORGREBASE_OAC_EXECUTION_MODE=LIVE_VERTEX
./run-enterprise-pilot.sh start \
  --pack examples/enterprise-quote-pilot/evergreen \
  --work-dir ../pilot-deepseek-new \
  --competition-mode golden --model-provider deepseek \
  --vertex-project YOUR_GCP_PROJECT
```

The launcher rejects DeepSeek unless `LIVE_VERTEX` is explicitly selected. `OFFLINE_LOCAL` permits only the local provider and cannot run a DeepSeek Reviewer. The Vertex mapping candidate still requires deterministic validation and the designated owner's admission. Four domain Workers remain deterministic. Both providers' cloud calls can incur charges; this path has not yet produced live DeepSeek request or business-closure evidence. Implementation and mocked-transport tests establish only the tested protocol behavior, not a successful live run.

The provider requests a JSON object and validates the schema locally. JSON-output mode is not a provider guarantee of every business-schema constraint.

## Inspect the outcome

Check model request/response status for the same run, output schema, candidate bindings, Reviewer versus deterministic decisions, and subsequent human approval. An API connectivity probe establishes only that the service processed that request, not completion of a Quote, enterprise onboarding or production acceptance.

Official names and request formats are documented in [DeepSeek's first API call guide](https://api-docs.deepseek.com/). Preserve transport, quota and output failures; never replace them with another provider's successful receipt.
