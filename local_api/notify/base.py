"""Phase 15 — Base notification channel abstract class and result type.

Defines the notification/reminder channel abstraction, separate from the
sync adapter layer (Phase 8A). Used for outbound user-facing notifications
such as WeChat reminders, as opposed to external system sync targets.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NotifyResult:
    """Result of a single notification channel send_reminder call.

    Fields:
        success: Whether the notification was sent (or simulated) successfully.
        mode: Operating mode — "dry_run", "test_mode", or "real".
        channel: Channel identifier (e.g. "wechat").
        task_id: The task_id being notified, if available.
        status: Machine-readable status ("skipped", "simulated",
                "not_implemented", "config_error", etc.).
        error_code: Machine-readable error code.
        error_message: Human-readable error description.
    """

    success: bool = False
    mode: str = "real"
    channel: str = ""
    task_id: Optional[str] = None
    status: str = "failed"
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class NotifyChannel(ABC):
    """Abstract base class for notification/reminder channels.

    Unlike SyncAdapter (which syncs data to external systems), NotifyChannel
    sends one-way user-facing notifications — reminders, alerts, etc.
    """

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Return the channel identifier (e.g. 'wechat')."""
        ...

    @abstractmethod
    def send_reminder(self, task_id: str, task_data: dict) -> NotifyResult:
        """Send a reminder notification for the given task.

        Args:
            task_id: Identifier of the task to remind about.
            task_data: Task details as a dict (title, priority, etc.).

        Returns:
            NotifyResult with success/failure and diagnostic fields.
        """
        ...
