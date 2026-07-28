""" Yapılandırma: .env dosyasını tipli ve doğrulanmış şekilde okur. """

from datetime import time
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pathlib import Path

# config.py -> services/simulator -> services -> <proje kökü>
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

if not ENV_FILE.exists():
    raise RuntimeError(
        f".env dosyasi bulunamadi: {ENV_FILE}\n"
        f".env.example dosyasini .env olarak kopyala."
    )

#hava için fiziksel sabitler:

AIR_DENSITY_KG_PER_M3 = 1.2
AIR_SPECIFIC_HEAT_J_PER_KG_K = 1005.0

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )

    # MQTT
    mqtt_host: str = "localhost"
    mqtt_port: int = Field(1883, gt=0, lt=65536)
    mqtt_topic_root : str = "jouleshift"
    mqtt_client_id_prefix : str = "jouleshift"

    # Bolge / Saha
    site_name : str = "Istanbul Ofis Plaza"
    site_latitude : float = Field(41.0082, ge=-90.0, le=90.0)
    site_longitude: float = Field(28.9784, ge=-180.0, le=180.0)
    site_timezone : str = "Europe/Istanbul"

    # Hava Durumu
    weather_api_url : str = "https://api.open-meteo.com/v1/forecast"
    weather_fetch_interval_seconds : int = Field(3600, gt=0)
    weather_forecast_hours : int = Field(48, gt=0, le=384)
    weather_http_timeout_seconds : float = Field(10.0, gt=0)
    weather_max_retries : int = Field(3,ge=0,le=10)
    weather_fallback_enabled : bool = True

    #Simulasyon Zamanı
    sim_tick_seconds : float = Field(1.0, gt=0)
    sim_time_scale : float = Field(1.0, gt=0, le=3600)

    #Oda Geometrisi ve Termal
    room_floor_area_m2 : float = Field(100.0, gt = 0)
    room_height_m: float = Field(3.0, gt=0)
    room_thermal_capacity_kj_per_m2_k: float = Field(150.0, gt=0)
    room_envelope_ua_w_per_k: float = Field(20.0, gt=0)
    room_ventilation_ach: float = Field(1.0, ge=0, le=10)
    room_hvac_rated_kw: float = Field(3.0, gt=0)
    room_hvac_cop: float = Field(3.0, gt=1.0, le=8.0)
    room_setpoint_default_c: float = Field(23.0, ge=16.0, le=30.0)
    room_setpoint_deadband_c: float = Field(0.5, gt=0, le=3.0)
    room_initial_temp_c: float = Field(24.0, ge=-20.0, le=60.0)

    # İç Kazançlar
    person_heat_w: float = Field(100.0, ge=0)
    plug_load_w_per_m2: float = Field(7.0, ge=0)
    lighting_load_w_per_m2: float = Field(8.0, ge=0)

    # CO2
    co2_outdoor_ppm: float = Field(420.0, gt=0)
    co2_per_person_l_per_s: float = Field(0.0052, gt=0)

    # Doluluk
    occupancy_max_per_room: int = Field(4, ge=0)
    occupancy_workday_start: time = time(8, 0)
    occupancy_workday_end: time = time(18, 0)
    occupancy_lunch_start: time = time(12, 0)
    occupancy_lunch_end: time = time(13, 0)
    occupancy_lunch_factor: float = Field(0.4, ge=0, le=1)
    occupancy_weekend_factor: float = Field(0.05, ge=0, le=1)

    # Güneş Kazancı
    room_window_area_m2: float = Field(25.0, ge=0)
    room_window_shgc: float = Field(0.35, gt=0, le=1.0)
    room_shading_factor: float = Field(0.60, gt=0, le=1.0)
    room_solar_orientation_factor: float = Field(0.75, gt=0, le=1.0)

    # Türetilmiş değerler

    @property # Oda hacmi
    def room_volume_m3(self) -> float:
        return self.room_floor_area_m2 * self.room_height_m
    
    @property # Oda termal kapasitesi
    def room_thermal_capacity_j_per_k(self) -> float:
        """ formul: kJ/(m2.K) -> J/K """
        return self.room_thermal_capacity_kj_per_m2_k * 1000.0 * self.room_floor_area_m2
    
    @property
    def room_ventilation_ua_w_per_k(self) -> float:
        """Hava değişim sayısını (ACH) ısı kaybı katsayısına çevirir."""
        flow_m3_per_s = self.room_ventilation_ach * self.room_volume_m3/3600.0
        return flow_m3_per_s * AIR_DENSITY_KG_PER_M3 * AIR_SPECIFIC_HEAT_J_PER_KG_K
    
    @property
    def room_total_ua_w_per_k(self) -> float:
        return self.room_envelope_ua_w_per_k + self.room_ventilation_ua_w_per_k
    
    @property
    def room_time_constant_hours(self) -> float:
        """tau = C / UA - odanın tepki hızı."""
        return self.room_thermal_capacity_j_per_k / self.room_total_ua_w_per_k / 3600.0
    
    @property
    def room_hvac_cooling_capacity_kw(self) -> float:
        """Elektrik gücü x COP = ısıl soğutma kapasitesi"""
        return self.room_hvac_rated_kw * self.room_hvac_cop
    
    @property
    def room_effective_solar_aperture_m2(self) -> float:
        """Isınmaya katkıda bulunan etkin pencere alanı."""
        return (
            self.room_window_area_m2
            * self.room_window_shgc
            * self.room_shading_factor
            * self.room_solar_orientation_factor
        )
    
    # Tutarlılık Kontrolleri

    @model_validator(mode="after")
    def _check_occupancy_schedule(self) -> "Settings":
        if self.occupancy_workday_start >= self.occupancy_workday_end:
            raise ValueError(
                "OCCUPANCY_WORKDAY_START, OCCUPANCY_WORKDAY_END'den once olmali"
            )
        if not(
            self.occupancy_lunch_start
            <= self.occupancy_lunch_start
            < self.occupancy_lunch_end
            <= self.occupancy_workday_end
        ):
            raise ValueError("Ogle arasi, is gunu araligi icinde olmali")
        return self

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Ayarlari bir kez okuyup onbellege alir."""
    return Settings()    