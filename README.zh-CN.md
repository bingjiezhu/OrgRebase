<p align="right"><a href="README.md">English</a></p>

# OrgRebase

源码可用工作区：产品 **OrgRebase** 与组织智能体契约 **OAC**。一次 clone 包含两棵树。

OrgRebase 使用 PolyForm Noncommercial 1.0.0，**不是** OSI 开源。OAC 在自己的路径上保持 Apache-2.0 与 CC BY 4.0。

[![CI](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml/badge.svg)](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/bingjiezhu/OrgRebase)](https://github.com/bingjiezhu/OrgRebase/releases)

| 路径 | 内容 | 版本 |
|---|---|---|
| [`orgrebase/`](orgrebase/README.zh-CN.md) | 控制面、WebUI、Skill、Schema、测试、报价复算 | 0.4.0 |
| [`oac-spec/`](oac-spec/README.md) | 契约、Schema、编译器、验证器、TCK | 0.3.0a0 |

## 第一次运行（无需模型、无需 OAC CLI）

需要 CPython 3.12–3.14 和 [uv](https://docs.astral.sh/uv/)。安装依赖需要网络；复算本身不需要云凭据、PostgreSQL 或 OAC。

```bash
git clone https://github.com/bingjiezhu/OrgRebase.git
cd OrgRebase/orgrebase
uv sync --locked --all-extras
uv run --frozen python scripts/run_public_quote_replay.py \
  --output ../quote-first-run --limit 1
```

输出目录必须事先不存在。打开 `../quote-first-run/index.html`。发票 `560602` 应为 `status=PASS`。贡献检查：在 `orgrebase/` 执行 `make check-core`。

## 开放范围

核心代码、Agent 身份、Skill、Schema、接口、测试与评测入口见英文 [README](README.md#what-is-published)。复用边界见 [REUSE-AND-LICENSING.md](orgrebase/docs/REUSE-AND-LICENSING.md)。部署见 [认证部署](orgrebase/docs/AUTHENTICATED-DEPLOYMENT.md)。

AgentTeams v1.2.3 是 Apache-2.0 上游，以 Git bundle 锁定供离线重建。仓库内适配器不改变其许可，也不把受控本地证据升级为分布式生产部署。

完整 Vertex 旅程把 `ORGREBASE_OAC_ROOT` 指到本仓库的 `oac-spec/`。凭据留在运行环境。

## 贡献与安全

Issue（缺陷 / 功能 / 复用反馈）、[社区与复用说明](COMMUNITY.md)、[贡献指南](orgrebase/CONTRIBUTING.md)、[安全报告](orgrebase/SECURITY.md)、[Releases](https://github.com/bingjiezhu/OrgRebase/releases)。

AgentTeams 以 Apache-2.0 上游锁定在本仓库中，不是对 AgentTeams 的上游贡献，也不宣称无关第三方的生产采用。复用尝试请用 Reuse Issue 模板登记。

许可索引：[LICENSES.md](LICENSES.md)。OrgRebase 商业使用需要单独书面授权。
