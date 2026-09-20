# OWB v1.1 Dataset Card

- **Name**: OrgRebase Workspace Benchmark (OWB) Open Core.
- **Purpose**: evaluate task formation, authority/context governance, execution-born dependency capture, certified selective rebase, continuous successor graphs, safety and Skill governance.
- **Cases**: 192 deterministic synthetic open-core cases; optional 64-case secret-seed Challenge Protocol.
- **Organizations**: 12 isolated fictional B2B SaaS organizations; one ephemeral store per case.
- **Languages**: English and Chinese task paraphrases.
- **Personal data**: none; all identities, customers, policies and clauses are synthetic.
- **Restricted values**: separated into private synthetic Domain source pack; absent from public organization pack.
- **License**: PolyForm-Noncommercial-1.0.0 for project-generated artifacts. Commercial use requires a separate written license.
- **Gold access**: evaluator-only capability; never passed to Agents/models.
- **Generation**: fixed version/seed, Python standard library, canonical JSON and SHA-256 manifest.
- **Limitations**: template-bound B2B SaaS tasks; does not estimate production ROI, global connector coverage or arbitrary free-text attribution quality.
- **Open-data realism**: optional MIT-licensed Microsoft sample schema adapter; not part of canonical score.

## Optional realism adapters

OWB Core is fully synthetic and self-contained. Separate, non-scoring adapters may use Microsoft WideWorldImporters/AdventureWorks (MIT) for enterprise schema smoke tests and CUAD/ContractNLI (CC-BY-4.0) for Legal projection/entailment smoke tests. External data is never downloaded automatically, must be pinned and attributed, and cannot replace OWB dependency/impact/privacy gold.
