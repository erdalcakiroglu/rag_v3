#!/usr/bin/env python
"""Embed-500 ZEHİR PROBU — hangi chunk, ve o chunk'ın HANGİ PARÇASI 500 veriyor?

NEDEN VAR (ölçüldü 2026-08-10, file_id=24 `Bankacilik_Kanunu_%2528Turkce%2529_2.pdf`):
Glif onarımı (tam-sayfa OCR) dosyayı KURTARDI — 535 chunk → 311, kardeş baskılarla
(302/304/292) aynı hizada, `parse_ok glyph_repair=encoding_repaired`. Ama embed
adımında Ollama `/api/embed` kalıcı 500 verdi; batch 32→16→8→4→2→1 yarılaması
kurtarmadı ve `run` TÜM korpus koşusunu durdurdu (1117 COMPLETED / 1 PENDING).

Batch küçültmenin kurtarmaması BELİRLEYİCİ: bu yük/bellek sorunu değil, tek bir
chunk'ın İÇERİĞİ. Aynı koşuda 22 dosya ve bu dosyanın ilk partileri 200 OK aldı —
backend ayakta. Kodda 2026-08-05'te ölçülen pipe-zehri için son çare fallback var
(`sanitize_for_embed`, '|' → boşluk) ama logda `embed_sanitized_fallback` satırı
YOK; kodda bu ancak sanitize metni DEĞİŞTİRMEDİĞİNDE olur, yani chunk'ta pipe yok.
Demek ki bu BAŞKA bir tetikleyici ve teşhis edilmeden fallback genişletilemez.

NE YAPAR
  1. Boru hattının GERÇEK adımlarını koşar (parse → clean → chunk). Taklit etmez:
     glif onarımı ParseAdapter'ın içinde, elle yeniden kurulursa üretimden sapar
     ve YANLIŞ chunk suçlanır.
  2. Her chunk'ı TEK TEK embed eder, patlayanları toplar.
  3. Patlayan her chunk için metni İKİYE BÖLEREK en küçük patlayan parçayı arar.
     "Bu chunk kötü" düzeltilebilir bir teşhis değildir; "şu parça 500 veriyor"
     düzeltilebilir. Minimal parça repr + kod-noktası dökümüyle basılır.
  4. Aday dönüşümleri (pipe→boşluk, NFKC, kontrol karakteri temizliği, kırpma)
     ayrı ayrı dener: hangisi 500'ü gideriyor? Fallback'in genişletilmesi ancak
     ÖLÇÜLEN dönüşüme dayanabilir.

NE YAZMAZ (korpus tablolarına dokunmaz)
  core_chunks / core_vectors / core_tables / core_figures'a YAZMAZ, dosya
  status'unu DEĞİŞTİRMEZ: `store.write_file` ve `set_status` HİÇ çağrılmaz.
  Parse/clean/chunk adaptörleri kendi telemetrisini yazar (metrics_ingestion,
  qc_findings) ve ParseAdapter `core_files.language` alanını set eder — bunlar
  normal koşunun da yazdığı append-only telemetridir, gizlenmiyor.

  UYARI: dosya PENDING iken koşulmalıdır. Devam eden bir `ingest run` varken
  koşmayın — aynı dosyayı iki süreç parse eder.

KULLANIM
  python scripts/embed_zehir_probe.py --file-id 24
  python scripts/embed_zehir_probe.py --file-id 24 --max-istek 400 --json
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata


def _force_utf8() -> None:
    for akis in (sys.stdout, sys.stderr):
        yeniden = getattr(akis, "reconfigure", None)
        if callable(yeniden):
            yeniden(encoding="utf-8", errors="replace")


def _kirp(metin: str, n: int) -> str:
    return metin if len(metin) <= n else metin[:n] + f"…(+{len(metin) - n})"


# --- zehir adayı dönüşümler ---------------------------------------------------
# Her biri AYRI denenir: amaç "bir şey işe yaradı" değil, HANGİSİNİN işe
# yaradığını bilmek. Fallback ancak ölçülen dönüşümle genişletilebilir.
def _kontrol_temizle(s: str) -> str:
    return "".join(
        " " if unicodedata.category(c) in ("Cc", "Cf", "Co", "Cs") and c not in "\n\t" else c
        for c in s)


def _glif_coz(s: str) -> str:
    """Bozuk alt-küme fontunun kaydırmasını GERİ ALIR (ölçüldü 2026-08-10).

    file_id=24 chunk#297'de her kontrol karakteri, ASCII karşılığının 29 eksiğiydi:
    \\x18\\x17\\x1a\\x15 → "5472", \\x14\\x17\\x11\\x13\\x16\\x11\\x15\\x13\\x13\\x19 → "14.03.2006".
    Çözülen tablo 5411'in değişiklik kayıtlarıyla BİREBİR uyuşuyor (5472/26108,
    5667/26537, 5754/26870, 6111/27857, KHK 662/28103) — yani kaydırma tahmin
    değil, beş bağımsız satırla doğrulanmış bir eşleme. Bu dönüşüm 500'ü
    gideriyorsa çözüm metni SİLMEK değil ONARMAK'tır.
    """
    return "".join(chr(ord(c) + 29) if ord(c) < 0x20 and c not in "\n\t" else c for c in s)


DONUSUMLER: list[tuple[str, object]] = [
    ("pipe→bosluk (mevcut fallback)", lambda s: s.replace("|", " ")),
    ("NFKC", lambda s: unicodedata.normalize("NFKC", s)),
    ("kontrol/PUA karakterleri→bosluk", _kontrol_temizle),
    ("glif kaydirmasi +29 COZ (onarim)", _glif_coz),
    ("bosluk sadelestirme", lambda s: " ".join(s.split())),
    ("ilk 2000 karakter", lambda s: s[:2000]),
    ("ilk 500 karakter", lambda s: s[:500]),
]

# Kanarya: içeriği kesinlikle zararsız, kısa Türkçe metin. Zehirli bir istekten
# SONRA bu da 500 verirse hata artık içerikten değil BACKEND DURUMUNDAN geliyordur
# (model runner düşmüş olabilir) — o hâlde ardışık dönüşüm sonuçları OKUNAMAZ.
# Bu ayrımı yapmadan "dönüşüm işe yaramadı" demek ölçüm hatası olur.
KANARYA = "Bankacılık Kanunu kapsamında bankaların faaliyet izni Kurul kararıyla verilir."


def _karakter_dokumu(metin: str, n: int = 12) -> list[tuple[str, str, int]]:
    """En sık geçen ALIŞILMADIK karakterler (harf/rakam/temel noktalama hariç)."""
    from collections import Counter
    sayac: Counter = Counter()
    for c in metin:
        kat = unicodedata.category(c)
        if kat[0] in ("L", "N") or c in " \n\t.,;:()[]-/%":
            continue
        sayac[c] += 1
    return [(repr(c), unicodedata.category(c), k) for c, k in sayac.most_common(n)]


def _en_uzun_boslukssuz(metin: str) -> tuple[int, str]:
    """En uzun boşluksuz dizi: tokenizer'ı patlatan tipik OCR artığı."""
    en_iyi = ""
    for parca in metin.split():
        if len(parca) > len(en_iyi):
            en_iyi = parca
    return len(en_iyi), en_iyi


class Zehirleyici:
    """Embed ucuna tek metin gönderip 500 verip vermediğini söyler.

    İstek sayısı SINIRLIDIR: teşhis uğruna canlı ucu dövmek yok. Sınır dolarsa
    bölme yarıda kesilir ve bu AÇIKÇA raporlanır — sessizce kırpılmaz.
    """

    def __init__(self, embedder, *, max_istek: int):
        self.embedder = embedder
        self.max_istek = max_istek
        self.istek = 0
        self.tukendi = False

    def patliyor_mu(self, metin: str) -> bool | None:
        """True=500/hata, False=geçti, None=istek bütçesi bitti (bilinmiyor)."""
        if self.istek >= self.max_istek:
            self.tukendi = True
            return None
        self.istek += 1
        try:
            self.embedder.embed_batch([metin])
            return False
        except Exception:
            return True

    def en_kucuk_zehir(self, metin: str, *, taban: int = 40) -> dict:
        """İkiye bölerek en küçük patlayan parçayı arar.

        Zehir bölünmeyle İKİ parçaya dağılmış olabilir (sınırdan geçen bir dizi);
        o hâlde iki yarı da geçer ve arama DURUR — bu bir başarısızlık değil,
        bilgidir ve 'bolunmeyle_kayboldu' olarak raporlanır.
        """
        parca, adim = metin, 0
        while len(parca) > taban:
            orta = len(parca) // 2
            sol, sag = parca[:orta], parca[orta:]
            s = self.patliyor_mu(sol)
            if s is None:
                return {"parca": parca, "adim": adim, "durum": "butce_bitti"}
            if s:
                parca, adim = sol, adim + 1
                continue
            g = self.patliyor_mu(sag)
            if g is None:
                return {"parca": parca, "adim": adim, "durum": "butce_bitti"}
            if g:
                parca, adim = sag, adim + 1
                continue
            return self._uclari_buda(parca, adim, "bolunmeyle_kayboldu")
        return self._uclari_buda(parca, adim, "minimal")

    def _uclari_buda(self, parca: str, adim: int, durum: str) -> dict:
        """Bölme durduktan sonra uçlardan budar (delta-debug daraltması).

        Ortadan bölme, zehir tam sınırdan geçtiğinde durur ve elde gereğinden
        büyük bir parça kalır. Uçları küçülen adımlarla budamak minimal parçaya
        yaklaştırır; zehir kaybolursa budama GERİ ALINIR, yani parça her zaman
        gerçekten patlayan bir metindir.
        """
        adim_boyu = max(len(parca) // 2, 1)
        while adim_boyu >= 1:
            for yeni in (parca[adim_boyu:], parca[:-adim_boyu]):
                if not yeni or yeni == parca:
                    continue
                p = self.patliyor_mu(yeni)
                if p is None:
                    return {"parca": parca, "adim": adim, "durum": "butce_bitti"}
                if p:
                    parca, adim = yeni, adim + 1
                    break
            else:
                adim_boyu //= 2
                continue
        return {"parca": parca, "adim": adim, "durum": durum}


def _tara(orch, file_id: int, *, max_istek: int, chunk_limit: int | None) -> dict:
    pr = orch.parse.parse_file(file_id)
    if pr.status == "FAILED":
        return {"hata": f"parse FAILED: {pr.fail_reason}"}
    co = orch.clean.clean_file(file_id, pr.parsed)
    if co.status == "FAILED":
        return {"hata": f"clean FAILED: {co.fail_reason}"}
    cho = orch.chunk.chunk_file(file_id, co.result.document)
    chunks = cho.chunks if chunk_limit is None else cho.chunks[:chunk_limit]

    z = Zehirleyici(orch.embed.embedder, max_istek=max_istek)
    saglam, zehirli = 0, []
    for c in chunks:
        sonuc = z.patliyor_mu(c.chunk_text)
        if sonuc is None:
            break
        if not sonuc:
            saglam += 1
            continue

        # Zehirli chunk: adli inceleme.
        uzunluk, en_uzun = _en_uzun_boslukssuz(c.chunk_text)
        kayit = {
            "chunk_index": c.chunk_index,
            "page_number": getattr(c, "page_number", None),
            "token_count": getattr(c, "token_count", None),
            "karakter": len(c.chunk_text),
            "pipe_var": "|" in c.chunk_text,
            "en_uzun_boslukssuz": uzunluk,
            "en_uzun_ornek": _kirp(en_uzun, 120),
            "alisilmadik_karakterler": _karakter_dokumu(c.chunk_text),
            "bas": c.chunk_text[:300],
            "son": c.chunk_text[-300:],
        }
        kayit["donusumler"] = []
        for ad, fn in DONUSUMLER:
            yeni = fn(c.chunk_text)  # type: ignore[operator]
            if yeni == c.chunk_text:
                kayit["donusumler"].append({"ad": ad, "sonuc": "metni DEGISTIRMEDI",
                                            "kanarya": "-"})
                continue
            # ÖNCE kanarya: backend zehirli istekten sonra ayakta mı? Değilse bu
            # dönüşümün sonucu içerik hakkında HİÇBİR ŞEY söylemez.
            k = z.patliyor_mu(KANARYA)
            p = z.patliyor_mu(yeni)
            kayit["donusumler"].append({
                "ad": ad,
                "sonuc": "butce bitti" if p is None else ("hala 500" if p else "GECTI"),
                "kanarya": "-" if k is None else ("DUSTU" if k else "saglam")})
        kayit["minimal"] = z.en_kucuk_zehir(c.chunk_text)
        son_kanarya = z.patliyor_mu(KANARYA)
        kayit["kanarya_son"] = ("-" if son_kanarya is None
                                else ("DUSTU" if son_kanarya else "saglam"))
        zehirli.append(kayit)

    return {"toplam_chunk": len(cho.chunks), "denenen": len(chunks),
            "saglam": saglam, "zehirli": zehirli,
            "istek": z.istek, "butce_bitti": z.tukendi}


def _bas(veri: dict, file_id: int) -> None:
    print("=" * 100)
    print(f"EMBED ZEHIR PROBU  file_id={file_id}")
    print("=" * 100)
    if "hata" in veri:
        print(f"  DURDU: {veri['hata']}")
        return
    print(f"  chunk: {veri['toplam_chunk']:,} (denenen {veri['denenen']:,})   "
          f"saglam: {veri['saglam']:,}   ZEHIRLI: {len(veri['zehirli'])}   "
          f"http istek: {veri['istek']:,}")
    if veri["butce_bitti"]:
        print("  UYARI: istek butcesi bitti -- tarama TAM DEGIL, --max-istek artirin.")
    if not veri["zehirli"]:
        print("  Hicbir chunk tek tek gonderilirken patlamadi.")
        print("  Bu, hatanin chunk ICERIGINDE degil PARTI BILESIMINDE ya da o anki")
        print("  backend durumunda oldugunu gosterir -- ayri bir teshis gerekir.")
        return
    print()
    for k in veri["zehirli"]:
        print("-" * 100)
        print(f"  chunk_index={k['chunk_index']}  s.{k['page_number']}  "
              f"{k['karakter']:,} karakter  {k['token_count']} token  "
              f"pipe={'VAR' if k['pipe_var'] else 'YOK'}")
        print(f"  en uzun bosluksuz dizi: {k['en_uzun_boslukssuz']} karakter")
        print(f"    {k['en_uzun_ornek']}")
        print(f"  alisilmadik karakterler: {k['alisilmadik_karakterler']}")
        print(f"  BAS: {k['bas']!r}")
        print(f"  SON: {k['son']!r}")
        print("  Donusumler (hangisi 500'u gideriyor?):")
        print("    kanarya = donusumden HEMEN ONCE gonderilen zararsiz metin. 'DUSTU'")
        print("    ise backend o an ayakta degildi ve o satirin sonucu OKUNAMAZ.")
        for d in k["donusumler"]:
            print(f"    {d['ad']:<38} -> {d['sonuc']:<18} (kanarya: {d['kanarya']})")
        print(f"    bolmeden sonra kanarya: {k.get('kanarya_son', '-')}")
        m = k["minimal"]
        print(f"  EN KUCUK PATLAYAN PARCA ({m['durum']}, {m['adim']} bolme, "
              f"{len(m['parca'])} karakter):")
        print(f"    {m['parca']!r}")
        if m["durum"] == "butce_bitti":
            print("    NOT: istek butcesi bitti -- bu parca MINIMAL DEGIL, yalnizca")
            print("    ulasilabilen en kucuk parca. --max-istek artirip tekrarlayin.")
        else:
            print(f"    kod noktalari: "
                  f"{[f'U+{ord(c):04X}' for c in m['parca'][:60]]}")
            if m["durum"] == "bolunmeyle_kayboldu":
                print("    (ortadan bolme zehir sinirdan gectigi icin durdu; parca")
                print("     uclardan budanarak kuculdu -- gosterilen metin GERCEKTEN patliyor)")
        print()


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file-id", type=int, required=True)
    ap.add_argument("--max-istek", type=int, default=600,
                    help="Toplam HTTP embed istegi tavani (canli ucu dovmemek icin)")
    ap.add_argument("--chunk-limit", type=int, default=None,
                    help="Yalniz ilk N chunk'i dene (hizli deneme)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from ragintel.config.settings import DbSettings
    from ragintel.database import Database
    from ragintel.ingestion.orchestrator import Orchestrator

    db = Database(DbSettings()).open()
    try:
        orch = Orchestrator(db)
        veri = _tara(orch, args.file_id, max_istek=args.max_istek,
                     chunk_limit=args.chunk_limit)
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()

    if args.json:
        print(json.dumps({"file_id": args.file_id, **veri},
                         ensure_ascii=False, indent=2, default=str))
    else:
        _bas(veri, args.file_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
