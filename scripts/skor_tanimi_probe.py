#!/usr/bin/env python
"""chunk_score tanımı: 'tavana değme' cezası haklı mı? — SALT-OKUMA.

NEDEN: `compute_chunk_metrics` (chunking/quality.py:40) truncated'ı
`token_count >= max_tokens` sayıyor ve bunu skora ÇARPAN olarak sokuyor:

    chunk_score = 100 · (1 − truncated_ratio) · (1 − below_min_ratio)

Ama bu boru hattında tavana değmek içerik KAYBI değil: gövde pencereleri 64
token overlap'li (chunker.py:_window_ranges), tablolar satır-gruplarına
bölünüyor, hiçbir chunk bge-m3'ün 8192 penceresini aşmıyor. Uzun ve iyi
yapılandırılmış bir belgede tavana değmek kaçınılmazdır → skor belge
KALİTESİNİ değil UZUNLUĞUNU cezalandırıyor olabilir.

Gerçek chunking kusuru olan tek grup `token_count > max_tokens`: bölünemeyen
tablo satırları (bütçe tutturulamamış).

NE ÖLÇER (hiçbir şey değiştirmeden, iki tanımı yan yana):
  §1 ÖLÇÜM-ARACI DOĞRULAMASI — core_chunks'tan yeniden hesaplanan ESKİ skor,
     core_files.quality_score ile tutuyor mu? Tutmuyorsa modelim yanlıştır ve
     §3-§5 okunmaz. [[olcum-zemini-dersleri]]
  §2 Korpus token kovaları (min-altı / orta / tam-max / max-üstü)
  §3 Eski vs yeni dağılım (chunk alt skoru + bileşik quality_score)
  §4 En düşük 10 — iki sıralama yan yana: yeni sıralama GERÇEKTEN bozuk
     dosyaları mı öne alıyor, yoksa yalnız sayılar mı kayıyor?
  §5 chunk_truncation_high bulgusu iki tanımda kaç dosyada açılırdı
  §6 section_alignment_ratio dağılımı — skora GİRMİYOR; hasat edilmeye
     değer bir ayırt-etme gücü var mı? (ayrı karar için ön-veri)

Üretim kodunu YENİDEN YAZMAZ: eski skoru üretmek için `compute_chunk_metrics`,
alt skorları/ağırlıkları çıkarmak için `QCConsolidator` doğrudan çağrılır.

SALT-OKUMA: DB'ye yazmaz, dosya yazmaz, config değiştirmez.

KOŞUM (H200, venv + .env.h200 yüklü):
    python scripts/skor_tanimi_probe.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

_SQL_KOVA = """
SELECT c.file_id,
       count(*)                                                        AS n,
       count(*) FILTER (WHERE c.token_count <  %(min_t)s)              AS min_alti,
       count(*) FILTER (WHERE c.token_count =  %(max_t)s)              AS tam_max,
       count(*) FILTER (WHERE c.token_count >  %(max_t)s)              AS max_ustu,
       count(*) FILTER (WHERE c.table_id IS NOT NULL)                  AS tablo
FROM core_chunks c
GROUP BY c.file_id;
"""

# section_alignment: gövde (tablo olmayan) chunk'larda section_title doluluğu.
_SQL_HIZALAMA = """
SELECT c.file_id,
       count(*) FILTER (WHERE c.table_id IS NULL)                            AS govde,
       count(*) FILTER (WHERE c.table_id IS NULL AND c.section_title <> '')  AS hizali
FROM core_chunks c
GROUP BY c.file_id;
"""

_SQL_DOSYA = """
SELECT f.file_id, f.file_name, f.quality_score
FROM core_files f
WHERE f.status = 'COMPLETED'
ORDER BY f.file_id;
"""

_SQL_METRIK = """
SELECT m.file_id, m.step, m.detail
FROM metrics_ingestion m
JOIN core_files f ON f.file_id = m.file_id AND f.status = 'COMPLETED'
ORDER BY m.file_id, m.metric_id;
"""


@dataclass
class _Sahte:
    """compute_chunk_metrics'in dokunduğu üç alan — başka alanı okumuyor."""
    token_count: int
    is_table: bool
    section_title: str | None


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _dagilim(vals: list[float]) -> str:
    if not vals:
        return "(boş)"
    s = sorted(vals)
    p = lambda q: s[min(len(s) - 1, int(len(s) * q))]          # noqa: E731
    return (f"min {s[0]:6.2f} | p05 {p(0.05):6.2f} | ort {sum(s)/len(s):6.2f} "
            f"| p50 {p(0.50):6.2f} | p95 {p(0.95):6.2f} | max {s[-1]:6.2f}")


def main() -> int:
    _force_utf8()

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    from ragintel.ingestion.chunking.quality import compute_chunk_metrics
    from ragintel.ingestion.qc import QCConsolidator

    db = Database(DbSettings()).open()
    try:
        qc = QCConsolidator(db)                 # config + ağırlıklar üretimdeki gibi
        cfg_ch = qc.cfg.group("chunking")
        max_t, min_t = int(cfg_ch.max_tokens), int(cfg_ch.min_tokens)
        esik = float(qc.cfg.group("quality").chunk.soft_flag_truncated_ratio)

        print("=" * 78)
        print("§0 EFEKTİF CONFIG")
        print("=" * 78)
        print(f"  chunking.max_tokens = {max_t}   min_tokens = {min_t}")
        print(f"  ağırlıklar          = {qc.weights}")
        print(f"  soft_flag_truncated_ratio = {esik}")

        with db.connection() as conn:
            cur = conn.cursor()
            kovalar = {r[0]: r[1:] for r in cur.execute(
                _SQL_KOVA, {"min_t": min_t, "max_t": max_t}).fetchall()}
            hizalama = {r[0]: r[1:] for r in cur.execute(_SQL_HIZALAMA).fetchall()}
            dosyalar = cur.execute(_SQL_DOSYA).fetchall()
            metrikler = cur.execute(_SQL_METRIK).fetchall()

        per_file_metrics: dict[int, list[tuple[str, dict]]] = {}
        for fid, step, detail in metrikler:
            per_file_metrics.setdefault(fid, []).append((step, detail or {}))

        satirlar = []          # (fid, ad, saklanan, eski, yeni, eski_ch, yeni_ch, kova)
        for fid, ad, saklanan in dosyalar:
            kova = kovalar.get(fid)
            if not kova:
                continue                                  # chunk'sız dosya — atla
            n, min_alti, tam_max, max_ustu, _tablo = kova

            # ESKİ chunk_score: üretim fonksiyonunun KENDİSİ ile üret (yeniden
            # yazmak, doğrulamak istediğim şeyi varsaymak olurdu).
            sahte = ([_Sahte(min_t - 1, False, None)] * min_alti
                     + [_Sahte(max_t, False, None)] * tam_max
                     + [_Sahte(max_t + 1, False, None)] * max_ustu
                     + [_Sahte(min_t, False, None)] * (n - min_alti - tam_max - max_ustu))
            eski_ch = compute_chunk_metrics(sahte, max_tokens=max_t,
                                            min_tokens=min_t)["chunk_score"]
            # YENİ: yalnız bütçe AŞIMI ceza; tavana değmek normal pencereleme.
            yeni_ch = round(100.0 * (1 - max_ustu / n) * (1 - min_alti / n), 2)

            subs = qc._extract_sub_scores(per_file_metrics.get(fid, []))  # noqa: SLF001

            def bilesik(chunk_val: float) -> float | None:
                s = {**subs, "chunk": chunk_val}
                num = sum(qc.weights[k] * v for k, v in s.items() if v is not None)
                den = sum(qc.weights[k] for k, v in s.items() if v is not None)
                return round(num / den, 2) if den else None

            satirlar.append((fid, ad, saklanan, bilesik(eski_ch), bilesik(yeni_ch),
                             eski_ch, yeni_ch, (n, min_alti, tam_max, max_ustu)))

        # ---------------------------------------------------------------- §1
        print("\n" + "=" * 78)
        print("§1 ÖLÇÜM-ARACI DOĞRULAMASI (eski formül yeniden üretilebiliyor mu?)")
        print("=" * 78)
        kiyas = [(f, a, sk, es) for f, a, sk, es, *_ in satirlar if sk is not None]
        tutan = [1 for _f, _a, sk, es in kiyas if es is not None and abs(sk - es) <= 0.05]
        print(f"  karşılaştırılan dosya : {len(kiyas)}")
        print(f"  birebir tutan (±0.05) : {len(tutan)}")
        sapan = sorted(((abs(sk - es), f, a, sk, es)
                        for f, a, sk, es in kiyas if es is not None
                        and abs(sk - es) > 0.05), reverse=True)[:10]
        if sapan:
            print(f"  ⚠ SAPAN {len(kiyas) - len(tutan)} dosya — en büyük 10:")
            for d, f, a, sk, es in sapan:
                print(f"      [{f}] {a[:44]:<44} saklanan={sk:6.2f} yeniden={es:6.2f} Δ={d:.2f}")
            print("  ⚠ Model tutmuyor → §3-§5 OKUNMAZ, önce sapmanın kökü bulunmalı.")
        else:
            print("  ✓ tutuyor → aşağıdaki yeni-tanım sayıları güvenilir.")

        # ---------------------------------------------------------------- §2
        print("\n" + "=" * 78)
        print("§2 KORPUS TOKEN KOVALARI")
        print("=" * 78)
        tn = sum(k[0] for k in kovalar.values())
        tmin = sum(k[1] for k in kovalar.values())
        ttam = sum(k[2] for k in kovalar.values())
        tust = sum(k[3] for k in kovalar.values())
        print(f"  toplam chunk        : {tn}")
        print(f"  min altı (<{min_t})      : {tmin:6d}  %{100*tmin/tn:5.2f}   → GERÇEK kusur")
        print(f"  orta                : {tn-tmin-ttam-tust:6d}  %{100*(tn-tmin-ttam-tust)/tn:5.2f}")
        print(f"  tam max (={max_t})      : {ttam:6d}  %{100*ttam/tn:5.2f}   → normal pencereleme")
        print(f"  max üstü (>{max_t})     : {tust:6d}  %{100*tust/tn:5.2f}   → bölünemeyen tablo satırı")
        print(f"  ESKİ truncated_ratio: %{100*(ttam+tust)/tn:5.2f}  (tam max + üstü)")
        print(f"  YENİ                : %{100*tust/tn:5.2f}  (yalnız üstü)")

        # ---------------------------------------------------------------- §3
        print("\n" + "=" * 78)
        print("§3 DAĞILIM — ESKİ vs YENİ")
        print("=" * 78)
        print(f"  chunk alt skoru ESKİ: {_dagilim([r[5] for r in satirlar])}")
        print(f"  chunk alt skoru YENİ: {_dagilim([r[6] for r in satirlar])}")
        print(f"  bileşik skor    ESKİ: {_dagilim([r[3] for r in satirlar if r[3] is not None])}")
        print(f"  bileşik skor    YENİ: {_dagilim([r[4] for r in satirlar if r[4] is not None])}")

        # ---------------------------------------------------------------- §4
        print("\n" + "=" * 78)
        print("§4 EN DÜŞÜK 10 — sıralama değişiyor mu?")
        print("=" * 78)
        for etiket, idx in (("ESKİ tanım", 3), ("YENİ tanım", 4)):
            print(f"\n  --- {etiket} ---")
            alt = sorted((r for r in satirlar if r[idx] is not None),
                         key=lambda r: r[idx])[:10]
            for fid, ad, _sk, e, y, e_ch, y_ch, (n, mn, tm, us) in alt:
                print(f"    [{fid:>4}] {ad[:40]:<40} eski={e:6.2f} yeni={y:6.2f}  "
                      f"chunk {e_ch:6.2f}→{y_ch:6.2f}  "
                      f"(n={n} min-altı={mn} tam-max={tm} üstü={us})")
        eski_10 = {r[0] for r in sorted((r for r in satirlar if r[3] is not None),
                                        key=lambda r: r[3])[:10]}
        yeni_10 = {r[0] for r in sorted((r for r in satirlar if r[4] is not None),
                                        key=lambda r: r[4])[:10]}
        print(f"\n  iki listede ORTAK: {len(eski_10 & yeni_10)}/10 dosya "
              f"→ {'sıralama büyük ölçüde AYNI' if len(eski_10 & yeni_10) >= 7 else 'sıralama DEĞİŞİYOR'}")

        # ---------------------------------------------------------------- §5
        print("\n" + "=" * 78)
        print(f"§5 chunk_truncation_high (eşik {esik}) — kaç dosyada açılır?")
        print("=" * 78)
        e_flag = sum(1 for r in satirlar if (r[7][2] + r[7][3]) / r[7][0] > esik)
        y_flag = sum(1 for r in satirlar if r[7][3] / r[7][0] > esik)
        print(f"  ESKİ tanımla: {e_flag} dosya")
        print(f"  YENİ tanımla: {y_flag} dosya")

        # ---------------------------------------------------------------- §6
        print("\n" + "=" * 78)
        print("§6 section_alignment_ratio — skora GİRMİYOR; ayırt ediyor mu?")
        print("=" * 78)
        oranlar = [h / g for g, h in hizalama.values() if g]
        print(f"  dosya sayısı (gövde chunk'ı olan): {len(oranlar)}")
        print(f"  {_dagilim([100 * o for o in oranlar])}")
        print(f"  hizalama = 0 olan dosya: {sum(1 for o in oranlar if o == 0)}")
        print(f"  hizalama = 1 olan dosya: {sum(1 for o in oranlar if o >= 0.999)}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
