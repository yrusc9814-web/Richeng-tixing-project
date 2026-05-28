"""Phase 4.4 — Payload hashing utilities for sync drift detection."""

import hashlib
import json
from typing import Any


def compute_payload_hash(task_data: dict[str, Any]) -> str:
    """Compute a SHA-256 hash of the task payload for drift detection.

    Normalises the input by sorting keys and serialising with sorted keys
    so that semantically identical payloads always produce the same hash.
    """
    normalised = json.dumps(task_data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def extract_task_payload(task_row: dict[str, Any]) -> dict[str, Any]:
    """Extract the sync-relevant fields from a full task row.

    Returns a subset of fields used to determine whether the task
    has changed since the last sync.
    """
    return {
        "task_id": task_row.get("task_id"),
        "title": task_row.get("title"),
        "description": task_row.get("description"),
        "priority": task_row.get("priority"),
        "status": task_row.get("status"),
        "start_time": task_row.get("start_time"),
        "due_time": task_row.get("due_time"),
        "timezone": task_row.get("timezone"),
        "location": task_row.get("location"),
    }
