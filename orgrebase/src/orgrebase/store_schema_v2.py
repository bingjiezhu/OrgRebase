"""Released PostgreSQL schema 2 contract, used only to validate upgrades."""

POSTGRES_SCHEMA_V2 = {
    "artifacts": {
        "columns": {
            "artifact_id": ("text", "NO"),
            "media_type": ("text", "NO"),
            "payload_digest": ("text", "NO"),
            "payload_json": ("text", "NO"),
        },
        "constraints": ["PRIMARY KEY (artifact_id)"],
    },
    "compensation_sagas": {
        "columns": {
            "attempt": ("bigint", "NO"),
            "receipt_json": ("text", "NO"),
            "request_digest": ("text", "NO"),
            "saga_key": ("text", "NO"),
            "status": ("text", "NO"),
        },
        "constraints": ["PRIMARY KEY (saga_key)"],
    },
    "current_pointers": {
        "columns": {"object_id": ("text", "NO"), "revision": ("bigint", "NO"), "version_key": ("text", "NO")},
        "constraints": [
            "PRIMARY KEY (object_id)",
            "FOREIGN KEY(version_key) REFERENCES object_versions (version_key) DEFERRABLE",
        ],
    },
    "domain_events": {
        "columns": {
            "event_digest": ("text", "NO"),
            "event_type": ("text", "NO"),
            "payload_json": ("text", "NO"),
            "previous_digest": ("text", "NO"),
            "sequence_no": ("bigint", "NO"),
        },
        "constraints": ["UNIQUE (event_digest)", "PRIMARY KEY (sequence_no)"],
    },
    "effect_intents": {
        "columns": {
            "created_at": ("text", "NO"),
            "effect_id": ("text", "NO"),
            "error_code": ("text", "YES"),
            "fence": ("bigint", "NO"),
            "lease_owner": ("text", "YES"),
            "lease_until": ("double precision", "YES"),
            "request_digest": ("text", "NO"),
            "request_json": ("text", "NO"),
            "result_json": ("text", "YES"),
            "state": ("text", "NO"),
            "target_key": ("text", "NO"),
            "updated_at": ("text", "NO"),
        },
        "constraints": [
            "PRIMARY KEY (effect_id)",
            "CHECK (((lease_owner IS NULL) = (lease_until IS NULL)))",
            "CHECK ((fence >= 0))",
            "CHECK ((state = ANY (ARRAY['READY'::text, "
            "'DISPATCHING'::text, 'COMMIT_UNKNOWN'::text, "
            "'CONFIRMED'::text, 'REJECTED'::text])))",
        ],
    },
    "external_operation_journal": {
        "columns": {
            "error_code": ("text", "YES"),
            "expected_external_parent": ("text", "NO"),
            "external_operation_id": ("text", "YES"),
            "operation": ("text", "NO"),
            "operation_key": ("text", "NO"),
            "repository_id": ("text", "NO"),
            "request_digest": ("text", "NO"),
            "result_json": ("text", "YES"),
            "status": ("text", "NO"),
        },
        "constraints": ["PRIMARY KEY (operation_key)"],
    },
    "idempotency_records": {
        "columns": {"key": ("text", "NO"), "request_digest": ("text", "NO"), "result_json": ("text", "NO")},
        "constraints": ["PRIMARY KEY (key)"],
    },
    "object_versions": {
        "columns": {
            "object_id": ("text", "NO"),
            "payload_digest": ("text", "NO"),
            "payload_json": ("text", "NO"),
            "version": ("text", "NO"),
            "version_key": ("text", "NO"),
        },
        "constraints": ["UNIQUE (object_id, version)", "PRIMARY KEY (version_key)"],
    },
    "private_records": {
        "columns": {
            "content_digest": ("text", "NO"),
            "content_json": ("text", "YES"),
            "created_at": ("text", "NO"),
            "deleted_at": ("text", "YES"),
            "deletion_reason": ("text", "YES"),
            "expires_at": ("text", "NO"),
            "owner_id": ("text", "NO"),
            "record_id": ("text", "NO"),
            "scope_ref": ("text", "NO"),
        },
        "constraints": [
            "PRIMARY KEY (record_id)",
            "CHECK ((((content_json IS NOT NULL) AND (deleted_at IS NULL) "
            "AND (deletion_reason IS NULL)) OR ((content_json IS NULL) "
            "AND (deleted_at IS NOT NULL) AND (deletion_reason IS NOT "
            "NULL))))",
        ],
    },
    "source_checkpoints": {
        "columns": {
            "connector_id": ("text", "NO"),
            "cursor": ("text", "YES"),
            "fence": ("bigint", "NO"),
            "lease_owner": ("text", "YES"),
            "lease_until": ("double precision", "YES"),
            "revision": ("bigint", "NO"),
        },
        "constraints": [
            "PRIMARY KEY (connector_id)",
            "CHECK (((lease_owner IS NULL) = (lease_until IS NULL)))",
            "CHECK ((fence >= 0))",
            "CHECK ((revision >= 0))",
        ],
    },
    "store_metadata": {
        "columns": {
            "recovery_required": ("integer", "NO"),
            "schema_version": ("integer", "NO"),
            "singleton": ("integer", "NO"),
            "tenant_id": ("text", "YES"),
        },
        "constraints": [
            "PRIMARY KEY (singleton)",
            "CHECK ((singleton = 1))",
            "CHECK ((recovery_required = ANY (ARRAY[0, 1])))",
        ],
    },
    "target_barriers": {
        "columns": {"effect_id": ("text", "NO"), "target_key": ("text", "NO")},
        "constraints": [
            "UNIQUE (effect_id)",
            "PRIMARY KEY (target_key)",
            "FOREIGN KEY(effect_id) REFERENCES effect_intents (effect_id)",
        ],
    },
    "version_states": {
        "columns": {"state": ("text", "NO"), "version_key": ("text", "NO")},
        "constraints": [
            "FOREIGN KEY(version_key) REFERENCES object_versions (version_key) DEFERRABLE",
            "PRIMARY KEY (version_key)",
        ],
    },
}
