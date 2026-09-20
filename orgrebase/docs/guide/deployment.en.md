# Deployment and operations

Local demonstrations and customer deployments share the business control plane but have different identity, data and operations prerequisites. `orgrebase serve` requires production configuration by default. Only explicit `--local-demo` selects the loopback local mode. A Host allowlist or UI role selector is not authentication.

## Customer-deployment prerequisites

| Area | Requirement |
|---|---|
| Database | PostgreSQL, migrated and provisioned before startup; no runtime superuser/BYPASSRLS |
| Enterprise Pack | Sealed v2 Pack with matching organization, Quote, domain/owner and resource bindings |
| Identity | HTTPS OIDC issuer, audience, JWKS and server-owned membership mapping |
| Network | TLS ingress, explicit hosts and proxy trust; credentials in a deployment secret facility |
| Sources/targets | Separately qualified field mappings, version semantics and permissions; reading is not admission |
| Maintenance | Backups, recovery quarantine, deletion-ledger replay and externally scheduled purge |

Key variables include `ORGREBASE_WORKSPACE_DB`, `ORGREBASE_TENANT_ID`, `ORGREBASE_ENTERPRISE_PACK`, `ORGREBASE_AUTH_ISSUER`, `ORGREBASE_AUTH_AUDIENCE`, `ORGREBASE_AUTH_JWKS_URL`, `ORGREBASE_AUTH_MEMBERSHIP_FILE` and `ORGREBASE_ALLOWED_HOSTS`. Remote PostgreSQL connections also require explicit `sslmode=verify-full&gssencmode=disable` and a trusted root certificate. Full formats are in the [authenticated deployment reference (English)](../AUTHENTICATED-DEPLOYMENT.md). Supply password-bearing DSNs and tokens through the deployment secret facility.

After migration, workspace provisioning and environment configuration, start the service behind the TLS reverse proxy:

```bash
uv run --frozen orgrebase serve --host 127.0.0.1 --port 8081
```

API identity settings alone enable Bearer clients. Browser login additionally requires `ORGREBASE_OIDC_CLIENT_ID`, the client-secret and session-key files, and an exact HTTPS `ORGREBASE_PUBLIC_ORIGIN`. Configure the callback and proxy trust described in [browser entry](../AUTHENTICATED-DEPLOYMENT.md#browser-and-network-entry) before using WebUI. `run-enterprise-pilot.sh` is a local evaluation launcher, not this deployment path.

## Implemented limits

- Tenants use separate databases; shared-database multi-tenancy is not qualified.
- Local OAC/Skill qualification pages are not production customer-onboarding endpoints. Fixture-backed routes are deliberately rejected in production.
- The Dataverse target currently updates only `name` and `description` on one configured draft Quote, not prices, line items, customers, orders or quote status.
- Internal rollback does not reverse a remote write. Reconcile unknown results before considering a retry; a timeout is not proof of no effect.

## Retention and capacity

Expired private text becomes unreadable, but clearing it requires an administrator purge or external scheduler. There is no built-in retention scheduler, and backups/WAL have separate lifetimes. See the [maintenance client (English)](../PRIVATE-DATA-LIFECYCLE.md#run-the-expiry-sweep).

Measure capacity against the actual workload and retain timeout, rejection and drop denominators. A passing health endpoint is not Quote capacity, and one local run is not an SLA. See the [capacity reference (English)](../HTTP-CAPACITY.md).
