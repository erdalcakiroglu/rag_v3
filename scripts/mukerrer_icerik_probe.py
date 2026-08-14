"""probe: korpusta İÇERİK olarak mükerrer dosyalar — ada HİÇ bakmadan.

NEDEN VAR
    2026-08-13'te 5411 Bankacılık Kanunu'nun altı baskısı elendi; karar ada değil
    İÇERİLME + ÇİFT YÖN + madde kapsamına dayanıyordu ve doğruydu (silinince
    retrieval r@10 0.618 → 0.682). Ama 08-14'te altısı da geri geldi ve fark
    edilmedi: dosya adları `_2` ekliydi, ad tabanlı hiçbir kontrol yakalayamadı.
    `find_file_by_checksum` de yakalayamaz — o yalnız BİREBİR AYNI BAYT'ı görür,
    farklı baskılar farklı bayttır.

    Yani korumamız iki uçtan da kör: hash çok dar, ad çok kırılgan. Aradaki tek
    sağlam ölçüt İÇERİK. Bu probe onu ölçer.

NE ÖLÇER
    1. Her dosyanın `chunk_text_norm`'undan kayan pencereli kelime shingle'ları
       üretilir (varsayılan 9 kelime, 3 kelime adım).
    2. Dosya başına bottom-k MinHash taslağı tutulur (k=--taslak). Bellek dosya
       başına sabit; korpus tamamı belleğe alınmaz.
    3. Ters indeksle aday çiftler bulunur, Jaccard ve İKİ YÖNLÜ İÇERİLME kestirilir:
           |A∩B| = J·(|A|+|B|)/(1+J)      içerilme_A = |A∩B| / |A|
    4. En yüksek --dogrula çift için TAM shingle kümesi yeniden okunup içerilme
       KESİN hesaplanır (kestirim mühür değildir).

    HÜKÜM DEĞİL, ADAY üretir. `A ⊂ B` demek "A'nın içeriği B'de var" demektir;
    silme kararı ayrıca madde kapsamına ve golden bağımlılığına bakmalı.

KORPUS ŞEKLİ — `--min-shingle` NEDEN DÜŞÜK
    BDDK korpusu aşırı çarpık (2026-08-14 ölçümü): 883 dosya tek sayfalık tebliğ
    (1-2 chunk, 500-2k karakter) ve toplam chunk'ın yalnız %2.3'ünü tutuyor; 99
    büyük dosya %92'sini tutuyor. İlk sürümdeki `--min-shingle 40` varsayılanı
    korpusun %64'ünü sessizce kıyas dışı bıraktı ve probe yine de "4 çift bulundu"
    dedi — bu projede altı kez yakalanan SESSİZ PAYDA ailesinin bir üyesi daha.
    Varsayılan 6'ya indirildi ve dışarıda kalan oran %5'i aşarsa koşum uyarı basar.

    Küçük dosyada doğruluk DÜŞMEZ, ARTAR: |A| ve |B| taslak boyutundan (k=256)
    küçükse taslak dosyanın TÜM shingle kümesidir ve Jaccard kestirim değil kesin
    hesaplanır. Alt sınırın tek sebebi çok kısa metinlerde tesadüfi %100 içerilme.

GOLDEN KORUMASI
    --golden verilirse her aday dosya, golden kanıt dosyalarıyla karşılaştırılır ve
    golden'ın DAYANDIĞI dosyalar açıkça işaretlenir. 08-13'te eleme sonrası eşleme
    52/52 kaldığı için şanslıydık; bu kez şansa bırakmıyoruz.

MALİYET / GÜVENLİK
    SALT OKUR: yalnız SELECT. DB'ye, config'e, golden'a, diske YAZMAZ; hiçbir dosya
    SİLMEZ. Korpusun tüm chunk metnini bir kez okur (43849 chunk ≈ birkaç dakika).

KULLANIM
    python -u scripts/mukerrer_icerik_probe.py --golden v1-bddk
    python -u scripts/mukerrer_icerik_probe.py --esik 0.5 --dogrula 30
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

VARSAYILAN_SHINGLE = 9
VARSAYILAN_ADIM = 3


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mukerrer_icerik_probe")
    ap.add_argument("--golden", default=None,
                    help="Golden set_version — kanıt dosyaları KORUNUYOR diye işaretlenir")
    ap.add_argument("--esik", type=float, default=0.60,
                    help="Aday eşiği: iki yönden biri bu içerilmeyi aşarsa raporlanır")
    ap.add_argument("--taslak", type=int, default=256, help="MinHash bottom-k boyutu")
    ap.add_argument("--shingle", type=int, default=VARSAYILAN_SHINGLE, help="Shingle kelime sayısı")
    ap.add_argument("--adim", type=int, default=VARSAYILAN_ADIM, help="Shingle adımı")
    ap.add_argument("--dogrula", type=int, default=20,
                    help="En yüksek N çift için TAM (kestirimsiz) içerilme hesapla")
    ap.add_argument("--min-shingle", type=int, default=6,
                    help="Bu kadar shingle üretmeyen dosya kıyasa girmez (çok kısa)")
    ap.add_argument("--kalabalik", type=int, default=20,
                    help="Bu kadar çok dosyada geçen shingle aday üretiminde atlanır (kalıp metin)")
    args = ap.parse_args(argv)

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    from ragintel.eval import repository as repo

    db = Database(DbSettings()).open()
    try:
        with db.connection() as conn:
            toplam_chunk = conn.execute("SELECT count(*) FROM core_chunks;").fetchone()[0]
            toplam_dosya = conn.execute(
                "SELECT count(*) FROM core_files WHERE status='COMPLETED';").fetchone()[0]
        print(f"KORPUS: {toplam_dosya} COMPLETED dosya · {toplam_chunk} chunk")
        print(f"ZEMİN: shingle={args.shingle} kelime · adım={args.adim} · taslak={args.taslak} "
              f"· eşik={args.esik} · doğrulanacak çift={args.dogrula}")

        golden_dosyalar: set[str] = set()
        if args.golden:
            with db.connection() as conn:
                for r in repo.list_golden_records(conn, args.golden):
                    for ev in (r.get("gold_evidence") or []):
                        golden_dosyalar.add(str(ev["file_name"]))
            print(f"GOLDEN KORUMASI: {args.golden} → {len(golden_dosyalar)} kanıt dosyası")
        else:
            print("GOLDEN KORUMASI: KAPALI (--golden verilmedi) — silme adayları "
                  "golden'a bağımlı olabilir, kontrol edilmeden silinmemeli")
        print()

        # --- 1) Dosya başına taslak + shingle sayısı -----------------------------
        print("[1/3] shingle taranıyor…")
        taslaklar, boyutlar, adlar = _tara(db, args)
        kucuk = toplam_dosya - len(taslaklar)
        print(f"  {len(taslaklar)} dosya kıyasa girdi · {kucuk} dosya "
              f"<{args.min_shingle} shingle olduğu için DIŞARIDA (çok kısa)")
        if toplam_dosya and kucuk / toplam_dosya > 0.05:
            print(f"  ⚠ PAYDA UYARISI: korpusun %{100*kucuk/toplam_dosya:.0f}'i kıyasa "
                  f"GİRMEDİ. Aşağıdaki\n    sonuç 'korpusta şu kadar mükerrer var' "
                  f"demek DEĞİLDİR — dışarıdaki dosyalarda\n    mükerrer varsa bu koşum "
                  f"onu göremez. --min-shingle düşürün.")

        # --- 2) Aday çiftler ----------------------------------------------------
        print("[2/3] aday çiftler…")
        adaylar = _adaylar(taslaklar, boyutlar, args.taslak, args.esik, args.kalabalik)
        print(f"  eşiği ({args.esik}) aşan {len(adaylar)} çift")

        # --- 3) Tam doğrulama ---------------------------------------------------
        adaylar.sort(key=lambda x: -max(x[2], x[3]))
        dogrulanacak = adaylar[: args.dogrula]
        print(f"[3/3] en yüksek {len(dogrulanacak)} çift TAM hesapla doğrulanıyor…")
        kesin = _dogrula(db, dogrulanacak, adlar, args)
        print()

        # --- rapor ---------------------------------------------------------------
        if not adaylar:
            print("İÇERİK MÜKERRERİ BULUNMADI (bu eşikte). Korpus temiz görünüyor.")
            return 0

        print("--- ADAYLAR (kesin = tam hesap, kest. = MinHash kestirimi) ---")
        korunan = 0
        for fa, fb, ca, cb, kaynak in kesin:
            a_ad, b_ad = adlar[fa], adlar[fb]
            etiket = _etiket(ca, cb)
            print(f"{etiket}  [{kaynak}]")
            isaret_a = " ★GOLDEN" if a_ad in golden_dosyalar else ""
            isaret_b = " ★GOLDEN" if b_ad in golden_dosyalar else ""
            print(f"    A: {a_ad[:66]}{isaret_a}  ({boyutlar[fa]} shingle)")
            print(f"    B: {b_ad[:66]}{isaret_b}  ({boyutlar[fb]} shingle)")
            print(f"    A⊂B %{ca*100:.1f} · B⊂A %{cb*100:.1f}")
            if isaret_a or isaret_b:
                korunan += 1
            print()

        print("--- OKUMA ---")
        print(f"Bu eşikte {len(adaylar)} çift, doğrulanan {len(kesin)}.")
        if korunan:
            print(f"⚠ {korunan} çiftte golden'ın DAYANDIĞI bir dosya var (★GOLDEN). "
                  f"O dosya silinirse\n  quote eşlemesi düşer ve karne bozulur — silme "
                  f"kararı önce diğer nüshaya taşınmalı.")
        print("ÇİFT YÖNLÜ ≈AYNI: iki dosya birbirini kapsıyor → biri gereksiz, "
              "hangisinin kalacağı\n  baskı/tarih tercihi (içerik farkı yok).")
        print("A ⊂ B: A'nın içeriği B'de VAR ama tersi değil → A muhtemelen eski/kısmi "
              "baskı.\n  Yine de madde kapsamı ayrıca bakılmalı; içerilme tek başına "
              "silme gerekçesi DEĞİL.")
        print("KISMİ: ortak mevzuat metni alıntılıyor olabilirler — mükerrer değil, "
              "SİLİNMEMELİ.")
        return 0
    finally:
        db.close()


def _tara(db, args):
    """Dosya başına: bottom-k taslak, tam shingle sayısı, ad.

    Dosya dosya okur — korpusun tüm metnini tek seferde belleğe ALMAZ. psycopg'nin
    istemci imleci `execute` anında tüm sonucu belleğe çeker; tek büyük sorgu bu
    yüzden ~90 MB metin demek olurdu. Dosya başına sorgu round-trip'i pahalı değil.
    """
    taslaklar: dict[int, frozenset[int]] = {}
    boyutlar: dict[int, int] = {}
    adlar: dict[int, str] = {}

    with db.connection() as conn:
        dosyalar = conn.execute(
            "SELECT file_id, file_name FROM core_files WHERE status='COMPLETED' "
            "ORDER BY file_id;").fetchall()
    toplam = len(dosyalar)
    for sira, (fid, fad) in enumerate(dosyalar, 1):
        fid = int(fid)
        adlar[fid] = str(fad)
        with db.connection() as conn:
            metinler = conn.execute(
                "SELECT chunk_text_norm FROM core_chunks WHERE file_id = %s "
                "ORDER BY chunk_index;", (fid,)).fetchall()
        kume: set[int] = set()
        n = args.shingle
        for (metin,) in metinler:
            kelimeler = (metin or "").split()
            for i in range(0, max(0, len(kelimeler) - n + 1), args.adim):
                kume.add(hash(" ".join(kelimeler[i:i + n])))
        if len(kume) >= args.min_shingle:
            boyutlar[fid] = len(kume)
            taslaklar[fid] = frozenset(sorted(kume)[: args.taslak])
        if sira % 100 == 0 or sira == toplam:
            print(f"    {sira}/{toplam} dosya", flush=True)
    return taslaklar, boyutlar, adlar


def _adaylar(taslaklar, boyutlar, k: int, esik: float, kalabalik: int = 20):
    """Ters indeksle aday çift bul, bottom-k ile Jaccard ve iki yönlü içerilme kestir.

    `kalabalik`'ten çok dosyada geçen shingle atlanır: mevzuat korpusunda kalıp metin
    ("MADDE 1 – (1) Bu Yönetmeliğin amacı…") her yerde geçer, ayırt etmez ve
    çift üretimini karesel patlatır. Bu yalnız hangi çiftlerin BAKILDIĞINI daraltır;
    bakılan çiftin kestirimi taslağın tamamı üzerinden yapılır.
    """
    ters: dict[int, list[int]] = defaultdict(list)
    for fid, tas in taslaklar.items():
        for h in tas:
            ters[h].append(fid)
    ortak: dict[tuple[int, int], int] = defaultdict(int)
    for dosyalar in ters.values():
        if len(dosyalar) < 2 or len(dosyalar) > kalabalik:
            continue
        for i, a in enumerate(dosyalar):
            for b in dosyalar[i + 1:]:
                ortak[(a, b) if a < b else (b, a)] += 1
    print(f"  {len(ortak)} çift değerlendirildi", flush=True)

    cikti = []
    for (a, b), _ in ortak.items():
        ta, tb = taslaklar[a], taslaklar[b]
        birlesim = sorted(ta | tb)[:k]
        if not birlesim:
            continue
        kesisim = sum(1 for h in birlesim if h in ta and h in tb)
        j = kesisim / len(birlesim)
        if j <= 0.0:
            continue
        na, nb = boyutlar[a], boyutlar[b]
        ortak_tahmin = j * (na + nb) / (1.0 + j)
        ca, cb = min(1.0, ortak_tahmin / na), min(1.0, ortak_tahmin / nb)
        if max(ca, cb) >= esik:
            cikti.append((a, b, ca, cb))
    return cikti


def _dogrula(db, ciftler, adlar, args):
    """Aday çiftler için TAM shingle kümesiyle kesin içerilme. Kestirim mühür değildir."""
    gerekli = {f for c in ciftler for f in c[:2]}
    kumeler: dict[int, set[int]] = {f: set() for f in gerekli}
    if gerekli:
        with db.connection() as conn:
            cur = conn.execute(
                "SELECT file_id, chunk_text_norm FROM core_chunks "
                "WHERE file_id = ANY(%s) ORDER BY file_id, chunk_index;", (list(gerekli),))
            while True:
                satirlar = cur.fetchmany(2000)
                if not satirlar:
                    break
                for fid, metin in satirlar:
                    kelimeler = (metin or "").split()
                    n = args.shingle
                    hedef = kumeler[int(fid)]
                    for i in range(0, max(0, len(kelimeler) - n + 1), args.adim):
                        hedef.add(hash(" ".join(kelimeler[i:i + n])))
    sonuc = []
    for a, b, ka, kb in ciftler:
        sa, sb = kumeler.get(a), kumeler.get(b)
        if not sa or not sb:
            sonuc.append((a, b, ka, kb, "kest."))
            continue
        ortak = len(sa & sb)
        sonuc.append((a, b, ortak / len(sa), ortak / len(sb), "kesin"))
    sonuc.sort(key=lambda x: -max(x[2], x[3]))
    return sonuc


def _etiket(ca: float, cb: float) -> str:
    if ca >= 0.90 and cb >= 0.90:
        return "ÇİFT YÖNLÜ ≈AYNI"
    if ca >= 0.90:
        return "A ⊂ B (A gereksiz olabilir)"
    if cb >= 0.90:
        return "B ⊂ A (B gereksiz olabilir)"
    return "KISMİ ÖRTÜŞME"


if __name__ == "__main__":
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
