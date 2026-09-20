# DeepSeek接入

当前客户端的API模型名默认为`deepseek-flash`。它是提供方alias，不是不可变模型制品摘要。固定模型名称不能代替服务版本与实际回执记录。

## 接口与凭据

| 项目 | 值 |
|---|---|
| provider | `deepseek` |
| endpoint | `https://api.deepseek.com/chat/completions` |
| model | `deepseek-flash` |
| 凭据变量 | `DEEPSEEK_API_KEY` |

通过秘密管理设施或当前终端环境提供key，不写入源码、截图或回执。OrgRebase验证结构化候选，记录状态、摘要和使用量；模型输出仍不能直接准入、批准或Apply。

## 运行边界

DeepSeek接入既有原生Reviewer候选路径。该旅程必须显式设置`ORGREBASE_OAC_EXECUTION_MODE=LIVE_VERTEX`：Vertex负责OAC映射，DeepSeek负责Reviewer工作。需要同时配置[Vertex指南](models-vertex.zh.md)中的项目与凭据，以及`DEEPSEEK_API_KEY`。变化轮的ModelRequestV2/Responses接口仍不属于本页provider的覆盖范围。

完成安装并由外部安全注入两套提供方凭据后，在产品目录执行；使用新的工作目录，并将`YOUR_GCP_PROJECT`替换为有权使用的Vertex项目：

```bash
export ORGREBASE_OAC_ROOT="$(pwd)/../oac-spec"
export ORGREBASE_VERTEX_MODEL_ID=gemini-3.8-flash
export ORGREBASE_OAC_EXECUTION_MODE=LIVE_VERTEX
./run-enterprise-pilot.sh start \
  --pack examples/enterprise-quote-pilot/evergreen \
  --work-dir ../pilot-deepseek-new \
  --competition-mode golden --model-provider deepseek \
  --vertex-project YOUR_GCP_PROJECT
```

启动器拒绝未显式选择`LIVE_VERTEX`的DeepSeek配置。`OFFLINE_LOCAL`只允许本地提供方，不能运行DeepSeek Reviewer。Vertex生成的映射候选仍需确定性校验和指定负责人准入；四域Worker仍为确定性程序。两家提供方的云调用都可能计费。该路径尚未取得DeepSeek真实调用或业务闭环记录；实现与模拟传输测试只证明已测试的协议行为，不代表真实运行成功。

provider请求JSON对象并在本地校验Schema；不把JSON输出模式说成提供方保证了全部业务Schema。

## 核对结果

检查同run的模型请求与响应状态、输出Schema、候选绑定、Reviewer/确定性判定差异，以及后续人工批准。API连接测试只能证明该请求被服务处理，不等于完成报价、企业接入或生产验收。

接口的官方名称与格式见[DeepSeek首次调用文档](https://api-docs.deepseek.com/zh-cn/)。网络、额度或输出结构失败时保留失败记录，不用其他模型的成功收据代替。
