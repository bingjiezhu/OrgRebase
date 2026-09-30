"""Conservative human-review notification intents and delivery receipts.

This module never chooses recipients from model output and never treats a
transport acknowledgement as approval.  The canonical work item remains the
authority source; a link always returns to the authenticated application.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orgrebase.auth import AuthenticationError
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.change_proposals import change_detail, require_action

_MEDIA = "application/vnd.orgrebase.change-notification+json"
_MESSAGE_CONTRACT = "orgrebase.review-required-message.v1"
_SAFE_TRANSPORT_REASON_CODES = frozenset(
    {
        "NOTIFICATION_TRANSPORT_TIMEOUT",
        "NOTIFICATION_TRANSPORT_UNAVAILABLE",
        "NOTIFICATION_TRANSPORT_RESULT_UNKNOWN",
    }
)


class NotificationRecipientBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    actor_id: str = Field(min_length=1, max_length=256)
    recipient_ref: str = Field(
        min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]*$"
    )


class NotificationChannelAuthorization(BaseModel):
    """Deployment-owned authorization for one external notification channel."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["orgrebase.notification-channel.v1"]
    enabled: bool = False
    channel_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    transport_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    reauthentication_origin: str = Field(min_length=1, max_length=512)
    recipients: tuple[NotificationRecipientBinding, ...] = Field(
        min_length=1, max_length=10_000
    )
    send_timeout_seconds: int = Field(default=10, ge=1, le=30)
    unknown_after_seconds: int = Field(default=30, ge=5, le=300)

    @model_validator(mode="after")
    def validate_authorization(self) -> NotificationChannelAuthorization:
        parsed = urlsplit(self.reauthentication_origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("NOTIFICATION_REAUTHENTICATION_ORIGIN_INVALID")
        actors = [item.actor_id for item in self.recipients]
        refs = [item.recipient_ref for item in self.recipients]
        if len(actors) != len(set(actors)) or len(refs) != len(set(refs)):
            raise ValueError("NOTIFICATION_RECIPIENT_BINDING_DUPLICATE")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))

    def recipient_for(self, actor_id: str) -> str:
        match = next((item for item in self.recipients if item.actor_id == actor_id), None)
        if match is None:
            raise IntegrityError("NOTIFICATION_RECIPIENT_NOT_AUTHORIZED")
        return match.recipient_ref


class NotificationTransportReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    dispatch_state: Literal["SENT", "SENT_UNKNOWN", "NOT_SENT"]
    provider_notification_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )


class NotificationQueryReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    delivery_state: Literal["SENT", "SENT_UNKNOWN", "DELIVERED", "READ"]
    provider_notification_id: str = Field(
        min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    )


class NotificationTransport(Protocol):
    """Authorized deployment adapter; implementations own their credentials."""

    def send(
        self, intent: dict[str, Any], *, timeout_seconds: int
    ) -> NotificationTransportReceipt: ...

    def query(
        self, provider_notification_id: str, *, timeout_seconds: int
    ) -> NotificationQueryReceipt: ...


def _intent_id(
    workspace: Any,
    detail: dict[str, Any],
    *,
    recipient_actor_id: str,
    recipient_ref: str,
    channel_id: str,
) -> str:
    preview = detail.get("preview") or {}
    digest = sha256_digest(
        {
            "tenant_id": workspace.profile.organization_id,
            "workspace_id": workspace.store.workspace_id,
            "event_digest": detail["event"]["digest"],
            "authority_revision": detail["authority"]["revision"],
            "preview_digest": preview.get("preview_digest"),
            "recipient_actor_id": recipient_actor_id,
            "recipient_ref": recipient_ref,
            "channel_id": channel_id,
            "message_contract": _MESSAGE_CONTRACT,
        }
    )
    return "change-notification:" + digest[7:]


def _channel_authorization_digest(
    config: NotificationChannelAuthorization,
    *,
    recipient_actor_id: str,
    recipient_ref: str,
) -> str:
    """Bind channel identity while excluding transport timing knobs."""

    return sha256_digest(
        {
            "schema_version": config.schema_version,
            "channel_id": config.channel_id,
            "transport_name": config.transport_name,
            "reauthentication_origin": config.reauthentication_origin.rstrip("/"),
            "recipient_actor_id": recipient_actor_id,
            "recipient_ref": recipient_ref,
        }
    )


def _intent_semantics(
    workspace: Any,
    detail: dict[str, Any],
    config: NotificationChannelAuthorization,
    *,
    notification_id: str,
    recipient_actor_id: str,
    recipient_ref: str,
) -> dict[str, Any]:
    return {
        "schema_version": "orgrebase.change-notification-intent.v1",
        "message_contract": _MESSAGE_CONTRACT,
        "notification_id": notification_id,
        "tenant_id": workspace.profile.organization_id,
        "workspace_id": workspace.store.workspace_id,
        "event_id": detail["event"]["event_id"],
        "event_digest": detail["event"]["digest"],
        "preview_digest": (detail.get("preview") or {}).get("preview_digest"),
        "authority_revision": detail["authority"]["revision"],
        "recipient_actor_id": recipient_actor_id,
        "recipient_ref": recipient_ref,
        "channel_id": config.channel_id,
        "transport_name": config.transport_name,
        "channel_authorization_digest": _channel_authorization_digest(
            config,
            recipient_actor_id=recipient_actor_id,
            recipient_ref=recipient_ref,
        ),
        "reason_code": "REVIEW_REQUIRED",
        "requested_action": "APPROVE_OR_REJECT",
        "summary": "OrgRebase action required",
        "reauthentication_link": (
            config.reauthentication_origin.rstrip("/")
            + "/workspace/changes/"
            + quote(detail["event"]["event_id"], safe="")
        ),
    }


def _require_channel_authorization(
    intent: dict[str, Any], config: NotificationChannelAuthorization
) -> None:
    try:
        recipient_ref = config.recipient_for(intent["recipient_actor_id"])
    except (KeyError, IntegrityError) as exc:
        raise IntegrityError("NOTIFICATION_CHANNEL_AUTHORIZATION_CHANGED") from exc
    expected = _channel_authorization_digest(
        config,
        recipient_actor_id=intent["recipient_actor_id"],
        recipient_ref=recipient_ref,
    )
    if (
        recipient_ref != intent["recipient_ref"]
        or expected != intent["channel_authorization_digest"]
    ):
        raise IntegrityError("NOTIFICATION_CHANNEL_AUTHORIZATION_CHANGED")


def _safe_transport_reason(error: Exception) -> str:
    code = getattr(error, "reason_code", None)
    return (
        code
        if isinstance(code, str) and code in _SAFE_TRANSPORT_REASON_CODES
        else "NOTIFICATION_TRANSPORT_RESULT_UNKNOWN"
    )


def _intent_artifact(notification_id: str) -> str:
    return f"{notification_id}:intent@v1"


def _intent_reservation_key(notification_id: str) -> str:
    return f"notification-intent-reservation:{notification_id}"


def _review_slot_key(
    workspace: Any, detail: dict[str, Any], *, channel_id: str
) -> str:
    return "notification-review-slot:" + sha256_digest(
        {
            "tenant_id": workspace.profile.organization_id,
            "workspace_id": workspace.store.workspace_id,
            "event_digest": detail["event"]["digest"],
            "authority_revision": detail["authority"]["revision"],
            "preview_digest": (detail.get("preview") or {}).get("preview_digest"),
            "channel_id": channel_id,
            "message_contract": _MESSAGE_CONTRACT,
        }
    )[7:]


def _dispatch_claim_key(notification_id: str) -> str:
    return f"notification-dispatch-claim:{notification_id}"


def _state_prefix(notification_id: str) -> str:
    return f"{notification_id}:state:"


def _states(workspace: Any, notification_id: str) -> list[dict[str, Any]]:
    artifacts = workspace.store.list_artifacts(
        artifact_id_prefix=_state_prefix(notification_id), expected_media_type=_MEDIA
    )
    states: list[dict[str, Any]] = []
    previous_digest = None
    for expected, artifact in enumerate(artifacts):
        payload = artifact.payload
        if (
            payload.get("sequence") != expected
            or payload.get("notification_id") != notification_id
            or payload.get("previous_state_digest") != previous_digest
        ):
            raise IntegrityError("NOTIFICATION_STATE_CHAIN_INVALID")
        states.append(payload)
        previous_digest = artifact.payload_digest
    return states


def _append_state(
    workspace: Any,
    connection: Any,
    *,
    notification_id: str,
    state: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    states = _states(workspace, notification_id)
    previous = states[-1] if states else None
    payload = {
        "schema_version": "orgrebase.change-notification-state.v1",
        "notification_id": notification_id,
        "sequence": len(states),
        "state": state,
        "previous_state_digest": sha256_digest(previous) if previous is not None else None,
        "recorded_at": workspace.clock.now(),
        **(detail or {}),
    }
    workspace.store.save_artifact(
        connection,
        f"{_state_prefix(notification_id)}{len(states):04d}@v1",
        _MEDIA,
        payload,
    )
    workspace.store.append_event(
        connection,
        "WORKSPACE_NOTIFICATION_STATE_RECORDED",
        {
            "notification_id": notification_id,
            "state": state,
            "sequence": len(states),
        },
    )
    return payload


def create_notification_intent(
    workspace: Any,
    event_id: str,
    config: NotificationChannelAuthorization,
) -> dict[str, Any]:
    """Create one immutable minimal notification for the current owner task."""

    config = NotificationChannelAuthorization.model_validate(config.model_dump())
    if not config.enabled:
        raise IntegrityError("NOTIFICATION_CHANNEL_DISABLED")
    require_action(workspace, "propose")
    with workspace._command_lock, workspace.store.transaction() as connection:
        require_action(workspace, "propose")
        detail = change_detail(workspace, event_id)
        allowed = set(detail["allowed_actions"])
        if "APPROVE" not in allowed:
            raise IntegrityError("NOTIFICATION_HUMAN_TASK_NOT_CURRENT")
        recipient_actor_id = detail["active_owner_id"]
        recipient_ref = config.recipient_for(recipient_actor_id)
        notification_id = _intent_id(
            workspace,
            detail,
            recipient_actor_id=recipient_actor_id,
            recipient_ref=recipient_ref,
            channel_id=config.channel_id,
        )
        semantics = _intent_semantics(
            workspace,
            detail,
            config,
            notification_id=notification_id,
            recipient_actor_id=recipient_actor_id,
            recipient_ref=recipient_ref,
        )
        semantics_digest = sha256_digest(semantics)
        review_slot_key = _review_slot_key(
            workspace, detail, channel_id=config.channel_id
        )
        review_slot_digest = sha256_digest(
            {
                "notification_id": notification_id,
                "channel_authorization_digest": semantics[
                    "channel_authorization_digest"
                ],
            }
        )
        try:
            review_slot = workspace.store.get_idempotent(
                review_slot_key, review_slot_digest, connection=connection
            )
        except RuntimeError as exc:
            raise IntegrityError(
                "NOTIFICATION_REVIEW_RECIPIENT_OR_CHANNEL_CHANGED"
            ) from exc
        try:
            reservation = workspace.store.get_idempotent(
                _intent_reservation_key(notification_id),
                semantics_digest,
                connection=connection,
            )
        except RuntimeError as exc:
            raise IntegrityError("NOTIFICATION_INTENT_CONFLICT") from exc
        artifact_id = _intent_artifact(notification_id)
        try:
            prior = workspace.store.load_artifact(artifact_id, _MEDIA).payload
        except KeyError:
            prior = None
        if review_slot is not None or reservation is not None or prior is not None:
            if (
                review_slot
                != {
                    "notification_id": notification_id,
                    "channel_authorization_digest": semantics[
                        "channel_authorization_digest"
                    ],
                }
                or reservation is None
                or prior is None
                or reservation
                != {
                    "artifact_id": artifact_id,
                    "intent_semantics_digest": semantics_digest,
                }
                or set(prior) != {*semantics, "created_at"}
                or {key: prior[key] for key in semantics} != semantics
                or not isinstance(prior["created_at"], str)
            ):
                raise IntegrityError("NOTIFICATION_INTENT_CONFLICT")
            return notification_status(workspace, notification_id)
        intent = {**semantics, "created_at": workspace.clock.now()}
        workspace.store.save_artifact(connection, artifact_id, _MEDIA, intent)
        _append_state(
            workspace,
            connection,
            notification_id=notification_id,
            state="READY",
        )
        workspace.store.append_event(
            connection,
            "WORKSPACE_NOTIFICATION_INTENT_RECORDED",
            {
                "notification_id": notification_id,
                "event_id": event_id,
                "event_digest": detail["event"]["digest"],
                "authority_revision": detail["authority"]["revision"],
                "channel_authorization_digest": intent[
                    "channel_authorization_digest"
                ],
            },
        )
        workspace.store.save_idempotent(
            connection,
            _intent_reservation_key(notification_id),
            semantics_digest,
            {
                "artifact_id": artifact_id,
                "intent_semantics_digest": semantics_digest,
            },
        )
        workspace.store.save_idempotent(
            connection,
            review_slot_key,
            review_slot_digest,
            {
                "notification_id": notification_id,
                "channel_authorization_digest": intent[
                    "channel_authorization_digest"
                ],
            },
        )
    return notification_status(workspace, notification_id)


def _load_intent(workspace: Any, notification_id: str) -> dict[str, Any]:
    try:
        return workspace.store.load_artifact(
            _intent_artifact(notification_id), _MEDIA
        ).payload
    except KeyError as exc:
        raise KeyError("NOTIFICATION_NOT_FOUND") from exc


def _authority_is_current(workspace: Any, intent: dict[str, Any]) -> bool:
    detail = change_detail(workspace, intent["event_id"])
    current = (
        detail["event"]["digest"] == intent["event_digest"]
        and detail["authority"]["revision"] == intent["authority_revision"]
        and detail["active_owner_id"] == intent["recipient_actor_id"]
        and detail["authority"]["blocked_reason"] is None
        and detail["status"] == "PREVIEWED"
        and detail["preview"] is not None
        and detail["approval"] is None
        and detail["outcome"] is None
    )
    if not current:
        return False
    directory = getattr(workspace, "members_for_action", None)
    verifier = getattr(workspace, "verify_membership", None)
    if directory is None or verifier is None:
        return True  # Controlled-local identity; never reported as external IAM proof.
    member = next(
        (
            item
            for item in directory("approve")
            if item["actor_id"] == intent["recipient_actor_id"]
        ),
        None,
    )
    if member is None:
        return False
    try:
        verifier(member["subject"], member["actor_id"], "approve")
        authorize_scope = getattr(workspace, "authorize_workspace_subject", None)
        if authorize_scope is not None:
            authorize_scope(member["subject"])
    except AuthenticationError:
        return False
    return True


def notification_status(workspace: Any, notification_id: str) -> dict[str, Any]:
    """Project delivery facts separately from current recipient authority."""

    require_action(workspace, "read")
    intent = _load_intent(workspace, notification_id)
    states = _states(workspace, notification_id)
    if not states:
        raise IntegrityError("NOTIFICATION_STATE_MISSING")
    latest = states[-1]
    projected_state = latest["state"]
    if (
        projected_state == "DISPATCHING"
        and workspace._wall_clock_epoch_ms() >= latest["unknown_after_epoch_ms"]
    ):
        projected_state = "SENT_UNKNOWN"
    return {
        "schema_version": "orgrebase.change-notification-status.v1",
        "notification_id": notification_id,
        "event_id": intent["event_id"],
        "recipient_actor_id": intent["recipient_actor_id"],
        "reason_code": intent["reason_code"],
        "requested_action": intent["requested_action"],
        "reauthentication_link": intent["reauthentication_link"],
        "delivery_state": projected_state,
        "recipient_authority_current": _authority_is_current(workspace, intent),
        "provider_notification_id": latest.get("provider_notification_id"),
        "delivery_is_approval": False,
        "canonical_business_writes": 0,
        "target_writes": 0,
    }


def dispatch_notification(
    workspace: Any,
    notification_id: str,
    config: NotificationChannelAuthorization,
    transport: NotificationTransport,
) -> dict[str, Any]:
    """Claim once, send outside SQL, and persist a separate send receipt."""

    config = NotificationChannelAuthorization.model_validate(config.model_dump())
    if not config.enabled:
        raise IntegrityError("NOTIFICATION_CHANNEL_DISABLED")
    intent = _load_intent(workspace, notification_id)
    _require_channel_authorization(intent, config)
    require_action(workspace, "propose")
    should_send = False
    with workspace._command_lock, workspace.store.transaction() as connection:
        require_action(workspace, "propose")
        claim_digest = sha256_digest(
            {
                "notification_id": notification_id,
                "intent_digest": sha256_digest(intent),
            }
        )
        try:
            claim = workspace.store.get_idempotent(
                _dispatch_claim_key(notification_id),
                claim_digest,
                connection=connection,
            )
        except RuntimeError as exc:
            raise IntegrityError("NOTIFICATION_DISPATCH_CLAIM_CONFLICT") from exc
        states = _states(workspace, notification_id)
        latest = states[-1]
        if latest["state"] == "DISPATCHING":
            if workspace._wall_clock_epoch_ms() >= latest["unknown_after_epoch_ms"]:
                _append_state(
                    workspace,
                    connection,
                    notification_id=notification_id,
                    state="SENT_UNKNOWN",
                    detail={"reason_code": "NOTIFICATION_DISPATCH_RECEIPT_MISSING"},
                )
        elif latest["state"] == "READY":
            if claim is not None:
                raise IntegrityError("NOTIFICATION_DISPATCH_STATE_INVALID")
            if not _authority_is_current(workspace, intent):
                _append_state(
                    workspace,
                    connection,
                    notification_id=notification_id,
                    state="INVALIDATED",
                    detail={"reason_code": "NOTIFICATION_RECIPIENT_AUTHORITY_CHANGED"},
                )
            else:
                _append_state(
                    workspace,
                    connection,
                    notification_id=notification_id,
                    state="DISPATCHING",
                    detail={
                        "unknown_after_epoch_ms": workspace._wall_clock_epoch_ms()
                        + config.unknown_after_seconds * 1000
                    },
                )
                workspace.store.save_idempotent(
                    connection,
                    _dispatch_claim_key(notification_id),
                    claim_digest,
                    {
                        "notification_id": notification_id,
                        "intent_digest": sha256_digest(intent),
                    },
                )
                should_send = True
    if not should_send:
        return notification_status(workspace, notification_id)

    try:
        receipt = NotificationTransportReceipt.model_validate(
            transport.send(intent, timeout_seconds=config.send_timeout_seconds)
        )
        state = {
            "SENT": "SENT",
            "SENT_UNKNOWN": "SENT_UNKNOWN",
            "NOT_SENT": "FAILED_NOT_SENT",
        }[receipt.dispatch_state]
        state_detail = {
            "provider_notification_id": receipt.provider_notification_id,
            "reason_code": None,
        }
    except Exception as error:
        # Arbitrary transport messages may contain credentials or remote bodies.
        state = "SENT_UNKNOWN"
        state_detail = {
            "provider_notification_id": None,
            "reason_code": _safe_transport_reason(error),
        }
    with workspace._command_lock, workspace.store.transaction() as connection:
        claim = workspace.store.get_idempotent(
            _dispatch_claim_key(notification_id),
            claim_digest,
            connection=connection,
        )
        if claim is None:
            raise IntegrityError("NOTIFICATION_DISPATCH_CLAIM_MISSING")
        latest = _states(workspace, notification_id)[-1]
        if latest["state"] not in {"DISPATCHING", "SENT_UNKNOWN"}:
            raise IntegrityError("NOTIFICATION_DISPATCH_RECEIPT_STATE_INVALID")
        _append_state(
            workspace,
            connection,
            notification_id=notification_id,
            state=state,
            detail=state_detail,
        )
    return notification_status(workspace, notification_id)


_DELIVERY_ORDER = {"SENT_UNKNOWN": 0, "SENT": 1, "DELIVERED": 2, "READ": 3}


def refresh_notification_delivery(
    workspace: Any,
    notification_id: str,
    config: NotificationChannelAuthorization,
    transport: NotificationTransport,
) -> dict[str, Any]:
    """Query a provider ID without resending the notification."""

    config = NotificationChannelAuthorization.model_validate(config.model_dump())
    intent = _load_intent(workspace, notification_id)
    _require_channel_authorization(intent, config)
    require_action(workspace, "read")
    states = _states(workspace, notification_id)
    latest = states[-1]
    provider_id = latest.get("provider_notification_id")
    if latest["state"] not in _DELIVERY_ORDER or provider_id is None:
        return notification_status(workspace, notification_id)
    try:
        receipt = NotificationQueryReceipt.model_validate(
            transport.query(provider_id, timeout_seconds=config.send_timeout_seconds)
        )
    except Exception:
        return notification_status(workspace, notification_id)
    if receipt.provider_notification_id != provider_id:
        raise IntegrityError("NOTIFICATION_QUERY_RECEIPT_ID_MISMATCH")
    with workspace._command_lock, workspace.store.transaction() as connection:
        claim_digest = sha256_digest(
            {
                "notification_id": notification_id,
                "intent_digest": sha256_digest(intent),
            }
        )
        claim = workspace.store.get_idempotent(
            _dispatch_claim_key(notification_id),
            claim_digest,
            connection=connection,
        )
        if claim is None:
            raise IntegrityError("NOTIFICATION_DISPATCH_CLAIM_MISSING")
        latest = _states(workspace, notification_id)[-1]
        if (
            latest["state"] not in _DELIVERY_ORDER
            or latest.get("provider_notification_id") != provider_id
            or _DELIVERY_ORDER[receipt.delivery_state]
            <= _DELIVERY_ORDER[latest["state"]]
        ):
            return notification_status(workspace, notification_id)
        _append_state(
            workspace,
            connection,
            notification_id=notification_id,
            state=receipt.delivery_state,
            detail={"provider_notification_id": provider_id, "reason_code": None},
        )
    return notification_status(workspace, notification_id)
