"""Bir ofis odasinin durumu, komut tepkisi ve telemetri uretimi.

thermal.py hesap yapiyor, bu sinif ise durum tutuyor: sicakligi ve co2'yi bir adimdan digerine tasiyor,
komutlari uyguluyor, ve olcum gurultusu ekliyor.

"""

from __future__ import annotations

import logging
import random
from collections import deque
from datetime import datetime
from uuid import UUID

import thermal
from config import Settings, get_settings
from models import CommandReason, RoomCommand, RoomTelemetry, utc_now
from occupancy import occupancy_at, occupancy_fraction, to_local
from weather import WeatherSnapshot

log = logging.getLogger(__name__)

class Room:
    """Tek bir ofis odasi: durum + komut tepkisi + telemetri"""

    def __init__(self, room_id: int, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        self.room_id = room_id

        self.indoor_temp_c = self._s.room_initial_temp_c
        self.co2_ppm = self._s.co2_outdoor_ppm
        self.setpoint_c = self._s.room_setpoint_default_c
        self.ventilation_ach = self._s.room_ventilation_ach

        self_last: thermal.ThermalOutputs | None = None

        self._rng = random.Random(room_id)

        self._seen_ids: deque[UUID] = deque(maxlen=self._s.command_id_memory)
        self._seen_set: set[UUID] = set()

        self.applied_command_count = 0
        self.duplicate_command_count = 0

    def apply_command(self, cmd: RoomCommand) -> bool:
        """
        Gelen komutu uygular, duplikasyon ise False dondurur.
        """

        if cmd.command_id in self._seen_set:
            self.duplicate_command_count += 1
            log.debug("oda %d: duplike komut atlandi.(%s)", self.room_id, cmd.command_id)
            return False
        
        if len(self._seen_ids) == self._seen_ids.maxlen:
            self._seen_set.discard(self._seen_ids[0])
        self._seen_ids.append(cmd.command_id)
        self._seen_set.add(cmd.command_id)

        if cmd.desired.setpoint_c is not None:
            self.setpoint_c = max(10.0, min(35.0, cmd.desired.setpoint_c))

        if cmd.desired.ventilation_ach is not None:
            self.ventilation_ach = max(0.0, min(10.0, cmd.desired.ventilation_ach))
        
        self.applied_command_count += 1
        log.info(
            "Oda %d: komut uygulandi reason=%s setpoint=%.1f ach=%.2f",
            self.room_id,
            cmd.reason.value,
            self.setpoint_c,
            self.ventilation_ach
        )
        return True
    
    def tick(self, now_utc:datetime, weather:WeatherSnapshot) -> RoomTelemetry:
        """Odayi bir adim ilerletir ve telemetri mesajini dondurur."""

        local_dt = to_local(now_utc, self._s)
        occ = occupancy_at(local_dt,self.room_id,self._s)
        occ_fraction = occupancy_fraction(local_dt, self.room_id, self._s)

        #fizik motorunu cagir

        out = thermal.step(
            thermal.ThermalInputs(
                indoor_temp_c=self.indoor_temp_c,
                setpoint_c= self.setpoint_c,
                co2_ppm=self.co2_ppm,
                outdoor_temp_c=weather.temp_c,
                radiation_w_per_m2=weather.radiation_w_per_m2,
                occupancy=occ,
                occupancy_fraction=occ_fraction,
                ventilation_ach=self.ventilation_ach,
                dt_seconds=self._s.sim_tick_seconds
            ),
            self._s,
        )
        #gercek durumu guncelle:

        self.indoor_temp_c = out.indoor_temp_c
        self.co2_ppm = out.co2_ppm
        self._last = out

        temp_reported = out.indoor_temp_c + self._rng.gauss(0.0, self._s.sensor_temp_noise_c)
        co2_reported = out.co2_ppm + self._rng.gauss(0.0,self._s.sensor_co2_noise_ppm)

        temp_reported = max(-20.0, min(60.0, temp_reported))
        co2_reported = max(300.0, min(5000.0, co2_reported))

        return RoomTelemetry(
            room_id=self.room_id,
            ts_utc=now_utc,
            temp_c=round(temp_reported, 2),
            setpoint_c=self.setpoint_c,
            co2_ppm=round(co2_reported, 1),
            occupancy=occ,
            hvac_kw=round(out.hvac_electric_kw, 3),
            cooling_kw=round(out.cooling_kw, 3),
            valve_pct=round(out.valve_pct, 1),
            ventilation_ach=round(self.ventilation_ach, 3),
            internal_gain_w=round(out.internal_gain_w, 1),
        )