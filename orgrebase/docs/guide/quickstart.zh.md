# 安装与快速开始

先用一笔公开历史交易验证安装、计价和批准链，再接模型。

## 取得匹配版本

使用与文档匹配的源码版本；若站点提供源码下载，先核对其SHA-256。产品源码目录包含`pyproject.toml`、`uv.lock`和`scripts/`。原先单独克隆产品仓库不包含OAC；现状公开仓库是工作区根，并列的`orgrebase/`和`oac-spec/`一次 clone 即可，产品目录就是`orgrebase/`。本节无需OAC。

进入`orgrebase/`后执行。需要Python 3.12.13与uv；首次依赖安装需要网络或完整缓存。

```bash
uv sync --locked --all-extras
uv run --frozen python scripts/run_public_quote_replay.py \
  --output ../quote-first-run --limit 1
```

输出目录必须不存在。打开`../quote-first-run/index.html`，核对`report.json`的`status=PASS`，以及`measurement_summary`中的`planned_cases=1`、`outcomes.PASS=1`、`outcomes.FAILED=0`和`outcomes.INCOMPLETE=0`。默认首个样本是发票560602；[交互计价演示](demo.zh.md)选用557670，两者用途不同。

这条命令实际执行Formation、Preview、脚本身份批准、Apply及独立金额核验。候选是本地确定性程序；无需模型、OAC、PostgreSQL或云凭据，也不构成原生AgentTeams或员工UAT证据。

## 下一步

- 完整云模型主线：[Vertex](models-vertex.zh.md)或[DeepSeek](models-deepseek.zh.md)。
- 配置复用与范围：[Skill和企业包](skills.zh.md)。
- 面向客户的身份和数据库：[部署](deployment.zh.md)。

如安装失败，保留退出码和错误，先检查Python版本、网络和锁文件；不要用旧HTML报告替代失败的新运行。
