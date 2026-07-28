"""MQTT mesaj semalari - sistemin veri sozlesmesi. """

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

def utc_now() -> datetime:
    """Her zaman zaman dilimi bilgisi tasiyan UTC damgasi"""
    return datetime.now(timezone.utc)

class CommandReason(str, Enum):
    """Bir Komutun hangi amacla uretildigi, Audit log ve arayuz bunu kullanir"""
    PEAK_SHAVING = "PEAK_SHAVING"
    OCCUPANCY_SETBACK = "OCCUPANCY_SETBACK"
    VENTILATION_DEMAND = "VENTILATION_DEMAND"
    DAYLIGHT_HARVESTING = "DAYLIGHT_HARVESTING"
    PRE_COOLING = "PRE_COOLING"
    FREE_COOLING = "FREE_COOLING"
    RELEASE = "RELEASE"
    MANUAL = "MANUAL"


# giden: telemetri

class RoomTelemetry(BaseModel):
    """Bir ofis odasinin anlik durumu. Simulator uretir, cekirdek motor tuketir."""

    model_config = ConfigDict(extra="forbid")

    room_id: int = Field(ge=1)
    ts_utc: datetime

    temp_c: float = Field(ge=-20.0, le=60.0)
    setpoint_c: float = Field(ge=10.0, le=35.0)
    co2_ppm : float = Field(ge=300.0, le=5000.0)
    occupancy : int = Field(ge=0.0)

    hvac_kw : float = Field(ge=0.0, description="Cektigi Elektrik Gucu")
    cooling_kw : float = Field(ge=0.0, description="Tasidigi Isi (hvac_kw x COP)")
    valve_pct: float = Field(ge=0.0, le=100.0)
    ventilation_ach : float = Field(ge=0.0, le=10.0)

    internal_gain_w: float = Field(ge=0.0)

class EnvironmentTelemetry(BaseModel):
    """Dis ortam kosullari, tum cihazlar icin ortak"""

    model_config = ConfigDict(extra="forbid")

    ts_utc: datetime
    outdoor_temp_c: float = Field(ge=-60.0, le=70.0)
    relative_humidity_pct : float = Field(ge=0.0, le = 100.0)
    shortwave_radiation_w_per_m2: float = Field(ge=0.0)
    cloud_cover_pct: float = Field(ge=0.0, le=100.0)
    is_day: bool
    source: str = Field(description="open-meteo | fallback | cached")

class DeviceStatus(BaseModel):
    """Cihaz cevrimici/cevrimdisi bildirimi, LWT ile de kullanilir"""
    model_config = ConfigDict(extra="forbid")

    online: bool
    ts_utc: datetime = Field(default_factory=utc_now)

