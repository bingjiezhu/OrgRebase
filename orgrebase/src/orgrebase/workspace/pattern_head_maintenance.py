"""Authenticated, explicitly fenced maintenance for the S05 Pattern head.

The database fence verifies a named deployment inventory. Operators must
keep every legacy writer role disabled until all old processes are retired;
this command cannot discover writers using undeclared alternate credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orgrebase.auth import (
    AuthenticationError,
    JWTAuthenticator,
    authorize,
    request_authorization,
    request_principal,
)
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.runtime_config import DeploymentSettings, open_workspace
from orgrebase.store import StateStore
from orgrebase.workspace.pattern_evolution import GovernedPatternService
from orgrebase.workspace.pattern_governance import (
    PatternAuthorityScope,
    PrincipalPatternGovernance,
)

_DIGEST = r"^sha256:[0-9a-f]{64}$"
_ROLE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class PatternHeadMaintenanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["orgrebase.pattern-head-maintenance.v1"]
    enabled: bool
    action: Literal["MIGRATE", "ROLLBACK", "INSPECT"]
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    access_token_variable: str = Field(pattern=r"^[A-Z_][A-Z0-9_]{0,127}$")
    maintenance_window_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    legacy_writer_roles: tuple[str, ...]
    corpus_actor_id: str = Field(min_length=1, max_length=256)
    author_actor_id: str = Field(min_length=1, max_length=256)
    evaluator_actor_id: str = Field(min_length=1, max_length=256)
    governor_actor_id: str = Field(min_length=1, max_length=256)
    reviewed_bundle_digest: str = Field(pattern=_DIGEST)
    reviewed_target_skill: Literal["structured-domain-handoff"]
    reviewed_predecessor_package_digest: str = Field(pattern=_DIGEST)
    expected_source_ref: str | None = None
    expected_source_digest: str | None = None
    expected_head_ref: str | None = None
    expected_head_digest: str | None = None
    expected_generation: int | None = Field(default=None, ge=1)
    expected_package_digest: str = Field(pattern=_DIGEST)
    expected_previous_ref: str | None = None
    expected_previous_digest: str | None = None
    expected_transition_kind: Literal["MIGRATE", "ROLLBACK"] | None = None
    original_mutation_config_path: str | None = None
    expected_mutation_config_digest: str | None = Field(default=None, pattern=_DIGEST)
    reason: str | None = None

    @model_validator(mode="after")
    def validate_action_and_fence(self):
        if (
            not self.enabled
            or not self.legacy_writer_roles
            or len(self.legacy_writer_roles) != len(set(self.legacy_writer_roles))
            or any(_ROLE.fullmatch(role) is None for role in self.legacy_writer_roles)
        ):
            raise ValueError("PATTERN_MAINTENANCE_CONFIG_FENCE_INVALID")
        source_set = self.expected_source_ref is not None and self.expected_source_digest is not None
        head_set = (
            self.expected_head_ref is not None
            and self.expected_head_digest is not None
            and self.expected_generation is not None
        )
        if self.action == "MIGRATE":
            if (
                not source_set or self.expected_head_ref is not None
                or self.expected_head_digest is not None
                or self.expected_generation is not None
                or self.expected_previous_ref is not None
                or self.expected_previous_digest is not None
                or self.expected_transition_kind is not None
                or self.original_mutation_config_path is not None
                or self.expected_mutation_config_digest is not None
                or self.reason is not None
            ):
                raise ValueError("PATTERN_MAINTENANCE_CONFIG_MIGRATE_INVALID")
            if re.fullmatch(
                r"admitted-pattern-skill:[0-9a-f]{64}@1", self.expected_source_ref
            ) is None:
                raise ValueError("PATTERN_MAINTENANCE_CONFIG_MIGRATE_INVALID")
            if re.fullmatch(_DIGEST, self.expected_source_digest) is None:
                raise ValueError("PATTERN_MAINTENANCE_CONFIG_MIGRATE_INVALID")
        elif self.action == "ROLLBACK":
            if (
                self.expected_source_ref is not None
                or self.expected_source_digest is not None
                or not head_set
                or self.expected_previous_ref is not None
                or self.expected_previous_digest is not None
                or self.expected_transition_kind is not None
                or self.original_mutation_config_path is not None
                or self.expected_mutation_config_digest is not None
            ):
                raise ValueError("PATTERN_MAINTENANCE_CONFIG_HEAD_INVALID")
            if re.fullmatch(
                r"pattern-skill-head:[0-9a-f]{64}@g[0-9]{8}", self.expected_head_ref
            ) is None:
                raise ValueError("PATTERN_MAINTENANCE_CONFIG_HEAD_INVALID")
            if re.fullmatch(_DIGEST, self.expected_head_digest) is None:
                raise ValueError("PATTERN_MAINTENANCE_CONFIG_HEAD_INVALID")
            if not self.reason or not self.reason.strip():
                raise ValueError("PATTERN_MAINTENANCE_CONFIG_REASON_REQUIRED")
        else:
            if (
                self.expected_source_ref is not None
                or self.expected_source_digest is not None
                or self.expected_head_ref is not None
                or self.expected_head_digest is not None
                or self.expected_generation is None
                or not isinstance(self.expected_previous_ref, str)
                or not self.expected_previous_ref
                or re.fullmatch(_DIGEST, self.expected_previous_digest or "") is None
                or self.expected_transition_kind is None
                or not isinstance(self.original_mutation_config_path, str)
                or not Path(self.original_mutation_config_path).is_absolute()
                or re.fullmatch(_DIGEST, self.expected_mutation_config_digest or "") is None
                or self.reason is not None
            ):
                raise ValueError("PATTERN_MAINTENANCE_CONFIG_INSPECT_INVALID")
        if (
            self.expected_source_ref is not None
            and self.expected_head_ref is not None
        ) or (
            (self.expected_source_ref is None) != (self.expected_source_digest is None)
        ) or (
            (self.expected_head_ref is None) != (self.expected_head_digest is None)
        ):
            raise ValueError("PATTERN_MAINTENANCE_CONFIG_EXACT_BASE_INVALID")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("PATTERN_MAINTENANCE_CONFIG_DUPLICATE_KEY")
        result[key] = value
    return result


def load_pattern_head_maintenance_config(path: Path) -> PatternHeadMaintenanceConfig:
    """Read a regular owner-controlled config without following a symlink."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid not in {os.getuid(), 0}
                or info.st_mode & 0o022
                or info.st_size > 65_536
            ):
                raise ValueError("PATTERN_MAINTENANCE_CONFIG_INVALID")
            raw = stream.read(65_537)
        return PatternHeadMaintenanceConfig.model_validate(
            json.loads(raw, object_pairs_hook=_unique_object)
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("PATTERN_MAINTENANCE_"):
            raise
        raise ValueError("PATTERN_MAINTENANCE_CONFIG_INVALID") from exc


def _original_mutation_for_inspect(
    inspect: PatternHeadMaintenanceConfig,
) -> PatternHeadMaintenanceConfig:
    assert inspect.action == "INSPECT" and inspect.original_mutation_config_path is not None
    original = load_pattern_head_maintenance_config(Path(inspect.original_mutation_config_path))
    shared = (
        "workspace_id", "maintenance_window_id", "legacy_writer_roles",
        "corpus_actor_id", "author_actor_id", "evaluator_actor_id",
        "governor_actor_id", "reviewed_bundle_digest",
        "reviewed_target_skill", "reviewed_predecessor_package_digest",
    )
    if (
        original.digest != inspect.expected_mutation_config_digest
        or original.action != inspect.expected_transition_kind
        or any(getattr(original, field) != getattr(inspect, field) for field in shared)
    ):
        raise IntegrityError("PATTERN_MAINTENANCE_ORIGINAL_COMMAND_MISMATCH")
    if original.action == "MIGRATE":
        if (
            inspect.expected_previous_ref != original.expected_source_ref
            or inspect.expected_previous_digest != original.expected_source_digest
            or inspect.expected_generation != 1
            or inspect.expected_package_digest != original.expected_package_digest
        ):
            raise IntegrityError("PATTERN_MAINTENANCE_ORIGINAL_COMMAND_MISMATCH")
    elif original.action == "ROLLBACK":
        if (
            inspect.expected_previous_ref != original.expected_head_ref
            or inspect.expected_previous_digest != original.expected_head_digest
            or inspect.expected_generation != original.expected_generation + 1
        ):
            raise IntegrityError("PATTERN_MAINTENANCE_ORIGINAL_COMMAND_MISMATCH")
    else:
        raise IntegrityError("PATTERN_MAINTENANCE_ORIGINAL_COMMAND_MISMATCH")
    return original


class PostgresLegacyWriterFence:
    """Verify named old DB login roles are fenced and have zero live sessions."""

    def __init__(self, store: StateStore, roles: tuple[str, ...]) -> None:
        if store.backend != "postgresql" or not roles:
            raise IntegrityError("PATTERN_MAINTENANCE_POSTGRES_REQUIRED")
        self.store = store
        self.roles = roles
        self._role_oids: dict[str, int] | None = None

    def window_digest(self, *, window_id: str, config_digest: str) -> str:
        if self._role_oids is None:
            raise IntegrityError("PATTERN_MAINTENANCE_FENCE_NOT_VERIFIED")
        return sha256_digest({
            "schema_version": "orgrebase.pattern-maintenance-fence.v2",
            "window_id": window_id,
            "config_digest": config_digest,
            "legacy_role_oids": self._role_oids,
            "policy": "POSTGRES_NOLOGIN_ZERO_ALL_OTHER_CURRENT_DB_CLIENT_BACKENDS",
        })

    def verify(self) -> None:
        connection = self.store.connection
        watcher = connection.execute(
            """SELECT current_user, r.rolsuper,
                      pg_has_role(current_user, 'pg_read_all_stats', 'USAGE')
               FROM pg_roles r WHERE r.rolname = current_user"""
        ).fetchone()
        if watcher is None or not (watcher[1] or watcher[2]):
            raise IntegrityError("PATTERN_MAINTENANCE_SESSION_VISIBILITY_REQUIRED")
        if watcher[0] in self.roles:
            raise IntegrityError("PATTERN_MAINTENANCE_WRITER_ROLE_REUSED")
        rows = connection.execute(
            "SELECT oid, rolname, rolcanlogin FROM pg_roles WHERE rolname = ANY(%s)",
            (list(self.roles),),
        ).fetchall()
        actual = {str(row[1]): (int(row[0]), bool(row[2])) for row in rows}
        if set(actual) != set(self.roles) or any(login for _, login in actual.values()):
            raise IntegrityError("PATTERN_MAINTENANCE_LEGACY_ROLE_NOT_DISABLED")
        role_oids = {name: oid for name, (oid, _) in actual.items()}
        if self._role_oids is not None and self._role_oids != role_oids:
            raise IntegrityError("PATTERN_MAINTENANCE_ROLE_IDENTITY_CHANGED")
        members = connection.execute(
            "SELECT count(*) FROM pg_auth_members WHERE roleid = ANY(%s)",
            (list(role_oids.values()),),
        ).fetchone()
        if members is None or int(members[0]) != 0:
            raise IntegrityError("PATTERN_MAINTENANCE_LEGACY_ROLE_INHERITABLE")
        active = connection.execute(
            """SELECT count(*) FROM pg_stat_activity
               WHERE datname = current_database()
                 AND backend_type = 'client backend'
                 AND usename = ANY(%s)""",
            (list(self.roles),),
        ).fetchone()
        if active is None or int(active[0]) != 0:
            raise IntegrityError("PATTERN_MAINTENANCE_LEGACY_SESSION_ACTIVE")
        prepared = connection.execute(
            """SELECT count(*) FROM pg_prepared_xacts
               WHERE database = current_database() AND owner = ANY(%s)""",
            (list(self.roles),),
        ).fetchone()
        if prepared is None or int(prepared[0]) != 0:
            raise IntegrityError("PATTERN_MAINTENANCE_LEGACY_PREPARED_XACT_ACTIVE")
        other_clients = connection.execute(
            """SELECT count(*) FROM pg_stat_activity
               WHERE datname = current_database()
                 AND backend_type = 'client backend'
                 AND pid <> pg_backend_pid()"""
        ).fetchone()
        if other_clients is None or int(other_clients[0]) != 0:
            raise IntegrityError("PATTERN_MAINTENANCE_OTHER_CLIENT_BACKEND_ACTIVE")
        other_prepared = connection.execute(
            "SELECT count(*) FROM pg_prepared_xacts WHERE database = current_database()"
        ).fetchone()
        if other_prepared is None or int(other_prepared[0]) != 0:
            raise IntegrityError("PATTERN_MAINTENANCE_OTHER_PREPARED_XACT_ACTIVE")
        self._role_oids = role_oids


def run_pattern_head_maintenance(config_path: Path) -> dict[str, Any]:
    """Apply one exact command, or inspect the post-commit head after lost reply."""
    config = load_pattern_head_maintenance_config(config_path)
    settings = DeploymentSettings.from_environment()
    if settings.mode != "production" or settings.identity is None:
        raise ValueError("PATTERN_MAINTENANCE_PRODUCTION_IDENTITY_REQUIRED")
    settings = settings.for_workspace(config.workspace_id)
    authenticator = JWTAuthenticator(settings.identity)
    principal = authenticator.authenticate(
        f"Bearer {os.environ.get(config.access_token_variable, '')}"
    )
    authorize(principal, "govern", settings.identity.tenant_id)
    settings.authorize_workspace(principal.subject)
    if principal.actor_id != config.governor_actor_id:
        raise AuthorizationError("PATTERN_PRINCIPAL_ROLE_SEPARATION_REQUIRED")

    def check_authorization() -> None:
        renewed = authenticator.authenticate(
            f"Bearer {os.environ.get(config.access_token_variable, '')}"
        )
        if (
            renewed.issuer, renewed.subject, renewed.tenant_id, renewed.actor_id
        ) != (
            principal.issuer, principal.subject, principal.tenant_id, principal.actor_id
        ):
            raise AuthenticationError("PATTERN_MAINTENANCE_IDENTITY_CHANGED", 403)
        authorize(renewed, "govern", settings.identity.tenant_id)
        settings.authorize_workspace(renewed.subject)
        if load_pattern_head_maintenance_config(config_path).digest != config.digest:
            raise ValueError("PATTERN_MAINTENANCE_CONFIG_CHANGED")
        request_principal.set(renewed)

    workspace = open_workspace(settings)
    authorization_token = request_authorization.set(check_authorization)
    principal_token = request_principal.set(principal)
    try:
        service = GovernedPatternService(
            workspace.store,
            corpus_authority=config.corpus_actor_id,
            evaluator_authority=config.evaluator_actor_id,
            governance_authority=config.governor_actor_id,
        )
        controller = PrincipalPatternGovernance(
            service,
            PatternAuthorityScope(
                tenant_id=settings.identity.tenant_id,
                workspace_id=config.workspace_id,
                corpus_actor_id=config.corpus_actor_id,
                author_actor_id=config.author_actor_id,
                evaluator_actor_id=config.evaluator_actor_id,
                governor_actor_id=config.governor_actor_id,
                reviewed_bundle_digest=config.reviewed_bundle_digest,
                reviewed_target_skill=config.reviewed_target_skill,
                reviewed_predecessor_package_digest=(
                    config.reviewed_predecessor_package_digest
                ),
            ),
        )
        fence = PostgresLegacyWriterFence(workspace.store, config.legacy_writer_roles)

        def qualified_window() -> None:
            check_authorization()
            fence.verify()

        window_digest = None
        original_mutation = None
        if config.action == "MIGRATE":
            qualified_window()
            window_digest = fence.window_digest(
                window_id=config.maintenance_window_id, config_digest=config.digest
            )
            receipt_ref = controller.migrate_legacy_head(
                expected_source_ref=config.expected_source_ref,
                expected_source_digest=config.expected_source_digest,
                expected_package_digest=config.expected_package_digest,
                maintenance_fence=qualified_window,
                maintenance_window_digest=window_digest,
            )
        elif config.action == "ROLLBACK":
            qualified_window()
            window_digest = fence.window_digest(
                window_id=config.maintenance_window_id, config_digest=config.digest
            )
            receipt_ref = controller.restore_direct_parent(
                reason=config.reason,
                expected_head_ref=config.expected_head_ref,
                expected_head_digest=config.expected_head_digest,
                expected_generation=config.expected_generation,
                expected_package_digest=config.expected_package_digest,
                qualification_check=qualified_window,
                maintenance_window_digest=window_digest,
            )
        else:
            original_mutation = _original_mutation_for_inspect(config)
            qualified_window()
            window_digest = fence.window_digest(
                window_id=original_mutation.maintenance_window_id,
                config_digest=original_mutation.digest,
            )
            receipt_ref = None
        head = service._current_stable_head(config.reviewed_target_skill)
        if head is None:
            raise IntegrityError("PATTERN_MAINTENANCE_HEAD_MISSING")
        if config.action == "INSPECT" and (
            head.payload["generation"] != config.expected_generation
            or head.payload["package_digest"] != config.expected_package_digest
            or head.payload["previous_head_ref"] != config.expected_previous_ref
            or head.payload["previous_head_digest"] != config.expected_previous_digest
            or head.payload["transition_kind"] != config.expected_transition_kind
            or head.payload["adoption_enabled"] is not False
        ):
            raise IntegrityError("PATTERN_MAINTENANCE_INSPECT_HEAD_MISMATCH")
        if original_mutation is not None:
            if (
                load_pattern_head_maintenance_config(
                    Path(config.original_mutation_config_path)
                ).digest != original_mutation.digest
            ):
                raise IntegrityError("PATTERN_MAINTENANCE_ORIGINAL_COMMAND_CHANGED")
            if original_mutation.action == "MIGRATE":
                principal_binding = head.payload.get("principal_binding")
                if (
                    head.payload.get("maintenance_window_digest") != window_digest
                    or head.payload.get("migration_receipt") != "LEGACY_V1_NO_GENERATION"
                    or not isinstance(principal_binding, dict)
                    or principal_binding.get("actor_id") != original_mutation.governor_actor_id
                    or head.payload.get("effective_version_ref")
                    != original_mutation.expected_source_ref
                    or head.payload.get("effective_version_digest")
                    != original_mutation.expected_source_digest
                ):
                    raise IntegrityError("PATTERN_MAINTENANCE_INSPECT_MUTATION_MISMATCH")
                receipt_ref = head.ref
            else:
                restoration_ref = head.payload.get("restoration_ref")
                if not isinstance(restoration_ref, str):
                    raise IntegrityError("PATTERN_MAINTENANCE_INSPECT_RESTORATION_REQUIRED")
                restoration = service._load(restoration_ref, "restoration")
                principal_binding = restoration.get("principal_binding")
                previous_id, previous_version = original_mutation.expected_head_ref.rsplit("@", 1)
                previous = workspace.store.get_object(previous_id, previous_version)
                if (
                    restoration.get("maintenance_window_digest") != window_digest
                    or restoration.get("withdrawn_head_ref")
                    != original_mutation.expected_head_ref
                    or restoration.get("withdrawn_head_digest")
                    != original_mutation.expected_head_digest
                    or restoration.get("actor_id") != original_mutation.governor_actor_id
                    or not isinstance(principal_binding, dict)
                    or principal_binding.get("actor_id") != original_mutation.governor_actor_id
                    or restoration.get("reason") != original_mutation.reason
                    or restoration.get("adoption_enabled") is not False
                    or restoration.get("predecessor_version_ref")
                    != head.payload.get("effective_version_ref")
                    or restoration.get("predecessor_version_digest")
                    != head.payload.get("effective_version_digest")
                    or restoration.get("predecessor_package_digest")
                    != head.payload.get("package_digest")
                    or restoration_ref not in head.source_refs
                    or previous.digest != original_mutation.expected_head_digest
                    or previous.payload.get("package_digest")
                    != original_mutation.expected_package_digest
                ):
                    raise IntegrityError("PATTERN_MAINTENANCE_INSPECT_MUTATION_MISMATCH")
                receipt_ref = restoration_ref
        return {
            "schema_version": "orgrebase.pattern-head-maintenance-result.v1",
            "action": config.action,
            "workspace_id": config.workspace_id,
            "maintenance_window_id": config.maintenance_window_id,
            "config_digest": config.digest,
            "mutation_config_digest": (
                original_mutation.digest if original_mutation is not None else config.digest
            ),
            "maintenance_window_digest": window_digest,
            "head_ref": head.ref,
            "head_digest": head.digest,
            "generation": head.payload["generation"],
            "package_digest": head.payload["package_digest"],
            "transition_kind": head.payload["transition_kind"],
            "qualification_status": head.payload["qualification_status"],
            "adoption_enabled": head.payload["adoption_enabled"],
            "receipt_ref": receipt_ref,
            "identity_subject_digest": sha256_digest(principal.subject),
        }
    finally:
        request_principal.reset(principal_token)
        request_authorization.reset(authorization_token)
        workspace.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Maintain one exact Pattern head")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--show-config-identity", action="store_true",
        help="Read only: print this owner's config digest before a mutation",
    )
    args = parser.parse_args(argv)
    if args.show_config_identity:
        config = load_pattern_head_maintenance_config(args.config)
        print(json.dumps({
            "schema_version": "orgrebase.pattern-head-maintenance-identity.v1",
            "action": config.action,
            "workspace_id": config.workspace_id,
            "maintenance_window_id": config.maintenance_window_id,
            "config_digest": config.digest,
            "database_fence_verified": False,
            "mutation_executed": False,
        }, ensure_ascii=False, sort_keys=True))
        return 0
    print(json.dumps(run_pattern_head_maintenance(args.config), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - script wrapper calls main
    raise SystemExit(main())
