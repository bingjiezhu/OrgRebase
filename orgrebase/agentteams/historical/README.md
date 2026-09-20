# 历史来源资格

`teamharness-v1.2.2.json` 保留升级前来源锁的原始字节，commit 为
`849182af8e017168a5a200a87b1062142caf462d`。其规范 JSON 摘要为
`sha256:d62e5470ad2d803d19fdc00b26958cb2d5f1e237b84870cf7c79464d75cb4888`。

它只用于复验既有 Formation Taskflow 与 OAC mapping 证据。源包构建器按固定摘要登记历史锁，
复验结果标明 `HISTORICAL_SOURCE`；历史资格不授权新的执行、准入或新版分布式部署。
活动来源与当前离线 bundle 仍由上一级 `source-lock.json`、`teamharness-lock.json` 决定。

历史锁中的旧 bundle 路径是当时的记录，不是当前发行依赖；历史复验不需要加载或运行旧 bundle。
升级不重写既有回执、摘要、模型结果与已提交的材料。
