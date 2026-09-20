# Enterprise Quote Pilot Runbook

> 开始前先读 [L0 产品真相](SYSTEM-MAP.md)。本 Runbook 只说明如何运行受控本地参考旅程，
> 不将历史 Golden、合成输入或本地执行提升为真实企业或生产证据。

## 适用范围

本入口用于单企业、单报价场景的受控本地 Pilot。当前公开 Golden 在同一业务 `run_id` 内实际完成
OAC 激活消费、Task Formation、最小上下文、pinned AgentTeams、独立 Worker/Reviewer、HTTP Tool、
quote-compose Skill、两次业务人工批准、选择性 Rebase、Quote 内部修订 3，以及运行后的受治理经验候选。
当前公开 Golden 来自真实 Vertex `gemini-3.8-flash` 运行，并已清除凭据、GCP 项目标识、项目端点、
原始提示与自由文本响应；无参数入口使用 `interactive`，完成点击旅程后会得到新的本地 Ollama 运行，但不会替代这条冻结证据。

## 1. 一键点击完整 OAC 到业务旅程

以下命令在 `orgrebase/` 仓库根目录执行。参考运行使用 CPython 3.12.13、uv 和本机摘要匹配的 Ollama `qwen2.5:3b`；独立 OAC 仓库默认位于相邻的 `../oac-spec`，其他位置通过 `ORGREBASE_OAC_ROOT` 指定。

```bash
uv sync --locked --all-extras
./run-semifinal-demo.sh
```

无参数入口默认为 `interactive`：创建新的临时状态，从企业包生成当前 OAC 草案，经负责人准入后进入员工需求、Quote 内部修订 3 和经验治理。OAC 准入真实决定该运行的领域、最小上下文和 AgentTeams 执行计划。每个需人工决定的步骤都由有权负责人显式确认。该运行拥有新的 `run_id`，业务候选使用本地 Ollama，不会冒充公开 Golden，也不会新调用 Vertex。

原 `guided` 模式已退役，显式调用会在任何状态写入或 provider 启动前拒绝。需要重开已激活工作区时，设置 `ORGREBASE_DEMO_WORK_DIR=/path/to/existing-state` 后运行 `interactive`；历史记录仍可读，新执行仍须满足当前版本的准入绑定。

启动入口仅绑定本机回环地址，默认端口 8000；如端口占用，设置 `ORGREBASE_DEMO_PORT` 选择空闲端口，不会替换已有服务。首次进入「企业接入 → OAC 组织契约」，点击「从已配置材料生成契约候选」，再由指定负责人审阅并准入；这是确定性材料编译，不是 Vertex 映射调用。

## 2. 独立复核正式封存证据

不需要云凭据或模型服务：

```bash
uv run python scripts/verify_golden_pilot_evidence.py \
  --root evidence/golden-competition/latest/pilot
```

verifier 只使用 Python 标准库，必须独立复算 `status=PASS`、`public_state_verification=PASS`、
`causal_verification=PASS` 和 `experience_verification=PASS`。公开包包含 101 个内容寻址制品，并验证
同一 `run_id` 下的 OAC、Task Intake、AgentTeams、Tool、Skill、两次批准、Rebase 和经验治理。它故意
不包含 `workspace.sqlite3` / WAL / SHM 或员工原始工作说明。封存证据不是可执行产品状态；需要产品
演示时必须用第 1 节的新鲜旅程。

## 3. 可选：生成新的 Vertex 候选运行

凭据只能通过本机 Application Default Credentials 或进程环境提供；不得写入 `.env`、源码、日志、证据目录、
提交 ZIP 或本文档。推荐 ADC：

```bash
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT="your-project-id"
uv sync --locked --all-extras
ORGREBASE_VERTEX_PROJECT="$GOOGLE_CLOUD_PROJECT" ./run-semifinal-demo.sh live
```

fresh run 会生成新的 `run_id`；不得把它冒充上面的当前公开 Golden。Vertex 必须返回两个不同 provider response ID，
新 `live` 入口默认请求 `gemini-3.8-flash`（可显式选择 `gemini-3.7-flash` 用于兼容性复核），两次结果均须满足 `STOP / LOW / schema valid`。模型仅向确定性 Reviewer 提供建议；
即使模型建议与确定性结论不一致，也只能被记录或覆盖，不能批准、发布或写入 Quote。

无 Vertex 凭据但希望新生一次受控本地 run 时，可运行离线可选路径：

```bash
./run-enterprise-pilot.sh
```

它使用 Ollama 离线 profile，只用于本地/CI 新鲜运行。服务只允许 loopback host；
默认身份模式是 `CONTROLLED_LOCAL_HEADER_IDENTITY`，不代表企业 IAM。

## 4. 人工完成 Quote v1→v3 与经验治理

1. 接入数据与 OAC 准入完成后，员工提交并确认工作说明；同一事务生成 Task Intake、Formation、最小上下文和 AgentTeams 执行计划。
2. Golden formation 完成后，页面进入内部工作稿。
3. 生成 launch-date Preview；服务端进入等待。倒计时归零后仍必须由 Product Owner 点击批准。
4. deterministic control 消费 exact Approval，选择性 Rebase 到产品调整稿。
5. 生成 currency Preview；再次等待。Finance Owner 显式点击后进入当前内部稿。
6. 核对当前内部稿的 `launch_date=2026-10-15`、`currency=EUR`，以及从企业接入到完成态的 16 项摘要链事件。
7. Quote 已完成后进入「能力与保障 → Skill 能力」：系统只能提炼 `IMPROVE existing structured-domain-handoff` 的
   `SINGLE_RUN_SEED` 候选；八个评测分区全部通过后，仍须由 `human:skill-steward` 等待至少 4 秒并点击批准。
8. 批准后核对 `APPROVED_CANARY`、discover/load/invoke、`RELEASE` dry-call `SUCCESS`、
   `current_quote_consumed_candidate=false` 与 canonical writes=`0`。这是后续运行的候选能力，不会倒灌本次 Quote。
9. 在「变化处置 → 业务数据演化与验收」核对同一 `run_id` 的 OAC、Agent、Reviewer、Model、Tool、Skill、Approval、Apply 和最终 Quote；独立机制与工程档案不得补作当前任务证据。

变化工作台仅在当前账号有执行权限、且批准后回读的同一提案仍允许 `APPLY` 时自动衔接应用；仅有批准权限时保留批准，交由有权执行者应用。应用结果未确认时先核对状态，仍待应用才重试 Apply，无需再次批准。

提前批准返回 `REVIEW_WINDOW_OPEN`；wrong owner、stale Preview 或摘要漂移必须失败且不产生目标写入。
Skill Steward 的审批是能力发布治理，不是第三次业务 Quote 审批。

控制台默认简体中文。点击 English 后再切回中文时不得刷新页面，也不得丢失当前 run、tab、stage、state、
展开详情或审批计时；`AgentTeams`、`Skill`、`Tool`、`run_id`、`digest` 等专有名词保持稳定。

## 5. 三个顶层入口与演示顺序

当前控制台只有三个顶层入口，原十页内容已收进页内标签。旧路由别名仍用于兼容历史链接，不代表另有十个操作入口。

| 顶层入口 | 页内标签 |
|---|---|
| 企业接入 | 企业材料、OAC 组织契约 |
| 变化处置 | 需求与当前交付、真实协作进度、业务数据演化与验收 |
| 能力与保障 | Skill 能力、历史验证档案、运行保障 |

完整操作按以下顺序：

```text
企业接入 / 企业材料 → OAC 组织契约
→ 变化处置 / 需求与当前交付 → 真实协作进度 → 业务数据演化与验收
→ 能力与保障 / Skill 能力 → 历史验证档案 → 运行保障
```

- 业务数据演化与验收只使用当前业务同一 `run_id`；
- 历史验证档案只使用明确标注来源、时间和边界的独立运行；
- 运行保障只显示当前健康/持久化和独立工程档案，不重复业务五段链。

三条路径可互相导航，但任一路径的 `PASS` 都不得补齐另一路径。

## 6. 验证当前公开 Golden 与完成态重启

当前 Golden 公开证据：

```bash
uv run python scripts/verify_golden_pilot_evidence.py \
  --root evidence/golden-competition/latest/pilot
```

verifier 仅使用 Python 标准库，不导入产品代码；它从 manifest 列出的全部内容寻址制品复算
Task/Process/Model/Tool/Skill/Formation/Approval/Apply/Experience 的顺序、内容摘要、权威边界和 Quote v3。

同一公开运行的完成态服务重启收据：

```bash
python3 scripts/verify_completed_state_restart_receipt.py \
  --receipt evidence/golden-competition/latest/pilot/restart/completed-state-restart-receipt.json \
  --before evidence/golden-competition/latest/pilot/restart/state-before.json \
  --after evidence/golden-competition/latest/pilot/restart/state-after.json \
  --golden-run evidence/golden-competition/latest/pilot/golden-run
```

该 verifier 只证明同一 SQLite 完成态在服务关闭和重开后保持同一运行、OAC/Task Intake 绑定、当前内部稿、
两次审批与经验治理状态；它不证明运行中 killed-process 续跑、分布式恢复或 HA/DR。

## 7. 使用企业自己的 Pack

```bash
./run-enterprise-pilot.sh init --output /path/to/draft
# 只编辑 draft 中的 JSON
./run-enterprise-pilot.sh seal \
  --draft /path/to/draft \
  --output /path/to/sealed-pack
./run-enterprise-pilot.sh start \
  --pack /path/to/sealed-pack \
  --work-dir /path/to/pilot-state
```

参考演示固定展示 Enterprise Quote 的 `launch_date`、`currency` 两次变化；当前 Pilot handler 另支持 `product_plan`。这不扩大本 Runbook 两次变化的实测声明。
换 Pack 不等于任意流程已经自动适配；新的流程族必须先提供契约、adapter 和验收证据。

## 8. `check` 健康检查的准确用途

```bash
./run-enterprise-pilot.sh check --pack /path/to/sealed-pack
```

`check` 是保留的确定性 Pack 健康检查；它不会运行当前 OAC-bound Golden AgentTeams 链，
也不能替代 OAC 接入、两次业务 Owner 点击或一次 Skill Steward 经验治理点击。评委稳定演示入口是无参数的
`run-semifinal-demo.sh`（等于 `interactive`）。`interactive` 和 `live` 都在启动层强制
OAC 先于报价业务进入同一受控链路。`make serve` 现在默认要求生产认证配置；单独的本地合成数据演示使用 `make serve-demo`。两者都不替代上述完整评审演示路径。

## 9. 故障与恢复边界

- 当前公开 restart receipt 已验证 SQLite 完成态服务重开后，OAC/Task Intake、当前内部稿、批准、16 项事件链和经验治理状态摘要不变；
- review window 在服务重启后不能缩短；
- stale/late result、wrong owner 和不匹配 Pack 均 fail closed；
- retained evidence 已验证 SIGKILL、OTLP、备份恢复与 Skill predecessor rollback；
- 本次 Golden Worker/Reviewer 运行中 killed-process durable resume、真实企业数据/连接器/ROI、分布式生产 AgentTeams、
  语义等价 full rebuild 的实际节省、外部 IAM/真人 UAT、生产 SLA/HA/异地 DR/多租户，以及经验多运行泛化仍为 `NOT_RUN`。
