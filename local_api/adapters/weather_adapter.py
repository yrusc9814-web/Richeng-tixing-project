"""Phase 8A — Mock Weather adapter.

This adapter handles sync_target 'weather'. Returns inline weather
results rather than creating external records.
"""

from __future__ import annotations

from typing import Optional

from .base import AdapterResult, SyncAdapter


class MockWeatherAdapter(SyncAdapter):
    """Mock adapter for weather sync.

    Always succeeds. Returns fixed external_id 'weather_inline'.
    """

    @property
    def target_name(self) -> str:
        return "weather"

    def validate_config(self) -> tuple[bool, Optional[str]]:
        return (True, None)

    def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        return AdapterResult(
            success=True,
            external_id="weather_inline",
            sync_result="success",
        )

    def pull(self, external_id: str) -> Optional[dict]:
        return {
            "external_id": external_id,
            "source": "weather",
            "temp": 25,
            "condition": "sunny",
            "humidity": 60,
        }
