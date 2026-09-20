"""Validated deployment inputs for the existing application runtime."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg.conninfo import conninfo_to_dict

from orgrebase.auth import IdentitySettings
from orgrebase.browser_auth import BrowserSessionSettings
from orgrebase.local_role_session import LocalRoleSessionSettings
from orgrebase.workspace_catalog import load_catalog


def _validate_production_postgres_transport(database_url: str | None) -> None:
    try:
        if urlparse(database_url or "").scheme not in {"postgres", "postgresql"}:
            raise ValueError("PRODUCTION_POSTGRESQL_REQUIRED")
        parameters = conninfo_to_dict(database_url or "")
    except psycopg.Error:
        raise ValueError("PRODUCTION_POSTGRES_CONNECTION_INVALID") from None
    if parameters.get("service") or os.environ.get("PGSERVICE"):
        raise ValueError("PRODUCTION_POSTGRES_SERVICE_INDIRECTION_FORBIDDEN")
    if os.environ.get("PGHOSTADDR"):
        raise ValueError("PRODUCTION_POSTGRES_IMPLICIT_HOSTADDR_FORBIDDEN")
    hosts = str(parameters.get("host", "")).split(",")
    if any(not host.strip() for host in hosts):
        raise ValueError("PRODUCTION_POSTGRES_EXPLICIT_HOST_REQUIRED")
    addresses = str(parameters.get("hostaddr", "")).split(",") if parameters.get("hostaddr") else [""] * len(hosts)
    if len(addresses) != len(hosts):
        raise ValueError("PRODUCTION_POSTGRES_HOST_LIST_MISMATCH")
    local = True
    for host, address in zip(hosts, addresses, strict=True):
        if address:
            try:
                target = ip_address(address)
            except ValueError:
                raise ValueError("PRODUCTION_POSTGRES_HOSTADDR_INVALID") from None
        elif host.startswith("/"):
            continue
        else:
            try:
                target = ip_address(host)
            except ValueError:
                local = False
                continue
        if not (target.is_loopback or (getattr(target, "ipv4_mapped", None) is not None and target.ipv4_mapped.is_loopback)):
            local = False
    if not local and (parameters.get("sslmode") != "verify-full" or parameters.get("gssencmode") != "disable"):
        raise ValueError("PRODUCTION_POSTGRES_VERIFIED_TLS_REQUIRED")


@dataclass(frozen=True)
class DeploymentSettings:
    mode: str = "production"
    identity: IdentitySettings | None = None
    database_url: str | None = None
    enterprise_pack: str | None = None
    private_retention_seconds: int = 86_400
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "[::1]", "testserver")
    browser_session: BrowserSessionSettings | None = None
    workspace_id: str = "default"
    workspace_catalog: Path | None = None
    effect_config: Path | None = None
    source_config: Path | None = None
    owner_change_policy: str = "disabled"
    local_role_session: LocalRoleSessionSettings | None = None

    def __post_init__(self) -> None:
        if self.owner_change_policy not in {"disabled", "mutual-consent-v1"}:
            raise ValueError("OWNER_CHANGE_POLICY_INVALID")
        if not isinstance(self.workspace_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", self.workspace_id):
            raise ValueError("WORKSPACE_ID_INVALID")
        if isinstance(self.private_retention_seconds, bool) or not isinstance(self.private_retention_seconds, int) or not 0 <= self.private_retention_seconds <= 604_800:
            raise ValueError("PRIVATE_RETENTION_INVALID")
        if self.mode not in {"local", "production"}:
            raise ValueError("DEPLOYMENT_MODE_INVALID")
        if self.local_role_session is not None:
            if self.mode != "local" or self.identity is not None or self.browser_session is not None:
                raise ValueError("AUTH_LOCAL_SESSION_DEPLOYMENT_FORBIDDEN")
            if urlparse(self.local_role_session.public_origin).hostname not in {host.strip("[]") for host in self.allowed_hosts}:
                raise ValueError("AUTH_BROWSER_HOST_NOT_ALLOWED")
        if self.mode == "production":
            if self.identity is None:
                raise ValueError("PRODUCTION_IDENTITY_REQUIRED")
            _validate_production_postgres_transport(self.database_url)
            if not self.enterprise_pack and self.workspace_catalog is None:
                raise ValueError("PRODUCTION_ENTERPRISE_PACK_REQUIRED")
            if not self.allowed_hosts or any(host in {"*", "testserver"} for host in self.allowed_hosts):
                raise ValueError("PRODUCTION_ALLOWED_HOSTS_REQUIRED")
        if self.browser_session is not None:
            if self.identity is None:
                raise ValueError("AUTH_BROWSER_IDENTITY_REQUIRED")
            if urlparse(self.browser_session.public_origin).hostname not in self.allowed_hosts:
                raise ValueError("AUTH_BROWSER_HOST_NOT_ALLOWED")
        if self.workspace_catalog is not None and self.mode != "production":
            raise ValueError("WORKSPACE_CATALOG_PRODUCTION_REQUIRED")

    def for_workspace(self, workspace_id: str) -> DeploymentSettings:
        if self.workspace_catalog is None:
            if workspace_id != self.workspace_id:
                from orgrebase.auth import AuthenticationError
                raise AuthenticationError("AUTH_WORKSPACE_DENIED", 403)
            return self
        entry = load_catalog(self.workspace_catalog).entry(workspace_id)
        return replace(self, workspace_id=workspace_id, enterprise_pack=entry.enterprise_pack,
                       effect_config=Path(entry.effect_config) if entry.effect_config else None,
                       source_config=Path(entry.source_config) if entry.source_config else None)

    def authorize_workspace(self, subject: str) -> None:
        if self.workspace_catalog is None:
            return
        from orgrebase.auth import AuthenticationError

        entry = load_catalog(self.workspace_catalog).entry(self.workspace_id)
        if subject not in entry.allowed_subjects:
            raise AuthenticationError("AUTH_WORKSPACE_DENIED", 403)
        if (entry.enterprise_pack != self.enterprise_pack
                or entry.effect_config != (str(self.effect_config) if self.effect_config else None)
                or entry.source_config != (str(self.source_config) if self.source_config else None)):
            raise AuthenticationError("AUTH_WORKSPACE_CONFIGURATION_CHANGED", 503)

    @classmethod
    def from_environment(cls) -> DeploymentSettings:
        mode = os.environ.get("ORGREBASE_DEPLOYMENT_MODE", "production").strip()
        retention = int(os.environ.get("ORGREBASE_PRIVATE_RETENTION_SECONDS", "86400"))
        owner_policy = os.environ.get("ORGREBASE_OWNER_CHANGE_POLICY", "disabled").strip()
        local_origin = os.environ.get("ORGREBASE_LOCAL_ROLE_SESSION_ORIGIN", "").strip()
        if local_origin and mode != "local":
            raise ValueError("AUTH_LOCAL_SESSION_DEPLOYMENT_FORBIDDEN")
        if mode != "production":
            return cls(mode=mode, private_retention_seconds=retention, owner_change_policy=owner_policy,
                       local_role_session=LocalRoleSessionSettings(local_origin) if local_origin else None)
        identity = IdentitySettings(
            issuer=os.environ.get("ORGREBASE_AUTH_ISSUER", ""),
            audience=os.environ.get("ORGREBASE_AUTH_AUDIENCE", ""),
            jwks_url=os.environ.get("ORGREBASE_AUTH_JWKS_URL", ""),
            tenant_id=os.environ.get("ORGREBASE_TENANT_ID", ""),
            membership_file=Path(os.environ.get("ORGREBASE_AUTH_MEMBERSHIP_FILE", "")),
            max_token_lifetime_seconds=int(os.environ.get("ORGREBASE_AUTH_MAX_TOKEN_SECONDS", "900")),
            ca_bundle=Path(os.environ["ORGREBASE_AUTH_CA_BUNDLE"]) if os.environ.get("ORGREBASE_AUTH_CA_BUNDLE") else None,
        )
        browser_session = None
        if os.environ.get("ORGREBASE_OIDC_CLIENT_ID"):
            browser_session = BrowserSessionSettings(
                client_id=os.environ["ORGREBASE_OIDC_CLIENT_ID"],
                client_secret_file=Path(os.environ.get("ORGREBASE_OIDC_CLIENT_SECRET_FILE", "")),
                encryption_key_file=Path(os.environ.get("ORGREBASE_SESSION_KEY_FILE", "")),
                public_origin=os.environ.get("ORGREBASE_PUBLIC_ORIGIN", ""),
                scopes=tuple(os.environ.get("ORGREBASE_OIDC_SCOPES", "openid").split()),
                session_seconds=int(os.environ.get("ORGREBASE_SESSION_SECONDS", "900")),
                trusted_endpoint_origins=tuple(value.strip() for value in os.environ.get("ORGREBASE_OIDC_ENDPOINT_ORIGINS", "").split(",") if value.strip()),
            )
        return cls(
            mode=mode, identity=identity, private_retention_seconds=retention, owner_change_policy=owner_policy,
            database_url=os.environ.get("ORGREBASE_WORKSPACE_DB", ""),
            enterprise_pack=os.environ.get("ORGREBASE_ENTERPRISE_PACK", ""),
            allowed_hosts=tuple(host.strip() for host in os.environ.get("ORGREBASE_ALLOWED_HOSTS", "").split(",") if host.strip()),
            browser_session=browser_session,
            workspace_id=os.environ.get("ORGREBASE_WORKSPACE_ID", "default"),
            workspace_catalog=Path(os.environ["ORGREBASE_WORKSPACE_CATALOG"]) if os.environ.get("ORGREBASE_WORKSPACE_CATALOG") else None,
            effect_config=Path(os.environ["ORGREBASE_EFFECT_CONFIG"]) if os.environ.get("ORGREBASE_EFFECT_CONFIG") else None,
            source_config=Path(os.environ["ORGREBASE_SOURCE_CONFIG"]) if os.environ.get("ORGREBASE_SOURCE_CONFIG") else None,
        )


def workspace_database_path(settings: DeploymentSettings) -> str:
    configured = settings.database_url or os.environ.get("ORGREBASE_WORKSPACE_DB", "").strip()
    root = Path(__file__).resolve().parents[2]
    root = root if (root / "pyproject.toml").is_file() else Path.cwd()
    selected = configured or str(root / ".orgrebase" / "workspace.sqlite3")
    if selected == ":memory:" or selected.startswith(("postgresql://", "postgres://")):
        return selected
    path = Path(selected).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def validate_deployment_environment(settings: DeploymentSettings) -> None:
    if settings.mode == "production":
        _validate_production_postgres_transport(settings.database_url)
    if settings.mode == "production" and os.environ.get("ORGREBASE_OAC_ADAPTATION_MODE", "optional").strip().lower() not in {"off", "optional"}:
        raise ValueError("PRODUCTION_OFFLINE_GOVERNANCE_FORBIDDEN")


def validate_workspace(workspace, settings: DeploymentSettings) -> None:
    from orgrebase.clock import SystemClock

    if settings.mode != "production":
        return
    if workspace.profile.organization_id != settings.identity.tenant_id:
        raise ValueError("PRODUCTION_TENANT_PROFILE_MISMATCH")
    if not isinstance(workspace.clock, SystemClock):
        raise ValueError("PRODUCTION_SYSTEM_CLOCK_REQUIRED")
    if workspace.approval_identity_mode != "VERIFIED_PRINCIPAL_IDENTITY":
        raise ValueError("PRODUCTION_VERIFIED_IDENTITY_REQUIRED")
    if (
        workspace.runtime_configuration is None or workspace.competition_mode != "off"
        or getattr(workspace.runtime_configuration, "schema_version", None) != "orgrebase.enterprise-quote-pilot-pack.v2"
        or getattr(workspace.runtime_configuration, "enterprise_binding", None) is None
        or getattr(getattr(workspace.profile, "runtime_compatibility", None), "handler_profile", None)
        == "northstar-acme-quote-v1"
    ):
        raise ValueError("PRODUCTION_ADMITTED_PACK_REQUIRED")
    health = workspace.store.check_health()
    if health["backend"] != "postgresql" or health["tenant_id"] != settings.identity.tenant_id:
        raise ValueError("PRODUCTION_TENANT_DATABASE_REQUIRED")
    if health.get("runtime_role_safe") is not True:
        raise ValueError("PRODUCTION_RESTRICTED_DATABASE_ROLE_REQUIRED")
    if health.get("workspace_id", "default") != settings.workspace_id:
        raise ValueError("PRODUCTION_WORKSPACE_DATABASE_MISMATCH")


def configure_workspace_identity(workspace, settings: DeploymentSettings, authenticator) -> None:
    workspace.effect_config_path = settings.effect_config
    workspace.source_config_path = settings.source_config
    workspace.owner_change_policy = settings.owner_change_policy
    if authenticator is not None:
        workspace.verify_membership = authenticator.verify_membership
        workspace.members_for_action = authenticator.members_for_action
        workspace.identity_issuer = settings.identity.issuer
        workspace.authorize_workspace_subject = settings.authorize_workspace


def open_workspace(settings: DeploymentSettings):
    """Open the single application service for API and authenticated workers."""
    import math

    from orgrebase.auth import CONTROLLED_LOCAL_SESSION_IDENTITY, JWTAuthenticator
    from orgrebase.clock import SystemClock
    from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
    from orgrebase.workspace.service import CONTROLLED_LOCAL_HEADER_IDENTITY, WorkspaceService

    settings = settings.for_workspace(settings.workspace_id)
    production = settings.mode == "production"
    validate_deployment_environment(settings)
    authenticator = JWTAuthenticator(settings.identity) if production else None
    try:
        review_duration = float(os.environ.get("ORGREBASE_WORKSPACE_REVIEW_SECONDS", "4").strip() or "4")
    except ValueError as exc:
        raise RuntimeError("ORGREBASE_WORKSPACE_REVIEW_SECONDS_INVALID") from exc
    if not math.isfinite(review_duration) or review_duration < 4:
        raise RuntimeError("ORGREBASE_WORKSPACE_REVIEW_SECONDS_UNDER_FOUR")
    task_intake = os.environ.get("ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED", "1").strip()
    if task_intake not in {"0", "1"}:
        raise RuntimeError("ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED_INVALID")
    pack_root = settings.enterprise_pack or os.environ.get("ORGREBASE_ENTERPRISE_PACK", "").strip()
    runtime = load_enterprise_quote_pilot_pack(pack_root) if pack_root else None
    if production and (runtime is None or runtime.schema_version != "orgrebase.enterprise-quote-pilot-pack.v2"
                       or runtime.enterprise_binding is None):
        raise ValueError("PRODUCTION_ADMITTED_PACK_REQUIRED")
    if production and runtime.profile.organization_id != settings.identity.tenant_id:
        raise ValueError("PRODUCTION_TENANT_PROFILE_MISMATCH")
    options = {"clock": SystemClock(), "store_tenant_id": settings.identity.tenant_id, "store_migrate": False} if production else {}
    workspace = WorkspaceService(
        store_path=workspace_database_path(settings),
        runtime_configuration=runtime,
        approval_identity_mode=("VERIFIED_PRINCIPAL_IDENTITY" if production else
                               CONTROLLED_LOCAL_SESSION_IDENTITY if settings.local_role_session else os.environ.get(
            "ORGREBASE_WORKSPACE_IDENTITY_MODE", CONTROLLED_LOCAL_HEADER_IDENTITY,
        ).strip()),
        review_duration_seconds=review_duration, task_intake_required=task_intake == "1",
        private_retention_seconds=settings.private_retention_seconds,
        workspace_id=settings.workspace_id,
        owner_change_policy=settings.owner_change_policy,
        **options,
    )
    try:
        validate_workspace(workspace, settings)
        configure_workspace_identity(workspace, settings, authenticator)
    except BaseException:
        workspace.close()
        raise
    return workspace
