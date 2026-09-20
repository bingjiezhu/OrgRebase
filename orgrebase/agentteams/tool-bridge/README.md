# Vertex 工具调用桥

这个桥为丢失 Gemini 工具调用签名的 OpenAI 兼容客户端恢复签名。它只转发模型请求，不执行工具，也不授予业务写入或批准权限。

本机运行：

```sh
export VERTEX_UPSTREAM_URL='https://aiplatform.googleapis.com/v1beta1/projects/YOUR_PROJECT/locations/global/endpoints/openapi'
uv run python -m orgrebase.vertex_tool_bridge
```

默认监听 `127.0.0.1:8080`。`PORT` 可修改端口；`VERTEX_BRIDGE_HOST` 可显式指定监听地址，空值会被拒绝。监听本机不影响向 Vertex AI 发起 HTTPS 请求。每个桥实例有自己的上游配置和有界签名缓存；同一实例的多次请求共享该缓存，以恢复后续工具结果回合所需的签名。

现有 `deployment.yaml` 显式配置 `VERTEX_BRIDGE_HOST=0.0.0.0`，保留 Kubernetes 的 Pod 探测和 ClusterIP Service 访问。`scripts/deploy_vertex_tool_bridge.sh` 继续使用该部署文件。

桥自身没有调用方认证，会向固定 Vertex 上游转发调用方提供的 `Authorization`。容器部署必须由部署环境限制可信调用方和访问路径；ClusterIP、健康检查和实例缓存隔离均不等于调用方认证。不要把该服务直接发布到公网或交给互不信任的调用方共享。需要多方访问时，应先落实部署边界和访问控制，再开放监听地址。
