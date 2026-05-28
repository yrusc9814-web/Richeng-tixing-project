"""Phase 8A — Base adapter abstract class and result type."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AdapterResult:
    """Result of a single adapter push call.

    Fields:
        success: Whether the push succeeded.
        external_id: External system's ID (Apple EKEvent ID, etc.).
        sync_attempt: Attempt number (default 1).
        error_code: Machine-readable error code ("network", "auth_failed", etc.).
        error_message: Human-readable error description.
        sync_result: Value for sync_log.sync_result — must be one of
                     ALLOWED_SYNC_RESULTS ("success", "skipped", "failed", "drift_detected").
    """
    success: bool = False
    external_id: Optional[str] = None
    sync_attempt: int = 1
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    sync_result: str = "success"

    def __post_init__(self):
        _ALLOWED = {"success", "skipped", "failed", "drift_detected"}
        if self.sync_result not in _ALLOWED:
            raise ValueError(
                f"sync_result must be one of {_ALLOWED}, got: {self.sync_result}"
            )


class SyncAdapter(ABC):
    """Abstract base class for external sync adapters."""

    @property
    @abstractmethod
    def target_name(self) -> str:
        """Return the sync_target identifier (e.g. 'apple_calendar', 'weather')."""
        ...

    @abstractmethod
    def validate_config(self) -> tuple[bool, Optional[str]]:
        """Validate adapter configuration. Returns (is_valid, error_message)."""
        ...

    @abstractmethod
    def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        """Push a task to the external system (create or update)."""
        ...

    @abstractmethod
    def pull(self, external_id: str) -> Optional[dict]:
        """Pull a record from the external system (for drift detection).
        Returns None if the record no longer exists externally.
        """
        ...
