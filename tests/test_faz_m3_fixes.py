"""M-3 — M-2 ön-veri yan bulgularının düzeltmeleri.

(a) scope taksonomi hizalaması → tests/test_faz6_auth.py::test_scope_isolation_bidirectional
    (orada; artık boş kümeyle geçemez) + buradaki @db doğrulaması.
(b) citation numaralandırma: metindeki her [n] = kaynak listesinin n'inci girdisi.
(c) PII tarih false-positive: sürüm dizesi maskelenmez, gerçek tarih maskelenir.
"""

from __future__ import annotations

import re

import pytest

from ragintel.agents.nodes.compose import _renumber_answer, _sources, compose_response
from ragintel.guardrails.pii import PiiPolicy, mask_pii

_MARKER = re.compile(r"\[(\d+)\]")


# --------------------------------------------------------------------------
# (b) citation numaralandırma
# --------------------------------------------------------------------------
def _chunk(cid, name="f.pdf", page=1):
    return {"chunk_id": cid, "source": {"file_name": name, "page": page, "section": None}}


def _assert_markers_resolve(answer: str, sources: list[dict]) -> None:
    """KABUL DEĞİŞMEZİ: metindeki her [n] kaynak listesinde n'inci girdiye denk gelir."""
    ns = [int(m) for m in _MARKER.findall(answer)]
    for n in ns:
        assert 1 <= n <= len(sources), f"askıda citation [{n}] — kaynak sayısı {len(sources)}"
        assert sources[n - 1]["n"] == n, "kaynak listesi n alanı sırayla artmıyor"


def test_model_markers_are_replaced_not_trusted():
    """Model 1 citation verip metinde '[2]' yazarsa askıda referans KALMAZ."""
    citations = [{"claim": "Danimarka'da elektriğin %76'sı kömürden üretilir",
                  "chunk_id": 46902, "quote": "Danimarka | 76"}]
    retrieved = [_chunk(46902)]
    answer = "Danimarka'da elektriğin %76'sı kömürden üretilir [2]."

    sources, numbering = _sources(citations, retrieved)
    out = _renumber_answer(answer, citations, numbering)

    assert len(sources) == 1
    assert "[2]" not in out
    assert "[1]" in out
    _assert_markers_resolve(out, sources)


def test_duplicate_chunk_citations_collapse_to_one_source():
    """Aynı chunk iki kez alıntılanırsa TEK kaynak (eskiden [1] ve [3] mükerrer girdiydi)."""
    # IP: RFC 5737 dokümantasyon aralığı — gerçek iç adres fixture'a yazılmaz.
    citations = [
        {"claim": "IP adresi 192.0.2.10", "chunk_id": 59640, "quote": "IP Address: 192.0.2.10"},
        {"claim": "SQL Server 2017", "chunk_id": 59640, "quote": "SQL Server Version: 2017"},
        {"claim": "Envanter kaydı mevcut", "chunk_id": 59700, "quote": "envanter"},
    ]
    retrieved = [_chunk(59640, "ggb.xlsx"), _chunk(59700, "inv.xlsx")]
    answer = "IP adresi 192.0.2.10 [1][3]. SQL Server 2017 olduğu. Envanter kaydı mevcut."

    sources, numbering = _sources(citations, retrieved)
    out = _renumber_answer(answer, citations, numbering)

    assert [s["chunk_id"] for s in sources] == [59640, 59700]
    assert [s["n"] for s in sources] == [1, 2]
    _assert_markers_resolve(out, sources)
    # aynı chunk'a ait iki claim de [1]'e bağlanır; ikinci kaynak [2]'dir
    assert "[3]" not in out and "[2]" in out


def test_unmatched_claim_marker_appended_not_dropped():
    """Claim parafraz edilmişse işaret cümleye zorlanmaz, sona eklenir — ama askıda kalmaz."""
    citations = [{"claim": "birebir geçmeyen iddia", "chunk_id": 5, "quote": "q"}]
    sources, numbering = _sources(citations, [_chunk(5)])
    out = _renumber_answer("Tamamen farklı sözcüklerle yazılmış yanıt [4].", citations, numbering)
    assert out.endswith("[1]") and "[4]" not in out
    _assert_markers_resolve(out, sources)


def test_answer_without_markers_is_left_untouched():
    """Model işaret koymadıysa compose UYDURMAZ — yanıt metni birebir korunur (regresyon)."""
    citations = [{"claim": "Karbon vergisi emisyonu fiyatlar", "chunk_id": 5, "quote": "q"}]
    _, numbering = _sources(citations, [_chunk(5)])
    original = "Karbon vergisi emisyonu fiyatlar."
    assert _renumber_answer(original, citations, numbering) == original


def test_declined_answer_has_no_dangling_markers():
    """sources=[] olan reddetme yolunda metinde [n] KALMAZ."""
    state = {"draft_answer": "Bulunamadı [1][2].", "citations": [], "retrieved": [],
             "validation": {"coverage": 0.0}, "budget": {}}
    out = compose_response(state, declined=True)
    assert out["sources"] == []
    assert "[1]" not in out["answer"] and "[2]" not in out["answer"]


def test_compose_end_to_end_marker_source_alignment():
    state = {
        "draft_answer": "Karbon vergisi bir politikadır [7]. Sınırda uygulama ithalatı etkiler [9].",
        "citations": [
            {"claim": "Karbon vergisi bir politikadır", "chunk_id": 11, "quote": "politika"},
            {"claim": "Sınırda uygulama ithalatı etkiler", "chunk_id": 22, "quote": "ithalat"},
        ],
        "retrieved": [_chunk(11, "a.pdf"), _chunk(22, "b.pdf")],
        "validation": {"coverage": 0.95}, "budget": {"iteration": 1, "tokens": 10},
    }
    out = compose_response(state, declined=False)
    assert [s["n"] for s in out["sources"]] == [1, 2]
    _assert_markers_resolve(out["answer"], out["sources"])
    assert "[7]" not in out["answer"] and "[9]" not in out["answer"]
    # [1] a.pdf'e, [2] b.pdf'e denk gelir
    assert out["sources"][0]["file_name"] == "a.pdf" and out["sources"][1]["file_name"] == "b.pdf"


# --------------------------------------------------------------------------
# (c) PII tarih false-positive
# --------------------------------------------------------------------------
_P = PiiPolicy(enabled=True, mask_tckn=True, mask_dates=True)

# FP regresyon fixture'ı: bunlar TARİH DEĞİL, maskelenmemeli.
_NOT_DATES = [
    "Microsoft SQL Server 2017 sürüm 14.0.3456.9 kullanılıyor",   # M-3(c) canlı FP
    "Ürün versiyonu 14.0.3456",                                    # ay=0 → tarih değil
    "Sürüm 1.14.0.3456 derlemesi",                                 # daha uzun dizinin parçası
    "Build 10.50.6000.34",
    "Dosya sürümü 12.05.3456",                                     # yıl aralık dışı
    "Yıl 1990 içinde",                                             # tek yıl (mevcut karar)
]

# Gerçek tarihler — maskelenmeli (regresyon).
_REAL_DATES = [
    ("Doğum tarihi 12.05.1980 olarak kayıtlı", "12.05.1980"),
    ("Tarih 1/1/1990 idi", "1/1/1990"),
    ("ISO biçim 1980-05-12 kaydı", "1980-05-12"),
    ("Ölçüm 2026-03-02 tarihinde alındı", "2026-03-02"),
    ("Cümle sonunda 12.05.1980.", "12.05.1980"),
]


@pytest.mark.parametrize("text", _NOT_DATES)
def test_version_strings_not_masked(text):
    out, count = mask_pii(text, _P)
    assert out == text, f"sürüm dizesi maskelendi: {out}"
    assert count == 0


@pytest.mark.parametrize("text,date", _REAL_DATES)
def test_real_dates_still_masked(text, date):
    out, count = mask_pii(text, _P)
    assert date not in out, f"gerçek tarih maskelenmedi: {out}"
    assert "[TARİH]" in out and count == 1


def test_version_and_date_in_same_text():
    """Aynı metinde sürüm korunur, tarih maskelenir."""
    out, count = mask_pii("Sürüm 14.0.3456.9, kurulum tarihi 12.05.1980.", _P)
    assert "14.0.3456.9" in out
    assert "[TARİH]" in out and "12.05.1980" not in out
    assert count == 1


# --------------------------------------------------------------------------
# (a) scope hizalaması — canlı veri
# --------------------------------------------------------------------------
@pytest.mark.db
def test_envanter_user_scope_matches_document_taxonomy(live_db):
    """envanter kullanıcısının scope'u dokümanların doc_scope'u ile aynı olmalı."""
    with live_db.connection() as conn:
        scopes = conn.execute(
            "SELECT allowed_doc_scopes FROM ragintel.users WHERE user_id='envanter';").fetchone()
        if scopes is None:
            pytest.skip("envanter kullanıcısı yok")
        doc_scopes = {r[0] for r in conn.execute("SELECT DISTINCT doc_scope FROM core_files;").fetchall()}
        orphan = conn.execute(
            "SELECT count(*) FROM ragintel.users u, unnest(u.allowed_doc_scopes) s "
            "WHERE s NOT IN (SELECT DISTINCT doc_scope FROM core_files);").fetchone()[0]

    assert "envanter" in list(scopes[0]), "envanter kullanıcısı kendi belgelerini göremez"
    assert "envanter" in doc_scopes
    assert orphan == 0, "hiçbir dokümanla eşleşmeyen kullanıcı scope'u var (taksonomi kayması)"


@pytest.mark.db
def test_envanter_user_can_read_own_table(live_db):
    """KABUL: envanter kullanıcısı KENDİ tablosunu görür (M-2 fail-closed uçtan uçta)."""
    from ragintel.database import table_repo
    with live_db.connection() as conn:
        row = conn.execute(
            "SELECT t.table_id FROM core_tables t JOIN core_files f USING (file_id) "
            "WHERE f.doc_scope='envanter' ORDER BY t.table_id LIMIT 1;").fetchone()
        assert row is not None
        tid = int(row[0])
        scopes = list(conn.execute(
            "SELECT allowed_doc_scopes FROM ragintel.users WHERE user_id='envanter';").fetchone()[0])
        payload = table_repo.get_table_for_scopes(conn, tid, scopes)
        # sızıntı hâlâ yok: default kullanıcı aynı tabloyu göremez
        denied = table_repo.get_table_for_scopes(conn, tid, ["default"])

    assert payload is not None and payload["table_id"] == tid
    assert denied is None
