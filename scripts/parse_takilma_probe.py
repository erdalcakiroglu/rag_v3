#!/usr/bin/env python
"""FAILED parse teşhisi: 'takıldı' mı 'yavaş' mı? — SALT-OKUMA.

NEDEN: iki dosya (Bankalarımız 2025, Bankacilik_Terminolojisi_2) parse
timeout'uyla FAILED. `parse_timeout_sec` yükseltilip retry koşuldu, YİNE düştü.
Bu noktada iki senaryo tamamen farklı yollara çıkar:
  • YAVAŞ  → süre lineer, sayfa başına maliyet yüksek → eşiği yükselt / mod değiştir
  • TAKILI → belirli bir sayfada süre patlıyor / hiç ilerlemiyor → bozuk PDF,
             sayfa atlama ya da farklı alt-parser gerekir
Eşik yükseltmek ikincisini ÇÖZMEZ, yalnız hatayı geciktirir. Ayrım ölçülmeden
config değiştirmek [[on-veri-kontrolu-kural]] ihlalidir.

NE YAPAR:
  §1 Efektif config (parse_timeout_sec/tableformer_mode/pdf_backend/thread) —
     retry'ın gerçekten yeni eşikle koşup koşmadığı buradan görülür.
  §2 FAILED dosyaların fail_reason/retry_count/boyut/updated_at'ı + kaydedilmiş
     parse metrikleri (duration_ms = pipeline'ın GERÇEKTE ne kadar koştuğu).
  §3 PDF anatomisi — docling'e HİÇ girmeden, pypdfium2 ile: sayfa sayısı, ham
     metin uzunluğu, metinsiz sayfa sayısı. Docling saniyeler içinde bunu
     okuyamıyorsa sorun dosyanın kendisindedir.
  §4 (--parse) docling'i ÖNPLANDA, zaman aşımı OLMADAN koşar ve süreyi ölçer.
     --page-range ile dilim dilim koşulursa süre-sayfa eğrisi çıkar: lineerse
     yavaş, bir dilimde patlıyorsa takılı.

SALT-OKUMA: DB'ye yazmaz, dosya yazmaz, config değiştirmez.

KOŞUM (H200, venv + .env.h200 yüklü):
    python scripts/parse_takilma_probe.py                      # hızlı: DB + PDF anatomisi
    python scripts/parse_takilma_probe.py --parse              # + tam parse (UZUN)
    python scripts/parse_takilma_probe.py --parse --page-range 1-20
    python scripts/parse_takilma_probe.py --parse --mode fast  # accurate ile karşılaştır

MALİYETİ KİM ÜRETİYOR (aynı dilimde koşup süreleri karşılaştır — üç aday):
    --mode fast     TableFormer accurate→fast
    --no-tables     tablo yapı-tanıma tamamen kapalı (TableFormer'ın ÜST sınırı)
    --no-figures    2x görsel render kapalı
Bunlar YALNIZ bu koşumu etkiler; config'e ve korpusa dokunmaz.
"""

from __future__ import annotations

import argparse
import sys
import time

_SQL_FAILED = """
SELECT f.file_id, f.file_name, f.status, f.retry_count, f.file_size,
       f.source_path, f.updated_at, f.fail_reason
FROM core_files f
WHERE f.status <> 'COMPLETED'
ORDER BY f.file_id;
"""

_SQL_METRICS = """
SELECT m.file_id, m.step, m.duration_ms, m.ok, left(m.detail::text, 240)
FROM metrics_ingestion m
JOIN core_files f ON f.file_id = m.file_id AND f.status <> 'COMPLETED'
ORDER BY m.file_id, m.metric_id;
"""


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _pdf_anatomy(path: str) -> dict:
    """Docling'e girmeden ham PDF anatomisi (pypdfium2 — saniyeler sürer)."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return {"hata": "pypdfium2 yok"}
    t0 = time.monotonic()
    try:
        doc = pdfium.PdfDocument(path)
    except Exception as exc:                       # bozuk/şifreli PDF
        return {"hata": f"{type(exc).__name__}: {exc}"}
    try:
        n = len(doc)
        chars = 0
        bos_sayfa = 0
        for i in range(n):
            page = doc[i]
            tp = None
            try:
                tp = page.get_textpage()
                txt = tp.get_text_bounded() or ""
            except Exception:
                txt = ""
            finally:
                # 400+ sayfalık dosyada textpage sızdırmak bellek şişirir.
                if tp is not None:
                    tp.close()
                page.close()
            chars += len(txt)
            if not txt.strip():
                bos_sayfa += 1
        return {"sayfa": n, "ham_karakter": chars, "metinsiz_sayfa": bos_sayfa,
                "okuma_sn": round(time.monotonic() - t0, 2)}
    finally:
        doc.close()


def _parse_timed(path: str, cfg_ing, mode: str | None, page_range,
                 *, figures: bool | None = None, tables: bool = True) -> dict:
    """Docling'i önplanda, zaman aşımı OLMADAN koşar ve süreyi ölçer."""
    from ragintel.ingestion.parsing.docling_backend import DoclingBackend
    from ragintel.config.settings import ParsingSettings

    backend = DoclingBackend(
        figure_images=bool(cfg_ing.figure_images) if figures is None else figures,
        figure_image_scale=float(cfg_ing.figure_image_scale),
        pdf_backend=ParsingSettings().pdf_backend,
        tableformer_mode=mode or str(cfg_ing.tableformer_mode),
        parse_num_threads=int(cfg_ing.parse_num_threads),
    )
    if not tables:
        # Tablo yapı-tanımayı tamamen kapat: TableFormer'ın payını ölçmenin
        # en keskin yolu (üretimde ASLA böyle koşulmaz — yalnız teşhis).
        # Converter cache'li (_converters[ocr]) → burada yapılan değişiklik
        # aşağıdaki parse/convert çağrısına taşınır.
        conv = backend._converter(False)            # noqa: SLF001
        kapatildi = False
        for fo in getattr(conv, "format_to_options", {}).values():
            po = getattr(fo, "pipeline_options", None)
            if po is not None and hasattr(po, "do_table_structure"):
                po.do_table_structure = False
                kapatildi = True
        if not kapatildi:                            # sessizce "kapattım" deme
            return {"hata": "do_table_structure bulunamadı → --no-tables uygulanamadı"}
    t0 = time.monotonic()
    if page_range is None:
        parsed = backend.parse(path, "pdf")
        sure = time.monotonic() - t0
        return {"sure_sn": round(sure, 1), "sayfa": len(parsed.pages),
                "tablo": len(parsed.tables), "sekil": len(parsed.figures),
                "karakter": len(parsed.body_text)}

    # Dilim koşumu: DoclingBackend page_range'i dışa açmıyor; teşhis için
    # converter'a doğrudan iniyoruz (salt-okuma, üretim yolu değişmez).
    import inspect
    conv = backend._converter(False)               # noqa: SLF001 — teşhis probu
    if "page_range" not in inspect.signature(conv.convert).parameters:
        return {"hata": "bu docling sürümü page_range desteklemiyor → --page-range'i kaldır"}
    result = conv.convert(path, page_range=page_range)
    sure = time.monotonic() - t0
    doc = result.document
    return {"sure_sn": round(sure, 1), "dilim": f"{page_range[0]}-{page_range[1]}",
            "tablo": len(getattr(doc, "tables", []) or []),
            "sayfa_basi_sn": round(sure / max(1, page_range[1] - page_range[0] + 1), 2)}


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description="FAILED parse teşhisi (salt-okuma)")
    ap.add_argument("--parse", action="store_true",
                    help="docling'i önplanda koştur (zaman aşımı YOK — uzun sürebilir)")
    ap.add_argument("--mode", choices=["accurate", "fast"],
                    help="TableFormer modunu geçici olarak ez (config DEĞİŞMEZ)")
    ap.add_argument("--page-range", help="ör. 1-20 — süre/sayfa eğrisi için dilim")
    ap.add_argument("--no-figures", action="store_true",
                    help="görsel çıkarmayı kapat (2x render payını ölçmek için)")
    ap.add_argument("--no-tables", action="store_true",
                    help="tablo yapı-tanımayı kapat (TableFormer payını ölçmek için)")
    ap.add_argument("--only", help="yalnız adında bu geçen dosya(lar)")
    args = ap.parse_args(argv)

    page_range = None
    if args.page_range:
        a, _, b = args.page_range.partition("-")
        page_range = (int(a), int(b or a))

    from ragintel.config.loader import load_config
    from ragintel.config.settings import DbSettings, ParsingSettings
    from ragintel.database import Database
    from ragintel.database.config_store import make_db_reader

    db = Database(DbSettings()).open()
    try:
        cfg = load_config(db_reader=make_db_reader(db))
        ing = cfg.group("ingestion")
        print("=" * 78)
        print("§1 EFEKTİF CONFIG (retry bu değerlerle koştu mu?)")
        print("=" * 78)
        print(f"  parse_timeout_sec  = {ing.parse_timeout_sec}")
        print(f"  tableformer_mode   = {ing.tableformer_mode}")
        print(f"  parse_num_threads  = {ing.parse_num_threads}")
        print(f"  pdf_backend        = {ParsingSettings().pdf_backend}  (ENV/kod — DB'de değil)")
        print(f"  figure_images      = {ing.figure_images} (scale {ing.figure_image_scale})")
        print(f"  max_retry          = {ing.max_retry}")

        with db.connection() as conn:
            cur = conn.cursor()
            rows = cur.execute(_SQL_FAILED).fetchall()
            metrics = cur.execute(_SQL_METRICS).fetchall()

        print("\n" + "=" * 78)
        print("§2 COMPLETED OLMAYAN DOSYALAR")
        print("=" * 78)
        hedefler: list[tuple[int, str, str]] = []
        for fid, name, status, retry, size, spath, upd, reason in rows:
            if args.only and args.only.lower() not in (name or "").lower():
                continue
            print(f"\n  [{fid}] {name}")
            print(f"        durum={status} retry={retry} boyut={size/1048576:.1f} MB "
                  f"güncelleme={upd}")
            print(f"        yol={spath}")
            print(f"        SEBEP: {reason}")
            for m in metrics:
                if m[0] == fid:
                    print(f"        metrik: {m[1]:<8} {m[2]/1000:>8.1f}s ok={m[3]} {m[4]}")
            if spath:
                hedefler.append((fid, name, spath))

        print("\n" + "=" * 78)
        print("§3 PDF ANATOMİSİ (docling YOK — ham pypdfium2)")
        print("=" * 78)
        for fid, name, spath in hedefler:
            print(f"  [{fid}] {name}: {_pdf_anatomy(spath)}")

        if args.parse:
            print("\n" + "=" * 78)
            print(f"§4 ÖNPLANDA PARSE — zaman aşımı YOK"
                  f"{f' · mod={args.mode}' if args.mode else ''}"
                  f"{f' · dilim={args.page_range}' if args.page_range else ''}"
                  f"{' · görsel KAPALI' if args.no_figures else ''}"
                  f"{' · tablo KAPALI' if args.no_tables else ''}")
            print("=" * 78)
            for fid, name, spath in hedefler:
                print(f"  [{fid}] {name} … koşuyor", flush=True)
                try:
                    print(f"        {_parse_timed(spath, ing, args.mode, page_range, figures=False if args.no_figures else None, tables=not args.no_tables)}")
                except Exception as exc:
                    print(f"        ÇÖKTÜ: {type(exc).__name__}: {exc}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
