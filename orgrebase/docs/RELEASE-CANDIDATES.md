# 制品资格与撤回

CI 的 `core` 面向普通 fork，无需 OAC 或服务凭据，执行明确范围的 `make check-core`。它从随附的 Git bundle 重建固定版本的 AgentTeams checkout。每个 PR 和 `main` push 均运行 Core、公开 OAC 契约门和 `check-enterprise-boundaries`，文档修改也经过相同的必需检查，避免路径过滤让分支保护永久等待；后者覆盖 PostgreSQL/HTTPS/OAC 与接入、预算、学习、双成果恢复。测试清单只在 Makefile 维护，工作流不覆盖。

手动触发的**公开发布门**使用锁文件和固定 Python，审计依赖，执行同一公开 OAC 契约门及包含真实 PostgreSQL 测试的企业边界矩阵。公开源码白名单有意排除某些冻结历史档案，因此不能在它上面把内部完整 `make check` 冒充为已通过的公开门。`check-enterprise-boundaries` 强制 `ORGREBASE_REQUIRE_POSTGRES_TESTS=1`；缺少数据库工具会失败，不能悄悄跳过并得到绿色结果。

在 `main` 手动运行工作流时，`release-candidate` 只有在同一提交的 `core` 和公开发布门都成功后才构建 OrgRebase 与 OAC 各自的 wheel/source archive，分别全新安装，核对许可与运行资源，并复算 OrgRebase 公开首单。它为两组件各生成一份绑定其确切制品的 SBOM，同时构建并复验对应的双组件公开源码 ZIP；全部下载件附 SHA-256 清单。GitHub Actions 身份签署所有候选资产的来源，并为两组件分别签署匹配的 SBOM。公开发布门被跳过时不会生成合格候选。结果保留为候选下载件；工作流没有包仓库发布或业务部署步骤。普通 push/PR 不运行签署步骤。

公开仓库的 push/PR 运行适用的自动检查；公开发布门与 `release-candidate` 只在 `workflow_dispatch` 时运行。候选是否合格，以**候选确切提交**的 GitHub 作业结果和产物摘要为准。本地构建的摘要、SBOM 或测试日志不等于 GitHub 签署结果，也不等于客户部署准入。维护者应保护 main 和工作流变更，并限制人工触发与部署权限；attestations 的可用条件见 [GitHub 官方说明](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)。

## 源码交付与公开发布

wheel 的运行资源仅在 `pyproject.toml` 的 `tool.hatch.build.targets.wheel.force-include` 中登记。常规 Hatch 构建、离线构建和隔离运行检查共用这份清单；增加资源时无需同步另外两份路径表。离线构建遇到声明资源缺失或路径越界会失败。`scripts/verify_packaged_runtime_assets.py` 检查隔离的包布局及本地运行，不等于已经验证最终 wheel 的字节、完整安装环境或真实企业部署。

双仓源码快照是可验证的交付方式：它按明确发行范围保留当前文件原字节，并提供逐文件清单、ZIP 摘要及复验命令。快照可以包含尚未提交的改动；Git HEAD 只描述提交历史，不能代表全部交付内容。源码快照不是完整仓库镜像，也不自带 GitHub 构建签署或生产部署资格。

已发布基线（2026-09-20）：公开仓库以工作区为根，`orgrebase/` 与 `oac-spec/` 并列；`v0.4.0` 是当时的工作区发布快照。文档站由同一份 Markdown 发布到 [GitHub Pages](https://bingjiezhu.github.io/OrgRebase/)。后续源码候选与旧 `v0.4.0`、更早的产品根 `v0.3.0` 各有独立身份；旧 release 和历史冻结证据不能证明当前候选。接收方仍需从可信渠道取得本次制品摘要，不能用版本字符串代替核验。

当前产品包元数据为 `0.5.0b4` Beta 候选。这个版本号只标识候选源码；只有对应提交完成本页的检查、制品构建与来源签署，才能把该制品作为可核验的 GitHub 候选。它不自动取得客户部署资格。

后续维护仍应先明确范围与许可，再绑定提交、OAC 准入指纹、锁文件、制品和对应验证结果。配置 remote、提交、打 tag、发布 release 是独立的维护操作，不由本地构建自动执行。新的本地源码快照在 GitHub 完整检查和签署作业完成前，只能称候选，不应沿用旧 tag 或产物的资格。

## 版本与发布流程

Python 分发元数据遵循 PEP 440，当前为 `0.5.0b4`；Git 标签使用对应的 SemVer 预发布名 `v0.5.0-beta.4`。公开 API 尚处于 0.x Beta，发布应标记为 prerelease，不能用 `v0.5.0` 暗示稳定版。修订已发布代码时递增版本，不能移动既有标签或替换已发布制品。[版本规范](https://packaging.python.org/en/latest/specifications/version-specifiers/) · [SemVer](https://semver.org/spec/v2.0.0.html)。

1. 在分支准备正式源码，提交 PR。核对适用的自动 CI 与文档预览，检查提交身份及公开选择范围。
2. 合并到 `main` 后，在确切提交手动运行 CI。等公开发布门、两组件安装、源码校验、SBOM 和来源签署全部成功；记录提交 SHA 与运行 ID。
3. 从该次运行下载 `orgrebase-candidate-<SHA>`。复验 `SHA256SUMS.txt`、来源签署及两组件 SBOM 签署，不重建或混用本地制品。
4. 开启仓库 immutable releases，以确切通过的提交创建对应标签和 draft prerelease。先附上全部资产，再发布草稿；发布后核对 tag、资产摘要及 GitHub release attestation。

GitHub 的 [immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases) 锁定发布资产及标签，并生成 release attestation。它不会补跑测试，也不会把既有历史发行变为当前资格。普通构建来源签署本身也不应描述为已经达到 SLSA Build Level 3。流程是否实际完成，以目标提交的 CI、签署和 Release 为准。

工作流 Actions 固定官方仓库的完整 commit SHA，并保留版本注释；默认只有 `contents: read`。仅维护者从 `main` 手动构建候选时取得签署权限。SBOM 从锁文件和显式的确切版本 `dist-info/METADATA` 取证：缺少或无有效 SPDX 的许可保持 `UNKNOWN`，完整锁图包含未安装的平台与 extras 分支，不声称它们在构建环境中全部安装。

## 配套 OAC 依赖

产品检查需要 OAC 公共 CLI。

原先（产品根仓库）：CI 另检出受信任的 `OAC_REPOSITORY`，并绑定 40 位 `OAC_REVISION`；指纹必须已列入产品 `configs/oac/runtime-admission-policy.json`。两项均未配置时只跑基础检查；仅配置一项、非固定提交、检出漂移或指纹不匹配都会使完整检查失败。

现状（工作区仓库）：一次 clone 已含同级 `oac-spec/`，不依赖开发者电脑上的第二份目录，也不再配置 `OAC_REPOSITORY`。根目录 `.github/workflows/` 是 GitHub 实际读取的权威副本，`orgrebase/.github/workflows/` 作为单产品布局兼容副本，测试要求两者逐字节一致。`core` 在每个 PR 与 `main` push 于 `orgrebase/` 运行；同次自动检查还运行 OAC 公开契约门和企业边界测试组。候选制品只在 `workflow_dispatch` 的公开发布门通过后构建，并设置 `ORGREBASE_OAC_ROOT=${{ github.workspace }}/oac-spec`。安装 OAC 依赖之前仍先跑 `verify_oac_dependency.py --root`，按项目名和当前准入策略核对源码指纹；工作区内的 OAC 树不再另传 `--revision`（不以第二仓库提交为真源）。checkout 不持久化凭据。[Checkout 官方说明](https://github.com/actions/checkout)。

更新 OAC 时，应审查新实现、同步产品准入策略修订和允许的指纹，再提交工作区内的 `oac-spec/`；不能把不匹配改成警告继续运行。

本地可检查当前源码是否被策略接受：

```sh
uv run python scripts/verify_oac_dependency.py --root ../oac-spec
```

这条命令不要求本地已经提交，因此结果的 `verified_git_revision` 为 null。本地通过不代表远程仓库的完整检查或生产部署资格已经完成。

## 验证候选制品

消费者下载后，核对组织可信渠道给出的制品 SHA-256，并验证来源：

```sh
gh attestation verify /path/to/orgrebase.whl -R bingjiezhu/OrgRebase \
  --signer-workflow bingjiezhu/OrgRebase/.github/workflows/ci.yml \
  --source-digest REVIEWED_COMMIT_SHA --source-ref refs/heads/main \
  --deny-self-hosted-runners

# 对同一制品单独检查 CycloneDX SBOM 签署。
gh attestation verify /path/to/orgrebase.whl -R bingjiezhu/OrgRebase \
  --predicate-type https://cyclonedx.org/bom \
  --source-digest REVIEWED_COMMIT_SHA

# 已完成 immutable release 发布后，复验 Release 与确切资产。
gh release verify v0.5.0-beta.4 -R bingjiezhu/OrgRebase
gh release verify-asset v0.5.0-beta.4 /path/to/orgrebase.whl \
  -R bingjiezhu/OrgRebase
```

验收记录应绑定 wheel 摘要、源提交、工作流身份、Python/lock 摘要、部署 ID、tenant、DomainPack/EnterpriseBinding、IdP、来源和目标动作资格。签名证明构建来源；业务正确性和真实效果资格仍由各自证据支持。只从制品旁边下载一个“可信公钥”不能自行建立组织信任。

## 撤回与升级

1. 把受影响制品摘要加入部署准入拒绝清单，记录原因、影响范围和替代版本。停止新的执行准入，保留证据与已发生效果。
2. 检查运行中的工作。未写出的提案重新核对版本和授权；`DISPATCHING` 或 `COMMIT_UNKNOWN` 先查询目标并对账，不能随应用版本回滚盲目重放。
3. 数据库按显式迁移规则处理，禁止通过覆盖数据库或重算历史摘要“回滚”。恢复副本保持隔离，先重放当前删除台账、核对授权与远端效果，再申请解除隔离。
4. 用替代制品重新执行安装、兼容读取、数据库和目标动作资格。旧证据保留原字节；撤回不等于删除旧审计。

当前实现不会自动维护企业的部署拒绝清单，也没有 HA、跨地域容灾或生产 SLA 的实测资格。部署方应把这份流程接入已有制品仓库和变更管理；不再另建一套产品内发布控制台。

## 运行版本与冻结事实

健康检查、readiness、OpenAPI 与遥测的版本来自当前源码元数据或已安装包元数据。`/api/release-facts` 保留冻结事实的原 `release`；其 `release_context` 分别给出 `runtime_version`、`evidence_release` 与两者关系。`HISTORICAL_RELEASE_FACTS` 说明它属于较早发行范围，不代表当前版本重新运行或获得相同资格。版本相同也不能代替确切制品的当前验证。
