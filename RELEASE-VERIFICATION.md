# OrgRebase 0.3.0 发布验证

验证日期：2026-08-16  
验证环境：macOS；全量回归使用 CPython 3.12.9，干净安装使用 CPython 3.14.2

## 验证结果

| 检查项 | 结果 |
| --- | --- |
| 全量测试 | 250 passed |
| Workspace 专项测试 | 87 passed |
| 分支覆盖率 | 86.91%（门槛 85%） |
| OrgWorkBench | 192 / 192 passed，OrgWorkScore 100.0 |
| Workspace 证据索引 | 41 项，内容摘要与事件链校验通过 |
| 导出 Schema | 58 项，无漂移 |
| Review Readiness | 44 checks passed，0 failed |
| Core 证据清单 | 16 项校验通过 |
| VMRC Proof Pack | PASS |
| 打包后运行资源 | PASS |
| Wheel RECORD | 78 个带摘要条目逐项匹配 |
| Python 3.14 干净安装 | PASS；CLI、打包资源与 Workspace 完整闭环通过 |

全量回归命令：

```bash
python -m pytest -W error --cov=orgrebase --cov-branch --cov-fail-under=85
```

证据与契约校验命令：

```bash
python scripts/workspace_evidence.py --verify evidence/workspace/latest
python scripts/export_contract_schemas.py --check
python scripts/verify_review_readiness.py --output evidence/workspace/latest/review-readiness.json
python scripts/verify_evidence_manifest.py evidence/latest/manifest.json
python scripts/verify_proof_pack.py evidence/latest/proof-pack.json
python scripts/verify_packaged_runtime_assets.py
```

## 发行边界

- Workspace 本地确定性闭环、同一运行链中的工具调用、证据沉淀、变更预览、审批门禁、最小重建和重启后再次变更均已验证。
- Workspace AgentTeams 的角色映射、固定 Worker 池、传输编译、候选摄取和静态检查已实现；外部 AgentTeams live 运行状态为 `NOT_RUN`。
- 真实企业连接器与真实用户研究尚未运行；用户验证参与者为 0，不主张生产 ROI。
- 发行物采用 PolyForm Noncommercial 1.0.0 与独立商业授权的双许可证模式，属于源码可用软件，不宣称为 OSI 开源软件。

最终 Wheel、源码发行包及其 SHA-256 以 `../release-builds/SHA256SUMS.txt` 为准。
