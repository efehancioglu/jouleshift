# JouleShift OS

Otonom bina enerji ve yük yönetim platformu. Gerçek zamanlı telemetriyi işleyip,
15 dakikalık talep projeksiyonuna göre otonom yük kısıtlama komutu üreten
kapalı çevrim bir BEMS (Building Energy Management System).

> Durum: geliştirme aşamasında. Faz 1 (kapalı çevrim) üzerinde çalışılıyor.

## Demo

_Adım 10'da eklenecek: eşiğe tırmanış → otonom müdahale → yükün düşüşü (GIF)_

## Mimari

_Adım 10'da eklenecek: docs/mimari.svg_

## Teknoloji yığını

| Katman | Teknoloji |
|---|---|
| Edge / simülasyon | Python 3.11, asyncio, paho-mqtt, Pydantic |
| Mesajlaşma | Eclipse Mosquitto (MQTT) |
| Çekirdek motor | .NET 8 / C#, MQTTnet, SignalR |
| Hot path | Redis |
| Cold path | PostgreSQL + TimescaleDB |
| ML servisi | Python, FastAPI, LightGBM |
| Arayüz | React, TypeScript, Tailwind CSS, Vite |

## Kurulum

_Adım 10'da eklenecek_

## Tasarım kararları

_Adım 10'da eklenecek: histerezis, 15 dk talep projeksiyonu, idempotent aktüasyon,
saf domain katmanı, hot/cold path ayrımı_

## Sınırlamalar

_Adım 10'da eklenecek_

## Lisans

MIT