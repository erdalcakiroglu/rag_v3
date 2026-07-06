"""İP-1 testleri için sentetik dosya üreticileri (deterministik, içerik-tabanlı)."""

from __future__ import annotations

import os
import zipfile

# Magic'in application/pdf tespit etmesi için geçerli minimal PDF imzası.
PDF_BYTES = (
    b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # desteklenmeyen tip


def make_txt(path: str, text: str = "Karbon vergisi hakkında deneme metni.\n") -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def make_pdf(path: str, extra: bytes = b"") -> None:
    with open(path, "wb") as f:
        f.write(PDF_BYTES + extra)


def make_png(path: str) -> None:
    with open(path, "wb") as f:
        f.write(PNG_BYTES)


def make_docx(path: str, body: str = "Deneme") -> None:
    """word/ üyesi içeren minimal OOXML (içerikten docx tespiti için)."""
    ct = (
        '<?xml version="1.0"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '</Types>'
    )
    doc = (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body><w:p><w:r><w:t>{body}</w:t></w:r></w:p></w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("word/document.xml", doc)


def make_xlsx(path: str, value: str = "veri") -> None:
    """xl/ üyesi içeren minimal OOXML (içerikten xlsx tespiti için)."""
    ct = (
        '<?xml version="1.0"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '</Types>'
    )
    wb = (
        '<?xml version="1.0"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheets><sheet name="S1" sheetId="1" r:id="rId1"/></sheets>'
        f'<!-- {value} --></workbook>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/uniq.txt", value)  # her dosya benzersiz içerik


def build_mixed_corpus(root: str) -> dict[str, int]:
    """~100 dosyalık karışık korpus üretir; beklenen sonuç sayacını döndürür.

    Dönen sözlük: created, skipped, rejected, versioned beklenenleri.
    """
    os.makedirs(root, exist_ok=True)
    n_valid = 0
    n_dup = 0
    n_reject = 0

    # 60 benzersiz geçerli dosya (tip döngüsü, benzersiz içerik).
    makers = [make_txt, make_pdf, make_docx, make_xlsx]
    exts = ["txt", "pdf", "docx", "xlsx"]
    for i in range(60):
        j = i % 4
        p = os.path.join(root, f"valid_{i:03d}.{exts[j]}")
        if exts[j] == "txt":
            make_txt(p, text=f"Benzersiz içerik numara {i}. Karbon vergisi.\n")
        elif exts[j] == "pdf":
            make_pdf(p, extra=f"unique-{i}".encode())
        elif exts[j] == "docx":
            make_docx(p, body=f"Docx govde {i}")
        else:
            make_xlsx(p, value=f"hucre-{i}")
        n_valid += 1

    # 25 duplicate (ilk 25 geçerli dosyanın birebir kopyası) -> SKIP.
    for i in range(25):
        src_ext = exts[i % 4]
        src = os.path.join(root, f"valid_{i:03d}.{src_ext}")
        dst = os.path.join(root, f"dup_{i:03d}.{src_ext}")
        with open(src, "rb") as s, open(dst, "wb") as d:
            d.write(s.read())
        n_dup += 1

    # 10 desteklenmeyen (png) -> REJECTED.
    for i in range(10):
        make_png(os.path.join(root, f"image_{i:02d}.png"))
        n_reject += 1

    # 5 yanlış uzantı ama geçerli içerik: .docx uzantılı ama PDF içerik -> pdf CREATED.
    for i in range(5):
        p = os.path.join(root, f"mislabeled_{i:02d}.docx")
        make_pdf(p, extra=f"mislabel-{i}".encode())
        n_valid += 1

    return {
        "created": n_valid,     # 65
        "skipped": n_dup,       # 25
        "rejected": n_reject,   # 10
        "total": n_valid + n_dup + n_reject,  # 100
    }
