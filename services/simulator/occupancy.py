"""Doluluk Takvimi: verilen yerel zamana gore odada kac kisi oldugunu dondurur."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from config import Settings, get_settings

RAMP_MINUTES = 45

def _to_minutes(t: time) -> float:
    """time nesnesini gun basindan itibaren dakikaya cevirir."""

    return t.hour * 60.0 + t.minute

def _ramp(x: float, start: float, end: float) -> float:
    """start -> end arasinda 0dan 1e dogrusal gecis"""
    if end <= start:
        return 1.0 if x>= end else 0.0
    return max(0.0,min(1.0,(x-start)/(end-start)))

def _room_phase_minutes(room_id: int) -> float:
     # 2654435761: Knuth'un çarpımsal karıştırma sabiti (2^32 / altın orana yakın asal).
     return float((room_id*2654435761)%41) - 20.0

def occupancy_fraction(
        local_dt:datetime,
        room_id: int = 0,
        settings: Settings | None = None,
) -> float:
    s = settings or get_settings()

    if local_dt.weekday() >= 5:
        return s.occupancy_weekend_factor
    
    minutes = local_dt.hour * 60.0 + local_dt.minute + local_dt.second / 60
    minutes += _room_phase_minutes(room_id)

    start = _to_minutes(s.occupancy_workday_start)
    end = _to_minutes(s.occupancy_workday_end)
    lunch_start = _to_minutes(s.occupancy_lunch_start)
    lunch_end = _to_minutes(s.occupancy_lunch_end)

    if minutes <= start - RAMP_MINUTES or minutes >= end + RAMP_MINUTES:
        return 0.0
    
    morning = _ramp(minutes,start - RAMP_MINUTES,start + RAMP_MINUTES)
    evening = 1.0 - _ramp(minutes, end - RAMP_MINUTES, end + RAMP_MINUTES)

    fraction = min(morning, evening)

    if lunch_start - RAMP_MINUTES < minutes < lunch_end + RAMP_MINUTES:
        into_lunch = _ramp(minutes, lunch_start - RAMP_MINUTES, lunch_start)   # 0→1
        out_of_lunch = _ramp(minutes, lunch_end, lunch_end + RAMP_MINUTES)     # 0→1
        depth = min(into_lunch, 1.0 - out_of_lunch)      

        lunch_multiplier = 1.0 - depth * (1.0 - s.occupancy_lunch_factor)
        fraction *= lunch_multiplier
    
    return max(0.0, min(1.0, fraction))

def occupancy_at(
        local_dt: datetime,
        room_id: int = 0,
        settings: Settings | None = None,
) -> int:
    """"Odadaki kisi sayisi"""
    s = settings or get_settings()

    return round(occupancy_fraction(local_dt, room_id,s)* s.occupancy_max_per_room)

def to_local(utc_dt: datetime, settings: Settings | None = None) -> datetime:
    """Utc damgasi sahanin yerel saatine cevrilir"""
    s = settings or get_settings()
    return utc_dt.astimezone(ZoneInfo(s.site_timezone))