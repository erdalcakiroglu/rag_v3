# M-11 — Admin Config sekmesi UI/UX yenileme: RAPOR

**Tarih:** 2026-07-20  **Kapsam:** yalnızca **render katmanı** (`ragintel/api/static/admin.html`).
**Kritik kısıt korundu:** `ui_schema.py` şema-güdümlü üretim, admin **PATCH/audit** ve **form doğrulama** kontratı **DEĞİŞMEDİ** — bu bir görünüm yenilemesidir. Backend'e dokunulmadı.

## Teslim edilen tasarım (8 nokta)

1. **Yoğun satır düzeni** — her alan tek satır: `[durum-noktası] mono-ad [⚠][ⓘ] … sağda kontrol`. Kart-blok düzeni kaldırıldı (`.frow`).
2. **Açıklama tıkla-aç** — `ⓘ` ikonu → alanın altında açılır (sürekli metin duvarı yok).
3. **Danger tek ikon** — `⚠` (tam metin `title`'da); her alanda tekrar eden uzun etiket yok.
4. **Üst araç çubuğu** — canlı arama (ad + açıklama üzerinde) + iki süzgeç: **riskli** / **değişmiş (N)**.
5. **Varsayılandan farklı** — mavi nokta + `varsayılan: X` + tek-tık **varsayılana dön** (default'a PATCH).
6. **İç içe gruplar** — katlanır alt-başlık (quality → weights/parse/embed…), **varsayılan KAPALI**, başlıkta sapan-sayacı.
7. **Alan-bazlı kayıt (OTOMATİK-KAYIT YOK)** — alan değişince satır-içi **Kaydet ✓ / geri al**; geçerli→PATCH+audit+rozet; geçersiz→alan-altı TR hata, DB değişmez.
8. **Gelişmiş (ham JSON)** grup başına fallback korundu.

## Kabul kriterleri — karşılandı

| Kriter | Kanıt |
|---|---|
| 84 alan render (enum→select, bool→toggle, int/float→sayı+ge/le, list→satır, dict→çok-satır); şema-güdümlü test geçer | pytest 31/31 (widget çıkarımı + sahte-alan otomatik) |
| Arama/filtre canlı; "değişmiş" sayacı doğru | Playwright: canlı arama (ad+açıklama), riskli süzgeç, `değişmiş (2)` sayacı |
| Alan kaydı: geçerli→PATCH+audit+rozet; geçersiz (hnsw_m=400)→alan-altı hata, DB değişmez | pytest (hnsw_m le=100 reddi, çapraz-alan) + backend PATCH kontratı değişmedi |
| Danger/varsayılan/geri-al doğru; katlanır alt-grup çalışır | Playwright: danger ikonu, diff-nokta + "varsayılan: 16", section varsayılan-kapalı + eşleşmede açılır |
| Mevcut testler yeşil; ham-JSON fallback çalışır | tüm paket yeşil; Playwright: ham-JSON toggle |

## Doğrulama kanıtları

- **Şema-güdümlü birim:** `tests/test_faz_m5_config_ui_schema.py` → **31/31** (backend değişmediği için M-11 bunları kıramaz; sahte alan UI şemasına otomatik düşer).
- **Gerçek-Chromium render (Playwright, mock backend):** **18/18** — yoğun satır, sahte alan + danger ikonu, diff-nokta + "varsayılan: X", `değişmiş` sayacı, ⓘ aç/kapa, canlı arama (ad+açıklama), eşleşmede section otomatik açılır, riskli süzgeç, section varsayılan-kapalı, düzenle→dirty (otomatik-kayıt YOK), Kaydet→PATCH ✓ flash, kayıt sonrası nokta, ham-JSON fallback, **sıfır JS hatası**.
- **Canlı (H200):** aynı statik `admin.html` imaja gömülü olarak dağıtıldı; config PATCH + tam-grup doğrulama H200 DB'sinde canlı doğrulandı (panelden `allowed_email_domains` kaydı 200 döndü — dağıtım sırasında). Canlı-served smoke: `curl -s localhost:8000/admin | grep -Eoc 'cfg-toolbar|class="frow"|fdot diff|subsec'`.

## Yorum kararları (raporlandı)

- Arama, aktif sekmedeki gruplar arası çalışır (sekmeler-arası değil).
- Bir aramanın açtığı alt-bölüm, süzgeç temizlenince açık kalır (bilinçli: kayıt sonrası çökmesin).

**Karar:** M-11 (Admin Config yoğun-satır render yenilemesi) tamamlandı; backend kontratı korunarak kabul kriterleri karşılandı. Kullanıcılar / Doküman&QC sekmeleri bu turda değişmedi.
