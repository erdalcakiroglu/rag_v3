"""İP-1 checksum + raw depo (DB gerekmez)."""

from __future__ import annotations

import os

from ragintel.ingestion.storage import (
    copy_to_storage,
    file_size,
    sha256_file,
    storage_path_for,
)
from tests import _corpus


def test_sha256_deterministic_and_size(tmp_path):
    p = str(tmp_path / "a.txt")
    _corpus.make_txt(p, "aynı içerik")
    h1 = sha256_file(p)
    h2 = sha256_file(p)
    assert h1 == h2 and len(h1) == 64
    assert file_size(p) == os.path.getsize(p)


def test_different_content_different_hash(tmp_path):
    a = str(tmp_path / "a.txt"); _corpus.make_txt(a, "içerik A")
    b = str(tmp_path / "b.txt"); _corpus.make_txt(b, "içerik B")
    assert sha256_file(a) != sha256_file(b)


def test_copy_is_idempotent_and_preserves_original(tmp_path):
    src = str(tmp_path / "src.pdf")
    _corpus.make_pdf(src)
    cs = sha256_file(src)
    root = str(tmp_path / "storage")

    d1 = copy_to_storage(src, root, cs, "src.pdf")
    mtime1 = os.path.getmtime(d1)
    d2 = copy_to_storage(src, root, cs, "src.pdf")  # ikinci kez -> no-op

    assert d1 == d2
    assert os.path.getmtime(d2) == mtime1          # yeniden kopyalanmadı
    assert d1 == os.path.abspath(storage_path_for(root, cs, "src.pdf"))
    assert os.path.exists(src)                      # orijinal duruyor
    assert sha256_file(d1) == cs                    # kopya bütünlüğü
