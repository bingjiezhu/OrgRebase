# Vertex Gemini接入

云模型参考启动器默认使用Vertex上的Gemini 3.8 Flash。它通过既有结构化候选接口参与OAC适配或Reviewer建议；确定性校验和负责人批准仍是独立门禁。

## 前置条件

- 完成[安装](quickstart.zh.md)，取得匹配的相邻`oac-spec/`。
- 在有权使用的Google Cloud项目启用所需Vertex服务，配置允许调用目标模型的身份。
- 凭据留在部署环境：支持显式Vertex token/key或配置好的ADC路径，不写进源码、命令示例或提交记录。
- 实际地区、配额、模型访问权限和服务条款由部署方核对。配置存在不是调用成功。

## 启动原生旅程

```bash
export ORGREBASE_VERTEX_PROJECT="YOUR_GCP_PROJECT"
export ORGREBASE_VERTEX_MODEL_ID="gemini-3.8-flash"
export ORGREBASE_OAC_ROOT="$(pwd)/../oac-spec"
export ORGREBASE_DEMO_PORT=8000
./run-semifinal-demo.sh live
```

在产品目录执行。项目ID不是API key；不要把密钥填进它。provider直接使用的项目环境名还包括`ORGREBASE_VERTEX_PROJECT_ID`，启动器会把上面的`ORGREBASE_VERTEX_PROJECT`传入项目参数。

显式凭据变量为`ORGREBASE_VERTEX_ACCESS_TOKEN`或`ORGREBASE_VERTEX_API_KEY`，由秘密管理设施注入。未配置时，仅按当前实现支持的ADC/gcloud解析路径获取凭据，不假设任意机器已经登录。先在自己的环境核对权限，不把token打印到日志。

`live`启用`LIVE_VERTEX` OAC路径，会产生真实外部请求与相应费用。准入后才进入任务协作；模型的PASS不代替确定性契约检查，更不代表业务批准。观察回执中的provider、模型、状态和使用量。成功回执证明成功响应；失败或结果未知的尝试，应按派发状态和逐次记录区分未发送、已发送或可能已发送。HTTP 429 也计为一次提供方尝试，不能记作零调用；未取得的使用量保留为未知，不按零处理。

## 失败与替代路径

身份、配额、网络或结构错误会记录为失败或未知；诊断须使用本次调用记录。先读原因和当前状态，再决定是否重试。无云模型时仍可做[无模型首跑](quickstart.zh.md)；本地Ollama是可显式选择的备用评估路径。

更细接口见[模型、Agent与Tool原文](../MODEL-AGENT-TOOL-INTERFACES.md)。

## 配额错误与超时边界

只有明确的 HTTP 429 响应才进行有界重试：最多3次提供方尝试，共用同一个60秒 provider budget，并保留逐次尝试记录。网络超时等结果未知情形不会自动重发。此限制针对一次提供方调用，不是整条业务链路在60或65秒内完成的承诺。
