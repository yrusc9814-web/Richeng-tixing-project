"""Phase 4.3 — Pydantic models for request validation and response serialization."""

from datetime import datetime
from typing import Optional, Annotated
from pydantic import BaseModel, Field, field_validator

from .config import (
    ALLOWED_PRIORITIES,
    ALLOWED_STATUSES,
    ALLOWED_CHANNELS,
    ALLOWED_TIMEZONES,
)

# ── Request Models ────────────────────────────────────────────────────────


class TaskCreateRequest(BaseModel):
    """Request body for POST /api/tasks"""
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    priority: str = Field(default="P2")
    status: str = Field(default="pending")
    start_time: Optional[str] = Field(default=None)  # ISO-8601 string
    due_time: Optional[str] = Field(default=None)     # ISO-8601 string
    timezone: str = Field(default="Asia/Shanghai")
    location: Optional[str] = Field(default=None, max_length=500)
    need_weather_check: bool = Field(default=False)
    reminder_channels: list[str] = Field(default=["local_ui"])
    created_channel: str = Field(default="api_test")

    @field_validator("priority")
    @classmethod
    def check_priority(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in ALLOWED_PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(ALLOWED_PRIORITIES)}")
        return v

    @field_validator("status", mode="after")
    @classmethod
    def check_status(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_STATUSES:
            raise ValueError(f"status must be one of {sorted(ALLOWED_STATUSES)}")
        return v

    @field_validator("created_channel")
    @classmethod
    def check_channel(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_CHANNELS:
            raise ValueError(f"created_channel must be one of {sorted(ALLOWED_CHANNELS)}")
        return v

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, v: str) -> str:
        if v not in ALLOWED_TIMEZONES:
            # Allow any IANA timezone that follows the Region/City pattern
            parts = v.split("/")
            if len(parts) != 2 or not all(p and p[0].isalpha() for p in parts):
                raise ValueError(f"timezone must be a valid IANA timezone identifier (e.g., Asia/Shanghai)")
        return v

    @field_validator("start_time", "due_time")
    @classmethod
    def check_iso_datetime(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            # Accept ISO-8601 — both with and without trailing Z/timezone offset
            v_clean = v.strip().replace(" ", "T")
            datetime.fromisoformat(v_clean)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid ISO-8601 datetime: {v}")
        return v


class TaskUpdateRequest(BaseModel):
    """Request body for PATCH /api/tasks/{task_id}"""
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    priority: Optional[str] = Field(default=None)
    status: Optional[str] = Field(default=None)
    start_time: Optional[str] = Field(default=None)
    due_time: Optional[str] = Field(default=None)
    timezone: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None, max_length=500)
    need_weather_check: Optional[bool] = Field(default=None)
    reminder_channels: Optional[list[str]] = Field(default=None)
    created_channel: Optional[str] = Field(default=None)

    @field_validator("priority")
    @classmethod
    def check_priority(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().upper()
        if v not in ALLOWED_PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(ALLOWED_PRIORITIES)}")
        return v

    @field_validator("status", mode="after")
    @classmethod
    def check_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in ALLOWED_STATUSES:
            raise ValueError(f"status must be one of {sorted(ALLOWED_STATUSES)}")
        return v

    @field_validator("created_channel")
    @classmethod
    def check_channel(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in ALLOWED_CHANNELS:
            raise ValueError(f"created_channel must be one of {sorted(ALLOWED_CHANNELS)}")
        return v

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALLOWED_TIMEZONES:
            parts = v.split("/")
            if len(parts) != 2 or not all(p and p[0].isalpha() for p in parts):
                raise ValueError(f"timezone must be a valid IANA timezone identifier (e.g., Asia/Shanghai)")
        return v

    @field_validator("start_time", "due_time")
    @classmethod
    def check_iso_datetime(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            v_clean = v.strip().replace(" ", "T")
            datetime.fromisoformat(v_clean)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid ISO-8601 datetime: {v}")
        return v


# ── Response Model ─────────────────────────────────────────────────────────


class TaskResponse(BaseModel):
    """15-field response — serialised for all task endpoints."""
    task_id: str
    title: str
    description: Optional[str] = None
    priority: str
    status: str
    start_time: Optional[str] = None
    due_time: Optional[str] = None
    timezone: str
    location: Optional[str] = None
    need_weather_check: bool = False
    reminder_channels: list = ["local_ui"]
    created_channel: str
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    """Wrapper for GET /api/tasks with pagination metadata."""
    tasks: list[TaskResponse]
    total: int
    limit: int
    offset: int


# ── System Status Model ────────────────────────────────────────────────────


class SystemStatusResponse(BaseModel):
    status: str
    api_version: str
    db_connected: bool
    task_count: int


# ── Sync Engine Models ─────────────────────────────────────────────────────


class SyncEngineStatusResponse(BaseModel):
    running: bool
    status: str
    scan_count: int
    last_scan_at: Optional[str] = None
    queued_pending: int


class SyncEngineStatsResponse(BaseModel):
    total_scans: int
    total_processed: int
    success_count: int
    failed_count: int
    success_rate: float
    failure_distribution: dict[str, int]
