# 业务 HTTP 容量观察

`scripts/run_http_capacity.py` 对已经启动的服务发送 `GET /api/workspace/state`。它使用固定到达时间表、有上限的并发和每请求总超时，记录业务响应、失败以及没有发送出去的请求。它不会发送业务写入、自动重试或访问健康检查来代替业务负载。

这是一项有边界的观测工具。单机结果不能认证生产容量、SLO、可用性、真实企业身份服务或客户收益。数据库和服务端身份校验不能从一次 HTTP 200 推断：通用报告明确把后端标为 `NOT_INFERRED_FROM_HTTP`，真实 PostgreSQL、运行角色、签名身份和数据规模需要运行者另留证据。

## 运行

先按 [认证部署说明](AUTHENTICATED-DEPLOYMENT.md) 启动已准入的工作区，并准备好待测业务数据。用现有凭据管理方式向当前进程注入以下环境变量，不把凭据写入命令参数或报告：

| 变量 | 内容 |
| --- | --- |
| `ORGREBASE_CAPACITY_BASE_URL` | 服务 origin，不能带账号、密码、查询、fragment 或业务路径；远程必须 HTTPS |
| `ORGREBASE_CAPACITY_ACCESS_TOKEN` | 有效、具有工作区读取权限的 Bearer token |
| `ORGREBASE_CAPACITY_CA_BUNDLE` | 可选的受信 CA 文件；不关闭证书校验 |

在产品目录执行，输出目录须已存在，文件必须是新路径：

```bash
.venv/bin/python scripts/run_http_capacity.py \
  --rates 1,2,4,8,16,32 \
  --seconds 5 \
  --max-in-flight 8 \
  --request-timeout 5 \
  --max-schedule-lag 0.25 \
  --cooldown-seconds 5 \
  --output ../.runtime/my-capacity-run/http-observation.json
```

每个到达率持续完整的 `--seconds` 秒，然后等待已发送请求完成或超时。`--cooldown-seconds` 可在档间留出 0–30 秒，默认 0；客户端超时不保证服务端同步任务已经停止，冷却参数必须随结果披露。客户端连接数等于 `--max-in-flight`；不跟随重定向，不读取环境代理。输出使用独占创建与 `0600` 权限，既有报告在发请求前就被拒绝；报告不保存 URL、token、CA 路径、响应正文或异常原文。

本工具最多接受 12 档、每档 60 秒、总到达窗口 600 秒、20,000 个计划请求、256 个并发和 30 秒请求总超时。每档还可能需要一个请求超时长度来等待在途请求，不能只用到达窗口冒充总耗时。响应体上限为 2 MiB，超出也计失败。

开始前先写入非空的 `NOT_RUN` 计划。运行过程中，每档开始与完成都会刷新 `INCOMPLETE` 检查点；正常结束才标 `COMPLETE`。中断时保留已完成档，未开始档保留计划数并标记未发送；正在执行的档标为 `INCOMPLETE_COUNTS_UNKNOWN`，不会把未知请求伪造成成功或未发送。报告包含实际起止时间与 runner 源码 SHA-256。比较实现时另保存对应服务端源码摘要，使用交错重复实验区分改动与同机时序波动。

## 分母与延迟

- `planned` 是固定时间表内的全部请求。`outcomes` 之和必须等于它。
- `success` 要求 HTTP 200、业务状态 schema 正确且事件链状态为 PASS。
- `http_error`、`invalid_business_response`、`transport_error`、`timeout` 和 `response_too_large` 都保留在分母中。
- 并发已满时记录 `concurrency_drop`，不排队、不补发。客户端调度落后超过配置上限或一个到达间隔时记录 `scheduler_drop`，不集中补发过去的请求。
- `sent` / `completed` 只统计实际启动的请求，包含失败和超时。客户端丢弃不会被误报为服务端拒绝。
- p50/p95/p99 使用**所有已发送请求**的“计划到达至完成”耗时，包含客户端调度延误；每条样本也单列实际请求耗时。未发送的请求没有伪造延迟，但完整保留调度延误和丢弃原因。
- `observed_successes_per_second` 分母包含在途请求排空时间。成功数除以计划数另列 `success_per_planned`；不能只展示成功请求的延迟而隐藏过载损失。

观察吞吐何时停止随到达率增长，同时看延迟、服务端 503、超时、客户端丢弃和客户端调度能力。`--max-in-flight` 太小会先测到客户端上限；应明确披露它，再有界调整。进程数、并发上限、keep-alive、业务历史规模、身份模式、网络和数据库版本改变后，需要重新测量。

固定到达时间表的方法参考 [Grafana k6 的开放与封闭负载模型说明](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/)：新请求的计划到达不应随上一请求变慢而推迟。本工具自行实现有界调度，不新增 k6 依赖。服务端并发上限与过载 503 的含义参考 [Uvicorn Server Behavior](https://www.uvicorn.org/server-behavior/)；实际参数仍须结合部署和业务测量决定。

## 本仓库的受控实验路径

复用 `tests/postgres_support.py` 的私有 Unix socket PostgreSQL 集群和受限运行角色，以及 `tests/test_browser_sessions.py` 的真实 Uvicorn HTTPS 服务、`tests/browser_oidc_provider.py` 的本地签名身份/JWKS 和 `tests/enterprise_pack_factory.py` 的合成企业包。这些仅供独立实验驱动使用，发布的 runner 不依赖 pytest 或测试包。

现有真实链路的最短验证命令是：

```bash
ORGREBASE_REQUIRE_POSTGRES_TESTS=1 .venv/bin/python -m pytest \
  tests/test_browser_sessions.py::test_real_https_code_pkce_session_csrf_form_and_logout -q
```

容量实验应先实际登记并拒绝 50 条变更，再向同一业务状态接口发压；记录可见历史数、SQL 次数、源代码摘要、PostgreSQL 版本、受限角色、运行进程数及限流参数。负载前后核对审计 head 和报价摘要不变，过载结束后重新请求业务接口确认恢复。临时集群只能启停本次创建的实例，不操作已有数据库服务。

基线、优化后和启用原生 Uvicorn 并发上限的结果分别存入新的 `.runtime/` 目录，保留所有失败。限流后的 503 是被测结果；HTTP keep-alive 连接也占用 Uvicorn 的并发额度，不能只按正在执行的业务请求解释这个参数。不得把旧的 25 次串行健康 smoke、十万条历史读取测试或离线计价正确性结果改称本实验。
