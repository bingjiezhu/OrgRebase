# 部署与运维

本地演示和客户部署使用同一业务控制面，但身份、数据与运维前提不同。`orgrebase serve`默认要求生产配置；只有显式`--local-demo`才进入回环本地模式。不要把Host白名单或界面角色选择当作身份认证。

## 客户部署前置项

| 项目 | 要求 |
|---|---|
| 数据库 | PostgreSQL；先迁移和预置工作区，runtime不使用superuser/BYPASSRLS |
| 企业包 | 密封v2包，组织、Quote、领域/Owner与资源绑定一致 |
| 身份 | HTTPS OIDC issuer、audience、JWKS和服务端成员映射 |
| 网络 | TLS入口、明确Host限制、受控代理信任；凭据放部署秘密管理设施 |
| 来源/目标 | 字段映射、版本语义与权限独立验收，读取不等于准入 |
| 维护 | 备份、恢复隔离、删除账本重放与外部定时purge |

关键环境变量包括`ORGREBASE_WORKSPACE_DB`、`ORGREBASE_TENANT_ID`、`ORGREBASE_ENTERPRISE_PACK`、`ORGREBASE_AUTH_ISSUER`、`ORGREBASE_AUTH_AUDIENCE`、`ORGREBASE_AUTH_JWKS_URL`、`ORGREBASE_AUTH_MEMBERSHIP_FILE`和`ORGREBASE_ALLOWED_HOSTS`。远程PostgreSQL连接还需显式使用`sslmode=verify-full&gssencmode=disable`并配置可信根证书。完整格式见[认证部署原文（英文）](../AUTHENTICATED-DEPLOYMENT.md)。含密码的DSN与token应仅由秘密管理设施提供。

完成迁移、工作区预置和环境配置后，在TLS反向代理后启动服务：

```bash
uv run --frozen orgrebase serve --host 127.0.0.1 --port 8081
```

仅设置API身份变量时，服务接受Bearer客户端；浏览器登录还需`ORGREBASE_OIDC_CLIENT_ID`、客户端秘密文件、会话密钥文件和精确HTTPS `ORGREBASE_PUBLIC_ORIGIN`。按[浏览器入口配置](../AUTHENTICATED-DEPLOYMENT.md#browser-and-network-entry)完成回调与代理信任设置后，再使用WebUI。`run-enterprise-pilot.sh`是本地评估启动器，不能替代这条部署路径。

## 已实现的限制

- 当前租户使用独立数据库，不宣称共享数据库多租户隔离已验收。
- 本地OAC/Skill资格页面不是客户生产准入接口；生产明确禁止依赖夹具的路径。
- Dataverse目标目前只修改一个报价草稿的`name`与`description`，不写价格、行项目、客户、订单或报价状态。
- 内部回滚不等于撤回远端写入。未知结果先对账，不能因请求超时就盲目重发。

## 保留期与容量

私有原文过期后不可读取，但真正清除需管理员purge或外部调度。没有内建自动保留期调度器，备份/WAL有自己的边界。见[维护命令（英文原文）](../PRIVATE-DATA-LIFECYCLE.md#run-the-expiry-sweep)。

容量按实际工作负载测量，保留超时、拒绝和丢弃分母。健康端点通过不等于报价容量，单次本地运行不等于SLA。详见[容量说明（英文原文）](../HTTP-CAPACITY.md)。
