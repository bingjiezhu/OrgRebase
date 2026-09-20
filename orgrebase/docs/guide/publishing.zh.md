# GitHub发布与文档部署

## 产品源码目录

[OrgRebase产品仓库](https://github.com/bingjiezhu/OrgRebase)维护源码、契约、测试、文档和许可证。产品根目录包含`pyproject.toml`、`src/`、`docs/`、`documentation/`与`LICENSE`；完整源码分发中的对应目录名为`orgrebase/`。部署文件、客户凭据与运行数据库分别管理。

OAC是独立依赖。原先：GitHub 产品根不含 OAC，完整源码分发才并列 `orgrebase/` 与 `oac-spec/`；单独产品仓库需另取版本放到`../oac-spec`或配置`ORGREBASE_OAC_ROOT`。现状：若按工作区发布，一次 clone 的仓库根即含两棵树，默认`../oac-spec`可用。为什么：配套交付，同时避免把 OAC 嵌进产品包内部或改写其 Apache-2.0 / CC BY 4.0 许可。完整CI在产品根布局下通过`OAC_REPOSITORY`与40位`OAC_REVISION`绑定；改为工作区后应在`orgrebase/`子目录跑检查，并直接使用仓库内的`oac-spec/`。core CI不需要 OAC。

## 发布前检查

1. 确认目标commit、许可证、第三方与模型条款、输入授权及凭据排除。
2. 运行对应检查，构建源码和安装制品，验证摘要并从解压后的目录复现入口。
3. 准备版本说明、已知限制和对应运行身份；受控验证与生产验收分别标注。
4. 由有权维护者创建tag和Release，核对制品、源码版本、下载权限与说明一致。

## 构建双语站点

在产品根目录执行：

```bash
uv sync --project documentation --locked --python 3.12.13
uv run --project documentation --frozen python documentation/build.py \
  --output /tmp/orgrebase-docs-new
python3 -m http.server 8018 --bind 127.0.0.1 --directory /tmp/orgrebase-docs-new
```

输出目录须为空且位于产品源码外。MkDocs、Material与i18n插件使用独立锁；一次构建产生中文和英文路径，语言菜单保持当前页。通过HTTP浏览以启用搜索。

加入经过审核的源码下载时，同时提供`--source-archive`与`--source-sha256`。构建器验证摘要及文档、引用源码和工具源字节；下载仅进入静态产物。分发包与站内下载使用同一源码ZIP和摘要。

原先：GitHub Pages 工作流默认只构建预览，有权维护者配置 Pages 并选择部署后才会发布站点。
现状：`main` 上的文档变更会构建并发布到 [bingjiezhu.github.io/OrgRebase](https://bingjiezhu.github.io/OrgRebase/)。工作流仍可用 `workflow_dispatch` 指定 `site_url`。详细命令及维护范围见[建站说明](../DOCUMENTATION-SITE.md)。

## 版本与验收记录

发行说明记录实际tag、commit、源码及安装包SHA-256和验收结果。文档站的内容摘要仅覆盖文档、引用源码与建站工具，不能代替完整产品摘要。历史模型回执、当前本地测试和客户验收各有自己的运行身份；下载页应绑定其实际提供的源码版本。
