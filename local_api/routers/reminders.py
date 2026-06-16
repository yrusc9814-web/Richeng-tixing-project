"""Phase59/60/61 local-safe status and import routes."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..database import get_db
from ..services.reminder_policy import generate_reminders_for_task, reminder_summary
from ..services.weather_rules import evaluate_weather_for_task, store_forecast, weather_overview
from ..services.apple_import_service import import_apple_snapshots

router = APIRouter(prefix="/api/local", tags=["local-safe-phase58-61"])


class AppleSnapshotImportRequest(BaseModel):
    events: list[dict] = Field(default_factory=list)


class WeatherForecastRequest(BaseModel):
    location: str
    forecast_time: str
    temperature: Optional[float] = None
    condition: Optional[str] = None
    rain_probability: Optional[float] = None
    wind_level: Optional[str] = None
    aqi: Optional[int] = None
    raw_payload: Optional[dict] = None
    expires_at: Optional[str] = None


def _task_or_404(task_id: str) -> dict:
    row = get_db().execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    data = dict(row)
    data["weather_sensitive"] = bool(data.get("weather_sensitive"))
    return data


@router.post("/reminders/tasks/{task_id}/generate")
def generate_task_reminders(task_id: str):
    return {"actions": generate_reminders_for_task(_task_or_404(task_id))}


@router.post("/weather/forecasts")
def create_weather_forecast(body: WeatherForecastRequest):
    return store_forecast(
        body.location,
        body.forecast_time,
        temperature=body.temperature,
        condition=body.condition,
        rain_probability=body.rain_probability,
        wind_level=body.wind_level,
        aqi=body.aqi,
        raw_payload=body.raw_payload,
        expires_at=body.expires_at,
    )


@router.post("/weather/tasks/{task_id}/evaluate")
def evaluate_task_weather(task_id: str):
    return evaluate_weather_for_task(_task_or_404(task_id))


@router.get("/reminders/summary")
def get_reminder_summary():
    return reminder_summary()


@router.get("/weather/overview")
def get_weather_overview():
    return weather_overview()


@router.post("/apple/import-snapshots")
def import_apple_events(body: AppleSnapshotImportRequest):
    return import_apple_snapshots(body.events)
