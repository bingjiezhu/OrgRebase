# OrgRebase 产品 Demo Runbook

> 用户与评委首先以 [L0 产品真相](SYSTEM-MAP.md) 理解主用户、唯一主流程和证据上限。
> 本文说明当前产品运行路线；保留的复赛录屏段落仅用于历史制作复现。
> 正式视频、PPT、源码快照须按各次提交包自己的清单、摘要和验证记录核对，不能用本页证明冻结材料已随当前代码更新。

## 一句话主线

企业已经拥有 Quote 与依赖基线；Quote v1 基线先在同一 `run_id` 中跑通固定版本
AgentTeams 的创建、委派、接单、补证、Skill 调用与 Reviewer 验收。上游产品事实或财务政策
变化后，OrgRebase 依据已激活 OAC 冻结 ChangeSet，从持久依赖收据投影最小变化团队并复用
已准入能力。当前启动入口的 golden 模式会为这份 ChangeSet 执行新的原生 AT 任务链；
工作台按该次尝试的绑定回执显示任务进度。预演会保存候选与审计证据，
正式报价与规范规则保持不变，只通知真正受影响的 Human Owner；
一次批准后，当前账号有执行权限且回读仍允许 `APPLY` 时，控制面自动选择性 Rebase，并交付后继 Quote、字段差异、回执和回滚锚点；仅有批准权限时保留批准，交由有权执行者应用。

```text
AT completed
  != Candidate admitted
  != Reviewer accepted
  != Human approved
  != Canonical applied
```

## 同一公开商品篮子与 Vertex 原生运行

这条路径将 UCI 发票 557670 的商品篮子送入既有原生 AgentTeams / Vertex 链路，不另建计价或批准引擎。先按 [Vertex 配置](guide/models-vertex.zh.md)准备获授权的项目与凭据；从已安装依赖的产品目录执行。两个输出目录均使用新的名字，不能复用已形成报价或批准的工作区。

```bash
uv run --frozen python scripts/run_public_quote_replay.py \
  --output ../retail-vertex-input-new --demo-invoice 557670

ORGREBASE_OAC_ROOT="$(pwd)/../oac-spec" \
ORGREBASE_OAC_ADAPTATION_MODE=required \
ORGREBASE_OAC_EXECUTION_MODE=LIVE_VERTEX \
ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED=1 \
ORGREBASE_VERTEX_MODEL_ID=gemini-3.8-flash \
ORGREBASE_LOCAL_ROLE_SESSION_ORIGIN=http://127.0.0.1:8879 \
./run-enterprise-pilot.sh start \
  --pack ../retail-vertex-input-new/pack \
  --work-dir ../retail-vertex-runtime-new \
  --competition-mode golden --model-provider vertex-ai \
  --host 127.0.0.1 --port 8879
```

第一条命令只校验保留样本并封装定价 Pack，不执行 Formation、批准或 Apply。它输出的 `demo-workspace.sqlite` 路径与本节原生工作区不同；本节使用 `--work-dir` 下的新数据库。不要把纯确定性示例输出的 `start_command` 当作本节 Vertex 启动命令。

此命令启用可选的本地角色模式。浏览器地址、`ORGREBASE_LOCAL_ROLE_SESSION_ORIGIN` 和监听端口必须一致；本例统一使用 `http://127.0.0.1:8879`，不要改用 `localhost` 访问。省略该环境变量即可保留普通本地流程。

侧栏的“切换演示角色”列出服务端根据当前工作区定义的角色。先选择角色，再点击“使用此角色”；切换会清空未提交草稿，不删除已经登记的提案、批准或运行记录。会话使用浏览器 Cookie，变更请求经过 CSRF、角色权限和精确负责人检查。这里是本机角色模拟，不是员工登录或企业 SSO；生产部署仍使用 [OIDC 与服务端成员映射](AUTHENTICATED-DEPLOYMENT.md)。

打开 `http://127.0.0.1:8879/`，按以下身份顺序操作。企业材料已在启动时加载，由接入负责人首先核对其来源：

1. 选择“企业接入负责人”，核对企业材料与来源，生成组织契约映射候选并检查实际模型记录；完成三项确认和服务端审阅门后准入。组织准入不会批准报价规则变化。
2. 切换为“业务发起人”，提交并确认该商品篮子的报价任务。执行原生领域任务与 Reviewer，查看基线报价、商品数量、历史单价、币种和税率。
3. 仍以业务发起人新建变更，选择“报价计算规则”，将“折扣率（%）”从 `0` 改为 `10`。填写必需的来源引用，例如 `policy:controlled-volume-discount-10pct@v1`；保留税率、税费口径、商品数量、单价和币种。界面填写百分数；提交时系统将 `10` 转换为 API 的 `discount_bps: 1000`。
4. 查看变更影响，核对该 ChangeSet 自己的 AT 任务、候选、独立契约复核、负责人和前后金额。预演保存候选与审计证据，现行报价和正式规则保持不变；业务发起人没有批准权限。
5. 切换为该提案对应的“财务负责人”，重新选择同一提案并完成审阅确认，再批准精确候选。此角色没有执行权限，提案应为“已批准”，正式报价仍为 v1。
6. 切换为“变更执行人”，选择这份已批准提案，点击“应用已批准提案”。核对实际 Apply 回执和后继 Quote v2；模型输出、预演金额或批准提示本身都不是生效证据。

普通本地模式没有上述角色会话；生产账号则按真实成员权限操作。一个账号同时具有批准和执行权限时，界面在批准后重新核对允许操作，再自动 Apply；仅有批准权限时仍保留已批准状态，等待执行人。

此固定样例的验收对照值为：0% 折扣时总额 1,928.45 GBP，10% 折扣经批准和 Apply 后为 1,735.61 GBP、Quote v2。再切换为业务发起人，新建 `20`% 折扣提案，填写新的来源引用，例如 `policy:controlled-discount-20pct-review@v1`。预演后切换为对应财务负责人，填写原因并记录拒绝；正式结果应仍为 v2 / 1,735.61 GBP。保留同一商品篮子和税率，以实际回执核对；拒绝未生效提案不等于回滚已生效版本。

查看能力治理时，使用“Skill 维护负责人”运行核心 Skill 的隔离兼容与恢复验证，并审阅经验候选；对应负责人由当前接口返回，不能用报价审批角色代替。此操作不改当前报价，也不是生产灰度。导出报价和审计记录时切回有导出权限的业务发起人。

本节是可执行操作说明，不提前声明某次新运行已通过。用该运行自己的 run ID、模型回执、批准与 Apply 记录验收。UCI 的商品、数量与历史单价是真实公开字段；组织、角色、20%税率和折扣政策均为受控配置，金额变化不是企业 ROI。独立无模型计价入口见 [双语 Demo 指南](guide/demo.zh.md)；纯算术回放与云模型运行不得混用身份。

默认 Evergreen 日期/币种示例仍保留为另一个可复现入口。使用定价 Pack 时，以本节的 `pricing_policy` 变化为业务主线，不把默认示例的字段变化套入商品篮子。两条路径共用控制面、准入和 Rebase。

## 默认 Evergreen 示例启动方式

在 `orgrebase/` 仓库根目录执行；使用 CPython 3.12.13 和 uv，独立 OAC 仓库默认位于相邻的 `../oac-spec`。若放在其他位置，先设置 `ORGREBASE_OAC_ROOT`。

```bash
uv sync --locked --all-extras
./run-semifinal-demo.sh
```

默认 `interactive` 创建新工作区。首次进入「企业接入 → OAC 组织契约」，点击
「从已配置材料生成契约候选」；该步骤使用既有确定性编译，不调用映射模型，也不复制历史映射回执。
Enterprise Contract Owner 必须审阅当前摘要、完成三项确认并达到服务端 4 秒时间门，准入后该不可变激活绑定才能进入
Task Intake、Formation、AgentTeams、Tool / Skill、两个业务 Owner 门与选择性 Rebase。
业务候选调用本机摘要匹配的 Ollama `qwen2.5:3b`，不发起 Vertex 调用。
默认地址为 `http://127.0.0.1:8000/`。端口占用时通过 `ORGREBASE_DEMO_PORT` 选择空闲端口；需要保留并重开同一状态时显式设置 `ORGREBASE_DEMO_WORK_DIR`。

| 入口 | 用途 | 模型与证据边界 |
|---|---|---|
| 无参数 / `interactive` | 保留的本地参考旅程 | `OFFLINE_LOCAL`；当前 OAC 准入；本地 Ollama 只产生 advisory |
| `live` | 显式生成云模型候选证据 | `LIVE_VERTEX`；凭据留在本机；不因配置存在就宣称成功 |

原 `guided` 模式已退役；显式调用会在创建目录、复制回执或启动 provider 前拒绝，并提示改用 `interactive`。
已激活历史工作区仍可通过 `ORGREBASE_DEMO_WORK_DIR` 重开；历史可读不代表旧授权能准入新执行。
公开 Golden 和历史验证档案是冻结投影，不是点击后现场计算的结果。

## 三个顶层入口与固定顺序

### 1. 企业接入（一次性管理面）

展示 Domain、Knowledge、Authority、Capability 和 Dependency 五类长期材料，
候选映射、确定性 OAC 校验、精确负责人准入与不可变激活绑定。
不完整输入保留 Gap / `UNKNOWN` 并停在 `HOLD`；Agent 不能自批组织契约。

### 2. 变化处置（变化驱动的 Quote 验证切片）

首屏先回答五个问题：什么发生了变化、哪些成果依赖它、哪些 Agent 自动处理、为什么只通知某位
Owner、批准后真正改了什么。Quote Operations Owner 只有两类主动操作：

1. 查看已准入变化、影响范围和最终零写 Preview；
2. 在自己拥有规范权威时批准；有执行权限时自动衔接应用，否则等待有权执行者应用，再阅读后继版本、保留证据和回执。

页内三个子区的职责是：

- **变化与已有基线**：从上游 ChangeSet 到现有 Quote、依赖与精确 Owner。
- **真实协作进度**：只读 `/api/workspace/run-progress`，展示同一 run 的创建、委派、ACK、上下文/结果交接、Tool、Skill、Reviewer、AT 终态、人工门与业务终态。「运行明细」展开初始形成动作日志和已有 Reviewer 调用的 Token/耗时；未报告的数据不按零计算，也不代表全任务账单或节约金额。
- **本次变更任务**：选择具体提案后，工作台从其详情读取候选尝试的 AT 创建、委派、执行、提交、检查与接收回执，以及独立契约复核。预演等待期间只轮询读取，不重复派发；失败或结果未知时保留原尝试，不能自动重派。
- **影响、审批与验收**：先显示 actual-read 影响、候选修正和 Preview/VMRC，再用纵向 `Quote v1 → v2 → v3` 与字段差异显示“拟改什么、谁有权、批准后实际改了什么、哪些证据支持保留”。

在「业务数据演化与验收」选中提案后，工作台顶部用同一份详情显示业务提出、AT 协作、
候选准入、独立契约复核、人工决定和正式生效。契约复核检查绑定与权限，不代替业务判断；
缺少原生 AT 记录的参考执行或历史数据会明确显示未观察到，不借用初始形成的成功记录。
展开生效结果可核对精确前驱/后继的十个业务字段，以及源任务、AT 项目、执行轨迹和批准回执。

第二案例可在预演后填入真实拒绝原因并点击「记录拒绝」：先确认当前报价版本没有变化，
再点击「修订为新提案」。新草稿保留拟改值、来源和变更原因，关联原提案；补齐依据后须重新
预演与批准。委派、撤销或授权到期即使不改变业务状态，也会使当前详情更新负责人及操作权限。

Finance 首轮 `ABSTAIN → Reviewer REPLAN → HTTP Tool 补证 → Finance A2 → PASS` 是 Quote v1
基线 Formation 中发生的候选恢复链，不能移植为后续变更的补证记录。
当前 golden 模式的后续变化轮另有按 ChangeSet 绑定的原生 AgentTeams taskflow；
其领域候选仍受现有候选执行器约束，AT 接收和独立契约复核均不等于人工批准或业务生效。
这条任务链使用受控本地 Matrix 与对象存储，不表示向外部 Element 聊天室派发，
也不能追认为旧归档或其他执行模式已经执行过。候选层默认自动；只有最终规范写入、`UNKNOWN`、
冲突/越权或 Skill 发布进入人工门。

在变化列表先看原负责人、有效受托审阅人或已记录的决定人，再打开需要处理的条目。
委派不自动取消原负责人的权限；列表是当前记录的只读提示，实际命令仍由后端重新核验。
职责已变化的旧提案应按提示重新提出；已生效但结果记录未恢复时，应恢复结果，不重复批准。

生效后展开“追溯本次变更的依据与执行”。上下文按每份清单分组，显示引用的投影名称、
前提/派生/排除记录数量及允许公开的原因，不显示排除对象的标识和原文。
“未记录排除项”只描述该清单；清单缺失或处置类型未知时不能推断成零，也不表示全库资料都已获准。
历史清单解释当时记录，不在页面上重新判定过去权限。核对Skill时使用本次实际调用回执，不能拿版本引用替代调用。

### 3. 能力与保障（次级管理面）

展示三个受治理 Skill、当前运行保障与历史独立验证档案。档案默认折叠，
必须明示冻结时间、run 身份和“非本次报价运行”。

历史档案的“合成机制参考实现对照”展示全部13个策略、指标分数和硬门结果。
部分策略分数较高仍因硬门失败；部分消融仍通过，两者都保留。该档案使用192个合成机制用例，
不是当前报价、打包产品或真实模型的效果评测，也不是决赛评分。数据缺失或校验失败时该子卡关闭，
当前业务与其他档案继续按各自状态显示。

三个 Skill 都以一份 canonical `SKILL.md` 为权威，用双语 description、
中英 references 和中英混输评测扩展可发现性；不创建两份可执行权威。

在「Skill 能力 → 核心 Skill：版本兼容与恢复验证」，Skill 负责人可以主动运行
`enterprise-quote-compose` 的固定版本对验证：调用保留的 1.3.0、完成 8 类资格检查、
受控试调用 1.3.1，再实际执行并调用 1.3.0。页面展示四阶段回执；同一工作区和包摘要对
只执行一次，刷新或重启读取原记录，失败或未知不自动重跑。

该入口使用明确标注的合成输入，最多 11 次 Skill 调用与 1 次本地 HTTP 工具调用。
验证只保存证据，不发布草稿、不改变业务 Skill 指针或报价，也不是生产灰度。
两个版本当前执行程序相同，因此只能证明版本兼容、资格门与可执行恢复，不能宣称质量提升。
另外两个核心 Skill 仍如实显示其现有回退决策范围，不编造尚未保存的可执行前驱版本。

## 历史复赛连续视频制作约定

以下 6–7 分钟、12 个语义段及编码格式是原复赛视频的制作约定，不是当前决赛的提交要求；新材料以对应赛事要求为准。现场演示仍沿用前述产品顺序和真实审批门。

原视频控制在 6–7 分钟，12 个语义段仅用于导读和审计标记，不是剪辑点。
全片只允许对唯一连续源区间做一次外层裁头尾和尺寸规范化，
`internal_cut_count=0`，不加速，不用分段 concat 伪造连续。

| 段 | 参考时长 | 内容 |
|---|---:|---|
| 01 | 30–40s | OAC 五类企业输入、候选映射、确定性校验和精确负责人激活 |
| 02 | 55–65s | Quote v1 基线 Formation：逐 Agent 上下文、Tool/Skill、候选、handoff、Reviewer 与 Finance 补证重规划 |
| 03 | 20–30s | 已有 Quote / 依赖基线、上游变化到达、ChangeSet 冻结和唯一业务 `run_id` |
| 04 | 30–40s | actual-read 影响发现、`AFFECTED / PRESERVE / UNKNOWN` 与最小组队 |
| 05 | 30–40s | 变化轮的 `LOCAL_DETERMINISTIC_ADVISORY`、最小变化团队与“未新建原生 taskflow”边界 |
| 06 | 35–45s | Product 最终 Preview、拟变化、VMRC、零写与真实 Owner 暂停 |
| 07 | 35–45s | 一次 Owner 点击后 Approval 与 Apply 分离，自动 selective Rebase |
| 08 | 35–45s | Finance 同构变化；证明是一套机制的第二次运行，不是第二方案 |
| 09 | 30–40s | Quote v3、字段 diff、回执、回滚锚点和自动进入当前历史档案 |
| 10 | 25–35s | 当前 run 七层终态观测投影与声明边界 |
| 11 | 25–35s | 三个 Skill 治理；BPI 不同 run 的独立机制验证 |
| 12 | 15–25s | 五阶权威边界、价值口径和 Pilot / Production 边界收束 |

长页定位使用浏览器原生 `behavior: "smooth"` 并记录滚动前后坐标。
路由切换回顶部是页面行为，不得用元素跳转替代页内滚动。

## 必须通过的画面与真实性门

- 30 秒内能回答变化是什么、影响了什么、哪些 Agent 自动处理、谁拥有最终权威和批准后改了什么。
- 同一 `run_id` 中观察到创建、委派、ACK、交接、Tool、Skill、Reviewer、重派和终态；导读文字不算证据。
- 未完成时不预显示结果；UI 只读持久化 action journal、receipt 和控制面状态。
- OAC Owner、Product Owner 和 Finance Owner 的三个服务端门都保留真实等待；业务门在 Preview/VMRC 后持久暂停，画面中的人只点击一次决策，Approval 与 Apply 仍保留独立回执。
- `Quote v1 → v2 → v3` 显示字段级差异、精确 Owner、保留对象和最终回执。
- 历史机制档案不冒充当前运行，模型化节省不冒充企业 ROI。
- 按上述历史复赛约定制作视频时，使用 1920×1080、25fps、H.264、无音轨、不超过 480 秒，并检查完整解码；其他提交不自动继承这些格式限制。
- 桌面和 390px 视口不得水平溢出；中英切换不丢 run、tab、stage 或审批计时。

## 机器复核

```bash
uv run python scripts/verify_golden_pilot_evidence.py \
  --root evidence/golden-competition/latest/pilot

python3 scripts/verify_completed_state_restart_receipt.py \
  --receipt evidence/golden-competition/latest/pilot/restart/completed-state-restart-receipt.json \
  --before evidence/golden-competition/latest/pilot/restart/state-before.json \
  --after evidence/golden-competition/latest/pilot/restart/state-after.json \
  --golden-run evidence/golden-competition/latest/pilot/golden-run

uv run python scripts/verify_formation_taskflow_probe.py \
  --evidence evidence/formation-taskflow/latest \
  --lock agentteams/historical/teamharness-v1.2.2.json

uv run python scripts/verify_skill_predecessor_rollback.py \
  evidence/skill-predecessor-rollback/latest
```

Golden verifier 证明当前 OAC-bound 同运行公开包；restart 只证明完成态 SQLite 服务重开。
Formation 示例按证据自己的 v1.2.2 历史锁做 `RETAINED_LOCK_REPLAY`，不要求本机保存旧 checkout，也不重写原回执。当前新运行使用 `agentteams/teamharness-lock.json` 固定的 v1.2.3；两者不能混用，历史锁复验也不是当前版本重跑证据。
Formation、公开流程和 Skill rollback 各属独立机制路径，不得借给当前任务验收。
页面、PPT 和视频只是机器证据的产品投影，不能替代证据本身。
