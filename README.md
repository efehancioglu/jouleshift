# JouleShift OS

**Ticari binalar için otonom enerji verimliliği ve yük yönetim platformu.**

Bir ofis binasının elektrik tüketimini saniye saniye izleyen, dört farklı hedefi
aynı anda gözeten ve insan onayı beklemeden donanıma müdahale eden kapalı çevrim
bir BEMS (Building Energy Management System).

> **Durum:** yapım aşamasında. Edge/simülasyon katmanı ve altyapı çalışıyor;
> çekirdek karar motoru, kalıcı depolama, arayüz ve tahmin servisi geliştiriliyor.

---

## Neden

Ticari binalarda enerji yönetimi hâlâ büyük ölçüde **sabit saatli zamanlayıcılara**
dayanıyor: "klimalar 08:00'de açılsın, 18:00'de kapansın." Bina gerçekte ne kadar
çektiğini bilmiyor, dışarıdaki hava durumunu dikkate almıyor, boş odaları
soğutmaya devam ediyor.

Bunun üç somut maliyeti var:

- **Güç aşım cezası.** Sözleşme gücü aşıldığında dağıtım şirketi ek ücret kesiyor
  ve bu aşım kimse fark etmeden gerçekleşiyor.
- **İsrafa giden kWh.** Boş odanın aydınlatması, güneşli günde tam yanan lambalar,
  kimse yokken çalışan havalandırma.
- **Yanlış boyutlandırılmış sözleşme.** Bina hiç 430 kW'ı geçmiyorsa 500 kW
  sözleşme gücü için ödenen sabit güç bedeli boşa gidiyor.

JouleShift OS bu üçünü de hedefliyor — ve bunu pasif bir izleme paneli olarak
değil, **kendi kararıyla müdahale eden** bir kontrol sistemi olarak yapıyor.

---

## Ne yapıyor

Sistem dört farklı amacı, dört farklı zaman ölçeğinde yönetiyor. Eşik aşımı
bunlardan yalnızca birini tetikliyor:

| Amaç | Tetikleyici | Zaman ölçeği | Faturadaki karşılığı |
|---|---|---|---|
| **Talep sınırlama** | 15 dk talep projeksiyonu eşiği aşıyor | Saniyeler | Güç aşım bedeli |
| **Sürekli verimlilik** | Eşik yok — kesintisiz çalışıyor | Sürekli | Enerji bedeli (kWh) |
| **Tarife arbitrajı** | Saat ve fiyat | Saatler | Enerji bedeli (kaydırma) |
| **Sözleşme optimizasyonu** | Geçmiş talep analizi | Aylar | Güç bedeli |

**Ve paranın çoğu ikinci satırda.** Yük kısıtlama yılda birkaç kez tetiklenip her
seferinde büyük bir cezayı önlüyor; sürekli verimlilik önlemleri hiç durmuyor ve
yıllık toplamda genellikle daha fazla kazandırıyor. Bu yüzden tasarruf hesabı
**önlenen ceza** ve **azaltılan tüketim** olarak ayrı ayrı raporlanıyor.

### Kontrol kolları

| Yük | Müdahale | Kazanç |
|---|---|---|
| Araç şarj istasyonları | Şarj gücünü kıs (22 → 7 kW) | ~150 kW |
| HVAC (ısı pompası) | Setpoint geri çekmesi | ~35 kW |
| Aydınlatma | Gün ışığı hasadı + kısma | ~10 kW tepe, sürekli kWh |
| Kullanım sıcak suyu | Ucuz saate ötele | ~20 kW |
| Sunucu odası, asansör | **Muaf** — asla kısılmıyor | — |

---

## Öne çıkan teknik kararlar

Bu kararların tamamı gerekçeleriyle [`docs/adr/`](docs/adr/) altında kayıtlı.

**Talep, anlık kW üzerinden değil 15 dakikalık projeksiyonla kontrol ediliyor.**
Güç aşım bedeli anlık tepe değere değil 15 dakikalık ortalama talebe göre
hesaplandığı için, kontrol edilen büyüklük faturalanan büyüklükle aynı tutuluyor.
Dilim sonundaki ortalama öngörülüyor ve karar ona göre veriliyor. Sonuç: sistem
dilim başında cömert, sonuna doğru agresif davranıyor — gereksiz müdahale sayısı
düşüyor.

**Öngörülü kontrol, sözleşme gücünün %96'sını güvenle kullanmayı sağlıyor.**
Tepkisel bir kontrolcü, yük oynaklığına karşı korunmak için %80–85 civarında
kalmak zorunda kalır. Hızlı tepki + büyük kısıtlama kapasitesi, güvenlik payını
daralttığı için mevcut altyapıdan daha fazla yararlanılabiliyor.

**Histerezis ve minimum bekleme süresi.** Tek eşikli bir kural saniyede bir komut
üretirdi (480 → kıs → 470 → bırak → 486 → kıs). Devreye girme ve bırakma eşikleri
ayrı (480/440 kW), ve alınan karar en az 5 dakika korunuyor.

**Idempotent aktüasyon.** MQTT QoS 1 "en az bir kez" garantisi verdiği için
duplikasyon bir arıza değil, protokolün normal davranışı. Üç katmanlı çözüm:
komutlar mutlak hedef durum bildiriyor (değişim miktarı değil), gönderici son
gönderdiği durumu karşılaştırıyor, alıcı `command_id` filtresi uyguluyor.

**Karar motoru saf fonksiyon.** Kural katmanı veritabanına, mesaj kuyruğuna ve
sistem saatine erişmiyor; karar için gereken her şey bir durum nesnesi olarak
veriliyor, zaman parametre olarak geçiriliyor. Böylece kararlar deterministik ve
kurulum gerektirmeden test edilebilir.

**Hot path / cold path ayrımı.** Karar yolunda veritabanı yok: telemetri → Redis →
kural → komut. Arşivleme ayrı bir yolda, sınırlı kapasiteli kuyruk üzerinden
toplu yazımla yapılıyor. Kuyruk dolduğunda en eski kayıt düşürülüyor — bir
telemetri satırı kaybetmek, karar döngüsünü bloklamaktan iyi.

**Emniyet kilitleri kural motorundan ayrı.** Üretilen her komut yayınlanmadan
önce süzülüyor: konfor sınırları, azami kısıtlama süresi, muaf yükler, komut hız
sınırı, otonom mod anahtarı. Gönderilen **ve süzülen** her komut denetim kaydına
yazılıyor — sistemin neyi yapmak isteyip yapamadığını görmek, ne yaptığını görmek
kadar önemli.

**Dış ortam verisi gerçek.** Dış hava sıcaklığı ve güneş ışınımı Open-Meteo'dan
alınıyor. Bu, hem "hava durumuna göre yönetim" iddiasını gerçek bir kaynağa
dayandırıyor, hem de tahmin modelinin en önemli dışsal değişkenini sentetik
olmaktan çıkarıyor. Servis erişilemez olduğunda son bilinen veriyle devam ediliyor
ve bu durum telemetride açıkça işaretleniyor.

---

## Bina modeli

Simülasyon, gerçekçi bir yük bütçesi üzerine kurulu — parametrelerin tamamı
yapılandırmadan geliyor.

| Özellik | Değer |
|---|---|
| Kullanım alanı | ~5.000 m² ticari ofis, 50 birim |
| Yaklaşık kullanıcı | 200 kişi |
| Enerji kaynağı | Tamamen elektrik (ısı pompası), doğalgaz yok |
| Sözleşme gücü | 500 kW |
| Müdahale eşiği | 480 kW devreye / 440 kW bırak |

Yaz puant senaryosunda toplam yük:

| Yük kalemi | Nominal | Puant | Kontrol |
|---|---|---|---|
| Araç şarj (10 × 22 kW) | 220 kW | 220 kW | Tam |
| HVAC (50 oda) | 150 kW | 150 kW | Setpoint |
| Aydınlatma | 40 kW | 30 kW | Kısma % |
| Priz yükleri | 35 kW | 30 kW | — |
| Mutfak | 25 kW | 5 kW | — |
| Sıcak su | 20 kW | 5 kW | Öteleme |
| Yardımcı ekipman | 20 kW | 20 kW | — |
| Sunucu odası | 30 kW | 30 kW | Muaf |
| Asansörler | 30 kW anlık | 8 kW ort. | Muaf |
| **Toplam** | | **498 kW** | **~200 kW manevra** |

Rakam bilinçli olarak sözleşme gücünün hemen altında ve eşiğin hemen üstünde
tutuldu: eşik gerçekten aşılıyor, ama sistemin başarabileceği bir manevra alanı
da var.

### Fizik modeli

Odalar 1R1C (toplu kapasitans) termal modeliyle simüle ediliyor. Dört ısı kanalı
hesaplanıyor: zarftan iletim, havalandırma, pencereden güneş kazancı, ve iç
kazançlar (insan + priz + aydınlatma). Isı pompasının **elektrik gücü** ile
**ısıl kapasitesi** COP üzerinden ayrı tutuluyor.

Modelden çıkan iki gözlem tasarımı doğrudan etkiledi:

- Havalandırmanın ısı kaybı katsayısı (100 W/K), zarfın beş katı (20 W/K). Taze
  havayı soğutmak, duvarlardan gelen ısıyı yenmekten çok daha pahalı — CO2 bazlı
  havalandırmanın gerekçesi bu.
- HVAC'ın **elektrik gücü** saniyeler içinde tepki veriyor, **oda sıcaklığı**
  saatler içinde. Setpoint geri çekmesi yükü anında düşürüyor; binanın termal
  kütlesi geçici bir tampon görevi görüyor. Tepe tıraşlamanın gerçek mekanizması
  bu.

---

## Mimari

```
┌─ EDGE / SİMÜLASYON ─────────────────────────────────────────┐
│  50 ofis odası · 10 araç şarj noktası · 6 alt sayaç          │
│  Gerçek hava verisiyle beslenen fizik modeli                 │
│  Telemetri yayınlar · komutlara tepki verir                  │
└──────────────────────────┬──────────────────────────────────┘
                           │ MQTT
┌──────────────────────────▼──────────────────────────────────┐
│  Eclipse Mosquitto                                          │
└──────────────────────────┬──────────────────────────────────┘
                           │ MQTT
┌──────────────────────────▼──────────────────────────────────┐
│  ÇEKİRDEK MOTOR (.NET 8)                                    │
│  Mesaj alımı · 15 dk projeksiyon · kural motoru (saf)        │
│  emniyet kilitleri · idempotent aktüasyon · canlı yayın      │
└──┬──────────────┬──────────────┬──────────────┬─────────────┘
   │ RESP         │ SQL (toplu)  │ HTTP         │ WebSocket
┌──▼───────┐ ┌────▼─────────┐ ┌──▼──────────┐ ┌─▼───────────┐
│  Redis   │ │ TimescaleDB  │ │ Tahmin      │ │  Arayüz     │
│ hot path │ │ cold path    │ │ servisi     │ │             │
└──────────┘ └──────────────┘ └─────────────┘ └─────────────┘
```

Broker yalnızca edge ile çekirdek motor arasında oturuyor. Redis, TimescaleDB ve
tahmin servisi çekirdek motorun yanındaki bağımlılıklar; hiçbiri MQTT konuşmuyor.

Bu ayrıştırmanın somut getirisi: sisteme gerçek bir mikrodenetleyici node'u
eklemek, çekirdek motorda **tek satır** değişiklik gerektirmiyor. Cihaz doğru
konuya doğru şemayla yayınladığı sürece, çekirdek motor onun yazılım mı donanım
mı olduğunu bilmiyor.

---

## Teknoloji yığını

| Katman | Teknoloji | Durum |
|---|---|---|
| Edge / simülasyon | Python 3.11, asyncio, aiomqtt, Pydantic, numpy, httpx | Çalışıyor |
| Mesajlaşma | Eclipse Mosquitto (MQTT) | Çalışıyor |
| Hot path | Redis | Ayakta |
| Dış veri | Open-Meteo (hava tahmini + ışınım) | Çalışıyor |
| Çekirdek motor | .NET 8 / C#, MQTTnet, SignalR | Yapım aşamasında |
| Cold path | PostgreSQL + TimescaleDB | Yapım aşamasında |
| Tahmin servisi | Python, FastAPI, LightGBM | Yapım aşamasında |
| Arayüz | React, TypeScript, Tailwind CSS, Vite | Yapım aşamasında |

### Şu an çalışan

- Docker Compose ile tek komutluk altyapı (Mosquitto + Redis, healthcheck'li)
- Tipli ve doğrulanmış yapılandırma; tüm parametreler `.env`'den
- MQTT konu şeması ve Pydantic mesaj sözleşmeleri
- Open-Meteo istemcisi: saatlik önbellek, doğrusal interpolasyon, üstel geri
  çekilmeli yeniden deneme, çevrimdışı yedek profil
- Doluluk takvimi: kademeli geçişler, öğle arası, hafta sonu, oda başına
  deterministik faz kaydırması
- 1R1C termal model: dört ısı kanalı, COP ayrımı, ölü bantlı oransal kontrol,
  CO2 denge denklemi
- Tek odalı kapalı çevrim: telemetri yayını, komut aboneliği, idempotency
  filtresi, LWT ile çevrimdışı tespiti
- 58 birim test

### Yapım aşamasında

- 50 oda + 10 şarj noktası + 6 alt sayaca ölçekleme
- Çekirdek karar motoru: 15 dk projeksiyon, kural motoru, emniyet kilitleri
- TimescaleDB arşivleme: hypertable, sürekli toplulaştırma, sıkıştırma ve
  saklama politikaları
- Maliyet ve ROI modeli (önlenen ceza + azaltılan tüketim ayrı)
- Canlı arayüz ve otonom mod anahtarı
- Sürekli verimlilik katmanı: boş alan geri çekmesi, CO2 bazlı havalandırma,
  şarj zamanlaması, sözleşme gücü raporu
- Yük tahmini (LightGBM) ve ön soğutma

---

## Kurulum

**Gereksinimler:** Docker Desktop, Python 3.11+

```bash
git clone <repo-url>
cd jouleshift

# Yapılandırma
cp .env.example .env

# Altyapı
docker compose up -d
docker compose ps          # iki servis de "Up (healthy)" olmalı

# Simülatör
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -r services/simulator/requirements.txt
pip install -r requirements-dev.txt  # testler için

cd services/simulator
python main.py
```

Telemetri akışını izlemek için ayrı bir terminalde:

```bash
docker exec -it jouleshift-mqtt mosquitto_sub -h localhost -t 'jouleshift/#' -v
```

### Kapalı çevrimi elle denemek

Broker konteynerinin kabuğuna girip bir setpoint komutu gönder:

```bash
docker exec -it jouleshift-mqtt sh

mosquitto_pub -h localhost -q 1 -t 'jouleshift/cmd/room/1' \
  -m '{"command_id":"11111111-1111-1111-1111-111111111111",
       "ts_utc":"2026-07-29T14:30:00Z",
       "reason":"PEAK_SHAVING",
       "desired":{"setpoint_c":24.5}}'
```

Simülatör logunda HVAC elektrik gücünün anında sıfıra düştüğünü, ardından oda
sıcaklığının yavaşça tırmandığını göreceksin. Aynı komutu ikinci kez gönderirsen
idempotency filtresi onu yok sayıyor.

### Testler

```bash
pytest
```

---

## Yapılandırma

Hiçbir eşik, port, bağlantı dizesi veya tarife değeri koda gömülü değil; tamamı
`.env` üzerinden geliyor. `.env.example` hangi parametrelerin var olduğunu
belgeliyor; gerçek değerleri taşıyan `.env` sürüm kontrolüne girmiyor.

Yapılandırma, uygulama başlarken tipli olarak okunuyor ve doğrulanıyor: geçersiz
bir değer (örneğin 1'in altında bir COP) programın **açılışta** anlaşılır bir
hatayla durmasına yol açıyor.

Çalışma sırasında değişmesi gereken şeyler `.env`'e ait değil — otonom mod
anahtarı gibi operatör kararları Redis'te tutuluyor ve arayüzden anında
değiştirilebiliyor.

---

## Sınırlamalar

Bunlar bilinçli kapsam kararları; her birinin gerekçesi [`docs/adr/`](docs/adr/)
altında kayıtlı.

**Veri sentetik.** Sistem yazılım tabanlı (software-in-the-loop) bir simülatörle
besleniyor. Dış ortam koşulları gerçek (Open-Meteo), ancak odaların termal
tepkisi ve yük profilleri modelden geliyor. Gerçek bir binada doğrulanmadı.

**Fizik modeli basitleştirilmiş.** 1R1C toplu kapasitans modeli kontrol
algoritması geliştirmek için kabul edilebilir bir yaklaşım, ancak bir bina enerji
simülasyonu iddiası değil. Duvar/döşeme/hava ayrı kapasitanslarla temsil
edilmiyor, oda geometrisi ve gölgeleme detaylı modellenmiyor.

**HVAC kontrolü oransal (P), integral terim yok.** Denge durumunda oda hedef
sıcaklığın bir miktar üstünde oturuyor (droop). Gerçek sistemler bunu PI kontrolle
ortadan kaldırıyor.

**Reaktif enerji ve güç faktörü modellenmiyor.** Yalnızca aktif güç (kW) hesaba
katılıyor. Gerçek faturaların bir kalemi olan reaktif enerji cezası kapsam dışı.

**Yenilenebilir üretim ve batarya depolama yok.** Çatı PV + batarya, enerji
arbitrajı hikâyesini güçlendirirdi; şimdilik kapsam dışı.

**MQTT kimlik doğrulaması ve TLS yok.** Broker yalnızca yerel Docker ağında
çalıştığı için anonim erişime açık. Üretim kullanımı için parola dosyası, TLS
dinleyici ve konu bazlı erişim denetimi gerekir.

**Tarife parametreleri örnek değerler.** Birim fiyatlar ve zaman dilimleri
doğrulanmadı; güncel değerler için EPDK ve ilgili dağıtım şirketinin tarife
duyuruları esas alınmalıdır. Tasarruf rakamları simülasyon varsayımlarına
dayanıyor.

**Uygulama kodu konteynerize edilmedi.** Altyapı Docker'da çalışıyor, servisler
geliştirme hızı için doğrudan çalıştırılıyor. Üretim dağıtımı için Dockerfile'lar
gerekir.

---

## Proje yapısı

```
jouleshift/
├── docker-compose.yml          Mosquitto + Redis
├── .env.example                Tüm yapılandırma parametreleri
├── pytest.ini
├── infra/
│   └── mosquitto/              Broker yapılandırması
├── docs/
│   └── adr/                    Mimari karar kayıtları
└── services/
    ├── simulator/              Python — edge katmanı ve fizik modeli
    ├── core-engine/            .NET 8 — karar motoru
    └── ai-service/             Python — tahmin servisi
```

---

## Kısaltmalar

| Kısaltma | Açılımı |
|---|---|
| BEMS | Building Energy Management System |
| ADR | Architecture Decision Record |
| ACH | Air Changes per Hour — hava değişim sayısı |
| UA | Isı kaybı katsayısı (W/K) — geçirgenlik × alan |
| COP | Coefficient of Performance — ısı pompası verimi |
| SHGC | Solar Heat Gain Coefficient — camın geçirdiği güneş ısısı oranı |
| DHW | Domestic Hot Water — kullanım sıcak suyu |
| LWT | Last Will and Testament — MQTT çevrimdışı bildirimi |
| QoS | Quality of Service — MQTT teslim garantisi seviyesi |
| SIL | Software-In-the-Loop — yazılım tabanlı simülasyon |
| ppm | parts per million — CO2 derişim birimi |

---

## Veri kaynakları

Hava durumu ve güneş ışınımı verisi [Open-Meteo](https://open-meteo.com/)
tarafından CC BY 4.0 lisansıyla sağlanmaktadır.

## Lisans

MIT
