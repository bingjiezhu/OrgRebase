# Licensing

Project-owned OAC material in this tree is Apache License 2.0, including normative prose, schemas, code, tests and root project files. The text is in `LICENSES/Apache-2.0.txt`.

## 原先

OAC separated normative prose from executable interoperability assets.

| Paths | License |
|---|---|
| `standard/`, `docs/`, `specs/`, `ctk/protocol/`, Markdown prose under `profiles/`, root project prose (`README*`, `AGENTS.md`, `CONTRIBUTING.md`, `GOVERNANCE.md`, `NOTICE.md`, `PATENT-NON-ASSERTION.md`) | CC BY 4.0 |
| `src/`, `schemas/`, `tck/` except `ctk/protocol/`, `implementations/`, `tests/`, `scripts/`, machine-readable examples and fixtures, build/CI/editor metadata, `CITATION.cff`, `THIRD_PARTY.yml` | Apache License 2.0 |

## 现状

Those project-owned paths are Apache License 2.0. This repository is an experimental proposed draft, not a claim of recognition by a standards body.

## 为什么会有这样的更新

The workspace uses one OSI license, Apache-2.0, for project-owned OrgRebase and OAC files. `LICENSES/CC-BY-4.0.txt` remains the verbatim text for upstream dataset terms. Dataset material keeps its upstream license regardless of its local path and must be declared in `THIRD_PARTY.yml`.
