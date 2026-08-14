"""Depo arşivi tarama yolunun DIŞINDA kalmalı — DB'siz bekçi.

NEDEN VAR: canlıda izlenen klasör `/datafile/ragintel/storage`, depo kökü de aynı
yer; `storage/raw/<sha>/…` yani ARŞİVİMİZ tarama ağacının içinde kalıyordu.
Checksum dedup bunu normalde SKIP'ler, ama bir dosyanın `core_files` satırı
silinince checksum kaydı da gider ve sonraki tarama dosyayı kendi arşivinden
diriltir. 2026-08-14'te tam bu oldu: 08-13'te elenen altı mükerrer Bankacılık
Kanunu baskısı geri geldi, retrieval r@10 0.682'den 0.618'e düştü.

Bu test DB'ye ihtiyaç duymaz — kusur dosya sistemi gezintisinde, bekçisi de orada
durmalı ki canlı DB olmadan da koşsun.
"""

from __future__ import annotations

import os

from ragintel.ingestion.scanner import FolderScanner


class _SessizLog:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


def _scanner(storage_root) -> FolderScanner:
    """DB'siz iskelet: `_iter_files` yalnız `storage_root` ve `log` kullanır."""
    s = object.__new__(FolderScanner)
    s.storage_root = str(storage_root)
    s.log = _SessizLog()
    return s


def _yaz(path: str, icerik: str = "x") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(icerik)


def _adlar(scanner: FolderScanner, folder: str, recursive: bool = True) -> set[str]:
    return {os.path.basename(p) for p in scanner._iter_files(folder, recursive)}


def test_depo_koku_taranirken_arsiv_atlanir(tmp_path):
    depo = tmp_path / "storage"
    _yaz(str(depo / "yeni_belge.pdf"))
    _yaz(str(depo / "raw" / "ab" / "abc123" / "silinmis_mukerrer.pdf"))

    adlar = _adlar(_scanner(depo), str(depo))

    assert "yeni_belge.pdf" in adlar
    assert "silinmis_mukerrer.pdf" not in adlar, "arşiv kopyası yeniden ingest edilir"


def test_depo_ustu_klasor_taranirken_de_atlanir(tmp_path):
    """İzlenen klasör depo kökünü KAPSIYORSA da arşiv dışarıda kalmalı."""
    ust = tmp_path / "ragintel"
    depo = ust / "storage"
    _yaz(str(ust / "gelen.pdf"))
    _yaz(str(depo / "kokte.pdf"))
    _yaz(str(depo / "raw" / "cd" / "cde456" / "arsiv.pdf"))

    adlar = _adlar(_scanner(depo), str(ust))

    assert {"gelen.pdf", "kokte.pdf"} <= adlar
    assert "arsiv.pdf" not in adlar


def test_dogrudan_arsiv_taranirsa_bos_doner(tmp_path):
    """Arşivin kendisi hedef gösterilse bile girdi kaynağı değildir."""
    depo = tmp_path / "storage"
    _yaz(str(depo / "raw" / "ef" / "ef789" / "arsiv.pdf"))

    assert _adlar(_scanner(depo), str(depo / "raw")) == set()


def test_baska_klasordeki_raw_dislanmaz(tmp_path):
    """Dışlama ada göre değil YOLA göre: alakasız bir 'raw' klasörü taranmalı.

    Aksi hâlde koruma, kullanıcının kendi 'raw' adlı kaynak klasörünü sessizce
    yutardı — kanıt yokluğunu yokluk kanıtı sanmanın dosya sistemi hâli.
    """
    depo = tmp_path / "storage"
    _yaz(str(depo / "kokte.pdf"))
    kaynak = tmp_path / "gelen_kutusu"
    _yaz(str(kaynak / "raw" / "musteri_belgesi.pdf"))

    assert "musteri_belgesi.pdf" in _adlar(_scanner(depo), str(kaynak))


def test_recursive_false_davranisi_degismedi(tmp_path):
    depo = tmp_path / "storage"
    _yaz(str(depo / "kokte.pdf"))
    _yaz(str(depo / "alt" / "derinde.pdf"))

    adlar = _adlar(_scanner(depo), str(depo), recursive=False)

    assert adlar == {"kokte.pdf"}
