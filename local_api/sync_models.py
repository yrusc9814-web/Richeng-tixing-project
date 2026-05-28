"""Phase 4.4 — Pydantic models for sync state and sync log CRUD."""

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .config import (
    ALLOWED_SYNC_TARGETS,
    ALLOWED_SYNC_STATUSES,
    ALLOWED_SYNC_RESULTS,
)


# ── Sync State Models ───────────────────────────────────────────────────────


class SyncStateCreateRequest(BaseModel):
    """Request body for POST /api/sync/state"""
    task_id: str = Field(..., min_length=1)
    sync_target: str = Field(...)
    external_id: Optional[str] = Field(default=None)
    sync_status: str = Field(default="pending")
    payload_hash: Optional[str] = Field(default=None)

    @field_validator("sync_target")
    @classmethod
    def check_sync_target(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_SYNC_TARGETS:
            raise ValueError(f"sync_target must be one of {sorted(ALLOWED_SYNC_TARGETS)}")
        return v

    @field_validator("sync_status")
    @classmethod
    def check_sync_status(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_SYNC_STATUSES:
            raise ValueError(f"sync_status must be one of {sorted(ALLOWED_SYNC_STATUSES)}")
        return v


class SyncStateUpdateRequest(BaseModel):
    """Request body for PATCH /api/sync/state/{sync_id}"""
    sync_status: Optional[str] = Field(default=None)
    sync_version: Optional[int] = Field(default=None, ge=1)
    external_id: Optional[str] = Field(default=None)
    payload_hash: Optional[str] = Field(default=None)
    last_synced_at: Optional[str] = Field(default=None)
    last_sync_trigger: Optional[str] = Field(default=None)

    @field_validator("sync_status")
    @classmethod
    def check_sync_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in ALLOWED_SYNC_STATUSES:
            raise ValueError(f"sync_status must be one of {sorted(ALLOWED_SYNC_STATUSES)}")
        return v


class SyncStateResponse(BaseModel):
    """Response model for sync_state records."""
    sync_id: str
    task_id: str
    sync_target: str
    sync_key: str
    payload_hash: Optional[str] = None
    sync_status: str
    sync_version: int = 1
    external_id: Optional[str] = None
    last_synced_at: Optional[str] = None
    last_sync_trigger: Optional[str] = None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class SyncStateListResponse(BaseModel):
    """Wrapper for listing sync_state records."""
    items: list[SyncStateResponse]
    total: int


# ── Sync Log Models ─────────────────────────────────────────────────────────


class SyncLogCreateRequest(BaseModel):
    """Request body for POST /api/sync/logs"""
    sync_id: str = Field(..., min_length=1)
    local_task_id: Optional[str] = Field(default=None)
    sync_target: str = Field(...)
    sync_attempt: int = Field(default=1, ge=1)
    sync_result: str = Field(...)
    error_code: Optional[str] = Field(default=None)
    error_message: Optional[str] = Field(default=None)
    drift_detected: bool = Field(default=False)
    drift_fields: Optional[str] = Field(default=None)
    payload_hash_before: Optional[str] = Field(default=None)
    payload_hash_after: Optional[str] = Field(default=None)
    external_id_before: Optional[str] = Field(default=None)
    external_id_after: Optional[str] = Field(default=None)
    request_id: Optional[str] = Field(default=None)
    triggered_by: str = Field(default="api")

    @field_validator("sync_target")
    @classmethod
    def check_sync_target(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_SYNC_TARGETS:
            raise ValueError(f"sync_target must be one of {sorted(ALLOWED_SYNC_TARGETS)}")
        return v

    @field_validator("sync_result")
    @classmethod
    def check_sync_result(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_SYNC_RESULTS:
            raise ValueError(f"sync_result must be one of {sorted(ALLOWED_SYNC_RESULTS)}")
        return v


class SyncLogResponse(BaseModel):
    """Response model for sync_logs records."""
    log_id: str
    sync_id: str
    local_task_id: Optional[str] = None
    sync_target: str
    sync_attempt: int = 1
    sync_result: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    drift_detected: bool = False
    drift_fields: Optional[str] = None
    payload_hash_before: Optional[str] = None
    payload_hash_after: Optional[str] = None
    external_id_before: Optional[str] = None
    external_id_after: Optional[str] = None
    request_id: Optional[str] = None
    triggered_by: str = "api"
    created_at: str

    model_config = {"from_attributes": True}


class SyncLogListResponse(BaseModel):
    """Wrapper for listing sync_logs records."""
    items: list[SyncLogResponse]
    total: int
