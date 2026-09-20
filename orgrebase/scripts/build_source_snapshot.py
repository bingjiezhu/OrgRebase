#!/usr/bin/env python3
"""Build and verify a deterministic two-repository release closure.

The source bundle is deliberately independent from the Python sdist.  It
copies only an explicit OrgRebase/OAC release allowlist: executable source,
contracts, current replay evidence and required tests.  Development Specs,
research experiment trees, unrelated old evidence generations, raw public
datasets and workbench material stay outside the archive; exact support
fixtures are admitted only when packaged runtime/tests bind them.  Symlinks,
special files, unsafe names, secrets and real machine-local paths fail closed.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

SCHEMA_VERSION = "orgrebase.source-snapshot.v3"
SNAPSHOT_ROOT_NAME = "source-snapshot"
ARCHIVE_NAME = "orgrebase-oac-source-snapshot.zip"
METADATA_NAME = "source-snapshot-metadata.json"
FIXED_ZIP_TIME = (2026, 8, 25, 8, 0, 0)
# Frozen identity used only to replay retained evidence, never to select the
# current runtime or source bundle.
HISTORICAL_TEAMHARNESS_LOCKS = {
    "sha256:d62e5470ad2d803d19fdc00b26958cb2d5f1e237b84870cf7c79464d75cb4888":
        "agentteams/historical/teamharness-v1.2.2.json",
}

EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".cache",
        ".git",
        ".hypothesis",
        ".mypy_cache",
        ".nox",
        ".orgrebase",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".tmp",
        ".venv",
        "__MACOSX",
        "__pycache__",
        "build",
        "cache",
        "dist",
        "node_modules",
        "venv",
    }
)
EXCLUDED_FILE_NAMES = frozenset({".coverage", ".DS_Store"})
SENSITIVE_CREDENTIAL_FILE_NAMES = frozenset(
    {
        ".netrc",
        ".npmrc",
        ".pypirc",
        "application_default_credentials.json",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
    }
)
DATABASE_SUFFIXES = (
    ".db",
    ".db-shm",
    ".db-wal",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
)
ORGREBASE_PRIVATE_PREFIXES = (
    "evidence/agentteams/debug",
    "evidence/agentteams/dispatch",
    "evidence/agentteams/dispatch-logs",
    "evidence/agentteams/live-sources",
    "evidence/agentteams/nonce-ledger",
    "evidence/agentteams/private-sessions",
    "evidence/goai-agentteams/latest/git-tool-repo",
    "evidence/latest/git-tool-repo",
    "evidence/semifinal-closure/archive",
    "evidence/semifinal-closure/failed",
    "evidence/semifinal-governed/archive",
    "evidence/semifinal-governed/failed",
    "evidence/skill-predecessor-rollback/archive",
)
# Authoring/submission material is a different delivery surface.  Keep the
# whole tree outside the executable source closure even if a future release
# prefix is accidentally broadened.  This is intentionally a prefix policy,
# not a filename blacklist.
ORGREBASE_ALWAYS_EXCLUDED_PREFIXES = ("submission",)
SEMIFINAL_PORTABLE_EVIDENCE_PREFIX = "evidence/semifinal-closure/latest"
GOLDEN_PILOT_PORTABLE_EVIDENCE_PREFIX = "evidence/golden-competition/latest/pilot"
CURRENT_GOLDEN_RUNTIME_REQUIRED_FILES = (
    "manifest.json",
    "verification.json",
    "state.json",
    "evidence-export.json",
    "quote-export.json",
    "golden-run/summary.json",
    "golden-run/agentteams/execution-plan.json",
    "golden-run/inputs/task-formation-decision-receipt.json",
    "golden-run/inputs/task-agent-context-envelope.json",
    "golden-run/tool/invocation.json",
    "golden-run/skill/receipt.json",
    "restart/completed-state-restart-receipt.json",
    "restart/completed-state-restart-verification.json",
    "restart/state-before.json",
    "restart/state-after.json",
)
AGENTTEAMS_RELEASE_RUNTIME_REQUIRED_FILES = (
    "run-agentteams-demo.sh",
    "verify-agentteams-demo.sh",
    "configs/goai-agentteams-demo.json",
    "examples/agentteams/change-request.json",
    "evidence/agentteams/public/historical-transport-v1.2.2.json",
    "evidence/agentteams/fresh-live/2026-08-25-561171ed039b/RUN-SUMMARY.json",
    "evidence/agentteams/fresh-live/2026-08-25-561171ed039b/live-receipt.json",
    "evidence/agentteams/fresh-live/2026-08-25-561171ed039b/semantic-ingestion.json",
    "evidence/agentteams/fresh-live/2026-08-25-561171ed039b/SHA256SUMS",
)
SEMIFINAL_PORTABLE_EVIDENCE_PREFIXES = (
    GOLDEN_PILOT_PORTABLE_EVIDENCE_PREFIX,
    SEMIFINAL_PORTABLE_EVIDENCE_PREFIX,
    "evidence/oac-quote-adaptation/latest",
    "evidence/semifinal-governed/latest",
    "evidence/public-process/latest",
    "evidence/skill-predecessor-rollback/latest",
    "evidence/semifinal-mvp/latest",
    "evidence/enterprise-quote-pilot/latest",
)
ORGREBASE_PRIVATE_EXACT_PATHS = frozenset(
    {
        "evidence/agentteams/candidate-ingestion-receipt.json",
        "evidence/agentteams/live-receipt.json",
        "evidence/agentteams/nonce-ledger.json",
        "evidence/agentteams/.nonce-ledger.json.lock",
        "evidence/agentteams/preflight.json",
        "evidence/agentteams/vertex-provider-probe.json",
    }
)

# The submission is a deliberately small release closure, not a mirror of the
# two development repositories.  A directory is traversed only when it is an
# ancestor of one of these prefixes or is itself an admitted prefix.
ORGREBASE_RELEASE_ROOT_FILES = frozenset(
    {
        ".gitignore",
        ".python-version",
        "CHANGELOG.md",
        "CODE_OF_CONDUCT.md",
        "COMMERCIAL-LICENSE.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "Makefile",
        "NOTICE.md",
        "README.md",
        "README.zh-CN.md",
        "RELEASE-VERIFICATION.md",
        "SECURITY.md",
        "pyproject.toml",
        "requirements-dev.txt",
        "requirements.txt",
        "uv.lock",
    }
)
ORGREBASE_RELEASE_TREE_PREFIXES = (
    "agentteams",
    "configs",
    "contracts",
    "demo",
    "examples",
    "fixtures",
    "orchestration",
    "schemas",
    "scripts",
    "skills",
    "src",
    "tests",
    "vendor/agentteams",
    "vendor/predecessors",
)
ORGREBASE_RELEASE_DOC_PREFIX = "docs"
ORGREBASE_RELEASE_DOC_EXCLUDED_PREFIXES = (
    "docs/specs",
    "docs/待做",
    "docs/archive",
    "docs/failed",
    "docs/experiments",
    "docs/workbench",
    "docs/工作台",
)
ORGREBASE_RELEASE_BENCHMARK_PREFIXES = (
    # Small attributed public input slice; raw downloads and local run databases stay outside releases.
    "benchmark/public-retail-quote/v1/README.md",
    "benchmark/public-retail-quote/v1/LICENSE.md",
    "benchmark/public-retail-quote/v1/dataset-manifest.json",
    "benchmark/public-retail-quote/v1/sample.json",
    # Required by the packaged runtime and current Golden verifier.
    "benchmark/orgworkbench",
    # ProductPath v0.1/v0.2 are immutable predecessors; v0.3 is the current
    # task-intake-bound successor.  All three are required to replay the
    # checked-in migration chain.  Do not broaden this to other generations.
    "benchmark/product-path-v0.1",
    "benchmark/product-path-v0.2-source-bound",
    "benchmark/product-path-v0.3-task-intake-bound",
    "benchmark/quote-value-v0.1",
    "benchmark/quote-value-v0.2-shadow",
    # Current public-process bridge fixture and its independently replayable
    # projection/receipt closure.  This is small source-bound evidence, not a
    # raw enterprise dataset.
    "benchmark/quote-value-v0.3-public-process",
    # Current public-real-process lane.  Raw downloads are intentionally not
    # admitted; only source metadata, the fixed projection and receipts are.
    "benchmark/quote-value-v0.4-bpi-real-process/LICENSES.json",
    "benchmark/quote-value-v0.4-bpi-real-process/MANIFEST.sha256",
    "benchmark/quote-value-v0.4-bpi-real-process/README.md",
    "benchmark/quote-value-v0.4-bpi-real-process/dataset-manifest.json",
    "benchmark/quote-value-v0.4-bpi-real-process/projection",
    "benchmark/quote-value-v0.4-bpi-real-process/retained",
)
ORGREBASE_RELEASE_EVIDENCE_PREFIXES = (
    "evidence/archive-revalidation/current",
    "evidence/golden-competition/latest/pilot",
    "evidence/oac-quote-adaptation/latest",
    "evidence/oac-public-real-process/latest",
    "evidence/public-process/latest",
    "evidence/public-real-process/latest",
    # Spec 067 successors.  Absence is valid while they are being built; once
    # present, only their current `latest` closure is admitted.
    "evidence/oac-agentic-adaptation/latest",
    "evidence/formation-taskflow/latest",
    # Sanitised AgentTeams compatibility evidence required by the packaged
    # GOAI reference entrypoint. Private root-level receipts remain denied.
    "evidence/agentteams/public",
    "evidence/agentteams/fresh-live/2026-08-25-561171ed039b",
    # Runtime read-model closure.  These four verifier-closed roots are
    # consumed directly by /api/semifinal/evidence after a clean extraction.
    # Omitting them makes the archive replay pass while the product cockpit
    # truthfully reports UNAVAILABLE, so they are part of the executable
    # product surface rather than authoring material.
    "evidence/semifinal-closure/latest",
    "evidence/semifinal-closure/supporting/quote-value-inputs",
    "evidence/semifinal-governed/latest",
    "evidence/semifinal-mvp/latest",
    "evidence/skill-predecessor-rollback/latest",
)
CORE_EVIDENCE_FILES = (
    "evidence/latest/manifest.json",
    "evidence/latest/demo.json",
    "evidence/latest/preview.json",
    "evidence/latest/impact-certificates.json",
    "evidence/latest/minimal-rebase-certificate.json",
    "evidence/latest/rebase-receipt.json",
    "evidence/latest/benchmark.json",
    "evidence/latest/conflict-receipt.json",
    "evidence/latest/failure-receipt.json",
    "evidence/latest/rollback-receipt.json",
    "evidence/latest/rollback-evidence.json",
    "evidence/latest/git-tool-evidence.json",
    "evidence/latest/observability.json",
    "evidence/latest/traces.otlp.json",
    "evidence/latest/logs.otlp.json",
    "evidence/latest/metrics.otlp.json",
    "evidence/latest/proof-pack.json",
)

DOCUMENTATION_SOURCE_FILES = (
    "documentation/pyproject.toml",
    "documentation/uv.lock",
    "documentation/mkdocs.yml",
    "documentation/build.py",
    "documentation/site.css",
    "documentation/overrides/main.html",
    "docs/DOCUMENTATION-SITE.md",
    ".github/workflows/docs.yml",
)

ORGREBASE_RELEASE_SUPPORT_FILES = (
    *DOCUMENTATION_SOURCE_FILES,
    *CORE_EVIDENCE_FILES,
    # Original guide bytes bound by the sealed historical MVP manifest.
    "evidence/semifinal-mvp/supporting/CONTRIBUTING.md",
    # Public contributor automation only; unrelated deployment workflows remain excluded.
    ".github/workflows/ci.yml",
    ".github/dependabot.yml",
    ".github/pull_request_template.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    # Required by Hatch's forced wheel include and therefore by `uv sync`.
    "evidence/release-facts.json",
    # Current workspace/ProductPath inputs remain separate from the fixed
    # historical QuoteValue snapshot above. Admit only this required support
    # closure; the rest of the mutable workspace evidence tree stays excluded.
    "evidence/workspace/latest/evaluation-suite.json",
    "evidence/workspace/latest/product-path-artifacts.json",
    "evidence/workspace/latest/product-path-blackbox.json",
    "evidence/workspace/latest/product-path-observations.json",
    "evidence/workspace/latest/product-path-wheel.whl",
    "evidence/workspace/latest/rebase/currency-minimal-rebase-certificate.json",
    "evidence/workspace/latest/rebase/launch-minimal-rebase-certificate.json",
    # Independent compensation mechanisms used by the product explanation.
    # They are deliberately exact files, not the whole evidence/latest tree
    # (which also contains a generated Git repository and unrelated history).
    "evidence/latest/failure-receipt.json",
    "evidence/latest/rollback-evidence.json",
    "evidence/latest/git-tool-evidence.json",
)
OAC_RELEASE_SUPPORT_FILES = (
    # The public-source manifest binds the exact SC-008 input pair and its
    # alternative accepted plan. Other experiment output is not a dependency.
    "experiments/plan-verification-portability/v0.1-seed-1/artifacts/SC-008/"
    "SC-008.change.json",
    "experiments/plan-verification-portability/v0.1-seed-1/artifacts/SC-008/"
    "veracier-proc01-contextual.snapshot.json",
    "experiments/plan-verification-portability/v0.1-seed-1/artifacts/plans/"
    "PV-POS-SC008-SPLIT.plan.json",
)
OAC_RELEASE_ROOT_FILES = frozenset(
    {
        ".editorconfig",
        ".gitattributes",
        ".gitignore",
        ".python-version",
        "CITATION.cff",
        "CHANGELOG.md",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "GOVERNANCE.md",
        "LICENSE.md",
        "Makefile",
        "NOTICE.md",
        "PATENT-NON-ASSERTION.md",
        "README.md",
        "README.zh-CN.md",
        "SECURITY.md",
        "THIRD_PARTY.yml",
        "pyproject.toml",
        "uv.lock",
    }
)
OAC_RELEASE_TREE_PREFIXES = (
    "LICENSES",
    "benchmark",
    "ctk",
    "docs",
    "implementations",
    "profiles",
    "schemas",
    "scripts",
    "src",
    "standard",
    "tck",
    "tests",
)

# Runtime delivery excludes retained evaluation archives, not installation resources.
ARCHIVE_REVALIDATION_PREFIX = "evidence/archive-revalidation/current"
RUNTIME_REVALIDATION_SUPPORT_FILES = (
    "benchmark/quote-value-v0.4-bpi-real-process/MANIFEST.sha256",
    "benchmark/quote-value-v0.4-bpi-real-process/LICENSES.json",
    "benchmark/quote-value-v0.4-bpi-real-process/dataset-manifest.json",
    "benchmark/quote-value-v0.4-bpi-real-process/projection/bpi2019-real-process-projection.json",
    "evidence/formation-taskflow/latest/inputs/execution-plan.json",
    "evidence/semifinal-closure/latest/summary.json",
    "evidence/semifinal-closure/latest/evidence-index.json",
    "evidence/semifinal-closure/latest/operations/tool/receipt.json",
    "evidence/semifinal-closure/latest/operations/tool/result.json",
    "evidence/semifinal-closure/latest/skills/quote-compose/input.json",
    "evidence/semifinal-closure/latest/skills/quote-compose/invocation-receipt.json",
    "evidence/semifinal-closure/latest/skills/quote-compose/result.json",
)

RUNTIME_PARITY_SUPPORT_FILES = (
    "evidence/golden-competition/latest/pilot/golden-run/summary.json",
)
RUNTIME_BENCHMARK_PREFIXES = (
    "benchmark/orgworkbench",
    "benchmark/quote-value-v0.1/public/current-process-baseline.json",
    "benchmark/public-retail-quote/v1/README.md",
    "benchmark/public-retail-quote/v1/LICENSE.md",
    "benchmark/public-retail-quote/v1/dataset-manifest.json",
    "benchmark/public-retail-quote/v1/sample.json",
)
RUNTIME_OAC_TREE_PREFIXES = (
    "LICENSES",
    "docs",
    "profiles",
    "schemas",
    "scripts",
    "src",
    "standard",
    "tck",
    "tests",
    "specs/009-proof-carrying-evolution-minimum-profile",
)
RUNTIME_OAC_SUPPORT_FILES = (
    "benchmark/data-readiness.json",
    "experiments/supplier-v02-portability/v0.2-seed-2/evidence-manifest.json",
    "experiments/plan-verification-portability/v0.1-seed-1/evidence-manifest.json",
    "experiments/evolution-minimum/v0.1-seed-1/evidence-manifest.json",
)
ENTRY_EN = """# OrgRebase executable source

[中文使用说明](README-FIRST.zh-CN.md)

OrgRebase carries enterprise rule changes through scoped candidates, independent
review, exact owner approval and canonical application. The archive contains both
`orgrebase/` and its required sibling `oac-spec/`, plus locked AgentTeams source.

## Install

Use CPython 3.12.13 and uv. Dependency installation needs network access.

```sh
(cd oac-spec && uv sync --locked --all-extras)
(cd orgrebase && uv sync --locked --all-extras)
```

For a model-free first run, follow the public transaction exercise in
`orgrebase/README.md`. For the native cloud-model journey, configure an authorized
Vertex project and credentials outside this source tree, then run:

```sh
(cd orgrebase && ORGREBASE_OAC_ROOT=../oac-spec ./run-semifinal-demo.sh live)
```

This uses controlled local task transport and explicitly configured cloud model
calls. It does not prove a distributed production deployment. Missing provider
configuration or failed calls must remain failures, not replaced with old results.
See the product README for supported models, authentication and operational options.
No model weights, credentials or prior workspace databases are supplied.

Read `orgrebase/docs/README.md` for architecture, usage and enterprise reuse.
The optional bilingual documentation site is rebuilt from the same Markdown using
its own locked toolchain; see `orgrebase/docs/DOCUMENTATION-SITE.md`. Generated sites
and documentation virtual environments are excluded from this source archive.

## Licensing

Keep the original LICENSE, NOTICE, THIRD_PARTY and dataset attribution files.
OrgRebase's source-available and commercial terms, OAC's path-specific terms and
third-party licenses remain unchanged. Original license texts control; this guide
and its translation do not create new rights. Customer deployment requires its own
identity, connector, business-value and capacity acceptance.
"""
ENTRY_ZH = """# OrgRebase 可执行源码使用说明

[English](README-FIRST.md)

OrgRebase 将企业规则变化落实为有范围的候选、独立复核、精确负责人批准和规范写入。
本包同时提供 `orgrebase/`、所需的并列 `oac-spec/` 及锁定的 AgentTeams 源码。

## 安装

使用 CPython 3.12.13 与 uv。首次安装依赖需要网络。

```sh
(cd oac-spec && uv sync --locked --all-extras)
(cd orgrebase && uv sync --locked --all-extras)
```

不需要模型的首跑见 `orgrebase/README.zh-CN.md` 的公开交易练习。
运行原生云模型场景前，在源码目录之外配置获授权的 Vertex 项目和凭据，然后执行：

```sh
(cd orgrebase && ORGREBASE_OAC_ROOT=../oac-spec ./run-semifinal-demo.sh live)
```

该路径使用受控本地任务传输和明确配置的云模型调用，不证明分布式生产部署。
缺少提供方配置或调用失败时应保留失败，不用历史结果替代。支持模型、身份配置及
运行选项见产品 README。本包不提供模型权重、凭据或既有工作区数据库。

架构、使用和企业复用从 `orgrebase/docs/README.md` 进入。可选双语文档站与现有
Markdown 共用内容，使用独立锁定工具链重建，见 `orgrebase/docs/DOCUMENTATION-SITE.md`。
生成站点和文档虚拟环境不包含在源码归档中。

## 许可

保留原始 LICENSE、NOTICE、THIRD_PARTY 及数据归属文件。OrgRebase 的源码可用和
商业条款、OAC 分路径条款及第三方许可均保持不变。许可原文具有权威性，本说明及
翻译不产生新的授权。客户部署还需独立完成身份、连接器、业务价值和容量验收。
"""
RUNTIME_SCOPE_EN = """
## Runtime profile

The package preserves installation, fresh business execution and `make check-core`
resources. It omits full historical evaluation archives and development material.
The exact Golden summary required by admission parity is a retained reference
fixture, not evidence generated by a new run. Unavailable historical cards are not
failed fresh business runs. Full archive-dependent checks require the full profile;
missing material is never a passing result.
The current archive-revalidation bundle and its exact small support closure are
retained separately. They bind rechecks to source and input hashes, have zero model
calls and do not claim to be the current business run. Original archives retain
their own identities; revalidation does not relabel their dates or permissions.
"""
RUNTIME_SCOPE_ZH = """
## Runtime 分发范围

本包保留安装、新业务执行和 `make check-core` 所需资源，不提供完整历史评测档案
及开发过程材料。准入 parity 所需的精确 Golden summary 是保留的参考夹具，不能
冒充新运行产生的证据。历史页面不可用不等于新业务执行失败。依赖完整历史档案的
检查需要 full 包，缺材料不能标为通过。
当前档案重验包及其精确支撑输入单独保留，绑定重验源码和输入摘要，模型调用为0，
不冒充当前业务运行。原档案保留自己的身份；重验不改写其日期或授权。
"""
RUNTIME_README = ENTRY_EN + RUNTIME_SCOPE_EN
RUNTIME_README_ZH = ENTRY_ZH + RUNTIME_SCOPE_ZH

DISTRIBUTION_README = """# OrgRebase source distribution

[English installation guide](README-FIRST.md) · [中文安装说明](README-FIRST.zh-CN.md)

OrgRebase carries changes in enterprise rules through scoped Agent tasks,
independent review, exact owner approval and verifiable application.

| Start here | Contents |
|---|---|
| [Product homepage](orgrebase/README.md) / [中文](orgrebase/README.zh-CN.md) | Introduction, architecture, Quick Start, Demo and development |
| [Documentation](orgrebase/docs/README.md) | Deployment, APIs, operations and reuse |
| [Contributing](orgrebase/CONTRIBUTING.md) | Development environment, checks and contribution terms |
| [OAC](oac-spec/README.md) | Organizational contracts, compiler and independent verification |
| [License index](LICENSES.md) | Component licenses and third-party attribution |

This is a paired source distribution, not a combined GitHub repository. Use
`orgrebase/` as the product repository root so its `.github/` workflows and
contribution templates apply. OAC remains a separately versioned component.
Keep this complete ZIP and its external metadata together as release assets.
The optional documentation site builds from the same product Markdown; follow
the [publishing guide](orgrebase/docs/guide/publishing.en.md).

The profile and exact file inventory are recorded in `source-snapshot-metadata.json`
beside the ZIP. Runtime-profile distributions support installation, a fresh
public-transaction run and `make check-core`; full archive-dependent checks need
the matching full-profile evidence. CI configuration is not a record of a CI run.
No Git history, credentials, model weights or customer databases are included.
"""

DISTRIBUTION_LICENSES = """# Component license index / 组件许可索引

This index grants no additional rights. Original license texts and notices apply
to their respective paths. 本索引不新增授权，各路径以原始许可证和通知为准。

| Component / 组件 | Terms and attribution / 条款与归属 |
|---|---|
| OrgRebase | [LICENSE](orgrebase/LICENSE), [commercial terms](orgrebase/COMMERCIAL-LICENSE.md), [NOTICE](orgrebase/NOTICE.md) |
| OAC | [License scope](oac-spec/LICENSE.md), [license texts](oac-spec/LICENSES/), [NOTICE](oac-spec/NOTICE.md), [third parties](oac-spec/THIRD_PARTY.yml) |
| AgentTeams source | [Source and upstream license](orgrebase/vendor/agentteams/README.md), [pinned source lock](orgrebase/agentteams/teamharness-lock.json) |
| Public retail sample | [Attribution and license](orgrebase/benchmark/public-retail-quote/v1/LICENSE.md) |
| Product dependencies and reuse | [Third-party inventory](orgrebase/docs/THIRD-PARTY-INVENTORY.md), [reuse guide](orgrebase/docs/REUSE-AND-LICENSING.md) |

OrgRebase uses PolyForm Noncommercial 1.0.0 with separate commercial licensing.
Publishing its source does not make it an unrestricted commercial-use license.
OAC and upstream components retain their own terms; this archive does not relicense
them. OrgRebase 商业用途需要独立授权；公开源代码不改变原有许可。
"""



def _require_profile(profile: str) -> None:
    if profile not in {"full", "runtime"}:
        raise SnapshotError("SOURCE_PROFILE_INVALID")


CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("openai_or_anthropic_key", re.compile(rb"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}\b")),
    ("google_api_key", re.compile(rb"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("google_oauth_access_token", re.compile(rb"\bya29\.[0-9A-Za-z_-]{20,}\b")),
    ("google_oauth_client_secret", re.compile(rb"\bGOCSPX-[0-9A-Za-z_-]{20,}\b")),
    ("aws_access_key", re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("github_fine_grained_token", re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("gitlab_token", re.compile(rb"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("slack_token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{16,}\b")),
    ("stripe_secret", re.compile(rb"\bsk_live_[A-Za-z0-9]{20,}\b")),
    (
        "pem_private_key",
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "bearer_value",
        re.compile(rb"\bBearer[ \t]+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE),
    ),
)
SYNTHETIC_CREDENTIAL_SENTINELS: dict[tuple[str, str], frozenset[bytes]] = {
    # Exact upstream test literals and documentation examples, including renamed history.
    ("agentteams-history/agentteams-controller/internal/auth/middleware_test.go", "bearer_value"): frozenset({b'Bearer worker-token'}),
    ("agentteams-history/agentteams-controller/internal/matrix/appservice_test.go", "bearer_value"): frozenset({b'Bearer test-as-token'}),
    ("agentteams-history/agentteams-controller/internal/matrix/client.go", "bearer_value"): frozenset({b'Bearer authentication'}),
    ("agentteams-history/agentteams-controller/internal/matrix/client_test.go", "bearer_value"): frozenset({b'Bearer creator-token'}),
    ("agentteams-history/agentteams-controller/internal/server/appservice_handler_test.go", "bearer_value"): frozenset({b'Bearer correct-token', b'Bearer test-hs-token'}),
    ("agentteams-history/agentteams-controller/internal/server/project_handler_test.go", "bearer_value"): frozenset({b'Bearer matrix-token'}),
    ("agentteams-history/hiclaw-controller/internal/auth/middleware_test.go", "bearer_value"): frozenset({b'Bearer worker-token'}),
    ("agentteams-history/hiclaw-controller/internal/matrix/appservice_test.go", "bearer_value"): frozenset({b'Bearer test-as-token'}),
    ("agentteams-history/hiclaw-controller/internal/matrix/client.go", "bearer_value"): frozenset({b'Bearer authentication'}),
    ("agentteams-history/hiclaw-controller/internal/matrix/client_test.go", "bearer_value"): frozenset({b'Bearer creator-token'}),
    ("agentteams-history/hiclaw-controller/internal/server/appservice_handler_test.go", "bearer_value"): frozenset({b'Bearer correct-token', b'Bearer test-hs-token'}),
    ("agentteams-history/hiclaw-controller/internal/server/project_handler_test.go", "bearer_value"): frozenset({b'Bearer matrix-token'}),
    ("agentteams-history/blog/agentteams-1.0.6-release.md", "bearer_value"): frozenset({b'Bearer eyJhbGciOiJSUzI1NiIs'}),
    ("agentteams-history/blog/zh-cn/agentteams-1.0.6-release.md", "bearer_value"): frozenset({b'Bearer eyJhbGciOiJSUzI1NiIs'}),
    ("agentteams-history/manager/tests/test-agentteams-find-skill.sh", "bearer_value"): frozenset({b'Bearer controller-token'}),
    ("agentteams-history/blog/hiclaw-1.0.6-release.md", "bearer_value"): frozenset({b'Bearer eyJhbGciOiJSUzI1NiIs'}),
    ("agentteams-history/blog/zh-cn/hiclaw-1.0.6-release.md", "bearer_value"): frozenset({b'Bearer eyJhbGciOiJSUzI1NiIs'}),
    ("agentteams-history/manager/tests/test-hiclaw-find-skill.sh", "bearer_value"): frozenset({b'Bearer controller-token'}),
    ("agentteams-history/qwenpaw/tests/test_heartbeat.py", "bearer_value"): frozenset({b'Bearer worker-token', b'Bearer file-token-2'}),
    ("agentteams-history/qwenpaw/tests/test_update.py", "bearer_value"): frozenset({b'Bearer gateway-secret'}),
    ("agentteams-history/shared/tests/test-oss-credentials.sh", "bearer_value"): frozenset({b'Bearer controller-token'}),
    ("agentteams-history/qwenpaw/tests/integration/test-update-runtime-config.sh", "bearer_value"): frozenset({b'Bearer fake-gateway-key'}),
    (
        "orgrebase/tests/test_http_capacity.py",
        "bearer_value",
    ): frozenset({b"Bearer private-bearer-token"}),
    (
        "orgrebase/tests/workspace/test_matrix_observation.py",
        "bearer_value",
    ): frozenset({b"Bearer private-test-token"}),
    (
        "orgrebase/tests/test_dispatch_fresh_core_agentteams_run.py",
        "bearer_value",
    ): frozenset(
        {
            b"Bearer must-not-enter-attempt-log",
            b"Bearer super-secret",
        }
    ),
    (
        "orgrebase/tests/test_competition_gates.py",
        "google_oauth_access_token",
    ): frozenset({b"ya29.must-not-leak-adc-token"}),
    (
        "orgrebase/scripts/verify_oac_agent_adaptation.py",
        "pem_private_key",
    ): frozenset({b"-----BEGIN PRIVATE KEY-----"}),
    (
        "orgrebase/tests/workspace/test_oac_agent_adaptation.py",
        "google_api_key",
    ): frozenset({b"AIza-secret-canary-must-never-persist"}),
    (
        "orgrebase/tests/workspace/test_model_adapter_boundaries.py",
        "bearer_value",
    ): frozenset({b"Bearer in-memory-test-key"}),
}
SNAPSHOT_BUILDER_RELATIVE = "orgrebase/scripts/build_source_snapshot.py"

FULL_REPLAY_COMMANDS = """
```sh
(cd orgrebase && make semifinal-mvp-check)
(cd orgrebase && python3 scripts/verify_golden_pilot_evidence.py --root evidence/golden-competition/latest/pilot)
(cd orgrebase && python3 scripts/verify_oac_quote_adaptation.py --root evidence/oac-quote-adaptation/latest --retained-build --project-root .)
(cd orgrebase && python3 scripts/verify_bpi2019_real_process_benchmark.py --project-root .)
(cd orgrebase && python3 scripts/verify_formation_taskflow_probe.py --evidence evidence/formation-taskflow/latest --lock agentteams/historical/teamharness-v1.2.2.json)
(cd orgrebase && python3 scripts/verify_bpi2019_oac_adaptation.py --evidence evidence/oac-public-real-process/latest --benchmark benchmark/quote-value-v0.4-bpi-real-process --config configs/oac/bpi2019-p2p-adaptation-v1.json)
```
"""
README_FIRST = ENTRY_EN + """
## Full archive profile

This profile also includes the allowlisted historical replay closures. Each archive
retains its own run, provider and source identity; it is not a fresh execution of
today's source. Raw public dataset downloads and private workspace databases are
excluded. Use these retained checks independently of the new business journey.
If this source ZIP is part of a larger delivery, follow its outer delivery README
for material verification; repository aggregate media checks are a separate scope.
""" + FULL_REPLAY_COMMANDS
README_FIRST_ZH = ENTRY_ZH + """
## Full 档案分发范围

此分发还包含白名单限定的历史重放闭包。每组档案保留自己的运行、提供方和源码
身份，不代表当前源码的新执行。公开数据原始下载和私有工作区数据库不包含在包中。
以下历史检查与新业务运行分别解释，不能混合结论。如本包属于更大的材料交付，
请按外层实际交付 README 核验材料；仓库中的媒体汇总检查属于另一范围。
""" + FULL_REPLAY_COMMANDS


MACHINE_LOCAL_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    (
        "macos_user_home",
        re.compile(rb"/" + rb"Users/[^/\s\"']+(?:/[^\s\"']+)*"),
    ),
    (
        "unix_user_home",
        re.compile(rb"/" + rb"home/[^/\s\"']+(?:/[^\s\"']+)*"),
    ),
    (
        "macos_private_tmp",
        re.compile(rb"/private/" + rb"tmp/[^\s\"']+"),
    ),
    (
        "unix_tmp",
        re.compile(rb"(?<!/private)/" + rb"tmp/[^\s\"']+"),
    ),
    (
        "macos_var_folders",
        re.compile(rb"/var/" + rb"folders/[^\s\"']+"),
    ),
    (
        "windows_user_home",
        re.compile(rb"[A-Za-z]:\\\\" + rb"Users\\\\[^\\\s\"']+(?:\\\\[^\s\"']+)*"),
    ),
)

# These values are synthetic rejection probes in test source, not runtime
# provenance.  Concatenation keeps the detector implementation from matching
# its own registry bytes.
SYNTHETIC_MACHINE_LOCAL_SENTINELS: dict[tuple[str, str], frozenset[bytes]] = {
    (
        "oac-spec/tests/test_plan_disagreement_minimization.py",
        "unix_tmp",
    ): frozenset({b"/" + b"tmp/history/manifest.json"}),
    (
        "orgrebase/tests/workspace/test_native_taskflow.py",
        "macos_private_tmp",
    ): frozenset(
        {
            b"/private/" + b"tmp/agentteams-v1.2.2.XpuETa",
            b"/private/" + b"tmp/agentteams-secret",
        }
    ),
    (
        "orgrebase/tests/workspace/test_native_taskflow.py",
        "macos_user_home",
    ): frozenset({b"/" + b"Users/alice/private-checkout"}),
    (
        "orgrebase/tests/workspace/test_native_taskflow.py",
        "unix_tmp",
    ): frozenset({b"/" + b"tmp/untracked-public-path"}),
    (
        "orgrebase/tests/workspace/test_semifinal_closure_safety.py",
        "macos_user_home",
    ): frozenset({b"/" + b"Users/example/private/stage"}),
}

# These are portable documentation placeholders, never observed host paths.
# They remain exact-path and exact-byte scoped so a changed command still
# fails closed.  The raw BPI artifact itself is not admitted to the release.
SYNTHETIC_DOCUMENTATION_PATH_SENTINELS: dict[tuple[str, str], frozenset[bytes]] = {
    ("orgrebase/docs/guide/publishing.en.md", "unix_tmp"): frozenset({b"/" + b"tmp/orgrebase-docs-new"}),
    ("orgrebase/docs/guide/publishing.zh.md", "unix_tmp"): frozenset({b"/" + b"tmp/orgrebase-docs-new"}),
    ("orgrebase/docs/DOCUMENTATION-SITE.md", "unix_tmp"): frozenset({
        b"/" + b"tmp/orgrebase-docs-v1", b"/" + b"tmp/orgrebase-docs-release",
    }),
    (
        "oac-spec/docs/validation/STATUS-2026-09-09-spec009.md",
        "unix_tmp",
    ): frozenset(
        {
            b"/" + b"tmp/oac-spec009-build",
            b"/" + b"tmp/oac-spec009-build/oac_contract-0.3.0a0-py3-none-any.whl",
            b"/" + b"tmp/oac-spec009-installed-replay.json",
        }
    ),
    (
        "orgrebase/benchmark/quote-value-v0.4-bpi-real-process/README.md",
        "unix_tmp",
    ): frozenset({b"/" + b"tmp/orgrebase-bpi2019-raw.xes"}),
    (
        "orgrebase/benchmark/quote-value-v0.4-bpi-real-process/dataset-manifest.json",
        "unix_tmp",
    ): frozenset({b"/" + b"tmp/orgrebase-bpi2019-raw.xes"}),
    (
        "orgrebase/scripts/data_adapters/build_bpi2019_real_process_projection.py",
        "unix_tmp",
    ): frozenset({b"/" + b"tmp/orgrebase-bpi2019-raw.xes"}),
    (
        "orgrebase/scripts/data_adapters/fetch_bpi_challenge_2019.py",
        "unix_tmp",
    ): frozenset({b"/" + b"tmp/orgrebase-bpi2019-raw.xes"}),
}


class SnapshotError(RuntimeError):
    """The requested snapshot violates the closed-world source contract."""


@dataclass(frozen=True)
class CopyResult:
    component: str
    files: int
    content_bytes: int
    excluded: tuple[dict[str, str], ...]
    synthetic_credential_sentinels: tuple[dict[str, Any], ...]


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sort_key(value: str) -> bytes:
    return value.encode("utf-8")


def _validate_root(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise SnapshotError(f"{label}_ROOT_IS_SYMLINK:{path}")
    resolved = path.resolve()
    if not resolved.is_dir():
        raise SnapshotError(f"{label}_ROOT_NOT_DIRECTORY:{path}")
    return resolved


def _validate_relative_path(relative: PurePosixPath) -> None:
    value = relative.as_posix()
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise SnapshotError(f"UNSAFE_SOURCE_PATH:{value!r}")


def _validate_zip_name(name: str) -> PurePosixPath:
    if not name or "\\" in name or any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise SnapshotError(f"ZIP_UNSAFE_PATH:{name!r}")
    is_directory = name.endswith("/")
    value = name[:-1] if is_directory else name
    pure = PurePosixPath(value)
    _validate_relative_path(pure)
    canonical = pure.as_posix() + ("/" if is_directory else "")
    if canonical != name or not name.startswith(SNAPSHOT_ROOT_NAME + "/"):
        raise SnapshotError(f"ZIP_UNSAFE_PATH:{name}")
    return pure


def _same_child_or_parent(key: str, prefix: str) -> bool:
    """Return true while walking to, at, or below an admitted prefix."""

    return key == prefix or key.startswith(prefix + "/") or prefix.startswith(key + "/")


def _release_allowlist_reason(
    relative: PurePosixPath,
    component: str,
    *,
    is_dir: bool,
    profile: str = "full",
) -> str | None:
    """Apply the explicit source-release closure before generic exclusions."""

    del is_dir  # Files and directories use the same path-prefix closure.
    key = relative.as_posix()
    _require_profile(profile)
    if profile == "runtime":
        if component == "oac-spec" and any(
            _same_child_or_parent(key, path)
            for path in (*OAC_RELEASE_SUPPORT_FILES, *RUNTIME_OAC_SUPPORT_FILES)
        ):
            return None
        if component == "orgrebase" and relative.parts[0] in {"benchmark", "evidence"}:
            prefixes = (*RUNTIME_BENCHMARK_PREFIXES, *RUNTIME_PARITY_SUPPORT_FILES, *RUNTIME_REVALIDATION_SUPPORT_FILES,
                        ARCHIVE_REVALIDATION_PREFIX, *CORE_EVIDENCE_FILES, "evidence/release-facts.json")
            return None if any(_same_child_or_parent(key, item) for item in prefixes) else "historical_archive_not_in_runtime_profile"
        if component == "oac-spec" and any(
            _same_child_or_parent(key, item) for item in RUNTIME_OAC_TREE_PREFIXES
        ):
            return None
        if component == "oac-spec" and len(relative.parts) > 1:
            return "not_in_runtime_profile"
        if component == "oac-spec" and key in {"ctk", "benchmark", "implementations", "experiments"}:
            return "not_in_runtime_profile"
    if component == "orgrebase":
        if any(
            key == prefix or key.startswith(prefix + "/") for prefix in ORGREBASE_ALWAYS_EXCLUDED_PREFIXES
        ):
            return "separate_submission_delivery_surface"
        if len(relative.parts) == 1 and (
            key in ORGREBASE_RELEASE_ROOT_FILES
            or (key.startswith("run-") and key.endswith(".sh"))
            or (key.startswith("verify-") and key.endswith(".sh"))
        ):
            return None
        if any(_same_child_or_parent(key, prefix) for prefix in ORGREBASE_RELEASE_TREE_PREFIXES):
            return None
        if _same_child_or_parent(key, ORGREBASE_RELEASE_DOC_PREFIX):
            if any(
                key == prefix or key.startswith(prefix + "/")
                for prefix in ORGREBASE_RELEASE_DOC_EXCLUDED_PREFIXES
            ):
                return "internal_or_future_documentation"
            return None
        if any(_same_child_or_parent(key, prefix) for prefix in ORGREBASE_RELEASE_BENCHMARK_PREFIXES):
            return None
        if any(_same_child_or_parent(key, prefix) for prefix in ORGREBASE_RELEASE_EVIDENCE_PREFIXES):
            return None
        if any(_same_child_or_parent(key, path) for path in ORGREBASE_RELEASE_SUPPORT_FILES):
            return None
        return "not_in_release_allowlist"

    if component == "oac-spec":
        if len(relative.parts) == 1 and key in OAC_RELEASE_ROOT_FILES:
            return None
        if any(_same_child_or_parent(key, prefix) for prefix in OAC_RELEASE_TREE_PREFIXES):
            return None
        if any(_same_child_or_parent(key, path) for path in OAC_RELEASE_SUPPORT_FILES):
            return None
        return "not_in_release_allowlist"
    raise SnapshotError(f"UNKNOWN_COMPONENT:{component}")


def _exclusion_reason(relative: PurePosixPath, component: str, *, is_dir: bool, profile: str = "full") -> str | None:
    key = relative.as_posix()
    allowlist_reason = _release_allowlist_reason(
        relative,
        component,
        is_dir=is_dir, profile=profile,
    )
    if allowlist_reason is not None:
        return allowlist_reason
    # No admitted evidence tree can bypass environment/private credential names.
    if any(part == ".env" or part.startswith(".env.") or part.endswith(".env") for part in relative.parts):
        return "environment_file"
    _validate_noncredential_path(relative, component)
    if (component == "orgrebase" and key.startswith(ARCHIVE_REVALIDATION_PREFIX + "/")
            and not is_dir and relative.name.lower().endswith(DATABASE_SUFFIXES)):
        return "private_revalidation_runtime_database"
    if component == "orgrebase" and any(
        key == prefix or key.startswith(prefix + "/") for prefix in ORGREBASE_RELEASE_EVIDENCE_PREFIXES
    ):
        # Verifier-closed semifinal operations packs may intentionally retain
        # SQLite query evidence.  The public Golden is different: its v3
        # manifest deliberately proves completion and restart from
        # content-addressed JSON projections and forbids runtime databases.
        if (
            not is_dir
            and (
                key == GOLDEN_PILOT_PORTABLE_EVIDENCE_PREFIX
                or key.startswith(GOLDEN_PILOT_PORTABLE_EVIDENCE_PREFIX + "/")
            )
            and relative.name.lower().endswith(DATABASE_SUFFIXES)
        ):
            return "private_golden_runtime_database"
        if (
            not is_dir
            and not (
                key == GOLDEN_PILOT_PORTABLE_EVIDENCE_PREFIX
                or key.startswith(GOLDEN_PILOT_PORTABLE_EVIDENCE_PREFIX + "/")
            )
            and relative.name.lower().endswith(
                (
                    ".db-shm",
                    ".db-wal",
                    ".sqlite-shm",
                    ".sqlite-wal",
                    ".sqlite3-shm",
                    ".sqlite3-wal",
                )
            )
        ):
            return "generated_database_journal"
        return None
    if any(part in EXCLUDED_DIRECTORY_NAMES or part.endswith(".egg-info") for part in relative.parts):
        return "generated_or_cache_directory"
    name = relative.name
    if name in EXCLUDED_FILE_NAMES or name.startswith(".coverage."):
        return "generated_file"
    if name == ".env" or name.startswith(".env.") or name.endswith(".env"):
        return "environment_file"
    if not is_dir and (name.endswith((".pyc", ".pyo")) or name.lower().endswith(DATABASE_SUFFIXES)):
        return "generated_or_database_file"
    if component == "orgrebase":
        if key in ORGREBASE_PRIVATE_EXACT_PATHS:
            return "private_runtime_or_submission_media"
        if any(key == prefix or key.startswith(prefix + "/") for prefix in ORGREBASE_PRIVATE_PREFIXES):
            return "private_runtime_evidence"
        if key.startswith("evidence/agentteams/"):
            runtime_parts = relative.parts[2:]
            if any(
                part
                in {
                    ".nonce-ledger.json.lock",
                    "dispatch-logs",
                    "git-tool-repo",
                    "live-sources",
                    "nonce-ledger.json",
                    "private-sessions",
                }
                or part.startswith(("debug", "dispatch-"))
                for part in runtime_parts
            ):
                return "private_runtime_evidence"
    return None


def _validate_noncredential_path(relative: PurePosixPath, component: str) -> None:
    name = relative.name.lower()
    if (
        name in SENSITIVE_CREDENTIAL_FILE_NAMES
        or name.endswith((".key", ".p12", ".pfx"))
        or ("service-account" in name and name.endswith(".json"))
    ):
        raise SnapshotError(f"CREDENTIAL_FILE_REJECTED:{component}/{relative.as_posix()}")


def _credential_scan(relative: str, raw: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    sentinel_relative = relative.removeprefix(SNAPSHOT_ROOT_NAME + "/")
    violations: list[str] = []
    sentinels: list[dict[str, Any]] = []
    for pattern_id, pattern in CREDENTIAL_PATTERNS:
        matches = pattern.findall(raw)
        if not matches:
            continue
        allowed = SYNTHETIC_CREDENTIAL_SENTINELS.get((sentinel_relative, pattern_id), frozenset())
        if sentinel_relative == SNAPSHOT_BUILDER_RELATIVE:
            allowed = frozenset().union(
                allowed,
                *(
                    values
                    for (_, registered_pattern_id), values in SYNTHETIC_CREDENTIAL_SENTINELS.items()
                    if registered_pattern_id == pattern_id
                ),
            )
        actual = [match for match in matches if match not in allowed]
        synthetic = len(matches) - len(actual)
        if actual:
            violations.append(f"CREDENTIAL_PATTERN:{pattern_id}:{relative}:{len(actual)}")
        if synthetic:
            sentinels.append({"path": relative, "patternId": pattern_id, "matches": synthetic})
    return violations, sentinels


def _scan_nested_archive(relative: str, raw: bytes, *, depth: int = 0) -> None:
    """Inspect ZIP/wheel members without extracting or printing matched values."""
    if not relative.lower().endswith((".zip", ".whl")):
        return
    if depth >= 4:
        raise SnapshotError(f"NESTED_ARCHIVE_DEPTH:{relative}")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if sum(item.file_size for item in members) > 256 * 1024 * 1024:
                raise SnapshotError(f"NESTED_ARCHIVE_SIZE:{relative}")
            names: set[str] = set()
            for item in members:
                path = PurePosixPath(item.filename)
                _validate_relative_path(path)
                if item.filename in names:
                    raise SnapshotError(f"NESTED_ARCHIVE_DUPLICATE:{relative}:{item.filename}")
                names.add(item.filename)
                if stat.S_ISLNK(item.external_attr >> 16):
                    raise SnapshotError(f"NESTED_ARCHIVE_SYMLINK:{relative}:{item.filename}")
                _validate_noncredential_path(path, relative)
                if any(part == ".env" or part.startswith(".env.") or part.endswith(".env") for part in path.parts):
                    raise SnapshotError(f"NESTED_CREDENTIAL_FILE:{relative}:{item.filename}")
                if item.is_dir():
                    continue
                value = archive.read(item)
                label = f"{relative}!/{item.filename}"
                violations, _ = _credential_scan(label, value)
                if violations:
                    raise SnapshotError(";".join(violations))
                _scan_nested_archive(label, value, depth=depth + 1)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, SnapshotError):
            raise
        raise SnapshotError(f"NESTED_ARCHIVE_INVALID:{relative}") from exc


def _scan_git_history(checkout: Path) -> dict[str, Any]:
    """Scan every reachable blob, including deleted files in bundled history."""
    result = subprocess.run(["git", "rev-list", "--objects", "--all"], cwd=checkout,
                            check=True, capture_output=True, timeout=120)
    objects = [line.split(b" ", 1) for line in result.stdout.splitlines()]
    request = b"".join(item[0] + b"\n" for item in objects)
    content = subprocess.run(["git", "cat-file", "--batch"], cwd=checkout, input=request,
                             check=True, capture_output=True, timeout=120).stdout
    offset = 0
    count = 0
    synthetic: list[dict[str, Any]] = []
    for entry in objects:
        end = content.index(b"\n", offset)
        oid, kind, size = content[offset:end].split()
        offset = end + 1
        raw = content[offset:offset + int(size)]
        offset += int(size) + 1
        if kind != b"blob":
            continue
        path = entry[1].decode("utf-8", errors="replace") if len(entry) == 2 else oid.decode()
        label = "agentteams-history/" + path
        _validate_noncredential_path(PurePosixPath(path), "agentteams-history")
        if any(part == ".env" or part.startswith(".env.") or part.endswith(".env") for part in PurePosixPath(path).parts):
            raise SnapshotError(f"GIT_HISTORY_CREDENTIAL_FILE:{path}")
        violations, allowed = _credential_scan(label, raw)
        synthetic.extend(allowed)
        if violations:
            raise SnapshotError(";".join(violations))
        _scan_nested_archive(label, raw)
        count += 1
    return {"status": "PASS", "reachableBlobsScanned": count, "scope": "ALL_BUNDLED_REACHABLE_BLOBS",
            "syntheticCredentialSentinels": synthetic, "matchedValuesDisclosed": False}


def _machine_local_path_scan(relative: str, raw: bytes) -> tuple[list[str], list[dict[str, Any]], bool]:
    """Reject every real host path; only exact non-runtime sentinels survive.

    The scanner never rewrites bytes.  In particular, an immutable evidence
    receipt with a host path fails the build instead of being made to look
    portable.  The returned boolean remains part of the v3 audit contract;
    admitted references are exact test/documentation sentinels and therefore
    can never be tool inputs.
    """

    sentinel_relative = relative.removeprefix(SNAPSHOT_ROOT_NAME + "/")
    matches_by_pattern: dict[str, list[bytes]] = {
        pattern_id: [match.group(0) for match in pattern.finditer(raw)]
        for pattern_id, pattern in MACHINE_LOCAL_PATTERNS
    }
    matches_by_pattern = {
        pattern_id: matches for pattern_id, matches in matches_by_pattern.items() if matches
    }
    if not matches_by_pattern:
        return [], [], False

    violations: list[str] = []
    references: list[dict[str, Any]] = []
    for pattern_id, matches in matches_by_pattern.items():
        allowed_synthetic = SYNTHETIC_MACHINE_LOCAL_SENTINELS.get(
            (sentinel_relative, pattern_id), frozenset()
        )
        allowed_documentation = SYNTHETIC_DOCUMENTATION_PATH_SENTINELS.get(
            (sentinel_relative, pattern_id), frozenset()
        )
        synthetic_count = sum(match in allowed_synthetic for match in matches)
        documentation_count = sum(match in allowed_documentation for match in matches)
        unregistered = [
            match
            for match in matches
            if match not in allowed_synthetic and match not in allowed_documentation
        ]
        if unregistered:
            violations.append(f"MACHINE_LOCAL_PATH:{pattern_id}:{relative}:{len(unregistered)}")
        if synthetic_count:
            references.append(
                {
                    "path": relative,
                    "patternId": pattern_id,
                    "matches": synthetic_count,
                    "classification": "SYNTHETIC_TEST_SENTINEL",
                    "usedAsToolInput": False,
                }
            )
        if documentation_count:
            references.append(
                {
                    "path": relative,
                    "patternId": pattern_id,
                    "matches": documentation_count,
                    "classification": "SYNTHETIC_DOCUMENTATION_PLACEHOLDER",
                    "usedAsToolInput": False,
                }
            )
    return violations, references, False


def _write_regular_file(path: Path, raw: bytes, *, executable: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    path.chmod(0o755 if executable else 0o644)


def _copy_component(source: Path, destination: Path, component: str, *, profile: str = "full") -> CopyResult:
    if destination.exists():
        raise SnapshotError(f"DESTINATION_ALREADY_EXISTS:{destination}")
    destination.mkdir(parents=True, mode=0o755)
    files = 0
    content_bytes = 0
    exclusions: list[dict[str, str]] = []
    sentinels: list[dict[str, Any]] = []

    def visit(source_dir: Path, destination_dir: Path, relative_dir: PurePosixPath) -> None:
        nonlocal files, content_bytes
        with os.scandir(source_dir) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name.encode("utf-8"))
        for entry in entries:
            relative = relative_dir / entry.name
            _validate_relative_path(relative)
            if entry.is_symlink():
                raise SnapshotError(f"SYMLINK_REJECTED:{component}/{relative.as_posix()}")
            is_dir = entry.is_dir(follow_symlinks=False)
            if not is_dir and not entry.is_file(follow_symlinks=False):
                raise SnapshotError(f"SPECIAL_FILE_REJECTED:{component}/{relative.as_posix()}")
            reason = _exclusion_reason(relative, component, is_dir=is_dir, profile=profile)
            if reason is not None:
                exclusions.append({"path": relative.as_posix(), "reason": reason})
                continue
            _validate_noncredential_path(relative, component)
            source_path = Path(entry.path)
            if is_dir:
                destination_path = destination_dir / entry.name
                destination_path.mkdir(mode=0o755)
                visit(source_path, destination_path, relative)
                continue
            before = entry.stat(follow_symlinks=False)
            raw = source_path.read_bytes()
            after = source_path.stat(follow_symlinks=False)
            if not stat.S_ISREG(after.st_mode) or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise SnapshotError(f"SOURCE_CHANGED_DURING_READ:{component}/{relative.as_posix()}")
            bundle_relative = f"{component}/{relative.as_posix()}"
            credential_violations, synthetic = _credential_scan(bundle_relative, raw)
            if credential_violations:
                raise SnapshotError(";".join(credential_violations))
            sentinels.extend(synthetic)
            _scan_nested_archive(bundle_relative, raw)
            _write_regular_file(
                destination_dir / entry.name,
                raw,
                executable=bool(before.st_mode & 0o111),
            )
            files += 1
            content_bytes += len(raw)

    visit(source, destination, PurePosixPath())
    return CopyResult(
        component=component,
        files=files,
        content_bytes=content_bytes,
        excluded=tuple(exclusions),
        synthetic_credential_sentinels=tuple(sentinels),
    )


def _inventory(root: Path) -> tuple[dict[str, Any], ...]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: _sort_key(item.relative_to(root).as_posix())):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise SnapshotError(f"INVENTORY_SYMLINK_REJECTED:{relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise SnapshotError(f"INVENTORY_SPECIAL_FILE_REJECTED:{relative}")
        entries.append(
            {
                "path": relative,
                "sizeBytes": path.stat().st_size,
                "sha256": _sha256_file(path),
                "executable": bool(path.stat().st_mode & 0o111),
            }
        )
    return tuple(entries)


def _inventory_digest(entries: Iterable[dict[str, Any]]) -> str:
    payload = json.dumps(tuple(entries), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return _sha256_bytes(payload)


def _build_zip(snapshot_root: Path, archive_path: Path) -> dict[str, int]:
    if archive_path.exists():
        raise SnapshotError(f"ARCHIVE_ALREADY_EXISTS:{archive_path}")
    paths = [
        snapshot_root,
        *sorted(
            snapshot_root.rglob("*"),
            key=lambda item: _sort_key(item.relative_to(snapshot_root.parent).as_posix()),
        ),
    ]
    files = 0
    directories = 0
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in paths:
            if path.is_symlink():
                raise SnapshotError(f"ZIP_SYMLINK_REJECTED:{path}")
            relative = path.relative_to(snapshot_root.parent).as_posix()
            info = zipfile.ZipInfo(date_time=FIXED_ZIP_TIME)
            info.create_system = 3
            if path.is_dir():
                info.filename = relative.rstrip("/") + "/"
                info.external_attr = (stat.S_IFDIR | 0o755) << 16 | 0x10
                archive.writestr(info, b"")
                directories += 1
            elif path.is_file():
                info.filename = relative
                info.compress_type = zipfile.ZIP_DEFLATED
                mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(info, path.read_bytes(), compresslevel=9)
                files += 1
            else:
                raise SnapshotError(f"ZIP_SPECIAL_FILE_REJECTED:{relative}")
    return {"fileEntries": files, "directoryEntries": directories, "totalEntries": files + directories}


def _audit_zip(archive_path: Path, *, profile: str = "full") -> dict[str, Any]:
    names: set[str] = set()
    files = 0
    directories = 0
    synthetic: list[dict[str, Any]] = []
    machine_local_references: list[dict[str, Any]] = []
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            name = info.filename
            if name in names:
                raise SnapshotError(f"ZIP_DUPLICATE_ENTRY:{name}")
            names.add(name)
            pure = _validate_zip_name(name)
            if len(pure.parts) >= 3 and pure.parts[1] in {"orgrebase", "oac-spec"}:
                component = pure.parts[1]
                component_relative = PurePosixPath(*pure.parts[2:])
                reason = _exclusion_reason(component_relative, component, is_dir=info.is_dir(), profile=profile)
                if reason is not None:
                    raise SnapshotError(f"ZIP_FORBIDDEN_PATH:{name}:{reason}")
                _validate_noncredential_path(component_relative, component)
            mode = (info.external_attr >> 16) & 0xFFFF
            if info.is_dir():
                if mode and not stat.S_ISDIR(mode):
                    raise SnapshotError(f"ZIP_DIRECTORY_MODE_INVALID:{name}")
                directories += 1
                continue
            if not stat.S_ISREG(mode):
                raise SnapshotError(f"ZIP_NON_REGULAR_ENTRY:{name}")
            raw = archive.read(info)
            violations, allowed = _credential_scan(name, raw)
            if violations:
                raise SnapshotError(";".join(violations))
            synthetic.extend(allowed)
            _scan_nested_archive(name, raw)
            path_violations, path_references, _ = _machine_local_path_scan(name, raw)
            if path_violations:
                raise SnapshotError(";".join(path_violations))
            machine_local_references.extend(path_references)
            files += 1
        bad_crc = archive.testzip()
    if bad_crc is not None:
        raise SnapshotError(f"ZIP_BAD_CRC:{bad_crc}")
    machine_local_tool_input_references = sum(
        int(bool(item["usedAsToolInput"])) for item in machine_local_references
    )
    if machine_local_tool_input_references:
        # Defensive invariant: today no admitted class can set this flag.  A
        # future class must define a new release policy instead of silently
        # weakening the existing one.
        raise SnapshotError("MACHINE_LOCAL_PATH_TOOL_INPUT_REFERENCE")
    return {
        "status": "PASS",
        "fileEntries": files,
        "directoryEntries": directories,
        "duplicateEntries": [],
        "unsafePaths": [],
        "forbiddenPaths": [],
        "symlinkOrSpecialEntries": [],
        "credentialMatches": [],
        "syntheticCredentialSentinels": sorted(
            synthetic,
            key=lambda item: (_sort_key(str(item["path"])), str(item["patternId"])),
        ),
        "machineLocalPathPolicy": {
            "status": "PASS_NO_REAL_HOST_PATH_EXCEPTIONS",
            "unregisteredReferences": 0,
            "realHostPathExceptions": 0,
            "immutableEvidencePathExceptions": 0,
            "toolInputReferences": 0,
            "allRealHostReferencesFailClosed": True,
            "syntheticSentinelsOnly": True,
            "contentRewriteMode": "NONE_ORIGINAL_BYTES_ONLY",
            "evidenceBytesRewritten": False,
        },
        "machineLocalPathReferences": sorted(
            machine_local_references,
            key=lambda item: (
                _sort_key(str(item["path"])),
                str(item["patternId"]),
                str(item["classification"]),
            ),
        ),
        "machineLocalPathExceptions": [],
        "machineLocalPathsUsedAsToolInputs": False,
        "badCrcEntry": None,
    }


def _safe_extract(archive_path: Path, destination: Path) -> Path:
    if destination.exists():
        raise SnapshotError(f"EXTRACT_DESTINATION_ALREADY_EXISTS:{destination}")
    destination.mkdir(parents=True, mode=0o755)
    root = destination.resolve()
    names: set[str] = set()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            name = info.filename
            if name in names:
                raise SnapshotError(f"ZIP_DUPLICATE_ENTRY:{name}")
            names.add(name)
            pure = _validate_zip_name(name)
            target = root.joinpath(*pure.parts)
            resolved_target = target.resolve()
            if resolved_target != root and root not in resolved_target.parents:
                raise SnapshotError(f"ZIP_PATH_ESCAPE:{name}")
            mode = (info.external_attr >> 16) & 0xFFFF
            if info.is_dir():
                if mode and not stat.S_ISDIR(mode):
                    raise SnapshotError(f"ZIP_DIRECTORY_MODE_INVALID:{name}")
                target.mkdir(parents=True, exist_ok=True, mode=0o755)
                continue
            if not stat.S_ISREG(mode):
                raise SnapshotError(f"ZIP_NON_REGULAR_ENTRY:{name}")
            _write_regular_file(target, archive.read(info), executable=bool(mode & 0o111))
    extracted = destination / SNAPSHOT_ROOT_NAME
    if not extracted.is_dir():
        raise SnapshotError("ZIP_SNAPSHOT_ROOT_MISSING")
    return extracted


def _compare_inventories(
    expected: tuple[dict[str, Any], ...],
    actual: tuple[dict[str, Any], ...],
    *,
    reason: str,
) -> dict[str, Any]:
    expected_by_path = {str(item["path"]): item for item in expected}
    actual_by_path = {str(item["path"]): item for item in actual}
    missing = sorted(set(expected_by_path) - set(actual_by_path), key=_sort_key)
    extra = sorted(set(actual_by_path) - set(expected_by_path), key=_sort_key)
    changed = sorted(
        (
            path
            for path in set(expected_by_path).intersection(actual_by_path)
            if expected_by_path[path] != actual_by_path[path]
        ),
        key=_sort_key,
    )
    if missing or extra or changed:
        raise SnapshotError(f"{reason}:missing={missing!r}:extra={extra!r}:changed={changed!r}")
    return {
        "status": "PASS",
        "expectedFiles": len(expected),
        "actualFiles": len(actual),
        "missing": [],
        "extra": [],
        "changed": [],
        "exactByteMatch": True,
    }


def _prepare_snapshot(
    orgrebase_root: Path, oac_root: Path, snapshot_root: Path, *, profile: str = "full"
) -> tuple[CopyResult, CopyResult]:
    snapshot_root.mkdir(parents=True, mode=0o755)
    org_result = _copy_component(orgrebase_root, snapshot_root / "orgrebase", "orgrebase", profile=profile)
    oac_result = _copy_component(oac_root, snapshot_root / "oac-spec", "oac-spec", profile=profile)
    _write_regular_file(snapshot_root / "README-FIRST.md", (README_FIRST if profile == "full" else RUNTIME_README).encode(), executable=False)
    _write_regular_file(snapshot_root / "README-FIRST.zh-CN.md", (README_FIRST_ZH if profile == "full" else RUNTIME_README_ZH).encode(), executable=False)
    _write_regular_file(snapshot_root / "README.md", DISTRIBUTION_README.encode(), executable=False)
    _write_regular_file(snapshot_root / "LICENSES.md", DISTRIBUTION_LICENSES.encode(), executable=False)
    return org_result, oac_result


class _ConsoleAssetReferences(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes: dict[str, str | None] = {}
        for name, value in attrs:
            # Browsers keep the first occurrence of a duplicate HTML attribute.
            attributes.setdefault(name, value)
        if tag == "base" and "href" in attributes:
            raise SnapshotError("CONSOLE_ASSET_BASE_URL_UNSUPPORTED")
        reference = None
        if tag == "script":
            reference = attributes.get("src")
        elif tag == "link" and "stylesheet" in (attributes.get("rel") or "").casefold().split():
            reference = attributes.get("href")
        if reference is not None:
            self.references.append(reference)


def _console_asset_paths(index: Path) -> tuple[str, ...]:
    """Resolve HTML script and stylesheet dependencies against the real /assets mount."""

    parser = _ConsoleAssetReferences()
    try:
        parser.feed(index.read_text(encoding="utf-8"))
        parser.close()
    except (OSError, UnicodeError) as exc:
        raise SnapshotError("CONSOLE_INDEX_UNREADABLE") from exc
    paths: set[str] = set()
    for reference in parser.references:
        try:
            url = urlsplit(reference)
            if url.netloc and url.scheme in {"", "http", "https"}:
                # An external dependency is not a file supplied by this archive.
                continue
            if url.scheme:
                raise ValueError("unsupported asset URL scheme")
            decoded = unquote(url.path, errors="strict")
            if ".." in decoded.split("/") or "\\" in decoded:
                raise ValueError("unsafe asset path")
            mounted = decoded.removeprefix("/").removeprefix("./")
            if not mounted.startswith("assets/"):
                raise ValueError("asset is outside the static mount")
            relative = PurePosixPath(mounted.removeprefix("assets/"))
            _validate_relative_path(relative)
        except (ValueError, UnicodeError) as exc:
            raise SnapshotError("CONSOLE_ASSET_PATH_INVALID") from exc
        paths.add(relative.as_posix())
    return tuple(sorted(paths, key=_sort_key))


def _verify_runtime_profile_closure(snapshot_root: Path) -> dict[str, Any]:
    required = ["README.md", "LICENSES.md", "README-FIRST.md", "README-FIRST.zh-CN.md"]
    for component in ("orgrebase", "oac-spec"):
        root = snapshot_root / component
        pyproject = root / "pyproject.toml"
        if not pyproject.is_file():
            continue  # Small source fixtures exercise the common packaging machinery.
        config = tomllib.loads(pyproject.read_text())
        includes = config.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {}).get("force-include", {})
        for source in includes:
            path = PurePosixPath(source)
            _validate_relative_path(path)
            target = root.joinpath(*path.parts)
            if not target.exists() or (target.is_dir() and not any(target.rglob("*"))):
                raise SnapshotError(f"RUNTIME_INSTALL_RESOURCE_MISSING:{component}/{source}")
        required.extend(f"{component}/{name}" for name in ("README.md", "uv.lock"))
        if component == "orgrebase":
            required.extend(f"orgrebase/{name}" for name in (
                "LICENSE", "NOTICE.md", "COMMERCIAL-LICENSE.md", "CONTRIBUTING.md",
                "run-semifinal-demo.sh", "run-enterprise-pilot.sh", "scripts/fetch_pinned_agentteams.py",
                "agentteams/teamharness-lock.json", "scripts/run_public_quote_replay.py",
                "benchmark/public-retail-quote/v1/sample.json", "benchmark/public-retail-quote/v1/LICENSE.md",
            ))
            wire_path = root / "src/orgrebase/workspace/oac_wire.py"
            try:
                manifest_paths = next(
                    ast.literal_eval(node.value)
                    for node in ast.parse(wire_path.read_text()).body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "_OAC_PUBLIC_SOURCE_PATHS" for target in node.targets)
                )
            except (OSError, SyntaxError, ValueError, StopIteration) as exc:
                raise SnapshotError("RUNTIME_OAC_PUBLIC_SOURCE_MANIFEST_INVALID") from exc
            if not isinstance(manifest_paths, tuple) or not manifest_paths:
                raise SnapshotError("RUNTIME_OAC_PUBLIC_SOURCE_MANIFEST_INVALID")
            for relative in manifest_paths:
                if not isinstance(relative, str):
                    raise SnapshotError("RUNTIME_OAC_PUBLIC_SOURCE_MANIFEST_INVALID")
                _validate_relative_path(PurePosixPath(relative))
                required.append("oac-spec/" + relative)
            required.extend("orgrebase/" + name for name in (*CORE_EVIDENCE_FILES, *RUNTIME_PARITY_SUPPORT_FILES))
            required.append("orgrebase/" + _read_agentteams_source_lock(root)["offline_bundle"]["path"])
            required.extend("orgrebase/demo/console/" + item for item in _console_asset_paths(root / "demo/console/index.html"))
        else:
            required.extend(f"oac-spec/{name}" for name in ("LICENSE.md", "NOTICE.md", "THIRD_PARTY.yml"))
    missing = [name for name in required if not (snapshot_root / name).is_file()]
    if missing:
        raise SnapshotError(f"RUNTIME_PROFILE_CLOSURE_INCOMPLETE:{missing!r}")
    return {"status": "PASS", "mode": "SOURCE_RUNTIME_INSTALL_CLOSURE", "requiredFileCount": len(required),
            "historicalArchiveReplay": "NOT_INCLUDED", "freshNativeExecution": "NOT_RUN_BY_PACKAGER",
            "modelWeightsPackaged": False, "sourceContentPolicy": "COPY_ORIGINAL_BYTES_ONLY"}


def _verify_archive_revalidation_closure(snapshot_root: Path) -> dict[str, Any]:
    """Bind the optional current recheck to its exact producer, input and output files."""
    project = snapshot_root / "orgrebase"
    bundle = project / ARCHIVE_REVALIDATION_PREFIX
    if not bundle.exists():
        if (project / "src/orgrebase/archive_revalidation.py").exists():
            raise SnapshotError("ARCHIVE_REVALIDATION_BUNDLE_MISSING")
        return {"status": "NOT_INCLUDED"}
    try:
        manifest = json.loads((bundle / "manifest.json").read_text())
        body = {key: value for key, value in manifest.items() if key != "digest"}
        digest = "sha256:" + _sha256_bytes(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                                     separators=(",", ":"), allow_nan=False).encode())
        if (manifest.get("schema_version") != "orgrebase.archive-revalidation.v1"
                or manifest.get("digest") != digest or manifest.get("live_model_calls") != 0
                or set(manifest.get("lanes", {})) != {"bpi", "formation", "skill", "owb"}):
            raise SnapshotError("ARCHIVE_REVALIDATION_MANIFEST_INVALID")

        def check_files(root: Path, records: Any) -> set[str]:
            if not isinstance(records, dict) or not records:
                raise SnapshotError("ARCHIVE_REVALIDATION_FILE_SET_EMPTY")
            for name, expected in records.items():
                if not isinstance(name, str) or PurePosixPath(name).as_posix() != name:
                    raise SnapshotError("ARCHIVE_REVALIDATION_PATH_INVALID")
                relative = PurePosixPath(name)
                _validate_relative_path(relative)
                path = root / name
                if path.is_symlink() or not path.is_file():
                    raise SnapshotError(f"ARCHIVE_REVALIDATION_FILE_MISSING:{name}")
                if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                    raise SnapshotError("ARCHIVE_REVALIDATION_FILE_DIGEST_INVALID")
                if _sha256_file(path) != expected:
                    raise SnapshotError(f"ARCHIVE_REVALIDATION_FILE_DIGEST_MISMATCH:{name}")
            return set(records)

        implementation = check_files(project, manifest["implementation_files"])
        inputs: set[str] = set()
        outputs = {"manifest.json"}
        for lane, definition in manifest["lanes"].items():
            inputs.update(check_files(project, definition["inputs"]))
            files = check_files(bundle, definition["files"])
            if any(not name.startswith(lane + "/") for name in files) or lane + "/verification.json" not in files:
                raise SnapshotError("ARCHIVE_REVALIDATION_LANE_BINDING_INVALID")
            report = json.loads((bundle / lane / "verification.json").read_text())
            if report.get("status") != "PASS" or report.get("failures", []):
                raise SnapshotError(f"ARCHIVE_REVALIDATION_REPORT_NOT_PASS:{lane}")
            outputs.update(files)
        actual = {path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if path.is_file()}
        if actual != outputs:
            raise SnapshotError("ARCHIVE_REVALIDATION_OUTPUT_FILE_SET_MISMATCH")
        return {"status": "PASS", "mode": "EXACT_REVALIDATION_FILE_CLOSURE",
                "manifestDigest": manifest["digest"], "implementationFiles": len(implementation),
                "inputFiles": len(inputs), "outputFiles": len(outputs), "liveModelCalls": 0,
                "currentBusinessRun": False, "semanticScope": "Each lane retains its own verifier and limitations"}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise SnapshotError("ARCHIVE_REVALIDATION_CLOSURE_INVALID") from exc


def _verify_documentation_source_closure(snapshot_root: Path) -> dict[str, Any]:
    """Optional documentation tooling must remain rebuildable outside a checkout."""
    root = snapshot_root / "orgrebase"
    if not (root / "documentation").exists():
        return {"status": "NOT_INCLUDED"}
    missing = [name for name in DOCUMENTATION_SOURCE_FILES if not (root / name).is_file()]
    if missing:
        raise SnapshotError(f"DOCUMENTATION_SOURCE_CLOSURE_INCOMPLETE:{missing!r}")
    return {"status": "PASS", "sourceFiles": list(DOCUMENTATION_SOURCE_FILES),
            "dependencyScope": "OPTIONAL_SEPARATE_DOCUMENTATION_TOOLCHAIN",
            "siteBuild": "NOT_RUN_BY_SOURCE_PACKAGER", "generatedSitePackaged": False}


def _verify_release_runtime_closure(snapshot_root: Path, *, profile: str = "full") -> dict[str, Any]:
    """Check the extracted release can replay every retained evidence lane."""

    documentation = _verify_documentation_source_closure(snapshot_root)
    revalidation = _verify_archive_revalidation_closure(snapshot_root)
    if profile == "runtime":
        return {**_verify_runtime_profile_closure(snapshot_root), "documentation": documentation,
                "archiveRevalidation": revalidation}
    required: list[str] = ["README-FIRST.md", "README-FIRST.zh-CN.md"]
    org = snapshot_root / "orgrebase"
    oac = snapshot_root / "oac-spec"
    product_release = (org / "pyproject.toml").is_file()
    oac_release = (oac / "pyproject.toml").is_file()
    agentic_release = (org / "evidence/oac-agentic-adaptation/latest").is_dir()
    formation_release = (org / "evidence/formation-taskflow/latest").is_dir()
    oac_public_real_release = (org / "evidence/oac-public-real-process/latest").is_dir()
    if product_release:
        required.extend(
            [
                "orgrebase/README.md",
                "orgrebase/pyproject.toml",
                "orgrebase/uv.lock",
                "orgrebase/run-semifinal-demo.sh",
                "orgrebase/run-enterprise-pilot.sh",
                *(
                    f"orgrebase/{relative}"
                    for relative in AGENTTEAMS_RELEASE_RUNTIME_REQUIRED_FILES
                ),
                "orgrebase/src/orgrebase/__init__.py",
                "orgrebase/src/orgrebase/cli.py",
                "orgrebase/src/orgrebase/semifinal_view.py",
                "orgrebase/src/orgrebase/workspace/competition_run.py",
                "orgrebase/src/orgrebase/workspace/oac_quote_adaptation.py",
                "orgrebase/demo/console/index.html",
                "orgrebase/scripts/verify_golden_pilot_evidence.py",
                "orgrebase/scripts/verify_completed_state_restart_receipt.py",
                "orgrebase/scripts/verify_oac_quote_adaptation.py",
                "orgrebase/scripts/verify_bpi2019_real_process_benchmark.py",
                "orgrebase/examples/enterprise-quote-pilot/evergreen/pack.json",
                "orgrebase/agentteams/teamharness-lock.json",
                "orgrebase/agentteams/source-lock.json",
                "orgrebase/" + _read_agentteams_source_lock(org)["offline_bundle"]["path"],
                (
                    "orgrebase/vendor/predecessors/enterprise-quote-compose/1.3.0/"
                    "orgrebase-0.4.0-py3-none-any.whl"
                ),
                "orgrebase/evidence/release-facts.json",
                "orgrebase/benchmark/product-path-v0.1/MANIFEST.sha256",
                (
                    "orgrebase/benchmark/product-path-v0.2-source-bound/"
                    "BENCHMARK-MIGRATION.json"
                ),
                (
                    "orgrebase/benchmark/product-path-v0.2-source-bound/"
                    "MANIFEST.sha256"
                ),
                (
                    "orgrebase/benchmark/product-path-v0.3-task-intake-bound/"
                    "BENCHMARK-MIGRATION.json"
                ),
                (
                    "orgrebase/benchmark/product-path-v0.3-task-intake-bound/"
                    "MANIFEST.sha256"
                ),
                "orgrebase/evidence/workspace/latest/product-path-artifacts.json",
                "orgrebase/evidence/workspace/latest/product-path-blackbox.json",
                "orgrebase/evidence/workspace/latest/product-path-observations.json",
                "orgrebase/evidence/workspace/latest/product-path-wheel.whl",
                *(
                    f"orgrebase/{GOLDEN_PILOT_PORTABLE_EVIDENCE_PREFIX}/{relative}"
                    for relative in CURRENT_GOLDEN_RUNTIME_REQUIRED_FILES
                ),
                "orgrebase/evidence/oac-quote-adaptation/latest/manifest.json",
                "orgrebase/evidence/public-process/latest/public-process-bridge-receipt.json",
                "orgrebase/benchmark/quote-value-v0.3-public-process/MANIFEST.sha256",
                "orgrebase/evidence/public-real-process/latest/summary.json",
                ("orgrebase/benchmark/quote-value-v0.4-bpi-real-process/dataset-manifest.json"),
                (
                    "orgrebase/benchmark/quote-value-v0.4-bpi-real-process/"
                    "projection/bpi2019-real-process-projection.json"
                ),
                (
                    "orgrebase/benchmark/quote-value-v0.4-bpi-real-process/"
                    "retained/bpi2019-real-process-verification.json"
                ),
            ]
        )
        if agentic_release:
            required.extend(
                [
                    "orgrebase/evidence/oac-agentic-adaptation/latest/manifest.json",
                    "orgrebase/evidence/oac-agentic-adaptation/latest/mapping-receipt.json",
                    "orgrebase/scripts/verify_oac_agent_adaptation.py",
                ]
            )
        if formation_release:
            required.extend(
                [
                    "orgrebase/evidence/formation-taskflow/latest/probe-receipt.json",
                    "orgrebase/evidence/formation-taskflow/latest/verification.json",
                    "orgrebase/evidence/formation-taskflow/latest/inputs/execution-plan.json",
                    "orgrebase/scripts/verify_formation_taskflow_probe.py",
                ]
            )
        if oac_public_real_release:
            required.extend(
                [
                    "orgrebase/evidence/oac-public-real-process/latest/manifest.json",
                    "orgrebase/evidence/oac-public-real-process/latest/adaptation-receipt.json",
                    (
                        "orgrebase/evidence/oac-public-real-process/latest/"
                        "typed-scope-execution/bpi2019-real-process-verification.json"
                    ),
                    "orgrebase/scripts/verify_bpi2019_oac_adaptation.py",
                    "orgrebase/configs/oac/bpi2019-p2p-adaptation-v1.json",
                ]
            )
    if oac_release:
        required.extend(
            [
                "oac-spec/README.md",
                "oac-spec/pyproject.toml",
                "oac-spec/uv.lock",
                "oac-spec/src/oac/__init__.py",
                "oac-spec/src/oac/cli.py",
                "oac-spec/standard/oac-core-v0.1.md",
                "oac-spec/standard/oac-conformance-v0.1.md",
                "oac-spec/schemas/index.json",
                "oac-spec/schemas/OrganizationSnapshot.schema.json",
                "oac-spec/schemas/OrganizationalDemand.schema.json",
                "oac-spec/tck/manifest.json",
                "oac-spec/ctk/README.md",
                *(f"oac-spec/{path}" for path in OAC_RELEASE_SUPPORT_FILES),
            ]
        )
    console_index = org / "demo/console/index.html"
    if console_index.is_file():
        required.extend(
            f"orgrebase/demo/console/{relative}"
            for relative in _console_asset_paths(console_index)
        )
    contribution_template = ".github/pull_request_template.md"
    if any(
        (org / name).is_file()
        and contribution_template in (org / name).read_text(encoding="utf-8")
        for name in ("README.md", "README.zh-CN.md", "CONTRIBUTING.md")
    ):
        required.append("orgrebase/.github/pull_request_template.md")
    missing = sorted(
        (relative for relative in required if not snapshot_root.joinpath(relative).is_file()),
        key=_sort_key,
    )
    if missing:
        raise SnapshotError(f"RELEASE_RUNTIME_CLOSURE_INCOMPLETE:{missing!r}")

    paths = tuple(item["path"] for item in _inventory(snapshot_root))
    forbidden_prefixes = (
        "orgrebase/docs/specs/",
        "orgrebase/docs/待做/",
        "orgrebase/submission/",
        "oac-spec/dist/",
        "oac-spec/experiments/",
        "oac-spec/specs/",
    )
    forbidden = sorted(
        (
            path
            for path in paths
            if (
                any(path.startswith(prefix) for prefix in forbidden_prefixes)
                and path
                not in {f"oac-spec/{relative}" for relative in OAC_RELEASE_SUPPORT_FILES}
            )
            or path.endswith("/BPI_Challenge_2019.xes")
            or "/quote-value-v0.4-bpi-real-process/raw/" in path
        ),
        key=_sort_key,
    )
    if forbidden:
        raise SnapshotError(f"RELEASE_RUNTIME_CLOSURE_FORBIDDEN:{forbidden!r}")
    return {
        "status": "PASS",
        "mode": "LOCKED_NO_CLOUD_MODEL_REPLAY_CLOSURE",
        "documentation": documentation,
        "archiveRevalidation": revalidation,
        "productReleaseDetected": product_release,
        "oacReleaseDetected": oac_release,
        "requiredFileCount": len(required),
        "missing": [],
        "forbidden": [],
        "goldenReplay": product_release,
        "oacAdaptationReplay": product_release and oac_release,
        "publicProcessBridgeReplay": product_release,
        "productPathFixedPointReplay": product_release,
        "bpi2019Replay": product_release,
        "agenticAdaptationReplay": agentic_release,
        "oacPublicRealAdaptationReplay": oac_public_real_release,
        "rawBpiDatasetPackaged": False,
    }


def _verify_semifinal_publication(orgrebase_root: Path) -> dict[str, Any]:
    evidence = orgrebase_root / SEMIFINAL_PORTABLE_EVIDENCE_PREFIX
    script = orgrebase_root / "scripts" / "verify_semifinal_closure.py"
    if not evidence.exists():
        if script.is_file():
            raise SnapshotError("SEMIFINAL_PUBLICATION_REQUIRED")
        return {"status": "NOT_PRESENT"}
    if not evidence.is_dir() or not (evidence / "evidence-index.json").is_file() or not script.is_file():
        raise SnapshotError("SEMIFINAL_PUBLICATION_INCOMPLETE")
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    source_path = str(orgrebase_root / "src")
    environment["PYTHONPATH"] = (
        source_path
        if not environment.get("PYTHONPATH")
        else source_path + os.pathsep + environment["PYTHONPATH"]
    )
    try:
        native = json.loads((evidence / "agentteams/lifecycle-receipt.json").read_text(encoding="utf-8"))
        lock_path, source_lock, source_scope = _retained_agentteams_source_lock(
            orgrebase_root, native.get("source_verification", {}).get("source_lock_digest", ""),
        )
        completed = subprocess.run(
            [sys.executable, str(script), "--evidence", str(evidence), "--retained-build",
             "--retained-quote-value-inputs",
             str(orgrebase_root / "evidence/semifinal-closure/supporting/quote-value-inputs"),
             "--lock", str(lock_path)],
            cwd=orgrebase_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        result = json.loads(completed.stdout) if completed.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SnapshotError("SEMIFINAL_PUBLICATION_VERIFY_FAILED") from exc
    if (
        not isinstance(result, dict)
        or result.get("status") != "PASS"
        or result.get("evidence_class") != "CONTROLLED_LOCAL_INTEGRATED_VERTICAL_SLICE"
        or result.get("native_verification_strength") != "RETAINED_LOCK_REPLAY"
        or result.get("verification_scope") != "RETAINED_ARTIFACT"
        or result.get("current_release_qualified") is not False
        or result.get("canonical_target_writes") != 0
        or result.get("production_readiness") is not False
        or result.get("agentteams_commit") != source_lock["commit"]
        or result.get("source_lock_digest") != _agentteams_lock_digest(source_lock)
    ):
        raise SnapshotError("SEMIFINAL_PUBLICATION_VERIFY_FAILED")
    return {
        "agentTeamsVersion": source_lock["tag"],
        "agentTeamsCommit": source_lock["commit"],
        "sourceVerificationScope": source_scope,
        **{
            key: result[key]
            for key in (
                "status",
                "evidence_class",
                "native_verification_strength",
                "terminal_state",
                "canonical_target_writes",
                "production_readiness",
                "verification_scope",
                "current_release_qualified",
                "pack_digest",
            )
        },
    }


def _verify_semifinal_mvp_publication(orgrebase_root: Path) -> dict[str, Any]:
    """Replay the evaluator-facing manifest without mutating retained evidence."""

    evidence = orgrebase_root / "evidence/semifinal-mvp/latest"
    summary_path = evidence / "summary.json"
    script = orgrebase_root / "scripts/verify_semifinal_mvp_manifest.py"
    if not evidence.exists() and not script.is_file():
        return {"status": "NOT_PRESENT"}
    if not evidence.is_dir() or not summary_path.is_file() or not script.is_file():
        raise SnapshotError("SEMIFINAL_MVP_PUBLICATION_INCOMPLETE")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="orgrebase-semifinal-mvp-verify-") as temporary:
            verification_path = Path(temporary) / "verification.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    str(evidence),
                    "--output",
                    str(verification_path),
                ],
                cwd=orgrebase_root,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            result = json.loads(completed.stdout) if completed.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SnapshotError("SEMIFINAL_MVP_PUBLICATION_VERIFY_FAILED") from exc
    totals = summary.get("completion_totals") if isinstance(summary, dict) else None
    if (
        not isinstance(result, dict)
        or result.get("status") != "PASS"
        or result.get("checked_claim_ceiling") != "SEMIFINAL_MVP_CONTROLLED_LOCAL_NOT_PRODUCTION"
        or result.get("production_ready") is not False
        or result.get("source_pack_count") != 4
        or not isinstance(summary, dict)
        or summary.get("status") != "PASS"
        or summary.get("production_ready") is not False
        or totals
        != {
            "recommendation_count": 28,
            "validated_count": 19,
            "not_run_count": 9,
        }
        or result.get("manifest_digest") != summary.get("digest")
    ):
        raise SnapshotError("SEMIFINAL_MVP_PUBLICATION_VERIFY_FAILED")
    return {
        "status": "PASS",
        "manifestDigest": result["manifest_digest"],
        "verificationDigest": result["digest"],
        "checkedClaimCeiling": result["checked_claim_ceiling"],
        "sourcePackCount": result["source_pack_count"],
        "completionTotals": totals,
        "productionReady": False,
    }


def _verify_semifinal_runtime_projection(orgrebase_root: Path) -> dict[str, Any]:
    """Load the exact read model used by the shipped semifinal cockpit.

    Replaying individual evidence packs is necessary but not sufficient: the
    product endpoint must also be able to assemble them after clean extraction.
    This check stays read-only and returns a compact, deterministic projection
    rather than copying UI state into the release metadata.
    """

    module = orgrebase_root / "src/orgrebase/semifinal_view.py"
    if not module.is_file():
        return {"status": "NOT_PRESENT"}
    code = r'''
import json
from pathlib import Path
from orgrebase.semifinal_view import semifinal_evidence_view

value = semifinal_evidence_view(Path.cwd())
formation = value.get("agent_collaboration", {}).get("formation_taskflow", {})
operations = value.get("operations", {})
recovery = value.get("recovery", {})
skills = value.get("skills", {})
print(json.dumps({
    "status": value.get("status"),
    "runId": value.get("run_id"),
    "agentTeamsActionCount": value.get("agent_collaboration", {}).get("action_count"),
    "skillCount": len(skills) if isinstance(skills, (dict, list)) else None,
    "formation": {
        "status": formation.get("status"),
        "plannedDomainCount": formation.get("planned_domain_count"),
        "actualDomainCount": formation.get("actual_agentteams_domain_count"),
        "topologyMatch": formation.get("topology_match"),
        "candidateOnly": formation.get("candidate_only"),
        "canonicalTargetWrites": formation.get("canonical_target_writes"),
    },
    "operationsKeys": sorted(operations) if isinstance(operations, dict) else [],
    "sigkillCount": recovery.get("sigkill_count"),
}, sort_keys=True))
'''
    environment = dict(os.environ)
    source_path = str(orgrebase_root / "src")
    environment["PYTHONPATH"] = (
        source_path
        if not environment.get("PYTHONPATH")
        else source_path + os.pathsep + environment["PYTHONPATH"]
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=orgrebase_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        result = json.loads(completed.stdout) if completed.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SnapshotError("SEMIFINAL_RUNTIME_PROJECTION_VERIFY_FAILED") from exc
    expected_operations = {
        "alert",
        "backup",
        "capacity",
        "connectors",
        "enterprise_readiness",
        "otlp",
        "query",
        "retention",
    }
    formation = result.get("formation") if isinstance(result, dict) else None
    if not (
        isinstance(result, dict)
        and result.get("status") == "PASS"
        and result.get("runId") == "run:orgrebase:semifinal-closure:quote-001"
        and result.get("agentTeamsActionCount") == 34
        and result.get("skillCount") == 3
        and isinstance(formation, dict)
        and formation
        == {
            "status": "PASS",
            "plannedDomainCount": 2,
            "actualDomainCount": 2,
            "topologyMatch": True,
            "candidateOnly": True,
            "canonicalTargetWrites": 0,
        }
        and set(result.get("operationsKeys", [])) == expected_operations
        and result.get("sigkillCount") == 2
    ):
        raise SnapshotError("SEMIFINAL_RUNTIME_PROJECTION_VERIFY_FAILED")
    return result


def _verify_compensation_publication(orgrebase_root: Path) -> dict[str, Any]:
    """Verify the three honest compensation boundaries shipped with the UI."""

    evidence = orgrebase_root / "evidence/latest"
    paths = {
        "failure": evidence / "failure-receipt.json",
        "rollback": evidence / "rollback-evidence.json",
        "git": evidence / "git-tool-evidence.json",
    }
    if not any(path.exists() for path in paths.values()):
        return {"status": "NOT_PRESENT"}
    if not all(path.is_file() for path in paths.values()):
        raise SnapshotError("COMPENSATION_PUBLICATION_INCOMPLETE")
    try:
        failure = json.loads(paths["failure"].read_text(encoding="utf-8"))
        rollback = json.loads(paths["rollback"].read_text(encoding="utf-8"))
        git = json.loads(paths["git"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError("COMPENSATION_PUBLICATION_INVALID") from exc
    rollback_receipt = rollback.get("receipt") if isinstance(rollback, dict) else None
    git_saga = git.get("compensation_saga") if isinstance(git, dict) else None
    git_verification = git.get("verification") if isinstance(git, dict) else None
    patch_receipt = git.get("patch", {}).get("receipt") if isinstance(git, dict) else None
    compensation_receipt = (
        git.get("compensation", {}).get("receipt") if isinstance(git, dict) else None
    )
    if not (
        isinstance(failure, dict)
        and failure.get("status") == "REJECTED_BEFORE_WRITE"
        and failure.get("target_writes") == 0
        and failure.get("before_state_digest") == failure.get("after_state_digest")
        and isinstance(rollback_receipt, dict)
        and rollback_receipt.get("status") == "ROLLED_BACK_PENDING_REBASE"
        and rollback_receipt.get("authoritative_claim_unchanged") is True
        and all(
            item.get("status") == "SUCCEEDED"
            for item in rollback_receipt.get("compensation_results", [])
        )
        and isinstance(git_saga, dict)
        and git_saga.get("status") == "ROLLED_BACK_PENDING_REBASE"
        and git_saga.get("residual_effects") == []
        and isinstance(patch_receipt, dict)
        and patch_receipt.get("status") == "SUCCEEDED"
        and isinstance(compensation_receipt, dict)
        and compensation_receipt.get("status") == "SUCCEEDED"
        and isinstance(git_verification, dict)
        and git_verification.get("status") == "PASS"
        and git_verification.get("worktree_clean") is True
        and git.get("evidence_boundary") == "LOCAL_REAL_TOOL"
    ):
        raise SnapshotError("COMPENSATION_PUBLICATION_INVALID")
    return {
        "status": "PASS",
        "canonicalPrewriteRejection": "VALIDATED_CONTROLLED_LOCAL",
        "downstreamStateCompensation": "VALIDATED_LOCAL_DETERMINISTIC_SEPARATE_RUN",
        "reversibleGitCompensation": "VALIDATED_LOCAL_REAL_TOOL_SEPARATE_RUN",
        "realEnterpriseConnectorCompensation": "NOT_RUN",
        "gitResidualEffectCount": 0,
        "failureReceiptSha256": _sha256_file(paths["failure"]),
        "rollbackEvidenceSha256": _sha256_file(paths["rollback"]),
        "gitEvidenceSha256": _sha256_file(paths["git"]),
    }


def _verify_golden_pilot_publication(orgrebase_root: Path) -> dict[str, Any]:
    """Independently verify the exact Spec 060 Golden evidence closure."""

    evidence = orgrebase_root / GOLDEN_PILOT_PORTABLE_EVIDENCE_PREFIX
    manifest_path = evidence / "manifest.json"
    script = orgrebase_root / "scripts/verify_golden_pilot_evidence.py"
    if not evidence.exists() and not script.is_file():
        return {"status": "NOT_PRESENT"}
    if not evidence.is_dir() or not manifest_path.is_file() or not script.is_file():
        raise SnapshotError("GOLDEN_PILOT_PUBLICATION_INCOMPLETE")
    try:
        completed = subprocess.run(
            [sys.executable, str(script), "--root", str(evidence)],
            cwd=orgrebase_root,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        result = json.loads(completed.stdout) if completed.returncode == 0 else None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SnapshotError("GOLDEN_PILOT_PUBLICATION_VERIFY_FAILED") from exc
    if (
        not isinstance(result, dict)
        or result.get("status") != "PASS"
        or result.get("verification_mode") != "STDLIB_ONLY_NO_PRODUCT_IMPORTS"
        or result.get("product_imports") != 0
        or result.get("causal_verification") != "PASS"
        or result.get("experience_verification") != "PASS"
        or result.get("entry_count") != manifest.get("files", {}).get("entry_count")
        or result.get("pack_digest") != manifest.get("files", {}).get("pack_digest")
        or result.get("run_id") != manifest.get("run_id")
        or manifest.get("status") != "PASS"
    ):
        raise SnapshotError("GOLDEN_PILOT_PUBLICATION_VERIFY_FAILED")
    return {
        "status": "PASS",
        "verificationMode": result["verification_mode"],
        "productImports": 0,
        "causalVerification": "PASS",
        "experienceVerification": "PASS",
        "runId": result["run_id"],
        "correlationId": manifest["correlation_id"],
        "entryCount": result["entry_count"],
        "packDigest": result["pack_digest"],
        "manifestDigest": manifest["digest"],
        "productionReady": False,
    }


def _verify_oac_reference_publication(orgrebase_root: Path) -> dict[str, Any]:
    """Independently replay the separate 51-entry OAC reference closure."""

    evidence = orgrebase_root / "evidence/oac-evolution/latest"
    summary_path = evidence / "summary.json"
    script = orgrebase_root / "scripts/verify_oac_evolution_evidence.py"
    wheel_root = orgrebase_root / "evidence/oac-evolution/wheel-check"
    wheel_pack = wheel_root / "pack"
    wheel_check_path = wheel_root / "wheel-check.json"
    if not evidence.exists() and not script.is_file():
        return {"status": "NOT_PRESENT"}
    if not all(
        (
            evidence.is_dir(),
            summary_path.is_file(),
            script.is_file(),
            wheel_pack.is_dir(),
            wheel_check_path.is_file(),
        )
    ):
        raise SnapshotError("OAC_REFERENCE_PUBLICATION_INCOMPLETE")
    try:
        canonical_run = subprocess.run(
            [sys.executable, str(script), str(evidence)],
            cwd=orgrebase_root,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        wheel_run = subprocess.run(
            [sys.executable, str(script), str(wheel_pack)],
            cwd=orgrebase_root,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        canonical = json.loads(canonical_run.stdout) if canonical_run.returncode == 0 else None
        wheel_evaluation = json.loads(wheel_run.stdout) if wheel_run.returncode == 0 else None
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        wheel_check = json.loads(wheel_check_path.read_text(encoding="utf-8"))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SnapshotError("OAC_REFERENCE_PUBLICATION_VERIFY_FAILED") from exc
    expected_boundaries = {
        "data": "SYNTHETIC_FIXTURE",
        "assurance": "SYNTHETIC_CONTROLLED_PROCESS_ASSURANCE_ONLY",
        "observation": "SYNTHETIC_CONTROLLED_OBSERVATION_NOT_EXTERNAL_GROUND_TRUTH",
        "runtime_evidence": "ZERO_EFFECT_HANDLER_COMPLETION_NOT_BUSINESS_TRUTH",
        "demand_admission": "NO_INDEPENDENT_KIND_SCHEMA_VALIDATED_AND_EXECUTION_APPROVAL_BOUND",
        "source_admission_authority": "human:veracier-shadow-owner",
        "authority_assurance": "DECLARED_NOT_AUTHENTICATED",
        "human_review": "NOT_RUN",
        "real_enterprise": "NOT_RUN",
        "external_effects": "NONE",
        "target_writes": 0,
        "production_ready": False,
    }
    wheels = wheel_check.get("wheels", {}) if isinstance(wheel_check, dict) else {}
    probes = wheel_check.get("probe", {}) if isinstance(wheel_check, dict) else {}
    if (
        not isinstance(canonical, dict)
        or canonical.get("status") != "PASS"
        or canonical.get("artifact_count") != 51
        or canonical.get("event_count") != 13
        or canonical.get("product_imports") != 0
        or canonical.get("failures") != []
        or not isinstance(wheel_evaluation, dict)
        or wheel_evaluation != canonical
        or not isinstance(summary, dict)
        or summary.get("status") != "PASS"
        or summary.get("maturity") != "SYNTHETIC_CONTROLLED_REFERENCE_MVP"
        or summary.get("boundaries") != expected_boundaries
        or summary.get("event_chain", {}).get("events") != 13
        or not isinstance(wheel_check, dict)
        or wheel_check.get("status") != "PASS"
        or wheel_check.get("pack_digest") != canonical.get("pack_digest")
        or wheel_check.get("independent_evaluation") != canonical
        or wheel_check.get("offline_build") is not True
        or wheel_check.get("clean_temporary_working_directory") is not True
        or wheel_check.get("oac_source_material_mode") != "READ_ONLY_PUBLIC_SOURCE_COMMITMENT"
        or not isinstance(wheels, dict)
        or set(wheels) != {"oac", "orgrebase"}
        or not isinstance(probes, dict)
        or set(probes) != {"oac", "orgrebase"}
        or any(
            not isinstance(item, dict) or item.get("module_loaded_from_wheel") is not True
            for item in probes.values()
        )
    ):
        raise SnapshotError("OAC_REFERENCE_PUBLICATION_VERIFY_FAILED")
    for item in wheels.values():
        if not isinstance(item, dict):
            raise SnapshotError("OAC_REFERENCE_WHEEL_DESCRIPTOR_INVALID")
        file_name = item.get("file")
        expected_digest = item.get("sha256")
        if not isinstance(file_name, str) or Path(file_name).name != file_name:
            raise SnapshotError("OAC_REFERENCE_WHEEL_DESCRIPTOR_INVALID")
        wheel_path = wheel_root / "wheels" / file_name
        if not wheel_path.is_file() or f"sha256:{_sha256_file(wheel_path)}" != expected_digest:
            raise SnapshotError("OAC_REFERENCE_WHEEL_DIGEST_MISMATCH")
    return {
        "status": "PASS",
        "maturity": "SYNTHETIC_CONTROLLED_REFERENCE_MVP",
        "artifactCount": 51,
        "eventCount": 13,
        "packDigest": canonical["pack_digest"],
        "eventChainHead": summary["event_chain"]["head_digest"],
        "independentEvaluator": "PASS",
        "productImports": 0,
        "dualWheel": "PASS",
        "offlineBuild": True,
        "cleanTemporaryWorkingDirectory": True,
        "modulesLoadedFromWheel": ["oac", "orgrebase"],
        "wheels": wheels,
        "humanReview": "NOT_RUN",
        "realEnterprise": "NOT_RUN",
        "targetWrites": 0,
        "productionReady": False,
    }


def _verify_oac_quote_adaptation_publication(
    orgrebase_root: Path,
    oac_root: Path,
) -> dict[str, Any]:
    """Verify the separate Spec 062 pre-execution enterprise-adaptation lane."""

    evidence = orgrebase_root / "evidence/oac-quote-adaptation/latest"
    manifest_path = evidence / "manifest.json"
    summary_path = evidence / "summary.json"
    owner_review_summary_relative = "artifacts/evergreen/owner-review-summary.json"
    owner_review_summary_path = evidence / owner_review_summary_relative
    script = orgrebase_root / "scripts/verify_oac_quote_adaptation.py"
    if not evidence.exists() and not script.is_file():
        return {"status": "NOT_PRESENT"}
    if not all(
        (
            evidence.is_dir(),
            manifest_path.is_file(),
            summary_path.is_file(),
            script.is_file(),
        )
    ):
        raise SnapshotError("OAC_QUOTE_ADAPTATION_PUBLICATION_INCOMPLETE")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--root",
                str(evidence),
                "--retained-build",
                "--project-root",
                str(orgrebase_root),
            ],
            cwd=orgrebase_root,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        result = json.loads(completed.stdout) if completed.returncode == 0 else None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SnapshotError("OAC_QUOTE_ADAPTATION_PUBLICATION_VERIFY_FAILED") from exc
    evergreen = summary.get("evergreen", {}) if isinstance(summary, dict) else {}
    veracier = summary.get("veracier", {}) if isinstance(summary, dict) else {}
    boundary = summary.get("oac_boundary", {}) if isinstance(summary, dict) else {}
    mutations = result.get("mutation_rejections", {}) if isinstance(result, dict) else {}
    manifest_entries = manifest.get("entries", []) if isinstance(manifest, dict) else []
    manifest_entry_paths = {
        entry.get("path")
        for entry in manifest_entries
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }
    if (
        not isinstance(result, dict)
        or result.get("status") != "PASS"
        or result.get("verification_scope") != "RETAINED_ARTIFACT"
        or result.get("current_release_qualified") is not False
        or result.get("evidence_class") != "VALIDATED_CONTROLLED_LOCAL"
        or result.get("claim_ceiling") != "CONTROLLED_LOCAL_ADAPTATION_MECHANISM_ONLY"
        or result.get("failure_codes") != []
        or result.get("oac_public_cli_checks") != 5
        or not isinstance(mutations, dict)
        or len(mutations) != 6
        or result.get("real_review_wait_ms", 0) < 4000
        or result.get("canonical_target_writes") != 0
        or not isinstance(manifest, dict)
        or manifest.get("status") != "CLOSED_WORLD"
        or manifest.get("entry_count") != 21
        or not isinstance(manifest_entries, list)
        or len(manifest_entries) != 21
        or owner_review_summary_relative not in manifest_entry_paths
        or not owner_review_summary_path.is_file()
        or manifest.get("pack_digest") != result.get("manifest_pack_digest")
        or not isinstance(summary, dict)
        or summary.get("status") != "PASS"
        or summary.get("canonical_target_writes") != 0
        or evergreen.get("status") != "READY_FOR_ORGREBASE"
        or evergreen.get("mapping_count") != 5
        or evergreen.get("gap_count") != 0
        or evergreen.get("bound_execution_started") is not False
        or veracier.get("status") != "HOLD"
        or veracier.get("gap_count") != 7
        or veracier.get("capsule_produced") is not False
        or veracier.get("execution_started") is not False
        or boundary.get("source_and_demand_validated") is not True
        or boundary.get("source_admission_validated") is not True
        or boundary.get("oac_plan_produced") is not False
        or boundary.get("oac_plan_certificate_produced") is not False
        or boundary.get("oac_runtime_invoked") is not False
    ):
        raise SnapshotError("OAC_QUOTE_ADAPTATION_PUBLICATION_VERIFY_FAILED")
    if any(
        summary.get(key) != "NOT_RUN"
        for key in (
            "real_enterprise_connectors",
            "real_enterprise_data",
            "enterprise_uat",
            "production_sla_ha_dr",
        )
    ):
        raise SnapshotError("OAC_QUOTE_ADAPTATION_CLAIM_BOUNDARY_INVALID")
    return {
        "status": "PASS",
        "evidenceClass": "VALIDATED_CONTROLLED_LOCAL",
        "claimCeiling": "CONTROLLED_LOCAL_ADAPTATION_MECHANISM_ONLY",
        "verificationScope": result["verification_scope"],
        "currentReleaseQualified": False,
        "oacReader": result.get("oac_reader"),
        "entryCount": 21,
        "packDigest": manifest["pack_digest"],
        "evergreenStatus": "READY_FOR_ORGREBASE",
        "veracierStatus": "HOLD",
        "veracierGapCount": 7,
        "realReviewWaitMs": result["real_review_wait_ms"],
        "oacPublicCliChecks": 5,
        "mutationRejections": 6,
        "canonicalTargetWrites": 0,
        "oacPlanProduced": False,
        "oacPlanCertificateProduced": False,
        "oacRuntimeInvoked": False,
        "boundExecutionStarted": False,
        "realEnterpriseValidated": "NOT_RUN",
        "productionReady": False,
    }


def _verify_public_real_process_publication(
    orgrebase_root: Path,
) -> dict[str, Any]:
    """Replay the retained BPI 2019 projection without packaging raw data."""

    evidence = orgrebase_root / "evidence/public-real-process/latest"
    benchmark = orgrebase_root / "benchmark/quote-value-v0.4-bpi-real-process"
    receipt_path = evidence / "bpi2019-real-process-benchmark-receipt.json"
    retained_verification_path = evidence / "bpi2019-real-process-verification.json"
    summary_path = evidence / "summary.json"
    dataset_path = benchmark / "dataset-manifest.json"
    script = orgrebase_root / "scripts/verify_bpi2019_real_process_benchmark.py"
    roots = (evidence, benchmark)
    if not any(path.exists() for path in (*roots, script)):
        return {"status": "NOT_PRESENT"}
    if not all(
        (
            evidence.is_dir(),
            benchmark.is_dir(),
            receipt_path.is_file(),
            retained_verification_path.is_file(),
            summary_path.is_file(),
            dataset_path.is_file(),
            script.is_file(),
        )
    ):
        raise SnapshotError("PUBLIC_REAL_PROCESS_PUBLICATION_INCOMPLETE")
    try:
        with tempfile.TemporaryDirectory(prefix="orgrebase-bpi2019-snapshot-verify-") as temporary:
            output = Path(temporary) / "verification.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--project-root",
                    str(orgrebase_root),
                    "--receipt",
                    str(receipt_path),
                    "--output",
                    str(output),
                ],
                cwd=orgrebase_root,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            replay = json.loads(output.read_text(encoding="utf-8"))
        retained = json.loads(retained_verification_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SnapshotError("PUBLIC_REAL_PROCESS_PUBLICATION_VERIFY_FAILED") from exc
    artifact = dataset.get("artifact", {}) if isinstance(dataset, dict) else {}
    if (
        completed.returncode != 0
        or replay != retained
        or not isinstance(replay, dict)
        or replay.get("status") != "PASS"
        or replay.get("verification_mode") != "INDEPENDENT_OFFLINE_DETERMINISTIC_REPLAY"
        or replay.get("implementation_independence") != "NO_EVALUATOR_OR_PRODUCT_IMPORTS"
        or replay.get("queries_replayed") != 128
        or replay.get("strategies_replayed") != 3
        or replay.get("failures") != []
        or not isinstance(summary, dict)
        or summary.get("status") != "PASS"
        or summary.get("query_count") != 128
        or summary.get("receipt_digest") != replay.get("receipt_digest")
        or not isinstance(dataset, dict)
        or dataset.get("dataset_id") != "bpi-challenge-2019-4tu-12715853"
        or dataset.get("license") != "CC-BY-4.0"
        or not isinstance(artifact, dict)
        or artifact.get("size_bytes") != 728_558_522
        or artifact.get("raw_bytes_in_repository") is not False
        or not isinstance(artifact.get("sha256"), str)
        or not artifact["sha256"].startswith("sha256:")
        or len(artifact["sha256"]) != 71
    ):
        raise SnapshotError("PUBLIC_REAL_PROCESS_PUBLICATION_VERIFY_FAILED")
    return {
        "status": "PASS",
        "datasetId": dataset["dataset_id"],
        "license": dataset["license"],
        "rawArtifactSizeBytes": artifact["size_bytes"],
        "rawArtifactSha256": artifact["sha256"],
        "rawBytesPackaged": False,
        "projectionDigest": replay["projection_digest"],
        "receiptDigest": replay["receipt_digest"],
        "verificationDigest": replay["digest"],
        "queriesReplayed": 128,
        "strategiesReplayed": 3,
        "claimBoundary": summary["claim_boundary"],
    }


def _agentteams_lock_digest(lock: dict[str, Any]) -> str:
    canonical = json.dumps(lock, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _retained_agentteams_source_lock(
    orgrebase_root: Path, source_lock_digest: str,
) -> tuple[Path, dict[str, Any], str]:
    """Select only the active lock or a digest-pinned historical qualification."""

    active = _read_agentteams_source_lock(orgrebase_root)
    if _agentteams_lock_digest(active) == source_lock_digest:
        return orgrebase_root / "agentteams/teamharness-lock.json", active, "ACTIVE_SOURCE"
    relative = HISTORICAL_TEAMHARNESS_LOCKS.get(source_lock_digest)
    if relative is None:
        raise SnapshotError("RETAINED_AGENTTEAMS_SOURCE_NOT_REGISTERED")
    path = orgrebase_root / relative
    try:
        if path.is_symlink():
            raise SnapshotError("RETAINED_AGENTTEAMS_SOURCE_LOCK_INVALID")
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError("RETAINED_AGENTTEAMS_SOURCE_LOCK_INVALID") from exc
    if _agentteams_lock_digest(lock) != source_lock_digest:
        raise SnapshotError("RETAINED_AGENTTEAMS_SOURCE_LOCK_INVALID")
    return path, lock, "HISTORICAL_SOURCE"


def _verify_oac_agentic_adaptation_publication(
    orgrebase_root: Path,
) -> dict[str, Any]:
    """Replay the optional Spec 067 Agent-assisted intake candidate pack."""

    evidence = orgrebase_root / "evidence/oac-agentic-adaptation/latest"
    script = orgrebase_root / "scripts/verify_oac_agent_adaptation.py"
    if not evidence.exists() and not script.is_file():
        return {"status": "NOT_PRESENT"}
    if (
        not evidence.is_dir()
        or not (evidence / "manifest.json").is_file()
        or not (evidence / "mapping-receipt.json").is_file()
        or not script.is_file()
    ):
        raise SnapshotError("OAC_AGENTIC_ADAPTATION_PUBLICATION_INCOMPLETE")
    try:
        receipt = json.loads((evidence / "mapping-receipt.json").read_text(encoding="utf-8"))
        lock_path, lock, source_scope = _retained_agentteams_source_lock(
            orgrebase_root, receipt.get("native_agentteams", {}).get("source_lock_digest", ""),
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--evidence",
                str(evidence),
                "--lock",
                str(lock_path),
            ],
            cwd=orgrebase_root,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        result = json.loads(completed.stdout) if completed.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SnapshotError("OAC_AGENTIC_ADAPTATION_PUBLICATION_VERIFY_FAILED") from exc
    if (
        not isinstance(result, dict)
        or result.get("status") != "PASS"
        or result.get("mapping_status") != "VALIDATED_CANDIDATE"
        or result.get("native_action_count") != 9
        or result.get("provider_request_id_present") is not True
        or result.get("canonical_target_writes") != 0
        or not isinstance(result.get("adaptation_run_id"), str)
        or not isinstance(result.get("manifest_digest"), str)
        or not isinstance(result.get("receipt_digest"), str)
        or result.get("agentteams_version") != lock["tag"]
        or result.get("agentteams_commit") != lock["commit"]
    ):
        raise SnapshotError("OAC_AGENTIC_ADAPTATION_PUBLICATION_VERIFY_FAILED")
    return {
        "status": "PASS",
        "adaptationRunId": result["adaptation_run_id"],
        "mappingStatus": "VALIDATED_CANDIDATE",
        "modelStatus": result.get("model_status"),
        "providerRequestIdPresent": True,
        "nativeActionCount": 9,
        "canonicalTargetWrites": 0,
        "manifestDigest": result["manifest_digest"],
        "receiptDigest": result["receipt_digest"],
        "agentTeamsVersion": lock["tag"],
        "agentTeamsCommit": lock["commit"],
        "sourceVerificationScope": source_scope,
    }


def _verify_formation_taskflow_publication(orgrebase_root: Path) -> dict[str, Any]:
    """Replay the retained two-domain Formation-compiled AgentTeams topology."""

    evidence = orgrebase_root / "evidence/formation-taskflow/latest"
    retained_path = evidence / "verification.json"
    receipt_path = evidence / "probe-receipt.json"
    plan_path = evidence / "inputs/execution-plan.json"
    script = orgrebase_root / "scripts/verify_formation_taskflow_probe.py"
    lock = orgrebase_root / "agentteams/teamharness-lock.json"
    if not evidence.exists() and not script.is_file():
        return {"status": "NOT_PRESENT"}
    if not all(
        (
            evidence.is_dir(),
            retained_path.is_file(),
            receipt_path.is_file(),
            plan_path.is_file(),
            script.is_file(),
            lock.is_file(),
        )
    ):
        raise SnapshotError("FORMATION_TASKFLOW_PUBLICATION_INCOMPLETE")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        lock, source_lock, source_scope = _retained_agentteams_source_lock(
            orgrebase_root, receipt.get("source_verification", {}).get("source_lock_digest", ""),
        )
        with tempfile.TemporaryDirectory(prefix="orgrebase-formation-taskflow-verify-") as temporary:
            output = Path(temporary) / "verification.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--evidence",
                    str(evidence),
                    "--lock",
                    str(lock),
                    "--output",
                    str(output),
                ],
                cwd=orgrebase_root,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            replay = json.loads(output.read_text(encoding="utf-8"))
        retained = json.loads(retained_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SnapshotError("FORMATION_TASKFLOW_PUBLICATION_VERIFY_FAILED") from exc
    expected_domains = ["legal", "product"]
    invariant_keys = (
        "status",
        "evidence_class",
        "claim_boundary",
        "run_id",
        "execution_plan_digest",
        "selected_domain_ids",
        "planned_domain_ids",
        "actual_domain_ids",
        "actual_task_ids",
        "agentteams_actions",
        "task_binding_count",
        "domain_task_count",
        "reviewer_task_count",
        "topology_match",
        "project_terminal_state",
        "candidate_only",
        "canonical_target_writes",
        "verified_receipt_digest",
    )
    if (
        completed.returncode != 0
        or not isinstance(replay, dict)
        or replay.get("status") != "PASS"
        or replay.get("verification_strength") != "RETAINED_LOCK_REPLAY"
        or replay.get("evidence_class")
        != "CONTROLLED_LOCAL_FORMATION_COMPILED_AGENTTEAMS"
        or replay.get("selected_domain_ids") != expected_domains
        or replay.get("planned_domain_ids") != expected_domains
        or replay.get("actual_domain_ids") != expected_domains
        or replay.get("agentteams_actions") != 20
        or replay.get("task_binding_count") != 3
        or replay.get("domain_task_count") != 2
        or replay.get("reviewer_task_count") != 1
        or replay.get("topology_match") is not True
        or replay.get("project_terminal_state") != "completed"
        or replay.get("candidate_only") is not True
        or replay.get("canonical_target_writes") != 0
        or not isinstance(retained, dict)
        or retained.get("verification_strength") != "PINNED_CHECKOUT_REPLAY"
        or any(replay.get(key) != retained.get(key) for key in invariant_keys)
        or not isinstance(receipt, dict)
        or receipt.get("digest") != replay.get("verified_receipt_digest")
        or not isinstance(plan, dict)
        or plan.get("digest") != replay.get("execution_plan_digest")
        or plan.get("selected_domain_ids") != expected_domains
    ):
        raise SnapshotError("FORMATION_TASKFLOW_PUBLICATION_VERIFY_FAILED")
    return {
        "status": "PASS",
        "evidenceClass": replay["evidence_class"],
        "claimBoundary": replay["claim_boundary"],
        "runId": replay["run_id"],
        "selectedDomainIds": expected_domains,
        "plannedDomainIds": expected_domains,
        "actualDomainIds": expected_domains,
        "agentTeamsActionCount": 20,
        "taskBindingCount": 3,
        "domainTaskCount": 2,
        "reviewerTaskCount": 1,
        "topologyMatch": True,
        "projectTerminalState": "completed",
        "retainedVerificationStrength": "PINNED_CHECKOUT_REPLAY",
        "portableVerificationStrength": "RETAINED_LOCK_REPLAY",
        "executionPlanDigest": replay["execution_plan_digest"],
        "receiptDigest": replay["verified_receipt_digest"],
        "portableVerificationDigest": replay["digest"],
        "candidateOnly": True,
        "canonicalTargetWrites": 0,
        "productionReady": False,
        "agentTeamsVersion": source_lock["tag"],
        "agentTeamsCommit": source_lock["commit"],
        "sourceVerificationScope": source_scope,
    }


def _verify_oac_public_real_process_publication(
    orgrebase_root: Path,
) -> dict[str, Any]:
    """Replay the BPI-to-OAC adaptation without inferring private organization facts."""

    evidence = orgrebase_root / "evidence/oac-public-real-process/latest"
    receipt_path = evidence / "adaptation-receipt.json"
    manifest_path = evidence / "manifest.json"
    benchmark = orgrebase_root / "benchmark/quote-value-v0.4-bpi-real-process"
    dataset_path = benchmark / "dataset-manifest.json"
    config = orgrebase_root / "configs/oac/bpi2019-p2p-adaptation-v1.json"
    script = orgrebase_root / "scripts/verify_bpi2019_oac_adaptation.py"
    if not evidence.exists() and not script.is_file():
        return {"status": "NOT_PRESENT"}
    if not all(
        (
            evidence.is_dir(),
            receipt_path.is_file(),
            manifest_path.is_file(),
            benchmark.is_dir(),
            dataset_path.is_file(),
            config.is_file(),
            script.is_file(),
        )
    ):
        raise SnapshotError("OAC_PUBLIC_REAL_PROCESS_PUBLICATION_INCOMPLETE")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--evidence",
                str(evidence),
                "--benchmark",
                str(benchmark),
                "--config",
                str(config),
            ],
            cwd=orgrebase_root,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        result = json.loads(completed.stdout) if completed.returncode == 0 else None
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SnapshotError("OAC_PUBLIC_REAL_PROCESS_PUBLICATION_VERIFY_FAILED") from exc
    actions = receipt.get("agentteams", {}).get("action_sequence", [])
    admission = receipt.get("human_admission", {}).get("admission", {})
    early_attempt = receipt.get("human_admission", {}).get("early_attempt", {})
    typed = receipt.get("typed_scope_execution", {})
    model = receipt.get("model_attempt", {})
    unknowns = receipt.get("unknown_dimensions", {})
    artifact = dataset.get("artifact", {}) if isinstance(dataset, dict) else {}
    expected_unknowns = {
        "APPROVAL_AUTHORITIES",
        "ORGANIZATION_VALUES",
        "PERMISSIONS",
        "REAL_RESPONSIBLE_OWNERS",
    }
    expected_limitations = {
        "TASK_DEFINED_GROUND_TRUTH_NOT_HUMAN_CAUSAL_ANNOTATION",
        "NOT_ENTERPRISE_QUOTE_DATA_OR_QUOTE_ROI",
        "NOT_ARBITRARY_ENTERPRISE_ADAPTATION",
        "NOT_PRODUCTION_DEPLOYMENT",
        "NOT_EXTERNAL_HUMAN_ACCEPTANCE",
    }
    if (
        not isinstance(result, dict)
        or result.get("status") != "PASS"
        or result.get("verification_mode") != "INDEPENDENT_STDLIB_CLOSED_WORLD_REPLAY"
        or result.get("product_imports") != 0
        or result.get("failures") != []
        or not isinstance(receipt, dict)
        or receipt.get("status") != "PASS"
        or receipt.get("claim_ceiling")
        != "PUBLIC_REAL_DATA_OAC_MAPPING_AND_TYPED_SCOPE_EXECUTION"
        or receipt.get("candidate_only") is not True
        or receipt.get("canonical_target_writes") != 0
        or len(actions) != 9
        or receipt.get("agentteams", {}).get("terminal_state") != "completed"
        or receipt.get("agentteams", {}).get("run_id")
        != receipt.get("adaptation_run_id")
        or early_attempt.get("status") != "REJECTED_TOO_EARLY"
        or admission.get("status") != "ADMITTED"
        or admission.get("elapsed_ms", 0) < 4000
        or admission.get("interaction_evidence")
        != "CONTROLLED_LOCAL_SCRIPTED_OWNER_COMMAND_NOT_EXTERNAL_HUMAN"
        or set(unknowns) != expected_unknowns
        or typed.get("ground_truth_class") != "TASK_DEFINED_QUERY_GROUND_TRUTH"
        or typed.get("query_count") != 128
        or typed.get("recall") != 1.0
        or typed.get("unsafe_false_unaffected_rate") != 0.0
        or set(receipt.get("limitations", [])) != expected_limitations
        or not isinstance(manifest, dict)
        or manifest.get("status") != "CLOSED_WORLD"
        or not isinstance(manifest.get("entry_count"), int)
        or manifest.get("entry_count") <= 0
        or not isinstance(manifest.get("entries"), list)
        or manifest.get("entry_count") != len(manifest["entries"])
        or manifest.get("claim_ceiling") != receipt.get("claim_ceiling")
        or not isinstance(dataset, dict)
        or receipt.get("dataset_manifest_digest") != dataset.get("digest")
        or receipt.get("source_sha256") != artifact.get("sha256")
        or artifact.get("raw_bytes_in_repository") is not False
    ):
        raise SnapshotError("OAC_PUBLIC_REAL_PROCESS_PUBLICATION_VERIFY_FAILED")
    return {
        "status": "PASS",
        "verificationMode": "INDEPENDENT_STDLIB_CLOSED_WORLD_REPLAY",
        "productImports": 0,
        "datasetId": dataset["dataset_id"],
        "sourceSha256": receipt["source_sha256"],
        "adaptationRunId": receipt["adaptation_run_id"],
        "executionRunId": receipt["execution_run_id"],
        "selectedMappingLane": receipt["selected_mapping_lane"],
        "modelEvidenceClass": model.get("evidence_class"),
        "modelId": model.get("model_id"),
        "providerRequestIdPresent": model.get("provider_request_id_present"),
        "agentTeamsActionCount": 9,
        "humanReviewWaitMs": admission["elapsed_ms"],
        "humanInteractionEvidence": admission["interaction_evidence"],
        "unknownDimensions": sorted(expected_unknowns),
        "groundTruthClass": typed["ground_truth_class"],
        "queriesReplayed": 128,
        "typedScopeRecall": 1.0,
        "unsafeFalseUnaffectedRate": 0.0,
        "manifestEntryCount": manifest["entry_count"],
        "manifestDigest": manifest["digest"],
        "receiptDigest": receipt["digest"],
        "claimBoundary": receipt["claim_ceiling"],
        "limitations": sorted(expected_limitations),
        "rawBytesPackaged": False,
        "canonicalTargetWrites": 0,
        "productionReady": False,
    }


def _verify_enterprise_pilot_publication(orgrebase_root: Path) -> dict[str, Any]:
    """Replay the exact Spec 054 Pack and retained Pilot evidence."""

    publication = orgrebase_root / "evidence/enterprise-quote-pilot/latest"
    evidence = publication / "run/evidence"
    pack = publication / "pack-sealed"
    retained_path = publication / "run/verification.json"
    script = orgrebase_root / "scripts/verify_enterprise_quote_pilot.py"
    if not publication.exists() and not script.is_file():
        return {"status": "NOT_PRESENT"}
    if not all(
        (
            publication.is_dir(),
            evidence.is_dir(),
            (evidence / "manifest.json").is_file(),
            (pack / "pack.json").is_file(),
            retained_path.is_file(),
            script.is_file(),
        )
    ):
        raise SnapshotError("ENTERPRISE_PILOT_PUBLICATION_INCOMPLETE")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                str(evidence),
                "--pack",
                str(pack),
            ],
            cwd=orgrebase_root,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        replay = json.loads(completed.stdout) if completed.returncode == 0 else None
        retained = json.loads(retained_path.read_text(encoding="utf-8"))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SnapshotError("ENTERPRISE_PILOT_PUBLICATION_VERIFY_FAILED") from exc
    if (
        not isinstance(replay, dict)
        or replay != retained
        or replay.get("status") != "PASS"
        or replay.get("deployment_maturity") != "PILOT_READY_CONTROLLED_LOCAL"
        or replay.get("wrong_owner_rejections") != 2
        or replay.get("stale_approval_rejections") != 2
        or replay.get("restart_count") != 1
        or replay.get("backup_restore") != "PASS"
        or replay.get("external_enterprise_target_writes") != 0
        or replay.get("real_enterprise_validated") != "NOT_RUN"
        or replay.get("production_ready") is not False
    ):
        raise SnapshotError("ENTERPRISE_PILOT_PUBLICATION_VERIFY_FAILED")
    return {
        "status": "PASS",
        "deploymentMaturity": replay["deployment_maturity"],
        "packDigest": replay["pack_digest"],
        "finalQuoteRef": replay["final_quote_ref"],
        "wrongOwnerRejections": replay["wrong_owner_rejections"],
        "staleApprovalRejections": replay["stale_approval_rejections"],
        "restartCount": replay["restart_count"],
        "backupRestore": replay["backup_restore"],
        "externalEnterpriseTargetWrites": 0,
        "realEnterpriseValidated": "NOT_RUN",
        "productionReady": False,
    }


def _read_agentteams_source_lock(orgrebase_root: Path) -> dict[str, Any]:
    """Read release identity without importing the application or its dependencies."""
    try:
        source = json.loads((orgrebase_root / "agentteams/source-lock.json").read_text(encoding="utf-8"))
        lock = json.loads((orgrebase_root / "agentteams/teamharness-lock.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError("AGENTTEAMS_SOURCE_LOCK_INVALID") from exc
    if (
        not isinstance(source, dict)
        or not isinstance(lock, dict)
        or source.get("schema_version") != "orgrebase.agentteams-source-lock.v1"
        or lock.get("schema_version") != "orgrebase.teamharness-source-lock.v1"
        or source.get("repository") != "https://github.com/agentscope-ai/AgentTeams"
        or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", str(source.get("tag", "")))
        or not re.fullmatch(r"[0-9a-f]{40}", str(source.get("commit", "")))
        or source.get("crd_api_version") != "agentteams.io/v1beta1"
        or (source.get("repository"), source.get("tag"), source.get("commit"))
        != (lock.get("upstream"), lock.get("tag"), lock.get("commit"))
    ):
        raise SnapshotError("AGENTTEAMS_SOURCE_IDENTITY_MISMATCH")
    descriptor = lock.get("offline_bundle")
    relative = descriptor.get("path") if isinstance(descriptor, dict) else None
    if not isinstance(relative, str):
        raise SnapshotError("AGENTTEAMS_OFFLINE_BUNDLE_DESCRIPTOR_INVALID")
    pure = PurePosixPath(relative)
    _validate_relative_path(pure)
    if pure.parts[:2] != ("vendor", "agentteams"):
        raise SnapshotError("AGENTTEAMS_OFFLINE_BUNDLE_PATH_ESCAPE")
    return lock


def _verify_agentteams_source_bundle(orgrebase_root: Path) -> dict[str, Any]:
    fetch_script = orgrebase_root / "scripts/fetch_pinned_agentteams.py"
    lock_path = orgrebase_root / "agentteams/teamharness-lock.json"
    if not fetch_script.is_file() and not lock_path.exists():
        return {"status": "NOT_PRESENT_MINIMAL_SOURCE_ROOT"}
    if not fetch_script.is_file() or not lock_path.is_file():
        raise SnapshotError("AGENTTEAMS_OFFLINE_SOURCE_INCOMPLETE")
    lock = _read_agentteams_source_lock(orgrebase_root)
    descriptor = lock.get("offline_bundle") if isinstance(lock, dict) else None
    if not isinstance(descriptor, dict):
        raise SnapshotError("AGENTTEAMS_OFFLINE_BUNDLE_MISSING")
    relative = descriptor.get("path")
    expected_digest = descriptor.get("sha256")
    commit = lock.get("commit")
    source_files = lock.get("source_files")
    license_path = descriptor.get("license_path")
    if (
        not isinstance(relative, str)
        or not relative
        or not isinstance(expected_digest, str)
        or not isinstance(commit, str)
        or not isinstance(source_files, dict)
        or not isinstance(license_path, str)
    ):
        raise SnapshotError("AGENTTEAMS_OFFLINE_BUNDLE_DESCRIPTOR_INVALID")
    pure = PurePosixPath(relative)
    _validate_relative_path(pure)
    bundle = orgrebase_root.joinpath(*pure.parts).resolve()
    try:
        bundle.relative_to(orgrebase_root.resolve())
    except ValueError as exc:
        raise SnapshotError("AGENTTEAMS_OFFLINE_BUNDLE_PATH_ESCAPE") from exc
    if not bundle.is_file() or f"sha256:{_sha256_file(bundle)}" != expected_digest:
        raise SnapshotError("AGENTTEAMS_OFFLINE_BUNDLE_DIGEST_MISMATCH")

    with tempfile.TemporaryDirectory(prefix="orgrebase-agentteams-bundle-") as temporary:
        checkout = Path(temporary) / "checkout"
        try:
            subprocess.run(
                ["git", "clone", "--quiet", "--no-checkout", str(bundle), str(checkout)],
                check=True,
                capture_output=True,
                timeout=120,
            )
            subprocess.run(
                ["git", "checkout", "--quiet", "--detach", commit],
                cwd=checkout,
                check=True,
                capture_output=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SnapshotError("AGENTTEAMS_OFFLINE_BUNDLE_CLONE_FAILED") from exc
        if not (checkout / license_path).is_file():
            raise SnapshotError("AGENTTEAMS_OFFLINE_BUNDLE_LICENSE_MISSING")
        history_scan = _scan_git_history(checkout)
        observed: dict[str, str] = {}
        for source_relative, expected in sorted(source_files.items()):
            if not isinstance(source_relative, str) or not isinstance(expected, str):
                raise SnapshotError("AGENTTEAMS_SOURCE_LOCK_INVALID")
            source_pure = PurePosixPath(source_relative)
            _validate_relative_path(source_pure)
            source = checkout.joinpath(*source_pure.parts)
            if not source.is_file():
                raise SnapshotError(f"AGENTTEAMS_OFFLINE_SOURCE_MISSING:{source_relative}")
            observed[source_relative] = f"sha256:{_sha256_file(source)}"
            if observed[source_relative] != expected:
                raise SnapshotError(f"AGENTTEAMS_OFFLINE_SOURCE_DRIFT:{source_relative}")
    return {
        "status": "PASS",
        "mode": "PACKAGED_COMPLETE_GIT_BUNDLE",
        "historyCredentialScan": history_scan,
        "path": relative,
        "sha256": expected_digest,
        "sizeBytes": bundle.stat().st_size,
        "commit": commit,
        "sourceFiles": observed,
        "licensePath": license_path,
        "networkRequiredForAgentTeamsSource": False,
    }


def _copy_result_payload(result: CopyResult) -> dict[str, Any]:
    return {
        "component": result.component,
        "files": result.files,
        "contentBytes": result.content_bytes,
        "excluded": list(result.excluded),
        "syntheticCredentialSentinels": list(result.synthetic_credential_sentinels),
    }


def _release_allowlist_payload(profile: str = "full") -> dict[str, Any]:
    return {
        "status": "PASS",
        "policy": "EXPLICIT_RELEASE_CLOSURE",
        "profile": profile,
        "runtimeBenchmarkPrefixes": list(RUNTIME_BENCHMARK_PREFIXES) if profile == "runtime" else None,
        "runtimeOacTreePrefixes": list(RUNTIME_OAC_TREE_PREFIXES) if profile == "runtime" else None,
        "runtimeOacSupportFiles": list(RUNTIME_OAC_SUPPORT_FILES) if profile == "runtime" else None,
        "runtimeParitySupportFiles": list(RUNTIME_PARITY_SUPPORT_FILES) if profile == "runtime" else None,
        "runtimeRevalidationSupportFiles": list(RUNTIME_REVALIDATION_SUPPORT_FILES) if profile == "runtime" else None,
        "currentRevalidationPrefix": ARCHIVE_REVALIDATION_PREFIX,
        "orgrebaseTreePrefixes": list(ORGREBASE_RELEASE_TREE_PREFIXES),
        "benchmarkPrefixes": list(ORGREBASE_RELEASE_BENCHMARK_PREFIXES),
        "evidencePrefixes": list(ORGREBASE_RELEASE_EVIDENCE_PREFIXES),
        "supportFiles": list(ORGREBASE_RELEASE_SUPPORT_FILES),
        "alwaysExcludedTreePrefixes": list(ORGREBASE_ALWAYS_EXCLUDED_PREFIXES),
        "oacTreePrefixes": list(OAC_RELEASE_TREE_PREFIXES),
        "oacSupportFiles": list(OAC_RELEASE_SUPPORT_FILES),
        "rawBpiDatasetPackaged": False,
        "developmentSpecsPackaged": False,
        "historicalExperimentTreePackaged": False,
        "submissionTreePackaged": False,
        "sourceContentPolicy": "COPY_ORIGINAL_BYTES_ONLY",
        "evidenceContentRewrites": 0,
    }


def build_source_snapshot(orgrebase_root: Path, oac_root: Path, output_dir: Path, *, profile: str = "full") -> dict[str, Any]:
    _require_profile(profile)
    orgrebase = _validate_root(orgrebase_root, "ORGREBASE")
    oac = _validate_root(oac_root, "OAC")
    if orgrebase == oac or orgrebase in oac.parents or oac in orgrebase.parents:
        raise SnapshotError("SOURCE_ROOTS_MUST_BE_DISTINCT_SIBLING_TREES")
    output = output_dir.resolve()
    if output.exists():
        raise SnapshotError(f"OUTPUT_DIRECTORY_ALREADY_EXISTS:{output}")
    for source, component in ((orgrebase, "orgrebase"), (oac, "oac-spec")):
        if source in output.parents:
            relative_output = PurePosixPath(output.relative_to(source).as_posix())
            raise SnapshotError(
                f"OUTPUT_INSIDE_INCLUDED_SOURCE_TREE:{component}/{relative_output.as_posix()}"
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    golden_pilot_publication = (_verify_golden_pilot_publication(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    oac_quote_adaptation_publication = (_verify_oac_quote_adaptation_publication(
        orgrebase,
        oac,
    ) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    public_real_process_publication = (_verify_public_real_process_publication(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    oac_agentic_adaptation_publication = (_verify_oac_agentic_adaptation_publication(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    formation_taskflow_publication = (_verify_formation_taskflow_publication(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    semifinal_publication = (_verify_semifinal_publication(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    semifinal_mvp_publication = (_verify_semifinal_mvp_publication(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    semifinal_runtime_projection = (_verify_semifinal_runtime_projection(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    compensation_publication = (_verify_compensation_publication(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    oac_public_real_process_publication = (_verify_oac_public_real_process_publication(
        orgrebase
    ) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    agentteams_source_bundle = _verify_agentteams_source_bundle(orgrebase)

    with tempfile.TemporaryDirectory(prefix=".source-snapshot-build-", dir=output.parent) as temporary:
        temporary_root = Path(temporary)
        snapshot_root = temporary_root / SNAPSHOT_ROOT_NAME
        org_result, oac_result = _prepare_snapshot(orgrebase, oac, snapshot_root, profile=profile)
        runtime_closure = _verify_release_runtime_closure(snapshot_root, profile=profile)
        expected_inventory = _inventory(snapshot_root)

        archive_path = temporary_root / ARCHIVE_NAME
        zip_stats = _build_zip(snapshot_root, archive_path)
        archive_size = archive_path.stat().st_size
        archive_sha256 = _sha256_file(archive_path)
        audit = _audit_zip(archive_path, profile=profile)

        extracted_root = _safe_extract(archive_path, temporary_root / "extracted")
        extracted_runtime_closure = _verify_release_runtime_closure(extracted_root, profile=profile)
        if extracted_runtime_closure != runtime_closure:
            raise SnapshotError("EXTRACTED_RUNTIME_CLOSURE_MISMATCH")
        extracted_semifinal_projection = (_verify_semifinal_runtime_projection(
            extracted_root / "orgrebase"
        ) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
        if extracted_semifinal_projection != semifinal_runtime_projection:
            raise SnapshotError("EXTRACTED_SEMIFINAL_RUNTIME_PROJECTION_MISMATCH")
        extraction = _compare_inventories(
            expected_inventory,
            _inventory(extracted_root),
            reason="EXTRACTED_TREE_MISMATCH",
        )

        replay_archive = temporary_root / "determinism-replay.zip"
        _build_zip(snapshot_root, replay_archive)
        if replay_archive.read_bytes() != archive_path.read_bytes():
            raise SnapshotError("DETERMINISTIC_ZIP_REPLAY_MISMATCH")

        metadata: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "status": "PASS",
            "snapshotRoot": SNAPSHOT_ROOT_NAME,
            "components": {
                "orgrebase": _copy_result_payload(org_result),
                "oac-spec": _copy_result_payload(oac_result),
            },
            "archive": {
                "name": ARCHIVE_NAME,
                "sha256": archive_sha256,
                "sizeBytes": archive_size,
                **zip_stats,
            },
            "inventory": {
                "files": list(expected_inventory),
                "fileCount": len(expected_inventory),
                "contentBytes": sum(int(item["sizeBytes"]) for item in expected_inventory),
                "sha256": _inventory_digest(expected_inventory),
            },
            "audit": audit,
            "releaseAllowlist": _release_allowlist_payload(profile),
            "runtimeClosure": runtime_closure,
            "extractionComparison": extraction,
            "determinismReplay": {
                "status": "PASS",
                "archiveSha256": archive_sha256,
                "exactArchiveBytes": True,
            },
            "goldenPilotPublication": golden_pilot_publication,
            "oacEnterpriseAdaptationPublication": oac_quote_adaptation_publication,
            "publicRealProcessPublication": public_real_process_publication,
            "oacAgenticAdaptationPublication": (oac_agentic_adaptation_publication),
            "formationTaskflowPublication": formation_taskflow_publication,
            "semifinalPublication": semifinal_publication,
            "semifinalMvpPublication": semifinal_mvp_publication,
            "semifinalRuntimeProjection": semifinal_runtime_projection,
            "compensationPublication": compensation_publication,
            "oacPublicRealProcessPublication": oac_public_real_process_publication,
            "agentTeamsSourceBundle": agentteams_source_bundle,
            "reproductionCommands": {
                "build": "make goai-source-snapshot OAC_ROOT=/path/to/oac-spec SOURCE_SNAPSHOT_OUTPUT=/path/to/output",
                "verify": "make goai-source-snapshot-verify OAC_ROOT=/path/to/oac-spec SOURCE_SNAPSHOT_OUTPUT=/path/to/output",
            },
            "claimBoundary": (
                "The snapshot proves deterministic packaging, exclusion, archive integrity, and exact-byte "
                "replay for the explicit two-repository release closure. It is not a repository mirror and "
                "does not attest Git history, external runtime execution, or production deployment."
            ),
        }

        delivery = temporary_root / "delivery"
        delivery.mkdir(mode=0o755)
        shutil.copyfile(archive_path, delivery / ARCHIVE_NAME)
        (delivery / ARCHIVE_NAME).chmod(0o644)
        metadata_path = delivery / METADATA_NAME
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        metadata_path.chmod(0o644)
        delivery.replace(output)
    return metadata


def _load_metadata(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"METADATA_INVALID:{path}") from exc
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        raise SnapshotError("METADATA_SCHEMA_VERSION_INVALID")
    return value


def _metadata_inventory(metadata: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    inventory = metadata.get("inventory")
    if not isinstance(inventory, dict) or not isinstance(inventory.get("files"), list):
        raise SnapshotError("METADATA_INVENTORY_INVALID")
    files = tuple(inventory["files"])
    for item in files:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sizeBytes", "sha256", "executable"}
            or not isinstance(item["path"], str)
            or not isinstance(item["sizeBytes"], int)
            or isinstance(item["sizeBytes"], bool)
            or item["sizeBytes"] < 0
            or not isinstance(item["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
            or not isinstance(item["executable"], bool)
        ):
            raise SnapshotError("METADATA_INVENTORY_ENTRY_INVALID")
        _validate_relative_path(PurePosixPath(item["path"]))
    paths = [item["path"] for item in files]
    if len(set(paths)) != len(paths):
        raise SnapshotError("METADATA_INVENTORY_PATHS_INVALID")
    if paths != sorted(paths, key=lambda value: _sort_key(str(value))):
        raise SnapshotError("METADATA_INVENTORY_NOT_SORTED")
    if (
        inventory.get("fileCount") != len(files)
        or inventory.get("contentBytes") != sum(int(item["sizeBytes"]) for item in files)
        or inventory.get("sha256") != _inventory_digest(files)
    ):
        raise SnapshotError("METADATA_INVENTORY_DIGEST_INVALID")
    return files


def verify_source_snapshot(orgrebase_root: Path, oac_root: Path, output_dir: Path, *, profile: str = "full") -> dict[str, Any]:
    _require_profile(profile)
    orgrebase = _validate_root(orgrebase_root, "ORGREBASE")
    oac = _validate_root(oac_root, "OAC")
    if orgrebase == oac or orgrebase in oac.parents or oac in orgrebase.parents:
        raise SnapshotError("SOURCE_ROOTS_MUST_BE_DISTINCT_SIBLING_TREES")
    output = output_dir.resolve()
    if not output.is_dir():
        raise SnapshotError(f"OUTPUT_DIRECTORY_NOT_FOUND:{output}")
    metadata = _load_metadata(output / METADATA_NAME)
    if metadata.get("releaseAllowlist") != _release_allowlist_payload(profile):
        raise SnapshotError("METADATA_RELEASE_ALLOWLIST_INVALID")
    archive_metadata = metadata.get("archive")
    if not isinstance(archive_metadata, dict) or archive_metadata.get("name") != ARCHIVE_NAME:
        raise SnapshotError("METADATA_ARCHIVE_INVALID")
    golden_pilot_publication = (_verify_golden_pilot_publication(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    if metadata.get("goldenPilotPublication") != golden_pilot_publication:
        raise SnapshotError("GOLDEN_PILOT_PUBLICATION_METADATA_MISMATCH")
    oac_quote_adaptation_publication = (_verify_oac_quote_adaptation_publication(
        orgrebase,
        oac,
    ) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    if metadata.get("oacEnterpriseAdaptationPublication") != oac_quote_adaptation_publication:
        raise SnapshotError("OAC_QUOTE_ADAPTATION_PUBLICATION_METADATA_MISMATCH")
    public_real_process_publication = (_verify_public_real_process_publication(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    if metadata.get("publicRealProcessPublication") != public_real_process_publication:
        raise SnapshotError("PUBLIC_REAL_PROCESS_PUBLICATION_METADATA_MISMATCH")
    oac_agentic_adaptation_publication = (_verify_oac_agentic_adaptation_publication(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    if metadata.get("oacAgenticAdaptationPublication") != oac_agentic_adaptation_publication:
        raise SnapshotError("OAC_AGENTIC_ADAPTATION_PUBLICATION_METADATA_MISMATCH")
    formation_taskflow_publication = (_verify_formation_taskflow_publication(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    if metadata.get("formationTaskflowPublication") != formation_taskflow_publication:
        raise SnapshotError("FORMATION_TASKFLOW_PUBLICATION_METADATA_MISMATCH")
    semifinal_publication = (_verify_semifinal_publication(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    if metadata.get("semifinalPublication") != semifinal_publication:
        raise SnapshotError("SEMIFINAL_PUBLICATION_METADATA_MISMATCH")
    semifinal_mvp_publication = (_verify_semifinal_mvp_publication(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    if metadata.get("semifinalMvpPublication") != semifinal_mvp_publication:
        raise SnapshotError("SEMIFINAL_MVP_PUBLICATION_METADATA_MISMATCH")
    semifinal_runtime_projection = (_verify_semifinal_runtime_projection(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    if metadata.get("semifinalRuntimeProjection") != semifinal_runtime_projection:
        raise SnapshotError("SEMIFINAL_RUNTIME_PROJECTION_METADATA_MISMATCH")
    compensation_publication = (_verify_compensation_publication(orgrebase) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    if metadata.get("compensationPublication") != compensation_publication:
        raise SnapshotError("COMPENSATION_PUBLICATION_METADATA_MISMATCH")
    oac_public_real_process_publication = (_verify_oac_public_real_process_publication(
        orgrebase
    ) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
    if (
        metadata.get("oacPublicRealProcessPublication")
        != oac_public_real_process_publication
    ):
        raise SnapshotError("OAC_PUBLIC_REAL_PROCESS_PUBLICATION_METADATA_MISMATCH")
    agentteams_source_bundle = _verify_agentteams_source_bundle(orgrebase)
    if metadata.get("agentTeamsSourceBundle") != agentteams_source_bundle:
        raise SnapshotError("AGENTTEAMS_SOURCE_BUNDLE_METADATA_MISMATCH")
    archive_path = output / ARCHIVE_NAME
    if not archive_path.is_file():
        raise SnapshotError("ARCHIVE_NOT_FOUND")
    if archive_metadata.get("sizeBytes") != archive_path.stat().st_size:
        raise SnapshotError("ARCHIVE_SIZE_MISMATCH")
    if archive_metadata.get("sha256") != _sha256_file(archive_path):
        raise SnapshotError("ARCHIVE_DIGEST_MISMATCH")
    metadata_inventory = _metadata_inventory(metadata)
    audit = _audit_zip(archive_path, profile=profile)
    if metadata.get("status") != "PASS" or metadata.get("audit") != audit:
        raise SnapshotError("METADATA_AUDIT_INVALID")
    if (
        archive_metadata.get("fileEntries") != audit["fileEntries"]
        or archive_metadata.get("directoryEntries") != audit["directoryEntries"]
        or archive_metadata.get("totalEntries") != audit["fileEntries"] + audit["directoryEntries"]
    ):
        raise SnapshotError("METADATA_ARCHIVE_ENTRY_COUNTS_INVALID")

    with tempfile.TemporaryDirectory(prefix="orgrebase-source-snapshot-verify-") as temporary:
        temporary_root = Path(temporary)
        extracted_root = _safe_extract(archive_path, temporary_root / "extracted")
        runtime_closure = _verify_release_runtime_closure(extracted_root, profile=profile)
        if metadata.get("runtimeClosure") != runtime_closure:
            raise SnapshotError("METADATA_RUNTIME_CLOSURE_INVALID")
        extracted_semifinal_projection = (_verify_semifinal_runtime_projection(
            extracted_root / "orgrebase"
        ) if profile == "full" else {"status": "NOT_INCLUDED_RUNTIME_PROFILE"})
        if extracted_semifinal_projection != semifinal_runtime_projection:
            raise SnapshotError("EXTRACTED_SEMIFINAL_RUNTIME_PROJECTION_MISMATCH")
        metadata_comparison = _compare_inventories(
            metadata_inventory,
            _inventory(extracted_root),
            reason="ARCHIVE_METADATA_TREE_MISMATCH",
        )

        current_root = temporary_root / "current" / SNAPSHOT_ROOT_NAME
        _prepare_snapshot(orgrebase, oac, current_root, profile=profile)
        if _verify_release_runtime_closure(current_root, profile=profile) != runtime_closure:
            raise SnapshotError("CURRENT_RUNTIME_CLOSURE_MISMATCH")
        source_comparison = _compare_inventories(
            _inventory(current_root),
            _inventory(extracted_root),
            reason="CURRENT_SOURCE_TREE_MISMATCH",
        )
        replay_archive = temporary_root / "replay.zip"
        _build_zip(current_root, replay_archive)
        if replay_archive.read_bytes() != archive_path.read_bytes():
            raise SnapshotError("CURRENT_SOURCE_ARCHIVE_BYTES_MISMATCH")

    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "PASS",
        "archive": {
            "name": ARCHIVE_NAME,
            "sha256": _sha256_file(archive_path),
            "sizeBytes": archive_path.stat().st_size,
        },
        "audit": audit,
        "metadataComparison": metadata_comparison,
        "currentSourceComparison": source_comparison,
        "deterministicArchiveReplay": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument(
            "--orgrebase-root",
            type=Path,
            default=Path(__file__).resolve().parents[1],
            help="OrgRebase source root (defaults to this checkout)",
        )
        subparser.add_argument(
            "--oac-root",
            type=Path,
            required=True,
            help="Required independent oac-spec source root",
        )
        subparser.add_argument("--output-dir", type=Path, required=True)
        subparser.add_argument("--profile", choices=("full", "runtime"), default="full")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            result = build_source_snapshot(args.orgrebase_root, args.oac_root, args.output_dir, profile=args.profile)
        else:
            result = verify_source_snapshot(args.orgrebase_root, args.oac_root, args.output_dir, profile=args.profile)
    except SnapshotError as exc:
        print(f"SOURCE_SNAPSHOT_ERROR:{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                "schemaVersion": result["schemaVersion"],
                "archive": result["archive"],
                "outputDir": str(args.output_dir),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
