"""Deterministik cleaning çekirdeği (İP-3).

ParsedDocument (İP-2) üzerinde çalışır; SAYFA yapısını korur (citation için) ve
tablo/şekilleri OLDUĞU GİBİ geçirir (İP-2 kazanır — cleaning tabloya dokunmaz).

Adımlar (anlam yeniden yazılmaz):
  1. Kırık encoding onarımı (ftfy).
  2. Gereksiz karakter temizliği (zero-width, soft-hyphen, kontrol, U+FFFD;
     satır içi ardışık boşluk sadeleştirme).
  3. Boş satır/blok atma.
  4. Header/footer tekrarı tespiti ve atma (çok sayıda sayfada baş/son konumda
     tekrarlayan kısa bloklar).

Çıktı: cleaned ParsedDocument + cleaning_metrics (kırpılan karakter, retention,
header/footer imzaları, encoding düzeltme sayısı).
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

import ftfy

from ..parsing.parsed_document import Page, ParsedDocument, Table
from ...text.normalize import normalize_for_quote

# Görünmez/gereksiz karakterler (soft hyphen, zero-width, BOM, replacement).
_JUNK = dict.fromkeys(map(ord, "­​‌‍⁠﻿�"), None)
_INLINE_WS = re.compile(r"[^\S\n]+")   # newline hariç ardışık boşluk

# --- tire glifi onarımı (0x02) ----------------------------------------------
# ÖLÇÜLDÜ (`scripts/c0_tanim_probe.py` Bölüm F+G, 2026-08-10): bozuk font
# eşlemesinde TİRE glifi 0x02'ye (STX) düşmüş — 1155 geçiş, 54 dosya. Bağlam
# hükmü tek başına veriyor: "20 Şu\x02 bat 1991", "Denet\x02 leme Kurulu",
# "gereğin\x02 ce", "borç\x02 ları", "CPMI\x02 IO SCO", "0,20*(A-B+C+D\x02 E)".
#
# İKİ AYRI TİRE, TEK GLİF. Ayrımı SAĞ TARAFIN BÜYÜK/KÜÇÜKLÜĞÜ verir:
#   * sağ küçük harfle başlıyorsa satır-sonu HECELEMESİ -> birleştir
#     ("Şu-bat" -> "Şubat"). Türkçede satır sonu tiresi kelimenin parçası
#     değildir; 610 geçişin tamamı bu kalıpta.
#   * sağ büyük harf/rakam/noktalama ise SÖZLÜKSEL tire -> "-" olarak kalır
#     ("CPMI-IOSCO", "Delta-Eşdeğeri", formülde "D-E"). Uydurmadan, sadık.
# Kalan tekil 0x02'ler de "-" olur: kaynakta gerçekten tire vardır, görünmez
# bir kontrol karakteri bırakmanın hiçbir savunması yok.
#
# BİLİNEN VE KABUL EDİLEN KAYIP: İngilizce bileşik terimler satır sonunda
# bölündüğünde tirelerini yitirir ("risk-weighted" -> "riskweighted"; ölçümde
# shortterm/nontrading/offbalance/foreignexchange de var, ~%9). Sözlük olmadan
# "Şubat" ile "risk-weighted" ayrılamaz ve tercih Türkçeden yana yapıldı:
# korpus ve sorgular Türkçe, kaybedilen terimler Basel tablolarında kalıyor.
#
# DÜZYAZIDA DA GEREKLİ: `_strip_junk` 0x02'yi SİLER ve geriye "Şu bat" kalır —
# DB'den görünmeyen, sessiz bir bozulma. Bu yüzden onarım `_strip_junk`tan
# ÖNCE koşar ve tablolara da uygulanır.
TIRE_GLIF = "\x02"
_TIRE = re.compile(r"(?<=[^\W\d_])\x02[ \t]?(?=([^\W\d_]))")


def _tire_karar(m: re.Match) -> str:
    return "" if m.group(1).islower() else "-"


def onar_tire_glifi(text: str) -> tuple[str, int, int]:
    """0x02'yi onarır; (metin, birleştirilen, tireye_çevrilen) döner."""
    if TIRE_GLIF not in text:
        return text, 0, 0
    birlesen = 0
    tutulan = 0

    def _sar(m: re.Match) -> str:
        nonlocal birlesen, tutulan
        out = _tire_karar(m)
        if out == "":
            birlesen += 1
        else:
            tutulan += 1
        return out

    ara = _TIRE.sub(_sar, text)
    # Kalanlar harfe komşu değil (rakam/noktalama/boşluk arası) — onlar da tire.
    tutulan += ara.count(TIRE_GLIF)
    return ara.replace(TIRE_GLIF, "-"), birlesen, tutulan


@dataclass
class CleanResult:
    document: ParsedDocument
    cleaned_text: str
    metrics: dict = field(default_factory=dict)


def _strip_junk(text: str) -> str:
    # Kontrol karakterlerini (kategori C*, \n/\t hariç) at.
    out = []
    for ch in text:
        if ch in "\n\t":
            out.append(ch)
            continue
        if unicodedata.category(ch).startswith("C"):
            continue
        out.append(ch)
    return "".join(out).translate(_JUNK)


def _clean_block(text: str) -> tuple[str, bool, int, int]:
    """Tek metin bloğunu temizler.

    (temiz_metin, encoding_düzeltildi_mi, tire_birleşen, tire_çevrilen).
    Tire onarımı EN BAŞTA koşar: `_strip_junk` 0x02'yi sessizce siler ve
    bağlam ("Şu bat") geri getirilemez hâle gelir.
    """
    tired, birlesen, cevrilen = onar_tire_glifi(text)
    fixed = ftfy.fix_text(tired)
    encoding_fixed = fixed != tired
    cleaned = _strip_junk(fixed)
    cleaned = _INLINE_WS.sub(" ", cleaned).strip()
    return cleaned, encoding_fixed, birlesen, cevrilen


def _tabloyu_onar(tables: list) -> tuple[list, int, int]:
    """Tablolarda YALNIZ tire glifini onarır; (tablolar, birleşen, çevrilen).

    İP-2 "cleaning tabloya dokunmaz" kuralının bilinçli ve DAR istisnası:
    yapı/hücre düzeni/boşluk aynen kalır, değişen tek şey bozuk font
    eşlemesinden gelen 0x02 karakteridir. Gerekçe ölçülü — depolanmış
    C0 taşıyan 760 chunk'ın 760'ı tablo kaynaklı (Bölüm E); tabloyu
    atlamak bu kusurun tamamını yerinde bırakırdı.
    """
    birlesen = cevrilen = 0
    out = []
    for t in tables:
        ft, b1, c1 = onar_tire_glifi(t.flattened_text)
        yeni_data = []
        for satir in t.data:
            hucreler = []
            for h in satir:
                if isinstance(h, str):
                    h, b2, c2 = onar_tire_glifi(h)
                    birlesen += b2
                    cevrilen += c2
                hucreler.append(h)
            yeni_data.append(hucreler)
        birlesen += b1
        cevrilen += c1
        out.append(
            Table(
                index=t.index,
                data=yeni_data,
                flattened_text=ft,
                page_no=t.page_no,
                sheet_name=t.sheet_name,
            )
            if (b1 or c1 or yeni_data != t.data)
            else t
        )
    return out, birlesen, cevrilen


def _detect_boilerplate(page_blocks: list[list[str]], total_pages: int) -> set[str]:
    """Header/footer imzalarını bulur: baş/son konumda çok sayıda sayfada
    tekrarlayan kısa bloklar (normalize edilmiş anahtar)."""
    if total_pages < 3:
        return set()
    threshold = max(3, math.ceil(0.5 * total_pages))
    counts: dict[str, int] = {}
    for blocks in page_blocks:
        if not blocks:
            continue
        # Yalnızca baş(2) ve son(2) bloklar header/footer adayı.
        candidates = set(blocks[:2] + blocks[-2:])
        for b in candidates:
            if len(b) > 200:
                continue
            key = normalize_for_quote(b)
            if key:
                counts[key] = counts.get(key, 0) + 1
    return {k for k, c in counts.items() if c >= threshold}


def clean_document(parsed: ParsedDocument) -> CleanResult:
    original_chars = len(parsed.body_text)

    # 1-2) Blok bazında encoding onarımı + karakter temizliği.
    encoding_fixes = 0
    tire_birlesen = 0
    tire_cevrilen = 0
    cleaned_pages_blocks: list[list[str]] = []
    for page in parsed.pages:
        blocks = []
        for raw in page.text_blocks:
            cleaned, fixed, b, c = _clean_block(raw)
            if fixed:
                encoding_fixes += 1
            tire_birlesen += b
            tire_cevrilen += c
            blocks.append(cleaned)   # boşları henüz atma (konum korunur)
        cleaned_pages_blocks.append(blocks)

    onarilmis_tablolar, tb, tc = _tabloyu_onar(parsed.tables)
    tire_birlesen += tb
    tire_cevrilen += tc

    # 3-4) Header/footer imzaları (boşluk atmadan önce konum korunur).
    signatures = _detect_boilerplate(cleaned_pages_blocks, len(parsed.pages))

    blank_removed = 0
    header_footer_removed = 0
    new_pages: list[Page] = []
    for page, blocks in zip(parsed.pages, cleaned_pages_blocks):
        kept = []
        for b in blocks:
            if not b:
                blank_removed += 1
                continue
            if normalize_for_quote(b) in signatures:
                header_footer_removed += 1
                continue
            kept.append(b)
        new_pages.append(Page(page_no=page.page_no, text_blocks=kept))

    cleaned_doc = ParsedDocument(
        pages=new_pages,
        sections=parsed.sections,      # İP-2 çıktısı korunur
        tables=onarilmis_tablolar,     # İP-2 kazanır — YALNIZ tire glifi onarılır
        figures=parsed.figures,
        language=parsed.language,
        parse_warnings=list(parsed.parse_warnings),
    )

    cleaned_text = cleaned_doc.body_text
    cleaned_chars = len(cleaned_text)
    retention = (cleaned_chars / original_chars) if original_chars else 1.0

    metrics = {
        "original_chars": original_chars,
        "cleaned_chars": cleaned_chars,
        "removed_chars": max(0, original_chars - cleaned_chars),
        "retention_ratio": round(retention, 4),
        "blank_blocks_removed": blank_removed,
        "header_footer_removed": header_footer_removed,
        "header_footer_signatures": sorted(signatures)[:20],
        "encoding_fixes": encoding_fixes,
        # 0x02 tire glifi (Bölüm F+G). birlesen = satır-sonu hecelemesi
        # kapatıldı; cevrilen = sözlüksel tire olarak "-" bırakıldı.
        "hyphen_glyph_joined": tire_birlesen,
        "hyphen_glyph_kept": tire_cevrilen,
    }
    return CleanResult(document=cleaned_doc, cleaned_text=cleaned_text, metrics=metrics)
