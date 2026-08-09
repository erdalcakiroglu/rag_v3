"""Chunking çekirdeği (İP-5) — deterministik, section-aware, overlap'li.

Strateji zinciri (config): section-based → paragraph fallback → sliding window.
Sınırlar app_config.chunking'ten (max_tokens/overlap_tokens/min_tokens). Token
sayımı enjekte edilen TokenCounter ile (üretimde BGE-M3). Tablolar bölünmez —
her tablonun flattened_text'i kendi chunk'ıdır. char_start/end temizlenmiş tam
metne göredir; overlap komşu chunk'ların char span'lerini kesiştirir.

Determinizm: hiçbir rastgelelik yok; aynı girdi+config → bit-bit aynı chunk seti.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...text.normalize import normalize_for_quote
from ..parsing.parsed_document import ParsedDocument, Table
from ..parsing.text_utils import flatten_table
from .chunk import Chunk
from .tokenizer import TokenCounter


@dataclass
class _Unit:
    text: str
    page_no: int | None
    section_title: str | None
    char_start: int
    char_end: int


def _build_units(doc: ParsedDocument) -> tuple[list[_Unit], str]:
    """Blokları okuma sırasında (page, section) etiketiyle düzleştirir ve
    kanonik tam metni ('\\n' ile join) + her bloğun char aralığını üretir."""
    section_titles = {}
    for s in doc.sections:
        key = normalize_for_quote(s.title)
        if key:
            section_titles[key] = s.title

    units: list[_Unit] = []
    parts: list[str] = []
    offset = 0
    current_section: str | None = None
    for page in doc.pages:
        for block in page.text_blocks:
            if not block:
                continue
            key = normalize_for_quote(block)
            if key in section_titles:
                current_section = section_titles[key]
            start = offset
            end = offset + len(block)
            units.append(_Unit(block, page.page_no, current_section, start, end))
            parts.append(block)
            offset = end + 1   # '\n' ayırıcı
    full_text = "\n".join(parts)
    return units, full_text


def _window_ranges(n: int, max_t: int, overlap: int, min_t: int) -> list[tuple[int, int]]:
    """Token index aralıkları: ≤max_t, overlap'li; küçük son pencereyi dengele."""
    if n <= max_t:
        return [(0, n)]
    step = max(1, max_t - overlap)
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < n:
        end = min(start + max_t, n)
        ranges.append((start, end))
        if end == n:
            break
        start += step
    # Son pencere min altındaysa: son ikiyi birleştirip dengeli böl (max aşmadan).
    if len(ranges) >= 2 and (ranges[-1][1] - ranges[-1][0]) < min_t:
        a_start = ranges[-2][0]
        total_end = ranges[-1][1]
        mid = a_start + (total_end - a_start) // 2
        ranges[-2] = (a_start, min(mid + overlap, total_end))
        ranges[-1] = (mid, total_end)
    return ranges


def _split_span(text: str, counter: TokenCounter, max_t: int, overlap: int,
                min_t: int) -> list[tuple[int, int, int]]:
    """Metni token pencerelerine böler -> [(char_start, char_end, token_count)]."""
    spans = counter.token_spans(text)
    n = len(spans)
    if n == 0:
        return []
    out = []
    for ts, te in _window_ranges(n, max_t, overlap, min_t):
        out.append((spans[ts][0], spans[te - 1][1], te - ts))
    return out


def _group_by_section(units: list[_Unit]) -> list[list[_Unit]]:
    groups: list[list[_Unit]] = []
    for u in units:
        if groups and groups[-1][0].section_title == u.section_title:
            groups[-1].append(u)
        else:
            groups.append([u])
    return groups


def _pack_paragraphs(units: list[_Unit], counter: TokenCounter,
                     max_t: int) -> list[list[_Unit]]:
    """Paragrafları (blokları) max_t bütçesine kadar açgözlü paketler."""
    groups: list[list[_Unit]] = []
    cur: list[_Unit] = []
    cur_tokens = 0
    for u in units:
        ut = counter.count(u.text)
        if cur and cur_tokens + ut > max_t:
            groups.append(cur)
            cur, cur_tokens = [], 0
        cur.append(u)
        cur_tokens += ut
    if cur:
        groups.append(cur)
    return groups


def _offset_lookup(units: list[_Unit], offset: int) -> _Unit:
    """char offset'i içeren birimi bulur (page/section için)."""
    lo = None
    for u in units:
        if u.char_start <= offset <= u.char_end:
            return u
        if u.char_start <= offset:
            lo = u
    return lo or units[0]


def _min_merge(chunks: list[Chunk], counter: TokenCounter,
               min_t: int, max_t: int) -> list[Chunk]:
    """min altı (tablo olmayan) chunk'ı komşusuna birleştirir (max aşmadan).

    Kural GERİYE birleştirmektir. Tek istisna İLK chunk'tır: arkasında
    birleşeceği bir şey yoktur, bu yüzden SONRAKİNİ kendine çeker (ileri).

    Gerekçe ÖLÇÜLDÜ (2026-08-09, canlı korpus 1117 dosya / 50065 chunk;
    scripts/metin_korunumu_probe.py §5): min-altı 1060 chunk'ın 831'i (%78.4)
    chunk_index=0'daydı ve yalnız geriye birleştirdiğimiz için hiçbiri
    birleşemiyordu. 807 dosyanın (korpusun %72'si) TEK kusuru buydu. Tipik
    hâli: 'section' stratejisinde belge başlığı kendi başına bir chunk olur
    (ör. tek sayfalık BDDK tebliği → 10 token'lık başlık + gövde), sonra
    gövdeden koparılmış o başlık gömülüp arama sonuçlarına içeriksiz bir
    eşleşme olarak girer.
    """
    if len(chunks) <= 1:
        return chunks
    result: list[Chunk] = []
    for c in chunks:
        if (result and not c.is_table and not result[-1].is_table
                and c.token_count < min_t
                and result[-1].token_count + c.token_count <= max_t):
            prev = result[-1]
            merged = prev.chunk_text + "\n" + c.chunk_text
            prev.chunk_text = merged
            prev.chunk_text_norm = normalize_for_quote(merged)
            prev.token_count = counter.count(merged)
            if c.char_end is not None:
                prev.char_end = c.char_end
        else:
            result.append(c)

    # İleri birleşme YALNIZ baştadır. Geriye birleşmede hayatta kalan hep ERKEN
    # olan chunk'tır (metadata'sını korur, char_end'i uzar); burada da aynısı:
    # result[0] yaşar, sonrakini yutar. Döngü, baştaki birkaç minik chunk'ın
    # (başlık + alt başlık) tek gövdede toplanabilmesi için.
    while (len(result) > 1 and not result[0].is_table and not result[1].is_table
           and result[0].token_count < min_t
           and result[0].token_count + result[1].token_count <= max_t):
        ilk, sonraki = result[0], result[1]
        merged = ilk.chunk_text + "\n" + sonraki.chunk_text
        ilk.chunk_text = merged
        ilk.chunk_text_norm = normalize_for_quote(merged)
        ilk.token_count = counter.count(merged)
        if sonraki.char_end is not None:
            ilk.char_end = sonraki.char_end
        if ilk.section_title is None:
            ilk.section_title = sonraki.section_title
        del result[1]
    return result


def _table_chunk(text: str, t: Table, counter: TokenCounter,
                 section_title: str | None, *,
                 row_start: int | None = None, row_end: int | None = None) -> Chunk:
    """Bir tablo (alt-)chunk'ı üretir — sheet/sayfa kimliği korunur, is_table=True.

    M-2b: tablo bağı (table_index + satır aralığı) chunk'ın KENDİSİNDE taşınır;
    okuma tarafı artık section_title'ı regex'le ayrıştırmak zorunda değil.
    """
    return Chunk(
        chunk_index=-1,
        chunk_text=text,
        chunk_text_norm=normalize_for_quote(text),
        token_count=counter.count(text),
        page_number=t.page_no,
        sheet_name=t.sheet_name,
        section_title=section_title,
        char_start=None,
        char_end=None,
        is_table=True,
        table_index=t.index,
        table_row_start=row_start,
        table_row_end=row_end,
    )


def _table_chunks(t: Table, counter: TokenCounter, subchunk_max: int) -> list[Chunk]:
    """M-1: Tablo eşik-altıysa AYNEN tek chunk (mevcut davranış). Eşiği aşarsa
    satır-gruplarına böl; her alt-chunk BAŞLIK satırını tekrar taşır (bağlamsız
    satır anlamsızdır) ve section_title'da tablo-indeks + satır aralığı tutulur."""
    text = t.flattened_text or ""
    if not text.strip():
        return []
    # Eşik-altı VEYA bölünecek gövde yok (yalnız başlık) → tek chunk, eskisi gibi.
    rows = t.data or []
    if counter.count(text) <= subchunk_max or len(rows) < 2:
        return [_table_chunk(text, t, counter, section_title=None)]

    header, body = rows[0], rows[1:]
    out: list[Chunk] = []
    group: list = []
    grp_start = 1  # gövde satırı numarası (başlık hariç, 1-tabanlı)

    def flush(g: list, start: int) -> None:
        gtext = flatten_table([header] + g)
        end = start + len(g) - 1
        span = f"satır {start}" if start == end else f"satır {start}-{end}"
        # section_title İNSAN İÇİN kalır (kaynak panelinde okunur); makine bağı
        # artık kolonlarda (table_index/row_start/row_end) — M-2b.
        out.append(_table_chunk(gtext, t, counter,
                                section_title=f"tablo{t.index} · {span}",
                                row_start=start, row_end=end))

    for i, row in enumerate(body):
        cand_text = flatten_table([header] + group + [row])
        if group and counter.count(cand_text) > subchunk_max:
            flush(group, grp_start)
            group = [row]
            grp_start = i + 1  # gövde 0-indeks i → 1-tabanlı satır i+1
        else:
            group.append(row)
    if group:
        flush(group, grp_start)
    return out


def chunk_document(doc: ParsedDocument, *, counter: TokenCounter,
                   strategy: str = "section", max_tokens: int = 512,
                   overlap_tokens: int = 64, min_tokens: int = 30,
                   table_subchunk_max_tokens: int = 512) -> list[Chunk]:
    units, full_text = _build_units(doc)

    # Strateji zinciri: section yoksa paragraph, tek blok/none ise sliding.
    has_sections = bool(doc.sections) and any(u.section_title for u in units)
    eff = strategy
    if eff == "section" and not has_sections:
        eff = "paragraph" if len(units) > 1 else "sliding"
    if eff == "paragraph" and len(units) <= 1:
        eff = "sliding"

    if not units:
        segments: list[list[_Unit]] = []
    elif eff == "section":
        segments = _group_by_section(units)
    elif eff == "paragraph":
        segments = _pack_paragraphs(units, counter, max_tokens)
    else:  # sliding
        segments = [units]

    chunks: list[Chunk] = []
    for seg in segments:
        seg_start = seg[0].char_start
        seg_end = seg[-1].char_end
        seg_text = full_text[seg_start:seg_end]
        for cs, ce, tok in _split_span(seg_text, counter, max_tokens,
                                       overlap_tokens, min_tokens):
            g_start = seg_start + cs
            g_end = seg_start + ce
            text = full_text[g_start:g_end]
            u = _offset_lookup(units, g_start)
            chunks.append(Chunk(
                chunk_index=-1,
                chunk_text=text,
                chunk_text_norm=normalize_for_quote(text),
                token_count=tok,
                page_number=u.page_no,
                section_title=u.section_title,
                char_start=g_start,
                char_end=g_end,
            ))

    # Tablolar: eşik-altı tek chunk; büyük tablolar satır-gruplarına bölünür (M-1).
    for t in doc.tables:
        chunks.extend(_table_chunks(t, counter, table_subchunk_max_tokens))

    chunks = _min_merge(chunks, counter, min_tokens, max_tokens)
    for i, c in enumerate(chunks):
        c.chunk_index = i
    return chunks
