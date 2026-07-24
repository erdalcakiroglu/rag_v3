"""FAZ 6 P2 — PII maskeleme testleri: TCKN checksum + FP + tarih + compose + log scrub."""

from __future__ import annotations

from ragintel.agents.nodes.compose import compose_response
from ragintel.config.loader import load_config
from ragintel.guardrails.pii import PiiPolicy, is_valid_tckn, mask_pii
from ragintel.observability.logging import _pii_scrub_processor

# Checksum-geçerli sentetik TCKN (gerçek kişi değil) ve geçersiz 11-haneli.
VALID_TCKN = "10000000146"
INVALID_TCKN = "12345678901"   # 11 hane ama checksum tutmaz


def test_tckn_checksum():
    assert is_valid_tckn(VALID_TCKN) is True
    assert is_valid_tckn(INVALID_TCKN) is False
    assert is_valid_tckn("0123456789") is False   # 10 hane
    assert is_valid_tckn("01234567890") is False   # ilk hane 0


def test_mask_tckn_only_valid():
    out, n = mask_pii(f"Kimlik {VALID_TCKN} numaralı", PiiPolicy())
    assert "[TCKN]" in out and VALID_TCKN not in out and n == 1


def test_mask_invalid_tckn_not_masked_false_positive():
    out, n = mask_pii(f"Fatura no {INVALID_TCKN}", PiiPolicy())
    assert INVALID_TCKN in out and "[TCKN]" not in out and n == 0  # FP önlendi


def test_mask_dates_but_not_single_year():
    out, n = mask_pii("Doğum 12.05.1980, olay 2026-01-03 ama yıl 1990 tektir.", PiiPolicy())
    assert out.count("[TARİH]") == 2 and "1990" in out and n == 2


def test_mask_disabled_noop():
    out, n = mask_pii(f"TC {VALID_TCKN}", PiiPolicy(enabled=False))
    assert out == f"TC {VALID_TCKN}" and n == 0


def test_custom_pattern():
    out, n = mask_pii("IBAN TR330006100519786457841326", PiiPolicy(custom_patterns=(r"TR\d{24}",)))
    assert "[PII]" in out and n == 1


# --- compose entegrasyonu (yanıt + quote) ------------------------------------
def _chunk(cid, text):
    return {"chunk_id": cid, "text": text, "score": 0.9,
            "source": {"file_id": 1, "file_name": "a.pdf", "page": 1, "section": "S", "version": 1},
            "retrieval_method": "hybrid"}


def test_compose_masks_answer_and_quote_and_counts():
    state = {
        "draft_answer": f"Başvuran {VALID_TCKN} kimlikli, doğum 12.05.1980.",
        "citations": [{"claim": "x", "chunk_id": 10, "quote": f"TC {VALID_TCKN} kayıtlı"}],
        "retrieved": [_chunk(10, "metin")],
        "validation": {"passed": True, "coverage": 0.95},
        "retry_count": 0,
        "budget": {"iteration": 2, "tokens_used": 100},
    }
    resp = compose_response(state, config=load_config())
    assert VALID_TCKN not in resp["answer"] and "[TCKN]" in resp["answer"] and "[TARİH]" in resp["answer"]
    assert VALID_TCKN not in resp["sources"][0]["quote"] and "[TCKN]" in resp["sources"][0]["quote"]
    assert resp["meta"]["pii_masked_count"] == 3   # 2 (yanıt: tckn+tarih) + 1 (quote: tckn)


# --- log scrub (PII log'a sızmaz) --------------------------------------------
def test_log_scrub_masks_tckn():
    ev = _pii_scrub_processor(None, "info", {"event": "x", "detail": f"kullanıcı TC {VALID_TCKN}"})
    assert VALID_TCKN not in ev["detail"] and "[TCKN]" in ev["detail"]


def test_log_scrub_leaves_clean_untouched():
    ev = _pii_scrub_processor(None, "info", {"event": "ok", "n": 5, "msg": "temiz mesaj"})
    assert ev == {"event": "ok", "n": 5, "msg": "temiz mesaj"}


# --- M-15: token SAYAÇLARI görünür, SIRLAR hâlâ redakte (iki yön de kilitli) ---
def test_log_scrub_keeps_numeric_token_counters_visible():
    """Latency anatomisi bu sayılara bağlı; 'token' ipucu bunları '***' yapıyordu."""
    ev = _pii_scrub_processor(None, "info", {
        "event": "llm_call_timing", "prompt_tokens": 5100, "completion_tokens": 312,
        "tokens_per_sec": 41.7, "eval_count": 312,
    })
    assert ev["prompt_tokens"] == 5100 and ev["completion_tokens"] == 312
    assert ev["tokens_per_sec"] == 41.7 and ev["eval_count"] == 312


def test_log_scrub_still_redacts_real_secrets():
    """M-12 kalkanı DARALMADI: sır alanları (string) aynen redakte."""
    ev = _pii_scrub_processor(None, "info", {
        "event": "x", "password": "hunter2", "api_key": "sk-abc", "token": "eyJhbGci",
        "authorization": "Bearer xyz", "db_password": "p", "refresh_token": "r",
    })
    for k in ("password", "api_key", "token", "authorization", "db_password", "refresh_token"):
        assert ev[k] == "***", k


def test_log_scrub_metric_exemption_is_narrow():
    """Muafiyet yalnız (birebir ad ∩ sayı) — string değer veya listede olmayan ad KAÇMAZ."""
    ev = _pii_scrub_processor(None, "info", {
        "event": "x",
        "prompt_tokens": "sk-gizli",      # allowlist'te AMA string → sır muamelesi
        "session_token_count": 5,          # sayı AMA birebir ad değil → redakte
        "max_tokens": True,                # bool int alt sınıfı — sayaç sayılmaz
    })
    assert ev["prompt_tokens"] == "***"
    assert ev["session_token_count"] == "***"
    assert ev["max_tokens"] == "***"
