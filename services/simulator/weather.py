from __future__ import annotations

import asyncio
import logging
import math
from bisect import bisect_left
from datetime import datetime,timedelta, timezone
import numpy as np
import httpx
from pydantic import BaseModel

from config import get_settings

log = logging.getLogger(__name__)

#API'den istenilecek saatlik degiskenler
HOURLY_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "shortwave_radiation",
    "cloud_cover",
    "is_day"
)

class HourlyPoint(BaseModel):
    """API'den gelen tek bir saatlik veri noktasi"""

    ts_utc: datetime
    temp_c: float
    humidity_pct: float
    radiation_w_per_m2: float
    cloud_cover_pct: float
    is_day:bool

class WeatherSnapshot(BaseModel):
    """Belirli bir ana ait, interpolasyonla uretilmis anlik kosullar"""

    ts_utc: datetime
    temp_c: float
    humidity_pct: float
    radiation_w_per_m2: float
    cloud_cover_pct: float
    is_day: bool
    source: str

class WeatherProvider:
    """"Saatte bir API'den ceker, her tick'te bellekten interpolasyonla okutur."""

    def __init__(self) -> None:
        self._s = get_settings() #ayarlar bir kez alinip saklanir.
        self._points: list[HourlyPoint] = []
        self._last_success_utc: datetime | None = None

        self._x: np.ndarray = np.empty(0, dtype=float)
        self._y_temp: np.ndarray= np.empty(0, dtype=float)
        self._y_hum: np.ndarray= np.empty(0, dtype=float)
        self._y_rad: np.ndarray= np.empty(0, dtype=float)
        self._y_cloud: np.ndarray= np.empty(0, dtype=float)
        self._y_day: np.ndarray= np.empty(0, dtype=float)

    # Okuma tarafi

    def current(self, now_utc:datetime | None = None) -> WeatherSnapshot:
        """Verilen ana ait kosullari dondurur. Ag beklemez, bu yuzden Async degil."""

        now = now_utc or datetime.now(timezone.utc)
        point = self._interpolate(now) #onbellekten interpolasyonla o ana ait degeri uret

        if point is None: #ilk acilista hic veri yok
            if not self._s.weather_fallback_enabled: #sentetik veriyle calismak da kabul edilmediyse
                raise RuntimeError(
                    "Hava verisi yok ve yedek profil kapali"
                    "(WEATHER_FALLBACK_ENABLED=false)"
                )
            point = self._synthetic(now)
            source = "fallback"
        else:
            source = self._freshness()
        
        return WeatherSnapshot(
            ts_utc=now,
            temp_c=point.temp_c,
            humidity_pct=point.humidity_pct,
            radiation_w_per_m2=point.radiation_w_per_m2,
            cloud_cover_pct=point.cloud_cover_pct,
            is_day=point.is_day,
            source=source,   
        )
    
    def _freshness(self) -> str:
        """Veri yeni mi, eski mi - 'open-meteo' veya cached dondurur"""
        if self._last_success_utc is None:
            return "fallback"
        age = datetime.now(timezone.utc) - self._last_success_utc
        stale_after = timedelta(seconds=self._s.weather_fetch_interval_seconds*2)
        return "cached" if age > stale_after else "open-meteo"
    
    def _interpolate(self, now: datetime) -> HourlyPoint | None:
        """Iki saatlik nokta arasinda dogrusal interpolasyon yapar(np.interp ile)"""
        if self._x.size == 0: #onbellek bos, cagiran fallbacke duser
            return None
        
        t = now.timestamp()

        return HourlyPoint(
            ts_utc=now,
            temp_c=float(np.interp(t, self._x, self._y_temp)),
            humidity_pct=float(np.interp(t, self._x, self._y_hum)),
            # Işınımı yine kırpıyoruz: girdide negatif bir artefakt varsa taşınmasın
            radiation_w_per_m2=max(0.0, float(np.interp(t, self._x, self._y_rad))),
            cloud_cover_pct=float(np.interp(t, self._x, self._y_cloud)),
            # Boolean'ı 0/1 olarak interpolasyona sokup 0.5 eşiğiyle geri çeviriyoruz.
            # "Yarı gündüz" diye bir şey olmadığı için eşikleme doğru olan.
            is_day=bool(np.interp(t, self._x, self._y_day) >= 0.5),            
        )
    
    async def run_forever(self) -> None:
        """Arka plan gorevi: periyodik olarak yeniler, main.py bunu task olarak baslatir."""
        while True:
            await self.refresh()

    async def refresh(self) -> bool:
        """Tek bir yenileme turu, basariliysa true"""
        params = {
            "latitude" : self._s.site_latitude,
            "longitude" : self._s.site_longitude,
            "hourly" : ",".join(HOURLY_VARIABLES),
            "timezone":"UTC",
            "past_days" : 1,
            "forecast_days": min(
                16,max(1,math.ceil(self._s.weather_forecast_hours/24))
            ),
        }

        for attempt in range(self._s.weather_max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self._s.weather_http_timeout_seconds
                ) as client:
                    resp = await client.get(self._s.weather_api_url, params= params)
                    resp.raise_for_status()
                    payload = resp.json()

                points = self._parse(payload)
                if not points:
                    raise ValueError("API yanitinda saatlik veri yok")
                
                self._points = points
                self._rebuild_arrays(points)
                self._last_success_utc = datetime.now(timezone.utc)
                log.info(
                    "Hava verisi guncellendi: %d saatlik nokta, %s .. %s",
                    len(points),
                    points[0].ts_utc.isoformat(),
                    points[-1].ts_utc.isoformat()
                )
                return True
            except Exception as exc:
                wait = 2**attempt
                is_last = attempt == self._s.weather_max_retries
                log.warning(
                    "Hava verisi cekilemedi (deneme %d/%d): %s%s",
                    attempt + 1,
                    self._s.weather_max_retries + 1,
                    exc,
                    ""if is_last else f" - {wait} s sonra tekrar "
                )
                if not is_last:
                    await asyncio.sleep(wait)

        if self._points:
            log.warning("son bilinen hava verisiyle devam ediliyor (stale)")
        else:
            log.warning("hic hava verisi yok, sentetik profil kullanilacak.")
        return False
    
    def _rebuild_arrays(self, points: list[HourlyPoint]) -> None:
        """Interpolasyon dizilerini yeniden kurar. Yalnizca basarili yenilemede cagrilir. """
        self._x = np.array([p.ts_utc.timestamp() for p in points], dtype=float)
        self._y_temp = np.array([p.temp_c for p in points], dtype= float)
        self._y_hum = np.array([p.humidity_pct for p in points], dtype=float)
        self._y_rad = np.array([p.radiation_w_per_m2 for p in points], dtype=float)
        self._y_cloud = np.array([p.cloud_cover_pct for p in points], dtype=float)
        self._y_day = np.array([1.0 if p.is_day else 0.0 for p in points], dtype=float)

    @staticmethod
    def _parse(payload: dict) -> list[HourlyPoint]:
        """Open-Meteo yanitini HourlyPoint listesine cevirir."""

        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []

        def col(name: str, default: float = 0.0) -> list[float]:
            """Bir degisken kolonunu guvenli sekilde float listesine cevirir"""
            values = hourly.get(name) or []
            return [
                default if v is None else float(v)
                for v in (values + [default] * (len(times) - len(values)))
            ]
        
        temps = col("temperature_2m", 20.0)
        hums = col("relative_humidity_2m", 50.0)
        rads = col("shortwave_radiation", 0.0)
        clouds = col("cloud_cover", 0.0)
        days = col("is_day", 0.0)

        points: list[HourlyPoint] = []
        for i, raw_ts in enumerate(times):
            ts = datetime.fromisoformat(raw_ts)
            if ts.tzinfo is None:
                #open-meteo zaman dilimi eki olmadan donduruyor. timezone = UTC olsun
                ts = ts.replace(tzinfo=timezone.utc)
            points.append(
                HourlyPoint(
                    ts_utc=ts,
                    temp_c=temps[i],
                    humidity_pct= hums[i],
                    radiation_w_per_m2= max(0.0, rads[i]),
                    cloud_cover_pct= clouds[i],
                    is_day=days[i]>=0.5
                )
            )
        
        points.sort(key=lambda p: p.ts_utc)
        return points
    
    #yedek profil
    def _synthetic(self,ts: datetime) -> HourlyPoint:
        """Internet olmadigi zaman kullanilan fiziksek olarak makul sentetik profil"""
        hour = ts.hour + ts.minute / 60.0

        mean_c, amplitude_c = 24.0, 7.0
        temp = mean_c + amplitude_c* math.sin(2*math.pi*(hour - 9.0) / 24.0)

        if 6.0 <= hour <= 18.0:
            radiation = 850.0 * math.sin(math.pi * (hour -6.0) / 12.0)
        else:
            radiation = 0.5
        
        humidity = max(25.0,min(80.0,100.0-2.0*(temp-10.0)))

        return HourlyPoint(
            ts_utc= ts,
            temp_c= round(temp,2),
            humidity_pct=round(humidity,1),
            radiation_w_per_m2=round(max(0.0,radiation),1),
            cloud_cover_pct=20.0,
            is_day=6.0 <= hour < 20.0
        )