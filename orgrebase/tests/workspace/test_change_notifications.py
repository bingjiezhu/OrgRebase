from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from orgrebase.clock import SystemClock
from orgrebase.domain import IntegrityError
from orgrebase.workspace import change_notifications
from orgrebase.workspace.change_notifications import (
    NotificationChannelAuthorization,
    NotificationQueryReceipt,
    NotificationRecipientBinding,
    NotificationTransportReceipt,
    create_notification_intent,
    dispatch_notification,
    notification_status,
    refresh_notification_delivery,
)
from orgrebase.workspace.change_proposals import change_detail, submit_change
from tests.workspace.test_change_proposals import command
from tests.workspace.test_change_proposals import workspace as workspace


def channel(workspace, **updates) -> NotificationChannelAuthorization:
    values = {
        "schema_version": "orgrebase.notification-channel.v1",
        "enabled": True,
        "channel_id": "review-channel",
        "transport_name": "test-substitute",
        "reauthentication_origin": "https://orgrebase.example",
        "recipients": (
            NotificationRecipientBinding(
                actor_id=workspace.change_owner["edit-1"], recipient_ref="user:finance"
            ),
        ),
        "send_timeout_seconds": 5,
        "unknown_after_seconds": 5,
    }
    values.update(updates)
    return NotificationChannelAuthorization.model_validate(values)


class Transport:
    def __init__(self):
        self.sends = 0
        self.queries = 0
        self.query_state = "DELIVERED"

    def send(self, intent, *, timeout_seconds):
        self.sends += 1
        assert timeout_seconds == 5
        assert "token" not in str(intent).lower()
        return NotificationTransportReceipt(
            dispatch_state="SENT", provider_notification_id="notification-1"
        )

    def query(self, provider_notification_id, *, timeout_seconds):
        self.queries += 1
        assert provider_notification_id == "notification-1"
        return NotificationQueryReceipt(
            delivery_state=self.query_state,
            provider_notification_id=provider_notification_id,
        )


def _intent(workspace):
    submit_change(workspace, command(workspace))
    workspace.preview_change("edit-1")
    return create_notification_intent(workspace, "edit-1", channel(workspace))


def test_notification_keeps_send_delivery_read_and_approval_separate(workspace):
    created = _intent(workspace)
    transport = Transport()

    sent = dispatch_notification(
        workspace, created["notification_id"], channel(workspace), transport
    )
    delivered = refresh_notification_delivery(
        workspace, created["notification_id"], channel(workspace), transport
    )
    transport.query_state = "READ"
    read = refresh_notification_delivery(
        workspace, created["notification_id"], channel(workspace), transport
    )

    assert created["delivery_state"] == "READY"
    assert sent["delivery_state"] == "SENT"
    assert delivered["delivery_state"] == "DELIVERED"
    assert read["delivery_state"] == "READ"
    assert read["delivery_is_approval"] is False
    assert workspace._approval_record("edit-1") is None
    assert transport.sends == 1 and transport.queries == 2
    intent = workspace.store.load_artifact(
        f"{created['notification_id']}:intent@v1",
        "application/vnd.orgrebase.change-notification+json",
    ).payload
    assert intent["summary"] == "OrgRebase action required"
    assert intent["reauthentication_link"].startswith("https://orgrebase.example/")
    assert "Enterprise Plus" not in str(intent)


def test_duplicate_dispatch_never_resends(workspace):
    created = _intent(workspace)
    transport = Transport()

    first = dispatch_notification(
        workspace, created["notification_id"], channel(workspace), transport
    )
    second = dispatch_notification(
        workspace, created["notification_id"], channel(workspace), transport
    )

    assert first == second
    assert transport.sends == 1


def test_system_clock_repeated_create_preserves_first_intent_timestamp(workspace):
    workspace.clock = SystemClock()
    submit_change(workspace, command(workspace))
    workspace.preview_change("edit-1")
    config = channel(workspace)

    first = create_notification_intent(workspace, "edit-1", config)
    stored = workspace.store.load_artifact(
        f"{first['notification_id']}:intent@v1",
        "application/vnd.orgrebase.change-notification+json",
    ).payload
    time.sleep(0.002)
    second = create_notification_intent(workspace, "edit-1", config)
    repeated = workspace.store.load_artifact(
        f"{first['notification_id']}:intent@v1",
        "application/vnd.orgrebase.change-notification+json",
    ).payload

    assert type(workspace.clock).__name__ == "SystemClock"
    assert first == second
    assert repeated["created_at"] == stored["created_at"]
    assert len(
        workspace.store.list_artifacts(
            artifact_id_prefix=f"{first['notification_id']}:state:"
        )
    ) == 1


def test_transport_timing_changes_reuse_one_logical_intent_and_claim(workspace):
    created = _intent(workspace)
    changed = channel(
        workspace, send_timeout_seconds=9, unknown_after_seconds=20
    )
    repeated = create_notification_intent(workspace, "edit-1", changed)

    class ChangedTimeoutTransport(Transport):
        def send(self, intent, *, timeout_seconds):
            self.sends += 1
            assert timeout_seconds == 9
            return NotificationTransportReceipt(
                dispatch_state="SENT", provider_notification_id="notification-1"
            )

    transport = ChangedTimeoutTransport()
    first = dispatch_notification(
        workspace, created["notification_id"], changed, transport
    )
    second = dispatch_notification(
        workspace, created["notification_id"], channel(workspace), transport
    )

    assert repeated["notification_id"] == created["notification_id"]
    assert first == second
    assert transport.sends == 1


def test_logical_id_binds_tenant_workspace_recipient_and_channel(workspace):
    submit_change(workspace, command(workspace))
    workspace.preview_change("edit-1")
    detail = change_detail(workspace, "edit-1")
    actor = detail["active_owner_id"]

    def identity(target, *, recipient_actor=actor, recipient_ref="user:finance", channel_id="review-channel"):
        return change_notifications._intent_id(
            target,
            detail,
            recipient_actor_id=recipient_actor,
            recipient_ref=recipient_ref,
            channel_id=channel_id,
        )

    base = identity(workspace)
    other_tenant = SimpleNamespace(
        profile=SimpleNamespace(organization_id="org:other"),
        store=SimpleNamespace(workspace_id=workspace.store.workspace_id),
    )
    other_workspace = SimpleNamespace(
        profile=SimpleNamespace(organization_id=workspace.profile.organization_id),
        store=SimpleNamespace(workspace_id="quote:other"),
    )
    assert len(
        {
            base,
            identity(other_tenant),
            identity(other_workspace),
            identity(workspace, recipient_actor="role:other"),
            identity(workspace, recipient_ref="user:other"),
            identity(workspace, channel_id="another-channel"),
        }
    ) == 6


def test_channel_identity_or_recipient_change_conflicts_for_the_same_review_slot(workspace):
    first = _intent(workspace)
    with pytest.raises(
        IntegrityError, match="NOTIFICATION_REVIEW_RECIPIENT_OR_CHANNEL_CHANGED"
    ):
        create_notification_intent(
            workspace,
            "edit-1",
            channel(workspace, reauthentication_origin="https://other.example"),
        )

    remapped = channel(
        workspace,
        recipients=(
            NotificationRecipientBinding(
                actor_id=workspace.change_owner["edit-1"],
                recipient_ref="user:finance-new",
            ),
        ),
    )
    with pytest.raises(
        IntegrityError, match="NOTIFICATION_REVIEW_RECIPIENT_OR_CHANNEL_CHANGED"
    ):
        create_notification_intent(workspace, "edit-1", remapped)

    another_channel = create_notification_intent(
        workspace, "edit-1", channel(workspace, channel_id="another-channel")
    )
    assert another_channel["notification_id"] != first["notification_id"]


def test_concurrent_create_and_dispatch_claim_one_intent_and_one_send(workspace):
    submit_change(workspace, command(workspace))
    workspace.preview_change("edit-1")
    config = channel(workspace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        created = list(
            pool.map(
                lambda _index: create_notification_intent(
                    workspace, "edit-1", config
                ),
                range(2),
            )
        )
    assert created[0]["notification_id"] == created[1]["notification_id"]

    entered, release = Event(), Event()

    class BlockingTransport(Transport):
        def send(self, intent, *, timeout_seconds):
            self.sends += 1
            entered.set()
            assert release.wait(5)
            return NotificationTransportReceipt(
                dispatch_state="SENT", provider_notification_id="notification-1"
            )

    transport = BlockingTransport()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            dispatch_notification,
            workspace,
            created[0]["notification_id"],
            config,
            transport,
        )
        assert entered.wait(5)
        concurrent = pool.submit(
            dispatch_notification,
            workspace,
            created[0]["notification_id"],
            config,
            transport,
        )
        assert concurrent.result(timeout=5)["delivery_state"] == "DISPATCHING"
        release.set()
        assert first.result(timeout=5)["delivery_state"] == "SENT"
    assert transport.sends == 1


def test_transport_exception_reason_is_fixed_and_never_persists_sensitive_text(workspace):
    created = _intent(workspace)
    sentinel = "Bearer TOP-SECRET transport-body"

    class SensitiveError(RuntimeError):
        reason_code = sentinel

    class SensitiveTransport(Transport):
        def send(self, intent, *, timeout_seconds):
            raise SensitiveError(sentinel)

    result = dispatch_notification(
        workspace, created["notification_id"], channel(workspace), SensitiveTransport()
    )
    states = workspace.store.list_artifacts(
        artifact_id_prefix=f"{created['notification_id']}:state:"
    )

    assert result["delivery_state"] == "SENT_UNKNOWN"
    assert sentinel not in str([item.payload for item in states])
    assert states[-1].payload["reason_code"] == "NOTIFICATION_TRANSPORT_RESULT_UNKNOWN"


def test_lost_send_receipt_stays_unknown_and_is_not_blindly_retried(
    workspace, monkeypatch
):
    now = [1_800_000_000_000]
    monkeypatch.setattr(workspace, "_wall_clock_epoch_ms", lambda: now[0])
    created = _intent(workspace)
    transport = Transport()
    original = change_notifications._append_state

    def lose_receipt(*args, **kwargs):
        if kwargs.get("state") == "SENT":
            raise RuntimeError("INJECTED_PROCESS_EXIT_AFTER_EXTERNAL_SEND")
        return original(*args, **kwargs)

    monkeypatch.setattr(change_notifications, "_append_state", lose_receipt)
    with pytest.raises(RuntimeError, match="INJECTED_PROCESS_EXIT"):
        dispatch_notification(
            workspace, created["notification_id"], channel(workspace), transport
        )
    monkeypatch.setattr(change_notifications, "_append_state", original)

    still_running = dispatch_notification(
        workspace, created["notification_id"], channel(workspace), transport
    )
    assert still_running["delivery_state"] == "DISPATCHING"
    now[0] += 6_000
    unknown = dispatch_notification(
        workspace, created["notification_id"], channel(workspace), transport
    )
    assert unknown["delivery_state"] == "SENT_UNKNOWN"
    assert transport.sends == 1


def test_stale_human_task_is_invalidated_before_transport(workspace):
    created = _intent(workspace)
    transport = Transport()
    workspace.reject_change(
        "edit-1",
        actor_id=workspace.change_owner["edit-1"],
        reason="Owner closed this review task",
    )

    result = dispatch_notification(
        workspace, created["notification_id"], channel(workspace), transport
    )

    assert result["delivery_state"] == "INVALIDATED"
    assert result["recipient_authority_current"] is False
    assert transport.sends == 0


def test_recipient_membership_revocation_stops_new_delivery(workspace):
    created = _intent(workspace)
    transport = Transport()
    workspace.members_for_action = lambda _action: []
    workspace.verify_membership = lambda *_args: None

    result = dispatch_notification(
        workspace, created["notification_id"], channel(workspace), transport
    )

    assert result["delivery_state"] == "INVALIDATED"
    assert result["recipient_authority_current"] is False
    assert transport.sends == 0


def test_out_of_order_delivery_query_cannot_downgrade_read(workspace):
    created = _intent(workspace)
    transport = Transport()
    dispatch_notification(
        workspace, created["notification_id"], channel(workspace), transport
    )
    transport.query_state = "READ"
    read = refresh_notification_delivery(
        workspace, created["notification_id"], channel(workspace), transport
    )
    transport.query_state = "DELIVERED"
    repeated = refresh_notification_delivery(
        workspace, created["notification_id"], channel(workspace), transport
    )

    assert read["delivery_state"] == repeated["delivery_state"] == "READ"
    assert notification_status(workspace, created["notification_id"]) == repeated


def test_channel_requires_explicit_https_reauthentication_origin(workspace):
    submit_change(workspace, command(workspace))
    with pytest.raises(ValueError, match="REAUTHENTICATION_ORIGIN_INVALID"):
        channel(workspace, reauthentication_origin="http://orgrebase.example")
