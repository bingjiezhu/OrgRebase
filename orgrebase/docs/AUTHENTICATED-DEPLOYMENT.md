# Authenticated single-tenant deployment

The existing Workspace API supports a PostgreSQL-backed, authenticated deployment configuration. It uses the same WorkspaceService and RebaseWorkflow as local validation. This configuration is a technical boundary; it does not certify an enterprise IdP, customer connector, backup service, or production rollout.

For synthetic local demonstrations, opt in with `orgrebase serve --local-demo` or `make serve-demo`. This mode has no external identity provider and the CLI permits only a loopback bind. Direct ASGI launches must explicitly set `ORGREBASE_DEPLOYMENT_MODE=local` and bind to loopback; a Host allowlist is not authentication. The explicitly named `enterprise-pilot-start` command remains a loopback-only local pilot. Ordinary `orgrebase serve`, `make serve`, and the ASGI factory use production configuration by default.

`orgrebase serve --limit-concurrency N --backlog M` exposes Uvicorn's existing resource limits. Choose `N` from measurements on the intended deployment; the default remains unset. The limit counts open connections (including idle keep-alive connections) and active tasks, and excess requests receive HTTP 503. `N` must be at least 2 because the current connection counts toward the limit. `M` controls the operating system's TCP accept queue; it does not queue requests refused with 503. Do not automatically repeat approval or apply commands after a transport failure: read their durable outcome first. See the [Uvicorn resource-limit semantics](https://www.uvicorn.org/server-behavior/) and the [HTTP capacity procedure](HTTP-CAPACITY.md) for load measurement and reporting boundaries.

Loopback local development without identity configuration exposes `/docs`, `/redoc`, and `/openapi.json` for API inspection and contract tooling. Local instances with controlled role sessions, OIDC or bearer-token authentication do not expose these routes, even after authentication; production also excludes them. Use the source routes and standalone contract schemas for protected instances instead of relaxing authentication. HTML and static resources use `Cache-Control: no-cache` to require revalidation while retaining ETag support. Business/session API responses and handled HTTP errors use `no-store`, including in local mode; existing authentication, CSRF checks and private-response headers remain in force.

## Optional local role sessions

For a local walkthrough with separate requester, approver and executor identities,
set an exact loopback origin before starting the service:

```bash
ORGREBASE_LOCAL_ROLE_SESSION_ORIGIN=http://127.0.0.1:8879 \
uv run --frozen orgrebase serve --local-demo --host 127.0.0.1 --port 8879
```

The same variable is supported by `run-enterprise-pilot.sh start`; the
[demo guide](guide/demo.en.md) includes the public-basket and Vertex prerequisites.
Keep the configured origin, browser address and listener port identical. Do not
switch between `localhost` and `127.0.0.1`, append a path to the origin, or expose
this mode through a remote proxy. Omitting the variable preserves ordinary local
operation. Production rejects this setting, and it cannot be combined with OIDC
or Bearer identity configuration.

Choose an identity through **Switch demo role → Use this role** in the sidebar.
The server derives the available actors from the workspace profile and resource
owners; the browser cannot submit an arbitrary actor or add roles. The current
identity remains visible beside the working area. The walkthrough uses:

| Local identity | Work performed |
|---|---|
| Organization onboarding owner | Generate, review and admit the OAC candidate |
| Business requester | Create the task and change proposals, preview impact, export results |
| Matching domain owner, such as Finance owner | Review and approve or reject that owner's exact proposal |
| Change executor | Apply an already approved proposal after permission and version checks |
| Skill steward | Run isolated Skill qualification/recovery and govern experience candidates |

The finance role has no execution permission. Its approval leaves the proposal
`APPROVED` and the official Quote unchanged until an executor applies it. The
requester cannot approve, and the executor cannot grant approval. In other
configurations an account with both permissions may let the UI proceed from
approval to Apply after a fresh server check; the two operations still retain
separate authority and receipts.

These are selectable example identities, not passwords, employee authentication
or enterprise SSO. They use opaque HttpOnly, SameSite=Strict browser cookies and
the existing server authorization chain, including exact-owner checks.
`GET /api/session` only reads session state; it never creates or replaces a cookie.
For a missing or expired local session, selecting a role explicitly sends
`POST /api/session/local-actor` with only `{"actor_id": "…"}`, the exact configured
Origin and `X-OrgRebase-Local-Session: initialize`. This initialization header is
accepted only when no valid session exists. A valid session still requires its
current CSRF token and exact Origin for role switches and business mutations;
the initialization header cannot bypass that check. An actor field or header
cannot override the selected identity on a business request.

A successful role switch rotates the cookie and clears unsent UI drafts.
Registered proposals and business records remain in the workspace; failed work
commands are never replayed automatically. Restarting the service invalidates
local sessions, so select a role again. The local role-selection endpoint is not
registered in production. Use production OIDC and server-owned membership below
for actual employees.

## Required configuration

Production is the default. You may set `ORGREBASE_DEPLOYMENT_MODE=production` explicitly. The application refuses to start unless all required inputs validate:

| Variable | Meaning |
|---|---|
| `ORGREBASE_WORKSPACE_DB` | A `postgresql://` or `postgres://` DSN for this tenant's dedicated database |
| `ORGREBASE_TENANT_ID` | Exact organization ID in the admitted Pack and the persisted database binding |
| `ORGREBASE_ENTERPRISE_PACK` | Sealed `orgrebase.enterprise-quote-pilot-pack.v2` directory with initial facts and an explicit EnterpriseBinding; future fixture changes and the built-in Northstar handler are rejected |
| `ORGREBASE_AUTH_ISSUER` | Exact HTTPS token issuer |
| `ORGREBASE_AUTH_AUDIENCE` | Audience of the OrgRebase resource API |
| `ORGREBASE_AUTH_JWKS_URL` | Administrator-configured HTTPS JWKS endpoint |
| `ORGREBASE_AUTH_MEMBERSHIP_FILE` | Server-owned membership JSON file |
| `ORGREBASE_ALLOWED_HOSTS` | Comma-separated explicit service host names; no wildcard or testserver |
| `ORGREBASE_AUTH_MAX_TOKEN_SECONDS` | Maximum access-token lifetime, 900 seconds by default; allowed range 30–3600 |
| `ORGREBASE_AUTH_CA_BUNDLE` | Optional trusted CA PEM bundle for an enterprise issuer; certificate and hostname verification remain required |
| `ORGREBASE_PRIVATE_RETENTION_SECONDS` | Private source-text retention, 86400 seconds by default; 0 disables raw persistence and the maximum is 604800 |

Store credentials in the deployment's secret facility. Do not put DSNs with passwords in repositories, shell transcripts, evidence packages, or browser code. Run the API under a dedicated service identity and use a separate database per tenant. SQLite remains the offline/local adapter. The implementation does not claim shared-database row-level tenant isolation.

Production parses PostgreSQL connection parameters with libpq's parser, including URI query overrides and host lists. Every connection requires an explicit nonempty host. Remote destinations require explicit `sslmode=verify-full&gssencmode=disable`; configure the appropriate trusted root certificate for the server. `prefer` can fall back to plaintext, and `require` does not establish hostname verification. GSS encryption otherwise takes precedence over the SSL mode, so this configuration explicitly selects the verified TLS transport. [PostgreSQL connection parameters](https://www.postgresql.org/docs/17/libpq-connect.html)

Only an absolute Unix-socket directory or a numeric loopback address qualifies for the controlled local transport exception. DNS names, including `localhost`, do not qualify automatically. `hostaddr` participates in destination classification, and mixed host lists must satisfy the remote rule. Empty hosts, service-file indirection and ambient `PGHOSTADDR` are rejected; `PGHOST` cannot supply a missing production host. Explicit transport settings are checked again when opening the runtime. This prevents an apparently local URI from inheriting a different network destination. Local deployment mode keeps its existing transport behavior. [PostgreSQL environment defaults](https://www.postgresql.org/docs/17/libpq-envars.html), [service-file precedence](https://www.postgresql.org/docs/17/libpq-pgservice.html)

The database is bound before business seeding. Reopening a database for a different tenant is rejected. Readiness uses the storage adapter's health operation, so no API handler depends on a specific driver's raw connection.

The production runtime does not migrate the database and rejects PostgreSQL superuser or BYPASSRLS roles. Run schema migration and tenant/workspace provisioning through the operator workflow, then give the runtime only the required data permissions. Browser sessions are tenant-wide records in that same database; selecting a workspace does not select a different identity.

API and source workers both call `runtime_config.open_workspace(settings)`. The v2 Pack binds the organization, quote object, Domain Pack digest and each resource's slot/object/domain/owner. It contains only initial facts. Subsequent observations enter `register_change` as explicit ChangeEvents. Production rejects v1 Packs before opening the database; v1 remains a readable historical fixture format through the same loader. A sealed Pack proves internal consistency, not the provenance of customer facts or organizational approval of the mapping.

## Local qualification and customer operation

The local `interactive`/`golden` journey exercises synthetic enterprise mapping, local AgentTeams
transport and explicitly scoped Skill qualification. Production rejects these fixture-backed
qualification and reset routes; enabling authentication does not turn the local mapping workflow
into an approved customer integration. Provision the sealed v2 Pack and organization-specific
source mappings through the operator workflow, then validate the actual identities and connectors.
See [enterprise pilot steps](ENTERPRISE-PILOT-RUNBOOK.md) and [source onboarding](DATAVERSE-SOURCE-ONBOARDING.md).

The shipped Dataverse target adapter is limited to `name` and `description` on one configured draft
Quote. It does not write calculated prices, line items, customers, orders or quote state. A confirmed
internal Rebase and its rollback affect internal governed versions; they do not imply an external
write or external compensation. Follow the target's separate [proposal, approval and reconciliation
contract](DATAVERSE-TARGET-OPERATIONS.md).

Set up an external bounded expiry sweep before accepting retained private task inputs. The
`ORGREBASE_PRIVATE_RETENTION_SECONDS` setting blocks expired reads but does not schedule physical
application-level deletion. [Private-input maintenance](PRIVATE-DATA-LIFECYCLE.md#run-the-expiry-sweep)
provides an administrator client and explains job monitoring and backup limits.

## Identity and membership

Requests use `Authorization: Bearer <access-token>`. The verifier uses PyJWT and cryptography, with RS256/ES256 signatures, an explicit key ID, exact issuer and audience, subject, issued-at and expiry checks, optional not-before validation, and zero expiry leeway. Explicit ID-token claims are rejected. Configure a distinct API audience at the IdP: absence of a `token_use` claim does not itself establish that a token is an access token.

The service does not accept roles, actor IDs, or tenant authority from request JSON or `X-OrgRebase-Actor` in production. An administrator maps the verified issuer/subject to an actor and roles in this file:

```json
{
  "tenant_id": "org:example",
  "members": [
    {"subject": "idp-stable-subject", "actor_id": "human:product-owner", "roles": ["approver"]},
    {"subject": "idp-workload-subject", "actor_id": "service:executor", "roles": ["executor"]}
  ]
}
```

Keep this file owned by the deployment administrator and read-only to the API process. Publish changes by atomically replacing the file. Duplicate JSON keys, unknown roles, duplicate members/roles, missing or malformed membership, and mismatched tenant IDs fail closed.

| Role | Permitted actions |
|---|---|
| reader | Tenant reads and exports |
| operator | Reads, exports, proposal and change registration; deletion of the caller's own retained task input |
| approver | Reads, exports, business approval |
| executor | Reads and applying an approved change |
| governor | Reads; Skill/experience/OAC qualification routes remain local-only |
| administrator | All listed actions, privacy expiry sweep and deletion-ledger maintenance |

These are tenant-wide permissions. Existing business owner checks and exact proposal/approval bindings still apply; an approver role cannot approve another owner's change. Task source text retains its additional exact-task-actor check. This is a bounded RBAC policy, not a general enterprise ABAC or relationship-authorization implementation.

Every private Workspace route, including reads and exports, is protected. Unknown private mutations require proposal permission rather than silently becoming public. Responses use `Cache-Control: no-store` and `Vary: Authorization`. A denied caller does not receive a private error payload or start a Workspace operation.

## Revocation and time

Membership is reloaded for every request. Approval persistence and applying an approved change also reauthorize after acquiring the database transaction, before business writes. The callback revalidates the token and membership and rejects a changed actor binding. This closes the observed request-to-lock wait window for local policy changes.

An IdP-revoked API JWT that remains cryptographically valid can remain usable until expiry unless membership is revoked locally. Browser sessions additionally support signed OIDC back-channel logout, described below. There is no IdP introspection or assumed immediate revocation of machine Bearer tokens. The maximum configured token lifetime bounds that exposure. JWKS data may be cached for 60 seconds; key rotation is not a substitute for subject/session revocation. The JWKS fetch is bounded, verifies TLS, ignores ambient proxies, and refuses redirects. There is no atomic transaction spanning the IdP, membership file and target system; changes after the last check require an explicitly stronger target-enforced authorization contract where the business requires it.

Production requires SystemClock. A new approval uses the observed UTC time and a 15-minute TTL. Apply checks `approved_at <= now < expires_at`, then checks again after acquiring the transaction. All writes in that commit use the same captured timestamp. Invalid or timezone-free approval timestamps are rejected. Local fixtures inject frozen inputs into the same workflow; historical evidence files are not rewritten.

## Browser and network entry

The production configuration exposes the Workspace API and opaque health/readiness probes. Without browser OIDC configuration it serves machine Bearer clients. Enabling the browser configuration also serves the application shell and its static assets; every private business request still needs the same verified Principal. Public competition evidence, demo actions, destructive reset routes and fixture-backed Skill/experience/OAC qualification routes remain unavailable. The local console's actor picker is not a login system.

`ORGREBASE_OAC_ADAPTATION_MODE=required` is rejected at production startup because that local qualification path depends on offline governance evidence. Removing its HTTP routes alone would leave an indirect entry through task Formation. A direct Formation request, when explicitly enabled by disabling task intake, must still bind the task actor to the verified caller.

The browser backend implements Authorization Code with PKCE S256 through Authlib. Register a confidential OIDC client at the issuer, with `client_secret_basic`, a distinct browser client ID and API audience, and exactly `https://your-service.example/api/session/callback` as redirect URI. The issuer must issue JWT access tokens for the configured API audience; opaque access tokens are not accepted by this resource API. Discovery must advertise code, S256 and the configured JWKS URI. Discovery and token HTTP clients verify TLS, refuse redirects and ignore ambient proxy configuration. [OIDC Core](https://openid.net/specs/openid-connect-core-1_0.html), [OAuth security best practice](https://www.rfc-editor.org/rfc/rfc9700.html), [Authlib HTTPX2 client](https://docs.authlib.org/en/stable/oauth2/client/http/httpx.html)

| Browser variable | Meaning |
|---|---|
| `ORGREBASE_OIDC_CLIENT_ID` | Enables the browser flow for this registered confidential client |
| `ORGREBASE_OIDC_CLIENT_SECRET_FILE` | UTF-8 client secret, owned by the service user, regular non-symlink file, mode 0600 |
| `ORGREBASE_SESSION_KEY_FILE` | Independent 32-byte random AES-GCM key encoded as URL-safe Base64; same file protections |
| `ORGREBASE_PUBLIC_ORIGIN` | Exact HTTPS origin, including any port, without path, query or fragment |
| `ORGREBASE_OIDC_SCOPES` | Space-separated registered scopes; includes `openid` and the issuer's API scope if required; `offline_access` is refused |
| `ORGREBASE_SESSION_SECONDS` | Maximum browser lifetime, default 900, range 30–3600 seconds; also capped by both token expiries |
| `ORGREBASE_OIDC_ENDPOINT_ORIGINS` | Optional explicit additional HTTPS origins permitted for discovered authorization/token endpoints; defaults to the issuer origin |

`GET /api/session` returns mode, the authenticated actor/tenant/roles, expiry, a CSRF token and fixed login/logout paths. No access token, client secret or encryption key is returned. `GET /api/session/login` starts the browser redirect; the callback verifies a single-use state, a separate HttpOnly browser-binding cookie, nonce, the ID-token signature, exact issuer/client audience/authorized party, optional access-token hash, and agreement with the already verified API token subject. The redirect destination after login is the configured origin, never a request parameter or forwarded host.

The application session cookie is opaque, Secure, HttpOnly, SameSite=Lax, Path=/ and has a `__Host-` name. Only its SHA-256 lookup digest is stored. Login verifiers and access tokens are encrypted with AES-GCM, with tenant, issuer, registered client ID, origin, record kind and lookup digest bound as authenticated data. Expired rows are denied on read and removed during new login preparation. Logout clears the ciphertext. Key-file replacement invalidates existing ciphertext and pending logins; all workers must receive the same replacement atomically and users must sign in again. A missing/invalid key fails closed; there is no unsigned-header fallback. The key is excluded from database backups. Recovery must erase restored sessions and pending logins before service resumes.

Cookie-authenticated POST/PUT/PATCH/DELETE requests require both `X-CSRF-Token` and an exact matching `Origin`. The token comes from the same-origin session response, never from a URL. Cross-site fetch metadata is rejected. A simultaneous Bearer header and session cookie is ambiguous and rejected. UI mutations are not automatically replayed after a 401. Machine Bearer clients continue to use the existing API authorization chain without cookie CSRF semantics. [OWASP CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)

`POST /api/session/logout` requires CSRF and immediately revokes this application's session across workers. It does not promise a global logout from every application at the issuer. Register `POST /api/session/backchannel-logout` if the IdP supports back-channel logout. The endpoint accepts a signed logout token for the exact issuer/client, with recent `iat`, `jti`, the logout event and `sid` and/or `sub`; nonce-bearing tokens and replayed events are refused. A session-specific notification revokes that sid, while a subject-only notification revokes the subject's sessions. Persistent fences also prevent a callback issued before the notification from creating a replacement session afterward. Without such a notification, IdP revocation remains bounded by the access-token/session lifetime and local membership checks. [OIDC back-channel logout](https://openid.net/specs/openid-connect-backchannel-1_0.html)

Session authentication runs the existing JWT verifier and reloads membership on each request and again at the business commit boundary. No refresh token is requested or stored; an expired session starts a new OIDC authorization flow. This bounds token retention and revocation exposure without introducing a second authorization policy.

Place the service behind TLS and a controlled ingress. The configured host allowlist checks host names, not caller identity. Health/readiness contain only opaque service status in production. API and target-write workers must receive different credentials; a source or model process must not inherit target credentials.

The browser service rejects requests whose actual scheme/authority differ from `ORGREBASE_PUBLIC_ORIGIN`. It never derives a callback from `Forwarded` or `X-Forwarded-Host`. If TLS terminates at a proxy, configure the ASGI server to trust forwarding information only from that proxy's exact network identity; never use a wildcard trust setting. Configure ingress access logs to omit query strings on the callback. The application also clears the callback query from the ASGI scope after parsing, applies no-store/referrer controls to identity responses, and does not echo provider errors or codes.

## Workspace selection

`ORGREBASE_WORKSPACE_CATALOG` selects a server-owned JSON catalog. Each entry refers to an already provisioned workspace in the same tenant database. Provision its exact profile, Pack digest and quote-object binding through the operator workflow before starting the API. A missing registration or mismatched binding fails startup. The HTTP API does not provision workspaces, accept database addresses, or accept Pack paths from the browser. Without a catalog, the existing single workspace remains available.

```json
{
  "schema_version": "orgrebase.workspace-catalog.v1",
  "workspaces": [
    {
      "workspace_id": "default",
      "label": "销售报价",
      "enterprise_pack": "packs/sales",
      "allowed_subjects": ["employee-subject", "source-worker-subject"],
      "effect_config": "targets/sales.json"
    },
    {
      "workspace_id": "renewals",
      "label": "续约报价",
      "enterprise_pack": "packs/renewals",
      "allowed_subjects": ["renewal-employee-subject"]
    }
  ]
}
```

Paths resolve relative to the catalog file. The catalog must be a regular non-symlink file without group/other write permissions; duplicate keys, duplicate workspace IDs, wildcard subjects and unrecognized fields are rejected. `allowed_subjects` contains exact verified OIDC subjects and does not grant extra roles; an empty list denies everyone. `ORGREBASE_WORKSPACE_ID` selects the startup/default workspace, default `default`. A configured catalog must contain that entry. An omitted `effect_config` makes external-effect operations unavailable for that workspace; it never inherits another workspace's target configuration.

`GET /api/workspaces` requires the same authenticated Principal in production and returns only `{workspace_id, label}` entries that the subject may access, plus `default_workspace_id`. A user with access only to a different workspace can still sign in and list it. Business requests select an entry with `X-OrgRebase-Workspace`; session endpoints and the workspace list remain tenant-wide. Unknown, duplicate-header and unauthorized selections return `AUTH_WORKSPACE_DENIED` after authentication. The state response includes `workspace_id`, and private responses vary on this header as well as Cookie and Authorization.

Every configured workspace runs the same `create_app` factory with its own service, progress, locks and lifecycle. An outer ASGI dispatcher selects these existing instances; it contains no business workflow. PostgreSQL connections bind to that workspace and use the database's row-security scope. Browser sessions remain shared across these instances, so one login can access permitted workspaces and logout revokes the session for all of them.

Catalog membership is reloaded on each request and at the final authorization check before commit. Formation registers that check with the owning StateStore transaction, so it also runs after later work in a borrowed transaction; a denial rolls back the entire transaction. Local-deterministic Formation also rechecks the original source lease at that boundary. Live and controlled AgentTeams modes retain their distinct time contracts. A revocation visible to the final check blocks the operation; this does not make an external identity provider or a catalog file atomically commit with the database. Reauthorization also refreshes the Principal used by typed actions, so retaining read permission after losing execution/approval permission cannot preserve a stale role. A running workspace refuses changed Pack or target-configuration paths until restart; adding entries also requires restart. Change catalog files atomically to avoid a transient unreadable policy denying requests. This is fail-closed behavior, not a live workspace-provisioning API.

Workers use `DeploymentSettings.for_workspace(id)` and `open_workspace(settings)` with the same catalog. They must call `settings.authorize_workspace(subject)` after JWT verification and on every dispatch/commit recheck; previously approved and delegated subjects also need current workspace membership. The browser selector is a request scope, never an authority assertion.

## Validation scope

`tests/test_identity_boundary.py` uses actual generated RSA keys and the real JWT verification code; JWKS transport is replaced by an in-process public-key source. Its production HTTP tests isolate the API boundary with a minimal Workspace stub; they do not simulate a real PostgreSQL deployment or claim an external IdP login.

`tests/test_runtime_clock.py` executes the real RebaseWorkflow and proves expiry, equality at issue time, invalid times, expiry while waiting for a transaction, and in-transaction authorization denial without writes. PostgreSQL integration and external effect recovery are validated separately.

`tests/test_identity_postgres.py` composes the production factory, actual PostgreSQL, actual RSA JWT verification and SystemClock with a v2 synthetic Pack. The JWKS transport and observed enterprise facts are controlled test inputs. Its acceptance path registers a new event, previews, approves as the mapped owner, applies, restarts and verifies persisted history; it does not establish a live external IdP or customer adoption.

`tests/test_browser_sessions.py` and `tests/browser_oidc_provider.py` exercise the real local HTTPS authorization redirect, PKCE token exchange, real signed ID/access tokens and network JWKS, encrypted server sessions, CSRF, logout/revocation and restart. This is an actual protocol implementation test against a controlled provider, not a claim that a customer's IdP configuration has been admitted. The session repository has separate one-time consumption and revocation-fence tests.

`tests/test_workspace_catalog.py` uses that same HTTPS provider and ordinary PostgreSQL runtime role for two provisioned workspaces: parallel Formation, separate events/exports, cross-workspace preview rejection, restart, shared-session logout, denied/default-free users, policy revocation at commit and configuration-rebinding refusal. `tests/test_browser_session_store.py` also races login against logout on independent PostgreSQL connections in both lock orders; neither order leaves an active revoked session.

Customer acceptance still needs the customer's issuer/audience and rotation/revocation behavior, service and human membership, controlled TLS ingress, dedicated database identity, actual target permissions, backup recovery and operational ownership. Do not replace those acceptance results with the local cryptographic tests.
