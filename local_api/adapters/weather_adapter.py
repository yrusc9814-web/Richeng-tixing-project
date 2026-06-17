"""Weather provider adapters.

Default execution path uses real HTTP weather providers when configured.
MockWeatherAdapter remains available only for legacy tests/imports and is not
used by runtime reminder evaluation.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from .base import AdapterResult, SyncAdapter


class WeatherProviderError(RuntimeError):
    """Raised when a real weather provider cannot return usable data."""


@dataclass
class WeatherProviderResult:
    provider: str
    location: str
    forecast_time: str
    temperature: Optional[float]
    condition: Optional[str]
    rain_probability: Optional[float]
    wind_level: Optional[str]
    aqi: Optional[int]
    raw_payload: dict
    request_id: str


class RealWeatherProvider:
    """Fetch weather data from real HTTP APIs.

    Provider order:
    1. QWeather/和风天气 when QWEATHER_API_KEY or HEFENG_WEATHER_KEY is set.
    2. Open-Meteo geocoding + forecast when no API key is configured.
    """

    def fetch(self, location: str, forecast_time: Optional[str] = None) -> WeatherProviderResult:
        key = os.environ.get("QWEATHER_API_KEY") or os.environ.get("HEFENG_WEATHER_KEY")
        if key:
            try:
                return self._fetch_qweather(location, key, forecast_time)
            except WeatherProviderError:
                return self._fetch_open_meteo(location, forecast_time)
        return self._fetch_open_meteo(location, forecast_time)

    def _fetch_qweather(self, location: str, key: str, forecast_time: Optional[str]) -> WeatherProviderResult:
        location_query = urllib.parse.urlencode({"location": location, "key": key})
        lookup_url = f"https://geoapi.qweather.com/v2/city/lookup?{location_query}"
        lookup_payload = self._get_json(lookup_url)
        locations = lookup_payload.get("location") or []
        if not locations:
            raise WeatherProviderError(f"QWeather location lookup returned no match for {location!r}")
        location_id = locations[0]["id"]

        weather_query = urllib.parse.urlencode({"location": location_id, "key": key})
        weather_url = f"https://devapi.qweather.com/v7/weather/now?{weather_query}"
        weather_payload = self._get_json(weather_url)
        now = weather_payload.get("now") or {}
        if not now:
            raise WeatherProviderError("QWeather now endpoint returned no weather payload")

        request_id = weather_payload.get("fxLink") or self._request_id(weather_payload)
        return WeatherProviderResult(
            provider="qweather",
            location=locations[0].get("name") or location,
            forecast_time=forecast_time or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            temperature=self._float_or_none(now.get("temp")),
            condition=now.get("text"),
            rain_probability=None,
            wind_level=now.get("windScale"),
            aqi=None,
            raw_payload={"lookup": self._redact_key(lookup_payload), "weather": self._redact_key(weather_payload)},
            request_id=request_id,
        )

    def _fetch_open_meteo(self, location: str, forecast_time: Optional[str]) -> WeatherProviderResult:
        place = self._resolve_open_meteo_place(location)

        forecast_query = urllib.parse.urlencode({
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "timezone": "Asia/Shanghai",
        })
        forecast_url = f"https://api.open-meteo.com/v1/forecast?{forecast_query}"
        forecast_payload = self._get_json(forecast_url)
        current = forecast_payload.get("current") or {}
        if not current:
            raise WeatherProviderError("Open-Meteo forecast returned no current weather payload")

        wind_speed = self._float_or_none(current.get("wind_speed_10m"))
        raw_payload = {"place": place, "forecast": forecast_payload}
        return WeatherProviderResult(
            provider="open_meteo",
            location=place.get("name") or location,
            forecast_time=forecast_time or current.get("time") or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            temperature=self._float_or_none(current.get("temperature_2m")),
            condition=f"weather_code:{current.get('weather_code')}",
            rain_probability=None,
            wind_level="大风" if wind_speed is not None and wind_speed >= 39 else None,
            aqi=None,
            raw_payload=raw_payload,
            request_id=self._request_id(raw_payload),
        )

    def _resolve_open_meteo_place(self, location: str) -> dict:
        local_places = {
            "厦门": {"name": "厦门", "latitude": 24.4798, "longitude": 118.0894, "source": "local_geocode"},
            "xiamen": {"name": "Xiamen", "latitude": 24.4798, "longitude": 118.0894, "source": "local_geocode"},
        }
        normalized = location.strip().lower()
        if normalized in local_places:
            return local_places[normalized]
        geo_query = urllib.parse.urlencode({"name": location, "count": 1, "language": "zh", "format": "json"})
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?{geo_query}"
        geo_payload = self._get_json(geo_url)
        results = geo_payload.get("results") or []
        if not results:
            raise WeatherProviderError(f"Open-Meteo geocoding returned no match for {location!r}")
        place = dict(results[0])
        place["source"] = "open_meteo_geocoding"
        return place

    @staticmethod
    def _get_json(url: str) -> dict:
        try:
            proc = subprocess.run(
                ["curl", "-ksS", "--max-time", "20", "-H", "User-Agent: LifeSync/1.0", url],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return json.loads(proc.stdout)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        req = urllib.request.Request(url, headers={"User-Agent": "LifeSync/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise WeatherProviderError(str(exc)) from exc

    @staticmethod
    def _float_or_none(value) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _request_id(payload: dict) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _redact_key(payload: dict) -> dict:
        text = json.dumps(payload, ensure_ascii=False)
        for key in (os.environ.get("QWEATHER_API_KEY"), os.environ.get("HEFENG_WEATHER_KEY")):
            if key:
                text = text.replace(key, "***")
        return json.loads(text)


class MockWeatherAdapter(SyncAdapter):
    """Legacy mock adapter for tests only; not used by real weather evaluation."""

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
