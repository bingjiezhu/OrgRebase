"""Bounded, default-off collection of Quote recovery observations.

The collector is a reader-only service principal.  It cannot assess cases,
freeze a corpus, publish a lesson, or change a business outcome.  Its cursor is
the existing fenced source checkpoint; cases and the cursor commit together.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from orgrebase.auth import AuthenticationError, request_principal
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.experience_contracts import CaseObservationV2
from orgrebase.workspace.quote_pattern_bridge import (
    _OBSERVED_EVENTS,
    PROFILE_ID,
    quote_recovery_observation_v2,
)

CURSOR_SCHEMA = "orgrebase.experience-collector-cursor.v1"
OBSERVATION_MEDIA = "application/vnd.orgrebase.experience-observation+json"
COLLECTION_MEDIA = "application/vnd.orgrebase.experience-collection+json"
GENESIS_DIGEST = "sha256:" + "0" * 64


@dataclass(frozen=True)
class CollectionResult:
    status: str
    cursor_sequence: int
    observed: int = 0
    quarantined: int = 0
    skipped: int = 0
    has_more: bool = False


def _cursor(value: str | None) -> tuple[int, str]:
    if value is None:
        return 0, GENESIS_DIGEST
    try:
        item = json.loads(value)
        sequence = item["sequence_no"]
        digest = item["event_digest"]
        if (
            set(item) != {"schema_version", "sequence_no", "event_digest"}
            or item["schema_version"] != CURSOR_SCHEMA
            or type(sequence) is not int or sequence < 1
            or not isinstance(digest, str) or len(digest) != 71
            or not digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in digest[7:])
            or canonical_json(item) != value
        ):
            raise ValueError("invalid cursor")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise IntegrityError("EXPERIENCE_CURSOR_INVALID") from exc
    return sequence, digest


def _encoded_cursor(sequence: int, digest: str) -> str:
    return canonical_json({
        "schema_version": CURSOR_SCHEMA,
        "sequence_no": sequence,
        "event_digest": digest,
    })


def _event_artifact_id(event_digest: str) -> str:
    return "experience-collection:" + event_digest[7:] + "@v1"


def _safe_case_reason(error: Exception) -> str:
    candidate = str(error)
    if isinstance(error, IntegrityError) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", candidate):
        return candidate
    return "EXPERIENCE_CASE_SOURCE_INVALID"


class ExperienceCollector:
    """One explicitly invoked worker for one workspace and one known profile."""

    def __init__(
        self, workspace: Any, *, collector_actor_id: str,
        enabled: bool = False, lease_seconds: float = 30.0,
    ) -> None:
        if not collector_actor_id or not collector_actor_id.strip():
            raise ValueError("EXPERIENCE_COLLECTOR_ACTOR_REQUIRED")
        if not 1 <= lease_seconds <= 300:
            raise ValueError("EXPERIENCE_COLLECTOR_LEASE_INVALID")
        self.workspace = workspace
        self.collector_actor_id = collector_actor_id
        self.enabled = enabled
        self.lease_seconds = lease_seconds
        self.connector_id = f"experience-collector:{PROFILE_ID}:v1"

    def _authorize(self) -> None:
        principal = request_principal.get()
        if principal is None:
            raise AuthenticationError("EXPERIENCE_COLLECTOR_PRINCIPAL_REQUIRED")
        if (
            principal.actor_id != self.collector_actor_id
            or principal.roles != frozenset({"reader"})
            or principal.tenant_id != self.workspace.store.tenant_id
        ):
            raise AuthorizationError("EXPERIENCE_COLLECTOR_SCOPE_DENIED")
        require_action(self.workspace, "read")

    def _claim(self, worker_id: str, now: float) -> dict[str, Any] | None:
        with self.workspace.store.transaction() as connection:
            return self.workspace.store.claim_source(
                connection, connector_id=self.connector_id, worker_id=worker_id,
                now=now, lease_seconds=self.lease_seconds,
            )

    def _release(self, worker_id: str, fence: int) -> None:
        with self.workspace.store.transaction() as connection:
            self.workspace.store.release_source_claim(
                connection, connector_id=self.connector_id,
                worker_id=worker_id, fence=fence,
            )

    def _save_case(self, connection: Any, case: CaseObservationV2, episode: dict[str, Any]) -> tuple[str, str]:
        if self.workspace.private_records.retention_seconds == 0:
            raise IntegrityError("EXPERIENCE_PRIVATE_RETENTION_DISABLED")
        episode_ref = "private-experience:" + case.digest[7:] + "@v1"
        episode_digest = sha256_digest(episode)
        self.workspace.private_records.write(
            connection, record_id=episode_ref,
            owner_id=self.collector_actor_id,
            scope_ref=f"experience:{case.case_id}", payload=episode,
        )
        case = CaseObservationV2.model_validate({
            **case.model_dump(mode="json", exclude={"digest"}),
            "private_episode_ref": episode_ref,
            "private_episode_digest": episode_digest,
        })
        case_ref = f"experience-case:{case.case_id}:{case.revision[7:]}@v2"
        self.workspace.store.save_artifact(
            connection, case_ref, OBSERVATION_MEDIA, case.model_dump(mode="json"),
        )
        return case_ref, case.digest

    def collect(self, *, worker_id: str, limit: int = 100) -> CollectionResult:
        """Check the full event chain page before filtering, then commit once.

        A bad individual case gets a persistent quarantine locator.  Sequence,
        digest, cursor, and authorization errors stop the page with no advance.
        """

        if not self.enabled:
            return CollectionResult(status="DISABLED", cursor_sequence=0)
        self._authorize()
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("EXPERIENCE_WORKER_ID_REQUIRED")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("EXPERIENCE_PAGE_LIMIT_INVALID")
        now = time.time()
        checkpoint = self._claim(worker_id, now)
        if checkpoint is None:
            return CollectionResult(status="BUSY", cursor_sequence=0)
        fence = int(checkpoint["fence"])
        try:
            sequence, previous_digest = _cursor(checkpoint["cursor"])
            if sequence:
                prior = self.workspace.store.event_by_digest(previous_digest)
                if prior is None or prior["sequence_no"] != sequence:
                    raise IntegrityError("EXPERIENCE_CURSOR_EVENT_MISMATCH")
            # No event_types/subject_key filter: unrelated rows also prove and
            # advance the continuous chain.
            page = self.workspace.store.event_page(after=sequence, limit=limit)
            events = page["items"]
            if not events:
                self._release(worker_id, fence)
                return CollectionResult(status="IDLE", cursor_sequence=sequence)
            observed: list[tuple[CaseObservationV2, dict[str, Any], dict[str, Any]]] = []
            quarantined: list[dict[str, Any]] = []
            skipped = 0
            for event in events:
                if event["sequence_no"] != sequence + 1 or event["previous_digest"] != previous_digest:
                    raise IntegrityError("EXPERIENCE_EVENT_CHAIN_DISCONTINUITY")
                sequence = event["sequence_no"]
                previous_digest = event["event_digest"]
                if event["event_type"] not in _OBSERVED_EVENTS:
                    skipped += 1
                    continue
                try:
                    case, episode = quote_recovery_observation_v2(self.workspace, event)
                except (AuthenticationError, AuthorizationError, PermissionError):
                    raise
                except IntegrityError as exc:
                    if (
                        event["event_type"] in {
                            "WORKSPACE_CHANGE_REJECTED", "WORKSPACE_CHANGE_OUTCOME_RECORDED",
                        }
                        and str(exc) == "EXPERIENCE_RECOVERY_REQUEST_NOT_FOUND"
                    ):
                        # Ordinary business rejection/Apply is outside the
                        # Quote recovery profile, not a failed learning case.
                        skipped += 1
                        continue
                    quarantined.append({
                        "schema_version": "orgrebase.experience-collection.v1",
                        "status": "QUARANTINED", "profile_id": PROFILE_ID,
                        "origin_sequence": event["sequence_no"],
                        "origin_event_digest": event["event_digest"],
                        "reason_code": _safe_case_reason(exc),
                    })
                    continue
                except (KeyError, TypeError, ValueError) as exc:
                    # No untrusted message or payload is copied to the durable
                    # quarantine record.  The original event remains addressable
                    # by digest for a separately authorized revisit.
                    quarantined.append({
                        "schema_version": "orgrebase.experience-collection.v1",
                        "status": "QUARANTINED",
                        "profile_id": PROFILE_ID,
                        "origin_sequence": event["sequence_no"],
                        "origin_event_digest": event["event_digest"],
                        "reason_code": _safe_case_reason(exc),
                    })
                    continue
                observed.append((case, episode, event))
            final_cursor = _encoded_cursor(sequence, previous_digest)
            self._authorize()
            with self.workspace.store.transaction() as connection:
                self.workspace.store.require_before_commit(connection, self._authorize)
                current = self.workspace.store.get_source_checkpoint(
                    self.connector_id, connection=connection,
                )
                if current is None or current["fence"] != fence or current["cursor"] != checkpoint["cursor"]:
                    raise IntegrityError("EXPERIENCE_CHECKPOINT_CHANGED")
                saved_count = 0
                for case, episode, event in observed:
                    if self.workspace.private_records.retention_seconds == 0:
                        quarantined.append({
                            "schema_version": "orgrebase.experience-collection.v1",
                            "status": "QUARANTINED", "profile_id": PROFILE_ID,
                            "origin_sequence": event["sequence_no"],
                            "origin_event_digest": event["event_digest"],
                            "reason_code": "EXPERIENCE_PRIVATE_RETENTION_DISABLED",
                        })
                        continue
                    case_ref, case_digest = self._save_case(connection, case, episode)
                    self.workspace.store.save_artifact(
                        connection, _event_artifact_id(event["event_digest"]), COLLECTION_MEDIA,
                        {
                            "schema_version": "orgrebase.experience-collection.v1",
                            "status": "OBSERVED", "profile_id": PROFILE_ID,
                            "origin_sequence": event["sequence_no"],
                            "origin_event_digest": event["event_digest"],
                            "case_ref": case_ref, "case_digest": case_digest,
                        },
                    )
                    saved_count += 1
                for record in quarantined:
                    self.workspace.store.save_artifact(
                        connection, _event_artifact_id(record["origin_event_digest"]),
                        COLLECTION_MEDIA, record,
                    )
                self.workspace.store.commit_source_page(
                    connection, connector_id=self.connector_id,
                    expected_cursor=checkpoint["cursor"], cursor=final_cursor,
                    worker_id=worker_id, fence=fence, now=time.time(),
                )
            return CollectionResult(
                status="PARTIAL" if page["next_cursor"] is not None else "COMPLETE",
                cursor_sequence=sequence, observed=saved_count,
                quarantined=len(quarantined), skipped=skipped,
                has_more=page["next_cursor"] is not None,
            )
        except BaseException:
            self._release(worker_id, fence)
            raise

    def quarantined(self) -> tuple[dict[str, Any], ...]:
        """Only the currently bound reader service sees exact revisit locators."""

        self._authorize()
        family = self.workspace.store.list_artifacts(
            artifact_id_prefix="experience-collection:", expected_media_type=COLLECTION_MEDIA,
        )
        return tuple(item.payload for item in family if item.payload.get("status") == "QUARANTINED")

    def revisit_quarantined(self, *, origin_event_digest: str, attempt_id: str) -> dict[str, Any]:
        """Explicitly re-evaluate one durable quarantine without moving cursor.

        The original quarantine remains immutable.  A later source repair gets
        a separately identified receipt and can create a new observed case.
        """

        if not self.enabled:
            raise RuntimeError("EXPERIENCE_COLLECTOR_DISABLED")
        self._authorize()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", origin_event_digest):
            raise ValueError("EXPERIENCE_REVISIT_EVENT_DIGEST_INVALID")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", attempt_id):
            raise ValueError("EXPERIENCE_REVISIT_ATTEMPT_ID_INVALID")
        original = self.workspace.store.load_artifact(
            _event_artifact_id(origin_event_digest), COLLECTION_MEDIA,
        ).payload
        if original.get("status") != "QUARANTINED" or original.get("origin_event_digest") != origin_event_digest:
            raise IntegrityError("EXPERIENCE_REVISIT_ORIGIN_INVALID")
        review_id = "experience-revisit:" + sha256_digest({
            "event_digest": origin_event_digest, "attempt_id": attempt_id,
        })[7:] + "@v1"
        try:
            return self.workspace.store.load_artifact(review_id, COLLECTION_MEDIA).payload
        except KeyError:
            pass
        event = self.workspace.store.event_by_digest(origin_event_digest)
        if event is None or event["sequence_no"] != original["origin_sequence"]:
            raise IntegrityError("EXPERIENCE_REVISIT_EVENT_MISSING")
        try:
            case, episode = quote_recovery_observation_v2(self.workspace, event)
            if self.workspace.private_records.retention_seconds == 0:
                raise IntegrityError("EXPERIENCE_PRIVATE_RETENTION_DISABLED")
        except (AuthenticationError, AuthorizationError, PermissionError):
            raise
        except (IntegrityError, KeyError, TypeError, ValueError) as exc:
            case = None
            episode = None
            reason = _safe_case_reason(exc)
        self._authorize()
        with self.workspace.store.transaction() as connection:
            self.workspace.store.require_before_commit(connection, self._authorize)
            if case is not None and episode is not None:
                case_ref, case_digest = self._save_case(connection, case, episode)
                record = {
                    "schema_version": "orgrebase.experience-collection.v1",
                    "status": "OBSERVED", "profile_id": PROFILE_ID,
                    "origin_sequence": event["sequence_no"],
                    "origin_event_digest": origin_event_digest,
                    "revisit_attempt_id": attempt_id,
                    "case_ref": case_ref, "case_digest": case_digest,
                }
            else:
                record = {
                    "schema_version": "orgrebase.experience-collection.v1",
                    "status": "QUARANTINED", "profile_id": PROFILE_ID,
                    "origin_sequence": event["sequence_no"],
                    "origin_event_digest": origin_event_digest,
                    "revisit_attempt_id": attempt_id, "reason_code": reason,
                }
            self.workspace.store.save_artifact(connection, review_id, COLLECTION_MEDIA, record)
        return record
