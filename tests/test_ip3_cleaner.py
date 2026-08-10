"""İP-3 deterministik cleaning (regresyon suiti; DB gerekmez).

v1 document_cleaning.py çalışma alanında bulunmadığından, bu testler İP-3
kurallarını (boş satır, header/footer, ftfy, gereksiz karakter, anlam korunur)
kodlar ve ileriye dönük regresyon setidir.
"""

from __future__ import annotations

from ragintel.ingestion.cleaning import clean_document
from ragintel.ingestion.parsing.parsed_document import (
    Figure,
    Page,
    ParsedDocument,
    Section,
    Table,
)


def _doc(pages_blocks, **kw):
    return ParsedDocument(pages=[Page(i + 1, b) for i, b in enumerate(pages_blocks)], **kw)


def test_blank_and_whitespace_blocks_removed():
    r = clean_document(_doc([["Gerçek metin.", "   ", "", "\t"]]))
    assert r.document.pages[0].text_blocks == ["Gerçek metin."]
    assert r.metrics["blank_blocks_removed"] == 3


def test_junk_chars_and_inline_whitespace():
    r = clean_document(_doc([["Karbon​ vergisi­  çok    boşluk"]]))
    assert r.document.pages[0].text_blocks == ["Karbon vergisi çok boşluk"]


def test_ftfy_fixes_broken_encoding():
    r = clean_document(_doc([["Ã©tude Ã¼zerine"]]))
    assert r.document.pages[0].text_blocks[0].startswith("étude üzerine")
    assert r.metrics["encoding_fixes"] == 1


def test_meaning_preserved_no_stopword_or_punctuation_removal():
    text = "Karbon vergisi nedir? Bu bir sorudur ve cevabı vardır."
    r = clean_document(_doc([[text]]))
    assert r.document.pages[0].text_blocks[0] == text   # anlam/punct korunur


def test_header_footer_repeated_across_pages_removed():
    pages = [["GIZLI - Ust Bilgi", f"Icerik {i}.", "Alt Bilgi www.x.com"] for i in range(4)]
    r = clean_document(_doc(pages))
    for p in r.document.pages:
        assert p.text_blocks == [f"Icerik {p.page_no - 1}."]
    assert r.metrics["header_footer_removed"] == 8      # 2 imza × 4 sayfa
    assert len(r.metrics["header_footer_signatures"]) == 2


def test_no_header_footer_removal_under_three_pages():
    pages = [["Tekrar Satır", "Icerik A"], ["Tekrar Satır", "Icerik B"]]
    r = clean_document(_doc(pages))
    # 3 sayfadan az -> boilerplate kaldırılmaz
    assert r.metrics["header_footer_removed"] == 0
    assert "Tekrar Satır" in r.document.pages[0].text_blocks


def test_tables_and_figures_pass_through_ip2_wins():
    doc = _doc([["gövde"]],
               tables=[Table(0, [["a", "b"]], "a | b", page_no=1)],
               figures=[Figure(0, page_no=1, caption="şekil")],
               sections=[Section("Başlık", 1, page_start=1)])
    r = clean_document(doc)
    assert len(r.document.tables) == 1 and r.document.tables[0].flattened_text == "a | b"
    assert len(r.document.figures) == 1
    assert r.document.sections[0].title == "Başlık"


# --- 0x02 tire glifi onarımı -------------------------------------------------
# Örneklerin tamamı korpustan ÖLÇÜLDÜ (c0_tanim_probe Bölüm F+G, 2026-08-10).

def test_satir_sonu_hecelemesi_birlestirilir():
    r = clean_document(_doc([["20 Şu\x02 bat 1991 tarihli"]]))
    assert r.document.pages[0].text_blocks[0] == "20 Şubat 1991 tarihli"
    assert r.metrics["hyphen_glyph_joined"] == 1
    assert r.metrics["hyphen_glyph_kept"] == 0


def test_bosluksuz_heceleme_de_birlestirilir():
    r = clean_document(_doc([["Denet\x02leme Kurulu"]]))
    assert r.document.pages[0].text_blocks[0] == "Denetleme Kurulu"


def test_sozluksel_tire_korunur_birlestirilmez():
    # Sağ taraf BÜYÜK harf -> gerçek tire; "CPMIIOSCO" üretmek yanlış olurdu.
    r = clean_document(_doc([["CPMI\x02 IO SCO ilkeleri", "Opsiyon Delta\x02 Eşdeğeri"]]))
    bloklar = r.document.pages[0].text_blocks
    assert bloklar[0] == "CPMI-IO SCO ilkeleri"
    assert bloklar[1] == "Opsiyon Delta-Eşdeğeri"
    assert r.metrics["hyphen_glyph_joined"] == 0
    assert r.metrics["hyphen_glyph_kept"] == 2


def test_formuldeki_eksi_isareti_tire_olarak_kalir():
    r = clean_document(_doc([["0,20*(A-B+C+D\x02 E)"]]))
    assert r.document.pages[0].text_blocks[0] == "0,20*(A-B+C+D-E)"


def test_hicbir_0x02_hayatta_kalmaz():
    # Harfe komşu olmayan tekil 0x02 de görünmez kalmaz, "-" olur.
    r = clean_document(_doc([["madde \x02 bendi", "Öz\x02.kaynak"]]))
    for blok in r.document.pages[0].text_blocks:
        assert "\x02" not in blok
    assert r.document.pages[0].text_blocks[0] == "madde - bendi"


def test_ardisik_hecelemeler_tek_tek_kapatilir():
    # sub() sağ harfi TÜKETMEZ (lookahead) — bitişik geçişler kaçmamalı.
    r = clean_document(_doc([["gereğin\x02 ce borç\x02 ları"]]))
    assert r.document.pages[0].text_blocks[0] == "gereğince borçları"
    assert r.metrics["hyphen_glyph_joined"] == 2


def test_tirenin_dokunulmadigi_metin_aynen_kalir():
    text = "Kurul, 2024-2025 dönemi için risk-ağırlıklı varlıkları belirler."
    r = clean_document(_doc([[text]]))
    assert r.document.pages[0].text_blocks[0] == text
    assert r.metrics["hyphen_glyph_joined"] == 0
    assert r.metrics["hyphen_glyph_kept"] == 0


def test_tabloda_da_onarilir_yapisi_bozulmadan():
    # Bölüm E: depolanmış C0 taşıyan 760 chunk'ın 760'ı TABLO kaynaklı.
    doc = _doc([["gövde"]],
               tables=[Table(0, [["Top\x02 lam Aktifler", "Yü\x02 zde"]],
                             "Top\x02 lam Aktifler | Yü\x02 zde", page_no=1)])
    r = clean_document(doc)
    t = r.document.tables[0]
    assert t.flattened_text == "Toplam Aktifler | Yüzde"
    assert t.data == [["Toplam Aktifler", "Yüzde"]]
    assert (t.index, t.page_no) == (0, 1)
    assert r.metrics["hyphen_glyph_joined"] == 4   # 2 hücre + 2 düzleştirilmiş


def test_temiz_tablo_nesnesi_kopyalanmaz():
    # Dokunulacak bir şey yoksa İP-2 nesnesi AYNEN geçer (kimlik korunur).
    t = Table(0, [["a", "b"]], "a | b", page_no=1)
    r = clean_document(_doc([["gövde"]], tables=[t]))
    assert r.document.tables[0] is t


def test_strip_junk_0x02_yi_sessizce_silemez():
    # Onarım _strip_junk'tan ÖNCE koşmazsa geriye "Şu bat" kalır ve bağlam
    # geri getirilemez. Bu test o sıralamayı kilitler.
    r = clean_document(_doc([["Şu\x02 bat"]]))
    assert r.document.pages[0].text_blocks[0] != "Şu bat"


def test_retention_ratio_computed():
    # 100 karakterlik gövde; header/footer yok, sadece küçük kırpma.
    doc = _doc([["A" * 100]])
    r = clean_document(doc)
    assert r.metrics["original_chars"] == 100
    assert r.metrics["retention_ratio"] == 1.0

    heavy = _doc([["kısa", "SILINECEK BOILERPLATE UZUN SATIR " * 2] for _ in range(4)])
    r2 = clean_document(heavy)
    assert r2.metrics["retention_ratio"] < 1.0
