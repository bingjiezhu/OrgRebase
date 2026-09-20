<p align="right"><a href="README.md">English</a> · <strong>简体中文</strong></p>

# Organizational Agent Contract（OAC）

**OAC 是一套实验性的、厂商中立的企业工作组织契约。** 它描述一次任务依赖哪些企业事实和目标，
临时组织必须覆盖哪些义务，必须遵守哪些权限与顺序约束，以及经验在成为受治理的新版本之前
必须留下什么证据。

```text
企业 Source + Demand
  → 一组满足契约的组织
  → 独立认证的 Plan
  → Runtime-owned Execution 引用
  → 独立认证的 Outcome
  → 演化候选
  → 受治理的不可变 Source successor
```

OAC 不是 Agent、Workflow Runtime、IAM、知识库或企业应用。Codex、Claude、LangGraph、
AG2、人类工作流或其他 Runtime 都可以消费 OAC Profile。标准定义组织契约和接受关系，
不执行工作，也不会因此获得企业真相权威。

## 核心思想

OAC 定义的是**可接受组织集合**，不是唯一 Agent 图。两个 Plan 可以使用不同角色、
WorkUnit 和边，只要都满足同一组冻结的义务、资格、职责分离、偏序、证据要求和
`UNKNOWN` 语义，就都可以合格。

因此以下权威必须分开：

- 编译器可以提出 Plan，但不能给自己的 Plan 发证；
- Runtime 可以记录执行，但不能给自己判业务成功；
- Outcome 权限可以给出有边界的结果，但不能发布新 Source；
- 候选生成器可以提出可复用程序，但不能自我准入；
- 治理权限批准精确的后继字节，不能只给 `promote()` 一张事后自由发挥的空白支票。

[相关工作与贡献边界](docs/architecture/related-work.md) 说明这些契约设计与授权一致性、来源追踪及受治理 Agent 系统的关系。

## 已实现的最小生命周期 Profile

Spec 009 在既有 Source、Plan 和 lowering 资源之上增加了三种严格、可移植的 Kind：

| Kind | 它记录什么 | 它不会自动授予什么 |
|---|---|---|
| `OrganizationalDemand` | 请求者、问责角色、精确 Source、目标、期望结果、证据义务、约束和效果上限 | Runtime 或 Source 权限 |
| `SourceAdmissionReceipt` | 对精确 Source 字节的权威决定，包括前驱/后继与候选/治理引用 | 不能因为有收据就制造事实真值 |
| `OutcomeCertificate` | 对精确 Source、Demand、Plan、Runtime-owned Execution 和 Observation 引用的独立结论 | 经验自动晋升 |

这三种 Kind 已进入模型、Kind Registry、生成 Schema、安装包、公共 CLI 与 evolution
TCK/证据门。Profile 还定义了确定性、域分离的 `S/D/P/X/O/E` 根，并拒绝根漂移、跨 namespace
替换、自我认证、非法正向晋升和前驱漂移。

`ExecutionReceipt`、`OutcomeObservation`、Procedure candidate、治理事务和活动 Source
指针仍是实现侧扩展，通过精确 ResourceRef 接入。**本仓库不拥有 Runtime。**

正式契约见 [Spec 009](specs/009-proof-carrying-evolution-minimum-profile/spec.md)。它的
Source/root-vector 证据仍是单参考实现的有界证明；历史 Spec 状态和外部独立性门继续显式保留。

## 既有 Supplier Profile 与多种 Plan

首个 Profile 面向供应商状态变化：

```text
OrganizationSnapshot + SemanticChangeSet
                ↓ 参考编译器
         OrganizationPlan
                ↓ 与编译器分离的验证器模块
          PlanCertificate
                ↓ 受控零效果 lowerer
 ZeroEffectRuntimeBundle + lowering receipt
```

编译器和验证器不会互相调用，但仍共享公开 Supplier Profile derivation relation。
这证明模块分离和拓扑独立，不是外部 clean-room 语义独立实现。

当前有界公开坐标证明：

- 主体、范围、关系和三值适用性语义，且 `UNKNOWN` 不被抹除；
- SC-008 同一义务契约下两个结构不同的合格 Plan；
- 对义务遗漏、自审批、非法顺序、伪造适用性、摘要漂移和 Unknown 擦除的注册负例；
- 冻结坐标上的内部 Python/Go 差分与 mutation 证据。

OAC 对 EDiTh 的专用标注仍是 exploratory，不是合格专家 Ground Truth。同仓库 Go
实现是有用的反证种子，不代表组织独立或认证。

## 外部 OrgRebase 参考旅程

平级 OrgRebase 仓库通过公共 OAC CLI 和 wire 资源运行一个合成受控闭环：

```text
Source/Demand 准入
  → BASE 与 SPLIT Plan
  → runner-owned 零效果执行
  → controlled Observation
  → 独立 ACCEPT/REJECT Outcome
  → 拓扑无关 Procedure candidate
  → 精确字节治理
  → 不可变 Profile/Snapshot successor
  → 指针回滚
```

这条旅程是该 Profile 的**外部参考实现**，不是 OAC 自己拥有 Runtime，也不是标准的生产证明。
当前证据仅为合成、零外部效果和 scripted governance。真人审批、真实企业使用、经过企业 IAM
认证的 Source 与生产部署仍为 `NOT_RUN`。

在双目录源码包中运行：

```bash
cd orgrebase
uv sync --all-extras
make workspace-oac-evolution-check
```

可辩护的组合结论属于 OrgRebase Reference：一个完整 OAC 合成受控闭环，加一个初赛 Quote
字节级非回归闭环。它不等于 OAC 标准自己执行了企业工作，也不证明跨企业泛化。

## 快速开始

需要 Python `>=3.12,<3.15` 和 [uv](https://docs.astral.sh/uv/)。
当前已验证基线为 **CPython 3.12.13**。安装范围也允许 3.13 和 3.14，但这些版本尚无同等的安装与行为验收记录。
复现这些记录时，请显式选择 CPython 3.12.13。

```bash
uv sync --all-extras
uv run oac demo
uv run oac tck
make evolution-evidence-check
make runtime-lowering-check
make check
```

公共命令：

```text
oac validate             严格校验注册资源
oac validate-evolution   Spec 009 语义校验
oac digest               RFC 8785 + SHA-256 detached digest
oac compile              Supplier Profile 参考编译器
oac verify               与编译器分离的 Plan 验证
oac lower                受控零效果 lowering
oac registry             机器可读 Registry
oac tck                  manifest-driven 开发 TCK
```

`make check` 会对检入路径和 installed-material commitment fail closed。解压或搬迁后的副本
使用 `make archive-replay-check`：它重算当前 Plan-verification 结果，只把声明的跨主机可移植语义投影与不可变 seed-1 坐标比较，然后运行同一复合门。它不会重写冻结的 parity summary、installed ledger 或 evidence manifest。

## 证据边界

| 主张 | 状态 |
|---|---|
| 严格 wire Kind、detached digest、Registry、Schema 和安装包 | 已实现并检查 |
| Contextual Supplier Profile 与有界 plural-valid Plan relation | 在冻结公开坐标上已实现并检查 |
| Spec 009 最小 Demand/Admission/Outcome Kind 与生命周期根负例清单 | 已实现并检查 |
| 零效果参考 lowering | 已实现；仍需外部 Runtime 二次准入 |
| 外部 OrgRebase 合成受控参考闭环 | 在 OrgRebase 中为 `PASS`；不是 OAC 生产证据 |
| 合格真人 Ground Truth | `NOT_RUN` |
| 外部维护的语义实现 | `NOT_RUN` |
| 真实企业效果、Runtime 可移植性或生产授权 | `NOT_RUN` / 不主张 |

OAC 的准确状态仍是 experimental proposed draft。它不主张正式认证、验证器完备性、
企业效果或自主组织演化。

## 仓库地图

| 路径 | 唯一责任 |
|---|---|
| `standard/` | 提议中的 Core、Conformance、适用性、lowering 与 identifier 规范文本 |
| `schemas/` | 生成 wire Schema 与 Kind/reason Registry |
| `profiles/` | Supplier Profile、来源信息和探索性标注 |
| `src/oac/` | 非规范参考模型、compiler、verifier、lowerer、roots 与公共 CLI |
| `tck/` | 开发用正例、负例、mutation、repeatability 与 evolution vectors |
| `ctk/` | 自包含 bundle、协议、Schema 与 code-independent runner |
| `implementations/` | 已披露的同仓库跨语言反证种子 |
| `experiments/` | 绑定修订的可移植性与 disagreement 证据 |
| `specs/` | Spec Kit 需求与 evidence-gated proposals |
| `docs/` | 决策、研究、架构、验证报告与[路线图](docs/ROADMAP.md) |

## 许可证

规范文本使用 [CC BY 4.0](LICENSES/CC-BY-4.0.txt)。Schema、TCK/CTK、示例和参考代码使用
[Apache-2.0](LICENSES/Apache-2.0.txt)。详见 [LICENSE.md](LICENSE.md)、[NOTICE](NOTICE.md)、
[CONTRIBUTING](CONTRIBUTING.md) 与[专利不主张](PATENT-NON-ASSERTION.md)。
