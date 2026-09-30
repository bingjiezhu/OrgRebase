# Demo与验证

<a id="model-free-priced-workspace"></a>

## 无模型计价工作区

使用受支持的 `priced-quote` 模板，在浏览器中审阅一次结构化计价变更。该路径需要匹配的同级 `oac-spec/` 源码，使用确定性组件和可切换的本地角色，不需要模型服务或云凭据。先核对 [Python 所链接的 SQLite](quickstart.zh.md#sqlite-runtime)，为草稿、Pack 和数据库使用新路径。从 `orgrebase/` 执行：

```bash
uv sync --locked --all-extras
uv run --frozen orgrebase enterprise-pilot-init \
  --template priced-quote --output ../priced-draft
uv run --frozen orgrebase enterprise-pilot-draft-preflight --draft ../priced-draft
uv run --frozen orgrebase enterprise-pilot-seal \
  --draft ../priced-draft --output ../priced-pack

ORGREBASE_OAC_ROOT="$(pwd)/../oac-spec" \
ORGREBASE_OAC_ADAPTATION_MODE=required \
ORGREBASE_OAC_EXECUTION_MODE=OFFLINE_LOCAL \
ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED=1 \
ORGREBASE_LOCAL_ROLE_SESSION_ORIGIN=http://127.0.0.1:8883 \
ORGREBASE_DELIVERABLE_PROFILE=quote-discount-memo-v1 \
uv run --frozen orgrebase enterprise-pilot-start \
  --pack ../priced-pack --store ../priced-state/workspace.sqlite3 \
  --host 127.0.0.1 --port 8883 --review-seconds 4 --competition-mode off
```

打开 `http://127.0.0.1:8883`；切换角色时保持这一确切地址：

1. 以 **企业接入负责人** 核对材料，生成组织契约，完成必要审阅并准入精确候选。
2. 以 **业务发起人** 填写报价工作说明，确认范围并启动任务。核对初始 Quote 与 Discount Memo 均属于当前工作区；示例总额为 USD 95.00。
3. 新建 **报价计算规则** 变更，将折扣从 5% 调为 10%。保留商品明细和 0% 税率，提供新的来源引用并预演。预演不会更改正式成果。
4. 由对应的 **财务负责人** 审阅并批准来源变更，再由每位必要的成果负责人审阅并签署自己的候选。批准集合未齐时不能应用。
5. 以 **变更执行人** 应用获批集合，核对 USD 90.00、两个后继成果和 Apply 回执。
6. 切回具有导出权限的 **业务发起人**，查看当前 Quote 与 Discount Memo，下载工作结果和审计记录，再刷新确认已完成状态。

七个 JSON 文件组成合成初始事实 v2 Pack：USD 100.00 商品金额、5% 折扣和 0% 示例税率。模板不包含预先批准的未来变化、员工凭据或客户资格。企业材料创建、密封和新工作区配置通过 CLI 或受认证 API 完成；浏览器负责契约审阅、任务确认、变更处置和导出。来源批准、逐成果批准与 Apply 各自保留回执。

`--competition-mode off` 使用固定业务时钟 `2026-08-15T00:00:00Z`，审批有效期等日期属于确定性演练。认证生产工厂使用 `SystemClock`；员工部署请按[企业身份与部署配置](../AUTHENTICATED-DEPLOYMENT.md)设置。本地角色 Cookie 是演示身份，不是 SSO。[双成果合同](../QUOTE-DISCOUNT-MEMO.md)列出完整输入与恢复边界。

## 原生协作主线

先按[Vertex](models-vertex.zh.md)或[DeepSeek](models-deepseek.zh.md)配置明确的模型入口。打开WebUI，核对运行模式，再依次完成组织契约审阅、任务需求确认、原生协作、候选与影响预览、指定负责人批准和实际Apply。

执行期间查看协作进度；成功后回到“需求与当前交付”检查报价。后续变化有自己的任务和回执，不能用初次Formation替代。模型调用失败时检查原因，不复用旧成功记录。

## 同一公开商品篮子与原生云模型协作

这条路径将 UCI 发票 557670 的商品篮子送入既有原生 AgentTeams / Vertex 链路，不另建计价或批准引擎。先按 [Vertex 配置](models-vertex.zh.md)准备获授权的项目与凭据；从已安装依赖的产品目录执行。两个输出目录均使用新的名字，不能复用已形成报价或批准的工作区。

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

侧栏的“切换演示角色”列出服务端根据当前工作区定义的角色。先选择角色，再点击“使用此角色”；切换会清空未提交草稿，不删除已经登记的提案、批准或运行记录。会话使用浏览器 Cookie，变更请求经过 CSRF、角色权限和精确负责人检查。这里是本机角色模拟，不是员工登录或企业 SSO；生产部署仍使用 [OIDC 与服务端成员映射](../AUTHENTICATED-DEPLOYMENT.md)。

打开 `http://127.0.0.1:8879/`，按以下身份顺序操作。企业材料已在启动时加载，由接入负责人首先核对其来源：

1. 选择“企业接入负责人”，核对企业材料与来源，生成组织契约映射候选并检查实际模型记录；完成三项确认和服务端审阅门后准入。组织准入不会批准报价规则变化。
2. 切换为“业务发起人”，提交并确认该商品篮子的报价任务。执行原生领域任务与 Reviewer，查看基线报价、商品数量、历史单价、币种和税率。
3. 仍以业务发起人新建变更，选择“报价计算规则”，将“折扣率（%）”从 `0` 改为 `10`。填写必需的来源引用，例如 `policy:controlled-volume-discount-10pct@v1`；保留税率、税费口径、商品数量、单价和币种。界面填写百分数；提交时系统将 `10` 转换为 API 的 `discount_bps: 1000`。
4. 查看变更影响，核对该 ChangeSet 自己的 AT 任务、候选、独立契约复核、负责人和前后金额。预演保存候选与审计证据，现行报价和正式规则保持不变；业务发起人没有批准权限。
5. 切换为该提案对应的“财务负责人”，重新选择同一提案并完成审阅确认，再批准精确候选。此角色没有执行权限，提案应为“已批准”，正式报价仍为 v1。
6. 切换为“变更执行人”，选择这份已批准提案，点击“应用已批准提案”。核对实际 Apply 回执和后继 Quote v2；模型输出、预演金额或批准提示本身都不是生效证据。

如果审批前发现证据不足，财务负责人可在同一提案内退回补证，业务发起人补交当前准入证据并选择注册执行实例，再重新预演、复核和批准。旧轮次保留；正式报价仍由执行人Apply。操作范围见[同一ChangeSet补证恢复](agentteams.zh.md#evidence-recovery)。这与下文“拒绝20%提案”是不同动作，拒绝不会自动开启恢复轮次。

普通本地模式没有上述角色会话；生产账号则按真实成员权限操作。一个账号同时具有批准和执行权限时，界面在批准后重新核对允许操作，再自动 Apply；仅有批准权限时仍保留已批准状态，等待执行人。

此固定样例的验收对照值为：0% 折扣时总额 1,928.45 GBP，10% 折扣经批准和 Apply 后为 1,735.61 GBP、Quote v2。再切换为业务发起人，新建 `20`% 折扣提案，填写新的来源引用，例如 `policy:controlled-discount-20pct-review@v1`。预演后切换为对应财务负责人，填写原因并记录拒绝；正式结果应仍为 v2 / 1,735.61 GBP。保留同一商品篮子和税率，以实际回执核对；拒绝未生效提案不等于回滚已生效版本。

查看能力治理时，使用“Skill 维护负责人”运行核心 Skill 的隔离兼容与恢复验证，并审阅经验候选；对应负责人由当前接口返回，不能用报价审批角色代替。此操作不改当前报价，也不是生产灰度。导出报价和审计记录时切回有导出权限的业务发起人。

本节是可执行操作说明，不提前声明某次新运行已通过。用该运行自己的 run ID、模型回执、批准与 Apply 记录验收。UCI 的商品、数量与历史单价是真实公开字段；组织、角色、20%税率和折扣政策均为受控配置，金额变化不是企业 ROI。下面的无模型入口仍可用于独立计价核对，两者不得混用运行身份。

## 公开交易计价

在已安装依赖的产品目录执行，输出目录必须不存在：

```bash
uv run --frozen python scripts/run_public_quote_replay.py \
  --output ../retail-demo-new --demo-invoice 557670

ORGREBASE_ENTERPRISE_PACK="$(pwd)/../retail-demo-new/pack" \
ORGREBASE_WORKSPACE_DB="$(pwd)/../retail-demo-new/demo-workspace.sqlite" \
ORGREBASE_CHANGE_MODEL_PROVIDER=local-deterministic \
uv run --frozen orgrebase serve --local-demo --host 127.0.0.1 --port 8879
```

打开`http://127.0.0.1:8879/`。数据准备没有提前生成报价或批准。提出任务、确认范围并运行后，看6条商品明细和金额；提出0%→10%折扣变更，预演时现行结果不变，匹配负责人批准后才得到后继。

在此固定样例中，小计为1,607.04 GBP；受控20%价外税下总额为1,928.45 GBP，折扣生效后为1,735.61 GBP。金额差是政策计算，不是人工成本节省。商品数量与单价来自UCI公开历史记录；税率、折扣、组织和权限为受控配置。

## 什么算完成

同时核对当前run、候选/任务绑定、精确批准、Apply回执、最终Quote及事件链。拒绝应保持现行结果，刷新不应再次执行。不同场景、历史档案和模型提供方各有身份；不要拼接为一条更强的成功链。

[完整计价细节（中文原文）](../PRICED-QUOTE-DEMO.md) · [历史证据核验范围（英文原文）](../HISTORICAL-BUILD-VERIFICATION.md)。

## 逐项查看协作与运行证据

在「真实协作进度」展开每个执行主体，逐项查看任务、收到的最小上下文、实际 Tool / Skill 调用、候选输出、交接对象和回执时间线。基线组队与后续变更是不同阶段；在「业务数据演化与验收」选择具体变更，再查看该变更自己的任务详情、负责人决定与生效前后结果。

完整体验还包括企业材料与组织契约、Skill 原文与受控恢复、当前运行档案、独立历史重验及运行保障。连接器配置、候选批准和档案验证分别显示各自的实际状态。

在「能力与保障 → 运行记录与验证」中，可分别进入「本次任务记录」「公开数据案例」「可靠性验证」：查看当前交付与批准、浏览采购规则影响范围案例，或检查组队与恢复结果。

### 本地演示会话时长

启用 `ORGREBASE_LOCAL_ROLE_SESSION_ORIGIN` 的本地角色模式默认会话有效期为 1 小时。长时间演示可在启动前显式设置 `ORGREBASE_LOCAL_ROLE_SESSION_SECONDS=86400`，最多 24 小时（允许范围 60–86400 秒）；显式设置为 `0` 则关闭服务端自动到期；该设置不适用于生产身份认证。配置变更需重启并重新选择角色，既有会话不会原地延长。服务重启或会话到期后仍需重新选择角色，已保存的报价、批准及运行记录保留。页面提示“请选择操作身份”不代表本地服务或网络失效。

无自动到期模式仍保留角色权限、CSRF、切换与退出失效检查。浏览器 Cookie 最长设置为400天，实际保留受浏览器策略控制，不承诺跨浏览器清理或服务重启永久登录。
