# M-15 kol-1 (prompt v4 — atılan serbest metni kes): **RED KAYDI**

**Tarih:** 2026-07-24 · **Ortam:** H200 (GGB-AIApp01), üretim DB `10.50.130.55/ragintel`
**Ölçüm:** `ragintel.eval run --golden v0.1 --runs 3 --agent-runs 3` (agent `qwen3.5:35b`, judge `llama3.3:latest`), süre 7394 s
**Karar:** **REDDEDİLDİ → `prompts.agent_system_active` `v2`'ye geri alındı.** Latency kazandı, fallback çöktü.

---

## 1. Fix neydi

ADIM 1 anatomisi ölçtü: üretilen karakterin %34.3'ü serbest metin, bunun **%24.9'u atılıyor** —
model tool çağırırken bir yandan cevabın prose'unu yazıyor, `agent_node` `tool_calls` dalına
girdiği için o metin hiç okunmuyor (~1.7 s/soru). Prompt v4, v2'ye **tam bir satır** ekledi:

> `- Tool çağıracaksan YANINDA düz metin YAZMA: o turda yalnızca tool çağrısını üret; açıklama, özet ya da taslak cevap yazma (bu metin okunmaz, yalnızca gecikme ekler).`

Tek değişken garantisi kod ve testle kilitlendi (`tests/test_faz_m15_prompt_v4.py`, 5 test).

---

## 2. Ölçüm — mekanizma tuttu, ürün bozuldu

**Mekanik kontrol (anatomi, n=15):** kural işe yaradı.

| | zemin (4 koşum) | v4 |
|---|---|---|
| ATILAN serbest metin | 8008-8039 krk (%24.9-25.5) | **4354 krk (%17.4)** |
| toplam üretim | 31.567 krk | 24.968 krk |
| p95 | 21.1 / 21.2 / 21.1 / 21.3 s | **17.2 s** |

Kazanç tahminin (1.7 s) iki katından fazla çıktı. Muhtemel sebep (ölçülmedi, hipotez):
atılan prose **mesaj geçmişine giriyor** ve sonraki her turda prompt-eval olarak yeniden
ödeniyor — yani kesmek hem üretimden hem prompt-eval'den kazandırıyor.

**Kalite karnesi (k=3):** kabul şartı çiğnendi.

| eksen | M-16 mührü (v2) | **v4** | şart |
|---|---|---|---|
| answerable fallback | 9/93 = **%9.68** | **19/93 = %20.4** | **✗ 2.1× gerileme** |
| unanswerable honesty | 9/15 | 14/15 | ✓ (aşağıya bak) |
| faithfulness (≥0.85) | 0.9273 | 0.934 | ✓ |
| context_precision (≥0.80) | 0.8774 | 0.881 | ✓ |

Fallback yalnızca gerilemedi: **M-16 FIX-1'in tüm kazanımını sildi ve M-9.1 zemininin
(%19.35) de altına düştü.** Gürültü değil — üç tekrarda tutarlı (6/31, 8/31, 5/31) ve
19 vakanın 15'i 3/3 deterministik: gs-v0-002, 005, 011, 022, 023.

---

## 3. Neden — risk önceden yazılmıştı, veri doğruladı

Prompt yazılmadan önce not edilen risk: **`reasoning_effort='none'` açıkken atılan prose
modelin fiilî not defteridir.** Kesilince model daha zayıf akıl yürütüyor, cevabı kuramıyor,
coverage eşiğini geçemiyor, fallback'e düşüyor.

Doğrudan kanıt: **gs-v0-002 ve gs-v0-023**, M-16'da *cevaplanan* "negatif-olgu" vakalarıydı
(FIX-2 tartışmasının konusuydular — "… bulunmamaktadır" = dünya hakkında doğru olgu).
v4'te ikisi de 3/3 fallback'e döndü. Yani v4 doğru cevapları reddetmeye çevirdi.

**Honesty'deki 9/15 → 14/15 kazanç DEĞİLDİR.** Aynı sebebin öteki yüzü: model daha çok
reddediyor, `_honesty()` tanımı da (`fabricated = len(sources) > 0`) reddetmeyi ödüllendiriyor.
Bu, M-16'nın "honesty ekseninde kırık olan davranış değil ölçüt" teşhisinin ve M-9'un
"sapkın gate teşviki" deseninin bire bir tekrarı. **Sistem daha dürüst olmadı, daha çok sustu.**
Bu sayı M-17'nin (honesty tanımı) lehine ek kanıttır, kol-1'in lehine değil.

---

## 4. Ne öğrendik (M-15'in geri kalanını yeniden çerçeveliyor)

1. **"Ürün çıktısına girmeyen token bedavadır" YANLIŞ.** Okunmayan metin, üretildiği anda
   modelin durumunu kuruyor. Latency kolları artık iki sınıfa ayrılıyor:
   - **davranış-nötr**: üretilen token bit bit aynı kalır (ön-ek/KV disiplini, tool şeması,
     taşıma katmanı) → karne riski ~yok.
   - **davranış-değiştiren**: modelin ne ürettiğine dokunur (prompt kuralları, `quote`
     kısaltma, tur azaltma) → her biri k=3 karne ister ve bu vaka gösterdi ki
     **maliyeti latency kazancından büyük olabilir**.
2. **Kol-1 ölü.** Aynı fikrin daha yumuşak biçimleri (örn. "kısa yaz" yerine "en fazla bir
   cümle") aynı riski taşır: not defterini daraltmak akıl yürütmeyi daraltır.
3. **Sıradaki kol, kol-2 (ön-ek disiplini) — ve artık tek sağlam aday o.** Davranış-nötr
   sınıfında: sayacı sistem mesajından çıkarmak + bağlamı append-only kurmak, üretilen
   token'ı hiç değiştirmez (bkz. ADIM 1 §4: cache VAR, sayaç öldürüyor, append-only %48).
   Tek kalite riski `_apply_budget`'ın tahliye politikası değişikliğidir — ayrı karar.
4. **Hedef aritmetiği kötüleşti.** Kol-1 gitti; elde kol-2'nin ~2.3 s'i kalıyor →
   p95 21.2 → **~19 s**. **P95 < 15 s, kalite korunarak, bu model/donanımda ulaşılamıyor.**
   Dürüst seçenekler: hedefi p50'ye çevirmek (13.3 s, zaten altında), streaming ile algılanan
   gecikmeyi düşürmek, daha küçük/hızlı model (kendi karnesiyle), ya da tur sayısına
   dokunmak — sonuncusu M-16 kazanımına en yakın tehdit ve bu koşum onun ne kadar kırılgan
   olduğunu gösterdi.

---

## 5. Artefaktlar

- Kod (git'te kalır, DB'de v4 gövdesi silinmez — sürüm kaydı): `ragintel/agents/prompts.py`
  (`SYSTEM_PROMPT_V4`, `V4_BASE`), `tests/test_faz_m15_prompt_v4.py`.
- Anahtar: `scripts/m15_prompt_v4_switch.py` — geri alma `--target v2 --apply`.
- Log: `/tmp/m15_v4_karne.log` (k=3 karne), `/tmp/m15_v4.log` (anatomi).
- Yan bulgu (ayrı kalem): gs-v0-029 tek koşumda 24.1 s'nin **16.8 s'si LLM DIŞI**
  (retrieval+embed+DB); zeminde LLM payı %89 iken bu soruda %30. İncelenmedi.
- Ölçüm zemini notu: bu oturumda üretim config'i bir süre **yanlış veritabanından**
  (dev makinesindeki yerel Postgres, `192.168.36.15`) doğrulanmaya çalışıldı. Üretim
  `10.50.130.55`. Her config teyidi bundan böyle konteynerde koşulur.
