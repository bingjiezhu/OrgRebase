# 安装与快速开始

先用一笔公开历史交易验证安装、计价和批准链，再接模型。

## 取得匹配版本

本指南对应产品 `0.5.0b4` Beta。运行前核对 `pyproject.toml` 的版本；使用源码下载时同时核对其 SHA-256。公开仓库以工作区为根，`orgrebase/` 与 `oac-spec/` 并列，产品目录包含 `pyproject.toml`、`uv.lock` 和 `scripts/`。本节无需 OAC。

包声明支持 Python 3.12–3.14；本版隔离首跑以 3.12.13 验证。安装 [uv](https://docs.astral.sh/uv/) 后，在匹配版本的仓库中运行：

```bash
git clone https://github.com/bingjiezhu/OrgRebase.git
cd OrgRebase/orgrebase
uv sync --locked
uv run --frozen python -c "import sqlite3; print(sqlite3.sqlite_version)"
uv run --frozen python scripts/run_public_quote_replay.py \
  --output ../quote-first-run --limit 1
```

若使用源码包，先进入解压后包含`pyproject.toml`和`scripts/`的目录，再运行`uv sync`。
首次安装依赖需要网络或完整缓存；
本次无需安装离线学习及开发可选依赖。

输出目录必须不存在。打开`../quote-first-run/index.html`，核对`report.json`的`status=PASS`，以及`measurement_summary`中的`planned_cases=1`、`outcomes.PASS=1`、`outcomes.FAILED=0`和`outcomes.INCOMPLETE=0`。默认首个样本是发票560602；[交互计价演示](demo.zh.md)选用557670，两者用途不同。

这条命令实际执行Formation、Preview、脚本身份批准、Apply及独立金额核验。候选是本地确定性程序；无需模型、OAC、PostgreSQL或云凭据，也不构成原生AgentTeams或员工UAT证据。

<a id="sqlite-runtime"></a>

## 本地运行的 SQLite 要求

复算与本地 WebUI 会写入文件式 SQLite 数据库。所选 Python 解释器必须链接 **SQLite 3.51.3 或更高版本**、**3.44.x 分支中的 3.44.6 或更高版本**，或 **3.50.x 分支中的 3.50.7 或更高版本**；这些版本包含上游 [WAL-reset 修复](https://sqlite.org/wal.html#walreset)。上述检查命令显示 Python 实际链接的库，独立的 `sqlite3` 命令可能使用另一版本。

`SQLITE_WAL_RUNTIME_UNSUPPORTED:<version>` 表示链接库未包含受支持的修复。安装或重新构建链接了补丁版 SQLite 的受支持 Python，用该解释器重建项目环境，再核对链接版本并重跑。只升级独立 SQLite CLI 不会更新 Python 的链接库。更换环境时保留已有数据库及其 WAL 文件，详见[存储与迁移](../STATE-STORE-MIGRATIONS.md)。生产部署使用 PostgreSQL。

## 创建受支持的企业模板

公开复算用于核对历史商品篮子。要准备自己的可审阅初始事实，从产品目录初始化独立的计价 Quote 模板：

```bash
uv run --frozen orgrebase enterprise-pilot-init \
  --template priced-quote --output ../priced-draft
uv run --frozen orgrebase enterprise-pilot-draft-preflight --draft ../priced-draft
uv run --frozen orgrebase enterprise-pilot-seal \
  --draft ../priced-draft --output ../priced-pack
```

每个输出路径均须新建。企业部署前替换合成事实、负责人和来源引用。密封只校验并封装精确输入，不准入契约、授权任务或应用变更。[无模型浏览器指南](demo.zh.md#model-free-priced-workspace)提供匹配 OAC 的前提和显式 Quote + Discount Memo 配置。

## 下一步

- 贡献者检查：先运行`uv sync --locked --extra dev`，再运行`make check-core`。
- 云模型参考主线：[Vertex](models-vertex.zh.md)；[DeepSeek](models-deepseek.zh.md)是可选 Reviewer 协议路径，尚无真实闭环记录。
- 配置复用与范围：[Skill和企业包](skills.zh.md)。
- 面向客户的身份和数据库：[部署](deployment.zh.md)。

如安装失败，保留退出码和错误，先检查 Python 解释器、链接 SQLite 版本、网络和锁文件；不要用旧 HTML 报告替代失败的新运行。
