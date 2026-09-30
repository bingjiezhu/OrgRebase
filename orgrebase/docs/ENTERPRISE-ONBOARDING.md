# 企业接入、升级与资格交接

企业接入不是把一个 JSON 文件复制进服务器。OrgRebase 分开显示契约定义、来源准入、Pack 密封、
工作区激活、业务 Formation 和客户环境资格，前一阶段通过不会自动授予后一阶段权威。
草稿预检、密封、profile/source 准入和 workspace 激活也是四个不同事实。

经过身份与工作区权限验证后：

```text
GET /api/workspace/onboarding-status
```

返回五类企业输入的完整性、来源版本/摘要、缺口与阻断门，以及当前任务、模板目录、来源绑定和接入阶段。每个模板绑定精确 template/profile/slot digest 和 schema ref。当前 schema/adapter 未单独注册字节摘要，投影明确返回 `BOUND_BY_TEMPLATE_DIGEST_ONLY` / `BOUND_BY_PACK_DIGEST_ONLY`，不把绑定摘要冒充为 schema/adapter 字节摘要。目录中的
`CONTRACT_ONLY` 表示 schema/template 可以读取或规划，不表示已有 renderer、rebuild handler、完整变化
Apply 或客户资格。只有当前准入 profile 的精确模板会显示 `CONFIGURED_RUNTIME_HANDLER`。`FORMATION_VERIFIED` 和 `GOVERNED_CHANGE_VERIFIED` 只根据当前工作区已持久化运行结果显示；`CUSTOMER_QUALIFIED` 必须来自对应客户环境验收，浏览器无法赋值。

状态投影不会创建事实、负责人或权限，也不会接收源 token。管理员仍按以下顺序操作：

1. 使用 `enterprise-pilot-init` 生成受支持报价模板的草稿，并替换全部示例企业事实、规则和职责。
2. 由业务责任人确认字段语义、来源、Owner、允许动作和缺口；缺少权威输入时保持 HOLD。
3. 先执行 `enterprise-pilot-draft-preflight --draft PATH`。它在临时目录重算候选摘要并检查五类输入，不保留 sealed 目录、不准入 workspace、不激活、不产生业务写入。
4. `enterprise-pilot-seal` 只做内容、摘要与 runtime 兼容预检。密封不是客户业务准入，也不会激活工作区。
5. 运维使用受限数据库角色、真实身份配置和已准入 Pack 启动新工作区。数据库会拒绝把另一 Pack
   或 profile 原地混入已有工作区。
6. Formation 提交后，员工才能在该工作区提出变化。真实客户 IdP、来源 ACL、连接器、恢复目标和
   员工使用仍要在客户环境单独验收。

同类企业复用要求 A、B 两组企业输入拥有各自组织、工作区、来源、负责人、规则和回执。只修改名称、
customer ID 或演示标签不算第二家企业接入。一个企业的批准、Source receipt 或客户资格不得复制给另一个企业。

当前 runtime 完整支持 Enterprise Quote。Public Summary、Residency FAQ 与 Discount Memo 等模板目前只按
各自真实实现阶段展示；模板存在不能作为完整业务支持声明。新增成果类型需要自己的输入组装、实际读取
依赖、输出验证、rebuild handler、审批范围和 successor 证据。

## 可执行的草稿到密封流程

从工作区 clone 的 `orgrebase/` 目录执行；草稿与密封输出目录必须事先不存在：

```bash
uv sync --locked
uv run --frozen orgrebase enterprise-pilot-init --output ../enterprise-draft
uv run --frozen orgrebase enterprise-pilot-draft-preflight \
  --draft ../enterprise-draft
uv run --frozen orgrebase enterprise-pilot-seal \
  --draft ../enterprise-draft \
  --output ../enterprise-sealed
uv run --frozen orgrebase enterprise-pilot-preflight \
  --pack ../enterprise-sealed \
  --output ../enterprise-preflight.json
```

预检输出的 `candidate_pack_digest` 是按当前草稿重算的候选身份；只有随后密封产物仍保持完全相同的输入时，其 `pack_digest` 才应相同。预检输出中的 `sealed_output_created=false`、`profile_admitted_for_workspace=false`、`workspace_activated=false` 和 `canonical_target_writes=0` 是权威边界，不是待前端改写的状态。

初始化支持两个固定模板：

| `--template` | 用途与边界 |
|---|---|
| `evergreen`（默认） | 历史 Evergreen v1 合成参考输入；复制原字节，不作为认证部署的 v2 Pack |
| `priced-quote` | 合成初始事实 v2 Pack，带显式 EnterpriseBinding 和 v2 定价输入；可用于新单 Quote 或 Quote + Discount Memo 工作区的受控首跑 |

例如：

```bash
uv run --frozen orgrebase enterprise-pilot-init \
  --template priced-quote --output ../priced-draft
uv run --frozen orgrebase enterprise-pilot-draft-preflight --draft ../priced-draft
uv run --frozen orgrebase enterprise-pilot-seal \
  --draft ../priced-draft --output ../priced-pack
```

模板不包含有效客户资格或已批准的未来变化。实际接入前仍需核对全部来源、职责和允许动作，
不能把示例身份当成员工认证。无需模型服务的角色分离浏览器首跑见[双成果工作区](QUOTE-DISCOUNT-MEMO.md#从公开示例开始)。
企业材料创建/编辑、密封与新工作区配置使用上述 CLI 或下面的受认证 API；当前浏览器负责契约准入、
员工任务、变化处置和导出，没有企业材料编辑器或新工作区配置按钮。

## 受认证的草稿恢复入口

部署服务提供一组受 `govern` 权限、当前可信 Principal 和精确 workspace 约束的草稿 API：

```text
POST /api/workspace/onboarding-drafts
POST /api/workspace/onboarding-drafts/update
POST /api/workspace/onboarding-drafts/preflight
POST /api/workspace/onboarding-drafts/seal
GET  /api/workspace/onboarding-draft-operations/{CREATE|UPDATE|PREFLIGHT|SEAL}/{operation_key}
GET  /api/workspace/onboarding-drafts/{draft_id}/revisions/{revision}?receipt_digest=sha256:...
GET  /api/workspace/onboarding-drafts/{draft_id}/revisions/{revision}/export?receipt_digest=sha256:...
```

创建和更新请求提交精确 `pack.json`、`profile.json` 以及 DOMAIN、KNOWLEDGE、AUTHORITY、CAPABILITY、DEPENDENCY 五个组件 JSON。原始内容只进入现有 `private_records` 留存层；artifact 和 idempotency 记录只保存工作区、主体、operation、请求、内容与五组件的摘要、版本和状态，不保存 `files`。浏览器响应统一 `Cache-Control: no-store`。草稿 ID 在规范记录中也只表现为工作区绑定的摘要。

每次创建、更新、预检和密封必须提供独立 `operation_key`。请求丢失响应时，客户端先读取同一 operation；相同 key 和相同请求返回已提交结果，相同 key 和不同请求返回 `IDEMPOTENCY_CONFLICT`。更新和密封创建不可变的下一个草稿版本，并校验父 receipt；并发写同一版本只有一个成功。受认证的私密草稿API在内存中完成预检与密封，不把请求原文物化到OS临时目录；提交前再次读取同一私密内容摘要和当前权限，因此不会把锁外旧结果绑定到已变化内容。文件式CLI仍只处理管理员显式提供的目录。密封仍不等于 profile 准入或 workspace 激活。

密封版本可以通过 `/export` 下载精确字节 ZIP。它只在内存构建，沿用当前作者、工作区、留存和撤权检查，响应为 `no-store`；下载不会准入或激活工作区。请原样解压，不要用另一种 JSON 格式重写组件。安装后 `pack_digest`、来源准入及投影摘要与密封回执相同。文件式与私密草稿式密封共用同一个内容编译器；既有目录 Pack 的摘要不变。密封版本在同一私密留存记录中保存精确 UTF-8 文本，读取时核对其内容摘要，避免 JSON 存储排序或自动补齐摘要字段改变待导出的源码字节。

早期内存草稿的历史 `memory:` 身份仍可回读，但不能静默作为目录身份导出；需从该私密版本显式执行新的 seal 操作，再使用新回执。旧回执与操作结果不会被重写。结构校验失败只返回稳定错误码，不将原始 `files` 或私密值回显到 422 响应。

同一 Principal 可在新会话中凭精确版本和 receipt 恢复私密草稿；切换主体、切换 workspace、成员撤权、会话失效或摘要不符均拒绝读取。私密记录到期后恢复与后续操作停止，现有 `POST /api/workspace/privacy/purge` 执行限量清扫并保留不含原文的删除账本及 operation receipt。恢复 operation 只回读结果，不重放批准、准入或激活。当前证据是受控本地浏览器会话、SQLite 与真实 PostgreSQL 存储测试，不是独立员工 UAT 或客户 IdP 验收。

浏览器只读取安全投影，不接收源 token、服务端路径或“可运行=true”之类客户端标签。Dataverse 来源连接器当前只映射已准入的标量字段；`quote_basket` 与 `pricing_policy` 等结构化输入使用有来源的结构化变化合同，不能伪装成文本字段。

## 干净安装和发布门

独立管理员从审核后的工作区源码或制品开始，先核对提供方公布的 SHA-256/来源签名，再安装锁定依赖。从源码验证：

```bash
cd orgrebase
uv sync --locked --extra dev
make check-core
```

`check-core` 是无 OAC、PostgreSQL 或服务凭据的贡献者门。公开发布候选还需在同一提交上通过 `make check-enterprise-boundaries` 和 OAC `public-check`，随后验证确切 wheel/sdist 的隔离安装与公开首单，生成对应 SBOM 与来源签署。企业边界门强制 PostgreSQL 测试，公开 OAC 门需要已安装依赖的匹配源码及 Go 1.22。可执行命令与 CI 范围见[贡献指南](../CONTRIBUTING.md#development)和[制品资格](RELEASE-CANDIDATES.md)。内部完整 `make check` 仅适用于携带完整历史档案的开发工作区，这些输入不在公开白名单内。安装、首单或本地源代码检查通过均不等于 GitHub 签署或客户生产验收。

## 升级、失败回退与旧 Pack

- v1 与 v2 Pack 保持原有读取语义；v2 额外要求 EnterpriseBinding 与认证单企业边界。读取旧 Pack 不会自动授予新准入。
- 升级前停止新执行，保留待审与 UNKNOWN 意图，核对待定外部效果，并在新库或恢复副本上演练。
- 生产 runtime 不自动迁移数据库。运维角色按[数据库迁移](STATE-STORE-MIGRATIONS.md)执行 `orgrebase database migrate`；应用角色不持有 schema 变更权。
- 新制品、新 Pack 或新路径需要新摘要和当前权限复核。旧 Preview、批准和来源确认不能迁移为新决定。
- 升级失败时保持旧服务与数据库原状。只有 schema 兼容时才能回到前一制品；应用回退不能撤销已经发生的 CRM 写入。

## A/B 复用证据与客户资格交接

受控测试要求 A、B 两组企业输入分别拥有组织、Pack/profile 摘要、来源、负责人、规则、数据库和回执。两者使用同一 Quote 模板完成 Formation→Preview→Owner 批准→Apply→重启/导出；交换 Owner 或 approval receipt 必须拒绝。这是受控本地复用证据，不是第二家真实客户或生产 PostgreSQL 资格。

每个真实试点在执行前填写：

| 输入 | 当前状态 | 责任人 |
|---|---|---|
| 业务用途、用户角色、允许的 Quote 范围 | 待客户确认 / NOT_RUN | 业务负责人 |
| IdP、成员映射、撤权和密钥更新 | 待客户环境 / NOT_RUN | IAM 管理员 |
| 来源 ACL、字段字典、水位和删除语义 | 待客户来源 / NOT_RUN | 源系统 Owner |
| 负载、预算、服务时段、留存、RPO/RTO 和支持责任 | 待试点参数 / NOT_RUN | 交付/运维负责人 |
| 外部动作字段、ETag、副作用和回查权限 | 默认不启用 / N/A；启用后 NOT_RUN | 目标系统 Owner |
| 员工 UAT、业务质量基线、全部成本与 ROI | 待真实样本 / NOT_RUN | 业务 Owner/独立复核人 |

交付记录必须绑定精确制品、配置、profile/Pack、IdP、来源、动作和验收环境，并列明资格失效条件。真实 IdP、源 ACL、员工 UAT、持续运维与 ROI 未运行时必须保持 `NOT_RUN`。
