"""Docling parse backend'i (İP-2 birincil): pdf/docx, dahili OCR (rapidocr).

Docling ağırdır (torch); import tembeldir ve converter'lar (OCR açık/kapalı)
önbelleğe alınır. DoclingDocument -> ParsedDocument eşlemesi sayfa numaralarını
(prov) korur — citation zinciri için zorunlu. Eşleme API farkına karşı
savunmacıdır; eksik alanlar parse_warnings'e düşer, çökmez.
"""

from __future__ import annotations

from .parsed_document import Figure, Page, ParsedDocument, Section, Table
from .text_utils import detect_language, flatten_table


def _resolve_pdf_backend(name: str):
    """PDF alt-parser sınıfını isimden çözer (import tembel; docling ağır).

    None dönerse docling KENDİ varsayılanını kullanır (dlparse). pypdfium2
    varsayılan çünkü docling-parse native katmanı bazı doğuştan-dijital banka
    PDF'lerinde std::bad_alloc atıp sayfayı sessizce düşürüyor (ölçüldü).
    """
    n = (name or "").strip().lower()
    if n in ("pypdfium2", "pdfium"):
        from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
        return PyPdfiumDocumentBackend
    if n in ("docling_parse", "dlparse", "dlparse_v4", "default", "auto", ""):
        return None
    if n in ("dlparse_v2", "docling_parse_v2"):
        from docling.backend.docling_parse_v2_backend import DoclingParseV2DocumentBackend
        return DoclingParseV2DocumentBackend
    raise ValueError(f"Bilinmeyen pdf_backend: {name!r}")


class DoclingBackend:
    name = "docling"
    # Glif onarımı (parsing/glyph_repair.py) bu yeteneği sorgular; fallback
    # backend'de bayrak yoktur ve onarım kolu sessizce atlanır.
    supports_full_page_ocr = True

    def __init__(self, *, figure_images: bool = True, figure_image_scale: float = 2.0,
                 pdf_backend: str = "pypdfium2", tableformer_mode: str = "accurate",
                 parse_num_threads: int = 4) -> None:
        self._converters: dict[bool, object] = {}
        # M-7: görsel çıkarma config'ten gelir (ingestion.figure_images/_scale).
        self.figure_images = figure_images
        self.figure_image_scale = figure_image_scale
        # PDF alt-parser (config: parse.pdf_backend). docling-parse native C++
        # katmanı bazı banka PDF'lerinde std::bad_alloc atıp sayfayı sessizce
        # düşürüyor; pypdfium2 varsayılan (bkz. ParsingSettings.pdf_backend).
        self.pdf_backend = pdf_backend
        # İP-2 (parse hızlandırma): TableFormer modu + thread sayısı (config-first).
        # 'fast' tabloyu KAPATMADAN CPU'da belirgin hızlandırır; GPU yokken
        # tablo-yoğun büyük PDF'lerin timeout'unu bu çözer (ölçüldü).
        self.tableformer_mode = (tableformer_mode or "accurate").strip().lower()
        self.parse_num_threads = int(parse_num_threads)

    def supports(self, file_type: str) -> bool:
        return file_type in ("pdf", "docx")

    def _converter(self, ocr: bool, full_page: bool = False):
        anahtar = (bool(ocr), bool(full_page))
        if anahtar not in self._converters:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                AcceleratorOptions, PdfPipelineOptions, TableFormerMode,
            )

            opts = PdfPipelineOptions()
            opts.do_ocr = ocr or full_page
            if full_page:
                # İKİSİ AYNI ŞEY DEĞİL: `do_ocr=True` yalnızca metin katmanı
                # OLMAYAN bölgeleri OCR'lar. Bozuk kodlamalı PDF'lerin metin
                # katmanı VARDIR (yalnız anlamsızdır), bu yüzden do_ocr hiç
                # tetiklenmez — ölçüldü, çıktı OCR'sız koşumla bit-bit aynı
                # çıktı. `force_full_page_ocr` metin katmanını yok sayıp
                # sayfayı piksellerinden okur; bozuk aileleri kurtaran TEK kol.
                opts.ocr_options.force_full_page_ocr = True
            opts.do_table_structure = True
            # İP-2: TableFormer modu (config-first). 'fast' tablo çıkarımını
            # KAPATMADAN CPU'da belirgin hızlandırır (mühürlü ACCURATE korpusa
            # dokunmaz; yalnız config 'fast' iken devrede). Savunmacı: bilinmeyen
            # değer 'accurate'a düşer.
            opts.table_structure_options.mode = (
                TableFormerMode.FAST if self.tableformer_mode == "fast"
                else TableFormerMode.ACCURATE
            )
            # İP-2: sinir-ağı thread sayısı (docling default 4; CPU'da yükseltmek
            # büyük PDF parse süresini kısaltır, GPU'da etkisiz).
            opts.accelerator_options = AcceleratorOptions(
                num_threads=self.parse_num_threads, device="auto",
            )
            if self.figure_images:
                # M-7: bu bayrak OLMADAN pic.image None kalır (görüntü hiç üretilmez).
                # Ölçüldü (2 dosya × 2 tur, ısınma elenmiş): parse süresine ölçülebilir
                # etkisi YOK (±2%, gürültü) — maliyet yalnızca disk (~10-20 KB/görsel).
                opts.generate_picture_images = True
                opts.images_scale = self.figure_image_scale
            fmt_kwargs = {"pipeline_options": opts}
            backend_cls = _resolve_pdf_backend(self.pdf_backend)
            if backend_cls is not None:   # None = docling'in kendi varsayılanı (dlparse)
                fmt_kwargs["backend"] = backend_cls
            self._converters[anahtar] = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(**fmt_kwargs)
                }
            )
        return self._converters[anahtar]

    def parse(self, path: str, file_type: str, *, ocr: bool = False,
              full_page_ocr: bool = False) -> ParsedDocument:
        conv = self._converter(ocr, full_page_ocr)
        result = conv.convert(path)
        document = result.document
        return _map_document(document, ocr=ocr, full_page_ocr=full_page_ocr)


def _picture_png(pic) -> bytes | None:
    """M-7: Docling PictureItem → PNG baytları. Görüntü ancak pipeline'da
    `generate_picture_images` açıkken üretilir; kapalıysa (veya API değişirse)
    None döner ve çağıran uyarı yazar — parse ÇÖKMEZ (mevcut savunmacı desen)."""
    img = getattr(pic, "image", None)
    pil = getattr(img, "pil_image", None) if img is not None else None
    if pil is None:
        return None
    try:
        import io
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _page_no_of(item) -> int | None:
    prov = getattr(item, "prov", None)
    if prov:
        return getattr(prov[0], "page_no", None)
    return None


def _map_document(document, *, ocr: bool, full_page_ocr: bool = False) -> ParsedDocument:
    pd = ParsedDocument()

    # Sayfa iskeleti (numara sırasıyla).
    pages: dict[int, Page] = {}
    try:
        for page_no in sorted(getattr(document, "pages", {}) or {}):
            pages[page_no] = Page(page_no=page_no, text_blocks=[])
    except Exception as exc:
        pd.warn(f"docling: sayfa listesi okunamadı ({exc})")

    def _page(no: int | None) -> Page:
        no = no or 1
        if no not in pages:
            pages[no] = Page(page_no=no, text_blocks=[])
        return pages[no]

    offset = 0
    # Metin öğeleri: başlık -> section, diğerleri -> gövde (sayfasına).
    for item in getattr(document, "texts", []) or []:
        text = (getattr(item, "text", "") or "").strip()
        if not text:
            continue
        label = str(getattr(item, "label", "")).lower()
        page_no = _page_no_of(item)
        if "section_header" in label or "title" in label:
            pd.sections.append(Section(
                title=text, level=1, page_start=page_no,
                char_span=(offset, offset + len(text)),
            ))
        _page(page_no).text_blocks.append(text)
        offset += len(text) + 1

    # Tablolar.
    for idx, tbl in enumerate(getattr(document, "tables", []) or []):
        rows = _table_rows(tbl, document)
        pd.tables.append(Table(
            index=idx, data=rows,
            flattened_text=flatten_table(rows), page_no=_page_no_of(tbl),
        ))

    # Şekiller.
    for idx, pic in enumerate(getattr(document, "pictures", []) or []):
        caption = None
        try:
            caption = pic.caption_text(document) or None
        except Exception:
            caption = None
        png = _picture_png(pic)
        if png is None:
            # Görüntü yoksa KAYIT YİNE OLUŞUR (sayfa/başlık) — yalnızca görüntü eksik.
            # Sessizce yutulmasın: hangi şeklin görüntüsü alınamadı, uyarıya düşsün.
            pd.warn(f"docling: şekil #{idx} görüntüsü alınamadı (storage_path boş kalacak)")
        pd.figures.append(Figure(index=idx, page_no=_page_no_of(pic),
                                 caption=caption, image_png=png))

    pd.pages = [pages[k] for k in sorted(pages)]
    if not pd.pages:
        pd.pages.append(Page(page_no=1, text_blocks=[]))
    if full_page_ocr:
        pd.warn("docling: tam-sayfa OCR etkin (metin katmanı yok sayıldı)")
    elif ocr:
        pd.warn("docling: OCR modu etkin")
    pd.language = detect_language(pd.body_text)
    return pd


def _table_rows(tbl, document) -> list[list]:
    """Docling TableItem -> satır/sütun listesi (savunmacı)."""
    try:
        df = tbl.export_to_dataframe()
        return [list(df.columns)] + df.astype(object).values.tolist()
    except Exception:
        pass
    try:
        grid = tbl.data.grid  # list[list[TableCell]]
        return [[getattr(c, "text", "") for c in row] for row in grid]
    except Exception:
        return []
