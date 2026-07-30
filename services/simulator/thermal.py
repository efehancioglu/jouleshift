"""Odanin fizik motoru, 1R1C termal model, HVAC oransal kontrolu CO2 dengesi"""

from __future__ import annotations

from dataclasses import dataclass

from config import(
    AIR_DENSITY_KG_PER_M3,
    AIR_SPECIFIC_HEAT_J_PER_KG_K,
    Settings,
    get_settings
)

@dataclass(frozen=True)
class ThermalInputs:
    """Bir adim icin gereken her sey, tumu cagiran tarafindan saglanir"""

    indoor_temp_c: float        #odanin suanki gercek sicakligi
    setpoint_c: float           #hedef sicaklik
    co2_ppm: float              #odanin suanki co2 derisimi
    outdoor_temp_c: float       #dis hava sicakligi(weather.py'dan)
    radiation_w_per_m2:float    #gunes isinimi (weather.py'dan)
    occupancy: int              #kisi sayisi
    occupancy_fraction: float   #doluluk orani
    ventilation_ach: float      #havalandirma debisi

    dt_seconds: float           #adim suresi

@dataclass(frozen=True)
class ThermalOutputs:
    """Bir adim sonrasi oda hali + o adimda hesaplanan isi akislari"""

    indoor_temp_c: float        #yeni sicaklik
    co2_ppm: float              #yeni co2

    hvac_electric_kw: float     #klimanin CEKTIGI elektrik - faturaya sayilan
    cooling_kw: float           #klimanin tasidigi isi
    valve_pct: float            #kismi yuk orani

    envelope_gain_w: float
    solar_gain_w: float
    internal_gain_w: float
    net_gain_w: float

# yardimci hesaplar:

def ventilation_ua_w_per_k(ventilation_ach: float, settings: Settings | None = None) -> float:
    """Verilen hava degisim sayisi icin isi kaybi katsayisi
    config.py'daki room_ventilation_ua_w_per_k, .env'deki sabit degeri kullaniyor.
    bu fonksiyon ise degisken debi icin hesapliyor. havalandirma kontrol edilebilir oldugunda gerekecek.
    """

    s = settings or get_settings()
    flow_m3_per_s = ventilation_ach * s.room_volume_m3 / 3600.0
    return flow_m3_per_s * AIR_DENSITY_KG_PER_M3 * AIR_SPECIFIC_HEAT_J_PER_KG_K

def internal_gain_w(
        occupancy: int,
        occupancy_fraction: float,
        settings: Settings | None = None
) -> float:
    """Ic isi kazanci: insan + priz + aydinlatma"""
    s = settings or get_settings()

    people = occupancy * s.person_heat_w

    plug_base = s.plug_standby_fraction
    plug = (
        s.plug_load_w_per_m2
        * s.room_floor_area_m2
        * (plug_base + (1.0 - plug_base) * occupancy_fraction )
    )

    light_base = s.lighting_base_fraction
    lighting = (
        s.lighting_load_w_per_m2
        * s.room_floor_area_m2
        * (light_base + (1.0 - light_base) * occupancy_fraction)
    )

    return people + plug + lighting

def hvac_demand(
    indoor_temp_c: float,
    setpoint_c: float,
    settings: Settings | None = None,
) -> float:
    """Oransal kontrolcu, 0..1 arasi kismi yuk talebi dondurur
    
    hata <= +yarim_olu_bant         -> 0 (yeterince serin)
    hata, bant boyunca              -> 0..1 dogrusal
    hata >= yarim_bant + tam_bant   -> 1 (tam kapasite)
    
    olu bant, kompresorun surekli acma-kapama yapmasini engelliyor, tepe tiraslamadaki histerezisin termal karsiligi
    
    """
    s = settings or get_settings()

    error_k = indoor_temp_c - setpoint_c
    half_deadband = s.room_setpoint_deadband_c / 2.0

    if error_k <= half_deadband:
        return 0.0
    
    return min(1.0,(error_k - half_deadband) / s.room_hvac_proportional_band_c) #olu bandin ustunde kalan hata, oransal bant boyunca 0..1'e haritalaniyor

def max_stable_dt_seconds(
    ventilation_ach: float,
    settings: Settings | None = None
) -> float:
    """Euler yontemi icin guvenli azami adim suresi."""
    s = settings or get_settings()
    ua_total = s.room_envelope_ua_w_per_k + ventilation_ua_w_per_k(ventilation_ach,s)
    tau_seconds = s.room_thermal_capacity_j_per_k / ua_total
    return tau_seconds / 10.0

#ana adim

def step(inputs: ThermalInputs, settings: Settings | None = None) -> ThermalOutputs:
    """Odayi bir adim ilerletir. """
    s = settings or get_settings()

    #1- ic isi kazanci: insan + priz + aydinlatma
    internal_w = internal_gain_w(inputs.occupancy, inputs.occupancy_fraction, s)

    #2- zarf + havalandirma, sicaklik farki pozitifse isi iceri giriyor.
    ua_total = s.room_envelope_ua_w_per_k + ventilation_ua_w_per_k(inputs.ventilation_ach,s)
    envelope_w = ua_total * (inputs.outdoor_temp_c - inputs.indoor_temp_c)
    
    #3- gunes: dort carpani config'de birlestirdigimiz etkin aciklik x isinim
    solar_w = s.room_effective_solar_aperture_m2 * inputs.radiation_w_per_m2

    #4- odaya giren toplam isi
    gain_w = envelope_w + solar_w + internal_w

    #5- HVAC: talep -> isil kapasite ve elektrik gucu, termal modele isil guc girer, faturaya elektrik gucu girer, aradaki oran COP (coefficient of performance)
    demand = hvac_demand(inputs.indoor_temp_c, inputs.setpoint_c, s)
    cooling_w = demand * s.room_hvac_cooling_capacity_kw * 1000.0
    electric_kw = demand * s.room_hvac_rated_kw

    #6- sicaklik adimi (Euler) : C dT/dt = net_isi , dt / C carpani, isi akisini sicaklik degisimine cevirir.
    net_w = gain_w - cooling_w
    new_temp_c = inputs.indoor_temp_c + (
        inputs.dt_seconds / s.room_thermal_capacity_j_per_k
    ) * net_w

    #7- CO2 dengesi, uretim : kisi basina L/s -> m3/s (1000'e bol)
    #seyreltme: taze hava, ic ile dis derisim farkini supuruyor.
    # V dC/dt = uretim x 1e6 - debi x (C_ic - C_dis)
    # 1e6 carpani m3/m3 oranini ppm'e cevirir

    co2_gen_m3_per_s = inputs.occupancy * s.co2_per_person_l_per_s /1000.0
    vent_flow_m3_per_s = inputs.ventilation_ach * s.room_volume_m3 / 3600.0
    dco2_per_s = (
        co2_gen_m3_per_s*1e6
        - vent_flow_m3_per_s * (inputs.co2_ppm - s.co2_outdoor_ppm)
    ) / s.room_volume_m3

    #max ile dis ortam derisiminin altina engelliyoruz, ne kadar havalandırılsa da ic derisim dis derisim altina inemez
    new_co2_ppm = max(s.co2_outdoor_ppm, inputs.co2_ppm + dco2_per_s*inputs.dt_seconds)

    return ThermalOutputs(
        indoor_temp_c= new_temp_c,
        co2_ppm= new_co2_ppm,
        hvac_electric_kw=electric_kw,
        cooling_kw= cooling_w /1000,
        valve_pct=demand* 100.0,
        envelope_gain_w=envelope_w,
        solar_gain_w=solar_w,
        internal_gain_w=internal_w,
        net_gain_w=net_w
    )