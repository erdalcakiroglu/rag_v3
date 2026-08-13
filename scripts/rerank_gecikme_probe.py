#!/usr/bin/env python
"""Rerank GECİKME probu — havuz derinliğinin sorgu başına maliyetini ölçer.

NEDEN
    A/B probu kaliteyi ölçtü ve pool 100'de GENEL recall@10 0.486→0.598 çıktı;
    eğri 100'de hâlâ yükseliyordu. Ama kalite tek başına sevk kararı vermez:
    over-fetch canlıda HER sorguya cross-encoder maliyeti bindirir. Elimizde o
    maliyetin rakamı YOK — A/B probu süre tutmuyor, p100 koşumu "yavaş geldi"
    diye elle kesildi. "Yavaş geldi" bir ölçüm değildir.

    Bu prob kaliteyi DEĞİL yalnız gecikmeyi ölçer ve retrieval'ı denklemden
    çıkarır: TEI'ye N gerçek chunk metni verilip tam rerank turunun duvar-saati
    süresi alınır. Ölçülen şey, üretimde havuzu N'e çıkarmanın sorgu başına
    ekleyeceği süredir.

ÖLÇÜM ZEMİNİ
    • DB'ye yalnız SELECT — hiçbir yazma yok.
    • Metinler GERÇEK korpustan; sentetik kısa metinle ölçmek maliyeti olduğundan
      küçük gösterirdi (cross-encoder maliyeti token sayısıyla büyür). Örneklemin
      karakter dağılımı da basılır ki sayının hangi metin boyuna ait olduğu belli
      olsun.
    • Isınma turu sayılmaz (ilk istek model/kernel ısınmasını taşır).
    • Her havuz için `--tekrar` kez ölçülüp MEDYAN raporlanır; tek koşum
      gürültüsüne karne mührü basmak bu projede daha önce yanlış sonuca götürdü.
    • İstemci partilemesi A/B probuyla AYNI (`--tei-batch`) — yoksa ölçülen
      gecikme oradaki koşumun gecikmesi olmaz.

KULLANIM
    python scripts/rerank_gecikme_probe.py                       # 10,20,50,100,200
    python scripts/rerank_gecikme_probe.py --havuzlar 20,100 --tekrar 5
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _ornek_metinler(db, adet: int) -> list[str]:
    """Korpustan deterministik dağılmış `adet` chunk metni (SALT OKUMA).

    `md5(chunk_id)` sıralaması hem rastgele dağılır hem tekrarlanabilir —
    `ORDER BY random()` koşumlar arası farklı örneklem verir ve gecikme
    karşılaştırmasını kirletirdi.
    """
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT chunk_text
            FROM core_chunks
            WHERE chunk_text IS NOT NULL AND length(chunk_text) > 0
            ORDER BY md5(chunk_id::text)
            LIMIT %s;
            """,
            (adet,),
        ).fetchall()
    return [str(r[0]) for r in rows]


def _tur(client, url: str, soru: str, metinler: list[str], parti: int) -> tuple[float, int]:
    """Tam rerank turu: süre (sn) ve atılan HTTP isteği sayısı."""
    t0 = time.perf_counter()
    istek = 0
    for bas in range(0, len(metinler), parti):
        resp = client.post(url, json={"query": soru,
                                      "texts": metinler[bas:bas + parti]})
        resp.raise_for_status()  # hata → ÇÖK; yarım ölçüm rapor edilmez
        istek += 1
    return time.perf_counter() - t0, istek


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(prog="rerank_gecikme_probe")
    ap.add_argument("--tei-url", default="http://localhost:8085")
    ap.add_argument("--havuzlar", default="10,20,50,100,200",
                    help="ölçülecek havuz derinlikleri (virgülle)")
    ap.add_argument("--tekrar", type=int, default=3, help="havuz başına ölçüm (medyan alınır)")
    ap.add_argument("--tei-batch", type=int, default=32, help="A/B probuyla AYNI olmalı")
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--soru", default="Bankaların kaldıraç oranı nasıl hesaplanır?",
                    help="gerçekçi bir sorgu (gecikme sorgu uzunluğuna zayıf bağlı)")
    args = ap.parse_args(argv)

    havuzlar = sorted({int(x) for x in args.havuzlar.split(",") if x.strip()})
    if not havuzlar:
        print("HATA: --havuzlar boş.", file=sys.stderr)
        return 2

    import httpx

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database

    db = Database(DbSettings()).open()
    try:
        metinler = _ornek_metinler(db, max(havuzlar))
    finally:
        db.close()
    if len(metinler) < max(havuzlar):
        print(f"HATA: korpustan {max(havuzlar)} chunk gelmedi ({len(metinler)}).",
              file=sys.stderr)
        return 2

    uz = sorted(len(t) for t in metinler)
    print("=" * 78)
    print(f"ORNEKLEM: {len(metinler)} chunk  karakter  "
          f"medyan {uz[len(uz)//2]}  p95 {uz[int(len(uz)*0.95)]}  max {uz[-1]}")
    print(f"TEI {args.tei_url}  parti={args.tei_batch}  tekrar={args.tekrar}")
    print("=" * 78)

    url = f"{args.tei_url.rstrip('/')}/rerank"
    client = httpx.Client(timeout=args.timeout)
    try:
        # Isınma — SAYILMAZ. İlk istek model yüklemesi/kernel ısınması taşır.
        _tur(client, url, args.soru, metinler[:4], args.tei_batch)

        print(f"\n{'havuz':>6}{'istek':>7}{'medyan s':>11}{'min s':>9}{'max s':>9}"
              f"{'chunk/s':>10}{'20ye gore':>11}")
        taban = None
        for n in havuzlar:
            olcumler = []
            istek = 0
            for _ in range(max(1, args.tekrar)):
                sure, istek = _tur(client, url, args.soru, metinler[:n], args.tei_batch)
                olcumler.append(sure)
            med = statistics.median(olcumler)
            if n == 20:
                taban = med
            kat = f"{med / taban:.1f}x" if taban else "-"
            print(f"{n:>6}{istek:>7}{med:>11.2f}{min(olcumler):>9.2f}"
                  f"{max(olcumler):>9.2f}{n / med:>10.1f}{kat:>11}")
    finally:
        client.close()

    print("\nOKUMA: 'medyan s' = uretimde havuzu o derinlige cikarmanin SORGU BASINA")
    print("ekleyecegi sure. Kalite kazanci (A/B probu) bu surenin karsiliginda alinir.")
    print("chunk/s duserse TEI doygunlukta demektir; sabitse maliyet dogrusal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
