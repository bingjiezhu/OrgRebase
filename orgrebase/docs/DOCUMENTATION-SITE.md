# 构建与部署文档站

文档站使用README、`docs/`、贡献和许可Markdown作为正文来源；核心指南在`docs/guide/`以`.zh.md`/`.en.md`逐页配对，每种语言只有一份维护源。构建时临时整理链接，
生成静态HTML、中文/英文搜索及精确来源清单；不维护第二套文章，不读取私有运行目录，不运行产品服务或模型。

## 原先怎么写（保留）

构建器可以把已经审核的源码 ZIP 挂到站点的“源码与版本”下载页，并核对 SHA-256。当时把生成好的 HTML 当作独立阅读入口。

## 现状（追加）

原先：GitHub 产品仓库以 README 与 `docs/` Markdown 为阅读入口。提交包和仓库都不预置 `site/`，需要静态站时再本地构建。

现状（2026-09-20）：Markdown 仍是真源。公开阅读入口增加 GitHub Pages，由同一套 `documentation/build.py` 从 `docs/guide/` 的中英配对指南生成：[中文](https://bingjiezhu.github.io/OrgRebase/) · [English](https://bingjiezhu.github.io/OrgRebase/en/)。源码仓库和决赛包仍然不预置 `site/`。

为什么：`git clone` 之后应能直接读 Markdown；站点只是同一批正文的托管渲染，避免再带一份会过期的 HTML 进 Git。

## 为什么这样更新

`git clone` 之后应能直接阅读；站点只是可选渲染。ZIP 下载仍是构建器能力，只在确实提供发行附件时使用，避免把未随仓库发布的下载地址写进站点。

## 本地构建与预览

原先：在产品仓库根（含`pyproject.toml`的目录）执行。现状：工作区 clone 后进入`orgrebase/`执行。文档工具链独立锁定在`documentation/`，不属于产品`uv sync --all-extras`，
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

一个配置、一次构建生成中文和英文路径，页首语言菜单保持当前页面。导航和搜索界面随语言切换，共用包含中英文内容的搜索索引。13组核心指南包含安装、Vertex/DeepSeek、Demo、AT、部署、架构、Skill、许可、贡献和GitHub发布。深层文档保留原文并标明语言，不属于完整英译范围。

## 绑定可下载源码

只有已经审核并取得摘要的源码ZIP才可以作为下载加入站点。提供完整64位SHA-256：

```bash
uv run --project documentation --frozen python documentation/build.py \
  --output /tmp/orgrebase-docs-release \
  --source-archive /path/to/orgrebase-oac-source-snapshot.zip \
  --source-sha256 REPLACE_WITH_REVIEWED_SHA256
```

构建器核对ZIP摘要，并逐项比对包内文档、引用源码和建站工具字节。站点的“源码与版本”页展示实际下载、
摘要和范围；不把远端旧main或旧Release说成本地新版本。源码ZIP只复制到生成目录，不写回源码，
避免源码包包含自身。省略这两个参数仍可构建文档，但不会出现虚假的下载地址。

站内源码链接展示本次构建读取的文件文本。目录、大文件或未包含的历史资源会明确给出源码路径与
适用范围，不跳转到不同版本。本站内容摘要只覆盖文档、引用源码和建站工具，不是完整产品发行资格。

## 部署到GitHub Pages

本仓库提供 `.github/workflows/docs.yml`：`main` 上的文档变更会构建并发布到
[GitHub Pages](https://bingjiezhu.github.io/OrgRebase/)。也可 `workflow_dispatch` 指定 `site_url`。

由有权维护者在GitHub仓库的Settings → Pages中选择GitHub Actions。GitHub生成的实际URL才是已部署地址；
本地构建没有发布公网。如果对外声称可下载当前候选，需要把审核后的源码包另行加入站点构建，不用远端旧Release替代。

也可把生成目录作为普通静态网站托管。实际部署地址确定后，使用`--site-url https://HOST/BASE/`
生成相应canonical地址；不要把MkDocs开发服务器当作生产服务。

官方参考：[Material安装](https://squidfunk.github.io/mkdocs-material/getting-started/)、
[MkDocs配置](https://www.mkdocs.org/user-guide/configuration/)、
[GitHub Pages工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)。
