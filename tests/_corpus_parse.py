"""İP-2 test korpusu üreticileri (kontrollü, deterministik)."""

from __future__ import annotations

import os


# NOT: pymupdf insert_text base14 fontu Türkçe glifleri render edemez -> üretilen
# PDF'ler ASCII metin kullanır (mojibake/garbage'ı önlemek için). docx/xlsx/txt
# UTF-8'i doğru gömdüğünden onlarda Türkçe içerik kullanılır.
def make_text_pdf(path: str, title: str = "Rapor Basligi",
                  body: str = "Karbon vergisi hakkinda govde metni. " * 6) -> None:
    """Büyük-font başlık (section) + gövde metni içeren metin-katmanlı PDF (ASCII)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 50), title, fontsize=22)      # başlık -> section
    y = 90
    for line in _wrap(body, 70):
        page.insert_text((40, y), line, fontsize=11)
        y += 16
    doc.save(path)
    doc.close()


def make_table_pdf(path: str) -> None:
    """Başlık + gövde + çizgi-tabanlı gerçek tablo (find_tables algılar)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 50), "Tablolu Rapor", fontsize=22)
    page.insert_text((40, 90), "Bu govde metni tablonun disindadir.", fontsize=11)
    x0, y0, cw, ch, ncol, nrow = 40, 120, 150, 25, 2, 3
    for r in range(nrow + 1):
        page.draw_line((x0, y0 + r * ch), (x0 + ncol * cw, y0 + r * ch))
    for c in range(ncol + 1):
        page.draw_line((x0 + c * cw, y0), (x0 + c * cw, y0 + nrow * ch))
    cells = [["Ad", "Deger"], ["Karbon", "42"], ["Vergi", "15"]]
    for r in range(nrow):
        for c in range(ncol):
            page.insert_text((x0 + c * cw + 5, y0 + r * ch + 17), cells[r][c], fontsize=11)
    doc.save(path)
    doc.close()


def make_scanned_pdf(path: str, text: str = "Bu sayfa taranmis; metin katmani yok.") -> None:
    """Image-only PDF (metin katmanı yok) -> coverage 0, OCR tetikler."""
    import fitz
    src = fitz.open()
    p = src.new_page()
    p.insert_text((72, 72), text, fontsize=14)
    scan = fitz.open()
    for pg in src:
        pix = pg.get_pixmap(dpi=100)
        np = scan.new_page(width=pg.rect.width, height=pg.rect.height)
        np.insert_image(np.rect, pixmap=pix)
    scan.save(path)
    scan.close()
    src.close()


def make_docx(path: str, headings: bool = True) -> None:
    """Heading stilli başlıklar + tablo içeren gerçek docx (python-docx)."""
    from docx import Document
    doc = Document()
    if headings:
        doc.add_heading("Ana Başlık", level=1)
    doc.add_paragraph("Karbon vergisi giriş paragrafı metni.")
    if headings:
        doc.add_heading("Alt Başlık", level=2)
    doc.add_paragraph("İkinci bölüm paragraf metni burada.")
    # Tablo hücreleri gövde paragraflarıyla ÇAKIŞMAYAN benzersiz token'lar.
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "TbAd"
    t.cell(0, 1).text = "TbDeger"
    t.cell(1, 0).text = "TbKarbon"
    t.cell(1, 1).text = "TbVal42"
    doc.save(path)


def make_xlsx(path: str, title: str = "veri") -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sayfa1"
    ws.append(["Ad", "Değer"])
    ws.append(["Karbon", 42])
    ws.append([title, 7])
    wb.save(path)


def make_txt(path: str, text: str = "Karbon vergisi düz metin.\n\nİkinci paragraf.") -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def build_corpus(root: str) -> dict[str, list[str]]:
    """Kabul korpusu: 5 pdf (1 taranmış), 3 docx, 2 xlsx, 2 txt."""
    os.makedirs(root, exist_ok=True)
    made: dict[str, list[str]] = {"pdf": [], "docx": [], "xlsx": [], "txt": [], "scanned": []}

    # 5 pdf: 3 metin, 1 tablolu, 1 taranmış
    for i in range(3):
        p = os.path.join(root, f"text_{i}.pdf")
        make_text_pdf(p, title=f"Rapor {i}", body=f"Benzersiz gövde {i}. " * 8)
        made["pdf"].append(p)
    tp = os.path.join(root, "table.pdf")
    make_table_pdf(tp)
    made["pdf"].append(tp)
    sp = os.path.join(root, "scanned.pdf")
    make_scanned_pdf(sp)
    made["pdf"].append(sp)
    made["scanned"].append(sp)

    for i in range(3):
        p = os.path.join(root, f"doc_{i}.docx")
        make_docx(p)
        made["docx"].append(p)
    for i in range(2):
        p = os.path.join(root, f"sheet_{i}.xlsx")
        make_xlsx(p, title=f"deg{i}")
        made["xlsx"].append(p)
    for i in range(2):
        p = os.path.join(root, f"note_{i}.txt")
        make_txt(p, text=f"Metin dosyası {i}.\n\nParagraf iki {i}.")
        made["txt"].append(p)
    return made
