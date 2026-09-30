# Quote + Discount Memo 双成果工作区

该运行配置用于一个新建、隔离的企业报价工作区。它把报价和折扣复核说明作为两个规范成果，
共同依赖明确版本的 `quote_basket`、`pricing_policy` 和 `currency`。旧的单 Quote 工作区、历史
对象和 `template:discount_exception_memo@v1` 不会被原位升级。

当前证据范围是 `VALIDATED_CONTROLLED_LOCAL`。SQLite 与 PostgreSQL 受限运行角色、标准应用工厂、
HTTP 逐负责人批准与受控浏览器界面均已验证；真实客户字段、职责、规则、UAT、ROI 和外部发布仍由企业验收单独确认。该 profile
的 `external_effects` 固定为 `DISABLED`，不会写入 CPQ、CRM、ERP 或 Office 文件。

## 运行合同

`DeliverableSetProfile` 是内容寻址的服务器准入资源，固定以下事实：

- 恰好一个 `QUOTE` 和一个 `DISCOUNT_MEMO` 对象；
- 每个成员的 object ID、模板、schema、adapter ref/digest、owner 和批准 scope；
- 底层 Enterprise Seed/Profile digest、Pack digest 和 runtime revision；
- 无外部效果。

新数据库第一次启动时，workspace registry 一次性绑定该 profile digest。已有单 Quote 数据库
不能挂载此 profile；双成果数据库也不能以旧单 Quote 配置重新打开。配置或 runtime revision
漂移会在 Preview/批准/Apply 前失败关闭。

```python
from orgrebase.workspace.formation import quote_discount_memo_profile
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService

runtime = load_enterprise_quote_pilot_pack("/absolute/path/to/sealed-priced-pack")
deliverables = quote_discount_memo_profile(runtime)
workspace = WorkspaceService(
    store_path="/absolute/path/to/new-workspace.sqlite",
    runtime_configuration=runtime,
    deliverable_set_profile=deliverables,
)
workspace.form_quote()
```

正式应用工厂只接受服务端 allowlist 中的固定名称，不接受客户传入 Python handler。
使用已密封的 priced Pack 启动新工作区：

```bash
export ORGREBASE_ENTERPRISE_PACK=/absolute/path/to/sealed-priced-pack
export ORGREBASE_DELIVERABLE_PROFILE=quote-discount-memo-v1
orgrebase serve --host 127.0.0.1 --port 8081
```

`single-quote` 仍是默认值。任意其他 profile 字符在打开数据库或 handler 前拒绝。

Pack 必须使用 `template:enterprise_quote@v2`，并完整准入 `quote_basket`、`pricing_policy`、
`currency` 和对应 owner。缺来源、缺 adapter、模板或 digest 不匹配时不会形成任何成果。

## 从公开示例开始

从源码工作区的 `orgrebase/` 目录执行。以下目录应事先不存在：

```bash
uv sync --locked --all-extras
uv run --frozen orgrebase enterprise-pilot-init \
  --template priced-quote --output ../priced-draft
uv run --frozen orgrebase enterprise-pilot-draft-preflight --draft ../priced-draft
uv run --frozen orgrebase enterprise-pilot-seal \
  --draft ../priced-draft --output ../priced-pack

ORGREBASE_OAC_ROOT=../oac-spec \
ORGREBASE_OAC_ADAPTATION_MODE=required \
ORGREBASE_OAC_EXECUTION_MODE=OFFLINE_LOCAL \
ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED=1 \
ORGREBASE_LOCAL_ROLE_SESSION_ORIGIN=http://127.0.0.1:8883 \
ORGREBASE_DELIVERABLE_PROFILE=quote-discount-memo-v1 \
uv run --frozen orgrebase enterprise-pilot-start \
  --pack ../priced-pack --store ../priced-state/workspace.sqlite3 \
  --host 127.0.0.1 --port 8883 --review-seconds 4 --competition-mode off
```

在 `http://127.0.0.1:8883` 选择企业接入负责人，从已配置材料生成契约候选，审阅并准入；
随后切换业务发起人，填写企业报价工作说明，确认范围并启动任务。该路径使用确定性运行组件，
不调用模型，也不要求本地模型服务。它仍经过 OAC 准入、任务确认和服务器权限检查。
角色切换是本地模拟身份；真实部署必须使用[认证配置](AUTHENTICATED-DEPLOYMENT.md)。

`--competition-mode off` 使用固定业务时钟 `2026-08-15T00:00:00Z`，审批有效期等日期属于这次确定性演练，不表示今天的生产时间。认证生产工厂使用 `SystemClock`；实际部署请按[身份与部署配置](AUTHENTICATED-DEPLOYMENT.md)设置，不使用本地演示身份。

`priced-quote` 是七个 JSON 文件组成的合成初始事实示例：采用 v2 Pack、显式 EnterpriseBinding、
v2 Quote 模板、USD 100.00 未折扣金额、5% 折扣和 0% 示例税率，初始总额为 USD 95.00。
它没有预先批准的未来变化、客户资格或外部效果。创建和密封不会授权业务执行。
接入真实企业前，需由各责任人替换来源、字段、规则、职责与权限并重新预检和密封。
无 `--template` 的初始化仍复制历史 Evergreen v1 示例，其原字节和语义保持不变。

首次形成后，可在变化编辑器把折扣从 5% 调为 10%，提供新的来源引用并预演。
来源负责人先批准；然后两位成果负责人分别批准自己的成果范围；最后由变更执行人应用。
结果应为 USD 90.00，报价与折扣说明同时生效。上线日期或产品方案变化只更新报价，保留折扣说明。
当前定价配置不支持单独改变币种；币种和报价明细需要同一原子变更合同，不能在此编辑器中拆开操作。
应用后切回具有导出权限的业务发起人，查看当前 Quote 与 Discount Memo，再在页面下载工作结果和审计记录，或显式读取独立的成果集合导出接口。变更执行人具有执行权限，不因此取得导出权限。

## 变化与批准

提交 `quote_basket` 或 `pricing_policy` 变更时，结构化 `value.source_ref` 必须与请求外层 `source_ref` 完全一致；不一致返回 `CHANGE_PROPOSAL_PRICING_SOURCE_MISMATCH`，不会登记提案或写入业务成果。

Preview 对两个成果分别给出 `REBUILD`、`PRESERVE_WITHIN_BOUNDARY` 或
`HOLD_FOR_REVIEW`。每个 REBUILD 成员在 Preview 阶段就产生并持久化安全审核投影、候选
TaskContext、WorkTrace、Coverage 和 Runtime Manifest；批准绑定这些 exact digest。投影展示最终候选
字段、金额和复核说明，不包含合同原文、成本底线或 secret。Apply 会重新执行并逐成员
对比 payload/context/trace/coverage/manifest digest。

折扣变化会重建两者；上线日期等非价格变化只重建 Quote，Memo 保持原字节、
原 WorkTrace、原 manifest 和依赖边，并显式标记 `PREDECESSOR_PRESERVED`，不伪装为新候选 Trace。
Memo 的 `last_price_change_set_*`、前后金额与差额只在实际价格重建时更新。本轮为什么
保留 Memo 记录在集合 Apply receipt 中。

来源 owner 的批准不能代替成果 owner 的批准。来源批准先通过旧 `approve_change`独立产生；
每个成果 owner 再用一次独立、已认证的成果决定命令只签自己的
exact scope。一个 Principal 不能代签另一 owner；新双成果批准不接受 BODY actor 字符串作为身份。
决定持久化 issuer/subject/actor、membership、workspace scope、当前责任 revision 和过期时间；Apply
事务内逐决定重验。缺一项、撤权、职责迁移、任一拒绝、任一成员 UNKNOWN 或 runtime 漂移，
整批 Apply 都不晋级。旧单 Quote BODY 兼容 API 没有被改写。

```text
GET  /api/workspace/deliverable-set
GET  /api/workspace/deliverable-set/changes/{event_id}
POST /api/workspace/approve/{event_id}/deliverable-set
GET  /api/workspace/export/deliverable-set
```

POST 只接收 `operation_id`、exact `preview_digest` 和 `APPROVED|REJECTED`；owner 由当前
Principal 推导。`operation_id` 与语义请求摘要持久绑定，响应丢失后重试会回读首次
`approved_at` 和 decision digest。operation namespace 同时绑定当前 owner，因此不同 owner 可使用
同一客户 operation ID，但不能互相借用回执。多实例在 candidate-set 级数据库锁内重读所有 owner 决定，
最后一份决定与 COMPLETE approval set 原子提交。
新成果决定还会重验源变更仍为 APPROVED、源批准身份与职责仍有效、runtime/来源观测未漂移；
源拒绝后不再展示成果签署动作。已提交操作的精确回读是历史事实，即使随后 Apply 改变当前 snapshot 也不重放命令。

成功 Apply 由唯一 `StateStore/RebaseWorkflow` 事务完成：需要重建成员的后继、保留成员的原版本、
各自 Trace/manifest、集合 snapshot、一次 graph pointer、基础 Rebase receipt 和集合 receipt 原子提交。
任一成员、manifest、pointer 或 receipt 写入失败都会整体回滚。重复命令回读同一内容寻址结果。

## 查看与验证

`workspace.deliverable_set_view()` 返回 profile、binding 和每个规范成员的 ref/digest/owner/state；
逐变化视图另返回 disposition、安全候选投影、当前 Principal 可用动作、decisions、
approval set 与 outcome。`workspace.state()` 在该 profile 下增加当前集合；Quote export、
evidence export 和独立 deliverable-set export 都绑定两个规范对象及批准证据。
这些集合读取在同一 StateStore 原生 read snapshot 中构造，多实例 Apply 不会让一个响应混合 v1/v2 成员。
受控 local role picker 只对当前 profile 精确列出的成果 owner 补充 approver 能力；这不会修改生产 IAM membership。

`GET /api/workspace/deliverable-set/changes/{event_id}` 在顶层 `apply_receipt` 返回已生效变更的真实、结构化集合回执；`GET /api/workspace/export/deliverable-set` 在 `changes[event_id].apply_receipt` 提供同一回执，关联重建或保留的成员。`GET /api/workspace/deliverable-set` 只返回当前集合，不含 `changes` 历史映射。未预演的逐变化集合读取返回 `409 / WORKSPACE_PREVIEW_REQUIRED`，表示仍需预演；它不会使已经形成的当前 Quote 或 Memo 失效。

```bash
uv run pytest -q tests/workspace/test_deliverable_set.py
uv run pytest -q tests/workspace/test_deliverable_set_postgres.py
```

测试包含独立 Decimal 金额向量（USD/JPY/KWD）、500→1000 bps、连续 PRESERVE→REBUILD、独立主体分步批准、
单主体代签拒绝、membership 撤销、责任迁移、UNKNOWN、缺 handler、审核投影/Trace/Manifest 篡改、
Formation receipt/event/artifact/idempotency 伪造、第二成员故障、幂等重试、重启和旧工作区兼容。条件采购成果
没有真实业务输入，状态为 `NOT_APPLICABLE`，本实现不声明采购、合同、付款或签约能力。
