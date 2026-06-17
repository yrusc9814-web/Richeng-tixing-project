"""Phase60 — real weather cache and evaluator."""

import json
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from ..adapters.weather_adapter import RealWeatherProvider, WeatherProviderError
from ..database import get_db
from .reminder_policy import _insert_log


def _iso_now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def _weather_id() -> str:
    return f"weather_{int(time.time() * 1000)}_{secrets.token_hex(3)}"


def store_forecast(location: str, forecast_time: str, *, temperature: Optional[float] = None,
                   condition: Optional[str] = None, rain_probability: Optional[float] = None,
                   wind_level: Optional[str] = None, aqi: Optional[int] = None,
                   raw_payload: Optional[dict] = None, expires_at: Optional[str] = None) -> dict:
    conn = get_db()
    now = _iso_now()
    expires = expires_at or (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(hours=6)).isoformat()
    weather_id = _weather_id()
    conn.execute(
        """INSERT INTO weather_cache (
            id, location, forecast_time, temperature, condition, rain_probability,
            wind_level, aqi, raw_payload, fetched_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (weather_id, location, forecast_time, temperature, condition, rain_probability,
         wind_level, aqi, json.dumps(raw_payload or {}, ensure_ascii=False), now, expires),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM weather_cache WHERE id = ?", (weather_id,)).fetchone())


def fetch_and_store_real_forecast(location: str, forecast_time: Optional[str] = None) -> dict:
    """Fetch real weather data over HTTP and persist it to weather_cache."""
    result = RealWeatherProvider().fetch(location, forecast_time)
    raw_payload = dict(result.raw_payload)
    raw_payload["provider"] = result.provider
    raw_payload["request_id"] = result.request_id
    return store_forecast(
        result.location,
        result.forecast_time,
        temperature=result.temperature,
        condition=result.condition,
        rain_probability=result.rain_probability,
        wind_level=result.wind_level,
        aqi=result.aqi,
        raw_payload=raw_payload,
    )


def _latest_forecast(location: str) -> Optional[dict]:
    row = get_db().execute(
        """SELECT * FROM weather_cache
           WHERE location = ? AND expires_at >= ?
           ORDER BY forecast_time DESC, fetched_at DESC LIMIT 1""",
        (location, _iso_now()),
    ).fetchone()
    return dict(row) if row else None


def is_abnormal_weather(forecast: dict) -> bool:
    condition = (forecast.get("condition") or "").lower()
    rain = forecast.get("rain_probability") or 0
    aqi = forecast.get("aqi") or 0
    wind = str(forecast.get("wind_level") or "")
    return any(word in condition for word in ("storm", "暴雨", "雨", "snow", "台风")) or rain >= 0.6 or aqi >= 150 or wind in {"7", "8", "9", "10", "大风"}


def evaluate_weather_for_task(task: dict, *, fetch_real: bool = True) -> dict:
    if not task.get("weather_sensitive"):
        return {"triggered": False, "reason": "not_weather_sensitive", "mock": False}
    location = task.get("location")
    if not location:
        return {"triggered": False, "reason": "no_location", "mock": False}

    forecast = _latest_forecast(location)
    fetch_evidence = None
    if fetch_real and not forecast:
        try:
            forecast = fetch_and_store_real_forecast(location, task.get("start_time") or task.get("due_time"))
            raw_payload = json.loads(forecast.get("raw_payload") or "{}")
            fetch_evidence = {
                "provider": raw_payload.get("provider"),
                "request_id": raw_payload.get("request_id"),
                "cache_id": forecast.get("id"),
            }
        except WeatherProviderError as exc:
            if not forecast:
                return {"triggered": False, "reason": "weather_provider_error", "error": str(exc), "mock": False}
            fetch_evidence = {"provider_error": str(exc), "cache_id": forecast.get("id")}

    if not forecast:
        return {"triggered": False, "reason": "no_forecast", "mock": False}
    if not is_abnormal_weather(forecast):
        return {"triggered": False, "reason": "normal_weather", "forecast": forecast, "fetch": fetch_evidence, "mock": False}
    payload = {"task_id": task["task_id"], "channel": "wechat", "trigger": "weather", "forecast_id": forecast["id"]}
    log = _insert_log(task["task_id"], "wechat", "weather", task.get("start_time") or task.get("due_time"), payload)
    return {"triggered": True, "forecast": forecast, "notification": log, "fetch": fetch_evidence, "mock": False}


def weather_overview() -> dict:
    conn = get_db()
    row = conn.execute("SELECT COUNT(*) AS cnt FROM weather_cache WHERE expires_at >= ?", (_iso_now(),)).fetchone()
    latest = conn.execute("SELECT location, condition, rain_probability, aqi, raw_payload FROM weather_cache ORDER BY fetched_at DESC LIMIT 1").fetchone()
    return {"active_forecasts": row["cnt"], "latest": dict(latest) if latest else None, "mock": False}
