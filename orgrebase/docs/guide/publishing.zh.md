# GitHub发布与文档部署

## 产品源码目录

[OrgRebase 仓库](https://github.com/bingjiezhu/OrgRebase)以工作区为根：`orgrebase/` 是产品源码，`oac-spec/` 是并列的独立契约项目。一次 clone 提供两棵树；在 `orgrebase/` 中运行 `uv sync`、`make check-core` 和文档站构建。OAC 不嵌入产品 Python 包，部署配置、客户凭据与运行数据库也不放进源码。

受保护源码和运行资源变更触发产品 Core、OAC 公开契约门及 PostgreSQL/OAC 企业边界检查。完整内部历史档案不在公开白名单内，因此公开 CI 不把旧 `make check` 或 `make archive-replay-check` 冒充新源码的发布资格。维护者手动启动公开发布门；构建、隔离安装、SBOM 与签署结果必须绑定同一提交。当前 Beta 本地检查不等于 GitHub 远端 CI 已通过。

## 发布前检查

1. 确认目标commit、许可证、第三方与模型条款、输入授权及凭据排除。
2. 运行对应检查，构建源码和安装制品，验证摘要并从解压后的目录复现入口。
3. 准备版本说明、已知限制和对应运行身份；受控验证与生产验收分别标注。
4. 由有权维护者创建tag和Release，核对制品、源码版本、下载权限与说明一致。

## 构建双语站点

在工作区的 `orgrebase/` 目录执行：

```bash
uv sync --project documentation --locked --python 3.12.13
uv run --project documentation --frozen python documentation/build.py \
  --output /tmp/orgrebase-docs-new
python3 -m http.server 8018 --bind 127.0.0.1 --directory /tmp/orgrebase-docs-new
```

输出目录须为空且位于产品源码外。MkDocs、Material与i18n插件使用独立锁；一次构建产生中文和英文路径，语言菜单保持当前页。通过HTTP浏览以启用搜索。

加入经过审核的源码下载时，同时提供`--source-archive`与`--source-sha256`。构建器验证摘要及文档、引用源码和工具源字节；下载仅进入静态产物。分发包与站内下载使用同一源码ZIP和摘要。

PR 只构建并保留只读预览；`main` 上的文档或站点输入变更才会构建并部署到 [bingjiezhu.github.io/OrgRebase](https://bingjiezhu.github.io/OrgRebase/)。本地建站不会推送或覆盖线上站。合并后还须检查线上“本站源码与下载”、许可页及语言切换，确认 Pages 已更新到目标源码。详细命令见[建站说明](../DOCUMENTATION-SITE.md)。

## 版本与验收记录

发行说明记录实际tag、commit、源码及安装包SHA-256和验收结果。文档站的内容摘要仅覆盖文档、引用源码与建站工具，不能代替完整产品摘要。历史模型回执、当前本地测试和客户验收各有自己的运行身份；下载页应绑定其实际提供的源码版本。
