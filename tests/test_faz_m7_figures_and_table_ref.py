"""M-7 Aşama 1 — REPROCESS'siz kanıtlanabilen her şey.

Kapsam:
  (a) scope ön-koşulu: run()/recover_stuck()/list_pending_files scope'lu; None = global
      (production davranışı bit-bit aynı).
  (b) M-2b: chunker tablo bağını KOLONA taşıyor; table_repo ÇİFT YOL (kolon → türetme).
  (c) M-7: Docling görsel çıkarma, dosya deposu, /api/figure 404-sızdırmazlığı.

DDL (FAZ7_Sema_Ek2) HENÜZ UYGULANMADI — bu testler kolonların YOKLUĞUNDA da geçer;
kolon-yolu, kolonların varlığını taklit eden sahte bağlantı/geçici şema ile sınanır.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from ragintel.database import storage_repo, table_repo
from ragintel.ingestion.chunking.chunker import chunk_document
from ragintel.ingestion.parsing.parsed_document import Figure, Page, ParsedDocument, Table
from ragintel.ingestion.chunking.tokenizer import WordTokenCounter
from ragintel.ingestion.storage import figure_path_for, save_figure_png

# Projenin kendi kelime-sayacı (test_ip5_chunker ile aynı) — deterministik.
_Counter = WordTokenCounter


# =============================================================================
# (b) M-2b — chunker tablo bağını kolona yazar
# =============================================================================
def _doc_with_table(rows: list[list[str]], index: int = 0) -> ParsedDocument:
    doc = ParsedDocument()
    doc.pages = [Page(page_no=1, text_blocks=[])]
    doc.tables = [Table(index=index, data=rows, flattened_text=_flat(rows), page_no=1)]
    return doc


def _flat(rows) -> str:
    return "\n".join(" | ".join(str(c) for c in r) for r in rows)


def test_small_table_carries_table_index_but_no_row_range():
    """Eşik-altı tablo TEK chunk: table_index dolu, satır aralığı NULL
    ('bu chunk tablonun tamamı' — DDL'deki CHECK ile aynı sözleşme)."""
    doc = _doc_with_table([["a", "b"], ["1", "2"]], index=3)
    chunks = chunk_document(doc, counter=_Counter(), max_tokens=500,
                            table_subchunk_max_tokens=500)
    tbl = [c for c in chunks if c.is_table]
    assert len(tbl) == 1
    assert tbl[0].table_index == 3
    assert tbl[0].table_row_start is None and tbl[0].table_row_end is None


def test_split_table_carries_row_range_matching_section_title():
    """Bölünen tabloda her alt-chunk kendi satır aralığını KOLONDA taşır ve bu,
    insan-okur section_title ile TUTARLIDIR (ikisi ayrışırsa bağ yalan söyler)."""
    rows = [["baslik1", "baslik2"]] + [[f"satir{i}", f"deger{i}"] for i in range(1, 7)]
    doc = _doc_with_table(rows, index=2)
    chunks = chunk_document(doc, counter=_Counter(), max_tokens=500,
                            table_subchunk_max_tokens=6)   # zorla böl
    tbl = [c for c in chunks if c.is_table]
    assert len(tbl) > 1, "tablo bölünmedi — test amacını yitirdi (vacuous olurdu)"

    for c in tbl:
        assert c.table_index == 2
        assert c.table_row_start is not None and c.table_row_end is not None
        assert 1 <= c.table_row_start <= c.table_row_end
        # section_title ile kolon AYNI aralığı söylemeli
        span = (f"satır {c.table_row_start}" if c.table_row_start == c.table_row_end
                else f"satır {c.table_row_start}-{c.table_row_end}")
        assert c.section_title == f"tablo2 · {span}"

    # Aralıklar gövde satırlarını BOŞLUKSUZ ve ÜST ÜSTE BİNMEDEN kaplamalı.
    spans = sorted((c.table_row_start, c.table_row_end) for c in tbl)
    assert spans[0][0] == 1
    for (_, prev_end), (nxt_start, _) in zip(spans, spans[1:]):
        assert nxt_start == prev_end + 1
    assert spans[-1][1] == len(rows) - 1        # başlık hariç gövde satır sayısı


def test_non_table_chunk_has_no_table_ref():
    """Karşı-örnek: düz metin chunk'ı tablo bağı TAŞIMAZ (aksi halde her chunk
    tablo-kökenli görünür ve kolon anlamsızlaşırdı)."""
    doc = ParsedDocument()
    doc.pages = [Page(page_no=1, text_blocks=["düz metin " * 20])]
    chunks = chunk_document(doc, counter=_Counter(), max_tokens=500)
    plain = [c for c in chunks if not c.is_table]
    assert plain, "düz metin chunk'ı üretilmedi — test vacuous olurdu"
    assert all(c.table_index is None for c in plain)


# =============================================================================
# (b) table_repo ÇİFT YOL — sahte bağlantı ile (DDL'den bağımsız)
# =============================================================================
class _FakeConn:
    """resolve_table_refs'in iki sorgusunu ayırt eden minimal sahte bağlantı."""

    def __init__(self, *, columns_present: bool, column_rows=(), derived_rows=()):
        self.columns_present = columns_present
        self.column_rows = list(column_rows)
        self.derived_rows = list(derived_rows)
        self.derived_asked_for: list[int] | None = None

    def execute(self, sql, params=None):
        if "information_schema.columns" in sql:
            return _Res([(1 if self.columns_present else 0,)])
        if "table_id IS NOT NULL" in sql:          # kolon yolu
            return _Res(self.column_rows)
        self.derived_asked_for = list(params["ids"])  # türetme yolu
        return _Res(self.derived_rows)


class _Res:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_resolve_prefers_column_path_when_present():
    conn = _FakeConn(columns_present=True,
                     column_rows=[(10, 77, 3, 5)],
                     derived_rows=[(10, 999, None, None)])   # türetme YANLIŞ cevap verse bile
    refs = table_repo.resolve_table_refs(conn, [10])
    assert refs == {10: {"table_id": 77, "row_start": 3, "row_end": 5}}
    assert conn.derived_asked_for is None, "kolon dolu olan chunk için türetme yolu ÇALIŞMAMALI"


def test_resolve_falls_back_to_derived_for_columnless_chunks():
    """Geçiş dönemi: kolonu NULL olan (REPROCESS öncesi yazılmış) chunk türetme
    yoluna düşer. Bu olmadan DDL ile REPROCESS arasında tablo gösterimi KIRILIRDI."""
    conn = _FakeConn(columns_present=True,
                     column_rows=[(10, 77, 3, 5)],        # 10 kolondan
                     derived_rows=[(20, 88, 1, 2)])       # 20 türetmeden
    refs = table_repo.resolve_table_refs(conn, [10, 20])
    assert refs[10]["table_id"] == 77
    assert refs[20]["table_id"] == 88
    assert conn.derived_asked_for == [20], "türetme YALNIZCA kolonu boş olanlar için sorulmalı"


def test_resolve_uses_derived_only_when_ddl_not_applied():
    """DDL uygulanmadan (kolon YOK) eski davranış birebir korunur."""
    conn = _FakeConn(columns_present=False, derived_rows=[(30, 55, None, None)])
    refs = table_repo.resolve_table_refs(conn, [30])
    assert refs == {30: {"table_id": 55, "row_start": None, "row_end": None}}
    assert conn.derived_asked_for == [30]


def test_copy_chunks_skips_ref_columns_when_ddl_absent():
    """Kod DDL'den ÖNCE de çalışmalı: kolonlar yoksa COPY onları YAZMAYA ÇALIŞMAZ
    (yoksa canlı DB'de her ingest patlardı — push ile DDL birbirini beklemez)."""
    class _C:
        def __init__(self):
            self.copy_sql = None

        def execute(self, sql, params=None):
            return _Res([(0,)])                 # kolon YOK

        def cursor(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def copy(self, sql):
            self.copy_sql = sql
            return _Cp()

    class _Cp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def write_row(self, row):
            assert len(row) == 10, "DDL yokken tablo bağı kolonları yazılmamalı"

    conn = _C()
    chunk = type("K", (), {"chunk_index": 0, "chunk_text": "x", "chunk_text_norm": "x",
                           "token_count": 1, "page_number": 1, "sheet_name": None,
                           "section_title": None, "char_start": None, "char_end": None,
                           "table_index": 5, "table_row_start": 1, "table_row_end": 2})()
    storage_repo.copy_chunks(conn, 1, [chunk], {5: 99})
    assert "table_id" not in conn.copy_sql


# =============================================================================
# (c) M-7 görseller — dosya deposu
# =============================================================================
def test_figure_path_is_file_id_scoped_and_idempotent(tmp_path):
    root = str(tmp_path)
    p1 = save_figure_png(root, 42, 0, b"\x89PNG-birinci")
    p2 = save_figure_png(root, 42, 0, b"\x89PNG-ikinci")   # REPROCESS: ÜZERİNE yazar
    assert p1 == p2
    assert open(p1, "rb").read() == b"\x89PNG-ikinci", "REPROCESS öksüz kopya bırakmamalı"
    assert figure_path_for(root, 42, 1).endswith("figures/42/1.png".replace("/", __import__("os").sep))


def test_docling_backend_enables_picture_images_only_when_configured():
    """Görsel çıkarma config'ten gelir; kapalıyken Docling'e bayrak GEÇİLMEZ."""
    from ragintel.ingestion.parsing.backends import get_backend
    on = get_backend("docling", figure_images=True, figure_image_scale=2.0)
    off = get_backend("docling", figure_images=False)
    assert on.figure_images is True and on.figure_image_scale == 2.0
    assert off.figure_images is False


def test_picture_png_returns_none_without_image():
    """Docling görüntü üretmediyse (bayrak kapalı / API değişti) None döner ve
    parse ÇÖKMEZ — kayıt yine oluşur, storage_path NULL kalır."""
    from ragintel.ingestion.parsing.docling_backend import _picture_png
    assert _picture_png(object()) is None
    assert _picture_png(type("P", (), {"image": None})()) is None


def test_picture_png_encodes_pil_image():
    """Pozitif ön-koşul (vacuous değil): gerçek bir PIL görüntüsü PNG'ye çevrilir."""
    pytest.importorskip("PIL")
    from PIL import Image
    from ragintel.ingestion.parsing.docling_backend import _picture_png

    pil = Image.new("RGB", (4, 4), (255, 0, 0))
    pic = type("P", (), {"image": type("I", (), {"pil_image": pil})()})()
    png = _picture_png(pic)
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO(png)).size == (4, 4)


# =============================================================================
# (a) SCOPE ÖN-KOŞULU — @db: testler kendi scope'unda kapalı devre
# =============================================================================
@pytest.mark.db
def test_pending_and_stuck_listing_is_scope_isolated(live_db):
    """Mekanik güvence: scope verilince YALNIZCA o scope görünür. Bu olmadan, REPROCESS
    penceresinde koşan bir test gerçek PENDING dosyaları işlemeye başlardı."""
    from ragintel.database.ingestion_repo import list_pending_files, list_stuck_processing

    scope = f"m7-test-{__import__('uuid').uuid4().hex[:8]}"
    with live_db.connection() as conn:
        fid = conn.execute(
            "INSERT INTO core_files (file_name, file_type, file_size, checksum, source_path, "
            "doc_scope, status, updated_at) VALUES (%s,'txt',1,%s,'/x',%s,'PENDING', now() - interval '2 hours') "
            "RETURNING file_id;",
            (f"{scope}.txt", scope, scope)).fetchone()[0]
    try:
        with live_db.connection() as conn:
            # Pozitif ön-koşul: dosya GERÇEKTEN global listede var (yoksa test vacuous).
            assert fid in [f["file_id"] for f in list_pending_files(conn)]
            # Kendi scope'unda görünür...
            mine = list_pending_files(conn, scope=scope)
            assert [f["file_id"] for f in mine] == [fid]
            # ...başka scope'ta GÖRÜNMEZ.
            assert list_pending_files(conn, scope="default") == [] or fid not in [
                f["file_id"] for f in list_pending_files(conn, scope="default")]

        # stuck listesi de scope'lu: bu dosya PROCESSING'e çekilirse yalnız kendi
        # scope'unda "takılı" sayılır — başkasının dosyasına DOKUNULMAZ.
        # NOT: UPDATE ile okuma AYRI transaction'larda olmalı — now() transaction
        # başlangıç zamanıdır ve updated_at'i trigger aynı now() ile yazar; aynı
        # transaction'da `updated_at < now()` hiçbir zaman doğru olmaz.
        with live_db.connection() as conn:
            conn.execute("UPDATE core_files SET status='PROCESSING' WHERE file_id=%s;", (fid,))
        with live_db.connection() as conn:
            assert list_stuck_processing(conn, 0, scope=scope) == [fid]
            assert fid not in list_stuck_processing(conn, 0, scope="default")
            assert fid in list_stuck_processing(conn, 0)          # scope=None → global
    finally:
        with live_db.connection() as conn:
            conn.execute("DELETE FROM core_files WHERE file_id = %s;", (fid,))


# =============================================================================
# (c) /api/figure — scope fail-closed (M-2 deseni birebir)
# =============================================================================
class _Resolver:
    def __init__(self, scopes):
        self.scopes = list(scopes)

    def resolve(self, token):
        from ragintel.api.auth import Unauthorized
        if token == "test-token":
            return {"user_id": "test", "tenant_id": "default", "roles": ["user"],
                    "allowed_doc_scopes": self.scopes, "is_admin": False}
        raise Unauthorized("geçersiz token")


class _Mock:
    def complete(self, **k):
        raise AssertionError("çağrılmamalı")

    def search_hybrid(self, *a, **k):
        return []

    def build(self, r):
        return {"blocks": [], "citations": [], "dropped_chunk_ids": []}


_AUTH = {"Authorization": "Bearer test-token"}


def _client(live_db, scopes=("default",)):
    from langgraph.checkpoint.memory import InMemorySaver

    from ragintel.api.app import create_app
    from ragintel.api.runtime import RagRuntime
    from ragintel.config.loader import load_config
    from ragintel.config.settings import LangfuseSettings
    from ragintel.database import make_db_reader

    cfg = load_config(db_reader=make_db_reader(live_db))
    rt = RagRuntime(db=live_db, config=cfg, gateway=_Mock(), checkpointer=InMemorySaver(),
                    service=_Mock(), context_builder=_Mock(),
                    langfuse=LangfuseSettings(host="", public_key="", secret_key=""),
                    resolver=_Resolver(scopes))
    return TestClient(create_app(rt))


@pytest.fixture
def figure_in_scope(live_db, tmp_path):
    """'envanter' scope'unda, DİSKTE gerçek PNG'si olan bir görsel kaydı."""
    png = b"\x89PNG\r\n\x1a\n" + b"sahte-govde"
    with live_db.connection() as conn:
        fid = conn.execute(
            "INSERT INTO core_files (file_name, file_type, file_size, checksum, source_path, "
            "doc_scope, status) VALUES ('m7.pdf','pdf',1,%s,'/x','envanter','COMPLETED') "
            "RETURNING file_id;", (f"m7-{__import__('uuid').uuid4().hex[:10]}",)).fetchone()[0]
    path = save_figure_png(str(tmp_path), fid, 0, png)
    with live_db.connection() as conn:
        gid = conn.execute(
            "INSERT INTO core_figures (file_id, page_number, figure_index, caption, storage_path) "
            "VALUES (%s, 1, 0, 'test şekli', %s) RETURNING figure_id;", (fid, path)).fetchone()[0]
    yield gid, png
    with live_db.connection() as conn:
        conn.execute("DELETE FROM core_files WHERE file_id = %s;", (fid,))


@pytest.mark.db
def test_figure_served_to_allowed_scope(live_db, figure_in_scope):
    """Pozitif ön-koşul: scope'u OLAN kullanıcı görseli GERÇEKTEN alabiliyor —
    yoksa aşağıdaki 404 testi her koşulda geçer (vacuous)."""
    gid, png = figure_in_scope
    r = _client(live_db, scopes=("envanter",)).get(f"/api/figure/{gid}", headers=_AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content == png


@pytest.mark.db
def test_envanter_figure_hidden_from_default_scope(live_db, figure_in_scope):
    """KABUL: envanter görseli default kullanıcıya 404 — 403 DEĞİL (403 varlığı sızdırır)."""
    gid, _ = figure_in_scope
    r = _client(live_db, scopes=("default",)).get(f"/api/figure/{gid}", headers=_AUTH)
    assert r.status_code == 404
    # Var olmayan id ile AYIRT EDİLEMEZ olmalı (aynı kod + aynı gövde).
    ghost = _client(live_db, scopes=("default",)).get("/api/figure/999999999", headers=_AUTH)
    assert ghost.status_code == 404
    assert r.json() == ghost.json()


@pytest.mark.db
def test_figure_requires_auth(live_db, figure_in_scope):
    gid, _ = figure_in_scope
    assert _client(live_db).get(f"/api/figure/{gid}").status_code == 401
    assert _client(live_db).get(f"/api/figure/{gid}",
                                headers={"Authorization": "Bearer yanlis"}).status_code == 401


@pytest.mark.db
def test_figure_without_stored_image_is_404(live_db):
    """storage_path NULL (görüntü kaydedilmemiş) → 404. 'Var ama boş' diye ayırt
    edilebilir bir yanıt vermek scope sızıntısına kapı açardı."""
    with live_db.connection() as conn:
        fid = conn.execute(
            "INSERT INTO core_files (file_name, file_type, file_size, checksum, source_path, "
            "doc_scope, status) VALUES ('m7b.pdf','pdf',1,%s,'/x','default','COMPLETED') "
            "RETURNING file_id;", (f"m7b-{__import__('uuid').uuid4().hex[:10]}",)).fetchone()[0]
        gid = conn.execute(
            "INSERT INTO core_figures (file_id, page_number, figure_index, caption, storage_path) "
            "VALUES (%s, 1, 0, NULL, NULL) RETURNING figure_id;", (fid,)).fetchone()[0]
    try:
        r = _client(live_db, scopes=("default",)).get(f"/api/figure/{gid}", headers=_AUTH)
        assert r.status_code == 404
    finally:
        with live_db.connection() as conn:
            conn.execute("DELETE FROM core_files WHERE file_id = %s;", (fid,))


def test_insert_figures_writes_storage_path():
    """storage_path artık SABİT None DEĞİL — verilen yol yazılır (M-7'nin özü)."""
    captured = {}

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def executemany(self, sql, rows):
            captured["rows"] = rows

    conn = type("C", (), {"cursor": lambda self: _Cur()})()
    figs = [Figure(index=0, page_no=1, caption="şekil"),
            Figure(index=1, page_no=2, caption=None)]
    storage_repo.insert_figures(conn, 7, figs, {0: "/depo/7/0.png"})
    rows = captured["rows"]
    assert rows[0][-1] == "/depo/7/0.png"          # görüntüsü olan → yol
    assert rows[1][-1] is None                      # görüntüsü olmayan → NULL, kayıt yine var
