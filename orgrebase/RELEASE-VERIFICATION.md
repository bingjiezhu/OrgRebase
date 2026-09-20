# OrgRebase 0.4.0 历史发行验证（2026-09-02）

验证日期：2026-09-02

> **历史检查点。** 下列计数、Golden 运行、源码快照和媒体提交结论只适用于
> 2026-09-02 当日绑定的制品，不是本次或后续源码发布版的通过凭证。

本页保留当日代码与冻结证据实际证明的范围。`PASS` 不自动等于真实企业验证或生产就绪；尚需外部环境完成的事项在对外材料中统一表述为“待企业环境验收”，机器证据继续保留可校验的原始状态值。新制品必须重新绑定其源码、工作区门、全新安装结果、依赖审计和发行包随附验证，不得沿用本页计数。

## 结论

截至该历史检查点，OrgRebase 0.4.0 已形成一个可复验的 **变化驱动选择性 Rebase 受控闭环 MVP**；企业报价是当时的落地验证切片，而不是产品边界：

```text
Enterprise Source
  → OAC contract admission
  → native AgentTeams Taskflow
  → authenticated HTTP Tool
  → Tool-bound enterprise-quote-compose Skill
  → OTLP traces / logs / metrics
  → CANDIDATE_ACCEPTED
  → exact human-owner approvals
  → VMRC selective Rebase
  → governed successor state and run archive
```

2026-09-02 Golden 业务链使用同一 `run_id` 串起 OAC 任务形成、AgentTeams、Tool、Skill、确定性验收、两次服务端人工批准、两次选择性 Rebase、Quote v3 与完成态档案。智能体候选层规范写入为 `0`；只有 OrgRebase 控制面能在精确负责人批准后写入后继状态。该证据证明当日受控本地闭环，不证明已接入企业生产系统，也不证明后续制品。

## 2026-09-02 机器验证

| 检查 | 当日结果 | 证明范围 |
| --- | --- | --- |
| OrgRebase 全量测试 | **1,475 passed** | 当日绑定仓库的 Python 行为回归；聚焦测试是其子集，不与全量数相加 |
| OAC 全量测试 | **480 passed** | 当日 sibling `oac-spec` 参考实现与标准资产 |
| Ruff | **PASS** | `src`、`tests`、`scripts` 静态检查 |
| Schema 导出 | **96 项，无漂移** | 5 legacy + 91 Workspace schema |
| 打包运行资产 | **192 / 192 passed** | mechanism/contract conformance 与 15 个 staged assets；不是 192 个企业案例 |
| ProductPath built-wheel black box | **12 / 12 passed** | 冻结合成 Northstar/Acme Profile 上的 HTTP 黑盒路径、3 次进程重启和 Approval/State overlap |
| ProductPath 结果与 Source 绑定攻击 | **9 / 9 rejected** | 结果或 Source 绑定被替换时 fail closed |
| ProductPath evaluator / gate 攻击 | **5 / 5 + 2 / 2 rejected** | 独立 evaluator 与 gate 边界不能被结果伪造绕过 |
| 当日 Golden 复赛交付 | **PASS** | 真实 Vertex `gemini-3.8-flash` 两次审查建议、Quote v3、40 个 AgentTeams actions、7 个 task bindings、5 个独立本地 Worker 进程、2 个 Reviewer 进程、两次服务端 Owner 门、同-run Tool / Skill 与 `APPROVED_CANARY`；`current_semifinal_delivery` 是当日 manifest 的字段名，不表示后续发布状态 |
| Retained Spec 045 兼容基线 | **PASS / HISTORICAL** | 34 个 actions / 6 个 bindings 的 Source + native AT + Tool + Skill + OTLP 同-run 候选链；仅作该历史阶段的非回归，不代表后续 Golden |
| Retained Spec 045 语义攻击 | **9 / 9 rejected** | Coalition 替换、OTLP 顺序/父链/状态替换、Tool 替换、权威根漂移、遥测隐私破坏、公开主机路径和未审查制品搭便车均被拒绝 |
| OAC Governed Evolution | **PASS** | 合成受控 51-artifact / 13-event 参考闭环、独立 evaluator、双 wheel 复放 |
| 复赛代码/证据闭包 | **PASS** | 当日双仓源码快照含 1,452 个文件；独立 build + verify 通过，凭据、本机路径、禁用路径、重复项、符号链接与特殊文件均为 0 |
| 08-31 外层媒体提交包 | **PASS / HISTORICAL** | 当时的 PPT/PDF/源码 ZIP/视频已通过 shallow 与 fresh-extraction deep 校验；它是历史提交封口，不替代任何后续源码验证 |
| CycloneDX SBOM | **PASS，33 components** | 当日 Python/runtime 组件清单的结构与版本绑定 |
| wheel | **0.4.0 clean build PASS** | 干净构建并从 wheel 字节复放当日运行资产 |
| AgentTeams 源码可重建性 | **PACKAGED COMPLETE GIT BUNDLE** | Apache-2.0 上游 v1.2.2 exact commit 可离线重建并校验 TeamHarness 源码；冻结 Python 依赖首次安装仍可能需要本机缓存或包索引 |

这些命令是该历史检查点的复现坐标；只有在精确恢复当日源码和依赖后，结果才能与上表比较。在更新后的工作树中执行会形成一次新验证，不能据此回写或沿用上表计数。

历史全量验证命令：

```bash
uv run ruff check src tests scripts
uv run pytest
uv run pytest tests/workspace
uv run python scripts/validate_assets.py
uv run python scripts/export_contract_schemas.py --check
uv run python scripts/verify_packaged_runtime_assets.py
uv run python scripts/generate_sbom.py \
  --artifact evidence/semifinal-closure/latest/artifacts/orgrebase-0.4.0-py3-none-any.whl \
  --output evidence/semifinal-closure/latest/operations/operations/sbom.cdx.json \
  --check
make semifinal-mvp-check
(cd ../oac-spec && uv run ruff check src tests scripts && uv run pytest)
```

当日源码快照不包含 PPT、PDF 或 Demo 视频。当日完整正式提交闭包的验收入口是在对应外层提交根运行：

```bash
python3 -B verify-submission.py --deep
```

仓库内 `make goai-semifinal-check` 只适用于另行组装了 `submission/` 媒体的开发 checkout，不是 source snapshot 的复验入口。后续正式提交包应使用该包随附的校验器和清单。

## 本次及后续候选的验证要求

本页不授予新候选发布资格。新候选至少应在与其绑定的工作区执行 `make check-core`；满足 sibling OAC、PostgreSQL 及其他前置条件后执行完整 `make check`，并保存实际输出。发行包还必须核对随附的源码清单、版本验证、全新安装复核和文档链接复核；组合源码包使用其同版 `source-snapshot-metadata.json` 和构建器的 `verify` 模式复验。发布结论以新制品自己的摘要和回执为准。

冻结的 Spec 062 适配证据使用旧 typed 摘要投影，其只读复验入口为：

```bash
uv run python scripts/verify_oac_quote_adaptation.py \
  --root evidence/oac-quote-adaptation/latest --retained-build --project-root .
```

该入口核对既有封存 OAC wheel 的固定摘要，在隔离 Python 进程中执行同一公共验证器，并明确返回 `RETAINED_ARTIFACT` 和 `current_release_qualified=false`。它不是原构建环境的精确复刻，也不授予后续资源准入资格。后续资源应通过 `--oac-root` 指定的新 CLI 验证；两种选择互斥，失败不会自动回退。新生成的证据应使用单独的输出目录，保留冻结材料原字节。

## 五项建议的历史完成度

### 1. 单一主用户与报价流程价值

**截至 2026-09-02 已完成到 synthetic/modelled 验证。** 单一主用户固定为 Enterprise Quote Operator；报价员工、产品、法务、财务和 GTM 的系统、八步流程、时限、RACI 与升级条件已形成可执行参考模型。

当日量化结果：

- 参考 `ReferenceWorkspaceBenchmarkSUT` 的 72 个变更用例与预声明 gold 一致；`0 / 72` 是契约一致性失配数，不是实际 `ImpactEngine` 的漏改率或客户业务错误率。
- 8 个决策案例中 2 个进入 `HOLD_FOR_REVIEW`。
- 参考 SUT 的 32 个安全用例与预声明错误码一致；不据此宣称实际生产授权系统的拦截率。
- 未校准成本系数下，声明的压力场景范围为 -10.0%–59.9%，基础场景为 49.4%（LOW/HIGH 为35.0%–59.9%）。这些是含负收益的敏感性算术，不是实测节省或置信区间。
  分母 `8.00` 假设八个目标全部重建；旧流程基线仍为 `NOT_RUN`，不是观测成本。

真实企业的报价周期、政策确认工时、漏改率、返工率、人工升级率和 ROI 仍待企业环境验收，不得用上述合成/模型值替代。

### 2. AgentTeams 原生任务链与权威边界

**2026-09-02 Golden 已完成到 controlled-local。** 当日复赛主链使用 AgentTeams v1.2.2 exact commit 的真实 TeamHarness `server.py::call_tool`，在同一 Golden run 跑出 **40 个 native actions、7 个 task bindings、5 个独立本地 Worker 进程和 2 个 Reviewer 进程**，并连接 Tool、Skill、两次服务端 Owner 门与 Quote v3。

**34 个 actions / 6 个 bindings 是 retained Spec 045 的更早兼容基线，不是 2026-09-02 Golden。** 它保留创建、规划、ready、委派、重复委派拒绝、接单、上下文交接、提交、检查、验收、取消、重派、迟到结果拒绝和终态等机制非回归；不得用它描述新制品。

权威边界为：AgentTeams 只负责 proposal/task transport；OrgRebase 确定性控制面负责策略、验收、冲突裁决和规范写入资格。Worker 只能产出 candidate，不能直接写权威状态。

2026-09-02 Golden 已在同一 run 内完成 controlled-local Owner Approval/Apply。另一个 Core proposal-plane `LIVE_AGENTTEAMS` 运行以及 retained Spec 051 两次真实 SIGKILL + SQLite WAL 恢复已在独立 run/证据包中成立，但不能跨 run 提升为该日 Golden 证据。Golden/Workspace 自主分布式 Worker、Golden Worker/Reviewer killed-process resume、OAC Resume 与外部真人/IAM Approval 仍待生产环境验收。

### 3. 三个 Skill 的同等级生命周期

**截至 2026-09-02 已完成到 run-local。** 当日 registry heads 为 `enterprise-launch-readiness@1.4.2`、`enterprise-quote-compose@1.3.1` 与 `structured-domain-handoff@1.1.2`；三者均具备可发现、可装载、可调用的 package/contract/program，具有评测分区、准入门槛、release receipt 和 rollback decision。quote-compose 还强制绑定同-run 非零 Tool result/receipt。每个包以单一 canonical `SKILL.md` 作执行入口，在 `description` 中提供中英文发现词，按需读取中英参考文档，并用中文、英文、中英混输和中文文件名用例防止语言路径漂移。

Spec 052 已在受控本地将 `enterprise-quote-compose@1.3.1` 回滚到其 exact direct predecessor `1.3.0` 并调用受限程序；幂等重放、进程状态重建、权限/系谱拒绝、幂等冲突和 Tool-binding 篡改拒绝均通过，且 `target_writes=0`。尚未完成的是跨进程持久化签名 trust store、生产 rollout rollback、跨企业资格与外部真实分布的统计性对抗评测。

### 4. Tool、OTLP 与运维闭环

**截至 2026-09-02 已完成到 controlled-local。** 同一运行链接通 loopback HTTP Source/Tool、认证、ETag、schema binding、OTLP/HTTP traces/logs/metrics、SQLite 查询与留存、告警规则、容量 smoke、备份恢复原语、部署配置和 SBOM。

尚未完成：真实 CRM/CPQ/ERP/CLM/知识库连接器、生产观测后端、多租户容量压测、真实 SLA、跨故障域 HA/DR 演练。

### 5. 可执行提交包

**截至 2026-09-02，代码侧已满足当日制品的可复验封装条件。** 源码快照包含完整 exact-commit AgentTeams Git bundle、许可证、lock、Spec 041–045、冻结证据、parent verifier、五类 mutation verifier、wheel 与 SBOM。快照会拒绝未登记机器本地路径；少量不可改写的历史 OAC 证据使用精确 digest 绑定例外，后续 successor 必须迁移到 `oac://` 逻辑坐标。

正式复赛 13 页可编辑 PPTX、高保真 PDF、源码 ZIP、350.840 秒单源连续无声 Demo 和附录由外层闭世界校验器绑定字节，并完成 fresh-extraction deep 复验。该 `PASS` 仍只是提交完整性和可复现性，不等于真实企业 UAT 或生产就绪。

## 证据坐标

- 历史 Spec 041–045：已移出当日可运行代码树；该历史验证只依赖下列当日代码、证据和 verifier
- 企业报价运行模型：[`docs/ENTERPRISE-QUOTE-OPERATING-MODEL.md`](docs/ENTERPRISE-QUOTE-OPERATING-MODEL.md)
- 集成闭环摘要：[`evidence/semifinal-closure/latest/summary.json`](evidence/semifinal-closure/latest/summary.json)
- 集成闭环索引：[`evidence/semifinal-closure/latest/evidence-index.json`](evidence/semifinal-closure/latest/evidence-index.json)
- 当日发行事实：[`evidence/release-facts.json`](evidence/release-facts.json)
- AgentTeams source lock：[`agentteams/teamharness-lock.json`](agentteams/teamharness-lock.json)
- 第三方清单：[`docs/THIRD-PARTY-INVENTORY.md`](docs/THIRD-PARTY-INVENTORY.md)

## 发行边界

```text
competition_technical_mvp                  GO
isolated_read_only_single_enterprise_pilot CONDITIONAL_GO
arbitrary_enterprise_production            NO_GO

enterprise_quote_operating_model           VALIDATED_SYNTHETIC_AND_MODELLED
agentteams_native_taskflow                  VALIDATED_CONTROLLED_LOCAL
skill_package_lifecycle                     VALIDATED_RUN_LOCAL
source_tool_otlp_operations                 VALIDATED_CONTROLLED_LOCAL
golden_workspace_approval_apply_same_run    VALIDATED_CONTROLLED_LOCAL_HEADER_IDENTITY
external_human_approval                     PENDING_ENTERPRISE_VALIDATION
live_agentteams_core_proposal_plane         PASS_SEPARATE_RUN
golden_workspace_autonomous_distributed_at  PENDING_ENTERPRISE_VALIDATION
real_enterprise_connectors                  PENDING_ENTERPRISE_VALIDATION
real_enterprise_value                       PENDING_ENTERPRISE_VALIDATION
production_ha_dr_sla                        PENDING_ENTERPRISE_VALIDATION
```

上述状态是 2026-09-02 检查点的 claim ceiling。任何演示、报告或对外介绍都不得把 controlled-local、synthetic/modelled 或 run-local 提升成真实企业生产结论，也不得把这些历史结果直接归给后续制品。
