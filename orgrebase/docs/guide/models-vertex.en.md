# Vertex Gemini integration

The cloud reference launcher defaults to Gemini 3.8 Flash on Vertex. It participates in OAC adaptation or Reviewer advice through the existing structured-candidate interface. Deterministic validation and exact-owner approval remain separate gates.

## Prerequisites

- Complete [installation](quickstart.en.md) and obtain the matching sibling `oac-spec/` source.
- Enable the required Vertex services in an authorized Google Cloud project and use an identity allowed to invoke the model.
- Keep credentials in the deployment environment: explicit Vertex token/key or the supported ADC path, never source files, command examples or execution records.
- Check region, quota, model access and service terms for the deployment. Having configuration is not proof of a successful call.

## Start the native journey

```bash
export ORGREBASE_VERTEX_PROJECT="YOUR_GCP_PROJECT"
export ORGREBASE_VERTEX_MODEL_ID="gemini-3.8-flash"
export ORGREBASE_OAC_ROOT="$(pwd)/../oac-spec"
export ORGREBASE_DEMO_PORT=8000
./run-semifinal-demo.sh live
```

Run from the product directory. A project ID is not an API key. The provider also accepts `ORGREBASE_VERTEX_PROJECT_ID`; the launcher forwards the `ORGREBASE_VERTEX_PROJECT` value above as its project argument.

Explicit credential variables are `ORGREBASE_VERTEX_ACCESS_TOKEN` or `ORGREBASE_VERTEX_API_KEY`, injected through a secret facility. Otherwise, only the implementation's supported ADC/gcloud resolver is used; it does not assume an arbitrary machine is already authenticated. Verify your own permissions without printing tokens into logs.

`live` selects the `LIVE_VERTEX` OAC path and makes real external requests that may incur charges. Admission precedes task execution. A model's PASS is neither a deterministic contract decision nor business approval. Inspect provider, model, status and usage in actual receipts. A successful receipt establishes a successful response. Count failed or unknown attempts that were sent or may have been sent according to their dispatch state and per-attempt records; an HTTP 429 response is a provider attempt, not zero calls. Unavailable usage remains unknown, not zero.

## Failures and alternatives

Keep identity, quota, transport and structured-output failures as failures or unknowns. Do not substitute historical receipts. Inspect the reason and current state before considering a retry. Without a cloud model, the [model-free first run](quickstart.en.md) remains available. Local Ollama is an explicitly selected fallback evaluation path.

See the original [Model/Agent/Tool interface reference](../MODEL-AGENT-TOOL-INTERFACES.md) for details.

## Quota errors and timeout boundaries

Only an explicit HTTP 429 response triggers bounded retry: at most three provider attempts share one 60-second provider budget, with a record for every attempt. Ambiguous outcomes such as network timeouts are not automatically resent. This bound applies to one provider invocation, not a promise that the entire business workflow completes within 60 or 65 seconds.
