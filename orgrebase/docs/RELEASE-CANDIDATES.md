# 制品资格与撤回

CI 的 `core` 面向普通 fork，无需 OAC 或服务凭据，执行明确范围的 `make check-core`。
完整 `check` 使用锁文件、项目固定 Python、依赖漏洞检查和真实 PostgreSQL 测试。
`make check` 强制 `ORGREBASE_REQUIRE_POSTGRES_TESTS=1`；缺少数据库工具会失败，不能悄悄跳过并得到绿色结果。

在 `main` 手动运行工作流时，`release-candidate` 只有在同一提交的 `core` 和完整 `check` 都成功后才构建 wheel/source archive，为确切制品生成可重验 SBOM，并使用 GitHub Actions 身份签署来源和 SBOM attestations。完整检查被跳过时不会生成合格候选。结果保留为候选下载件；工作流没有包仓库发布或部署步骤。普通 push/PR 不运行签署步骤。

此工作流配置尚未在远端执行。本地构建的摘要、SBOM 或测试日志不等于 GitHub 签署结果，也不等于客户部署准入。组织需确认仓库计划支持 attestations，保护 main 和工作流变更，限制人工触发与部署权限。按 [GitHub 官方说明](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)，公开仓库与企业私有仓库有不同的可用条件。

## 源码交付与公开发布

wheel 的运行资源仅在 `pyproject.toml` 的 `tool.hatch.build.targets.wheel.force-include` 中登记。常规 Hatch 构建、离线构建和隔离运行检查共用这份清单；增加资源时无需同步另外两份路径表。离线构建遇到声明资源缺失或路径越界会失败。`scripts/verify_packaged_runtime_assets.py` 检查隔离的包布局及本地运行，不等于已经验证最终 wheel 的字节、完整安装环境或真实企业部署。

双仓源码快照是可验证的交付方式：它按明确发行范围保留当前文件原字节，并提供逐文件清单、ZIP 摘要及复验命令。快照可以包含尚未提交的改动；Git HEAD 只描述提交历史，不能代表全部交付内容。源码快照不是完整仓库镜像，也不自带 GitHub 构建签署或生产部署资格。

原先（截至 2026-09-14）：产品源码版本为 0.4.0，[公开 release](https://github.com/bingjiezhu/OrgRebase/releases) 仍为 v0.3.0；本地 OAC 仓库尚未配置 remote。公开发布尚未同步，不妨碍通过双仓快照审阅契约、相关工作和实现。

现状（2026-09-20）：公开仓库改为工作区根，`orgrebase/` 与 `oac-spec/` 并列，产品版本仍为 0.4.0，发行标签为 `v0.4.0`。接收方仍需从可信渠道取得本次制品摘要，不能用版本字符串或旧 release 代替核验。旧的 v0.3.0 产品根发布及冻结证据保留各自身份；新源码不沿用旧制品的验证资格。

后续维护仍应先明确范围与许可，再绑定提交、OAC 准入指纹、锁文件、制品和对应验证结果。配置 remote、提交、打 tag、发布 release 是独立的维护操作，不由本地构建自动执行。

## 配套 OAC 依赖

产品检查需要 OAC 公共 CLI。

原先（产品根仓库）：CI 另检出受信任的 `OAC_REPOSITORY`，并绑定 40 位 `OAC_REVISION`；指纹必须已列入产品 `configs/oac/runtime-admission-policy.json`。两项均未配置时只跑基础检查；仅配置一项、非固定提交、检出漂移或指纹不匹配都会使完整检查失败。

现状（工作区仓库）：一次 clone 已含同级 `oac-spec/`，不依赖开发者电脑上的第二份目录，也不再配置 `OAC_REPOSITORY`。`core` 在每次 push/PR 于 `orgrebase/` 运行。完整 OAC + PostgreSQL 仅 `workflow_dispatch`，并设置 `ORGREBASE_OAC_ROOT=${{ github.workspace }}/oac-spec`。安装 OAC 依赖之前仍先跑 `verify_oac_dependency.py --root`，按项目名和当前准入策略核对源码指纹；工作区内的 OAC 树不再另传 `--revision`（不以第二仓库提交为真源）。checkout 不持久化凭据。[Checkout 官方说明](https://github.com/actions/checkout)。

更新 OAC 时，应审查新实现、同步产品准入策略修订和允许的指纹，再提交工作区内的 `oac-spec/`；不能把不匹配改成警告继续运行。

本地可检查当前源码是否被策略接受：

```sh
uv run python scripts/verify_oac_dependency.py --root ../oac-spec
```

这条命令不要求本地已经提交，因此结果的 `verified_git_revision` 为 null。本地通过不代表远程仓库的完整检查或生产部署资格已经完成。

## 验证候选制品

消费者下载后，核对组织可信渠道给出的制品 SHA-256，并验证来源：

```sh
gh attestation verify /path/to/orgrebase.whl -R bingjiezhu/OrgRebase
```

验收记录应绑定 wheel 摘要、源提交、工作流身份、Python/lock 摘要、部署 ID、tenant、DomainPack/EnterpriseBinding、IdP、来源和目标动作资格。签名证明构建来源；业务正确性和真实效果资格仍由各自证据支持。只从制品旁边下载一个“可信公钥”不能自行建立组织信任。

## 撤回与升级

1. 把受影响制品摘要加入部署准入拒绝清单，记录原因、影响范围和替代版本。停止新的执行准入，保留证据与已发生效果。
2. 检查运行中的工作。未写出的提案重新核对版本和授权；`DISPATCHING` 或 `COMMIT_UNKNOWN` 先查询目标并对账，不能随应用版本回滚盲目重放。
3. 数据库按显式迁移规则处理，禁止通过覆盖数据库或重算历史摘要“回滚”。恢复副本保持隔离，先重放当前删除台账、核对授权与远端效果，再申请解除隔离。
4. 用替代制品重新执行安装、兼容读取、数据库和目标动作资格。旧证据保留原字节；撤回不等于删除旧审计。

当前实现不会自动维护企业的部署拒绝清单，也没有 HA、跨地域容灾或生产 SLA 的实测资格。部署方应把这份流程接入已有制品仓库和变更管理；不再另建一套产品内发布控制台。
