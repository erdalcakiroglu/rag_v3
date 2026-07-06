"""Docling parse backend'i (İP-2 birincil): pdf/docx, dahili OCR (rapidocr).

Docling ağırdır (torch); import tembeldir ve converter'lar (OCR açık/kapalı)
önbelleğe alınır. DoclingDocument -> ParsedDocument eşlemesi sayfa numaralarını
(prov) korur — citation zinciri için zorunlu. Eşleme API farkına karşı
savunmacıdır; eksik alanlar parse_warnings'e düşer, çökmez.
"""

from __future__ import annotations

from .parsed_document import Figure, Page, ParsedDocument, Section, Table
from .text_utils import detect_language, flatten_table


class DoclingBackend:
    name = "docling"

    def __init__(self) -> None:
        self._converters: dict[bool, object] = {}

    def supports(self, file_type: str) -> bool:
        return file_type in ("pdf", "docx")

    def _converter(self, ocr: bool):
        if ocr not in self._converters:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions

            opts = PdfPipelineOptions()
            opts.do_ocr = ocr
            opts.do_table_structure = True
            self._converters[ocr] = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=opts)
                }
            )
        return self._converters[ocr]

    def parse(self, path: str, file_type: str, *, ocr: bool = False) -> ParsedDocument:
        conv = self._converter(ocr)
        result = conv.convert(path)
        document = result.document
        return _map_document(document, ocr=ocr)


def _page_no_of(item) -> int | None:
    prov = getattr(item, "prov", None)
    if prov:
        return getattr(prov[0], "page_no", None)
    return None


def _map_document(document, *, ocr: bool) -> ParsedDocument:
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
        pd.figures.append(Figure(index=idx, page_no=_page_no_of(pic), caption=caption))

    pd.pages = [pages[k] for k in sorted(pages)]
    if not pd.pages:
        pd.pages.append(Page(page_no=1, text_blocks=[]))
    if ocr:
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
