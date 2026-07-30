"""Open-Meteo istemcisi: gerçek dış ortam koşulları, önbellek ve yedek profil."""

# Tip ipuçlarını metin olarak değerlendirir; "X | None" yazımını ve sınıfın
# kendi adını kendi içinde tip olarak kullanmayı sorunsuz hale getirir.
from __future__ import annotations

import asyncio          # Arka plan görevi ve yeniden denemeler arası bekleme için
import logging          # print yerine seviyeli, modül adı taşıyan log için
import math             # Sentetik profildeki sinüs eğrileri ve ceil için
from datetime import datetime, timedelta, timezone   # Zaman damgaları ve süre farkları

import httpx            # Asenkron HTTP istemcisi (requests'in async karşılığı)
import numpy as np      # Vektörel diziler ve np.interp (doğrusal interpolasyon)
from pydantic import BaseModel   # Veri şemaları ve doğrulama

from config import get_settings  # Yapılandırma (önbellekli tekil nesne)

# Logger'ı modül adıyla oluşturuyoruz; çıktıda "weather" görünür, kaynağı belli olur
log = logging.getLogger(__name__)

# API'den isteyeceğimiz saatlik değişkenler. Tuple çünkü bu liste asla değişmeyecek
# (immutable olması, yanlışlıkla değiştirilmesini engeller).
HOURLY_VARIABLES = (
    "temperature_2m",          # Dış hava sıcaklığı, 2 m yükseklikte - RC modelin girdisi
    "relative_humidity_2m",    # Bağıl nem - free cooling uygunluğu için (v0.6)
    "shortwave_radiation",     # Güneş ışınımı W/m2 - güneş kazancı ve gün ışığı hasadı
    "cloud_cover",             # Bulutluluk % - ışınımı yorumlamayı kolaylaştırır
    "is_day",                  # Gündüz/gece bayrağı - aydınlatma mantığı için
)


class HourlyPoint(BaseModel):
    """API'den gelen tek bir saatlik veri noktası (ham ölçüm)."""

    ts_utc: datetime                # Bu ölçümün ait olduğu saat, UTC
    temp_c: float                   # Santigrat derece
    humidity_pct: float             # Yüzde
    radiation_w_per_m2: float       # W/m2
    cloud_cover_pct: float          # Yüzde
    is_day: bool                    # API 0/1 döndürür, biz boolean tutuyoruz


class WeatherSnapshot(BaseModel):
    """Belirli bir ana ait, interpolasyonla üretilmiş anlık koşullar."""

    ts_utc: datetime                # Hangi ana ait (saat başı olmak zorunda değil)
    temp_c: float
    humidity_pct: float
    radiation_w_per_m2: float
    cloud_cover_pct: float
    is_day: bool
    # Verinin nereden geldiği: "open-meteo" (taze) | "cached" (bayat) | "fallback".
    # Tüketiciye durumu veriyle birlikte taşır; loglarda aramak gerekmez.
    source: str


class WeatherProvider:
    """Saatte bir API'den çeker, her tick'te bellekten interpolasyonla okutur."""

    def __init__(self) -> None:
        # Ayarları bir kez alıp saklıyoruz; her metotta get_settings() çağırmıyoruz
        self._s = get_settings()
        # Ham noktalar (loglama ve hata ayıklama için tutuluyor)
        self._points: list[HourlyPoint] = []
        # En son başarılı çekimin zamanı; tazelik kararı bunun üzerinden veriliyor
        self._last_success_utc: datetime | None = None

        # Interpolasyon için hazırlanmış paralel diziler.
        # Her tick'te yeniden kurmak yerine yenilemede bir kez hazırlanıyor:
        # saniyede 66 kez 48 elemanlı liste kurmanın anlamı yok.
        # Zaman ekseni unix saniye (float) - np.interp datetime ile çalışmıyor.
        self._x: np.ndarray = np.empty(0, dtype=float)          # zaman ekseni
        self._y_temp: np.ndarray = np.empty(0, dtype=float)
        self._y_hum: np.ndarray = np.empty(0, dtype=float)
        self._y_rad: np.ndarray = np.empty(0, dtype=float)
        self._y_cloud: np.ndarray = np.empty(0, dtype=float)
        self._y_day: np.ndarray = np.empty(0, dtype=float)      # 0/1 olarak tutuluyor

    # --- okuma tarafi (hizli, IO yok) --------------------------------

    def current(self, now_utc: datetime | None = None) -> WeatherSnapshot:
        """Verilen ana ait koşulları döndürür. Ağ beklemez, bu yüzden async DEĞİL."""
        # Parametre verilmezse şimdiyi kullan. Parametre kabul etmesi test için önemli:
        # "öğlen ne olur" senaryosunu sistem saatini değiştirmeden yazabiliyorsun.
        now = now_utc or datetime.now(timezone.utc)

        # Önbellekten interpolasyonla o ana ait değeri üret
        point = self._interpolate(now)

        if point is None:                       # Hiç veri yok (ilk açılış / internet yok)
            if not self._s.weather_fallback_enabled:
                # Bilinçli tercih: "sentetik veriyle çalışmayı kabul etmiyorum" demenin yolu
                raise RuntimeError(
                    "Hava verisi yok ve yedek profil kapali "
                    "(WEATHER_FALLBACK_ENABLED=false)"
                )
            point = self._synthetic(now)         # Sentetik profile düş
            source = "fallback"
        else:
            source = self._freshness()           # Veri var: taze mi bayat mı

        # Ham noktayı, kaynak bilgisiyle birlikte dış dünyaya sunulan tipe çeviriyoruz
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
        """Veri taze mi bayat mı - 'open-meteo' veya 'cached' döndürür."""
        if self._last_success_utc is None:       # Hiç başarılı çekim olmadıysa
            return "fallback"
        age = datetime.now(timezone.utc) - self._last_success_utc   # Verinin yaşı
        # Eşik iki aralık: bir yenilemeyi kaçırmak normal (geçici ağ dalgalanması),
        # iki aralık boyunca kaçırmak gerçek bir problem işareti.
        stale_after = timedelta(seconds=self._s.weather_fetch_interval_seconds * 2)
        return "cached" if age > stale_after else "open-meteo"

    def _interpolate(self, now: datetime) -> HourlyPoint | None:
        """İki saatlik nokta arasında doğrusal interpolasyon (np.interp ile)."""
        if self._x.size == 0:            # Önbellek boş -> çağıran fallback'e düşecek
            return None

        t = now.timestamp()              # datetime -> unix saniye (float)

        # np.interp(x, xp, fp): xp/fp örneklerinden x noktasındaki değeri üretir.
        # Aralık DISINDA otomatik olarak fp[0] veya fp[-1] döndürüyor - yani
        # "veri penceresinden önce/sonra" kenar durumlarını kendisi hallediyor.
        # Doğrusal seçimi bilinçli: kübik spline aralık dışına taşabiliyor
        # (ışınımda negatif değer üretebilir), doğrusal asla taşmıyor.
        # float(...) ile numpy.float64'ten normal Python float'a çeviriyoruz.
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

    # --- yazma tarafi (yavas, ag) ------------------------------------

    async def run_forever(self) -> None:
        """Arka plan görevi: periyodik olarak yeniler. main.py bunu task olarak başlatır."""
        while True:
            await self.refresh()                 # Başarısız olsa bile devam et
            # Bekleme sırasında olay döngüsü diğer görevlere (odalar) bakıyor
            await asyncio.sleep(self._s.weather_fetch_interval_seconds)

    async def refresh(self) -> bool:
        """Tek bir yenileme turu (yeniden denemeler dahil). Başarılıysa True."""
        params = {
            "latitude": self._s.site_latitude,       # Sahanın konumu
            "longitude": self._s.site_longitude,
            "hourly": ",".join(HOURLY_VARIABLES),    # API virgülle ayrılmış liste istiyor
            # UTC zorunlu: sistemde her şeyin UTC olması kuralını API sınırında da uygula
            "timezone": "UTC",
            # Bir gün geriye de veri al. Tahmin günün 00:00'ından başladığı için
            # "şu an" için interpolasyon yaparken kenarda sıkışmayı engeller.
            "past_days": 1,
            # API gün cinsinden istiyor, biz .env'de saat tutuyoruz. min/max ile
            # API'nin kabul ettiği aralığa kırpıyoruz: kullanıcı 1000 saat yazsa da
            # geçerli bir istek gidiyor.
            "forecast_days": min(
                16, max(1, math.ceil(self._s.weather_forecast_hours / 24))
            ),
        }

        # max_retries=3 -> toplam 4 deneme (ilk + 3 tekrar). "+1" bu yüzden.
        for attempt in range(self._s.weather_max_retries + 1):
            try:
                # Her denemede yeni istemci: bağlantı havuzu temiz başlar.
                # timeout ASLA atlanmaz - belirtilmezse istek sonsuza kadar bekleyebilir
                # ve o görev hiç geri dönmez.
                async with httpx.AsyncClient(
                    timeout=self._s.weather_http_timeout_seconds
                ) as client:
                    resp = await client.get(self._s.weather_api_url, params=params)
                    resp.raise_for_status()      # 4xx/5xx durumunda istisna fırlat
                    payload = resp.json()        # JSON'u sözlüğe çevir

                points = self._parse(payload)    # Ham yanıtı tiplenmiş listeye çevir
                if not points:                   # Yanıt geldi ama içi boş
                    raise ValueError("API yanitinda saatlik veri yok")

                # Başarı: önbelleği, interpolasyon dizilerini ve zaman damgasını güncelle
                self._points = points
                self._rebuild_arrays(points)
                self._last_success_utc = datetime.now(timezone.utc)
                log.info(
                    "Hava verisi guncellendi: %d saatlik nokta, %s .. %s",
                    len(points),
                    points[0].ts_utc.isoformat(),
                    points[-1].ts_utc.isoformat(),
                )
                return True

            except Exception as exc:
                # Geniş yakalama burada meşru: timeout, bağlantı hatası, HTTP hatası,
                # JSON ayrıştırma hatası, boş veri - hepsinin cevabı aynı (tekrar dene).
                # Ama yutmuyoruz: logluyoruz ve durumu bildiriyoruz.
                wait = 2**attempt                # Üstel geri çekilme: 1, 2, 4 saniye
                is_last = attempt == self._s.weather_max_retries
                log.warning(
                    "Hava verisi cekilemedi (deneme %d/%d): %s%s",
                    attempt + 1,
                    self._s.weather_max_retries + 1,
                    exc,
                    "" if is_last else f" - {wait} s sonra tekrar",
                )
                if not is_last:
                    await asyncio.sleep(wait)    # Son denemeden sonra boşuna bekleme

        # Tüm denemeler başarısız. Hangi bozulma seviyesinde olduğumuzu açıkça logla.
        if self._points:
            log.warning("Son bilinen hava verisiyle devam ediliyor (stale)")
        else:
            log.warning("Hic hava verisi yok, sentetik profil kullanilacak")
        return False

    def _rebuild_arrays(self, points: list[HourlyPoint]) -> None:
        """Interpolasyon dizilerini yeniden kurar. Yalnızca başarılı yenilemede çağrılır."""
        # timestamp(): datetime'ı unix saniyeye çeviriyor. np.interp sayısal eksen
        # istiyor, datetime nesnesiyle çalışamıyor.
        self._x = np.array([p.ts_utc.timestamp() for p in points], dtype=float)
        self._y_temp = np.array([p.temp_c for p in points], dtype=float)
        self._y_hum = np.array([p.humidity_pct for p in points], dtype=float)
        self._y_rad = np.array([p.radiation_w_per_m2 for p in points], dtype=float)
        self._y_cloud = np.array([p.cloud_cover_pct for p in points], dtype=float)
        # Boolean'ı 0.0/1.0 olarak tutuyoruz ki interpolasyona girebilsin;
        # okurken 0.5 eşiğiyle tekrar boolean'a çevireceğiz.
        self._y_day = np.array([1.0 if p.is_day else 0.0 for p in points], dtype=float)

    @staticmethod
    def _parse(payload: dict) -> list[HourlyPoint]:
        """Open-Meteo yanıtını HourlyPoint listesine çevirir.

        staticmethod: self kullanmıyor, sadece girdi->çıktı dönüşümü. Test etmesi kolay
        ve ağa çıkmadan sınanabiliyor.
        """
        # "or {}" / "or []": API beklenen alanı döndürmezse KeyError yerine boş yapı
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []

        def col(name: str, default: float = 0.0) -> list[float]:
            """Bir değişken kolonunu güvenli şekilde float listesine çevirir."""
            values = hourly.get(name) or []
            return [
                default if v is None else float(v)   # null değerleri varsayılanla değiştir
                # Kolon zaman listesinden kısaysa varsayılanla doldur: dış bir API'nin
                # her zaman tutarlı davranacağını varsaymak riskli.
                for v in (values + [default] * (len(times) - len(values)))
            ]

        # Varsayılanlar fiziksel olarak makul: veri gelmezse saçma değer üretmesin
        temps = col("temperature_2m", 20.0)
        hums = col("relative_humidity_2m", 50.0)
        rads = col("shortwave_radiation", 0.0)
        clouds = col("cloud_cover", 0.0)
        days = col("is_day", 0.0)

        points: list[HourlyPoint] = []
        for i, raw_ts in enumerate(times):
            ts = datetime.fromisoformat(raw_ts)     # "2026-07-29T14:00" -> datetime
            if ts.tzinfo is None:
                # Open-Meteo zaman dilimi eki olmadan döndürüyor. timezone=UTC
                # istediğimiz için bunlar UTC - ama nesne bunu "bilmiyor".
                # Sınırda bilgiyi ekliyoruz; yoksa ileride karşılaştırmada TypeError.
                ts = ts.replace(tzinfo=timezone.utc)
            points.append(
                HourlyPoint(
                    ts_utc=ts,
                    temp_c=temps[i],
                    humidity_pct=hums[i],
                    # Işınım negatif olamaz; API'den gelen yuvarlama artefaktlarını kırp.
                    # Negatif ışınım termal modelde ters yönde ısı akışı üretirdi.
                    radiation_w_per_m2=max(0.0, rads[i]),
                    cloud_cover_pct=clouds[i],
                    is_day=days[i] >= 0.5,          # 0/1 sayısını boolean'a çevir
                )
            )

        # Sıralamayı garanti et: np.interp artan bir x ekseni bekliyor ve sıralı
        # olmayan bir dizide SESSIZCE yanlış sonuç verir. Tek satırlık sigorta.
        points.sort(key=lambda p: p.ts_utc)
        return points

    # --- yedek profil -----------------------------------------------

    def _synthetic(self, ts: datetime) -> HourlyPoint:
        """İnternet yokken kullanılan, fiziksel olarak makul sentetik profil.

        Amaç gerçek veriyi taklit etmek değil; internet yokken sistemin
        çalışmaya devam etmesi ve imkânsız değer üretmemesi.
        """
        # Günün kaçıncı saatindeyiz (ondalıklı, ör. 14.5 = 14:30)
        hour = ts.hour + ts.minute / 60.0

        # Günlük sıcaklık dalgası. "-9.0" kaydırması tepeyi 15:00'e, dibi 03:00'e
        # getiriyor: gerçek sıcaklık tepesi öğlen değil öğleden sonradır, çünkü
        # zemin öğleden sonra da ısınmaya devam ediyor.
        mean_c = 24.0
        amplitude_c = 7.0
        temp = mean_c + amplitude_c * math.sin(2.0 * math.pi * (hour - 9.0) / 24.0)

        # Işınım: gün doğumu 06:00, batışı 18:00, tepe 12:00. Yarım sinüs eğrisi.
        # 850 W/m2 açık bir yaz günü için makul tepe değer.
        # GECE TAM SIFIR olmalı - fiziksel olarak gece güneş ışınımı yok.
        # Hiçbir taban/ofset eklenmiyor; else dalı doğrudan 0.0 veriyor.
        if 6.0 <= hour <= 18.0:
            radiation = 850.0 * math.sin(math.pi * (hour - 6.0) / 12.0)
        else:
            radiation = 0.0

        # Nem sıcaklıkla ters ilişkili (kaba yaklaşım): hava ısındıkça bağıl nem
        # düşer. min/max ile 25-90 aralığında tutuyoruz; formülün kendisi bu
        # aralığın dışına çıkabilir ve %120 nem diye bir şey yok.
        humidity = max(25.0, min(90.0, 100.0 - 2.0 * (temp - 10.0)))

        return HourlyPoint(
            ts_utc=ts,
            temp_c=round(temp, 2),                   # Gereksiz ondalık taşımayalım
            humidity_pct=round(humidity, 1),
            # max(0.0, ...) ikinci bir sigorta: yuvarlama sonrası da negatif olmasın
            radiation_w_per_m2=round(max(0.0, radiation), 1),
            cloud_cover_pct=20.0,                    # Sentetik profilde bulut modellemiyoruz
            is_day=6.0 <= hour < 20.0,               # Alacakaranlık dahil
        )