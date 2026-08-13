# RagIntel Golden Dataset v1 — BDDK korpusuna ÇIPALANMIŞ (30 soru)

> Bu belge `RagIntel_Turk_Bankacilik_Golden_Dataset_v1.md` taslağının yerine geçer.
> Taslak bilgiden yazılmıştı; bu sürüm **korpusun metninden kesildi**.
> Kaynak: `golden_v1_kaynak_probe` çıktısı (`docs/result.txt`, 2026-08-11).

## 0. Neden yeniden yazıldı

Taslağın 30 sorusundan **21'i korpusta bulunmayan yönetmeliklere** çıpalanmıştı.
Bu bir atıf hatası değil, ölçüm aracının kendisinin boşlukta durmasıydı — eski
golden'ın başına gelenin (quote eşleme 0/43, tüm metrikler 0.000) aynısı.

Ölçüldü (`golden_mevzuat_kimlik_probe` Bölüm D + `golden_v1_kaynak_probe`):

| Taslağın dayandığı kaynak | Korpusta kaynak metni | Düşen soru |
|---|---|---|
| Sermaye Yeterliliği Yönetmeliği | **YOK** | E03, M01, M04, M05, H01, H02, H06, H07, H09 |
| Likidite Yeterliliği Yönetmeliği | **YOK** | E04, M05, H01, H06, H07 |
| ~~Kaldıraç Yönetmeliği~~ | **VAR — bu satır ÇÜRÜDÜ**, aşağıya bkz. | E09, M01, H01, H09 |
| Sermaye Tamponları Yönetmeliği | **YOK** | M04, H09 |
| YP Net Genel Pozisyon Yönetmeliği | **YOK** | M09 |
| Sistemik Önemli Bankalar Yönetmeliği | **YOK** (yalnız `mevzuat_1167` atıf yapar) | E10, M10 |
| Karşılık Yönetmeliği | **YOK** | E05, M03, H02, H03, H06, H08 |

Korpusun gerçek bileşimi: **kanunlar** (5411 / 5464 / 6361) + **BDDK rehberleri**
+ **tebliğ/genelgeler** + **TBB yayınları**. Set bunun üzerine kuruldu.

### 0b. DÜZELTME (2026-08-12) — "yönetmelik metni yok" hükmü fazla genişti

`mevzuat_1340.pdf` başka bir sebeple incelenirken (M10'un atıf alıntısı oraya da
düşüyordu) **kaldıraç yönetmeliğinin kaynak metni olduğu görüldü**:

```
chunk#8  s.3  bölüm: Sistemik önemli bankaların kaldıraç oranları
         "MADDE 5 -(1) Kurul, … Sistemik Önemli Bankalar Hakkında Yönetmeliğin
          3 üncü maddesinin birinci fıkrasının (o) bendi uyarınca sistemik önemli
          banka olarak tanımlanan bankalar için 4 üncü maddenin üçüncü fıkrasında
          belirtilen asgari oranlardan daha ihtiyatlı … kaldıraç oranı belirlemeye
          yetkilidir."
chunk#9  s.3  bölüm: Toplam risk tutarının hesaplanması        (MADDE 6)
chunk#11 s.3  bölüm: Bilanço içi varlık risk tutarı            (MADDE 7)
```

Yani yukarıdaki tablonun "Kaldıraç Yönetmeliği → YOK" satırı **yanlış**.

Kökü önemli, çünkü aynı hata başka satırlarda da olabilir: dosyalar
`mevzuat_NNNN.pdf` diye adlandırılmış, kapak sayfası taşımıyor ve
`golden_mevzuat_kimlik_probe` yönetmelikleri **ad/kapak deseniyle** arıyordu —
madde gövdesiyle değil. `golden_v1_kaynak_probe`'un `sistemik önemli banka`
desenini "ölü" ilan etmesi de aynı sebepten: tepe isabetler kısaltma tablolarıydı,
gerçek düzenleme sıralamada altta kaldı.

**Bunun kapsamı ölçülmedi.** Kalan 6 "YOK" satırı doğrulanmış sayılmamalı;
teyit madde gövdesinden aranarak yapılır, ör.:

```bash
python scripts/golden_alinti_probe.py \
  --alinti "asgari kaldıraç oranı" --alinti "sermaye koruma tamponu" \
  --alinti "likidite karşılama oranı hesaplanmasında"
```

**Bu setin 30 sorusunu geçersiz kılmaz** — 31 alıntının 31'i korpustan kesilip
doğrulandı, hiçbiri bu satırlara dayanmıyor. Etkisi iki yerde:
- **Genişletme fırsatı:** kaldıraç konulu sorular (taslağın E09/M01/H01/H09'u)
  artık `mevzuat_1340` üzerine yazılabilir.
- **§5c'nin dayanağı çürük:** `unanswerable` kolu "bu yönetmelikler korpusta yok"
  varsayımına oturacaktı; o varsayım artık tek tek ölçülmeden kullanılamaz.

## 1. Bilerek DIŞARIDA bırakılanlar

**5411 m.33 (bağımsız denetim) — KORPUS ÇELİŞİYOR, soru yazılamaz.**
Korpustaki iki baskı farklı hüküm veriyor:

- `5411 sayılı Bankacılık Kanunu.pdf` chunk=38 → *"(Değişik birinci fıkra: 6/12/2012-6362/145 md.) Kamu Gözetimi, Muhasebe ve Denetim Standartları Kurumu tarafından yetkilendirilmiş…"* — **yürürlükteki metin**
- `5411_Guncel_2.pdf` chunk=38 → *"Bu Kanunun 15 inci maddesine göre yetkilendirilecek bağımsız denetim kuruluşlarının çalışmalarına ilişkin esaslar Türkiye Serbest Muhasebeci Malî Müşavirler…"* — **2005 orijinali, mülga**

Adında "Guncel" geçen dosya güncel değil. Retriever hangisini döndürürse
döndürsün "doğru" sayılamaz. Bu bir **korpus kusuru**; ayrı kalem olarak
işlenmeli (yanlış baskının elenmesi veya sürüm etiketlenmesi).

**Ölü desenler** (yetkili kaynağı getirmedi, golden'a girmedi):
`özkaynak` (914 chunk; tepe isabet hesap planı dökümü `mevzuat_1334`),
`gerçeğe uygun değer` (222 chunk; aynı sorun — çipa dosya-adıyla kuruldu),
`likidite karşılama oranı` (tepe isabetler kısaltma tabloları),
`iç sistemlere ilişkin` (tek isabet, yanlış kanun),
`müşteri sırrı` (tepe isabet kaynakça sayfası),
`sistemik önemli banka` (tepe isabet org şeması tablosu — **ama bu "ölü" hükmü
§0b ile çürüdü: desen sıralaması `mevzuat_1340`'ı kaçırdı, kaynak orada**).

**M10'un ikinci isabeti DIŞLANDI.** Alıntı 20 (`…Yönetmeliğin 3 üncü maddesinin
birinci fıkrasının`) `mevzuat_1167` yanında `mevzuat_1340` s.3'e de düşüyordu.
Mükerrer-baskı kuralı burada geçerli değil: 1340, 1167'nin başka baskısı değil —
kaldıraç düzenlemesi, aynı maddeye yalnız *hangi bankalar* sorusunu
çözmek için atıf yapıyor. Gold bırakılsaydı, M10'a ("önlem planı rehberi hangi
bankaları muhatap alır?") kaldıraç hükmü döndüren retriever **doğru** sayılırdı.
Set `--dislanan mevzuat_1340.pdf` ile üretilir: 94 → **93 evidence**, 31 dosya.

**Havuz bayat:** prob 72 dosyalık reprocess'ten ÖNCE koştu; 57 dosya "C0 artığı
(reprocess bekliyor)" diye elenmişti. Reprocess bitti (0x02 korpustan silindi),
prob tekrar koşturulunca havuz büyür ve yeni çıpalar açılabilir.

---

## 2. Kolay — tek doküman, tek chunk (`single_fact`)

| ID | Soru | Golden Answer | gold_evidence (dosya · s. · alıntı) |
|---|---|---|---|
| E01 | Bir banka istediği her finansal faaliyeti serbestçe yürütebilir mi? | Hayır. 5411 m.4 faaliyet konularını **sayma yöntemiyle** belirler; bankalar diğer kanunlardaki hükümler saklı kalmak kaydıyla yalnız maddede sayılan faaliyetleri gerçekleştirebilir (mevduat/katılım fonu kabulü, kredi verme, ödeme ve fon transferi, saklama, kart işlemleri vb.). | `5411_Guncel_2.pdf` s.3 · `5411 sayılı Bankacılık Kanunu.pdf` s.3 — "Bankalar, diğer kanunlarda öngörülen hükümler saklı kalmak kaydıyla aşağıda belirtilen faaliyetleri gerçekleştirebilirler" |
| E02 | Kuruluş izni alan bir banka doğrudan faaliyete başlayabilir mi? | Hayır. Kuruluş veya şube açma izni alan bankaların Kurul'dan **ayrıca faaliyet izni** alması şarttır (5411 m.10). İzinler Resmî Gazete'de yayımlanır. | `5411 sayılı Bankacılık Kanunu.pdf` s.7 — "Bu Kanunun 6 ncı maddesi çerçevesinde kuruluş veya Türkiye'de şube açma izni alan bankaların, Kuruldan ayrıca faaliyet izni alması şarttır" |
| E03 | Bir bankanın tek bir gerçek/tüzel kişiye veya risk grubuna kullandırabileceği kredinin üst sınırı nedir? | Özkaynakların **yüzde yirmibeşi** (5411 m.54). Bu oran m.49/2'deki risk grubu için yüzde yirmi olarak uygulanır; Kurul yüzde yirmibeşe kadar yükseltmeye yetkilidir. | `5411_Guncel_2.pdf` s.21 · `5411 sayılı Bankacılık Kanunu.pdf` s.25 — "kullandırılabilecek kredilerin toplamı özkaynakların yüzde yirmibeşini aşamaz" |
| E04 | Banka kurucu ortaklarında iflas/konkordato açısından hangi şart aranır? | Kurucu ortakların 2004 sayılı İcra ve İflas Kanunu hükümlerine göre **müflis olmaması, konkordato ilan etmemiş olması**, uzlaşma suretiyle yeniden yapılandırma başvurusunun tasdik edilmemiş olması ve haklarında iflasın ertelenmesi kararı verilmemiş olması gerekir (5411 m.8). | `5411_Guncel_2.pdf` s.6 · `5411 sayılı Bankacılık Kanunu.pdf` s.6 — "2004 sayılı İcra ve İflas Kanunu hükümlerine göre müflis olmaması, konkordato ilân etmiş olmaması" |
| E05 | Kurum personelinin görevi sırasında öğrendiği sırlara ilişkin yükümlülüğü nedir? | Kurul/Fon başkan ve üyeleri ile personeli, öğrendikleri banka, bağlı ortaklık, iştirak ve **müşteri sırlarını** kanunen yetkili olanlardan başkasına açıklayamaz ve kendi/başkası yararına kullanamaz (5411 m.73). Yükümlülük dışarıdan destek hizmeti alınan kişi ve kuruluşları da kapsar. | `5411_Guncel_2.pdf` s.28 · `5411 sayılı Bankacılık Kanunu.pdf` s.34 — "görevleri sırasında öğrendikleri bankalara ve bunların bağlı ortaklık, iştirak, birlikte kontrol edilen ortaklıkları ve müşterilerine ait sırları" |
| E06 | Bir veri hangi andan itibaren müşteri sırrı sayılır? | Bankacılık faaliyetlerine özgü olarak **bankayla müşteri ilişkisi kurulduktan sonra oluşan** veriler müşteri sırrı niteliğini kazanır (5411 m.73/3 uyarınca, BDDK 2022 genelgesi). | `mevzuat_1135.pdf` s.2 — "bankayla müşteri ilişkisi kurulduktan sonra oluşan veriler müşteri sırrı niteliğini haiz olmaktadır" |
| E07 | Kart sözleşmelerinin yazılı şekline ilişkin punto/renk şartı nedir? | **En az on iki punto ve koyu siyah harflerle** hazırlanmış yazılı şekil; ya da uzaktan iletişim araçlarıyla mesafeli olarak veya Kurul'un yazılı şekil yerine geçeceğini belirlediği yöntemle (5464 m.24). | `5464 sayılı Banka Kartları ve Kredi Kartları Kanunu` s.9 — "en az on iki punto ve koyu siyah harflerle hazırlanacak yazılı şekilde" |
| E08 | Finansal kiralama sözleşmesi nedir? | Kiralayanın, kiracının talebi ve seçimi üzerine üçüncü kişiden veya kiracıdan satın aldığı ya da hâlihazırda mülkiyetindeki bir malın **zilyetliğini**, her türlü faydayı sağlamak üzere **kira bedeli karşılığında** kiracıya bırakmasını öngören sözleşmedir (6361 m.18). | `6361 sayılı Finansal Kiralama, Faktoring, Finansman…` s.12 — "kiralayanın, kiracının talebi ve seçimi üzerine üçüncü bir kişiden veya bizzat kiracıdan satın aldığı veya başka suretle temin ettiği" |
| E09 | Faktoring sözleşmesi hangi fonksiyonları içerebilir? | Fatura ile tevsik edilen (veya Kurulca belirlenen esaslarla tevsik edilebilen) alacakların devralınması suretiyle **tahsilat**, **borçlu ve müşteri hesaplarının tutulması**, bunun yanı sıra **finansman** veya **faktoring garantisi** fonksiyonlarından biri ya da tümü (6361 m.38). | `6361 sayılı Finansal Kiralama, Faktoring, Finansman…` s.17 — "müşterisine sağladığı tahsilat, borçlu ve müşteri hesaplarının tutulmasının yanı sıra finansman veya faktoring garantisi fonksiyonlarından herhangi birini ya da tümünü içeren sözleşmedir" |
| E10 | Mevduat ve katılım fonları kim tarafından sigorta edilir, kapsam dışı olanlar kimlerdir? | **Tasarruf Mevduatı Sigorta Fonu** tarafından. Kredi kuruluşları nezdindeki **resmi kuruluşlara, kredi kuruluşlarına ve finansal kuruluşlara ait** mevduat/katılım fonları kapsam dışıdır (5411 m.63). Kredi kuruluşları sigortaya tâbi kısım üzerinden prim öder. | `5411 sayılı Bankacılık Kanunu.pdf` s.29 — "haricindeki tüm mevduat ve katılım fonları, Tasarruf Mevduatı Sigorta Fonu tarafından sigorta edilir" |

---

## 3. Orta — doğru bölümü bulma + yorumlama

| ID | Soru | Golden Answer | gold_evidence |
|---|---|---|---|
| M01 | Kurul'un istediği tedbirler alınmazsa banka için süre bakımından nasıl bir sınır işler? | Tedbirlerin Kurul'un verdiği süre içinde **ya da her hâlükârda en geç oniki ay içinde** kısmen/tamamen alınmaması hâlinde — veya alınmasına rağmen mali bünyenin güçlendirilemeyeceğinin tespiti hâlinde — faaliyet izninin kaldırılması ya da Fon'a devir gündeme gelir (5411 m.71). | `5411_Guncel_2.pdf` s.28 · `5411 sayılı Bankacılık Kanunu.pdf` s.33 — "her halükârda en geç oniki ay içinde kısmen ya da tamamen alınmaması" |
| M02 | Kredi riskinde önemli artış olmayan bir finansal araç için hiç karşılık ayrılmaz mı? | Ayrılır. Önemli artış yoksa zarar karşılığı **12 aylık beklenen kredi zararına eşit** bir tutardan ölçülür (TFRS 9 § 5.5.5). Tüm kredi tutarları için daima BKZ hesaplanması esastır; "karşılıksız" bir kategori yoktur. | `mevzuat_0943.pdf` s.12 — "önemli derecede artış meydana gelmemiş olması durumunda işletme söz konusu finansal araca ilişkin zarar karşılığını 12 aylık beklenen kredi zararlarına eşit bir tutardan ölçer" |
| M03 | Ömür boyu beklenen kredi zararı hangi durumda finansal tablolara alınır? | İlk defa finansal tablolara alınmasından bu yana **kredi riskinde önemli artış** olan tüm finansal araçlar için — bireysel ya da toplu olarak, makul ve **ileriye yönelik** olanlar dâhil desteklenebilir tüm bilgiler dikkate alınarak (TFRS 9 § 5.5.4). | `mevzuat_0943.pdf` s.13 — "ilk defa finansal tablolara alınmasından bu yana kredi riskinde önemli artışlar olan tüm finansal araçlar için" |
| M04 | Sorunlu bir alacak yeniden yapılandırıldığında bankanın işi biter mi? | Hayır. Rehber, yapılandırma **uygulandıktan sonra** bankaların bunların **etkililiğini ve etkinliğini izlemesini** ister; yapılandırma olasılıkları sorunlu alacağın olumsuz etkilerini ortadan kaldırmak ve sınırlandırmak amacıyla değerlendirilir. | `mevzuat_1040.pdf` s.13 — "Yeniden yapılandırma uygulanması halinde, bankalar bunların etkililiğini ve etkinliğini izlemelidir" |
| M05 | Likidite ölçütlerinin izlenmesi tek başına yeterli midir? | Hayır. Likidite ölçütlerinden **ayrı olarak**, likidite pozisyonu veya olası fon gereksinimlerindeki artan riskleri önceden tespit eden **erken uyarı göstergeleri** kullanılmalıdır; içsel veriler kadar dışsal göstergeler de kullanılabilir. | `mevzuat_0954.pdf` s.10 — "Likidite ölçütlerinden ayrı olarak, likidite pozisyonu veya olası fon gereksinimlerine ilişkin artan risklerin önceden tespit edilmesine yönelik olarak erken uyarı göstergeleri kullanılmalıdır" |
| M06 | Bankacılık hesaplarından kaynaklanan faiz oranı riski için ölçüm yapmak yeterli midir? | Hayır. İlke 1'e göre BHFOR **tespit edilmeli, ölçülmeli, izlenmeli, kontrol edilmeli ve yönetilmelidir**; ayrıca bankacılık hesaplarından kaynaklanan kredi farkı riski (BHKFFR) de izlenip değerlendirilmelidir. | `mevzuat_1291.pdf` s.3 — "BHFOR bankalarca tespit edilmeli, ölçülmeli, izlenmeli, kontrol edilmeli ve yönetilmelidir" |
| M07 | Faizsiz bankacılık danışma komitesi üyelerinde hangi öğrenim ve deneyim şartları aranır? | Üyelerin **asgari üçte ikisinin** İlahiyat veya dengi alanda en az lisans öğrenimi görmüş **ya da** faizsiz finans alanında yüksek lisans/doktora derecesine sahip olması **ve ayrıca** faizsiz finans alanında **en az üç yıl** mesleki deneyimi bulunması zorunludur; Kurul bu şartları tüm üyeler için arayabilir. | `mevzuat_1323.pdf` s.2 — "faizsiz finans alanında yüksek lisans ya da doktora derecesine sahip olmanın yanı sıra, faizsiz finans alanında en az üç yıl mesleki deneyime sahip olması zorunludur" |
| M08 | Gerçeğe uygun değer ölçümünde yönetişim sorumluluğu kimdedir? | **Yönetim kurulunda.** Risk yönetimi ve finansal raporlama amaçlı olarak gerçeğe uygun değerle ölçülen bütün finansal araçlar için yeterli yönetim yapılanması ve kontrol süreçlerinin oluşturulmasını sağlamak yönetim kurulunun görevidir; süreçler banka genelinde tutarlı ve risk yönetimiyle bütünleşik olmalıdır. | `mevzuat_0945.pdf` s.2 — "Yönetim kurulu, risk yönetimi ve finansal raporlama amaçları için gerçeğe uygun değer yöntemiyle değeri belirlenen bütün finansal araçlara ilişkin yeterli yönetim yapılanmasının ve kontrol süreçlerinin oluşturulmasını sağlamalıdır" |
| M09 | Kredi izlemesinde erken uyarı göstergeleri neye dayandırılmalıdır? | Kredi riskindeki artışları **zamanında** tespit etmeye imkân veren uygun bir **BT ve veri altyapısı** tarafından desteklenen nicel ve nitel EUG'lar; toplam portföy, alt portföy, sektör, coğrafi bölge ve münferit alacak bazında geliştirilmeli, sürdürülmeli ve düzenli değerlendirilmelidir. | `mevzuat_1041.pdf` s.46 — "zamanında tespit etmeye imkan veren uygun bir BT ve veri altyapısı tarafından desteklenen" |
| M10 | Önlem planı rehberi hangi bankaları muhatap alır? | Rehberdeki "Banka" tanımı **Sistemik Önemli Bankalar Hakkında Yönetmeliğin 3'üncü maddesinin birinci fıkrasının (o) bendinde** tanımlanan bankalara atıf yapar; yani rehber sistemik önemli bankalar içindir. *(Not: anılan Yönetmeliğin kendi metni korpusta yoktur — cevap atıf düzeyinde kalır.)* | `mevzuat_1167.pdf` s.1 — "Sistemik Önemli Bankalar Hakkında Yönetmeliğin 3 üncü maddesinin birinci fıkrasının" |

---

## 4. Zor — çok doküman, TEK odak

> Tasarım kuralı: iki parçalı ("X'i ve Y'yi açıklayın") soru **yazılmadı**.
> Ölçüldü ve ders alındı — iki parçalı synthesis sorusu recall'ü sahte olarak
> çökertiyor (bkz. `rerank-ab-onveri`, rr-ext recall %3). Her soru tek bir şey
> sorar; çok-dokümanlılık cevabın **dayanağından** gelir, sorunun parçalarından değil.

| ID | Soru | Golden Answer | gold_evidence |
|---|---|---|---|
| H01 | Bankaların kredi karşılığı ayırma yükümlülüğü hangi düzeyde doğar? | İki katmanlı: **kanun düzeyinde** 5411 m.53, bankalara doğmuş/doğması muhtemel zararlar ve değer azalışları için yeterli düzeyde karşılık ayrılmasına ilişkin **politika oluşturma ve uygulama** yükümlülüğü getirir; **ölçüm düzeyinde** ise tutar TFRS 9 beklenen kredi zararı yaklaşımıyla, BCBS'e paralel üç parametre (TO, THK, RMT) üzerinden belirlenir. | `5411_Guncel_2.pdf` s.20 — "doğmuş veya doğması muhtemel zararların karşılanması ve bunlar dışında kalan varlıkların değer azalışları için yeterli düzeyde karşılık ayrılmasına" · `Finansal_Riskler_ve_Turev_Urunler_2.pdf` s.105 — "beklenen kredi zararı hesaplaması için BCBS tarafından belirlenen beklenen kredi zararı yaklaşımına paralel şekilde 3 temel parametre bulunmaktadır" |
| H02 | Banka bir müşteri verisini üçüncü tarafla paylaşırken hangi çerçeveye tabidir? | 5411 m.73'ün sır saklama yükümlülüğü, verinin **banka-müşteri ilişkisi kurulduktan sonra oluşmuş olması** hâlinde müşteri sırrı rejimini devreye sokar; paylaşım ancak kanunen yetkili olanlara veya ilgili düzenlemelerdeki istisnalar çerçevesinde ve **ölçülü** biçimde yapılabilir. Yükümlülük dışarıdan destek hizmeti alınan kişi/kuruluşları da kapsar. | `5411 sayılı Bankacılık Kanunu.pdf` s.34 — "görevleri sırasında öğrendikleri bankalara ve bunların bağlı ortaklık, iştirak, birlikte kontrol edilen ortaklıkları ve müşterilerine ait sırları" · `mevzuat_1135.pdf` s.2 — "bankayla müşteri ilişkisi kurulduktan sonra oluşan veriler müşteri sırrı niteliğini haiz olmaktadır" |
| H03 | Likidite yetersizliği tek başına bir kuruluşun faaliyet izninin kaldırılmasına yol açabilir mi? | Evet, kuruluş türüne göre. **6361 m.50/A** tasarruf finansman şirketleri için likidite düzeyinin sürdürülememesini veya güvenilir hesaplanamamasını doğrudan bir kaldırma/tasfiye sebebi sayar. **5411 m.71**'de ise bankalar için yol dolaylıdır: önce m.70 tedbirleri, bunların süresinde veya en geç oniki ayda alınmaması hâlinde izin kaldırma/Fon'a devir. | `6361 sayılı Finansal Kiralama, Faktoring, Finansman…` s.26 — "Likidite düzeyinin sürdürülememesi veya sürdürülemeyeceğinin anlaşılması, likidite hesaplamasının güvenilir şekilde gerçekleştirilememesi" · `5411_Guncel_2.pdf` s.28 — "her halükârda en geç oniki ay içinde kısmen ya da tamamen alınmaması" |
| H04 | Henüz sorunlu hâle gelmemiş bir kredide bankanın izleme yükümlülüğü var mıdır? | Evet. Kredi izleme sistemleri, kredi riskindeki artışları zamanında tespit eden **BT ve veri altyapısıyla desteklenen EUG'lar** üzerine kurulur; sorunlu alacak rehberi de yapılandırma sonrası izlemeyi zorunlu tutar. İzleme yükümlülüğü sorunlu hâle gelmeyi beklemez. | `mevzuat_1041.pdf` s.46 — "zamanında tespit etmeye imkan veren uygun bir BT ve veri altyapısı tarafından desteklenen" · `mevzuat_1040.pdf` s.13 — "Yeniden yapılandırma uygulanması halinde, bankalar bunların etkililiğini ve etkinliğini izlemelidir" |
| H05 | Değerleme süreçlerinin doğruluğu bankanın kendi beyanına mı bırakılmıştır? | Hayır. İç katmanda **yönetim kurulu**, gerçeğe uygun değerle ölçülen bütün araçlar için yeterli yönetim yapılanması ve kontrol süreçlerini kurmakla yükümlüdür; dış katmanda **Kurum**, İç Sistemler ve İSEDES Yönetmeliği kapsamında bankaların sermaye gereksinimlerini etkin belirleyip belirlemediğini düzenli denetler ve gerektiğinde talimat/yaptırım mekanizmalarını işletir. | `mevzuat_0945.pdf` s.2 — "Yönetim kurulu, risk yönetimi ve finansal raporlama amaçları için gerçeğe uygun değer yöntemiyle değeri belirlenen bütün finansal araçlara ilişkin yeterli yönetim yapılanmasının ve kontrol süreçlerinin oluşturulmasını sağlamalıdır" · `mevzuat_0944.pdf` s.3 — "Kurum, İç Sistemler ve İSEDES Yönetmeliği kapsamında bankaların riskleri için bulundurmaları gereken sermaye gereksinimlerini etkin bir biçimde belirleyip belirlemediklerini" |
| H06 | Faiz oranı riski için tutulacak sermayeyi kim belirler? | Birincil sorumluluk bankadadır: BHFOR tespit/ölçüm/izleme/kontrol/yönetim döngüsü İlke 1 ile bankaya yüklenir. Ancak bu içsel belirlemenin **etkinliği Kurum tarafından denetlenir** — İç Sistemler ve İSEDES Yönetmeliği kapsamında bankaların riskleri için bulundurmaları gereken sermaye gereksinimlerini etkin belirleyip belirlemedikleri düzenli olarak incelenir. | `mevzuat_1291.pdf` s.3 — "BHFOR bankalarca tespit edilmeli, ölçülmeli, izlenmeli, kontrol edilmeli ve yönetilmelidir" · `mevzuat_0944.pdf` s.3 — "Kurum, İç Sistemler ve İSEDES Yönetmeliği kapsamında bankaların riskleri için bulundurmaları gereken sermaye gereksinimlerini etkin bir biçimde belirleyip belirlemediklerini" |
| H07 | Banka kartı ile kredi kartı arasındaki hukuki fark nedir? | **Banka kartı**, mevduat hesabı veya özel cari hesapların kullanımı dâhil bankacılık hizmetlerinden yararlanmayı sağlar — yani kart hamilinin **kendi hesabına** bağlıdır. **Kredi kartı** ise nakit kullanımı gerekmeksizin mal/hizmet alımı veya nakit çekme olanağı sağlar; fizikî varlığı bulunmayan kart numarası da bu kapsamdadır (5464 m.3). | `5464 sayılı Banka Kartları ve Kredi Kartları Kanunu` s.1 — "Banka kartı: Mevduat hesabı veya özel carî hesapların kullanımı dahil bankacılık hizmetlerinden yararlanmayı sağlayan kartı" ve "Nakit kullanımı gerekmeksizin mal ve hizmet alımı veya nakit çekme olanağı sağlayan basılı kartı" |
| H08 | Faizsiz bankacılık uyumu bankanın ticari tercihi midir, düzenleyici yükümlülük mü? | Düzenleyici yükümlülüktür. Tebliğ, **5411 m.29 ve m.93**'e dayanılarak hazırlanmıştır; kurulan danışma komitesi üst düzey yönetimin ve ilgili tarafların etkisinden **uzak ve bağımsız** karar almak zorundadır ve banka menfaat çatışmalarını önleyecek tedbirleri almakla yükümlüdür. | `mevzuat_1323.pdf` s.1 — "Bu Tebliğ, 19/10/2005 tarihli ve 5411 sayılı Bankacılık Kanununun 29 uncu ve 93 üncü maddelerine dayanılarak hazırlanmıştır" · `mevzuat_1323.pdf` s.2 — "Danışma komitesi, üst düzey yönetim ve ilgili bütün tarafların etkisinden uzak ve bağımsız şekilde karar alır" |
| H09 | Açık bankacılıkta API üzerinden veri paylaşımı yalnızca teknik bir entegrasyon mudur? | Hayır. API teknik olarak "bir yazılım veya programın hedef yazılımda belirlenen işlev ve bilgileri kullanmasını sağlayan arayüz"dür; ancak paylaşım Bankaların Bilgi Sistemleri ve Elektronik Bankacılık Hizmetleri Hakkında Yönetmelik kapsamında **bilgi sistemlerinin yönetimi ve elektronik bankacılık hizmetlerinin sunulması** rejimine tabidir; buna kimlik doğrulama ve işlem güvenliği kriterleri eklenir. | `acik-bankacilik-uygulamalari-potansiyel-etkileri-ve…` s.21 — "bir yazılım veya programın hedef yazılım veya programda belirlenen işlev ve bilgileri kullanmasını sağlayan arayüzdür" · aynı dosya s.28 — "bankaların işlemlerini gerçekleştirirken kullandıkları bilgi sistemlerinin yönetimi ile elektronik bankacılık hizmetlerinin sunulması" |
| H10 | Likidite göstergelerinde bozulma tespit eden bir bankanın hazır bulundurması gereken nedir? | Erken uyarı göstergeleri bozulmayı **önceden** tespit etmeye yarar; tespitin karşılığı, mali bünyeyi **korumak veya iyileştirmek ve faaliyetleri sürdürmek** amacıyla önceden hazırlanmış, zamanında ve etkili şekilde uygulanabilecek bir **önlem planıdır**. Gösterge ile plan aynı zincirin iki halkasıdır. | `mevzuat_0954.pdf` s.10 — "Likidite ölçütlerinden ayrı olarak, likidite pozisyonu veya olası fon gereksinimlerine ilişkin artan risklerin önceden tespit edilmesine yönelik olarak erken uyarı göstergeleri kullanılmalıdır" · `mevzuat_1167.pdf` s.1 — "mali bünyelerini korumak veya iyileştirmek ve faaliyetlerini sürdürmek amacıyla" |

---

## 5. Kaynak dağılımı

| Kaynak | Tür | Soru |
|---|---|---|
| 5411 sayılı Bankacılık Kanunu (2 baskı) | Kanun | E01–E05, E10, M01, H01, H02, H03 |
| 5464 sayılı Banka/Kredi Kartları Kanunu | Kanun | E07, H07 |
| 6361 sayılı Fin. Kiralama, Faktoring, Finansman | Kanun | E08, E09, H03 |
| `mevzuat_0943` TFRS 9 rehberi | Rehber | M02, M03 |
| `mevzuat_0944` denetim / İSEDES | Rehber | H05, H06 |
| `mevzuat_0945` gerçeğe uygun değer | Rehber | M08, H05 |
| `mevzuat_0954` likidite | Rehber | M05, H10 |
| `mevzuat_1040` sorunlu alacak | Rehber | M04, H04 |
| `mevzuat_1041` kredi izleme | Rehber | M09, H04 |
| `mevzuat_1167` önlem planı | Rehber | M10, H10 |
| `mevzuat_1291` BHFOR | Rehber | M06, H06 |
| `mevzuat_1323` faizsiz bankacılık | Tebliğ | M07, H08 |
| `mevzuat_1135` sır paylaşımı genelgesi | Genelge | E06, H02 |
| `acik-bankacilik-…` | TBB yayını | H09 |
| `Finansal_Riskler_ve_Turev_Urunler_2` | Kitap | H01 |

15 ayrı kaynak, 20 ayrı chunk. Tek dosyaya çıpalama yok — mükerrer baskılarda
alıntı **tüm baskılarda** evidence olarak yazılır (`golden_alinti_probe`
mükerrer-baskı kuralı), yoksa recall sahte olarak çöker.

Alıntı çözümlemesi koştu (`--alinti-dosya`, 2026-08-11): **31 alıntının 31'i
`ISABET >= 1`**, sıfır ıska. Çözümleme 32 dosyada **77 ayrı `(dosya, sayfa)`
evidence girdisi** verdi; bunlar 30 kayda dağıtılınca (H katmanı çıpa paylaşır)
toplam 94 evidence oluyor; §1'deki `mevzuat_1340` dışlaması bir girdi düşürünce
**93 evidence / 31 dosya**. Ham çıktı `docs/golden_v1_evidence.json`.

## 5b. Mükerrer baskının recall'e ÖLÇÜLEN bedeli

Mükerrer-baskı kuralı doğru ama bedava değil. `recall_at_k` bu kod tabanında
sert paydalı: `len(gold ∩ topk) / len(gold)` (`ragintel/eval/metrics.py`).
5411 korpusta 6 baskıda durduğu için bazı soruların gold kümesi 6–9 chunk;
k=5'te bu kümenin tamamı **fiziksel olarak** dönemez.

| Kayıt | gold chunk | recall@5 tavanı | recall@10 | recall@20 |
|---|---|---|---|---|
| `gs-bddk-h01` | 9 | **0.556** | 1.000 | 1.000 |
| `gs-bddk-e03` | 8 | **0.625** | 1.000 | 1.000 |
| `gs-bddk-e01` | 7 | **0.714** | 1.000 | 1.000 |
| `gs-bddk-e02` / `h02` / `h03` | 6 | **0.833** | 1.000 | 1.000 |

**Set geneli recall@5 tavanı = 0.946.** Kusursuz bir retriever bile bunu aşamaz;
30 kaydın 6'sı etkileniyor. recall@10 ve recall@20 tavanı 1.000 — sorun yalnız
k=5'te.

Sonuçlar:
- **Manşet metrik recall@10 olmalı.** recall@5 okunacaksa 1.0'a değil **0.946'ya**
  göre okunur; aksi hâlde korpus mükerrerliği retriever kusuru gibi raporlanır.
- `nDCG@k` ve `MRR` bu tavandan **etkilenmez** (IDCG `min(|gold|, k)` alır,
  MRR ilk isabete bakar) — k=5'te sağlıklı okunan metrikler bunlar.
- Metrik kodu **değiştirilmedi**. Gerçek çözüm ölçüde değil korpusta: 5411'in
  fazla baskılarının elenmesi (§1'deki korpus kusuru kalemiyle aynı iş) tavanı
  kendiliğinden 1.0'a çıkarır.

### 5b-DÜZELTME (2026-08-12) — tavan hesabı bedeli AZ GÖSTERDİ

Yukarıdaki tavan tablosu aritmetik olarak doğru ama **yanlış mekanizmayı**
ölçüyor, ve ona dayanarak §8'de yazdığım "düşüklüğün kaynağı mükerrer baskı
DEĞİL" cümlesi **yanlıştı**. Tavan, "k kaç kanıt sığdırabilir" sorusunun cevabı;
gerçek bedel ise baskıların **birbiriyle yarışması**.

Teşhis probu ölçtü (`docs/synth2.txt`), `gs-bddk-e01`:

| baskı | sıra |
|---|---|
| `Bankacilik_Kanunu_2.pdf` | **10** |
| `BankacilikKanunu_11.baski-web_2.pdf` | 13 |
| `5411 sayılı Bankacılık Kanunu.pdf` | 15 |
| `5411_Guncel_2.pdf` | 21 |

Aynı cümlenin dört kopyası top-21'in dördünü işgal ediyor. gold_n=7 olduğu için
tavan k=10'da 1.000 — yani tavana göre "sorun yok". Oysa **ölçülen** recall@10
= 1/7 = **0.143**. Sistem cevabı 10. sırada bulmuş; metrik onu, kalan beş
kopyayı getirmediği için cezalandırıyor.

İki ayrı sonuç:

1. **Ölçüm çarpıtması.** `single_fact` için ayrı-alıntı bazında top-10 isabet
   **%78.9**, karnedeki recall@10 ise **0.6786**. Fark, kullanıcı için hiçbir
   şey ifade etmeyen "kaç kopya getirdin" sorusundan geliyor. Doğru semantik
   *any-of*: mükerrer baskı grubu TEK gold kalem sayılmalı.
2. **Üretim kusuru — ölçümden bağımsız.** Bu yarış canlıda da oluyor.
   `default_top_k=10` ile kullanıcıya giden bağlamın dört slotu aynı metnin
   kopyaları olabiliyor; context_token_budget bir kez ödenip dört kez
   harcanıyor ve o slotlarda durabilecek başka kanıt dışarı itiliyor.
   Yani baskı elemesi bir eval temizliği değil, **erişim kalitesi işi**.

Metrik kodu yine değiştirilmedi (any-of varyantı yazmak, ölçütü kusurun
üstüne örtmek olurdu). Doğru sıra: önce fazla baskıların korpustan elenmesi,
sonra re-baseline.

## 5c. AÇIK EKSİK — `unanswerable` kolu yok

v0'da 36 kaydın **5'i** `answerable=false` idi. Bu sette **0**. Taslakta da
yoktu; yeni bir eksik değil ama devredilmemeli: M-17 honesty ölçütü
(`honest = declined ∧ (kaynak=0 ∨ coverage=1.0)`) cevaplanamaz soru olmadan
**hiç koşamaz**. Set bu hâliyle retrieval'i ölçer, dürüstlüğü ölçmez.

Kolu kurmanın planı §0'daki "şu 7 yönetmelik korpusta yok" listesine oturacaktı
— "asgari sermaye yeterliliği oranı yönetmelikte kaç?" tipi sorular tam da
cevaplanamaz olurdu. **§0b bu dayanağı çürüttü:** o listenin kaldıraç satırı
yanlış çıktı, kalan 6 satır da aynı yöntemle (ad/kapak deseni) üretilmişti.

Dolayısıyla yokluk **soru soru ölçülmeden** bu kol yazılmaz. İki ayrı risk var
ve ikisi de dürüst sistemi yanlış saydırır:
- yönetmelik metni aslında korpusta (kaldıraçta olduğu gibi),
- yönetmelik yok ama korpustaki kitaplar aynı oranı anlatıyor
  (ör. `Kitap-Banka_Muhasebesi`, `Finansal_Riskler_ve_Turev_Urunler_2`).

Her aday `unanswerable` soru için `golden_alinti_probe --alinti` ile
**ISABET: 0** görülmelidir; ancak o zaman cevaplanamazlık ölçülmüş olur.

## 6. Puanlama ve veri modeli

Taslağın 4–9. bölümleri (10 puan/soru, ağırlıklı metrikler %35/%30/%25/%10,
`required_concepts`, retrieval/generation ayrımı) **aynen geçerlidir** ve
buraya tekrarlanmadı — bkz. `RagIntel_Turk_Bankacilik_Golden_Dataset_v1.md`
bölüm 4–9.

## 7. Doğrulama zinciri

**Adım 1 — alıntı çözümlemesi. KOŞTU, GEÇTİ (2026-08-11).**

```bash
python scripts/golden_alinti_probe.py --alinti-dosya docs/golden_v1_alintilar.txt
```

31/31 `ISABET >= 1`, sıfır ıska. Tek bir `ISABET: 0` bile
`gates.evidence_precondition`i çıkış 2'ye düşürür ve gate hiç koşmazdı.
Çıktı `docs/golden_v1_evidence.json` olarak saklandı.

**Adım 2 — `doc_scope` teyidi. KOŞTU, GEÇTİ (2026-08-12).**

Sonuç: **tek scope `'default'`, 1118 dosya / 43962 chunk.** Bölünme yok, gold
evidence dosyalarının tamamı bu scope'ta. Adım 3'e `--doc-scope default` verilir.

Neden ölçüldü:

`map_gold_chunks` adayları `f.file_name = … AND f.doc_scope = rec.doc_scope`
ile süzer (`ragintel/eval/repository.py`). Scope yanlışsa aday kümesi **boş**
döner, 93 evidence'ın 93'ü unmapped olur ve metrikler 0.000 çıkar — eski
golden'ın arızasının birebir aynısı, üstelik "korpus kötü" gibi okunur.
Varsayım yasak, ölçülür. Tek dosyaya değil **korpus geneline** bakılır — scope'lar
bölünmüşse tek dosya yanıltır:

```bash
python scripts/golden_aday_tarama_probe.py --limit-table 0 --limit-synth 0
```

**Adım 3 — JSONL üretimi (DB'ye dokunmaz).**

```bash
python scripts/golden_v1_jsonl_uret.py --doc-scope default --dislanan mevzuat_1340.pdf
```

`--dislanan` gerekçesi §1'de. 30 kayıt / 93 evidence / 31 dosya yazar ve `load_golden_jsonl` şema+benzersizlik
doğrulamasından geçirir. Soru-cevap metni bu belgeden, alıntılar probe
çıktısından okunur — script hiçbir metni kendi yazmaz.

**Adım 4 — kuru koşum (yükleme ÖNCESİ). KOŞTU, GEÇTİ (2026-08-12).**

```bash
python -m ragintel.eval retrieval --from-file eval/golden/v1.jsonl --variant hybrid --json
```

`--from-file` seti DB'ye yazmadan koşar. Bakılacak tek sayı önce metrikler
değil **eşleme oranı**: `mapped_evidence` 93/93 olmalı. 93'ün altındaysa
`unmapped` listesi hangi `(dosya, sayfa, alıntı)` üçlüsünün düştüğünü söyler —
neredeyse kesin `doc_scope` ya da sayfa kayması demektir, o hâlde yükleme yapılmaz.

Sonuç: **93/93 = 1.0, unmapped 0.** Ölçüm aracı ayakta; bundan sonraki her
sayı korpus/retriever hakkındadır, araç hakkında değil.

Metrikler okunurken §5b geçerli: **manşet recall@10**, recall@5 tavanı 0.946.

Çıktı `2>&1` ile dosyaya alınırsa başına log satırları düşer ve `json.load`
patlar; ilk `gold_mapping` taşıyan nesne `JSONDecoder().raw_decode` ile taranır.

**Adım 5 — yükleme. Ayrı ve bilinçli adım; hiçbir probe bunu yapmaz.
KOŞTU (2026-08-12): `inserted: 30, skipped: 0`.**

```bash
python -m ragintel.eval load eval/golden/v1.jsonl --version v1-bddk
```

---

## 8. v1 BASELINE — ilk geçerli karne (2026-08-12, `hybrid`, n=30)

Eşleme 93/93 olduğu için bu sayılar **gerçek**: araç kusuru değil, sistemin
BDDK korpusundaki hâli. Karbon-vergisi dönemine ait hiçbir rakamla
karşılaştırılamaz (farklı korpus, farklı set) — bu satır sıfır noktasıdır.

| | recall@5 | recall@10 | recall@20 | nDCG@10 | MRR | n |
|---|---|---|---|---|---|---|
| **genel** | 0.4093 | **0.4968** | 0.5704 | 0.3824 | 0.3882 | 30 |
| `single_fact` | 0.5667 | 0.6786 | 0.7152 | 0.5189 | 0.5180 | 19 |
| `synthesis` | 0.0511 | **0.1011** | 0.2522 | 0.0611 | 0.0806 | 10 |
| `citation_sensitive` | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1 |

`citation_sensitive` n=1 — istatistik değil, tek gözlem; "%100" diye okunamaz.

**Manşet bulgu: `synthesis` çöküyor.** single_fact recall@10 0.6786 iken
synthesis 0.1011 — 6.7 kat fark. Bu setin ölçmek için var olduğu şey tam da bu.

Üç aday açıklama vardı; **üçü de ölçüldü, §9'a bakınız.** Aşağıdaki liste
hipotezlerin ilk hâlidir, hüküm değildir:

1. **Yapısal ceza.** synthesis kayıtlarının gold'u iki AYRI dokümandan geliyor;
   tek sorgunun top-k'sında ikisinin de bulunması gerekiyor. `recall_at_k`
   paydası sert olduğu için bir çıpayı bulmak 0.5 veriyor. Ama bu tek başına
   yetmiyor: kuyrukta h08 ve h10 **tüm k'larda 0.0** — hiçbir çıpa bulunmamış.
   Yani ceza var, ama tek sebep o değil.
2. **Sorgu-doküman uyumsuzluğu.** Soru A+B konusunu birlikte soruyor; retriever
   A'nın dokümanına kilitleniyor, B hiç yüzeye çıkmıyor.
3. **Ölçüt kusuru ihtimali — atlanmamalı.** [[rerank-ab-onveri]] dersinde
   synthesis'te ölçülen %3 recall'ün kökü retriever değil ölçü aracıydı.
   Bu sette iki parçalı soru bilerek yazılmadı (§4), ama iddia
   ölçülmeden doğru sayılamaz.

Ayırt edici ölçüm: sıfır alan synthesis kayıtları için top-20'de çıpa
dosyalarının HİÇ görünüp görünmediğine bakmak. Görünüyor ama sıralama düşükse
(2); hiç görünmüyorsa sorgu terimleri chunk metniyle örtüşmüyor demektir.
Bu ölçülmeden synthesis'e müdahale edilmez.

> **Bu bölümde bir cümle YANLIŞTI.** "recall@5 0.4093, tavan 0.946'nın çok
> altında → düşüklüğün kaynağı mükerrer baskı DEĞİL" diye yazmıştım. Tavan
> testi bu soruyu cevaplayamaz; baskılar birbiriyle yarışıyor ve `single_fact`
> tarafında bedeli büyük. Bkz. §5b-DÜZELTME.

---

## 9. TEŞHİS — synthesis çöküşünün sebebi ÖLÇÜLDÜ (2026-08-12)

`scripts/synthesis_teshis_probe.py`, hybrid, derinlik 200, BULUNDU eşiği
rank ≤ 10. Kontrol kolu (`single_fact`) aynı ölçümle koştu.

**Ölçüm zemini önce denetlendi.** Servis `retrieval.max_top_k = 20` ile kırpıyor;
ilk koşum `--derinlik 200` istendiği hâlde 20 döndürmüş, yani teşhis top-20'ye
göreli kalacakmış. Store yolu üretim parametreleriyle sürülerek aşıldı.
Tutarlılık denetimi: 4 kayıtta ilk 20 sıra ayrışıyor, **4'ünün de 4'ü** store
aynı k ile koşulduğunda servisle birebir aynı → sapma yol farkı değil ANN aday
havuzunun k'ya bağlılığı. Beklenen artefakt; sınıflar geçerli.

**AYRI ALINTI başına (mükerrer baskı tekilleştirilmiş):**

| kategori | alıntı | BULUNDU | GEC | YANLIS_CHUNK | DOSYA_YOK |
|---|---|---|---|---|---|
| `single_fact` | 19 | **%78.9** | %21.1 | %0.0 | %0.0 |
| `synthesis` | 20 | **%20.0** | %40.0 | %30.0 | %10.0 |

### Hüküm

**(3) ÖLÇÜT KUSURU / terim uyuşmazlığı — ÇÜRÜDÜ.** `DOSYA_YOK` synthesis'te
20 alıntının 2'si, single_fact'te 19'un 0'ı. Çıpa dosyaları top-200'e giriyor.
Sorular korpusun diliyle örtüşüyor; ölçtüğümüz şey bu kez gerçekten sistem.
[[rerank-ab-onveri]] turunun tuzağına düşülmedi.

**(1) YAPISAL CEZA / sorgu tek dokümana kilitleniyor — ÇÜRÜDÜ.** Kilitlenme
deseni "1 bulundu"nun baskın olmasını gerektirirdi. Ölçülen: 10 synthesis
kaydının **7'si SIFIR** alıntı buluyor, 2'si bir, 1'i iki. Sert payda ikinci
çıpayı cezalandırmadan önce birinci çıpa zaten bulunamıyor.

**(2) SIRALAMA — DOĞRULANDI, tek ayakta kalan açıklama.** synthesis
alıntılarının **%70'i** (GEC %40 + YANLIS_CHUNK %30) top-200 içinde ama
top-10 dışında. Bilgi erişilebilir; yukarı çıkamıyor.

### Türetilen iki ayrı bulgu

- **Bu synthesis'e özgü bir hastalık değil, orada şiddetli.** `single_fact`'te
  de %21.1 GEC var. Ortak kök sıralama.
- **`YANLIS_CHUNK` asimetrisi:** synthesis %30, single_fact **%0**. Dosya
  yüzeye çıkıyor ama alıntının durduğu chunk çıkmıyor. Bu chunk
  granülerliğine/komşuluğa işaret ediyor, kelime dağarcığına değil —
  `lookup_window` ve chunk sınırları ayrı bir inceleme kalemi.

### Ne YAPILMADI ve neden

Rerank'e geçilmedi. Ölçüm "doğru chunk havuzda ama sırası düşük" diyor; bu
cross-encoder'ın tarif edildiği durum, **ama tarif eşleşmesi kanıt değildir**.
Rerank'in bu havuzda sırayı düzeltip düzeltmediği ancak A/B ile bilinir.
Değişen şey şu: [[rerank-ab-onveri]] kalemi "canlı talep ölçülemedi" diye
askıya alınmıştı; artık talep tahmini değil **ölçülmüş bir darboğaz** var.
Kalemi yeniden açma kararı kullanıcıya aittir.

Sıra önerisi: önce mükerrer baskı elemesi (§5b-DÜZELTME — hem ölçümü hem
canlı bağlamı düzeltir, ucuz), sonra re-baseline, sonra rerank A/B.
Baskı yarışı sürerken rerank ölçülürse kazanç kopyalarla karışır.

---

## 10. MÜKERRER BASKI ELEMESİ — küme 1 kararı ÖLÇÜLDÜ (2026-08-13)

§9'un sıra önerisindeki ilk kalem. `scripts/mukerrer_baski_probe.py` +
`scripts/madde_kapsam_probe.py`, ikisi de salt-okuma.

### Tespit yöntemi üç kez değişti — ikisi benim kusurumdu

1. **Hash eşitliği YETMEDİ.** `md5(chunk_text_norm)` tam eşitlik ister; farklı
   PDF'ler farklı chunk sınırı üretir. %60 eşikte tek çift buldu, oysa 5411
   yedi dosyada duruyor. Hash ADAY üretimine indirildi, karar **içerilme**
   testine geçti (küçük dosyadan pencere, büyük dosyada `position()`) — bu,
   golden alıntı eşlemesinin ta kendisi, sınırdan bağımsız.
2. **Tek yönlü içerilme "kapsayan derleme"yi mükerrer sandı.** 58 chunk'lık
   5464 kanunu 399 chunk'lık `263_2.pdf` içinde %60 çıkınca küme oldu; heuristik
   **büyük dosyayı** silmeyi önerdi. Ters yön %8. Çift yönlü ölçüm eklendi.
3. **Golden çözünürlük testim HAM alıntıyı NORM sütununda aradı.** "10 alıntının
   5'i tutulanda çözülmüyor" dedi ve 4'ünün kaynağı tutulacak dosyanın
   kendisiydi — olacak şey değil. `chunk_text_norm` = NFKC+lowercase+ws-collapse;
   `normalize_for_quote`'tan geçirilince çözülmeyen **1**'e indi.

### Küme 1 — 7 dosya, aynı kanun

| dosya | chunk | sayfa | işaret | son yıl | madde | geçici |
|---|---|---|---|---|---|---|
| **5411 sayılı Bankacılık Kanunu.pdf** (TUT) | 269 | 117 | **19** | **2025** | 171 | **35** |
| Bankacilik_Kanunu_2.pdf | 289 | 209 | 0 | 2022 | 171 | 33 |
| BankacilikKanunu_11.baski-web_2.pdf | 291 | 196 | 0 | 2017 | 171 | 31 |
| Bankacilik_Kanunu_%2528Turkce%2529_2.pdf | 309 | 206 | 0 | 2015 | 171 | 31 |
| BankacilikKanunu_8.baski-web_2.pdf | 298 | 203 | 0 | 2014 | 171 | 31 |
| BankacilikKanunu_7.baski_2.pdf | 298 | 204 | 0 | 2013 | 171 | 31 |
| 5411_Guncel_2.pdf | 235 | 92 | 0 | 2010 | **170** | **0** |

**Sayfa farkı silmeye engel değil — ölçüldü.** Tutulacak aday eleyeceği her
dosyadan az sayfalı (117 vs 196-209) ve bu haklı bir itirazdı. İki ölçüm çürüttü:

- **Pencere boyu testi.** 160ch'de ters yön %40, 60ch'de **%72-77** ve simetri
  kuruldu. Uzun pencere tutmuyordu çünkü TBB baskıları madde gövdesine dipnot
  numarası serpiştiriyor (`"...durdurulması, 35 34 7222 sayılı kanun ile
  değiştirilmiştir 63 b)..."`), virgül öncesi boşluk bırakıyor, 2015 baskısında
  font bozulması var (`ඈnoඈsd\%ඈuru...`). Fark **dizgi**, içerik değil.
- **Madde kapsamı.** Kanun metninde bütünlüğün doğal ölçüsü rastgele karakter
  penceresi değil madde numarası kümesidir; dizgi/dipnot/OCR gürültüsü madde
  başlıklarını topluca yok edemez. Yedi dosyanın hepsinde **171 madde**,
  tutulanda **eksik 0**, geçici maddede tutulan **en yüksek** (35).

Yan bulgu: `5411_Guncel_2.pdf` 170 madde ve **0 geçici madde** ile grubun en
eksik dosyası. Adında "Guncel" geçmesine rağmen bayat olduğu üçüncü kez
doğrulandı — [[korpus-degisti-golden-bayat]]'taki ad-deseni dersi.

### Küme 2 — KAPANDI, silinecek dosya yok

`5464 sayılı Banka Kartları...` (58 chunk) `263_2.pdf` (399 chunk) içinde %60,
tersi **%8**. Bu mükerrer baskı değil, derlemenin kanunu içermesi. Tek yönlü
ölçüm burada büyük dosyayı sildirecekti.

### h01'in alıntısı — silmeye engel değil ama AYRI bir kalem

`gs-bddk-h01`'in kanun alıntısı beş eski baskıda çözülüyor, tutulacak dosyada
çözülmüyor. İz sürüldü: ön ek 53 karakter chunk 59'da tutuyor, **chunk sonuna
1082 karakter var** — sınır kusuru değil. Baş ve son tutup ortası tutmuyor.
İki açıklama açık: lafız değişti (m.53 karşılıklar hükmü değiştirildi) ya da
konsolide metin ortaya `(Değişik: …)` şerhi ekledi. **Ayırt edilmeden "hüküm
değişti" denemez**; chunk 59 okunacak. Her iki hâlde h01 dört kanıtla ayakta
kalır, üretici patlamaz.

### Karar ve beklenen sonuç

Altı dosya elenir, konsolide metin tutulur (kullanıcı onayı 2026-08-13).
`dosya_kaldir.py` yalnız `core_files` satırını siler, ham PDF diskte kalır —
karar geri alınabilir.

Golden'da beklenen: e01 −6, e02 −5, e03 −5, e04 −3, e05 −4, e10 −1, m01 −4,
h01 −5, h02 −4, h03 −4 = **−41**, yani **93 → 52 evidence / 31 → 25 dosya**.
Üretici başka bir sayı verirse durulur; tahmin ölçümün denetimidir.

`load_golden_set` evidence'ı yükleme anında korpusta arar
(`EvidenceValidationError`): dosyalar silindikten sonra ESKİ `v1.jsonl` ile
`eval load` **patlar**. Sıra zorunludur: sil → üret → kuru koşum → yükle.
Hash değiştiği için `replace_set` devreye girer, sürüm adı `v1-bddk` kalır.

Baseline (§8) bu elemeden SONRA yeniden ölçülür. §8 sayıları kopya yarışı
içeren korpusa aittir; yeni sayılarla aynı tabloda karşılaştırılamaz.
