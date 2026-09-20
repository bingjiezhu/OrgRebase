# 贡献与安全反馈

优先提交可说明问题、可验证边界的小改动，保持候选、准入、批准与规范写入的分工。

## 开发环境与检查

```bash
uv sync --locked --all-extras
make check-core
```

`check-core`是有限的贡献者检查，不需要相邻OAC、PostgreSQL或服务凭据，不能代替完整集成验收。完整`make check`还需要准入的OAC源码、PostgreSQL工具以及对应历史档案；轻量分发包可能不含这些档案，不可用skip或旧结果宣称完整发布通过。

行为改动应提供适合其风险的负例和验证。修复权限或一致性问题时，保留错误Owner、陈旧摘要、重试与未知结果的测试，并核对其仍能捕获相应错误。

## 文档与翻译

核心指南以同名`.zh.md`/`.en.md`配对。修改命令、参数、权限或许可时同步两种语言。详细资料保留原文语言标记。可选建站环境与业务依赖分开，见[建站与发布](publishing.zh.md)。

## 反馈路径

一般问题可使用[项目Issues](https://github.com/bingjiezhu/OrgRebase/issues)，注明精确源码版本、运行模式、复现步骤和脱敏错误。复用与集成尝试使用 Reuse 模板。敏感漏洞按[SECURITY原文](../../SECURITY.md)的私密渠道报告，不把凭据或客户资料公开到Issue。

[贡献条款原文](../../CONTRIBUTING.md)、[社区说明](../COMMUNITY.md)和[行为准则](../../CODE_OF_CONDUCT.md)按原文适用。存在反馈机制不等于已有第三方采用、上游贡献或承诺了响应SLA。
