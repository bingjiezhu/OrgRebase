# 构建与部署文档站

文档站使用README、`docs/`、贡献和许可Markdown作为正文来源；核心指南在`docs/guide/`以`.zh.md`/`.en.md`逐页配对，每种语言只有一份维护源。当前源码对应 OrgRebase `0.5.0b4` Beta；版本来自产品 `pyproject.toml`，站点页脚和“本站源码与下载”页同时展示版本及内容摘要。构建时临时整理链接，
生成静态HTML、中文/英文搜索及精确来源清单；不维护第二套文章，不读取私有运行目录，不运行产品服务或模型。

## 当前结构

公开仓库以工作区为根，产品源码在 `orgrebase/`，配套 OAC 在同级 `oac-spec/`。Markdown 是阅读真源；GitHub Pages 是同一批正文的托管渲染：[中文](https://bingjiezhu.github.io/OrgRebase/) · [English](https://bingjiezhu.github.io/OrgRebase/en/)。仓库不预置生成的 `site/`。本地候选不会自动更新线上站，线上许可和版本以部署后实际页面为准。

只有经过审核的源码 ZIP 才能加入站点下载页；构建器核对 ZIP SHA-256 和实际引用的文档、源码、工具字节。没有附件时不显示虚假下载地址。

## 本地构建与预览

在工作区的 `orgrebase/` 目录执行。文档工具链独立锁定在`documentation/`，不属于产品`uv sync --all-extras`，
也不会进入产品运行依赖。

```bash
uv sync --project documentation --locked --python 3.12.13
uv run --project documentation --frozen python documentation/build.py \
  --output /tmp/orgrebase-docs-v1
python3 -m http.server 8018 --bind 127.0.0.1 --directory /tmp/orgrebase-docs-v1
```

输出目录必须为空且位于产品源码目录之外。重建用新目录，避免覆盖先前版本。
打开 `http://127.0.0.1:8018/`，不要使用`file://`：浏览器搜索需要通过HTTP读取索引。
静态站没有账号、业务API或后台写入；停止预览按Ctrl-C。首次安装文档依赖需联网或完整缓存。

工具链固定MkDocs 1.6.1、Material 9.7.7、mkdocs-static-i18n 1.3.1及独立`uv.lock`。选择兼容的已锁版本，不隐式跟随主版本升级。
字体使用本机字体，站点搜索在浏览器内执行，不使用外部搜索服务或分析脚本。

## 中英文与深层原文

一个配置、一次构建生成中文和英文路径，页首语言菜单保持当前页面。导航和搜索界面随语言切换，共用包含中英文内容的搜索索引。核心指南包含项目方法、安装、Vertex/DeepSeek、Demo、AT、部署、架构、Skill、许可、贡献和GitHub发布。深层文档保留原文并标明语言，不属于完整英译范围。

## 绑定可下载源码

只有已经审核并取得摘要的源码ZIP才可以作为下载加入站点。提供完整64位SHA-256：

```bash
uv run --project documentation --frozen python documentation/build.py \
  --output /tmp/orgrebase-docs-release \
  --source-archive /path/to/orgrebase-oac-source-snapshot.zip \
  --source-sha256 REPLACE_WITH_REVIEWED_SHA256
```

构建器核对ZIP摘要，并逐项比对包内文档、引用源码和建站工具字节。站点的“本站源码与下载”页展示实际下载、
摘要和范围；不把远端旧main或旧Release说成本地新版本。源码ZIP只复制到生成目录，不写回源码，
避免源码包包含自身。省略这两个参数仍可构建文档，但不会出现虚假的下载地址。

站内源码链接展示本次构建读取的文件文本。目录、大文件或未包含的历史资源会明确给出源码路径与
适用范围，不跳转到不同版本。本站内容摘要只覆盖文档、引用源码和建站工具，不是完整产品发行资格。

## 部署到GitHub Pages

本仓库提供 `.github/workflows/docs.yml`：PR 严格构建并保存预览；只有 `main` push 或在 `main` 手动触发才具备 Pages 部署权限。OrgRebase、OAC 与根正式文件的适用变化触发构建；工作流从同一检出构建并复验 `github` profile 源码 ZIP，将确切 SHA-256 和 ZIP 传入独立锁定的文档构建器，再发布到
[GitHub Pages](https://bingjiezhu.github.io/OrgRebase/)。维护者也可在 `main` 手动触发。

由有权维护者在GitHub仓库的Settings → Pages中选择GitHub Actions。GitHub生成的实际URL才是已部署地址；
本地构建没有发布公网。合并后核对线上许可页、语言切换和“本站源码与下载”页的版本及摘要；在此之前不能把旧站说成新候选。自动构建的下载件来自该次检出，并通过 ZIP 与文档内容匹配检查；它仍与人工门通过后签署的 GitHub Release 资产有独立身份，页面不借用旧 Release 的资格。Actions 使用官方完整 commit SHA；PR 构建保持只读仓库权限。

也可把生成目录作为普通静态网站托管。实际部署地址确定后，使用`--site-url https://HOST/BASE/`
生成相应canonical地址；不要把MkDocs开发服务器当作生产服务。

官方参考：[Material安装](https://squidfunk.github.io/mkdocs-material/getting-started/)、
[MkDocs配置](https://www.mkdocs.org/user-guide/configuration/)、
[GitHub Pages工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)。
