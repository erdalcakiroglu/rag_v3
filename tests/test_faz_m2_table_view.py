"""M-2 — yapısal tablo gösterimi: kontrat, normalizasyon, chunk→tablo çözümleme,
scope fail-closed (404) ve tablo-olmayan kaynakların regresyonu.

M-2b DDL uygulanıp M-7 Aşama 2 reprocess-all (41/41 dosya) tamamlandıktan sonra her
tablo-kökenli chunk'ın `table_id` kolonu KALICI olarak dolu (doğrulandı: NULL=0). Eski
section_title-regex/eşitlik-join TÜRETME yolu artık hiç tetiklenmediği için ölü kod
olarak sökülmüştür (bkz. table_repo.py); bu dosyadaki testler de yalnız KOLON yolunu
sınar.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from ragintel.api.app import create_app
from ragintel.api.runtime import RagRuntime
from ragintel.api.schemas import FinalResponse, Source
from ragintel.config.loader import load_config
from ragintel.database import table_repo
from ragintel.llm.gateway import LLMResponse, ToolCall


class _StubGateway:
    """Graph kurulabilsin diye; M-2 uçları LLM çağırmaz."""
    name = "stub"

    def complete(self, *, messages, tools):
        args = {"answer": "bulunamadı", "citations": []}
        tc = ToolCall(id="c1", name="submit_answer", arguments=args)
        raw = {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "submit_answer", "arguments": json.dumps(args)}}]}
        return LLMResponse(content=None, tool_calls=[tc], prompt_tokens=1, completion_tokens=1, raw_message=raw)


class _Resolver:
    """Fail-closed sahte resolver: token → scope eşlemesi."""

    def __init__(self, scopes: list[str]):
        self.scopes = scopes

    def resolve(self, token):
        from ragintel.api.auth import Unauthorized
        if token == "test-token":
            return {"user_id": "test", "tenant_id": "default", "roles": ["user"],
                    "allowed_doc_scopes": self.scopes, "is_admin": False}
        raise Unauthorized("geçersiz token")


_AUTH = {"Authorization": "Bearer test-token"}


def _runtime(live_db, scopes=("default",)):
    from langgraph.checkpoint.memory import InMemorySaver

    from ragintel.config.settings import LangfuseSettings
    from ragintel.database import make_db_reader
    from ragintel.retrieval import ContextBuilder, RetrievalService
    cfg = load_config(db_reader=make_db_reader(live_db))
    return RagRuntime(
        db=live_db, config=cfg, gateway=_StubGateway(), checkpointer=InMemorySaver(),
        service=RetrievalService(db=live_db, config=cfg),
        context_builder=ContextBuilder(db=live_db, config=cfg),
        langfuse=LangfuseSettings(host="", public_key="", secret_key=""),
        resolver=_Resolver(list(scopes)))


# --- 1) Kontrat: table_ref additive, tablo-olmayanda None (DB'siz) ------------
def test_source_table_ref_is_additive_and_optional():
    base = {"n": 1, "file_name": "f.pdf", "page": 3, "section": "S", "chunk_id": 9, "quote": "q"}
    assert Source.model_validate(base).table_ref is None          # REGRESYON: eski gövde aynen geçerli

    with_ref = {**base, "table_ref": {"table_id": 7, "row_start": 2, "row_end": 5}}
    src = Source.model_validate(with_ref)
    assert src.table_ref.table_id == 7 and src.table_ref.row_start == 2 and src.table_ref.row_end == 5

    # Yol B (eşik-altı tablo): satır aralığı yok
    whole = Source.model_validate({**base, "table_ref": {"table_id": 7}})
    assert whole.table_ref.row_start is None and whole.table_ref.row_end is None

    # extra='forbid' hâlâ yürürlükte (drift yakalanır)
    with pytest.raises(Exception):
        Source.model_validate({**base, "table_ref": {"table_id": 7, "sürpriz": 1}})
    with pytest.raises(Exception):
        FinalResponse.model_validate({
            "answer": "x", "sources": [], "confidence": "low", "followups": [], "beklenmedik": 1,
            "meta": {"iterations": 0, "tokens": 0, "latency_ms": 0, "model": "m", "trace_id": "t"}})


# --- 2) Normalizasyon: jenerik başlık, satır yutulmaz (DB'siz) ----------------
def test_normalize_keeps_real_header():
    raw = [["Ülke", "Oran"], ["Finlandiya", "73,11"], ["Norveç", "61,42"]]
    t = table_repo.normalize_table_data(raw)
    assert t["headerless"] is False
    assert t["columns"] == ["Ülke", "Oran"]
    assert t["rows"] == [["Finlandiya", "73,11"], ["Norveç", "61,42"]]


def test_normalize_placeholder_header_uses_generic_columns_without_swallowing_rows():
    ph = table_repo.PLACEHOLDER_CELL
    raw = [[ph, ph, ph], ["\xa0Kuveyt", "37.32", "73.13"], ["\xa0Brunei", "48.33", "46.38"]]
    t = table_repo.normalize_table_data(raw)
    assert t["headerless"] is True
    assert t["columns"] == ["Kolon 1", "Kolon 2", "Kolon 3"]
    # KRİTİK: hiçbir gövde satırı başlığa terfi ettirilip yutulmadı; NBSP temizlendi.
    assert t["rows"] == [["Kuveyt", "37.32", "73.13"], ["Brunei", "48.33", "46.38"]]


def test_normalize_header_with_one_empty_cell_is_not_headerless():
    t = table_repo.normalize_table_data([["", "Vergi"], ["Benzin cent/litre", "53.85"]])
    assert t["headerless"] is False and t["columns"] == ["", "Vergi"]


def test_normalize_rejects_broken_shapes():
    for bad in (None, {}, [], "satır", [["ok"], "satır-değil"]):
        with pytest.raises(ValueError):
            table_repo.normalize_table_data(bad)


# --- 3) @db chunk→tablo çözümleme --------------------------------------------
def _one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


@pytest.mark.db
def test_subchunk_resolves_table_and_row_range_via_column(live_db):
    """M-1 alt-chunk'ı → table_id + satır aralığı (UI vurgusunun kaynağı).

    section_title yalnız ADAY chunk'ı BULMAK için kullanılıyor; çözümlemenin kendisi
    (resolve_table_refs) artık TEK yoldan — `core_chunks.table_id` kolonundan — geçer."""
    with live_db.connection() as conn:
        row = _one(conn, """
            SELECT chunk_id, section_title FROM core_chunks
            WHERE section_title ~ '^tablo[0-9]+ · satır' ORDER BY chunk_id LIMIT 1;""")
        assert row is not None, "M-1 alt-chunk'ı yok — aday bulunamadı"
        chunk_id = int(row[0])
        refs = table_repo.resolve_table_refs(conn, [chunk_id])

    assert chunk_id in refs
    ref = refs[chunk_id]
    assert ref["table_id"] > 0
    assert ref["row_start"] is not None and ref["row_end"] is not None
    assert ref["row_start"] <= ref["row_end"]


@pytest.mark.db
def test_non_table_chunk_has_no_table_ref(live_db):
    """REGRESYON: tablo-kökenli olmayan chunk sonuçta YER ALMAZ → table_ref eklenmez."""
    with live_db.connection() as conn:
        row = _one(conn, """
            SELECT chunk_id FROM core_chunks WHERE table_id IS NULL ORDER BY chunk_id LIMIT 1;""")
        assert row is not None
        chunk_id = int(row[0])
        refs = table_repo.resolve_table_refs(conn, [chunk_id])
    assert chunk_id not in refs


@pytest.mark.db
def test_duplicate_table_payloads_resolve_deterministically(live_db):
    """Mükerrer table_data → min(table_id) (kararlı; çağrılar arası oynamaz)."""
    with live_db.connection() as conn:
        ids = [int(r[0]) for r in conn.execute(
            "SELECT chunk_id FROM core_chunks ORDER BY chunk_id LIMIT 400;").fetchall()]
        first = table_repo.resolve_table_refs(conn, ids)
        second = table_repo.resolve_table_refs(conn, ids)
    assert first == second and first, "çözümleme boş veya kararsız"


# --- 4) @db scope fail-closed: envanter tablosu default kullanıcıya 404 -------
def _envanter_table_id(conn) -> int | None:
    row = _one(conn, """SELECT t.table_id FROM core_tables t JOIN core_files f USING (file_id)
                        WHERE f.doc_scope = 'envanter' ORDER BY t.table_id LIMIT 1;""")
    return int(row[0]) if row else None


@pytest.mark.db
def test_envanter_table_hidden_from_default_scope_repo_level(live_db):
    with live_db.connection() as conn:
        tid = _envanter_table_id(conn)
        assert tid is not None, "envanter scope'lu tablo yok — scope testi anlamsız"
        assert table_repo.get_table_for_scopes(conn, tid, ["default"]) is None      # scope dışı
        assert table_repo.get_table_for_scopes(conn, tid, []) is None               # boş scope → fail-closed
        assert table_repo.get_table_for_scopes(conn, tid, ["envanter"]) is not None  # yetkili görür


@pytest.mark.db
def test_envanter_table_returns_404_not_403(live_db):
    """403 tablonun VAR OLDUĞUNU sızdırırdı; scope dışı ve yok olan AYNI yanıtı verir."""
    with live_db.connection() as conn:
        tid = _envanter_table_id(conn)
    assert tid is not None

    client = TestClient(create_app(_runtime(live_db, scopes=["default"])))
    denied = client.get(f"/api/table/{tid}", headers=_AUTH)
    missing = client.get("/api/table/99999999", headers=_AUTH)
    assert denied.status_code == 404
    assert missing.status_code == 404
    assert denied.json() == missing.json()      # ayırt edilemez (varlık bilgisi sızmaz)

    # Yetkili kullanıcı aynı tabloyu görebilir → 404 gerçekten scope kaynaklı
    ok = TestClient(create_app(_runtime(live_db, scopes=["envanter"]))).get(f"/api/table/{tid}", headers=_AUTH)
    assert ok.status_code == 200 and ok.json()["table_id"] == tid


@pytest.mark.db
def test_table_endpoint_requires_bearer(live_db):
    client = TestClient(create_app(_runtime(live_db)))
    with live_db.connection() as conn:
        tid = int(_one(conn, "SELECT table_id FROM core_tables ORDER BY table_id LIMIT 1;")[0])
    assert client.get(f"/api/table/{tid}").status_code == 401                      # başlık yok
    assert client.get(f"/api/table/{tid}", headers={"Authorization": "Bearer nope"}).status_code == 401


# --- 5) @db uç nokta payload'ı ------------------------------------------------
@pytest.mark.db
def test_table_endpoint_payload_shape(live_db):
    with live_db.connection() as conn:
        tid = int(_one(conn, """SELECT t.table_id FROM core_tables t JOIN core_files f USING (file_id)
                                WHERE f.doc_scope = 'default' AND jsonb_array_length(t.table_data) > 2
                                ORDER BY t.table_id LIMIT 1;""")[0])
    r = TestClient(create_app(_runtime(live_db))).get(f"/api/table/{tid}", headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["table_id"] == tid and body["renderable"] is True
    assert body["file_name"] and isinstance(body["table_index"], int)
    assert body["columns"] and body["rows"]
    assert body["row_count"] == len(body["rows"])
    assert all(len(row) == len(body["columns"]) for row in body["rows"])   # UI kolon hizası
