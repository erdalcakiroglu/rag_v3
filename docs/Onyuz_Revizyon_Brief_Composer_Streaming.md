# Önyüz Revizyon Brief'i — Alt-Sabit Composer + Streaming Yerleşimi

**Hedef:** Sohbet arayüzünü Claude kalıbına yaklaştırmak: giriş kutusu boş ekranda ortada,
konuşma başlayınca alta iner ve orada sabitlenir. Aşama-streaming (`/api/ask/stream`) bu yeni
yerleşimle doğru eşleşsin.

**Kime:** VS Code / Sonnet 4.8 (uygulayıcı)
**Kimden:** Mimari
**Tarih:** 2026-07-24

---

## 0. Kapsam ve DOKUNMA listesi (önce bunu oku)

Bu **saf önyüz** revizyonudur. Aşağıdakilere **DOKUNULMAZ**:

- `/api/ask` ve `/api/ask/stream` **sözleşmesi** (olay adları, yük şekli, sıralama) — aynen kalır.
- `compose` düğümü, grounding / PII maskeleme / citation yerleşimi — M-16 mührü.
- Olay yükü **allowlist**'i (`tool_event` yalnız `query`≤120krk + sayılar; `node_event`
  bilinmeyen düğüm→`None`) — M-15 güvenlik kararı.
- `run_agent`, `run_agent_stream`, eval yolu (`eval/harness.py:151`) — karne yeniden koşulmaz.
- AuthN'in akıştan önce koşması, SSE `json.dumps` kare kaçışı, `X-Accel-Buffering: no`.

**Değişen tek katman:** DOM yapısı + CSS yerleşimi + istemci-tarafı scroll/composer davranışı
(JS). Backend'e istek gitmez. Bu değişiklik **kabul karnesini geçersiz kılmaz.**

---

## 1. İki durum + bir geçiş

Arayüzün iki hali var:

| durum | tetik | giriş kutusu konumu | içerik alanı |
|---|---|---|---|
| **boş** | oturumda henüz mesaj yok | dikey ortada, karşılama başlığıyla | yok (yalnız karşılama) |
| **sohbet** | ilk soru gönderildi | **alta sabit**, tam genişlik | üstte, kaydırılabilir mesaj listesi |

- Geçiş **tek yönlü** ve oturum başına bir kez: ilk `submit`'te giriş ortadan alta iner.
  Kısa bir CSS transition (200-300ms) yeterli; abartılı animasyon istemiyoruz.
- "Yeni sohbet" tıklanınca → boş duruma dön (giriş tekrar ortada).
- Boş durumdaki karşılama: mevcut kimliğe uygun (ör. "ragintel — kurumsal doküman asistanı",
  örnek soru placeholder'ı korunur). Claude'un metnini kopyalama, kendi kimliğimiz.

---

## 2. Yapış-alta (stick-to-bottom) scroll — EN KRİTİK kısım

Alt-sabit girişin bedeli scroll yönetimidir. Kural:

1. **Takip modu (varsayılan):** kullanıcı en alttayken yeni içerik (mesaj veya aşama olayı)
   eklendikçe otomatik en alta kay. Streaming sırasında `open`/`tool`/`retrieved`/`step`/`final`
   olayları geldikçe ekran akışı takip eder.
2. **Serbest mod:** kullanıcı yukarı kaydırırsa (eski cevabı okuyor) **otomatik kaydırma DURUR.**
   Akış devam etse bile ekran zıplamaz.
3. **"En alta git" düğmesi:** serbest moddayken ve altta yeni içerik varken sağ-altta yüzen bir
   düğme çıkar; tıklanınca takip moduna döner.
4. Takip modundayken kullanıcı ↑ birkaç px kaydırınca serbest moda geç; en alta gelince tekrar
   takip moduna dön. (Eşik: `scrollHeight - scrollTop - clientHeight < ~40px` → "alttayım".)

> Bu doğru yapılmazsa akış sırasında ekran zıplar — arayüzün en sinir bozucu hatası. Streaming'in
> tüm değeri buradan gelir; test edilmeden kapatılmaz.

---

## 3. Composer (giriş kutusu) davranışı

- **Enter = gönder**, **Shift+Enter = yeni satır.**
- **Otomatik büyüyen textarea:** tek satırdan başlar, içerik arttıkça belirli bir max yüksekliğe
  (ör. ~6-8 satır) kadar büyür, sonra kendi içinde kaydırılır. Sabit tek-satır input DEĞİL.
- **Koşarken kilit:** bir soru işlenirken (ilk `open` olayı ile `final`/hata arası) giriş
  disabled; "Sor" düğmesi "Durduruluyor…"/durdur haline geçer (mümkünse `AbortController` ile
  `fetch` stream'i iptal — backend zaten kilidi serileştiriyor, iptal yalnız istemci-tarafı UX).
- Boş girişte veya yalnız boşlukta gönder engellenir.
- Gönderince giriş temizlenir; kullanıcı mesajı hemen listeye eklenir (optimistik), ardından
  streaming başlar.

---

## 4. Aşama olaylarının yerleşimi (streaming ile uyum)

- Aşama satırları (Belgelerde arıyorum… / N kaynak bulundu / Bağlamı genişletiyorum /
  Kaynaklar doğrulanıyor) **asistan cevabının belireceği yerde**, girişin hemen üstünde akar.
- `final` gelince aşama satırları ya kaybolur ya da "gösterildi" halinde küçülür; nihai cevap
  + KAYNAKLAR + "yüksek güven" rozeti + tur/token/trace meta satırı mevcut düzeniyle basılır.
- Aşama satır metni **`textContent`** ile yazılır, `innerHTML` ile DEĞİL (arama sorgusu model
  üretimi metindir — M-15 kuralı, burada da geçerli).
- Akış kullanılamazsa (`/api/ask/stream` başarısız) sessizce `/api/ask`'e düş — bugünkü davranış.

---

## 4b. Cevap metni okunabilirliği

Öncelik sırasına göre — ilk üçü etkinin çoğu. **1-4 ve 6 saf önyüz (CSS/JS)**; 5 backend'e dokunur.

| # | kaldıraç | değer | katman |
|---|---|---|---|
| 1 | **Satır uzunluğu (measure)** | cevap sütunu ~560-680px / satır başına 60-75 krk; ekranın kalanı boş kalır | CSS |
| 2 | **Boyut + kontrast + satır yüksekliği** | 15-16px, açık renk (ör. #e6ebf5), line-height 1.7-1.75 | CSS |
| 3 | **Serif = "asistan sesi"** | cevap METNİ serif; rozet/meta/KAYNAKLAR sans kalır (göz "cevap" ile "arayüz"ü ayırır) | CSS |
| 4 | **Atıf işaretleri akışı bölmesin** | satır-içi `[1]` yerine üst-simge (¹) tıklanabilir/hover'da kaynağı gösteren çip | CSS + küçük JS |
| 6 | **Nefes alanı** | rozet↔metin↔KAYNAKLAR arası cömert boşluk, blok etrafında padding | CSS |

**5. Paragraf kırılımı — TEK backend dokunuşu, ayrı karar:** cevap şu an tek blok; 2-4 cümlede
bir paragraf gözü dinlendirir. Bunun için ya `compose` cümle sayısına göre paragraf üretir, ya
da modelin `submit_answer`'da verdiği paragraf yapısı korunur. Bu **M-16 mühür bölgesi** (`compose`) —
bu brief'in saf-önyüz kapsamı DIŞINDA. Yapılacaksa küçük bir test + gerekçe ister; şimdilik **opsiyon**.

**7. Opsiyonel (ileri):** cümleye hover'da ilgili kaynağı hafifçe aydınlatma. Değerli ama karmaşık; backlog.

---

## 5. Kabul kriterleri (uygulayıcı bunları göstermeli)

- [ ] Boş ekran: giriş ortada + karşılama. İlk soruda alta iner, geçiş pürüzsüz.
- [ ] Sohbet ekranı: giriş alta sabit; mesaj listesi üstte bağımsız kaydırılıyor.
- [ ] Uzun konuşmada en alttayken akış otomatik takip ediyor; ekran zıplamıyor.
- [ ] Kullanıcı yukarı kaydırınca akış takibi duruyor; "en alta git" düğmesi çıkıyor ve çalışıyor.
- [ ] Enter gönderiyor, Shift+Enter satır ekliyor, textarea büyüyüp max'ta içeride kayıyor.
- [ ] Koşarken giriş kilitli + durdur düğmesi; iptal istemcide akışı durduruyor.
- [ ] "Yeni sohbet" boş duruma döndürüyor.
- [ ] Aşama satırları girişin üstünde akıyor; `final` mevcut cevap/KAYNAKLAR/rozet düzenini bozmuyor.
- [ ] Cevap metni dar sütunda (~560-680px), 15-16px serif, yüksek kontrast, satır yük. ≥1.7.
- [ ] Atıf işaretleri üst-simge/çip olarak akışı bölmüyor; tıklama/hover ilgili kaynağı gösteriyor.
- [ ] Küçük ekran (dar pencere) ve mobil genişlikte giriş+liste taşmıyor.
- [ ] Backend'e tek yeni istek türü eklenmedi; sözleşme/güvenlik allowlist'i değişmedi (diff kanıtı).

---

## 6. Not

Backend sabit olduğundan bu iş **karneyi geçersiz kılmaz** ve M-15/M-16 mühürlerine dokunmaz.
Diff yalnızca şablon/CSS/istemci-JS dosyalarında görünmeli. Görünürse endişe: `agent`, `compose`,
`stream` event üretimi, allowlist veya auth dosyalarında değişiklik. Bunlar bu brief'in kapsamı
DIŞINDA — çıkarsa ayrı gerekçe ister.

---

## 7. Gelecek çalışma — kullanıcı ayarlanabilir tercihler (backlog, bu revizyonun kapsamı DIŞINDA)

Bu revizyonda **sabit** değerlerle (dar sütun, serif, koyu tema, tek model) başlanır. Sonraki
aşamada aşağıdakiler **kullanıcı tarafından değiştirilebilir** hale getirilir. Şimdilik yalnız NOT:

| tercih | ne değişir | saklama | katman |
|---|---|---|---|
| **Yazı ve font** | cevap metni boyutu (S/M/L), satır uzunluğu, serif↔sans | kullanıcı profili (Postgres) + oturum (Redis) | önyüz + küçük ayar API'si |
| **Tema** | koyu / açık (belki yüksek-kontrast) — CSS değişkenleriyle | aynı | saf önyüz + tercih kaydı |
| **Model** | agent modeli seçimi (ör. qwen3.5:35b ↔ alternatif); yetki/whitelist'e tabi | kullanıcı profili + `app_config` sınırları | önyüz seçim + backend model yönlendirme |

Tasarım ilkeleri (uygulanınca):
- Tercihler **kullanıcı-başına** saklanır (mevcut kullanıcı/oturum altyapısı üzerine); varsayılan
  = bu brief'teki sabit değerler.
- **Tema ve font saf sunum** — CSS değişkeni katmanı baştan buna hazır kurulmalı (renkleri/ölçüleri
  değişkenlere bağla, sabit hex gömme) ki sonra ayar eklemek yeniden yazım gerektirmesin.
- **Model seçimi backend'e dokunur** ve güvenlik/yetki kapısına tabidir: kullanıcı yalnız
  whitelist'teki modelleri seçebilir; seçim `app_config` sınırlarını aşamaz; veri egemenliği
  istisnası kapalı kalır. Ayrı karar + kabul kaydı ister.
- Bu revizyonu **CSS değişkeni disiplini** ile yapmak, gelecekteki tema/font ayarını neredeyse
  bedelsiz kılar — brief'in en önemli ileriye-dönük kazancı budur.
